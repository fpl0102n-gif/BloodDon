"""Photo and file deletion queue models.

Implements P2.4 per docs/02-database.md §5.10 & §5.14, docs/06-business-rules.md §8,
and docs/07-test-cases.md §5.7:
- Photo: General atmosphere or individual donor photo.
- BR-PHOTO-01/02: kind is 'general' or 'individual'.
- T-PHOTO-02-a: CheckConstraint enforces that individual photos must have a donor_id,
  and general photos must NOT have a donor_id: ((kind = 'individual') = (donor_id IS NOT NULL)).
- BR-PHOTO-05: mime_type restricted to image/jpeg, image/png, image/webp.
- BR-PHOTO-06: size_bytes must be positive (> 0).
- BR-PHOTO-12: caption length <= 200 characters.
- BR-CSITE-06: Composite foreign key to campaign_sites(campaign_id, site_id) ON DELETE RESTRICT.
- FileDeletionQueue: Staging queue for asynchronous storage deletion.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.campaign import CampaignSite
    from app.models.donor import Donor
    from app.models.user import User


class Photo(Base):
    """Event or donor photo entity associated with a campaign site."""

    __tablename__ = "photos"

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
    storage_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="general",
        server_default="general",
    )
    caption: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    donor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("donors.id", ondelete="CASCADE"),
        nullable=True,
    )
    mime_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
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
    donor: Mapped["Donor | None"] = relationship(
        "Donor",
    )
    uploader: Mapped["User | None"] = relationship(
        "User",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "site_id"],
            ["campaign_sites.campaign_id", "campaign_sites.site_id"],
            name="fk_photos_campaign_site",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN ('general', 'individual')",
            name="kind",
        ),
        CheckConstraint(
            "(kind = 'individual' AND donor_id IS NOT NULL) OR (kind = 'general' AND donor_id IS NULL)",
            name="individual_donor",
        ),
        CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="mime_type",
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="size_bytes",
        ),
        CheckConstraint(
            "caption IS NULL OR char_length(caption) <= 200",
            name="caption_len",
        ),
        Index(
            "ix_photos_campaign_site",
            "campaign_id",
            "site_id",
        ),
        Index(
            "ix_photos_donor_id",
            "donor_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Photo(id={self.id}, campaign_id={self.campaign_id}, "
            f"site_id={self.site_id}, kind='{self.kind}', storage_key='{self.storage_key}')>"
        )


class FileDeletionQueue(Base):
    """Queue of storage objects pending background physical deletion."""

    __tablename__ = "file_deletion_queue"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    storage_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_file_deletion_queue_deleted_at",
            "deleted_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<FileDeletionQueue(id={self.id}, storage_key='{self.storage_key}', "
            f"created_at={self.created_at}, deleted_at={self.deleted_at})>"
        )
