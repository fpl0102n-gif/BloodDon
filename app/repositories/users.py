"""User repository managing database queries for User entities.

Per docs/05-architecture.md §5.2 and docs/02-database.md §5.2:
- Encapsulates pure SQLAlchemy queries for user lookups, lockout states, and credential updates.
- Strictly adheres to layer dependency rules: imports only models and core.
- No direct commit/rollback calls; transaction is managed at the request boundary.
"""

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    """Repository handling database operations for User accounts."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Fetch user by primary key ID."""
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        """Fetch user by case-insensitive normalized email address."""
        clean_email = email.strip().lower()
        stmt = select(User).where(func.lower(User.email) == clean_email)
        return self.db.execute(stmt).scalar_one_or_none()

    def increment_failed_login(
        self,
        user: User,
        locked_until: datetime | None = None,
    ) -> None:
        """Increment failed login attempts counter and update lockout expiration."""
        user.failed_login_count += 1
        user.locked_until = locked_until
        self.db.flush()

    def reset_failed_logins(
        self,
        user: User,
        *,
        last_login_at: datetime | None = None,
    ) -> None:
        """Clear failed login counters and optionally record successful login timestamp."""
        user.failed_login_count = 0
        user.locked_until = None
        if last_login_at is not None:
            user.last_login_at = last_login_at
        self.db.flush()

    def update_password_hash(self, user: User, new_hash: str) -> None:
        """Update user's stored Argon2id password hash upon rehash or password change."""
        user.password_hash = new_hash
        self.db.flush()

    def unlock(self, user_id: uuid.UUID) -> bool:
        """Administratively clear account lockout and failed attempt counters."""
        stmt = (
            update(User).where(User.id == user_id).values(failed_login_count=0, locked_until=None)
        )
        result = self.db.execute(stmt)
        return int(cast(CursorResult[Any], result).rowcount) > 0
