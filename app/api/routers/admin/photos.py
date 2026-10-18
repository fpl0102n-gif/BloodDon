"""Admin photos API router.

Per:
- docs/04-api.md §18 (Admin photos contract: GET /admin/campaigns/{id}/photos, DELETE /admin/photos/{id})
- docs/06-business-rules.md §8 (BR-PHOTO-04: Admin can upload for any participating site in a campaign)
- docs/07-test-cases.md §5.7 (T-PHOTO-04-b: Admin uploads to participating site B -> 201)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.api.deps import get_photo_service, require
from app.core.config import Settings, get_settings
from app.policy.permissions import Permission
from app.schemas.photo import PhotoListOut, PhotoOut
from app.services.photos import PhotoService
from app.services.uploads import read_upload_file_safely

router = APIRouter(tags=["Admin Photos"])


@router.post(
    "/admin/campaigns/{campaign_id}/photos",
    response_model=PhotoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload photo for a participating campaign site as administrator",
)
def upload_admin_campaign_photo(
    campaign_id: uuid.UUID,
    file: Annotated[
        UploadFile,
        File(description="Image file in JPEG, PNG, or WebP format (max 5MB)"),
    ],
    site_id: Annotated[
        uuid.UUID,
        Form(description="Participating site ID"),
    ],
    kind: Annotated[
        str,
        Form(description="Photo category: 'general' or 'individual'"),
    ],
    photo_service: Annotated[PhotoService, Depends(get_photo_service)],
    user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
    settings: Annotated[Settings, Depends(get_settings)],
    donor_id: Annotated[
        uuid.UUID | None,
        Form(description="Associated donor ID for individual photos"),
    ] = None,
    caption: Annotated[
        str | None,
        Form(description="Optional photo caption (max 200 chars, no HTML)"),
    ] = None,
) -> PhotoOut:
    """Upload and process photo for any participating site in a campaign (BR-PHOTO-04, T-PHOTO-04-b)."""
    raw_bytes = read_upload_file_safely(file, max_bytes=settings.max_upload_bytes)
    photo = photo_service.upload_admin_photo(
        user=user,
        raw_bytes=raw_bytes,
        campaign_id=campaign_id,
        site_id=site_id,
        kind=kind,
        donor_id=donor_id,
        caption=caption,
    )
    return photo_service.to_photo_out(photo)


@router.get(
    "/admin/campaigns/{campaign_id}/photos",
    response_model=PhotoListOut,
    status_code=status.HTTP_200_OK,
    summary="List all photos for a campaign with optional site filtering",
)
def list_admin_campaign_photos(
    campaign_id: uuid.UUID,
    photo_service: Annotated[PhotoService, Depends(get_photo_service)],
    user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
    site_id: Annotated[
        uuid.UUID | None,
        Query(description="Optional site ID filter"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Page limit (max 100)"),
    ] = 25,
    offset: Annotated[
        int,
        Query(ge=0, description="Page offset"),
    ] = 0,
) -> PhotoListOut:
    """List photos for a campaign as administrator per docs/04-api.md §18."""
    del user  # Authenticated by require(Permission.CAMPAIGN_MANAGE)
    photos, total = photo_service.photo_repo.list_by_campaign(
        campaign_id=campaign_id,
        site_id=site_id,
        limit=limit,
        offset=offset,
    )
    items = [photo_service.to_photo_out(p) for p in photos]
    return PhotoListOut(items=items, total=total, limit=limit, offset=offset)


@router.delete(
    "/admin/photos/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete any photo and stage storage key for background deletion",
)
def delete_admin_photo(
    photo_id: uuid.UUID,
    photo_service: Annotated[PhotoService, Depends(get_photo_service)],
    user: Annotated[Any, Depends(require(Permission.PHOTO_DELETE))],
) -> None:
    """Delete photo entity and enqueue its storage key as administrator (docs/04-api.md §18, BR-PHOTO-10)."""
    photo_service.delete_admin_photo(user=user, photo_id=photo_id)
