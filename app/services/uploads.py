"""Media upload domain service.

Per:
- docs/05-architecture.md §13 (Storage and image processing)
- docs/06-business-rules.md §10:
  - BR-PHOTO-05 (★): Verified image content (JPEG, PNG, WebP).
  - BR-PHOTO-06: 5MB size limit.
  - BR-PHOTO-07 (★): Re-encoded, EXIF completely stripped, resized longest side <= 1600px.
  - BR-PHOTO-08: Server-generated UUID key, no client filename used.
  - BR-PHOTO-09: Failure removes file immediately (no orphans).
  - BR-PHOTO-14: Admin covers/logos stored under admin/ prefix, not in photos table.
- docs/04-api.md §18 (POST /admin/uploads -> { key, url }).
"""

import uuid
from typing import Any

from app.core.config import Settings, get_settings
from app.core.errors import FileTooLargeError
from app.services.audit import AuditService
from app.storage.base import Storage, get_storage
from app.storage.images import process_uploaded_image


def read_upload_file_safely(file: Any, max_bytes: int = 5242880) -> bytes:
    """Read binary content from uploaded file in chunks, enforcing strict size limits early.

    Raises:
        FileTooLargeError: If accumulated stream size exceeds max_bytes.
    """
    buffer = bytearray()
    chunk_size = 64 * 1024  # 64 KB chunks
    file_obj = getattr(file, "file", file)

    while True:
        chunk = file_obj.read(chunk_size)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise FileTooLargeError(
                f"File size exceeds maximum permitted limit of {max_bytes} bytes."
            )

    return bytes(buffer)


class UploadService:
    """Domain service managing media file processing and storage."""

    def __init__(
        self,
        storage: Storage | None = None,
        audit_service: AuditService | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.storage = storage or get_storage()
        self.audit_service = audit_service
        self.settings = settings or get_settings()

    def upload_admin_image(
        self,
        raw_bytes: bytes,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> tuple[str, str]:
        """Validate, process, and persist an administrative image upload.

        Conforms to:
        - BR-PHOTO-05: Validates real content (JPEG, PNG, WebP only).
        - BR-PHOTO-06: Enforces size limits and decompression bomb protection.
        - BR-PHOTO-07: Strips all EXIF metadata and scales down to <= 1600px.
        - BR-PHOTO-08 & BR-PHOTO-14: Server-generated UUID key stored under admin/ prefix.
        - BR-PHOTO-09: Deletes file from storage if downstream operations fail.
        - BR-PHOTO-14: Does NOT create an entry in the photos table.

        Returns:
            tuple[str, str]: (storage_key, public_url)
        """
        # 1. Run through shared image validation, sanitization, and downscaling pipeline
        processed = process_uploaded_image(raw_bytes, settings=self.settings)

        # 2. Generate server-side UUID key under admin prefix (BR-PHOTO-08, BR-PHOTO-14)
        file_key = f"admin/{uuid.uuid4()}{processed.extension}"

        # 3. Save processed bytes to storage provider
        self.storage.save(file_key, processed.content, content_type=processed.content_type)

        # 4. Record audit event photo.upload without PII (BR-AUDIT-01/05, BR-PHOTO-09)
        try:
            if self.audit_service and actor_user_id:
                self.audit_service.log_event(
                    action="photo.upload",
                    entity_type="upload",
                    actor_user_id=actor_user_id,
                    details={"key": file_key, "format": processed.format},
                )
        except Exception:
            # If audit logging or transaction fails, clean up storage file (BR-PHOTO-09)
            self.storage.delete(file_key)
            raise

        public_url = self.storage.get_url(file_key)
        return file_key, public_url
