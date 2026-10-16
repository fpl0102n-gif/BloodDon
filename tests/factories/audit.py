"""Test factories for audit logs, emergency phone reveal logs, and rate limit events."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog, PhoneRevealLog, RateLimitEvent


def create_audit_log(
    session: Session | None = None,
    *,
    action: str = "donor.status_change",
    entity_type: str = "donor",
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    id_: int | None = None,
    at: datetime | None = None,
    **kwargs: Any,
) -> AuditLog:
    """Create an AuditLog entry (details must NOT contain PII)."""
    if details is None:
        details = {"old_status": "attended", "new_status": "donated"}

    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id or uuid.uuid4(),
        actor_user_id=actor_user_id,
        details=details,
        at=at or datetime.now(UTC),
        **kwargs,
    )
    if id_ is not None:
        entry.id = id_

    if session is not None:
        session.add(entry)
        session.flush()

    return entry


def create_phone_reveal_log(
    session: Session | None = None,
    *,
    donor_id: uuid.UUID | None = None,
    ip_hash: str = "test_salted_ip_hash",
    id_: int | None = None,
    created_at: datetime | None = None,
    **kwargs: Any,
) -> PhoneRevealLog:
    """Create a PhoneRevealLog entry."""
    entry = PhoneRevealLog(
        donor_id=donor_id or uuid.uuid4(),
        ip_hash=ip_hash,
        created_at=created_at or datetime.now(UTC),
        **kwargs,
    )
    if id_ is not None:
        entry.id = id_

    if session is not None:
        session.add(entry)
        session.flush()

    return entry


def create_rate_limit_event(
    session: Session | None = None,
    *,
    key_hash: str = "test_key_hash",
    action: str = "login",
    id_: int | None = None,
    created_at: datetime | None = None,
    **kwargs: Any,
) -> RateLimitEvent:
    """Create a RateLimitEvent entry."""
    entry = RateLimitEvent(
        key_hash=key_hash,
        action=action,
        created_at=created_at or datetime.now(UTC),
        **kwargs,
    )
    if id_ is not None:
        entry.id = id_

    if session is not None:
        session.add(entry)
        session.flush()

    return entry
