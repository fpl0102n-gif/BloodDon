"""Admin Sites API router (P4.1).

Per:
- docs/04-api.md §14 (Admin Sites: GET, POST, PATCH, DELETE)
- docs/06-business-rules.md §4 (BR-SITE-01..04)
- docs/03-auth-permissions.md §11.3 (Permission.SITE_MANAGE)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.api.deps import get_site_service, require
from app.policy import Permission
from app.schemas.site import SiteCreate, SiteOut, SiteUpdate
from app.services.sites import SiteService

router = APIRouter(prefix="/admin/sites", tags=["Admin Sites"])


@router.get(
    "",
    response_model=list[SiteOut],
    status_code=status.HTTP_200_OK,
    summary="List all sites (including inactive)",
)
def list_sites(
    site_service: Annotated[SiteService, Depends(get_site_service)],
    _user: Annotated[Any, Depends(require(Permission.SITE_MANAGE))],
) -> list[SiteOut]:
    """Retrieve all physical donation sites / medical clubs across the platform."""
    sites = site_service.list_sites(include_inactive=True)
    return [SiteOut.model_validate(s) for s in sites]


@router.post(
    "",
    response_model=SiteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new donation site",
)
def create_site(
    payload: SiteCreate,
    site_service: Annotated[SiteService, Depends(get_site_service)],
    current_user: Annotated[Any, Depends(require(Permission.SITE_MANAGE))],
) -> SiteOut:
    """Create a new donation site."""
    created = site_service.create_site(payload, actor_user_id=current_user.id)
    return SiteOut.model_validate(created)


@router.get(
    "/{site_id}",
    response_model=SiteOut,
    status_code=status.HTTP_200_OK,
    summary="Get site details",
)
def get_site(
    site_id: uuid.UUID,
    site_service: Annotated[SiteService, Depends(get_site_service)],
    _user: Annotated[Any, Depends(require(Permission.SITE_MANAGE))],
) -> SiteOut:
    """Get full details of a specific site."""
    site = site_service.get_site(site_id)
    return SiteOut.model_validate(site)


@router.patch(
    "/{site_id}",
    response_model=SiteOut,
    status_code=status.HTTP_200_OK,
    summary="Update or deactivate site",
)
def update_site(
    site_id: uuid.UUID,
    payload: SiteUpdate,
    site_service: Annotated[SiteService, Depends(get_site_service)],
    current_user: Annotated[Any, Depends(require(Permission.SITE_MANAGE))],
) -> SiteOut:
    """Update site information or toggle active status per BR-SITE-02."""
    updated = site_service.update_site(site_id, payload, actor_user_id=current_user.id)
    return SiteOut.model_validate(updated)


@router.delete(
    "/{site_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Conditionally delete unused site (requires step-up R)",
)
def delete_site(
    site_id: uuid.UUID,
    site_service: Annotated[SiteService, Depends(get_site_service)],
    current_user: Annotated[
        Any,
        Depends(require(Permission.SITE_MANAGE, require_reauth=True)),
    ],
) -> None:
    """Hard delete an unused site per BR-SITE-03 (rejects with 409 in_use if ever used)."""
    site_service.delete_site(site_id, actor_user_id=current_user.id)
