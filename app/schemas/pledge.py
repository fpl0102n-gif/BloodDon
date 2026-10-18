"""Pledge Pydantic schemas.

Per:
- docs/04-api.md §7 (POST /public/campaigns/{slug}/pledges)
- docs/06-business-rules.md §5 (BR-PLEDGE-01..11)
- docs/07-test-cases.md §5.4 (T-PLEDGE-01..11)
"""

import uuid
from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import AppBaseModel, NormalizedName, PhoneNumber


class PledgeCreateIn(AppBaseModel):
    """Payload for submitting an intention to attend a blood drive (BR-PLEDGE-01..04)."""

    site_id: uuid.UUID = Field(
        ...,
        description="ID of participating donation site where user intends to attend (BR-PLEDGE-03)",
    )
    first_name: NormalizedName = Field(
        ...,
        min_length=2,
        max_length=40,
        description="First name of pledger (2-40 characters per BR-PLEDGE-04)",
    )
    phone: PhoneNumber | None = Field(
        default=None,
        description="Optional contact phone number (8-15 digits normalized per BR-GEN-01, BR-PLEDGE-04)",
    )
    website: str | None = Field(
        default=None,
        max_length=255,
        description="Honeypot field (must be empty; if filled, silently ignored per BR-PLEDGE-05)",
    )

    @field_validator("phone", mode="before")
    @classmethod
    def empty_phone_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("website", mode="before")
    @classmethod
    def empty_website_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v
