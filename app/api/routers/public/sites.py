"""Public Sites API router.

Per:
- docs/04-api.md §6.9 (GET /public/sites)
- docs/06-business-rules.md §4 (BR-SITE-02: deactivation hides site from public view)
- docs/07-test-cases.md §5.3 (T-SITE-02-a)
- docs/03-auth-permissions.md §11.1 (Permission.SITE_READ)
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_site_service, require
from app.policy import Permission
from app.schemas.site import SitePublicOut
from app.services.sites import SiteService

router = APIRouter(prefix="/public/sites", tags=["Public Sites"])


@router.get(
    "",
    response_model=list[SitePublicOut],
    status_code=status.HTTP_200_OK,
    summary="List active donation sites (for public discovery)",
)
def list_public_sites(
    response: Response,
    site_service: Annotated[SiteService, Depends(get_site_service)],
    _user: Annotated[Any, Depends(require(Permission.SITE_READ))],
) -> list[SitePublicOut]:
    """Retrieve all active physical donation sites (excludes deactivated sites per BR-SITE-02)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    active_sites = site_service.list_sites(include_inactive=False)
    return [SitePublicOut.model_validate(s) for s in active_sites]
