"""MFA repository managing recovery codes in the database (P3.7).

Per docs/02-database.md §5.6, docs/03-auth-permissions.md §7, and docs/05-architecture.md §5.2:
- Encapsulates pure SQLAlchemy queries for MfaRecoveryCode entities.
- Strictly adheres to layer dependency rules: imports only models and core.
- No direct commit/rollback calls; transaction is managed at the request boundary.
"""

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models.user import MfaRecoveryCode


class MfaRecoveryCodeRepository:
    """Repository handling database operations for hashed MFA backup recovery codes."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def replace_recovery_codes(
        self,
        user_id: uuid.UUID,
        code_hashes: list[str],
    ) -> list[MfaRecoveryCode]:
        """Replace all existing recovery codes for a user with newly generated hashed codes."""
        # Remove any prior codes
        self.delete_all_for_user(user_id)

        entities: list[MfaRecoveryCode] = [
            MfaRecoveryCode(
                user_id=user_id,
                code_hash=h,
                used_at=None,
            )
            for h in code_hashes
        ]
        self.db.add_all(entities)
        self.db.flush()
        return entities

    def get_unused_code(
        self,
        user_id: uuid.UUID,
        code_hash: str,
    ) -> MfaRecoveryCode | None:
        """Fetch an unused recovery code matching the provided SHA-256 hash."""
        stmt = select(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == user_id,
            MfaRecoveryCode.code_hash == code_hash,
            MfaRecoveryCode.used_at.is_(None),
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def mark_used(
        self,
        recovery_code: MfaRecoveryCode,
        now: datetime,
    ) -> None:
        """Mark a recovery code as consumed (single-use per BR-AUTH-10 / T-AUTH-10-b)."""
        recovery_code.used_at = now
        self.db.flush()

    def count_unused(self, user_id: uuid.UUID) -> int:
        """Count the number of remaining unused recovery codes for a user."""
        stmt = select(func.count(MfaRecoveryCode.id)).where(
            MfaRecoveryCode.user_id == user_id,
            MfaRecoveryCode.used_at.is_(None),
        )
        return int(self.db.execute(stmt).scalar() or 0)

    def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        """Delete all recovery codes for a user upon MFA reset or deactivation."""
        stmt = delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        result = self.db.execute(stmt)
        return int(cast(CursorResult[Any], result).rowcount)
