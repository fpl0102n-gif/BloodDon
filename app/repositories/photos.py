"""Photo entity repository.

Per:
- docs/05-architecture.md §11 (Repositories layer: SQLAlchemy queries only, no business logic or commits).
- docs/02-database.md §5.10 (photos table schema & queries).
- docs/06-business-rules.md §8 (BR-PHOTO-01..14).
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.donor import Donor
from app.models.photo import FileDeletionQueue, Photo


class PhotoRepository:
    """Repository handling database operations for Photo entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, photo: Photo) -> Photo:
        """Persist a new Photo entity and flush."""
        self.db.add(photo)
        self.db.flush()
        return photo

    def get_by_id(self, photo_id: uuid.UUID) -> Photo | None:
        """Fetch a single photo by its primary key ID."""
        stmt = select(Photo).where(Photo.id == photo_id)
        return self.db.scalar(stmt)

    def list_by_site(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[Photo], int]:
        """Fetch photos belonging to a specific site in a campaign (newest first).

        Returns:
            tuple[Sequence[Photo], int]: (items, total_count)
        """
        count_stmt = select(func.count(Photo.id)).where(
            Photo.campaign_id == campaign_id,
            Photo.site_id == site_id,
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = (
            select(Photo)
            .where(
                Photo.campaign_id == campaign_id,
                Photo.site_id == site_id,
            )
            .order_by(Photo.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = self.db.scalars(stmt).all()
        return items, total

    def list_by_campaign(
        self,
        campaign_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[Photo], int]:
        """Fetch photos for a campaign, with optional site filtering (newest first)."""
        conditions = [Photo.campaign_id == campaign_id]
        if site_id is not None:
            conditions.append(Photo.site_id == site_id)

        count_stmt = select(func.count(Photo.id)).where(*conditions)
        total = self.db.scalar(count_stmt) or 0

        stmt = (
            select(Photo)
            .where(*conditions)
            .order_by(Photo.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = self.db.scalars(stmt).all()
        return items, total

    def list_public_by_campaign(
        self,
        campaign_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[Photo], int]:
        """Fetch public photos for a published campaign (BR-PHOTO-11).

        General photos are always shown. Individual photos are shown only if the donor has
        consent_photo = True and anonymized_at is None.
        """
        base_filter = [
            Photo.campaign_id == campaign_id,
            or_(
                Photo.kind == "general",
                and_(
                    Photo.kind == "individual",
                    Donor.id.is_not(None),
                    Donor.consent_photo.is_(True),
                    Donor.anonymized_at.is_(None),
                ),
            ),
        ]
        if site_id is not None:
            base_filter.append(Photo.site_id == site_id)

        count_stmt = (
            select(func.count(Photo.id))
            .outerjoin(Donor, Photo.donor_id == Donor.id)
            .where(*base_filter)
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = (
            select(Photo)
            .outerjoin(Donor, Photo.donor_id == Donor.id)
            .where(*base_filter)
            .order_by(Photo.created_at.desc(), Photo.id.desc())
            .offset(offset)
            .limit(limit)
        )
        items = self.db.scalars(stmt).all()
        return items, total

    def delete_photo_and_enqueue(self, photo: Photo) -> None:
        """Delete photo entity and enqueue its storage_key into file_deletion_queue (BR-PHOTO-10)."""
        queue_entry = FileDeletionQueue(storage_key=photo.storage_key)
        self.db.add(queue_entry)
        self.db.delete(photo)
        self.db.flush()

    def delete(self, photo: Photo) -> None:
        """Hard delete a photo entity without queuing (internal helper)."""
        self.db.delete(photo)
        self.db.flush()
