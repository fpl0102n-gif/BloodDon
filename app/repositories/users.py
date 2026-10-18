"""User repository managing database queries for User entities.

Per docs/05-architecture.md §5.2 and docs/02-database.md §5.2:
- Encapsulates pure SQLAlchemy queries for user lookups, lockout states, and credential updates.
- Strictly adheres to layer dependency rules: imports only models and core.
- No direct commit/rollback calls; transaction is managed at the request boundary.
"""

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select, text, update
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

    def activate_user(self, user: User, password_hash: str) -> None:
        """Activate user account and set initial password hash from accepted invitation."""
        user.password_hash = password_hash
        user.is_active = True
        user.failed_login_count = 0
        user.locked_until = None
        self.db.flush()

    def reset_password(self, user: User, password_hash: str) -> None:
        """Update user password hash and clear any active lockout state."""
        user.password_hash = password_hash
        user.failed_login_count = 0
        user.locked_until = None
        self.db.flush()

    def unlock(self, user_id: uuid.UUID) -> bool:
        """Administratively clear account lockout and failed attempt counters."""
        stmt = (
            update(User).where(User.id == user_id).values(failed_login_count=0, locked_until=None)
        )
        result = self.db.execute(stmt)
        return int(cast(CursorResult[Any], result).rowcount) > 0

    def create(self, user: User) -> User:
        """Persist a new User record and flush."""
        self.db.add(user)
        self.db.flush()
        return user

    def count_active_admins(self) -> int:
        """Count total currently active administrator accounts."""
        stmt = select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))
        return int(self.db.execute(stmt).scalar() or 0)

    def acquire_admin_lock(self) -> None:
        """Acquire transaction-scoped advisory lock for admin concurrency control."""
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(text("SELECT pg_advisory_xact_lock(74220)"))

    def list_users(
        self,
        *,
        role: str | None = None,
        site_id: uuid.UUID | None = None,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[User]:
        """List users with optional filtering and pagination."""
        stmt = select(User)
        if role is not None:
            stmt = stmt.where(User.role == role)
        if site_id is not None:
            stmt = stmt.where(User.site_id == site_id)
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        stmt = stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars().all())

    def bulk_deactivate_supervisors(self) -> int:
        """Deactivate all active supervisors in a single atomic update."""
        stmt = (
            update(User)
            .where(User.role == "supervisor", User.is_active.is_(True))
            .values(is_active=False)
        )
        result = self.db.execute(stmt)
        self.db.flush()
        return int(cast(CursorResult[Any], result).rowcount)

    def delete_mfa_recovery_codes(self, user_id: uuid.UUID) -> int:
        """Delete all MFA recovery codes for a user upon MFA reset."""
        from app.models.user import MfaRecoveryCode

        stmt = delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        result = self.db.execute(stmt)
        self.db.flush()
        return int(cast(CursorResult[Any], result).rowcount)
