"""Donor model (actual blood donors).

Implements P2.3 per docs/02-database.md §5.9, docs/06-business-rules.md §6-§7,
and docs/07-test-cases.md §5.5-§5.6:
- BR-DONOR-03: Initial status is 'attended'.
- BR-DONOR-04: first_name between 1 and 40 chars; optional last_name <= 40 chars; blood_type check.
- BR-DONOR-05: Client can provide UUID id for idempotent retries.
- BR-DONOR-07: donated_at is filled only when status='donated'.
- BR-CONSENT-01: Four independent explicit boolean consent flags, defaulting to false.
- BR-CONSENT-02: Phone requires consent (consent_keep_phone OR consent_public_phone).
- BR-CONSENT-03: Public phone requires phone AND blood_type.
- BR-CSITE-06: Composite foreign key to campaign_sites(campaign_id, site_id) ON DELETE RESTRICT.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.campaign import CampaignSite
    from app.models.user import User


class Donor(Base):
    """Actual blood donor entity recorded at a specific campaign site."""

    __tablename__ = "donors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    first_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    last_name: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    phone: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    blood_type: Mapped[str | None] = mapped_column(
        String(5),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="attended",
        server_default=text("'attended'"),
    )
    consent_keep_phone: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    consent_hero_name: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    consent_photo: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    consent_public_phone: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    registered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    donated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    campaign_site: Mapped["CampaignSite"] = relationship(
        "CampaignSite",
    )
    registered_by_user: Mapped["User | None"] = relationship(
        "User",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "site_id"],
            ["campaign_sites.campaign_id", "campaign_sites.site_id"],
            name="fk_donors_campaign_site",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "char_length(first_name) BETWEEN 1 AND 40",
            name="first_name_len",
        ),
        CheckConstraint(
            "last_name IS NULL OR char_length(last_name) <= 40",
            name="last_name_len",
        ),
        CheckConstraint(
            "phone IS NULL OR phone ~ '^\\+?[0-9]{8,15}$'",
            name="phone_format",
        ),
        CheckConstraint(
            "blood_type IS NULL OR blood_type IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')",
            name="blood_type",
        ),
        CheckConstraint(
            "status IN ('attended', 'donated')",
            name="status",
        ),
        CheckConstraint(
            "phone IS NULL OR consent_keep_phone OR consent_public_phone",
            name="phone_needs_consent",
        ),
        CheckConstraint(
            "NOT consent_public_phone OR (phone IS NOT NULL AND blood_type IS NOT NULL)",
            name="public_phone_needs_data",
        ),
        CheckConstraint(
            "(status = 'donated') OR donated_at IS NULL",
            name="donated_at",
        ),
        Index(
            "ix_donors_campaign_site_status",
            "campaign_id",
            "site_id",
            "status",
        ),
        Index(
            "ix_donors_emergency",
            "blood_type",
            postgresql_where=text(
                "consent_public_phone AND status = 'donated' AND anonymized_at IS NULL"
            ),
        ),
        Index(
            "ix_donors_site_first_name_lower",
            "site_id",
            func.lower(first_name),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Donor(id={self.id}, campaign_id={self.campaign_id}, "
            f"site_id={self.site_id}, first_name='{self.first_name}', "
            f"status='{self.status}')>"
        )
