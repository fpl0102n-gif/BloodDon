"""Pydantic schemas for file deletion queue inspection and management.

Per:
- docs/04-api.md §20 (Admin file queue schemas)
- docs/07-test-cases.md §5.15 (T-FILE-01..03)
"""

from datetime import datetime

from pydantic import Field

from app.schemas.common import AppBaseModel


class FileQueueItemOut(AppBaseModel):
    """Schema representing a single file deletion queue item."""

    id: int
    storage_key: str
    created_at: datetime
    deleted_at: datetime | None = None


class FileQueueListOut(AppBaseModel):
    """Paginated list of file deletion queue items."""

    items: list[FileQueueItemOut]
    total: int
    limit: int
    offset: int


class FileQueueProcessIn(AppBaseModel):
    """Parameters for physical file queue batch processing."""

    batch_size: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of items to process in this run",
    )
    dry_run: bool = Field(
        default=False,
        description="Simulate processing without unlinking files or updating the database",
    )


class FileQueueProcessOut(AppBaseModel):
    """Result of batch processing file deletion queue."""

    processed_count: int = Field(description="Number of queued items processed")
    dry_run: bool = Field(description="Whether operation was executed in dry-run mode")
