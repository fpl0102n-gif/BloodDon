"""Supervisor photo management API router.

Per:
- docs/04-api.md §12 (Supervisor photos contract)
  - POST /supervisor/photos (multipart/form-data) -> 201 Created + PhotoOut
  - GET /supervisor/photos -> 200 OK + PhotoListOut
- docs/06-business-rules.md §8 (BR-PHOTO-01..12)
- docs/07-test-cases.md §4 (Canonical permission matrix row 116 & line 137: admin on supervisor routes -> 403)
- docs/07-test-cases.md §5.7 (T-PHOTO-03..12)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.api.deps import get_photo_service, require
from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError
from app.policy.permissions import Permission
from app.schemas.photo import PhotoListOut, PhotoOut
from app.services.photos import PhotoService
from app.services.uploads import read_upload_file_safely

router = APIRouter(prefix="/supervisor/photos", tags=["Supervisor Photos"])


def _require_supervisor_user(user: Any) -> Any:
    """Enforce that the caller is an active supervisor with an assigned site context.

    Per docs/07 §4 line 137: Administrators calling supervisor routes receive 403 forbidden
    because admins operate globally and have no assigned site context.
    """
    if getattr(user, "role", None) != "supervisor" or getattr(user, "site_id", None) is None:
        raise ForbiddenError("This operation is only available to assigned site supervisors.")
    return user


@router.post(
    "",
    response_model=PhotoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload general atmosphere or individual donor photo",
)
def upload_supervisor_photo(
    file: Annotated[
        UploadFile,
        File(description="Image file in JPEG, PNG, or WebP format (max 5MB)"),
    ],
    kind: Annotated[
        str,
        Form(description="Photo category: 'general' or 'individual'"),
    ],
    photo_service: Annotated[PhotoService, Depends(get_photo_service)],
    user: Annotated[Any, Depends(require(Permission.PHOTO_UPLOAD))],
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
    """Validate, sanitize, strip EXIF, downscale, and store event/donor photo.

    Enforces:
    - BR-PHOTO-01/02: kind in ('general', 'individual'); individual requires donor_id.
    - BR-PHOTO-03 (★): donor belongs to same site and live campaign with consent_photo=true.
    - BR-PHOTO-04: supervisor for their site and live campaign only.
    - BR-PHOTO-05 (★): real image content inspection (JPEG/PNG/WebP). Disguised text -> 422.
    - BR-PHOTO-06: 5MB size limit and decompression bomb protection.
    - BR-PHOTO-07 (★): full re-encoding stripping all EXIF (including GPS), resizing to <= 1600px.
    - BR-PHOTO-08: server-generated UUID storage key, client filename discarded.
    - BR-PHOTO-09: transactional failure cleanup preventing orphaned storage files.
    - BR-PHOTO-12: caption <= 200 chars, rejects '<' or '>'.
    """
    _require_supervisor_user(user)

    raw_bytes = read_upload_file_safely(file, max_bytes=settings.max_upload_bytes)
    photo = photo_service.upload_supervisor_photo(
        user=user,
        raw_bytes=raw_bytes,
        kind=kind,
        donor_id=donor_id,
        caption=caption,
    )
    return photo_service.to_photo_out(photo)


@router.get(
    "",
    response_model=PhotoListOut,
    status_code=status.HTTP_200_OK,
    summary="List photos for supervisor's site in the active campaign",
)
def list_supervisor_photos(
    photo_service: Annotated[PhotoService, Depends(get_photo_service)],
    user: Annotated[Any, Depends(require(Permission.PHOTO_UPLOAD))],
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Page limit (max 100)"),
    ] = 25,
    offset: Annotated[
        int,
        Query(ge=0, description="Page offset"),
    ] = 0,
) -> PhotoListOut:
    """Retrieve photos uploaded for the supervisor's assigned site (newest first)."""
    _require_supervisor_user(user)

    photos, total = photo_service.list_supervisor_photos(
        user=user,
        limit=limit,
        offset=offset,
    )
    items = [photo_service.to_photo_out(p) for p in photos]
    return PhotoListOut(items=items, total=total, limit=limit, offset=offset)


@router.delete(
    "/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete photo at supervisor site and stage storage key for deletion",
)
def delete_supervisor_photo(
    photo_id: uuid.UUID,
    photo_service: Annotated[PhotoService, Depends(get_photo_service)],
    user: Annotated[Any, Depends(require(Permission.PHOTO_DELETE))],
) -> None:
    """Delete photo at supervisor's assigned site during active live campaign (docs/04-api.md §12.3, BR-PHOTO-10)."""
    _require_supervisor_user(user)
    photo_service.delete_supervisor_photo(user=user, photo_id=photo_id)
