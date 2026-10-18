"""Pydantic schemas for photo entities and upload endpoints.

Per:
- docs/04-api.md §12.1 (POST /supervisor/photos response: { id, kind, caption, url, donor_id, created_at })
- docs/04-api.md §12.2 (GET /supervisor/photos pagination)
- docs/06-business-rules.md §8 (BR-PHOTO-01..12)
- CLAUDE.md / GEMINI.md Rule 6.4: extra="forbid"
"""

import uuid
from datetime import datetime

from pydantic import ConfigDict, Field

from app.schemas.common import AppBaseModel


class PhotoOut(AppBaseModel):
    """Public/supervisor representation of a photo entity."""

    id: uuid.UUID
    kind: str = Field(description="Photo category: 'general' or 'individual'")
    caption: str | None = Field(default=None, description="Optional photo caption")
    url: str = Field(description="Accessible URL for the photo media asset")
    donor_id: uuid.UUID | None = Field(
        default=None,
        description="Associated donor ID for individual photos; None for general photos",
    )
    created_at: datetime = Field(description="Timestamp when photo was uploaded")

    model_config = ConfigDict(extra="forbid")


class PhotoListOut(AppBaseModel):
    """Paginated collection of photo entities."""

    items: list[PhotoOut] = Field(description="List of photo items")
    total: int = Field(ge=0, description="Total matching photo count")
    limit: int = Field(ge=1, le=100, description="Pagination page size limit")
    offset: int = Field(ge=0, description="Pagination page offset")

    model_config = ConfigDict(extra="forbid")


class PhotoPublicOut(AppBaseModel):
    """Public representation of a campaign photo (docs/04-api.md §6.8)."""

    id: uuid.UUID
    url: str = Field(description="Accessible URL for the photo media asset")
    caption: str | None = Field(default=None, description="Optional photo caption")
    site_id: uuid.UUID = Field(description="ID of the participating site where photo was taken")
    kind: str = Field(description="Photo category: 'general' or 'individual'")

    model_config = ConfigDict(extra="forbid")


class PhotoPublicListOut(AppBaseModel):
    """Paginated collection of public campaign photos (docs/04-api.md §6.8)."""

    items: list[PhotoPublicOut] = Field(description="List of public photo items")
    total: int = Field(ge=0, description="Total matching photo count")
    limit: int = Field(ge=1, le=100, description="Pagination page size limit")
    offset: int = Field(ge=0, description="Pagination page offset")

    model_config = ConfigDict(extra="forbid")
