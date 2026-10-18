"""Pydantic schemas for Free Posts (P9.1).

Per:
- docs/04-api.md §19 (Admin Posts API: GET, POST, PATCH, DELETE)
- docs/04-api.md §6.10 (Public Posts API: GET /public/posts, GET /public/posts/{slug})
- docs/06-business-rules.md (BR-POST-01..05)
- docs/07-test-cases.md §5.15 (T-POST-01..05)
"""

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import AppBaseModel

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
SLUG_REGEX = re.compile(SLUG_PATTERN)
MAX_CONTENT_BYTES = 100 * 1024  # 100 KB = 102,400 bytes (BR-POST-04)


def validate_slug_format(value: str) -> str:
    """Validate slug format strictly per BR-POST-01 and BR-CAMP-02."""
    slug = value.strip()
    if not slug:
        raise ValueError("Slug cannot be empty")
    if not SLUG_REGEX.match(slug):
        raise ValueError(
            "Slug must consist of lowercase Latin letters, numbers, and single hyphens without leading or trailing hyphens"
        )
    return slug


def validate_content_size(value: str | None) -> str | None:
    """Validate Markdown content size does not exceed 100 KB per BR-POST-04."""
    if value is not None:
        byte_len = len(value.encode("utf-8"))
        if byte_len > MAX_CONTENT_BYTES:
            raise ValueError(
                f"Content size ({byte_len} bytes) exceeds maximum limit of {MAX_CONTENT_BYTES} bytes (100 KB)"
            )
    return value


class PostCreate(AppBaseModel):
    """Admin payload to create a new free post or article (BR-POST-01..05)."""

    title: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Article or post title",
        ),
    ]
    slug: Annotated[
        str,
        Field(
            description="URL-safe unique identifier (lowercase alphanumeric and hyphens)",
            examples=["importance-of-blood-donation"],
        ),
    ]
    campaign_id: Annotated[
        uuid.UUID | None,
        Field(
            default=None,
            description="Optional associated campaign ID (BR-POST-05)",
        ),
    ] = None
    excerpt: Annotated[
        str | None,
        Field(
            default=None,
            max_length=500,
            description="Short summary or excerpt for cards and previews",
        ),
    ] = None
    content: Annotated[
        str | None,
        Field(
            default=None,
            description="Full post content in Markdown (max 100 KB)",
        ),
    ] = None
    cover_url: Annotated[
        str | None,
        Field(
            default=None,
            max_length=1000,
            description="Optional cover image URL",
        ),
    ] = None
    is_published: Annotated[
        bool,
        Field(
            default=False,
            description="Publication flag (BR-POST-02: populates published_at on first publish)",
        ),
    ] = False

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


class PostUpdate(AppBaseModel):
    """Admin payload to modify post attributes (BR-POST-01..05)."""

    title: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None
    slug: Annotated[str | None, Field(default=None)] = None
    campaign_id: Annotated[uuid.UUID | None, Field(default=None)] = None
    excerpt: Annotated[str | None, Field(default=None, max_length=500)] = None
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


class PostAdminOut(AppBaseModel):
    """Complete post entity returned to administrators."""

    id: uuid.UUID
    campaign_id: uuid.UUID | None
    slug: str
    title: str
    excerpt: str | None
    content: str | None
    cover_url: str | None
    is_published: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PostAdminSummaryOut(AppBaseModel):
    """Post summary without full content for admin list overviews."""

    id: uuid.UUID
    campaign_id: uuid.UUID | None
    slug: str
    title: str
    excerpt: str | None
    cover_url: str | None
    is_published: bool
    published_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PostPublicCardOut(AppBaseModel):
    """Public post card representation for list views (BR-POST-03)."""

    id: uuid.UUID
    slug: str
    title: str
    excerpt: str | None
    cover_url: str | None
    published_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PostPublicOut(AppBaseModel):
    """Public detail representation of a published post (BR-POST-03, BR-POST-04)."""

    id: uuid.UUID
    slug: str
    title: str
    excerpt: str | None
    content: str | None
    cover_url: str | None
    published_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")
