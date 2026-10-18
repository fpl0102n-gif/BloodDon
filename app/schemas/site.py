"""Pydantic schemas for donation Site resources.

Per:
- docs/04-api.md §14 (Admin Sites API: GET, POST, PATCH, DELETE)
- docs/04-api.md §6.9 (Public Sites API: GET /public/sites)
- docs/06-business-rules.md §4 (BR-SITE-01..04)
- docs/06-business-rules.md §2 (BR-GEN-02 normalized names, BR-GEN-06 extra='forbid')
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from app.schemas.common import AppBaseModel, NormalizedName


class SiteBase(AppBaseModel):
    """Shared baseline attributes for physical donation sites."""

    name: NormalizedName = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Official club / center name (normalized per BR-GEN-02)",
    )
    address: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Physical location or campus address",
    )
    map_url: str | None = Field(
        default=None,
        max_length=500,
        description="Online map URL (Google Maps, OpenStreetMap)",
    )
    latitude: Decimal | None = Field(
        default=None,
        ge=-90,
        le=90,
        description="Latitude geographic coordinate (-90 to +90)",
    )
    longitude: Decimal | None = Field(
        default=None,
        ge=-180,
        le=180,
        description="Longitude geographic coordinate (-180 to +180)",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Public informational description",
    )
    logo_key: str | None = Field(
        default=None,
        max_length=255,
        description="Server storage key for the club/site logo",
    )


class SiteCreate(SiteBase):
    """Request payload for creating a new donation site via POST /admin/sites."""

    pass


class SiteUpdate(AppBaseModel):
    """Request payload for updating an existing site via PATCH /admin/sites/{id}."""

    name: NormalizedName | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Updated site name",
    )
    address: str | None = Field(
        default=None,
        min_length=2,
        max_length=255,
        description="Updated address",
    )
    map_url: str | None = Field(
        default=None,
        max_length=500,
        description="Updated map URL",
    )
    latitude: Decimal | None = Field(
        default=None,
        ge=-90,
        le=90,
        description="Updated latitude (-90 to +90)",
    )
    longitude: Decimal | None = Field(
        default=None,
        ge=-180,
        le=180,
        description="Updated longitude (-180 to +180)",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Updated description",
    )
    logo_key: str | None = Field(
        default=None,
        max_length=255,
        description="Updated logo key",
    )
    is_active: bool | None = Field(
        default=None,
        description="Active status (deactivation hides from public view per BR-SITE-02)",
    )


class SiteOut(AppBaseModel):
    """Comprehensive site entity returned to administrative users."""

    id: uuid.UUID
    name: str
    address: str
    map_url: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    description: str | None
    logo_key: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )


class SitePublicOut(AppBaseModel):
    """Publicly visible site representation returned on public endpoints."""

    id: uuid.UUID
    name: str
    address: str
    map_url: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    description: str | None
    logo_key: str | None

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )
