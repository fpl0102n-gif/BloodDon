"""Session domain service implementing server-side session management (P3.2).

Per:
- docs/03-auth-permissions.md §8 (Server-side sessions, SHA-256 hashed storage, timeouts).
- docs/06-business-rules.md §11 (BR-AUTH-11 server sessions, BR-AUTH-12 durations, BR-AUTH-13 revocations).
- docs/11-decisions.md (D-07 server sessions, D-16 role timeouts).
"""

import uuid
from datetime import datetime, timedelta

from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.errors import NotAuthenticatedError, SessionExpiredError
from app.core.security import generate_secure_token, hash_token
from app.models.user import AuthSession, User
from app.repositories.sessions import SessionRepository


class SessionService:
    """Service implementing server-side session lifecycle, timeouts, and revocations."""

    def __init__(
        self,
        repository: SessionRepository,
        *,
        clock: Clock | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.repo = repository
        self.clock = clock or get_clock()
        self.settings = settings or get_settings()

    def create_session(
        self,
        user_id: uuid.UUID,
        role: str,
        *,
        ip_hash: str | None = None,
        user_agent: str | None = None,
        mfa_verified: bool = False,
    ) -> tuple[str, AuthSession]:
        """Create a new server-side session and return raw token along with stored session entity.

        Conforms to BR-AUTH-11:
        - Generates cryptographically secure 256-bit random raw token.
        - Hashes raw token with SHA-256 and stores only the hash in auth_sessions.id.
        - Computes absolute expiration from user role per BR-AUTH-12 and D-16.
        """
        raw_token = generate_secure_token(32)
        token_hash = hash_token(raw_token)
        now = self.clock.now()

        # Determine absolute timeout per role (BR-AUTH-12 / D-16)
        if role == "supervisor":
            absolute_min = self.settings.session_absolute_supervisor_min
        else:
            absolute_min = self.settings.session_absolute_admin_min

        expires_at = now + timedelta(minutes=absolute_min)

        session = AuthSession(
            id=token_hash,
            user_id=user_id,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            revoked_at=None,
            mfa_verified=mfa_verified,
            ip_hash=ip_hash,
            user_agent=user_agent[:500] if user_agent else None,
        )

        created_session = self.repo.create(session)
        return raw_token, created_session

    def validate_session(self, raw_token: str) -> tuple[AuthSession, User]:
        """Validate raw token against stored session, verifying timeouts and account status.

        Conforms to BR-AUTH-11, BR-AUTH-12, and BR-AUTH-13:
        - Rejects missing, corrupted, or nonexistent sessions with NotAuthenticatedError.
        - Rejects explicitly revoked sessions with SessionExpiredError.
        - Drops sessions immediately if user account is disabled (is_active=False).
        - Enforces role-specific idle and absolute timeouts.
        - Throttles last_seen_at updates (60-second window) to minimize database write load.
        """
        if not raw_token or not raw_token.strip():
            raise NotAuthenticatedError("Missing or empty session token.")

        token_hash = hash_token(raw_token)
        session = self.repo.get_by_id(token_hash)

        if session is None:
            raise NotAuthenticatedError("Session not found or invalid.")

        if session.revoked_at is not None:
            raise SessionExpiredError("Session has been revoked.")

        user = session.user
        if user is None or not user.is_active:
            raise NotAuthenticatedError("User account is inactive or disabled.")

        now = self.clock.now()

        # 1. Absolute timeout check
        if now >= session.expires_at:
            raise SessionExpiredError("Session absolute timeout reached.")

        # 2. Idle timeout check (BR-AUTH-12)
        if user.role == "supervisor":
            idle_min = self.settings.session_idle_supervisor_min
        else:
            idle_min = self.settings.session_idle_admin_min

        idle_delta = timedelta(minutes=idle_min)
        if (now - session.last_seen_at) > idle_delta:
            raise SessionExpiredError("Session idle timeout reached.")

        # 3. Activity touch throttling (update last_seen_at if >= 60 seconds have elapsed)
        if (now - session.last_seen_at).total_seconds() >= 60:
            self.repo.touch_last_seen(session.id, now)
            session.last_seen_at = now

        return session, user

    def revoke_session(self, raw_token: str) -> None:
        """Revoke a single session by raw token (e.g. single-device logout)."""
        token_hash = hash_token(raw_token)
        self.repo.revoke(token_hash, self.clock.now())

    def revoke_session_by_hash(self, session_id_hash: str) -> None:
        """Revoke a single session by its stored SHA-256 ID hash."""
        self.repo.revoke(session_id_hash, self.clock.now())

    def revoke_all_user_sessions(
        self,
        user_id: uuid.UUID,
        *,
        except_raw_token: str | None = None,
    ) -> int:
        """Revoke all active sessions for a user, optionally exempting the current session (BR-AUTH-13)."""
        except_hash = hash_token(except_raw_token) if except_raw_token else None
        return self.repo.revoke_all_for_user(
            user_id,
            self.clock.now(),
            except_session_id_hash=except_hash,
        )

    def mark_mfa_verified(self, raw_token: str) -> None:
        """Promote an interim session to MFA-verified status upon successful second factor verification."""
        token_hash = hash_token(raw_token)
        session = self.repo.get_by_id(token_hash)
        if session is not None and session.revoked_at is None:
            session.mfa_verified = True

    def list_active_sessions(self, user_id: uuid.UUID) -> list[AuthSession]:
        """List unexpired, unrevoked sessions for an account."""
        return self.repo.list_active_for_user(user_id, self.clock.now())

    def cleanup_expired_sessions(self, *, before: datetime | None = None) -> int:
        """Hard delete sessions expired before cutoff per BR-PRIV-11."""
        cutoff = before or self.clock.now()
        return self.repo.delete_expired(cutoff)
