"""Admin file deletion queue API router.

Per:
- docs/04-api.md §20 (Admin file queue endpoints)
  - GET /admin/file-queue (file_queue:manage)
  - POST /admin/file-queue/{id}/mark-deleted (file_queue:manage)
- docs/06-business-rules.md §8 (BR-PHOTO-10: Asynchronous physical file deletion via queue)
- docs/07-test-cases.md §5.15 (T-FILE-01..03)
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_file_queue_service, require
from app.policy.permissions import Permission
from app.schemas.file_queue import (
    FileQueueItemOut,
    FileQueueListOut,
    FileQueueProcessIn,
    FileQueueProcessOut,
)
from app.services.file_queue import FileQueueService

router = APIRouter(prefix="/admin/file-queue", tags=["Admin File Queue"])


@router.get(
    "",
    response_model=FileQueueListOut,
    status_code=status.HTTP_200_OK,
    summary="List files awaiting physical deletion from storage",
)
def list_pending_files(
    file_queue_service: Annotated[FileQueueService, Depends(get_file_queue_service)],
    user: Annotated[Any, Depends(require(Permission.FILE_QUEUE_MANAGE))],
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Page limit (max 100)"),
    ] = 25,
    offset: Annotated[
        int,
        Query(ge=0, description="Page offset"),
    ] = 0,
) -> FileQueueListOut:
    """List pending file deletion queue entries awaiting background processing (docs/04-api.md §20)."""
    del user  # Authenticated by require(Permission.FILE_QUEUE_MANAGE)
    items, total = file_queue_service.list_pending(limit=limit, offset=offset)
    out_items = [
        FileQueueItemOut(
            id=item.id,
            storage_key=item.storage_key,
            created_at=item.created_at,
            deleted_at=item.deleted_at,
        )
        for item in items
    ]
    return FileQueueListOut(items=out_items, total=total, limit=limit, offset=offset)


@router.post(
    "/{item_id}/mark-deleted",
    response_model=FileQueueItemOut,
    status_code=status.HTTP_200_OK,
    summary="Mark queue item as deleted after storage file removal",
)
def mark_file_deleted(
    item_id: int,
    file_queue_service: Annotated[FileQueueService, Depends(get_file_queue_service)],
    user: Annotated[Any, Depends(require(Permission.FILE_QUEUE_MANAGE))],
) -> FileQueueItemOut:
    """Mark file queue entry as deleted with current timestamp (docs/04-api.md §20, T-FILE-02-a)."""
    item = file_queue_service.mark_deleted(queue_id=item_id, user=user)
    return FileQueueItemOut(
        id=item.id,
        storage_key=item.storage_key,
        created_at=item.created_at,
        deleted_at=item.deleted_at,
    )


@router.post(
    "/process",
    response_model=FileQueueProcessOut,
    status_code=status.HTTP_200_OK,
    summary="Process a batch of pending deletions from physical storage",
)
def process_file_queue(
    file_queue_service: Annotated[FileQueueService, Depends(get_file_queue_service)],
    user: Annotated[Any, Depends(require(Permission.FILE_QUEUE_MANAGE))],
    payload: FileQueueProcessIn | None = None,
) -> FileQueueProcessOut:
    """Process pending file deletion queue entries and unlink files from storage (BR-FILE-02, T-FILE-03-a)."""
    data = payload or FileQueueProcessIn()
    count = file_queue_service.process_queue(
        batch_size=data.batch_size,
        dry_run=data.dry_run,
        actor_user_id=user.id,
    )
    return FileQueueProcessOut(processed_count=count, dry_run=data.dry_run)
