"""Session repository managing database queries for AuthSession entities.

Per docs/05-architecture.md §5.2 and docs/02-database.md §5.7:
- Encapsulates pure SQLAlchemy queries for session management.
- Strictly adheres to layer dependency rules: imports only models and core.
- No direct commit/rollback calls; transaction is managed at the request boundary.
"""

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, joinedload

from app.models.user import AuthSession


class SessionRepository:
    """Repository handling database operations for server-side AuthSession entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, session: AuthSession) -> AuthSession:
        """Persist a new AuthSession record and flush to transaction."""
        self.db.add(session)
        self.db.flush()
        return session

    def get_by_id(self, session_id_hash: str) -> AuthSession | None:
        """Fetch session by SHA-256 ID hash, eagerly loading associated User."""
        stmt = (
            select(AuthSession)
            .options(joinedload(AuthSession.user))
            .where(AuthSession.id == session_id_hash)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def touch_last_seen(self, session_id_hash: str, now: datetime) -> None:
        """Update last_seen_at timestamp for active session."""
        stmt = update(AuthSession).where(AuthSession.id == session_id_hash).values(last_seen_at=now)
        self.db.execute(stmt)

    def revoke(self, session_id_hash: str, now: datetime) -> None:
        """Revoke a single session by setting revoked_at timestamp."""
        stmt = (
            update(AuthSession)
            .where(AuthSession.id == session_id_hash, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        self.db.execute(stmt)

    def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        now: datetime,
        *,
        except_session_id_hash: str | None = None,
    ) -> int:
        """Revoke all active sessions for a user, optionally exempting a specific session."""
        stmt = update(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        if except_session_id_hash is not None:
            stmt = stmt.where(AuthSession.id != except_session_id_hash)

        stmt = stmt.values(revoked_at=now)
        result = self.db.execute(stmt)
        return int(cast(CursorResult[Any], result).rowcount)

    def list_active_for_user(
        self,
        user_id: uuid.UUID,
        now: datetime,
    ) -> list[AuthSession]:
        """List all unrevoked, non-expired sessions for a user ordered by activity."""
        stmt = (
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.last_seen_at.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def delete_expired(self, before: datetime) -> int:
        """Hard delete expired sessions past retention threshold per BR-PRIV-11."""
        stmt = delete(AuthSession).where(AuthSession.expires_at < before)
        result = self.db.execute(stmt)
        return int(cast(CursorResult[Any], result).rowcount)
