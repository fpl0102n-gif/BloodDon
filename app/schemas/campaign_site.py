"""Pydantic schemas for CampaignSite participation (P4.3).

Per:
- docs/04-api.md §13.1 (Admin Campaign Sites: GET, PUT, DELETE)
- docs/04-api.md §6.4 (Public Campaign Sites: GET /public/campaigns/{slug}/sites)
- docs/06-business-rules.md §4 (BR-CSITE-01..06)
- docs/07-test-cases.md §5.3 (T-CSITE-01..06)
"""

import uuid
from datetime import time
from typing import Annotated

from pydantic import ConfigDict, Field, model_validator

from app.schemas.common import AppBaseModel


class CampaignSitePut(AppBaseModel):
    """Admin payload to add or update site participation in a campaign (BR-CSITE-02, 03, 05)."""

    start_time: Annotated[
        time | None,
        Field(
            default=None,
            description="Operating start time (e.g., 08:30:00)",
        ),
    ] = None
    end_time: Annotated[
        time | None,
        Field(
            default=None,
            description="Operating end time (e.g., 17:00:00)",
        ),
    ] = None
    supervisors_text: Annotated[
        str | None,
        Field(
            default=None,
            max_length=500,
            description="Display text of on-site supervisory team (presentation only, BR-CSITE-05)",
        ),
    ] = None
    notes: Annotated[
        str | None,
        Field(
            default=None,
            max_length=1000,
            description="Logistical notes for internal administrative reference",
        ),
    ] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_time_order(self) -> "CampaignSitePut":
        """Enforce BR-CSITE-03: end_time must be strictly greater than start_time when both are provided."""
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("end_time must be strictly greater than start_time")
        return self


class CampaignSiteOut(AppBaseModel):
    """Admin representation of a participating site in a campaign."""

    campaign_id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    site_address: str
    start_time: time | None
    end_time: time | None
    supervisors_text: str | None
    notes: str | None

    model_config = ConfigDict(extra="forbid")


class CampaignSitePublicOut(AppBaseModel):
    """Public representation of a participating site in a campaign (docs/04 §6.4)."""

    site_id: uuid.UUID
    name: str
    logo_url: str | None
    address: str
    map_url: str | None
    start_time: time | None
    end_time: time | None
    supervisors_text: str | None
    description: str | None
    pledges_count: int

    model_config = ConfigDict(extra="forbid")
