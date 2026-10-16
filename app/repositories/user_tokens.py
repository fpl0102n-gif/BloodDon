"""UserToken repository managing database queries for one-time tokens.

Per docs/05-architecture.md §5.2 and docs/02-database.md §5.5:
- Encapsulates pure SQLAlchemy queries for UserToken lookup, creation, and invalidation.
- Strictly adheres to layer dependency rules: imports only models and core.
- No direct commit/rollback calls; transaction is managed at the request boundary.
"""

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models.user import UserToken


class UserTokenRepository:
    """Repository handling database operations for UserToken entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_token(
        self,
        user_id: uuid.UUID,
        purpose: str,
        token_hash: str,
        expires_at: datetime,
    ) -> UserToken:
        """Create and persist a new hashed one-time token."""
        token = UserToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
        )
        self.db.add(token)
        self.db.flush()
        return token

    def get_by_hash(self, token_hash: str) -> UserToken | None:
        """Fetch token by exact SHA-256 hash."""
        stmt = select(UserToken).where(UserToken.token_hash == token_hash)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_active_token(
        self,
        token_hash: str,
        purpose: str,
        now: datetime,
    ) -> UserToken | None:
        """Fetch token matching hash and purpose that is unused and unexpired."""
        stmt = select(UserToken).where(
            UserToken.token_hash == token_hash,
            UserToken.purpose == purpose,
            UserToken.used_at.is_(None),
            UserToken.expires_at > now,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def mark_as_used(self, token: UserToken, now: datetime) -> None:
        """Mark a token as consumed with the specified timestamp."""
        token.used_at = now
        self.db.flush()

    def invalidate_active_tokens(
        self,
        user_id: uuid.UUID,
        purpose: str,
        now: datetime,
    ) -> int:
        """Invalidate all unconsumed tokens for a user and purpose (e.g. on resend)."""
        stmt = (
            update(UserToken)
            .where(
                UserToken.user_id == user_id,
                UserToken.purpose == purpose,
                UserToken.used_at.is_(None),
                UserToken.expires_at > now,
            )
            .values(used_at=now)
        )
        result = self.db.execute(stmt)
        self.db.flush()
        return int(cast(CursorResult[Any], result).rowcount)
