"""API integration tests for supervisor and admin photo endpoints (P5.6).

Per:
- docs/04-api.md §12 (POST & GET /supervisor/photos)
- docs/06-business-rules.md §8 (BR-PHOTO-01..12)
- docs/07-test-cases.md §4 (Canonical permission matrix row 116 & line 137: admin on supervisor routes -> 403)
- docs/07-test-cases.md §5.7:
  - T-PHOTO-03-a (★): photo consent missing -> 422 photo_consent_missing.
  - T-PHOTO-03-b (★): individual photo with consent -> 201 Created.
  - T-PHOTO-03-c (★): foreign-site donor -> 404 not_found.
  - T-PHOTO-04-a: general atmosphere photo -> 201 Created.
  - T-PHOTO-04-b: admin photo upload for participating site B -> 201 Created.
  - T-PHOTO-04-c: upload after campaign end -> 409 no_live_campaign.
  - T-PHOTO-05-a (★): disguised text file -> 422 invalid_image.
  - T-PHOTO-05-b (★): GIF/SVG/PDF return 415; JPEG/PNG/WebP return 201.
  - T-PHOTO-06-a: file > 5MB -> 413 file_too_large.
  - T-PHOTO-06-b: decompression bomb -> 422 invalid_image.
  - T-PHOTO-07-a (★): complete EXIF & GPS stripping verified.
  - T-PHOTO-07-b: image resizing down to <= 1600px.
  - T-PHOTO-08-a: server-generated UUID storage key, client filename discarded.
  - T-PHOTO-12-a: caption > 200 chars -> 422 validation_error.
  - T-PHOTO-12-b: caption with '<' or '>' -> 422 validation_error.
"""

import io
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_db,
    get_session_service,
    get_settings,
    get_storage_dep,
    record_session_reauth,
)
from app.core.clock import FakeClock
from app.main import create_app
from app.models.audit import AuditLog
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from app.storage.base import FakeStorage


def make_test_image(
    fmt: str = "JPEG",
    size: tuple[int, int] = (100, 100),
    color: tuple[int, int, int] = (200, 50, 50),
    exif_data: dict[int, Any] | None = None,
) -> bytes:
    """Helper creating simple test image bytes with optional EXIF."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if exif_data and fmt.upper() == "JPEG":
        exif = img.getexif()
        for tag, val in exif_data.items():
            exif[tag] = val
        img.save(buf, format=fmt, exif=exif)
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


# ==============================================================================
# Test Fixtures
# ==============================================================================


@pytest.fixture
def fake_storage() -> FakeStorage:
    """In-memory FakeStorage instance."""
    return FakeStorage(public_base_url="/media")


@pytest.fixture
def test_clock() -> FakeClock:
    """FakeClock initialized to stable UTC reference."""
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC))


@pytest.fixture
def photo_client(
    db_session: Session,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> TestClient:
    """FastAPI TestClient wired to isolated transactional DB session, clock, and storage."""
    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def override_get_clock() -> FakeClock:
        return test_clock

    def override_get_storage() -> FakeStorage:
        return fake_storage

    def override_get_session_service() -> SessionService:
        repo = SessionRepository(db_session)
        return SessionService(repo, clock=test_clock, settings=get_settings())

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_clock] = override_get_clock
    app.dependency_overrides[get_storage_dep] = override_get_storage
    app.dependency_overrides[get_session_service] = override_get_session_service

    return TestClient(app)


@pytest.fixture
def live_campaign(db_session: Session, test_clock: FakeClock) -> Campaign:
    """Current live campaign fixture."""
    campaign = Campaign(
        id=uuid.uuid4(),
        slug="live-photo-camp",
        title="حملة الصور الميدانية",
        status="live",
        is_published=True,
        is_current=True,
        campaign_date=test_clock.now().date(),
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


@pytest.fixture
def site_a(db_session: Session, live_campaign: Campaign) -> Site:
    """Participating site A."""
    site = Site(
        id=uuid.uuid4(),
        name="موقع أ - كلية الطب",
        address="شارع الجامعة",
        is_active=True,
    )
    db_session.add(site)
    db_session.flush()

    cs = CampaignSite(
        campaign_id=live_campaign.id,
        site_id=site.id,
    )
    db_session.add(cs)
    db_session.flush()
    return site


@pytest.fixture
def site_b(db_session: Session, live_campaign: Campaign) -> Site:
    """Participating site B."""
    site = Site(
        id=uuid.uuid4(),
        name="موقع ب - المستشفى الجامعي",
        address="شارع المستشفى",
        is_active=True,
    )
    db_session.add(site)
    db_session.flush()

    cs = CampaignSite(
        campaign_id=live_campaign.id,
        site_id=site.id,
    )
    db_session.add(cs)
    db_session.flush()
    return site


@pytest.fixture
def sup_a(db_session: Session, site_a: Site) -> User:
    """Active supervisor at site A."""
    user = User(
        id=uuid.uuid4(),
        email="sup_a@blood.local",
        full_name="مشرف موقع أ",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
        password_hash="argon2id$mocked",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def sup_b(db_session: Session, site_b: Site) -> User:
    """Active supervisor at site B."""
    user = User(
        id=uuid.uuid4(),
        email="sup_b@blood.local",
        full_name="مشرف موقع ب",
        role="supervisor",
        site_id=site_b.id,
        is_active=True,
        password_hash="argon2id$mocked",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Active administrator with MFA verified."""
    user = User(
        id=uuid.uuid4(),
        email="admin@blood.local",
        full_name="الأدمن العام",
        role="admin",
        site_id=None,
        is_active=True,
        password_hash="argon2id$mocked",
        totp_enabled=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def donor_da1(db_session: Session, live_campaign: Campaign, site_a: Site) -> Donor:
    """Donor dA1 at site A with consent_photo=False."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="عمر",
        last_name="خالد",
        status="attended",
        consent_keep_phone=False,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()
    return donor


@pytest.fixture
def donor_da2(db_session: Session, live_campaign: Campaign, site_a: Site) -> Donor:
    """Donor dA2 at site A with consent_photo=True."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="سامي",
        last_name="علي",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()
    return donor


@pytest.fixture
def donor_db1(db_session: Session, live_campaign: Campaign, site_b: Site) -> Donor:
    """Donor dB1 at site B with consent_photo=True."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_b.id,
        first_name="بلال",
        last_name="حسن",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()
    return donor


def auth_headers(
    client: TestClient,
    db_session: Session,
    user: User,
    test_clock: FakeClock,
    *,
    mfa_verified: bool = True,
    fresh_reauth: bool = False,
) -> dict[str, str]:
    """Helper setting session cookie on test client and returning CSRF header."""
    settings = get_settings()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=test_clock, settings=settings)

    raw_token, session = session_service.create_session(
        user.id,
        user.role,
        mfa_verified=mfa_verified,
    )

    if fresh_reauth:
        record_session_reauth(session.id, test_clock.now())
    else:
        clear_session_reauth(session.id)

    db_session.flush()

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    client.cookies.set(settings.cookie_name, raw_token)
    return {"X-CSRF-Token": csrf_token}


# ==============================================================================
# API Test Cases
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-03")
def test_t_photo_03_a_photo_consent_missing(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    donor_da1: Donor,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-03-a (★): Uploading individual photo for donor without photo consent returns 422 photo_consent_missing."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "individual", "donor_id": str(donor_da1.id)},
        files={"file": ("donor.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "photo_consent_missing"
    # Verify no record created and no file stored
    assert len(fake_storage._files) == 0
    stmt = select(Photo).where(Photo.donor_id == donor_da1.id)
    assert db_session.scalar(stmt) is None


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-03")
def test_t_photo_03_b_individual_photo_with_consent_succeeds(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    donor_da2: Donor,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-03-b (★): Uploading individual photo for donor with consent_photo=true returns 201 Created."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={
            "kind": "individual",
            "donor_id": str(donor_da2.id),
            "caption": "متبرع متطوع",
        },
        files={"file": ("donor.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["kind"] == "individual"
    assert data["donor_id"] == str(donor_da2.id)
    assert data["caption"] == "متبرع متطوع"
    assert data["url"].startswith("/media/photos/")

    # Verify database persistence
    photo_id = uuid.UUID(data["id"])
    photo = db_session.get(Photo, photo_id)
    assert photo is not None
    assert photo.donor_id == donor_da2.id
    assert fake_storage.exists(photo.storage_key)

    # Verify audit event photo.upload without PII
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "photo.upload",
            AuditLog.entity_id == photo_id,
        )
    )
    assert audit is not None
    assert audit.actor_user_id == sup_a.id
    assert "first_name" not in audit.details
    assert "phone" not in audit.details


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-03")
@pytest.mark.br("BR-DONOR-09")
def test_t_photo_03_c_foreign_site_donor_returns_not_found(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    donor_db1: Donor,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-03-c (★): sup_a uploading individual photo for dB1 returns 404 not_found, never 403."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "individual", "donor_id": str(donor_db1.id)},
        files={"file": ("donor_b.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert len(fake_storage._files) == 0


@pytest.mark.br("BR-PHOTO-04")
def test_t_photo_04_a_general_photo_for_site_succeeds(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-04-a: Supervisor uploads general atmosphere photo for their site in live campaign."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image(fmt="PNG")

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "صورة الفريق المشرف"},
        files={"file": ("team.png", img_bytes, "image/png")},
        headers=headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["kind"] == "general"
    assert data["donor_id"] is None
    assert data["caption"] == "صورة الفريق المشرف"

    photo = db_session.get(Photo, uuid.UUID(data["id"]))
    assert photo is not None
    assert photo.kind == "general"
    assert fake_storage.exists(photo.storage_key)


@pytest.mark.br("BR-PHOTO-04")
def test_t_photo_04_b_admin_photo_upload_for_participating_site(
    photo_client: TestClient,
    db_session: Session,
    admin_user: User,
    live_campaign: Campaign,
    site_b: Site,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-04-b: Administrator uploads photo for participating site B via admin endpoint -> 201."""
    headers = auth_headers(photo_client, db_session, admin_user, test_clock)
    img_bytes = make_test_image(fmt="WEBP")

    response = photo_client.post(
        f"/api/v1/admin/campaigns/{live_campaign.id}/photos",
        data={
            "site_id": str(site_b.id),
            "kind": "general",
            "caption": "صورة موقع ب بواسطة الإدارة",
        },
        files={"file": ("site_b.webp", img_bytes, "image/webp")},
        headers=headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["kind"] == "general"
    assert data["caption"] == "صورة موقع ب بواسطة الإدارة"

    photo = db_session.get(Photo, uuid.UUID(data["id"]))
    assert photo is not None
    assert photo.site_id == site_b.id
    assert photo.campaign_id == live_campaign.id
    assert fake_storage.exists(photo.storage_key)


@pytest.mark.br("BR-PHOTO-04")
@pytest.mark.br("BR-CAMP-16")
def test_t_photo_04_c_upload_on_ended_campaign_rejected(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    live_campaign: Campaign,
    test_clock: FakeClock,
) -> None:
    """T-PHOTO-04-c: Supervisor photo upload on non-live campaign returns 409 no_live_campaign."""
    live_campaign.status = "ended"
    db_session.flush()

    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("ended.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_live_campaign"


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-05")
def test_t_photo_05_a_disguised_text_file_rejected(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
) -> None:
    """T-PHOTO-05-a (★): Text file disguised with .jpg extension and image/jpeg Content-Type returns 422 invalid_image."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    disguised_text = b"GIF89a Fake header with plain ascii text content"

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("fake.jpg", disguised_text, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-05")
def test_t_photo_05_b_unsupported_formats_rejected_with_415(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
) -> None:
    """T-PHOTO-05-b (★): GIF, SVG, and PDF uploads return 415 unsupported_media_type."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)

    # 1. SVG
    svg_payload = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
    resp_svg = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("icon.svg", svg_payload, "image/svg+xml")},
        headers=headers,
    )
    assert resp_svg.status_code == 415
    assert resp_svg.json()["error"]["code"] == "unsupported_media_type"

    # 2. PDF
    pdf_payload = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    resp_pdf = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("doc.pdf", pdf_payload, "application/pdf")},
        headers=headers,
    )
    assert resp_pdf.status_code == 415
    assert resp_pdf.json()["error"]["code"] == "unsupported_media_type"

    # 3. GIF
    gif_img = Image.new("RGB", (50, 50), (10, 20, 30))
    gif_buf = io.BytesIO()
    gif_img.save(gif_buf, format="GIF")
    resp_gif = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("anim.gif", gif_buf.getvalue(), "image/gif")},
        headers=headers,
    )
    assert resp_gif.status_code == 415
    assert resp_gif.json()["error"]["code"] == "unsupported_media_type"


@pytest.mark.br("BR-PHOTO-06")
def test_t_photo_06_a_file_too_large_rejected(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
) -> None:
    """T-PHOTO-06-a: File size exceeding 5MB returns 413 file_too_large."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    oversized_bytes = b"X" * (5 * 1024 * 1024 + 1)

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("huge.jpg", oversized_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-07")
def test_t_photo_07_a_exif_and_gps_stripped(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-07-a (★): Uploaded JPEG containing EXIF and GPS is re-encoded with 0 EXIF tags."""
    # Tag 305 = Software, Tag 271 = Make
    exif_payload = {305: "CameraApp v1.0", 271: "TestCameraMaker"}
    raw_img = make_test_image(fmt="JPEG", exif_data=exif_payload)

    # Verify test fixture image itself contains EXIF before upload
    img_before = Image.open(io.BytesIO(raw_img))
    assert len(img_before.getexif()) > 0

    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("camera.jpg", raw_img, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 201
    photo_id = uuid.UUID(response.json()["id"])
    photo = db_session.get(Photo, photo_id)
    assert photo is not None

    # Retrieve stored bytes and inspect with Pillow
    stored_bytes = fake_storage.get(photo.storage_key)
    assert stored_bytes is not None

    stored_img = Image.open(io.BytesIO(stored_bytes))
    stored_exif = stored_img.getexif()
    assert len(stored_exif) == 0, f"Expected 0 EXIF tags, found: {list(stored_exif.keys())}"


@pytest.mark.br("BR-PHOTO-07")
def test_t_photo_07_b_image_longest_side_downscaled(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-07-b: Large image (2400x1200) has longest side resized to <= 1600px."""
    raw_img = make_test_image(size=(2400, 1200))
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("large.jpg", raw_img, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 201
    photo_id = uuid.UUID(response.json()["id"])
    photo = db_session.get(Photo, photo_id)
    assert photo is not None

    stored_bytes = fake_storage.get(photo.storage_key)
    assert stored_bytes is not None
    stored_img = Image.open(io.BytesIO(stored_bytes))

    width, height = stored_img.size
    assert max(width, height) <= 1600
    assert width == 1600
    assert height == 800  # Aspect ratio 2:1 preserved


@pytest.mark.br("BR-PHOTO-08")
def test_t_photo_08_a_client_filename_discarded(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
) -> None:
    """T-PHOTO-08-a: Malicious client filename is discarded; server UUID key is assigned."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("../../etc/passwd.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 201
    photo_id = uuid.UUID(response.json()["id"])
    photo = db_session.get(Photo, photo_id)
    assert photo is not None
    assert "passwd" not in photo.storage_key
    assert ".." not in photo.storage_key


@pytest.mark.br("BR-PHOTO-12")
def test_t_photo_12_a_caption_too_long_rejected(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
) -> None:
    """T-PHOTO-12-a: Caption with > 200 characters returns 422 validation_error."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "صورة" * 100},
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.br("BR-PHOTO-12")
def test_t_photo_12_b_caption_with_html_rejected(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    test_clock: FakeClock,
) -> None:
    """T-PHOTO-12-b: Caption containing HTML characters returns 422 validation_error."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "<b>أجواء</b>"},
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.critical
def test_admin_on_supervisor_photos_returns_403(
    photo_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_clock: FakeClock,
) -> None:
    """Permission matrix line 137 & row 116: Admin accessing /supervisor/photos returns 403 forbidden."""
    headers = auth_headers(photo_client, db_session, admin_user, test_clock)
    img_bytes = make_test_image()

    response = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_supervisor_list_photos_pagination_and_isolation(
    photo_client: TestClient,
    db_session: Session,
    sup_a: User,
    sup_b: User,
    test_clock: FakeClock,
) -> None:
    """GET /supervisor/photos returns paginated photos for caller's site only."""
    # 1. Upload photo at site A as sup_a
    img_bytes = make_test_image()
    headers_a = auth_headers(photo_client, db_session, sup_a, test_clock)
    res_a = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "صورة الموقع أ"},
        files={"file": ("a.jpg", img_bytes, "image/jpeg")},
        headers=headers_a,
    )
    assert res_a.status_code == 201

    # 2. Upload photo at site B as sup_b
    headers_b = auth_headers(photo_client, db_session, sup_b, test_clock)
    res_b = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "صورة الموقع ب"},
        files={"file": ("b.jpg", img_bytes, "image/jpeg")},
        headers=headers_b,
    )
    assert res_b.status_code == 201

    # 3. sup_a listing photos sees site A photo and NOT site B photo
    headers_a = auth_headers(photo_client, db_session, sup_a, test_clock)
    list_a = photo_client.get("/api/v1/supervisor/photos?limit=25&offset=0", headers=headers_a)
    assert list_a.status_code == 200
    data_a = list_a.json()
    assert data_a["total"] == 1
    assert data_a["items"][0]["caption"] == "صورة الموقع أ"

    # 4. sup_b listing photos sees site B photo and NOT site A photo
    headers_b = auth_headers(photo_client, db_session, sup_b, test_clock)
    list_b = photo_client.get("/api/v1/supervisor/photos?limit=25&offset=0", headers=headers_b)
    assert list_b.status_code == 200
    data_b = list_b.json()
    assert data_b["total"] == 1
    assert data_b["items"][0]["caption"] == "صورة الموقع ب"


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-04")
def test_matrix_row_116_post_supervisor_photos_all_personas(
    photo_client: TestClient,
    db_session: Session,
    site_a: Site,
    test_clock: FakeClock,
) -> None:
    """Docs/07 §4 row 116: POST /supervisor/photos matrix validation across all 6 canonical personas.

    Expected:
    - anon: 401
    - sup_a: 201
    - sup_disabled: 401
    - admin_nomfa: 403 (mfa_required)
    - admin: 403 (forbidden)
    - admin_fresh: 403 (forbidden)
    """
    img_bytes = make_test_image()

    # 1. anon: no credentials
    photo_client.cookies.clear()
    resp_anon = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
    )
    assert resp_anon.status_code == 401
    assert resp_anon.json()["error"]["code"] == "not_authenticated"

    # 2. sup_disabled: inactive supervisor
    user_disabled = User(
        id=uuid.uuid4(),
        email="sup_disabled_p56@blood.local",
        full_name="مشرف معطل",
        role="supervisor",
        site_id=site_a.id,
        is_active=False,
        password_hash="argon2id$mocked",
    )
    db_session.add(user_disabled)
    db_session.flush()

    headers_disabled = auth_headers(photo_client, db_session, user_disabled, test_clock)
    resp_disabled = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
        headers=headers_disabled,
    )
    assert resp_disabled.status_code == 401
    assert resp_disabled.json()["error"]["code"] == "not_authenticated"

    # 3. admin_nomfa: admin without MFA verification
    user_admin = User(
        id=uuid.uuid4(),
        email="admin_nomfa_p56@blood.local",
        full_name="أدمن بلا إم إف إيه",
        role="admin",
        site_id=None,
        is_active=True,
        password_hash="argon2id$mocked",
        totp_enabled=True,
    )
    db_session.add(user_admin)
    db_session.flush()

    headers_admin_nomfa = auth_headers(
        photo_client, db_session, user_admin, test_clock, mfa_verified=False
    )
    resp_admin_nomfa = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
        headers=headers_admin_nomfa,
    )
    assert resp_admin_nomfa.status_code == 403
    assert resp_admin_nomfa.json()["error"]["code"] == "mfa_required"

    # 4. admin: admin with MFA verified (no site context on supervisor routes)
    headers_admin = auth_headers(
        photo_client, db_session, user_admin, test_clock, mfa_verified=True
    )
    resp_admin = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
        headers=headers_admin,
    )
    assert resp_admin.status_code == 403
    assert resp_admin.json()["error"]["code"] == "forbidden"

    # 5. admin_fresh: admin with fresh step-up re-authentication
    headers_admin_fresh = auth_headers(
        photo_client,
        db_session,
        user_admin,
        test_clock,
        mfa_verified=True,
        fresh_reauth=True,
    )
    resp_admin_fresh = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
        headers=headers_admin_fresh,
    )
    assert resp_admin_fresh.status_code == 403
    assert resp_admin_fresh.json()["error"]["code"] == "forbidden"

    # 6. sup_a: active supervisor at participating site during live campaign
    user_sup_a = User(
        id=uuid.uuid4(),
        email="sup_matrix_p56@blood.local",
        full_name="مشرف مصفوفة",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
        password_hash="argon2id$mocked",
    )
    db_session.add(user_sup_a)
    db_session.flush()

    headers_sup_a = auth_headers(photo_client, db_session, user_sup_a, test_clock)
    resp_sup_a = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "صورة مصفوفة"},
        files={"file": ("matrix.jpg", img_bytes, "image/jpeg")},
        headers=headers_sup_a,
    )
    assert resp_sup_a.status_code == 201
    assert resp_sup_a.json()["kind"] == "general"


@pytest.mark.br("BR-PHOTO-10")
def test_t_photo_10_a_delete_supervisor_photo_success(
    photo_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    sup_a: User,
    live_campaign: Campaign,
    site_a: Site,
) -> None:
    """T-PHOTO-10-a: Supervisor deletes photo at own site -> 204, photo deleted, queued in file_deletion_queue."""
    from app.models.photo import FileDeletionQueue

    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    # Upload photo
    resp_up = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "صورة للحذف"},
        files={"file": ("del.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )
    assert resp_up.status_code == 201
    photo_id = resp_up.json()["id"]

    # Verify photo entity exists in DB
    photo_entity = db_session.scalar(select(Photo).where(Photo.id == uuid.UUID(photo_id)))
    assert photo_entity is not None
    storage_key = photo_entity.storage_key

    # Delete photo
    headers_del = auth_headers(photo_client, db_session, sup_a, test_clock)
    resp_del = photo_client.delete(
        f"/api/v1/supervisor/photos/{photo_id}",
        headers=headers_del,
    )
    assert resp_del.status_code == 204

    # Verify photo entity is deleted
    photo_after = db_session.scalar(select(Photo).where(Photo.id == uuid.UUID(photo_id)))
    assert photo_after is None

    # Verify storage key enqueued into file_deletion_queue in same transaction
    queue_row = db_session.scalar(
        select(FileDeletionQueue).where(FileDeletionQueue.storage_key == storage_key)
    )
    assert queue_row is not None
    assert queue_row.deleted_at is None


@pytest.mark.br("BR-PHOTO-10")
@pytest.mark.critical
def test_t_photo_10_b_delete_foreign_site_photo_yields_404(
    photo_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    sup_a: User,
    admin_user: User,
    live_campaign: Campaign,
    site_b: Site,
) -> None:
    """T-PHOTO-10-b: Supervisor attempts to delete photo at foreign site -> 404 (never 403)."""
    admin_headers = auth_headers(
        photo_client, db_session, admin_user, test_clock, mfa_verified=True
    )
    img_bytes = make_test_image()

    resp_admin = photo_client.post(
        f"/api/v1/admin/campaigns/{live_campaign.id}/photos",
        data={"site_id": str(site_b.id), "kind": "general"},
        files={"file": ("foreign.jpg", img_bytes, "image/jpeg")},
        headers=admin_headers,
    )
    assert resp_admin.status_code == 201
    foreign_photo_id = resp_admin.json()["id"]

    # Supervisor sup_a attempts to delete site_b photo
    sup_headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    resp_del = photo_client.delete(
        f"/api/v1/supervisor/photos/{foreign_photo_id}",
        headers=sup_headers,
    )
    assert resp_del.status_code == 404
    assert resp_del.json()["error"]["code"] == "not_found"


@pytest.mark.br("BR-PHOTO-10")
def test_delete_supervisor_photo_after_campaign_ends_yields_409(
    photo_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    sup_a: User,
    live_campaign: Campaign,
) -> None:
    """BR-CAMP-16 ★: Supervisor deleting photo after campaign ends yields 409 no_live_campaign."""
    headers = auth_headers(photo_client, db_session, sup_a, test_clock)
    img_bytes = make_test_image()

    resp_up = photo_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general"},
        files={"file": ("test.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )
    assert resp_up.status_code == 201
    photo_id = resp_up.json()["id"]

    # Campaign ends
    live_campaign.status = "ended"
    db_session.flush()

    headers_del = auth_headers(photo_client, db_session, sup_a, test_clock)
    resp_del = photo_client.delete(
        f"/api/v1/supervisor/photos/{photo_id}",
        headers=headers_del,
    )
    assert resp_del.status_code == 409
    assert resp_del.json()["error"]["code"] == "no_live_campaign"


@pytest.mark.br("BR-PHOTO-10")
def test_delete_admin_photo_success(
    photo_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    admin_user: User,
    live_campaign: Campaign,
    site_b: Site,
) -> None:
    """Admin deletes photo via DELETE /admin/photos/{id} -> 204 No Content and enqueues in file_deletion_queue."""
    from app.models.photo import FileDeletionQueue

    headers = auth_headers(photo_client, db_session, admin_user, test_clock, mfa_verified=True)
    img_bytes = make_test_image()

    resp_up = photo_client.post(
        f"/api/v1/admin/campaigns/{live_campaign.id}/photos",
        data={"site_id": str(site_b.id), "kind": "general"},
        files={"file": ("admin_photo.jpg", img_bytes, "image/jpeg")},
        headers=headers,
    )
    assert resp_up.status_code == 201
    photo_id = resp_up.json()["id"]

    photo_entity = db_session.scalar(select(Photo).where(Photo.id == uuid.UUID(photo_id)))
    assert photo_entity is not None
    storage_key = photo_entity.storage_key

    # Delete photo via admin endpoint
    headers_del = auth_headers(photo_client, db_session, admin_user, test_clock, mfa_verified=True)
    resp_del = photo_client.delete(
        f"/api/v1/admin/photos/{photo_id}",
        headers=headers_del,
    )
    assert resp_del.status_code == 204

    # Verify deleted
    assert db_session.scalar(select(Photo).where(Photo.id == uuid.UUID(photo_id))) is None
    queue_row = db_session.scalar(
        select(FileDeletionQueue).where(FileDeletionQueue.storage_key == storage_key)
    )
    assert queue_row is not None


@pytest.mark.br("BR-PHOTO-10")
def test_delete_supervisor_photo_matrix_all_personas(
    photo_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    live_campaign: Campaign,
    site_a: Site,
) -> None:
    """Canonical permission matrix verification for DELETE /supervisor/photos/{id} across all 6 personas.

    - anon: 401 unauthenticated
    - sup_disabled: 401 unauthenticated
    - admin_nomfa: 403 mfa_required
    - admin: 403 forbidden (admin operates globally with no site context)
    - admin_fresh: 403 forbidden
    - sup_a: 204 No Content
    """
    img_bytes = make_test_image()

    # Create a photo at site_a to be targeted
    photo_target = Photo(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        storage_key=f"photos/{live_campaign.id}/{site_a.id}/{uuid.uuid4()}.jpg",
        kind="general",
        mime_type="image/jpeg",
        size_bytes=len(img_bytes),
    )
    db_session.add(photo_target)
    db_session.flush()

    # 1. anon: unauthenticated visitor
    photo_client.cookies.clear()
    resp_anon = photo_client.delete(f"/api/v1/supervisor/photos/{photo_target.id}")
    assert resp_anon.status_code == 401

    # 2. sup_disabled: deactivated supervisor
    user_sup_dis = User(
        id=uuid.uuid4(),
        email="sup_dis_del@blood.local",
        full_name="مشرف معطل",
        role="supervisor",
        site_id=site_a.id,
        is_active=False,
        password_hash="argon2id$mocked",
    )
    db_session.add(user_sup_dis)
    db_session.flush()

    headers_sup_dis = auth_headers(photo_client, db_session, user_sup_dis, test_clock)
    resp_sup_dis = photo_client.delete(
        f"/api/v1/supervisor/photos/{photo_target.id}",
        headers=headers_sup_dis,
    )
    assert resp_sup_dis.status_code == 401

    # 3. admin_nomfa: admin without MFA
    user_admin = User(
        id=uuid.uuid4(),
        email="admin_del_matrix@blood.local",
        full_name="أدمن مصفوفة",
        role="admin",
        site_id=None,
        is_active=True,
        password_hash="argon2id$mocked",
        totp_enabled=True,
    )
    db_session.add(user_admin)
    db_session.flush()

    headers_admin_nomfa = auth_headers(
        photo_client, db_session, user_admin, test_clock, mfa_verified=False
    )
    resp_admin_nomfa = photo_client.delete(
        f"/api/v1/supervisor/photos/{photo_target.id}",
        headers=headers_admin_nomfa,
    )
    assert resp_admin_nomfa.status_code == 403
    assert resp_admin_nomfa.json()["error"]["code"] == "mfa_required"

    # 4. admin: admin with MFA verified (no site context on supervisor routes -> 403)
    headers_admin = auth_headers(
        photo_client, db_session, user_admin, test_clock, mfa_verified=True
    )
    resp_admin = photo_client.delete(
        f"/api/v1/supervisor/photos/{photo_target.id}",
        headers=headers_admin,
    )
    assert resp_admin.status_code == 403
    assert resp_admin.json()["error"]["code"] == "forbidden"

    # 5. admin_fresh: admin with fresh step-up reauth
    headers_admin_fresh = auth_headers(
        photo_client,
        db_session,
        user_admin,
        test_clock,
        mfa_verified=True,
        fresh_reauth=True,
    )
    resp_admin_fresh = photo_client.delete(
        f"/api/v1/supervisor/photos/{photo_target.id}",
        headers=headers_admin_fresh,
    )
    assert resp_admin_fresh.status_code == 403
    assert resp_admin_fresh.json()["error"]["code"] == "forbidden"

    # 6. sup_a: active supervisor at site_a during live campaign -> 204
    user_sup_a = User(
        id=uuid.uuid4(),
        email="sup_del_matrix_a@blood.local",
        full_name="مشرف مصفوفة الحذف",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
        password_hash="argon2id$mocked",
    )
    db_session.add(user_sup_a)
    db_session.flush()

    headers_sup_a = auth_headers(photo_client, db_session, user_sup_a, test_clock)
    resp_sup_a = photo_client.delete(
        f"/api/v1/supervisor/photos/{photo_target.id}",
        headers=headers_sup_a,
    )
    assert resp_sup_a.status_code == 204
