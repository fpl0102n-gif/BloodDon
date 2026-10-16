"""Audit logging, emergency phone reveal tracking, and rate limiting event models.

Implements P2.4 per docs/02-database.md §5.12, §5.13, §5.15,
docs/06-business-rules.md §13 (AUDIT), and docs/07-test-cases.md §5.12:
- AuditLog: Append-only ledger recording security and business domain events.
  BR-AUDIT-01: details must NEVER contain PII (names, phones, blood types).
- PhoneRevealLog: Records emergency phone number lookups by salted IP hash for rate limit window auditing.
  docs/02-database.md §5.13: donor_id has NO foreign key so donor purge does not alter reveal logs.
- RateLimitEvent: Persistent audit/event model for rate-limiting events.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class AuditLog(Base):
    """Append-only audit trail recording sensitive security and domain actions."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=True,
    )

    # Relationships
    actor: Mapped["User | None"] = relationship(
        "User",
    )

    __table_args__ = (
        Index(
            "ix_audit_log_at",
            "at",
        ),
        Index(
            "ix_audit_log_entity",
            "entity_type",
            "entity_id",
        ),
        Index(
            "ix_audit_log_actor",
            "actor_user_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog(id={self.id}, at={self.at}, action='{self.action}', "
            f"actor_user_id={self.actor_user_id}, entity='{self.entity_type}')>"
        )


class PhoneRevealLog(Base):
    """Tracks rate-limited reveal attempts for emergency phone lookups."""

    __tablename__ = "phone_reveal_log"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    ip_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    donor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_phone_reveal_log_ip_created_at",
            "ip_hash",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PhoneRevealLog(id={self.id}, ip_hash='{self.ip_hash}', "
            f"donor_id={self.donor_id}, created_at={self.created_at})>"
        )


class RateLimitEvent(Base):
    """Tracks rate limiting events across sliding windows."""

    __tablename__ = "rate_limit_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    key_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_rate_limit_events_window",
            "key_hash",
            "action",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<RateLimitEvent(id={self.id}, action='{self.action}', "
            f"key_hash='{self.key_hash}', created_at={self.created_at})>"
        )
