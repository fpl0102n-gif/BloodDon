"""Integration tests for Admin Image Upload API endpoint (P4.7).

Per:
- docs/04-api.md §18 (POST /admin/uploads contract)
- docs/06-business-rules.md §10:
  - BR-PHOTO-05 (★): Verified image content (JPEG, PNG, WebP only).
  - BR-PHOTO-06: 5MB size limit, decompression bomb protection.
  - BR-PHOTO-07 (★): Re-encoded, EXIF completely stripped, resized longest side <= 1600px.
  - BR-PHOTO-08: Server-generated UUID key, no client filename used.
  - BR-PHOTO-09: Failure removes file immediately (no orphans).
  - BR-PHOTO-14: Admin covers/logos processed identically, not stored in photos table.
- docs/07-test-cases.md §5.7:
  - T-PHOTO-05-a (★): Content validation rejects disguised text files.
  - T-PHOTO-05-b (★): Unsupported formats (SVG/GIF/PDF) return 415.
  - T-PHOTO-06-a: 5MB size limit enforced with 413.
  - T-PHOTO-07-a (★): EXIF stripped.
  - T-PHOTO-08-a: Server generated UUID key.
  - T-PHOTO-09-a: Clean up on failure.
  - T-PHOTO-14-a: No entry in photos table.
"""

import io
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_settings,
)
from app.models.audit import AuditLog
from app.models.photo import Photo
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from app.services.uploads import UploadService
from app.storage import FakeStorage
from tests.factories.campaign import create_site
from tests.factories.user import create_user

# ==============================================================================
# Fixtures and Helpers
# ==============================================================================


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user."""
    return create_user(
        db_session,
        email="admin.upload@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create a supervisor user assigned to a test site."""
    site = create_site(db_session, name="موقع مشرف الرفع")
    return create_user(
        db_session,
        email="supervisor.upload@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


def create_auth_headers_and_cookies(
    test_client: TestClient,
    db_session: Session,
    user: User,
    *,
    mfa_verified: bool = True,
) -> dict[str, str]:
    """Helper to attach session cookie and CSRF token for a given user."""
    settings = get_settings()
    clock = get_clock()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    raw_token, session = session_service.create_session(
        user.id,
        user.role,
        mfa_verified=mfa_verified,
    )

    clear_session_reauth(session.id)
    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    test_client.cookies.set(settings.cookie_name, raw_token)

    return {"X-CSRF-Token": csrf_token}


def make_test_jpeg(size: tuple[int, int] = (100, 100)) -> bytes:
    """Create a simple valid JPEG in-memory."""
    img = Image.new("RGB", size, (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ==============================================================================
# 1. Authorization & Role Matrix
# ==============================================================================


@pytest.mark.api
def test_admin_uploads_requires_authentication(test_client: TestClient) -> None:
    """Anonymous visitors receive 401 on /admin/uploads."""
    resp = test_client.post("/api/v1/admin/uploads")
    assert resp.status_code == 401


@pytest.mark.api
def test_admin_uploads_forbidden_for_supervisor(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """Supervisors lack CAMPAIGN_MANAGE and receive 403 forbidden."""
    headers = create_auth_headers_and_cookies(
        test_client, db_session, supervisor_user, mfa_verified=True
    )
    jpeg_bytes = make_test_jpeg()

    resp = test_client.post(
        "/api/v1/admin/uploads",
        files={"file": ("photo.jpg", jpeg_bytes, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.api
def test_admin_uploads_requires_mfa(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Admin without MFA verified receives 403 mfa_required."""
    headers = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=False
    )
    jpeg_bytes = make_test_jpeg()

    resp = test_client.post(
        "/api/v1/admin/uploads",
        files={"file": ("photo.jpg", jpeg_bytes, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "mfa_required"


# ==============================================================================
# 2. Successful Upload & Business Rules
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-05")
@pytest.mark.br("BR-PHOTO-08")
@pytest.mark.br("BR-PHOTO-14")
def test_admin_upload_success_jpeg(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_storage: FakeStorage,
) -> None:
    """Valid JPEG upload succeeds with 201, generates UUID key under admin/, and stores file."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    jpeg_bytes = make_test_jpeg((200, 150))

    resp = test_client.post(
        "/api/v1/admin/uploads",
        files={"file": ("my_cool_banner.jpg", jpeg_bytes, "image/jpeg")},
        headers=headers,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert "key" in body
    assert "url" in body

    key = body["key"]
    url = body["url"]

    # BR-PHOTO-08 & BR-PHOTO-14: Server-generated UUID under admin/ prefix; client name discarded
    assert key.startswith("admin/")
    assert key.endswith(".jpg")
    assert "my_cool_banner" not in key
    assert url == f"/media/{key}"

    # Verify stored in fake_storage
    assert fake_storage.exists(key) is True
    assert fake_storage.get(key) is not None

    # T-PHOTO-14-a (★): Verify NO record was inserted into photos table
    photos_count = db_session.scalar(select(Photo))
    assert photos_count is None, "Admin cover/logo must not be saved into photos table"

    # Verify audit event photo.upload
    audit_event = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "photo.upload")
    ).first()
    assert audit_event is not None
    assert audit_event.entity_type == "upload"
    assert audit_event.actor_user_id == admin_user.id
    assert audit_event.details.get("key") == key


@pytest.mark.api
def test_admin_upload_root_prefix_compatibility(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Endpoint works identically on /admin/uploads (without /api/v1 prefix)."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    jpeg_bytes = make_test_jpeg((100, 100))

    resp = test_client.post(
        "/admin/uploads",
        files={"file": ("logo.jpg", jpeg_bytes, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["key"].startswith("admin/")


@pytest.mark.api
@pytest.mark.br("BR-PHOTO-08")
def test_admin_upload_ignores_path_traversal_filename(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-08-a: Malicious client filename with traversal is completely ignored."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    jpeg_bytes = make_test_jpeg()

    resp = test_client.post(
        "/api/v1/admin/uploads",
        files={"file": ("../../../../etc/passwd.jpg", jpeg_bytes, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 201
    key = resp.json()["key"]
    assert key.startswith("admin/")
    assert ".." not in key
    assert "passwd" not in key


# ==============================================================================
# 3. Error Cases & Validations
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-PHOTO-05")
def test_admin_upload_rejects_fake_image_file(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-PHOTO-05-a: Non-image file disguised as image returns 422 invalid_image."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    fake_text = b"Hello, I am pretending to be a JPG file!"

    resp = test_client.post(
        "/api/v1/admin/uploads",
        files={"file": ("shady.jpg", fake_text, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_image"


@pytest.mark.api
@pytest.mark.br("BR-PHOTO-05")
def test_admin_upload_rejects_unsupported_format_svg(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-PHOTO-05-b: SVG upload returns 415 unsupported_media_type."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'

    resp = test_client.post(
        "/api/v1/admin/uploads",
        files={"file": ("vector.svg", svg_bytes, "image/svg+xml")},
        headers=headers,
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "unsupported_media_type"


@pytest.mark.api
@pytest.mark.br("BR-PHOTO-06")
def test_admin_upload_rejects_file_too_large(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-PHOTO-06-a: File exceeding 5MB limit returns 413 file_too_large."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    oversized_bytes = b"A" * (5242880 + 1024)  # > 5MB

    resp = test_client.post(
        "/api/v1/admin/uploads",
        files={"file": ("huge.jpg", oversized_bytes, "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "file_too_large"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-09")
def test_admin_upload_cleans_up_on_downstream_failure(
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-09-a: If downstream audit logging or transaction fails, the uploaded file is purged."""
    mock_audit = MagicMock()
    mock_audit.log_event.side_effect = RuntimeError("Simulated audit write failure")

    upload_service = UploadService(storage=fake_storage, audit_service=mock_audit)
    jpeg_bytes = make_test_jpeg()

    with pytest.raises(RuntimeError, match="Simulated audit write failure"):
        upload_service.upload_admin_image(jpeg_bytes, actor_user_id=admin_user)

    # Verify no file remains orphaned in storage (BR-PHOTO-09)
    assert len(fake_storage._files) == 0, "Orphaned files detected after downstream failure"
