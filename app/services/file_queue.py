"""File deletion queue domain service.

Per:
- docs/05-architecture.md §12 (Services layer: domain logic, zero commit)
- docs/04-api.md §20 (Admin file queue management)
- docs/06-business-rules.md §8 (BR-PHOTO-10: Asynchronous physical file deletion via queue)
- docs/07-test-cases.md §5.15 (T-FILE-01..03)
"""

import uuid
from collections.abc import Sequence

from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.models.photo import FileDeletionQueue
from app.models.user import User
from app.repositories.file_queue import FileQueueRepository
from app.services.audit import AuditService
from app.storage.base import Storage
from app.storage.factory import create_storage


class FileQueueService:
    """Service managing storage object deletion queue inspection and processing."""

    def __init__(
        self,
        file_queue_repo: FileQueueRepository,
        storage: Storage | None = None,
        clock: Clock | None = None,
        audit_service: AuditService | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.file_queue_repo = file_queue_repo
        self.settings = settings or get_settings()
        self.storage = storage or create_storage(self.settings)
        self.clock = clock or SystemClock()
        self.audit_service = audit_service

    def list_pending(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[FileDeletionQueue], int]:
        """List queue items awaiting physical storage deletion (FIFO order)."""
        return self.file_queue_repo.list_pending(limit=limit, offset=offset)

    def mark_deleted(
        self,
        queue_id: int,
        user: User,
    ) -> FileDeletionQueue:
        """Mark a queue entry as physically deleted (docs/04-api.md §20, T-FILE-02-a).

        Raises:
            NotFoundError: If queue_id does not exist.
        """
        item = self.file_queue_repo.get_by_id(queue_id)
        if item is None:
            raise NotFoundError(f"File queue entry with ID '{queue_id}' not found.")

        updated = self.file_queue_repo.mark_deleted(item, self.clock.now())

        if self.audit_service:
            self.audit_service.log_event(
                action="file_queue.mark_deleted",
                entity_type="file_deletion_queue",
                entity_id=None,
                actor_user_id=user.id,
                details={
                    "queue_id": item.id,
                    "storage_key": item.storage_key,
                },
            )

        return updated

    def process_queue(
        self,
        *,
        batch_size: int = 50,
        dry_run: bool = False,
        actor_user_id: uuid.UUID | None = None,
    ) -> int:
        """Process a batch of pending deletions from physical storage (T-FILE-03-a).

        Unlinks files from the storage backend and stamps deleted_at on the queue items.
        Returns the number of files processed.
        """
        items = self.file_queue_repo.get_pending_batch(limit=batch_size)
        if not items:
            return 0

        now = self.clock.now()
        for item in items:
            if not dry_run:
                self.storage.delete(item.storage_key)
                self.file_queue_repo.mark_deleted(item, now)

        if not dry_run and self.audit_service:
            self.audit_service.log_event(
                action="file_queue.batch_process",
                entity_type="file_deletion_queue",
                entity_id=None,
                actor_user_id=actor_user_id,
                details={
                    "processed_count": len(items),
                    "batch_size": batch_size,
                },
            )

        return len(items)
