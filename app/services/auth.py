"""Authentication service managing login flows, account lockout, and credential verification (P3.3).

Per:
- docs/03-auth-permissions.md §5 & §6 (Login flow, timing attack defense, lockout, rehash).
- docs/06-business-rules.md §11 (BR-AUTH-04 rehash, BR-AUTH-05 uniform error, BR-AUTH-06 lockout, BR-AUTH-08 MFA).
- docs/07-test-cases.md §5.10 (T-AUTH-05-a/b, T-AUTH-06-a/b/c/d/e).
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.errors import InvalidCredentialsError
from app.core.security import (
    dummy_verify_password,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.user import AuthSession, User
from app.repositories.users import UserRepository
from app.services.sessions import SessionService


@dataclass(frozen=True)
class LoginResult:
    """Authentication result holding user details, issued session, and MFA requirement flag."""

    user: User
    raw_session_token: str
    session: AuthSession
    mfa_required: bool


class AuthService:
    """Domain service handling user authentication, progressive lockout, and rehash on login."""

    def __init__(
        self,
        user_repo: UserRepository,
        session_service: SessionService,
        *,
        clock: Clock | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.user_repo = user_repo
        self.session_service = session_service
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
