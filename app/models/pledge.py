"""Pledge model (declarations of intent).

Implements P2.3 per docs/02-database.md §5.8, docs/06-business-rules.md §5,
and docs/07-test-cases.md §5.4:
- BR-PLEDGE-01: Pure declaration of intent, not bound to any donor entity.
- BR-PLEDGE-04: first_name between 2 and 40 characters; optional normalized phone.
- BR-PLEDGE-06: Partial unique index (campaign_id, site_id, phone) WHERE phone IS NOT NULL.
- BR-CSITE-06: Composite foreign key to campaign_sites(campaign_id, site_id) ON DELETE CASCADE.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.campaign import CampaignSite


class Pledge(Base):
    """Pledge entity representing a symbolic intention to attend a blood drive."""

    __tablename__ = "pledges"

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
    phone: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    campaign_site: Mapped["CampaignSite"] = relationship(
        "CampaignSite",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "site_id"],
            ["campaign_sites.campaign_id", "campaign_sites.site_id"],
            name="fk_pledges_campaign_site",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "char_length(first_name) BETWEEN 2 AND 40",
            name="first_name_len",
        ),
        CheckConstraint(
            "phone IS NULL OR phone ~ '^\\+?[0-9]{8,15}$'",
            name="phone_format",
        ),
        Index(
            "uq_pledges_campaign_site_phone",
            "campaign_id",
            "site_id",
            "phone",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
        ),
        Index(
            "ix_pledges_campaign_site",
            "campaign_id",
            "site_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Pledge(id={self.id}, campaign_id={self.campaign_id}, "
            f"site_id={self.site_id}, first_name='{self.first_name}')>"
        )
