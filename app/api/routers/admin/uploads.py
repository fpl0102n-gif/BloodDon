"""Admin media upload API router (P4.7).

Per:
- docs/04-api.md §18 (POST /admin/uploads contract)
- docs/06-business-rules.md §10 (BR-PHOTO-05..08, BR-PHOTO-14)
- docs/03-auth-permissions.md §11.3 (CAMPAIGN_MANAGE permission)
- docs/05-architecture.md §12 (Routers layer rules: no direct repo/model imports)
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.api.deps import get_upload_service, require
from app.core.config import Settings, get_settings
from app.policy import Permission
from app.schemas.upload import UploadOut
from app.services.uploads import UploadService, read_upload_file_safely

router = APIRouter(prefix="/admin/uploads", tags=["Admin Uploads"])


@router.post(
    "",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload cover image or site logo",
)
def upload_admin_image(
    file: Annotated[
        UploadFile,
        File(description="Image file in JPEG, PNG, or WebP format (max 5MB)"),
    ],
    upload_service: Annotated[UploadService, Depends(get_upload_service)],
    user: Annotated[Any, Depends(require(Permission.CAMPAIGN_MANAGE))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UploadOut:
    """Validate, sanitize, strip EXIF, downscale, and store cover or logo image."""
    raw_bytes = read_upload_file_safely(file, max_bytes=settings.max_upload_bytes)
    actor_id = getattr(user, "id", None)
    key, url = upload_service.upload_admin_image(raw_bytes, actor_user_id=actor_id)
    return UploadOut(key=key, url=url)
