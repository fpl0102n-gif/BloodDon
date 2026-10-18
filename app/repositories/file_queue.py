"""Repository for file deletion queue operations.

Per:
- docs/02-database.md §7 (file_deletion_queue table specification)
- docs/05-architecture.md §11 (Repositories layer: queries and mutations without commit)
- docs/06-business-rules.md §8 (BR-PHOTO-10: storage_key staging into queue)
- docs/07-test-cases.md §5.15 (T-FILE-01..03)
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.photo import FileDeletionQueue


class FileQueueRepository:
    """SQLAlchemy repository managing file deletion queue entries."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, item_id: int) -> FileDeletionQueue | None:
        """Fetch queue item by primary key."""
        stmt = select(FileDeletionQueue).where(FileDeletionQueue.id == item_id)
        return self.db.scalar(stmt)

    def list_pending(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[FileDeletionQueue], int]:
        """Fetch pending queue entries (where deleted_at IS NULL), ordered FIFO (oldest first)."""
        count_stmt = select(func.count(FileDeletionQueue.id)).where(
            FileDeletionQueue.deleted_at.is_(None)
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = (
            select(FileDeletionQueue)
            .where(FileDeletionQueue.deleted_at.is_(None))
            .order_by(FileDeletionQueue.created_at.asc(), FileDeletionQueue.id.asc())
            .offset(offset)
            .limit(limit)
        )
        items = self.db.scalars(stmt).all()
        return items, total

    def get_pending_batch(self, limit: int = 50) -> Sequence[FileDeletionQueue]:
        """Fetch up to `limit` pending items for worker processing."""
        stmt = (
            select(FileDeletionQueue)
            .where(FileDeletionQueue.deleted_at.is_(None))
            .order_by(FileDeletionQueue.created_at.asc(), FileDeletionQueue.id.asc())
            .limit(limit)
        )
        return self.db.scalars(stmt).all()

    def enqueue(self, storage_key: str) -> FileDeletionQueue:
        """Enqueue a storage key pending physical deletion."""
        entry = FileDeletionQueue(storage_key=storage_key)
        self.db.add(entry)
        self.db.flush()
        return entry

    def mark_deleted(self, item: FileDeletionQueue, deleted_at: datetime) -> FileDeletionQueue:
        """Update an existing queue item with the physical deletion timestamp."""
        item.deleted_at = deleted_at
        self.db.flush()
        return item
