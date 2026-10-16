"""SQLAlchemy models for Campaigns, Sites, and Campaign Participation.

Implements P2.1 per docs/02-database.md §3, §4, §5.1, §5.2, §5.3,
docs/05-architecture.md §4, and docs/06-business-rules.md §3 & §4:
- Campaign: Complete lifecycle status (upcoming, live, ended), slug constraint,
  is_published flag, is_current partial unique index (only one current campaign),
  and JSONB frozen final_stats.
- Site: Permanent physical blood collection centers / medical clubs.
- CampaignSite: Join table defining site participation in a specific campaign,
  with operating time order checks and composite primary key (campaign_id, site_id).
"""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Campaign(Base):
    """Blood donation day campaign event and blog post.

    Governed by BR-CAMP-* rules:
    - Exactly one campaign can be marked is_current at any time.
    - Slug must match lower-case alphanumeric kebab format.
    - Status transitions: upcoming -> live -> ended.
    """

    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    cover_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    campaign_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="upcoming",
        server_default="upcoming",
        index=True,
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    final_stats: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    personal_data_purged_at: Mapped[datetime | None] = mapped_column(
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
    campaign_sites: Mapped[list["CampaignSite"]] = relationship(
        "CampaignSite",
        back_populates="campaign",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name="slug_format",
        ),
        CheckConstraint(
            "status IN ('upcoming', 'live', 'ended')",
            name="status",
        ),
        Index(
            "uq_campaigns_single_current",
            "is_current",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Campaign(id={self.id}, slug='{self.slug}', status='{self.status}', "
            f"is_current={self.is_current}, is_published={self.is_published})>"
        )


class Site(Base):
    """Permanent physical donation center / medical club location.

    Governed by BR-SITE-* rules:
    - Sites persist across campaigns.
    - Deactivation sets is_active=false rather than hard deleting historical associations.
    """

    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    logo_key: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    address: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    map_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )
    longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
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
    campaign_sites: Mapped[list["CampaignSite"]] = relationship(
        "CampaignSite",
        back_populates="site",
    )

    def __repr__(self) -> str:
        return f"<Site(id={self.id}, name='{self.name}', is_active={self.is_active})>"


class CampaignSite(Base):
    """Participation of a specific Site in a Campaign event.

    Governed by BR-CSITE-* rules:
    - Composite primary key (campaign_id, site_id).
    - Composite foreign key target for pledges, donors, and photos.
    - Operating time check: end_time > start_time when both are provided.
    """

    __tablename__ = "campaign_sites"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    start_time: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )
    end_time: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )
    supervisors_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    campaign: Mapped["Campaign"] = relationship(
        "Campaign",
        back_populates="campaign_sites",
    )
    site: Mapped["Site"] = relationship(
        "Site",
        back_populates="campaign_sites",
    )

    __table_args__ = (
        CheckConstraint(
            "end_time IS NULL OR start_time IS NULL OR end_time > start_time",
            name="time_order",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<CampaignSite(campaign_id={self.campaign_id}, site_id={self.site_id}, "
            f"start_time={self.start_time}, end_time={self.end_time})>"
        )
