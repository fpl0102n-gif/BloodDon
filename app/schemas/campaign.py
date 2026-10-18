"""Pydantic schemas for Campaigns (P4.2).

Per:
- docs/04-api.md §13 (Admin Campaigns: GET, POST, PATCH, DELETE)
- docs/04-api.md §6 (Public Campaigns: GET /public/campaigns, /current, /{slug})
- docs/04-api.md §23.1 (Public Campaign Schema)
- docs/06-business-rules.md §3 (BR-CAMP-01..18)
- docs/07-test-cases.md §5.2 (T-CAMP-01, 02, 13, 14, 15)
"""

import re
import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import AppBaseModel

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
SLUG_REGEX = re.compile(SLUG_PATTERN)
MAX_CONTENT_BYTES = 100 * 1024  # 100 KB = 102,400 bytes (BR-CAMP-15)


def validate_slug_format(value: str) -> str:
    """Validate slug format strictly per BR-CAMP-02."""
    slug = value.strip()
    if not slug:
        raise ValueError("Slug cannot be empty")
    if not SLUG_REGEX.match(slug):
        raise ValueError(
            "Slug must consist of lowercase Latin letters, numbers, and single hyphens without leading or trailing hyphens"
        )
    return slug


def validate_content_size(value: str | None) -> str | None:
    """Validate Markdown content size does not exceed 100 KB per BR-CAMP-15."""
    if value is not None:
        byte_len = len(value.encode("utf-8"))
        if byte_len > MAX_CONTENT_BYTES:
            raise ValueError(
                f"Content size ({byte_len} bytes) exceeds maximum limit of {MAX_CONTENT_BYTES} bytes (100 KB)"
            )
    return value


class CampaignCreate(AppBaseModel):
    """Admin payload to create a new campaign event / post (BR-CAMP-01)."""

    title: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Campaign title (e.g., 'اليوم الوطني للتبرع بالدم 2026')",
        ),
    ]
    slug: Annotated[
        str,
        Field(
            description="URL-safe unique identifier (lowercase alphanumeric and hyphens)",
            examples=["national-blood-drive-2026"],
        ),
    ]
    campaign_date: Annotated[
        date,
        Field(
            description="Date on which the donation drive takes place",
        ),
    ]
    summary: Annotated[
        str | None,
        Field(
            default=None,
            max_length=500,
            description="Short summary for cards and social previews",
        ),
    ]
    content: Annotated[
        str | None,
        Field(
            default=None,
            description="Full campaign post in Markdown (max 100 KB)",
        ),
    ]
    cover_url: Annotated[
        str | None,
        Field(
            default=None,
            max_length=1000,
            description="Optional URL of the cover image",
        ),
    ]

    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        cleaned = " ".join(v.strip().split())
        if not cleaned:
            raise ValueError("Title cannot be blank")
        return cleaned

    @field_validator("slug")
    @classmethod
    def clean_slug(cls, v: str) -> str:
        return validate_slug_format(v)

    @field_validator("content")
    @classmethod
    def clean_content(cls, v: str | None) -> str | None:
        return validate_content_size(v)


class CampaignUpdate(AppBaseModel):
    """Admin payload to modify campaign attributes (BR-CAMP-12, 14, 15)."""

    title: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None
    slug: Annotated[str | None, Field(default=None)] = None
    campaign_date: Annotated[date | None, Field(default=None)] = None
    summary: Annotated[str | None, Field(default=None, max_length=500)] = None
    content: Annotated[str | None, Field(default=None)] = None
    cover_url: Annotated[str | None, Field(default=None, max_length=1000)] = None
    is_published: Annotated[bool | None, Field(default=None)] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = " ".join(v.strip().split())
            if not cleaned:
                raise ValueError("Title cannot be blank")
            return cleaned
        return v

    @field_validator("slug")
    @classmethod
    def clean_slug(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_slug_format(v)
        return v

    @field_validator("content")
    @classmethod
    def clean_content(cls, v: str | None) -> str | None:
        return validate_content_size(v)


class CampaignOut(AppBaseModel):
    """Complete campaign entity returned to administrators."""

    id: uuid.UUID
    slug: str
    title: str
    summary: str | None
    content: str | None
    cover_url: str | None
    campaign_date: date
    status: str
    is_published: bool
    is_current: bool
    final_stats: dict[str, Any] | None
    ended_at: datetime | None
    personal_data_purged_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class CampaignEndSiteStat(AppBaseModel):
    """Site breakdown entry in final_stats upon campaign conclusion."""

    site_id: uuid.UUID
    name: str
    pledges: int
    present: int
    donated: int

    model_config = ConfigDict(extra="forbid")


class CampaignFinalStatsOut(AppBaseModel):
    """Frozen final statistics returned upon ending a campaign (BR-CAMP-09, docs/04 §13)."""

    pledges: int
    present: int
    donated: int
    heroes: int
    by_site: list[CampaignEndSiteStat]
    frozen_at: datetime

    model_config = ConfigDict(extra="forbid")


class CampaignSummaryOut(AppBaseModel):
    """Campaign summary without full content for admin lists."""

    id: uuid.UUID
    slug: str
    title: str
    summary: str | None
    cover_url: str | None
    campaign_date: date
    status: str
    is_published: bool
    is_current: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class CampaignPublicOut(AppBaseModel):
    """Public representation of an active/published campaign (docs/04 §23.1).

    Omits internal administrative flags: is_current, personal_data_purged_at, etc.
    """

    id: uuid.UUID
    slug: str
    title: str
    summary: str | None
    content: str | None
    cover_url: str | None
    campaign_date: date
    status: str
    final_stats: dict[str, Any] | None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class CampaignPublicCardOut(AppBaseModel):
    """Public campaign card representation for list views (docs/04 §6.2)."""

    id: uuid.UUID
    slug: str
    title: str
    summary: str | None
    cover_url: str | None
    campaign_date: date
    status: str

    model_config = ConfigDict(from_attributes=True, extra="forbid")
