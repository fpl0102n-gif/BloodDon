"""Audit repository managing append-only persistence of audit trail entries.

Per docs/05-architecture.md §5.2, docs/02-database.md §5.12, and docs/06-business-rules.md §13 (BR-AUDIT-02):
- Encapsulates pure SQLAlchemy queries for AuditLog persistence.
- Strictly adheres to layer dependency rules: imports only models and core.
- No direct commit/rollback calls; transaction is managed at the request boundary.
- Append-only semantics: creates and queries log records; never modifies or deletes.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog, PhoneRevealLog


class AuditRepository:
    """Repository handling database operations for AuditLog entries."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, entry: AuditLog) -> AuditLog:
        """Add audit log entry and flush to current database transaction.

        Conforms to BR-AUDIT-03:
        - Flushes in the caller's transaction so that audit failures abort the operation.
        - Does NOT commit; transaction boundary is maintained by the request lifecycle.
        """
        self.db.add(entry)
        self.db.flush()
        return entry

    def get_by_id(self, log_id: int) -> AuditLog | None:
        """Fetch audit log record by primary key."""
        return self.db.get(AuditLog, log_id)

    def list_events(
        self,
        *,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Query audit log records ordered newest first (for testing and administrative inspection)."""
        stmt = select(AuditLog)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if entity_type is not None:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        if actor_user_id is not None:
            stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
        if from_date is not None:
            stmt = stmt.where(AuditLog.at >= from_date)
        if to_date is not None:
            stmt = stmt.where(AuditLog.at <= to_date)

        stmt = stmt.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars().all())


class PhoneRevealRepository:
    """Repository handling database operations for PhoneRevealLog entries (BR-EMERG-08, P7.2).

    Per docs/02-database.md §5.13:
    - Tracks emergency reveal operations by salted IP hash.
    - donor_id has NO foreign key so retention purging of donors does not invalidate reveal counts.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, entry: PhoneRevealLog) -> PhoneRevealLog:
        """Add phone reveal log entry and flush to current database transaction."""
        self.db.add(entry)
        self.db.flush()
        return entry

    def count_by_ip_since(self, ip_hash: str, since: datetime) -> int:
        """Count reveals for a given hashed IP since a specified timestamp."""
        stmt = (
            select(func.count(PhoneRevealLog.id))
            .where(PhoneRevealLog.ip_hash == ip_hash)
            .where(PhoneRevealLog.created_at >= since)
        )
        return int(self.db.execute(stmt).scalar() or 0)

    def count_total_since(self, since: datetime) -> int:
        """Count total reveals across all IPs since a specified timestamp."""
        stmt = select(func.count(PhoneRevealLog.id)).where(PhoneRevealLog.created_at >= since)
        return int(self.db.execute(stmt).scalar() or 0)

    def delete_older_than(self, cutoff: datetime) -> int:
        """Hard delete phone reveal logs older than cutoff timestamp per BR-PRIV-11."""
        stmt = delete(PhoneRevealLog).where(PhoneRevealLog.created_at < cutoff)
        result = self.db.execute(stmt)
        return int(getattr(result, "rowcount", 0))
