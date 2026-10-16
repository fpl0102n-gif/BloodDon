"""Test factories for photos and file deletion queue."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.photo import FileDeletionQueue, Photo

_photo_counter = 0


def create_photo(
    session: Session | None = None,
    *,
    campaign_id: uuid.UUID,
    site_id: uuid.UUID,
    id_: uuid.UUID | None = None,
    storage_key: str | None = None,
    kind: str = "general",
    caption: str | None = "صورة توثيقية للأجواء",
    donor_id: uuid.UUID | None = None,
    mime_type: str = "image/jpeg",
    size_bytes: int = 1024 * 500,
    uploaded_by: uuid.UUID | None = None,
    created_at: datetime | None = None,
    **kwargs: Any,
) -> Photo:
    """Create a Photo model instance obeying kind/donor_id constraints."""
    global _photo_counter
    _photo_counter += 1

    if storage_key is None:
        storage_key = f"campaigns/{campaign_id}/sites/{site_id}/photo_{_photo_counter}_{uuid.uuid4().hex[:8]}.jpg"

    # Enforce ck_photos_individual_donor:
    # ((kind = 'individual' AND donor_id IS NOT NULL) OR (kind = 'general' AND donor_id IS NULL))
    if kind == "individual" and donor_id is None:
        donor_id = uuid.uuid4()
    elif kind == "general":
        donor_id = None

    photo = Photo(
        id=id_ or uuid.uuid4(),
        campaign_id=campaign_id,
        site_id=site_id,
        storage_key=storage_key,
        kind=kind,
        caption=caption,
        donor_id=donor_id,
        mime_type=mime_type,
        size_bytes=size_bytes,
        uploaded_by=uploaded_by,
        created_at=created_at or datetime.now(UTC),
        **kwargs,
    )

    if session is not None:
        session.add(photo)
        session.flush()

    return photo


def create_file_deletion_queue(
    session: Session | None = None,
    *,
    storage_key: str | None = None,
    id_: int | None = None,
    created_at: datetime | None = None,
    deleted_at: datetime | None = None,
    **kwargs: Any,
) -> FileDeletionQueue:
    """Create a FileDeletionQueue entry."""
    if storage_key is None:
        storage_key = f"stale_photos/{uuid.uuid4().hex}.jpg"

    entry = FileDeletionQueue(
        storage_key=storage_key,
        created_at=created_at or datetime.now(UTC),
        deleted_at=deleted_at,
        **kwargs,
    )
    if id_ is not None:
        entry.id = id_

    if session is not None:
        session.add(entry)
        session.flush()

    return entry
