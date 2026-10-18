"""Public Campaigns API router (P4.2).

Per:
- docs/04-api.md §6.1 (GET /public/campaigns/current)
- docs/04-api.md §6.2 (GET /public/campaigns)
- docs/04-api.md §6.3 (GET /public/campaigns/{slug})
- docs/06-business-rules.md §3 (BR-CAMP-03: only published campaigns are visible)
- docs/07-test-cases.md §5.2 (T-CAMP-03-a, T-CAMP-03-b, T-CAMP-03-c)
- docs/03-auth-permissions.md §11.1 (Permission.CAMPAIGN_READ)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    check_pledge_rate_limit,
    get_campaign_service,
    get_photo_service,
    get_pledge_service,
    get_stats_service,
    require,
)
from app.policy import Permission
from app.schemas.campaign import CampaignPublicCardOut, CampaignPublicOut
from app.schemas.campaign_site import CampaignSitePublicOut
from app.schemas.photo import PhotoPublicListOut
from app.schemas.pledge import PledgeCreateIn
from app.schemas.stats import (
    CampaignCountersOut,
    HeroPublicListOut,
    SiteRankingOut,
)
from app.services.campaigns import CampaignService
from app.services.photos import PhotoService
from app.services.pledges import PledgeService
from app.services.stats import StatsService

router = APIRouter(prefix="/public/campaigns", tags=["Public Campaigns"])


@router.get(
    "/current",
    response_model=CampaignPublicOut,
    status_code=status.HTTP_200_OK,
    summary="Get current published campaign for homepage",
)
def get_current_campaign(
    response: Response,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    _user: Annotated[Any, Depends(require(Permission.CAMPAIGN_READ))],
) -> CampaignPublicOut:
    """Retrieve the current published campaign (BR-CAMP-03, 04, 05). 404 if none."""
    response.headers["Cache-Control"] = "public, max-age=300"
    campaign = campaign_service.get_public_current_campaign()
    return CampaignPublicOut.model_validate(campaign)


@router.get(
    "",
    response_model=list[CampaignPublicCardOut],
    status_code=status.HTTP_200_OK,
    summary="List published campaigns (catalog view)",
)
def list_public_campaigns(
    response: Response,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    _user: Annotated[Any, Depends(require(Permission.CAMPAIGN_READ))],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter by status (upcoming, live, ended)"),
    ] = None,
    year: Annotated[int | None, Query(description="Filter by year of campaign_date")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CampaignPublicCardOut]:
    """Retrieve published campaigns ordered newest date first (BR-CAMP-03)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    campaigns = campaign_service.list_public_campaigns(
        status=status_filter,
        year=year,
        limit=limit,
        offset=offset,
    )
    return [CampaignPublicCardOut.model_validate(c) for c in campaigns]


@router.get(
    "/{slug}",
    response_model=CampaignPublicOut,
    status_code=status.HTTP_200_OK,
    summary="Get published campaign by slug",
)
def get_public_campaign(
    slug: str,
    response: Response,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    _user: Annotated[Any, Depends(require(Permission.CAMPAIGN_READ))],
) -> CampaignPublicOut:
    """Retrieve full details of a published campaign by slug (BR-CAMP-03: 404 if unpublished)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    campaign = campaign_service.get_public_campaign(slug)
    return CampaignPublicOut.model_validate(campaign)


@router.get(
    "/{slug}/sites",
    response_model=list[CampaignSitePublicOut],
    status_code=status.HTTP_200_OK,
    summary="List participating sites for a published campaign",
)
def list_public_campaign_sites(
    slug: str,
    response: Response,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    _user: Annotated[Any, Depends(require(Permission.CAMPAIGN_SITE_READ))],
) -> list[CampaignSitePublicOut]:
    """Retrieve all active participating sites and pledge counts for a published campaign (BR-CAMP-03, BR-SITE-02)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    rows = campaign_service.list_public_campaign_sites(slug)
    return [
        CampaignSitePublicOut(
            site_id=site.id,
            name=site.name,
            logo_url=site.logo_key,
            address=site.address,
            map_url=site.map_url,
            start_time=cs.start_time,
            end_time=cs.end_time,
            supervisors_text=cs.supervisors_text,
            description=site.description,
            pledges_count=pledges_count,
        )
        for cs, site, pledges_count in rows
    ]


@router.get(
    "/{slug}/photos",
    response_model=PhotoPublicListOut,
    status_code=status.HTTP_200_OK,
    summary="List photos for a published campaign gallery",
)
def list_public_campaign_photos(
    slug: str,
    response: Response,
    photo_service: Annotated[PhotoService, Depends(get_photo_service)],
    _user: Annotated[Any, Depends(require(Permission.PHOTO_READ))],
    site_id: Annotated[
        uuid.UUID | None,
        Query(description="Optional filter by participating site ID"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Page size limit (max 100 per BR-GEN-05)"),
    ] = 25,
    offset: Annotated[
        int,
        Query(ge=0, description="Page offset"),
    ] = 0,
) -> PhotoPublicListOut:
    """Retrieve photos for a published campaign gallery (BR-CAMP-03: 404 if unpublished; BR-PHOTO-11)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    photos, total = photo_service.list_public_photos(
        slug=slug,
        site_id=site_id,
        limit=limit,
        offset=offset,
    )
    items = [photo_service.to_photo_public_out(p) for p in photos]
    return PhotoPublicListOut(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/{slug}/counters",
    response_model=CampaignCountersOut,
    status_code=status.HTTP_200_OK,
    summary="Get aggregated live or frozen campaign counters",
)
def get_public_campaign_counters(
    slug: str,
    response: Response,
    stats_service: Annotated[StatsService, Depends(get_stats_service)],
    _user: Annotated[Any, Depends(require(Permission.STATS_PUBLIC))],
) -> CampaignCountersOut:
    """Retrieve campaign counters (BR-CAMP-03: 404 if unpublished; BR-COUNT-01..03, 06)."""
    response.headers["Cache-Control"] = "public, max-age=30"
    return stats_service.get_campaign_counters(slug)


@router.get(
    "/{slug}/heroes",
    response_model=HeroPublicListOut,
    status_code=status.HTTP_200_OK,
    summary="List heroes for a published campaign",
)
def list_public_campaign_heroes(
    slug: str,
    response: Response,
    stats_service: Annotated[StatsService, Depends(get_stats_service)],
    _user: Annotated[Any, Depends(require(Permission.STATS_PUBLIC))],
    site_id: Annotated[
        uuid.UUID | None,
        Query(description="Optional filter by participating site ID"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=200, description="Page size limit (hard cap 200 per BR-GEN-05)"),
    ] = 50,
    offset: Annotated[
        int,
        Query(ge=0, description="Page offset"),
    ] = 0,
) -> HeroPublicListOut:
    """Retrieve public heroes roster (BR-CAMP-03: 404 if unpublished; BR-HERO-01..06, BR-COUNT-06)."""
    response.headers["Cache-Control"] = "public, max-age=30"
    items, total = stats_service.list_public_heroes(
        slug=slug,
        site_id=site_id,
        limit=limit,
        offset=offset,
    )
    return HeroPublicListOut(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{slug}/ranking",
    response_model=list[SiteRankingOut],
    status_code=status.HTTP_200_OK,
    summary="Get site ranking for a published campaign",
)
def get_public_campaign_ranking(
    slug: str,
    response: Response,
    stats_service: Annotated[StatsService, Depends(get_stats_service)],
    _user: Annotated[Any, Depends(require(Permission.STATS_PUBLIC))],
) -> list[SiteRankingOut]:
    """Retrieve competition site ranking (BR-CAMP-03: 404 if unpublished; BR-RANK-01..05, BR-COUNT-06)."""
    response.headers["Cache-Control"] = "public, max-age=30"
    return stats_service.get_campaign_ranking(slug)


@router.post(
    "/{slug}/pledges",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Submit an intention to attend a blood drive (pledge)",
    dependencies=[
        Depends(check_pledge_rate_limit),
    ],
)
def create_pledge(
    slug: str,
    data: PledgeCreateIn,
    pledge_service: Annotated[PledgeService, Depends(get_pledge_service)],
    _user: Annotated[Any, Depends(require(Permission.PLEDGE_CREATE))],
) -> Response:
    """Submit a declaration of intent to attend a blood donation drive (BR-PLEDGE-01..11).

    Returns 204 No Content for new records, duplicate phone submissions, and honeypot triggers
    with completely identical status, body, and headers (BR-PLEDGE-06 ★).
    """
    pledge_service.create_pledge(slug, data)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
