"""Admin Campaigns API router (P4.2).

Per:
- docs/04-api.md §13 (Admin Campaigns: GET, POST, PATCH, DELETE)
- docs/06-business-rules.md §3 (BR-CAMP-01, 02, 12, 13, 14, 15)
- docs/03-auth-permissions.md §11.3 (Permission.CAMPAIGN_MANAGE, require_reauth for DELETE)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_campaign_service, require
from app.policy import Permission
from app.schemas.campaign import (
    CampaignCreate,
    CampaignFinalStatsOut,
    CampaignOut,
    CampaignSummaryOut,
    CampaignUpdate,
)
from app.schemas.campaign_site import CampaignSiteOut, CampaignSitePut
from app.services.campaigns import CampaignService

router = APIRouter(prefix="/admin/campaigns", tags=["Admin Campaigns"])


@router.get(
    "",
    response_model=list[CampaignSummaryOut],
    status_code=status.HTTP_200_OK,
    summary="List all campaigns (admin overview)",
)
def list_campaigns(
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    _user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter by status (upcoming, live, ended)"),
    ] = None,
    is_published: Annotated[
        bool | None,
        Query(description="Filter by publication flag"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CampaignSummaryOut]:
    """Retrieve all campaigns across any status for administrative management."""
    campaigns = campaign_service.list_campaigns(
        status=status_filter,
        is_published=is_published,
        limit=limit,
        offset=offset,
    )
    return [CampaignSummaryOut.model_validate(c) for c in campaigns]


@router.post(
    "",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new campaign",
)
def create_campaign(
    payload: CampaignCreate,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
) -> CampaignOut:
    """Create a new campaign initialized to upcoming, unpublished, and not current (BR-CAMP-01)."""
    created = campaign_service.create_campaign(payload, actor_user_id=current_user.id)
    return CampaignOut.model_validate(created)


@router.get(
    "/{campaign_id}",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Get campaign details",
)
def get_campaign(
    campaign_id: uuid.UUID,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    _user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
) -> CampaignOut:
    """Get complete details of a specific campaign."""
    campaign = campaign_service.get_campaign(campaign_id)
    return CampaignOut.model_validate(campaign)


@router.patch(
    "/{campaign_id}",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Update campaign attributes",
)
def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
) -> CampaignOut:
    """Update campaign metadata, publication status, or content (BR-CAMP-12, 14, 15)."""
    updated = campaign_service.update_campaign(
        campaign_id,
        payload,
        actor_user_id=current_user.id,
    )
    return CampaignOut.model_validate(updated)


@router.post(
    "/{campaign_id}/set-current",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Set campaign as current (BR-CAMP-04, 05)",
)
def set_current_campaign(
    campaign_id: uuid.UUID,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
) -> CampaignOut:
    """Set the campaign as the active current campaign (requires is_published=True)."""
    updated = campaign_service.set_current_campaign(campaign_id, actor_user_id=current_user.id)
    return CampaignOut.model_validate(updated)


@router.post(
    "/{campaign_id}/open",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Open campaign: upcoming -> live (BR-CAMP-06, 07)",
)
def open_campaign(
    campaign_id: uuid.UUID,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
) -> CampaignOut:
    """Transition campaign from upcoming to live and set as current (BR-CAMP-06)."""
    opened = campaign_service.open_campaign(campaign_id, actor_user_id=current_user.id)
    return CampaignOut.model_validate(opened)


@router.post(
    "/{campaign_id}/revert",
    response_model=CampaignOut,
    status_code=status.HTTP_200_OK,
    summary="Revert campaign: live -> upcoming (BR-CAMP-08, requires step-up R)",
)
def revert_campaign(
    campaign_id: uuid.UUID,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[
        Any,
        Depends(require(Permission.CAMPAIGN_MANAGE, require_reauth=True)),
    ],
) -> CampaignOut:
    """Revert campaign from live to upcoming (only allowed if no donors recorded, BR-CAMP-08)."""
    reverted = campaign_service.revert_campaign(campaign_id, actor_user_id=current_user.id)
    return CampaignOut.model_validate(reverted)


@router.post(
    "/{campaign_id}/end",
    response_model=CampaignFinalStatsOut,
    status_code=status.HTTP_200_OK,
    summary="End campaign and freeze final statistics (BR-CAMP-09, requires step-up R)",
)
def end_campaign(
    campaign_id: uuid.UUID,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[
        Any,
        Depends(require(Permission.CAMPAIGN_END, require_reauth=True)),
    ],
) -> CampaignFinalStatsOut:
    """Transition campaign from live to ended, compute, and freeze final_stats in one atomic transaction."""
    ended = campaign_service.end_campaign(campaign_id, actor_user_id=current_user.id)
    return CampaignFinalStatsOut.model_validate(ended.final_stats)


@router.delete(
    "/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Conditionally delete unused campaign (requires step-up R)",
)
def delete_campaign(
    campaign_id: uuid.UUID,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[
        Any,
        Depends(require(Permission.CAMPAIGN_MANAGE, require_reauth=True)),
    ],
) -> None:
    """Hard delete an unused campaign (rejects with 409 in_use if pledges/donors/photos exist)."""
    campaign_service.delete_campaign(campaign_id, actor_user_id=current_user.id)


# --------------------------------------------------------------------------
# CampaignSite Participation Endpoints (docs/04 §13.1, BR-CSITE-01..06)
# --------------------------------------------------------------------------


@router.get(
    "/{campaign_id}/sites",
    response_model=list[CampaignSiteOut],
    status_code=status.HTTP_200_OK,
    summary="List campaign participating sites",
)
def list_campaign_sites(
    campaign_id: uuid.UUID,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    _user: Annotated[Any, Depends(require(Permission.CAMPAIGN_SITE_MANAGE))],
) -> list[CampaignSiteOut]:
    """List all sites participating in this campaign (BR-CSITE-01)."""
    participations = campaign_service.list_campaign_sites(campaign_id)
    return [
        CampaignSiteOut(
            campaign_id=cs.campaign_id,
            site_id=cs.site_id,
            site_name=cs.site.name,
            site_address=cs.site.address,
            start_time=cs.start_time,
            end_time=cs.end_time,
            supervisors_text=cs.supervisors_text,
            notes=cs.notes,
        )
        for cs in participations
    ]


@router.put(
    "/{campaign_id}/sites/{site_id}",
    response_model=CampaignSiteOut,
    status_code=status.HTTP_200_OK,
    summary="Add or update site participation in campaign",
)
def add_or_update_campaign_site(
    campaign_id: uuid.UUID,
    site_id: uuid.UUID,
    payload: CampaignSitePut,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[Any, Depends(require(Permission.CAMPAIGN_SITE_MANAGE))],
) -> CampaignSiteOut:
    """Add or update site participation in campaign (BR-CSITE-02, 03, 05)."""
    saved = campaign_service.add_or_update_campaign_site(
        campaign_id,
        site_id,
        payload,
        actor_user_id=current_user.id,
    )
    return CampaignSiteOut(
        campaign_id=saved.campaign_id,
        site_id=saved.site_id,
        site_name=saved.site.name,
        site_address=saved.site.address,
        start_time=saved.start_time,
        end_time=saved.end_time,
        supervisors_text=saved.supervisors_text,
        notes=saved.notes,
    )


@router.delete(
    "/{campaign_id}/sites/{site_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove site participation from campaign",
)
def delete_campaign_site(
    campaign_id: uuid.UUID,
    site_id: uuid.UUID,
    campaign_service: Annotated[CampaignService, Depends(get_campaign_service)],
    current_user: Annotated[Any, Depends(require(Permission.CAMPAIGN_SITE_MANAGE))],
) -> None:
    """Remove site participation from campaign (BR-CSITE-04: rejects with 409 in_use if donors/photos exist)."""
    campaign_service.remove_campaign_site(
        campaign_id,
        site_id,
        actor_user_id=current_user.id,
    )
