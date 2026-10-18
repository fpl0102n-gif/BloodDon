"""Public Statistics, Heroes, Counters, and Ranking schemas (P6.3).

Per:
- docs/04-api.md §6.5 (GET /public/campaigns/{slug}/counters)
- docs/04-api.md §6.6 (GET /public/campaigns/{slug}/heroes)
- docs/04-api.md §6.7 (GET /public/campaigns/{slug}/ranking)
- docs/06-business-rules.md §9 (BR-HERO-*, BR-RANK-*, BR-COUNT-*)
- docs/05-architecture.md §8 (Schemas layer: strict validation, extra='forbid')
"""

import uuid

from pydantic import Field

from app.schemas.common import AppBaseModel, PaginatedResponse


class CampaignCountersOut(AppBaseModel):
    """Aggregated campaign counter metrics (BR-COUNT-01..03, docs/04 §6.5)."""

    pledges_count: int = Field(ge=0, description="Total pledges / intentions to attend")
    present_count: int = Field(ge=0, description="Total donors present / attended (any status)")
    donated_count: int = Field(ge=0, description="Total successful donors (status=donated)")
    heroes_count: int = Field(
        ge=0, description="Total heroes (status=donated AND consent_hero_name=True)"
    )


class HeroPublicOut(AppBaseModel):
    """Individual hero representation in public heroes roster (BR-HERO-02 ★, docs/04 §6.6).

    STRICT PRIVACY GUARD:
    Per BR-HERO-02 ★, exactly and only first_name, site_id, and site_name may be returned.
    No last_name, blood_type, phone, registered_by, or demographic data.
    """

    first_name: str = Field(description="First name of the hero donor")
    site_id: uuid.UUID = Field(description="Participating donation site ID")
    site_name: str = Field(description="Participating donation site name")


class HeroPublicListOut(PaginatedResponse[HeroPublicOut]):
    """Paginated collection of heroes for public display."""


class SiteRankingOut(AppBaseModel):
    """Site ranking item per campaign (BR-RANK-01..05, docs/04 §6.7)."""

    rank: int = Field(ge=1, description="Standard competition rank (1, 1, 3...)")
    site_id: uuid.UUID = Field(description="Participating site ID")
    site_name: str = Field(description="Participating site name")
    logo_url: str | None = Field(default=None, description="Site logo URL or storage key")
    donors_count: int = Field(ge=0, description="Total successful donors count")
