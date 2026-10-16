"""Multi-Factor Authentication (MFA) domain service (P3.7).

Per:
- docs/03-auth-permissions.md §7 (TOTP, pyotp, encrypted secret, recovery codes, anti-reuse).
- docs/06-business-rules.md §11 (BR-AUTH-08 admin MFA, BR-AUTH-09 TOTP ±1 window & anti-replay, BR-AUTH-10 recovery codes, BR-AUTH-17 admin cannot disable MFA).
- docs/07-test-cases.md §5.10 (T-AUTH-08, T-AUTH-09-a/b/c/d, T-AUTH-10-a/b).
- docs/11-decisions.md (D-15 supervisor optional MFA, D-16 timeouts).
"""

import logging
import secrets
import threading
import uuid
from dataclasses import dataclass
from typing import Final

import pyotp

from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.errors import (
    ForbiddenError,
    InvalidCredentialsError,
    NotFoundError,
    ValidationError,
)
from app.core.security import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_code,
    hash_token,
)
from app.models.user import AuthSession, User
from app.repositories.mfa import MfaRecoveryCodeRepository
from app.repositories.users import UserRepository
from app.services.sessions import SessionService

logger = logging.getLogger("app.services.mfa")

# Maximum failed verification attempts on an interim session before instant revocation
MAX_INTERIM_MFA_ATTEMPTS: Final[int] = 5


@dataclass(frozen=True)
class MfaSetupResult:
    """Setup details returned when enrolling in two-factor authentication."""

    secret: str
    provisioning_uri: str
    recovery_codes: list[str]


class MfaService:
    """Domain service managing TOTP two-factor authentication and backup recovery codes."""

    def __init__(
        self,
        user_repo: UserRepository,
        recovery_repo: MfaRecoveryCodeRepository,
        session_service: SessionService,
        *,
        clock: Clock | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.user_repo = user_repo
        self.recovery_repo = recovery_repo
        self.session_service = session_service
        self.clock = clock or get_clock()
        self.settings = settings or get_settings()

        # Thread-safe in-memory counter tracking failed attempts per interim session ID
        self._interim_attempts: dict[str, int] = {}
        self._lock = threading.Lock()

    def _get_interim_attempts(self, session_id: str) -> int:
        with self._lock:
            return self._interim_attempts.get(session_id, 0)

    def _increment_interim_attempts(self, session_id: str) -> int:
        with self._lock:
            current = self._interim_attempts.get(session_id, 0) + 1
            self._interim_attempts[session_id] = current
            return current

    def _clear_interim_attempts(self, session_id: str) -> None:
        with self._lock:
            self._interim_attempts.pop(session_id, None)

    def _handle_interim_failure(self, session: AuthSession | None) -> None:
        """Record a failed MFA attempt on an interim session and revoke upon 5 failures (BR-AUTH-09 / T-AUTH-09-d)."""
        if session is None:
            return

        fails = self._increment_interim_attempts(session.id)
        if fails >= MAX_INTERIM_MFA_ATTEMPTS:
            now = self.clock.now()
            self.session_service.revoke_session_by_hash(session.id)
            session.revoked_at = now
            self.user_repo.db.flush()
            logger.warning(
                "Interim session %s revoked after 5 failed MFA attempts", session.id[:10]
            )

    # ==========================================================================
    # 1. Setup & Enrollment Flow (docs/03 §7, T-AUTH-10-a)
    # ==========================================================================

    def initiate_mfa_setup(self, user_id: uuid.UUID) -> MfaSetupResult:
        """Generate a new base32 TOTP secret, QR provisioning URI, and 8 backup recovery codes.

        Per docs/03 §7 & docs/06 BR-AUTH-10:
        - totp_secret_enc is encrypted using Fernet (AES).
        - totp_enabled remains False until explicitly confirmed by entering a valid code.
        - 8 recovery codes are generated, stored as SHA-256 hashes, and returned once.
        """
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        # 1. Generate unencoded base32 secret and totp helper
        raw_secret = pyotp.random_base32()
        totp = pyotp.TOTP(raw_secret)
        provisioning_uri = totp.provisioning_uri(
            name=user.email,
            issuer_name="Blood Donation Platform",
        )

        # 2. Encrypt secret using application Fernet key
        encrypted_secret = encrypt_totp_secret(raw_secret, self.settings.totp_encryption_key)
        user.totp_secret_enc = encrypted_secret
        user.totp_enabled = False
        user.totp_last_used_step = None

        # 3. Generate 8 backup recovery codes (BR-AUTH-10 / T-AUTH-10-a)
        recovery_codes = [generate_recovery_code(10) for _ in range(8)]
        code_hashes = [hash_token(code) for code in recovery_codes]

        self.recovery_repo.replace_recovery_codes(user.id, code_hashes)
        self.user_repo.db.flush()

        return MfaSetupResult(
            secret=raw_secret,
            provisioning_uri=provisioning_uri,
            recovery_codes=recovery_codes,
        )

    def confirm_mfa_setup(self, user_id: uuid.UUID, code: str) -> bool:
        """Confirm enrollment by verifying the first 6-digit TOTP code, enabling MFA on success."""
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        if not user.totp_secret_enc:
            raise ValidationError("No pending MFA setup found. Please initiate MFA setup first.")

        raw_secret = decrypt_totp_secret(user.totp_secret_enc, self.settings.totp_encryption_key)
        totp = pyotp.TOTP(raw_secret)
        now = self.clock.now()
        cur_step = totp.timecode(now)

        # Enforce acceptance window ±1 step (BR-AUTH-09)
        clean_code = code.strip()
        matching_step: int | None = None
        for offset in [-1, 0, 1]:
            step = cur_step + offset
            if secrets.compare_digest(totp.generate_otp(step), clean_code):
                matching_step = step
                break

        if matching_step is None:
            raise InvalidCredentialsError("Invalid MFA confirmation code.")

        # Successfully confirmed: enable TOTP and record step
        user.totp_enabled = True
        user.totp_last_used_step = matching_step
        self.user_repo.db.flush()
        return True

    # ==========================================================================
    # 2. Code Verification Flow (BR-AUTH-09, T-AUTH-09, T-AUTH-10)
    # ==========================================================================

    def verify_totp(
        self,
        user_id: uuid.UUID,
        code: str,
        *,
        session: AuthSession | None = None,
    ) -> bool:
        """Verify 6-digit TOTP code against ±1 window and enforce anti-replay (totp_last_used_step).

        Conforms strictly to BR-AUTH-09:
        - Acceptance window: current step ± 1 step (30 seconds each).
        - Replay defense: code matching a step <= user.totp_last_used_step is rejected.
        - Interim session rate limiting: 5 failed attempts immediately revokes the session.
        """
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        if not user.totp_enabled or not user.totp_secret_enc:
            self._handle_interim_failure(session)
            raise InvalidCredentialsError("Two-factor authentication is not enabled.")

        # Check if session is already revoked
        if session is not None and session.revoked_at is not None:
            raise InvalidCredentialsError("Session has been revoked.")

        raw_secret = decrypt_totp_secret(user.totp_secret_enc, self.settings.totp_encryption_key)
        totp = pyotp.TOTP(raw_secret)
        now = self.clock.now()
        cur_step = totp.timecode(now)

        clean_code = code.strip()
        matching_step: int | None = None
        for offset in [-1, 0, 1]:
            step = cur_step + offset
            if secrets.compare_digest(totp.generate_otp(step), clean_code):
                matching_step = step
                break

        # 1. Reject codes outside ±1 window (T-AUTH-09-b)
        if matching_step is None:
            self._handle_interim_failure(session)
            raise InvalidCredentialsError("Invalid MFA code.")

        # 2. Reject reused code from same or earlier step (BR-AUTH-09 / T-AUTH-09-c)
        if user.totp_last_used_step is not None and matching_step <= user.totp_last_used_step:
            self._handle_interim_failure(session)
            raise InvalidCredentialsError("MFA code has already been used.")

        # Verification successful: record step and promote session
        user.totp_last_used_step = matching_step
        user.last_login_at = now

        if session is not None:
            session.mfa_verified = True
            self._clear_interim_attempts(session.id)

        self.user_repo.db.flush()
        return True

    def verify_recovery_code(
        self,
        user_id: uuid.UUID,
        code: str,
        *,
        session: AuthSession | None = None,
    ) -> bool:
        """Verify single-use backup recovery code against stored hashes (BR-AUTH-10 / T-AUTH-10-b)."""
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        if not user.totp_enabled:
            self._handle_interim_failure(session)
            raise InvalidCredentialsError("Two-factor authentication is not enabled.")

        if session is not None and session.revoked_at is not None:
            raise InvalidCredentialsError("Session has been revoked.")

        clean_code = code.strip().upper()
        code_hash = hash_token(clean_code)

        recovery_entry = self.recovery_repo.get_unused_code(user_id, code_hash)
        if recovery_entry is None:
            self._handle_interim_failure(session)
            raise InvalidCredentialsError("Invalid or already used recovery code.")

        now = self.clock.now()
        self.recovery_repo.mark_used(recovery_entry, now)
        user.last_login_at = now

        if session is not None:
            session.mfa_verified = True
            self._clear_interim_attempts(session.id)

        self.user_repo.db.flush()
        return True

    def verify_mfa(
        self,
        user_id: uuid.UUID,
        *,
        code: str | None = None,
        recovery_code: str | None = None,
        session: AuthSession | None = None,
    ) -> bool:
        """Unified verification gateway accepting either a 6-digit TOTP code or backup recovery code."""
        if bool(code) == bool(recovery_code):
            raise ValidationError("Provide either 'code' or 'recovery_code', not both or neither.")

        if code is not None:
            return self.verify_totp(user_id, code, session=session)
        if recovery_code is not None:
            return self.verify_recovery_code(user_id, recovery_code, session=session)

        raise ValidationError("No verification factor provided.")

    # ==========================================================================
    # 3. Disabling & Administrative Reset (BR-AUTH-17, BR-AUTH-13)
    # ==========================================================================

    def disable_mfa(self, actor: User, target_user: User) -> None:
        """Disable two-factor authentication for a supervisor account (BR-AUTH-17).

        Administrators CANNOT disable MFA on their accounts under any circumstances.
        """
        if target_user.role == "admin":
            raise ForbiddenError("Administrators cannot disable two-factor authentication.")

        if actor.id != target_user.id:
            raise ForbiddenError(
                "Users can only disable two-factor authentication for their own account."
            )

        target_user.totp_secret_enc = None
        target_user.totp_enabled = False
        target_user.totp_last_used_step = None
        self.recovery_repo.delete_all_for_user(target_user.id)
        self.user_repo.db.flush()

    def reset_mfa_by_admin(self, admin_user: User, target_user: User) -> None:
        """Administratively reset MFA for a user who lost their device and codes (BR-USER-08 / BR-AUTH-13).

        Revokes all active sessions for the target user immediately.
        """
        if admin_user.role != "admin":
            raise ForbiddenError("Only administrators may reset user two-factor authentication.")

        target_user.totp_secret_enc = None
        target_user.totp_enabled = False
        target_user.totp_last_used_step = None
        self.recovery_repo.delete_all_for_user(target_user.id)

        # Drop all sessions immediately per BR-AUTH-13
        self.session_service.revoke_all_user_sessions(target_user.id)
        self.user_repo.db.flush()
