"""Authentication service managing login flows, account lockout, and credential verification (P3.3).

Per:
- docs/03-auth-permissions.md §5 & §6 (Login flow, timing attack defense, lockout, rehash).
- docs/06-business-rules.md §11 (BR-AUTH-04 rehash, BR-AUTH-05 uniform error, BR-AUTH-06 lockout, BR-AUTH-08 MFA).
- docs/07-test-cases.md §5.10 (T-AUTH-05-a/b, T-AUTH-06-a/b/c/d/e).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.errors import (
    InvalidCredentialsError,
    InvalidTokenError,
    NotFoundError,
)
from app.core.security import (
    dummy_verify_password,
    generate_secure_token,
    hash_password,
    hash_token,
    needs_rehash,
    validate_password,
    verify_password,
)
from app.mail import (
    Mailer,
    get_mailer,
    render_invitation_email,
    render_password_reset_email,
)
from app.models.user import AuthSession, User
from app.repositories.user_tokens import UserTokenRepository
from app.repositories.users import UserRepository
from app.services.sessions import SessionService

logger = logging.getLogger("app.services.auth")


@dataclass(frozen=True)
class LoginResult:
    """Authentication result holding user details, issued session, and MFA requirement flag."""

    user: User
    raw_session_token: str
    session: AuthSession
    mfa_required: bool


class AuthService:
    """Domain service handling user authentication, invitations, and password resets."""

    def __init__(
        self,
        user_repo: UserRepository,
        session_service: SessionService,
        token_repo: UserTokenRepository | None = None,
        *,
        mailer: Mailer | None = None,
        clock: Clock | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.user_repo = user_repo
        self.session_service = session_service
        self.token_repo = token_repo or UserTokenRepository(user_repo.db)
        self.mailer = mailer or get_mailer()
        self.clock = clock or get_clock()
        self.settings = settings or get_settings()

    def authenticate(
        self,
        email: str,
        password: str,
        *,
        ip_hash: str | None = None,
        user_agent: str | None = None,
    ) -> LoginResult:
        """Authenticate user credentials and issue server-side session.

        Conforms strictly to BR-AUTH-05, BR-AUTH-06, and BR-AUTH-08:
        1. Non-existent email -> dummy verify + 401 invalid_credentials.
        2. Uninitialized password_hash (pending invite) -> dummy verify + 401 invalid_credentials.
        3. Active lockout -> dummy verify + 401 invalid_credentials (does not reveal lock).
        4. Password mismatch -> increment counter (triggering 15/30/60m lockout on multiples of 5) + 401.
        5. Deactivated user (is_active=False) -> 401 invalid_credentials.
        6. Success -> reset counters, rehash if needed, determine MFA, create session.
        """
        clean_email = email.strip().lower()
        now = self.clock.now()

        # Step 1: Lookup user by normalized email
        user = self.user_repo.get_by_email(clean_email)
        if user is None:
            dummy_verify_password(password)
            raise InvalidCredentialsError("Invalid email or password.")

        # Step 2: Handle uninitialized accounts (pending invitations without password)
        if user.password_hash is None:
            dummy_verify_password(password)
            raise InvalidCredentialsError("Invalid email or password.")

        # Step 3: Check active lockout (BR-AUTH-06)
        if user.locked_until is not None and user.locked_until > now:
            dummy_verify_password(password)
            raise InvalidCredentialsError("Invalid email or password.")

        # Step 4: Verify Argon2id password hash (BR-AUTH-04)
        is_valid = verify_password(password, user.password_hash)
        if not is_valid:
            new_failed_count = user.failed_login_count + 1
            locked_until = None

            # Lockout policy: 5 consecutive failures triggers lockout, doubling up to 60m cap
            if new_failed_count % 5 == 0:
                lockout_multiplier = new_failed_count // 5
                # 5 failures -> 15 min, 10 failures -> 30 min, 15+ failures -> 60 min
                lockout_minutes = min(60, 15 * (2 ** (lockout_multiplier - 1)))
                locked_until = now + timedelta(minutes=lockout_minutes)
            elif (
                user.failed_login_count >= 5
                and user.locked_until is not None
                and user.locked_until <= now
            ):
                # Retain existing expired status or clear until next multiple
                locked_until = None

            self.user_repo.increment_failed_login(user, locked_until=locked_until)
            raise InvalidCredentialsError("Invalid email or password.")

        # Step 5: Check account active status (BR-AUTH-07)
        if not user.is_active:
            raise InvalidCredentialsError("Invalid email or password.")

        # Step 6: Clear failed login counters on successful password verification
        self.user_repo.reset_failed_logins(user)

        # Step 7: Automatic rehash if Argon2id parameters are outdated (BR-AUTH-04)
        if needs_rehash(user.password_hash):
            new_hash = hash_password(password)
            self.user_repo.update_password_hash(user, new_hash)

        # Step 8: Determine MFA requirement and session issuance (BR-AUTH-08)
        # Administrators ALWAYS require MFA; supervisors require MFA if totp_enabled
        mfa_required = (user.role == "admin") or bool(user.totp_enabled)

        if not mfa_required:
            # Full session, record last_login_at
            user.last_login_at = now
            raw_token, session = self.session_service.create_session(
                user.id,
                role=user.role,
                ip_hash=ip_hash,
                user_agent=user_agent,
                mfa_verified=True,
            )
        else:
            # Interim session (mfa_verified=False), last_login_at recorded upon MFA success
            raw_token, session = self.session_service.create_session(
                user.id,
                role=user.role,
                ip_hash=ip_hash,
                user_agent=user_agent,
                mfa_verified=False,
            )

        return LoginResult(
            user=user,
            raw_session_token=raw_token,
            session=session,
            mfa_required=mfa_required,
        )

    def unlock_user(self, user_id: uuid.UUID) -> bool:
        """Administratively clear lockout on a user account (BR-AUTH-06 / T-AUTH-06-e)."""
        return self.user_repo.unlock(user_id)

    def create_invitation(
        self,
        user_id: uuid.UUID,
        *,
        send_email: bool = True,
    ) -> tuple[str, str, bool]:
        """Create a single-use hashed invitation token with 72-hour TTL (BR-AUTH-02).

        Per:
        - BR-AUTH-02: Random token, stored as SHA-256 hash only, 72h validity, single use.
          Resending invalidates prior active tokens.
        - BR-USER-09: If sending email fails, return invitation link once to the caller.

        Returns:
            tuple[raw_token, invite_url, email_sent]
        """
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        now = self.clock.now()

        # Invalidate any prior active invitation tokens (BR-AUTH-02 / T-AUTH-02-d)
        self.token_repo.invalidate_active_tokens(user.id, "invite", now)

        raw_token = generate_secure_token(32)
        token_hash = hash_token(raw_token)
        expires_at = now + timedelta(hours=72)

        self.token_repo.create_token(
            user_id=user.id,
            purpose="invite",
            token_hash=token_hash,
            expires_at=expires_at,
        )

        invite_url = (
            f"{self.settings.public_base_url.rstrip('/')}/auth/accept-invite?token={raw_token}"
        )

        email_sent = False
        if send_email:
            try:
                site_name = user.site.name if user.site else None
                subject, body_text, body_html = render_invitation_email(
                    invite_url=invite_url,
                    role=user.role,
                    site_name=site_name,
                )
                self.mailer.send(
                    to=user.email,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                )
                email_sent = True
            except Exception as exc:
                logger.warning(
                    "Failed to deliver invitation email to user %s: %s",
                    user.id,
                    exc,
                )
                email_sent = False

        return raw_token, invite_url, email_sent

    def accept_invitation(self, token: str, password: str) -> User:
        """Accept an invitation using a one-time token and set account password (BR-AUTH-02).

        Per:
        - BR-AUTH-02 / T-AUTH-02-b/c/d: Token must be valid, unexpired (<= 72h), and unused.
        - BR-AUTH-03 / T-AUTH-03-a/b: Password must conform to policy (10-128 chars, non-common, etc.).
        """
        now = self.clock.now()
        token_hash = hash_token(token)

        token_record = self.token_repo.get_active_token(token_hash, "invite", now)
        if token_record is None:
            raise InvalidTokenError("Invalid, expired, or already used invitation token.")

        user = self.user_repo.get_by_id(token_record.user_id)
        if user is None:
            raise InvalidTokenError("User associated with invitation token does not exist.")

        # Validate password policy against user's email and full name
        validate_password(
            password,
            user_email=user.email,
            user_name=user.full_name,
        )

        new_hash = hash_password(password)
        self.user_repo.activate_user(user, new_hash)
        self.token_repo.mark_as_used(token_record, now)

        return user

    def request_password_reset(self, email: str) -> None:
        """Initiate password reset with uniform response and 1-hour token TTL (BR-AUTH-15).

        Per:
        - BR-AUTH-15: Uniform response (204 No Content) regardless of user existence.
        - T-AUTH-15-a: Response is identical for existing vs non-existing accounts.
        - T-AUTH-15-b: Non-existing or inactive email triggers NO email delivery.
        - Token has 1 hour TTL and is single-use.
        """
        clean_email = email.strip().lower()
        user = self.user_repo.get_by_email(clean_email)

        # Defense against user enumeration: do not leak whether account exists or is active
        if user is None or not user.is_active:
            dummy_verify_password()
            return

        now = self.clock.now()

        # Invalidate any prior active password reset tokens for this user
        self.token_repo.invalidate_active_tokens(user.id, "password_reset", now)

        raw_token = generate_secure_token(32)
        token_hash = hash_token(raw_token)
        expires_at = now + timedelta(hours=1)

        self.token_repo.create_token(
            user_id=user.id,
            purpose="password_reset",
            token_hash=token_hash,
            expires_at=expires_at,
        )

        reset_url = (
            f"{self.settings.public_base_url.rstrip('/')}/auth/reset-password?token={raw_token}"
        )

        try:
            subject, body_text, body_html = render_password_reset_email(reset_url)
            self.mailer.send(
                to=user.email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )
        except Exception as exc:
            logger.warning(
                "Failed to deliver password reset email to user %s: %s",
                user.id,
                exc,
            )

    def reset_password(self, token: str, new_password: str) -> User:
        """Reset user password using one-time token and invalidate all sessions (BR-AUTH-15).

        Per:
        - BR-AUTH-15: 1h validity, single use, invalidates all sessions, leaves MFA intact.
        - BR-AUTH-13: Invalidate all sessions of user.
        - T-AUTH-15-c: Expired (> 1h) or re-used token raises InvalidTokenError (HTTP 400).
        - T-AUTH-15-d: MFA is NOT removed; if user had MFA enabled, it is still required.
        """
        now = self.clock.now()
        token_hash = hash_token(token)

        token_record = self.token_repo.get_active_token(token_hash, "password_reset", now)
        if token_record is None:
            raise InvalidTokenError("Invalid, expired, or already used password reset token.")

        user = self.user_repo.get_by_id(token_record.user_id)
        if user is None:
            raise InvalidTokenError("User associated with reset token does not exist.")

        # Validate password policy
        validate_password(
            new_password,
            user_email=user.email,
            user_name=user.full_name,
        )

        new_hash = hash_password(new_password)
        self.user_repo.reset_password(user, new_hash)
        self.token_repo.mark_as_used(token_record, now)

        # Invalidate ALL active sessions across all devices (BR-AUTH-13 / BR-AUTH-15)
        self.session_service.revoke_all_user_sessions(user.id)

        return user
