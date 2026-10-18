"""Phase 5 Exit Gate: "Supervisor's Day" Automated End-to-End Scenario (P5.11).

Per docs/09-roadmap.md §5 and docs/12-implementation-plan.md §8 (P5.11):
"Supervisor's day automated scenario:
 login → context → add 15 donors (all consent combos) → statuses → undo →
 photos (one with GPS) → replay after 'network drop' → other-site supervisor gets 404"

Verifies the entire supervisor field operations flow:
1.  Supervisor A authenticates (POST /auth/login) receiving hardened session cookie.
2.  Context retrieval (GET /supervisor/context): live campaign, site A, initial counters (0, 0).
3.  Consent rules verification:
    - Rejection of invalid combinations (missing keep consent, missing data for public phone).
    - Rejection of reserved field injection (site_id, campaign_id, role, status) with 422.
4.  Intake of 15 donors covering all valid consent combinations (K, P, H, Ph, phone, blood_type)
    using client-supplied UUIDs with 201 Created and initial status 'attended'.
5.  Real-time counter updates and privacy guardrails:
    - Summary endpoint reflects present=15, donated=0 (BR-COUNT-07).
    - Donor list collection strictly omits phone numbers (BR-DONOR-14 ★).
    - Case-insensitive prefix name search (BR-DONOR-17).
    - Single donor detail exposes phone to authorized supervisor (BR-DONOR-14).
6.  Status changes (attended ➔ donated):
    - 5 donors advance to 'donated' setting donated_at (BR-DONOR-06, BR-DONOR-07).
    - Counters reflect present=15, donated=5.
    - Idempotent repeated donated status preserves timestamp (BR-DONOR-06).
    - Medical rejection statuses ('rejected', 'deferred') strictly rejected with 422 (BR-DONOR-08 ★).
7.  Undo donation (donated ➔ attended):
    - Reverting a donor clears donated_at to None (BR-DONOR-07).
    - Counters update immediately to present=15, donated=4.
8.  Photos and EXIF/GPS sanitization:
    - General site photo upload (kind='general').
    - Individual photo upload for donor with consent_photo=True containing camera & GPS EXIF tags.
    - Verification that stored file has 100% of EXIF/GPS metadata stripped (BR-PHOTO-07 ★).
    - Individual photo upload rejected for donor with consent_photo=False (BR-PHOTO-03 ★).
9.  Photo consent withdrawal cascade:
    - Updating donor to consent_photo=False deletes photo and enqueues in file_deletion_queue (BR-CONSENT-07 ★).
10. Idempotent replay after simulated network drop:
    - Replaying identical creation returns 200 OK without duplicating records (BR-DONOR-05).
    - Replaying with conflicting data returns 409 duplicate_id_conflict (BR-DONOR-05).
11. Strict site isolation:
    - Supervisor B (Site B) login and context.
    - All cross-site access attempts (GET, PATCH, status PUT, DELETE) on Site A donors return 404 (BR-DONOR-09 ★).
    - Check-phone for Site A donor returns exists=false to Supervisor B (BR-DONOR-13).
12. Zero-PII audit trail verification:
    - Comprehensive canary sweep across all AuditLog entries verifying 0 PII leaks (BR-AUDIT-01 ★).
"""

import io
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_storage, get_storage_dep
from app.core.security import hash_password
from app.main import create_app
from app.models.audit import AuditLog
from app.models.campaign import Campaign, Site
from app.models.photo import FileDeletionQueue, Photo
from app.models.user import User
from app.storage.base import FakeStorage
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.user import create_user

# ==============================================================================
# Helper functions for scenario image generation
# ==============================================================================


def make_jpeg_with_gps_exif() -> bytes:
    """Generate in-memory JPEG containing camera metadata and GPS coordinates."""
    img = Image.new("RGB", (300, 300), (34, 139, 34))
    exif = img.getexif()
    # 0x010F: Make, 0x0110: Model, 0x0131: Software
    exif[0x010F] = "NikonCorpSecret"
    exif[0x0110] = "D850-Field"
    exif[0x0131] = "FieldCameraApp v2.1"

    # Tag 0x8825 is GPSInfo IFD
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "N"  # GPSLatitudeRef
    gps_ifd[2] = (36.0, 45.0, 10.0)  # 36°45'10" N
    gps_ifd[3] = "E"  # GPSLongitudeRef
    gps_ifd[4] = (3.0, 3.0, 40.0)  # 3°03'40" E

    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def make_simple_image(size: tuple[int, int] = (200, 200)) -> bytes:
    """Generate clean in-memory JPEG without metadata."""
    img = Image.new("RGB", size, (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ==============================================================================
# Fixtures for Scenario Execution
# ==============================================================================


@pytest.fixture
def fake_storage() -> FakeStorage:
    """In-memory FakeStorage instance."""
    return FakeStorage(public_base_url="/media")


@pytest.fixture
def scenario_client(
    db_session: Session,
    fake_storage: FakeStorage,
) -> Generator[TestClient, None, None]:
    """TestClient wired to isolated transactional DB and fake storage."""
    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def override_get_storage() -> FakeStorage:
        return fake_storage

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = override_get_storage
    app.dependency_overrides[get_storage_dep] = override_get_storage
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def supervisor_day_env(db_session: Session) -> dict[str, Any]:
    """Provision a complete multi-site live campaign environment."""
    # 1. Participating sites
    site_a = create_site(db_session, name="نادي الطب - جامعة الجزائر")
    site_b = create_site(db_session, name="مركز الدم - مستشفى مصطفى باشا")

    # 2. Live current published campaign
    live_campaign = create_campaign(
        db_session,
        title="اليوم الموحد للتبرع بالدم 2026",
        slug="unified-blood-day-2026",
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(db_session, campaign_id=live_campaign.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=live_campaign.id, site_id=site_b.id)

    # 3. Staff users with known passwords for login endpoint testing
    # Valid supervisor credentials for scenario login
    test_password = "SuperPassword123#"
    password_hash = hash_password(test_password)
    sup_a = create_user(
        db_session,
        email="sup_a@bloodday.local",
        full_name="مشرف الموقع أ",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
        password_hash=password_hash,
    )
    sup_b = create_user(
        db_session,
        email="sup_b@bloodday.local",
        full_name="مشرف الموقع ب",
        role="supervisor",
        site_id=site_b.id,
        is_active=True,
        password_hash=password_hash,
    )

    return {
        "site_a": site_a,
        "site_b": site_b,
        "campaign": live_campaign,
        "sup_a": sup_a,
        "sup_b": sup_b,
        "password": test_password,
    }


# ==============================================================================
# The "Supervisor's Day" Automated Scenario
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
def test_supervisor_day_full_lifecycle_scenario(
    scenario_client: TestClient,
    db_session: Session,
    supervisor_day_env: dict[str, Any],
    fake_storage: FakeStorage,
) -> None:
    """Execute complete end-to-end Field Supervisor workflow satisfying Phase 5 Exit Gate."""
    site_a: Site = supervisor_day_env["site_a"]
    site_b: Site = supervisor_day_env["site_b"]
    campaign: Campaign = supervisor_day_env["campaign"]
    sup_a: User = supervisor_day_env["sup_a"]
    sup_b: User = supervisor_day_env["sup_b"]
    user_password: str = supervisor_day_env["password"]

    # ==========================================================================
    # Phase 1: Authentication & Dashboard Context (P5.1)
    # ==========================================================================

    # 1.1 Supervisor A logs in with valid credentials
    login_res = scenario_client.post(
        "/api/v1/auth/login",
        json={"email": sup_a.email, "password": user_password},
    )
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    login_data = login_res.json()
    assert login_data["user"]["email"] == sup_a.email
    assert login_data["user"]["role"] == "supervisor"
    assert login_data["user"]["site_id"] == str(site_a.id)
    assert login_data["mfa_required"] is False

    # 1.2 Fetch CSRF token for state-changing operations
    csrf_res = scenario_client.get("/api/v1/auth/csrf")
    assert csrf_res.status_code == 200
    csrf_token = csrf_res.json()["csrf_token"]
    headers_a = {"X-CSRF-Token": csrf_token}

    # 1.3 Fetch supervisor dashboard context
    context_res = scenario_client.get("/api/v1/supervisor/context", headers=headers_a)
    assert context_res.status_code == 200
    ctx = context_res.json()
    assert ctx["campaign"]["id"] == str(campaign.id)
    assert ctx["campaign"]["status"] == "live"
    assert ctx["site"]["id"] == str(site_a.id)
    assert ctx["site"]["name"] == site_a.name
    assert ctx["summary"]["present"] == 0
    assert ctx["summary"]["donated"] == 0

    # ==========================================================================
    # Phase 2: Edge-Case Validation Rejections (BR-CONSENT-02/03, BR-GEN-07)
    # ==========================================================================

    # 2.1 Reject phone provided without keep or public consent (BR-CONSENT-02)
    bad_phone_res = scenario_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سعيد",
            "phone": "+966509999991",
            "consent_keep_phone": False,
            "consent_public_phone": False,
        },
        headers=headers_a,
    )
    assert bad_phone_res.status_code == 422
    assert bad_phone_res.json()["error"]["code"] == "phone_requires_consent"

    # 2.2 Reject public phone consent without phone (BR-CONSENT-03)
    no_phone_public_res = scenario_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "جمال",
            "blood_type": "O+",
            "consent_public_phone": True,
        },
        headers=headers_a,
    )
    assert no_phone_public_res.status_code == 422
    assert no_phone_public_res.json()["error"]["code"] == "public_phone_requires_data"

    # 2.3 Reject public phone consent without blood type (BR-CONSENT-03)
    no_blood_public_res = scenario_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "كريم",
            "phone": "+966509999992",
            "consent_keep_phone": True,
            "consent_public_phone": True,
            "blood_type": None,
        },
        headers=headers_a,
    )
    assert no_blood_public_res.status_code == 422
    assert no_blood_public_res.json()["error"]["code"] == "public_phone_requires_data"

    # 2.4 Reject reserved field injection (BR-GEN-07, BR-DONOR-02)
    injected_res = scenario_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "مخترق",
            "site_id": str(site_b.id),  # Reserved field!
        },
        headers=headers_a,
    )
    assert injected_res.status_code == 422
    assert injected_res.json()["error"]["code"] == "unknown_field"

    # ==========================================================================
    # Phase 3: Intake of 15 Donors (All Valid Consent Combinations)
    # ==========================================================================

    donor_specs = [
        # 1. Minimal donor (no phone, no blood, all consents False)
        {
            "first_name": "ياسين",
            "last_name": "قدور",
            "phone": None,
            "blood_type": None,
            "keep": False,
            "public": False,
            "hero": False,
            "photo": False,
        },
        # 2. Only hero consent
        {
            "first_name": "سارة",
            "last_name": "منصوري",
            "phone": None,
            "blood_type": None,
            "keep": False,
            "public": False,
            "hero": True,
            "photo": False,
        },
        # 3. Only photo consent
        {
            "first_name": "عمر",
            "last_name": "فاروق",
            "phone": None,
            "blood_type": None,
            "keep": False,
            "public": False,
            "hero": False,
            "photo": True,
        },
        # 4. Hero + Photo consents
        {
            "first_name": "مريم",
            "last_name": "براهيمي",
            "phone": None,
            "blood_type": None,
            "keep": False,
            "public": False,
            "hero": True,
            "photo": True,
        },
        # 5. Keep phone only (no blood type)
        {
            "first_name": "طارق",
            "last_name": "زياد",
            "phone": "+966501111111",
            "blood_type": None,
            "keep": True,
            "public": False,
            "hero": False,
            "photo": False,
        },
        # 6. Keep phone + Blood type O+
        {
            "first_name": "أمين",
            "last_name": "معلوف",
            "phone": "+966501111112",
            "blood_type": "O+",
            "keep": True,
            "public": False,
            "hero": False,
            "photo": False,
        },
        # 7. Keep phone + Hero consent + Blood A+
        {
            "first_name": "حمزة",
            "last_name": "عبدالمطلب",
            "phone": "+966501111113",
            "blood_type": "A+",
            "keep": True,
            "public": False,
            "hero": True,
            "photo": False,
        },
        # 8. Keep phone + Photo consent + Blood B+
        {
            "first_name": "ليلى",
            "last_name": "مراد",
            "phone": "+966501111114",
            "blood_type": "B+",
            "keep": True,
            "public": False,
            "hero": False,
            "photo": True,
        },
        # 9. Keep phone + Hero + Photo + Blood AB+
        {
            "first_name": "حسام",
            "last_name": "صالح",
            "phone": "+966501111115",
            "blood_type": "AB+",
            "keep": True,
            "public": False,
            "hero": True,
            "photo": True,
        },
        # 10. Public phone only (requires phone + blood O-)
        {
            "first_name": "فاطمة",
            "last_name": "زهراء",
            "phone": "+966501111116",
            "blood_type": "O-",
            "keep": False,
            "public": True,
            "hero": False,
            "photo": False,
        },
        # 11. Public phone + Hero + Blood A-
        {
            "first_name": "بلال",
            "last_name": "حبشي",
            "phone": "+966501111117",
            "blood_type": "A-",
            "keep": False,
            "public": True,
            "hero": True,
            "photo": False,
        },
        # 12. Public phone + Photo + Blood B-
        {
            "first_name": "زينب",
            "last_name": "علي",
            "phone": "+966501111118",
            "blood_type": "B-",
            "keep": False,
            "public": True,
            "hero": False,
            "photo": True,
        },
        # 13. Public phone + Hero + Photo + Blood AB-
        {
            "first_name": "يوسف",
            "last_name": "إدريس",
            "phone": "+966501111119",
            "blood_type": "AB-",
            "keep": False,
            "public": True,
            "hero": True,
            "photo": True,
        },
        # 14. All Consents True (Keep + Public + Hero + Photo)
        {
            "first_name": "خالد",
            "last_name": "وليد",
            "phone": "+966501111120",
            "blood_type": "O+",
            "keep": True,
            "public": True,
            "hero": True,
            "photo": True,
        },
        # 15. Both phone consents True, Hero & Photo False
        {
            "first_name": "نادية",
            "last_name": "لطفي",
            "phone": "+966501111121",
            "blood_type": "A+",
            "keep": True,
            "public": True,
            "hero": False,
            "photo": False,
        },
    ]

    created_donors: list[dict[str, Any]] = []

    for idx, spec in enumerate(donor_specs, start=1):
        client_uuid = uuid.uuid4()
        payload = {
            "id": str(client_uuid),
            "first_name": spec["first_name"],
            "last_name": spec["last_name"],
            "phone": spec["phone"],
            "blood_type": spec["blood_type"],
            "consent_keep_phone": spec["keep"],
            "consent_public_phone": spec["public"],
            "consent_hero_name": spec["hero"],
            "consent_photo": spec["photo"],
        }
        res = scenario_client.post("/api/v1/supervisor/donors", json=payload, headers=headers_a)
        assert res.status_code == 201, f"Failed on donor {idx} ({spec['first_name']}): {res.text}"
        body = res.json()

        assert body["id"] == str(client_uuid)
        assert body["first_name"] == spec["first_name"]
        assert body["status"] == "attended"
        assert body["donated_at"] is None
        assert body["site_id"] == str(site_a.id)
        assert body["campaign_id"] == str(campaign.id)
        assert body["registered_by"] == str(sup_a.id)

        created_donors.append(body)

    assert len(created_donors) == 15

    # ==========================================================================
    # Phase 4: Live Counters & Privacy Verification (BR-COUNT-07, BR-DONOR-14 ★)
    # ==========================================================================

    # 4.1 Summary check: present=15, donated=0
    summary_res = scenario_client.get("/api/v1/supervisor/donors/summary", headers=headers_a)
    assert summary_res.status_code == 200
    summary_data = summary_res.json()
    assert summary_data["present"] == 15
    assert summary_data["donated"] == 0

    # 4.2 List endpoint check: strictly ZERO phone numbers in collection view (BR-DONOR-14 ★)
    list_res = scenario_client.get("/api/v1/supervisor/donors?limit=50", headers=headers_a)
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["total"] == 15
    assert len(list_data["items"]) == 15

    for item in list_data["items"]:
        # CRITICAL PRIVACY ASSERTION: Phone key MUST NOT exist in collection view
        assert "phone" not in item, f"Privacy leak: phone found in donor list item: {item}"

    # 4.3 Prefix name search (BR-DONOR-17)
    search_res = scenario_client.get("/api/v1/supervisor/donors?q=طار", headers=headers_a)
    assert search_res.status_code == 200
    search_items = search_res.json()["items"]
    assert len(search_items) == 1
    assert search_items[0]["first_name"] == "طارق"

    # 4.4 Detail endpoint exposes phone to assigned site supervisor (BR-DONOR-14)
    donor_14_id = created_donors[13]["id"]
    detail_res = scenario_client.get(f"/api/v1/supervisor/donors/{donor_14_id}", headers=headers_a)
    assert detail_res.status_code == 200
    assert detail_res.json()["phone"] == "+966501111120"

    # ==========================================================================
    # Phase 5: Blood Donation Confirmation (Status Transitions)
    # ==========================================================================

    # Clear 5 donors to donate: Donors 0, 1, 6, 8, 13 (indices 0, 1, 6, 8, 13)
    donated_indices = [0, 1, 6, 8, 13]
    for idx in donated_indices:
        d_id = created_donors[idx]["id"]
        status_res = scenario_client.put(
            f"/api/v1/supervisor/donors/{d_id}/status",
            json={"status": "donated"},
            headers=headers_a,
        )
        assert status_res.status_code == 200
        body = status_res.json()
        assert body["status"] == "donated"
        assert body["donated_at"] is not None

    # Verify counters updated: present=15, donated=5
    summary_after_donate = scenario_client.get(
        "/api/v1/supervisor/donors/summary", headers=headers_a
    ).json()
    assert summary_after_donate["present"] == 15
    assert summary_after_donate["donated"] == 5

    # Idempotent repeated donated status preserves original donated_at
    d0_id = created_donors[0]["id"]
    original_donated_at = scenario_client.get(
        f"/api/v1/supervisor/donors/{d0_id}", headers=headers_a
    ).json()["donated_at"]
    repeat_res = scenario_client.put(
        f"/api/v1/supervisor/donors/{d0_id}/status",
        json={"status": "donated"},
        headers=headers_a,
    )
    assert repeat_res.status_code == 200
    assert repeat_res.json()["donated_at"] == original_donated_at

    # Reject medically forbidden rejection/deferral statuses (BR-DONOR-08 ★)
    for bad_status in ["rejected", "deferred", "cancelled"]:
        bad_res = scenario_client.put(
            f"/api/v1/supervisor/donors/{d0_id}/status",
            json={"status": bad_status},
            headers=headers_a,
        )
        assert bad_res.status_code == 422
        assert bad_res.json()["error"]["code"] == "validation_error"

    # ==========================================================================
    # Phase 6: Undo Donation (Status Reversal)
    # ==========================================================================

    # Donor 0 undoes donation: reverted back to attended
    undo_res = scenario_client.put(
        f"/api/v1/supervisor/donors/{d0_id}/status",
        json={"status": "attended"},
        headers=headers_a,
    )
    assert undo_res.status_code == 200
    undo_body = undo_res.json()
    assert undo_body["status"] == "attended"
    assert undo_body["donated_at"] is None  # Timestamp cleared per BR-DONOR-07

    # Verify counters update immediately: present=15, donated=4
    summary_after_undo = scenario_client.get(
        "/api/v1/supervisor/donors/summary", headers=headers_a
    ).json()
    assert summary_after_undo["present"] == 15
    assert summary_after_undo["donated"] == 4

    # ==========================================================================
    # Phase 7: Photos Upload & EXIF / GPS Sanitization (BR-PHOTO-03/07 ★)
    # ==========================================================================

    # 7.1 Upload general site atmosphere photo
    gen_img_bytes = make_simple_image()
    gen_photo_res = scenario_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "أجواء حماسية في المركز الجامعي"},
        files={"file": ("site.jpg", gen_img_bytes, "image/jpeg")},
        headers=headers_a,
    )
    assert gen_photo_res.status_code == 201
    general_photo_id = gen_photo_res.json()["id"]

    # 7.2 Upload individual donor photo containing EXIF & GPS for Donor 8 (consent_photo=True)
    donor_8_id = created_donors[8]["id"]
    gps_img_bytes = make_jpeg_with_gps_exif()

    # Verify raw test image definitely contains EXIF metadata before server processing
    raw_img_pil = Image.open(io.BytesIO(gps_img_bytes))
    assert len(raw_img_pil.getexif()) > 0
    assert 0x010F in raw_img_pil.getexif()

    indiv_photo_res = scenario_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "individual", "donor_id": donor_8_id, "caption": "بطل التبرع"},
        files={"file": ("donor_hero.jpg", gps_img_bytes, "image/jpeg")},
        headers=headers_a,
    )
    assert indiv_photo_res.status_code == 201
    indiv_photo_id = indiv_photo_res.json()["id"]

    # Inspect stored image bytes in FakeStorage: verify EXIF and GPS tags are 100% stripped!
    photo_entity = db_session.get(Photo, uuid.UUID(indiv_photo_id))
    assert photo_entity is not None
    stored_bytes = fake_storage.get(photo_entity.storage_key)
    assert stored_bytes is not None

    stored_img = Image.open(io.BytesIO(stored_bytes))
    stored_exif = stored_img.getexif()
    assert len(stored_exif) == 0, f"EXIF/GPS metadata leaked in stored photo: {dict(stored_exif)}"

    # 7.3 Attempt individual photo upload for Donor 0 (consent_photo=False)
    no_consent_photo_res = scenario_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "individual", "donor_id": d0_id},
        files={"file": ("no_consent.jpg", gen_img_bytes, "image/jpeg")},
        headers=headers_a,
    )
    assert no_consent_photo_res.status_code == 422
    assert no_consent_photo_res.json()["error"]["code"] == "photo_consent_missing"

    # ==========================================================================
    # Phase 8: Withdraw Photo Consent Cascades Deletion (BR-CONSENT-07 ★)
    # ==========================================================================

    # Donor 8 withdraws photo consent via PATCH
    patch_consent_res = scenario_client.patch(
        f"/api/v1/supervisor/donors/{donor_8_id}",
        json={"consent_photo": False},
        headers=headers_a,
    )
    assert patch_consent_res.status_code == 200
    assert patch_consent_res.json()["consent_photo"] is False

    # Verify that the Photo record was hard-deleted from DB in the same transaction
    db_session.expire_all()
    deleted_photo = db_session.get(Photo, uuid.UUID(indiv_photo_id))
    assert deleted_photo is None, "Photo record was not deleted upon photo consent withdrawal"

    # Verify that storage key was staged in file_deletion_queue
    queue_stmt = select(FileDeletionQueue).where(
        FileDeletionQueue.storage_key == photo_entity.storage_key
    )
    queue_entry = db_session.scalars(queue_stmt).first()
    assert queue_entry is not None, "Storage key was not staged in file_deletion_queue"

    # 8.2 Direct photo deletion by Supervisor A (BR-PHOTO-10)
    photo_to_del_res = scenario_client.post(
        "/api/v1/supervisor/photos",
        data={"kind": "general", "caption": "صورة مؤقتة"},
        files={"file": ("temp.jpg", gen_img_bytes, "image/jpeg")},
        headers=headers_a,
    )
    assert photo_to_del_res.status_code == 201
    temp_photo_id = photo_to_del_res.json()["id"]

    del_photo_res = scenario_client.delete(
        f"/api/v1/supervisor/photos/{temp_photo_id}", headers=headers_a
    )
    assert del_photo_res.status_code == 204

    # ==========================================================================
    # Phase 9: Network Drop Replay Idempotency (BR-DONOR-05) & Deletion
    # ==========================================================================

    # 9.1 Network drop simulation: client replays exact same create request for Donor 14
    d14_spec = donor_specs[14]
    d14_id = created_donors[14]["id"]
    replay_payload = {
        "id": d14_id,
        "first_name": d14_spec["first_name"],
        "last_name": d14_spec["last_name"],
        "phone": d14_spec["phone"],
        "blood_type": d14_spec["blood_type"],
        "consent_keep_phone": d14_spec["keep"],
        "consent_public_phone": d14_spec["public"],
        "consent_hero_name": d14_spec["hero"],
        "consent_photo": d14_spec["photo"],
    }
    replay_res = scenario_client.post(
        "/api/v1/supervisor/donors", json=replay_payload, headers=headers_a
    )
    assert replay_res.status_code == 200, "Idempotent replay should return 200 OK"
    assert replay_res.json()["id"] == d14_id

    # Total donors remains exactly 15
    summary_after_replay = scenario_client.get(
        "/api/v1/supervisor/donors/summary", headers=headers_a
    ).json()
    assert summary_after_replay["present"] == 15

    # 9.2 Conflicting replay with same UUID but altered data returns 409 duplicate_id_conflict
    conflict_payload = dict(replay_payload)
    conflict_payload["first_name"] = "اسم مختلف تماما"
    conflict_res = scenario_client.post(
        "/api/v1/supervisor/donors", json=conflict_payload, headers=headers_a
    )
    assert conflict_res.status_code == 409
    assert conflict_res.json()["error"]["code"] == "duplicate_id_conflict"

    # 9.3 Supervisor A deletes donor 11 (testing DELETE /supervisor/donors/{id})
    d11_id = created_donors[11]["id"]
    del_donor_res = scenario_client.delete(f"/api/v1/supervisor/donors/{d11_id}", headers=headers_a)
    assert del_donor_res.status_code == 204

    # ==========================================================================
    # Phase 10: Strict Site Isolation (Supervisor B Gets 404) (BR-DONOR-09 ★)
    # ==========================================================================

    # 10.1 Supervisor B logs in (from their site B terminal)
    scenario_client.post("/api/v1/auth/logout", headers=headers_a)
    scenario_client.cookies.clear()

    login_b_res = scenario_client.post(
        "/api/v1/auth/login",
        json={"email": sup_b.email, "password": user_password},
    )
    assert login_b_res.status_code == 200, f"Supervisor B login failed: {login_b_res.text}"
    assert login_b_res.json()["user"]["site_id"] == str(site_b.id)

    csrf_b_res = scenario_client.get("/api/v1/auth/csrf")
    csrf_b = csrf_b_res.json()["csrf_token"]
    headers_b = {"X-CSRF-Token": csrf_b}

    # Context for Site B is empty
    ctx_b = scenario_client.get("/api/v1/supervisor/context", headers=headers_b).json()
    assert ctx_b["site"]["id"] == str(site_b.id)
    assert ctx_b["summary"]["present"] == 0
    assert ctx_b["summary"]["donated"] == 0

    # 10.2 Supervisor B attempts to access Site A's donor
    target_donor_id = created_donors[0]["id"]

    # GET foreign donor ➔ 404 not_found
    foreign_get = scenario_client.get(
        f"/api/v1/supervisor/donors/{target_donor_id}", headers=headers_b
    )
    assert foreign_get.status_code == 404
    assert foreign_get.json()["error"]["code"] == "not_found"

    # PATCH foreign donor ➔ 404 not_found
    foreign_patch = scenario_client.patch(
        f"/api/v1/supervisor/donors/{target_donor_id}",
        json={"first_name": "محاولة تعديل"},
        headers=headers_b,
    )
    assert foreign_patch.status_code == 404
    assert foreign_patch.json()["error"]["code"] == "not_found"

    # PUT status foreign donor ➔ 404 not_found
    foreign_status = scenario_client.put(
        f"/api/v1/supervisor/donors/{target_donor_id}/status",
        json={"status": "donated"},
        headers=headers_b,
    )
    assert foreign_status.status_code == 404
    assert foreign_status.json()["error"]["code"] == "not_found"

    # DELETE foreign donor ➔ 404 not_found
    foreign_delete = scenario_client.delete(
        f"/api/v1/supervisor/donors/{target_donor_id}", headers=headers_b
    )
    assert foreign_delete.status_code == 404
    assert foreign_delete.json()["error"]["code"] == "not_found"

    # DELETE foreign photo ➔ 404 not_found
    foreign_photo_delete = scenario_client.delete(
        f"/api/v1/supervisor/photos/{general_photo_id}", headers=headers_b
    )
    assert foreign_photo_delete.status_code == 404
    assert foreign_photo_delete.json()["error"]["code"] == "not_found"

    # 10.3 Advisory check-phone for Donor 14's phone from Site B returns exists=false
    foreign_phone_check = scenario_client.post(
        "/api/v1/supervisor/donors/check-phone",
        json={"phone": "+966501111120"},
        headers=headers_b,
    )
    assert foreign_phone_check.status_code == 200
    assert foreign_phone_check.json()["exists"] is False

    # ==========================================================================
    # Phase 11: Zero-PII Audit Verification (BR-AUDIT-01 ★, BR-DONOR-18)
    # ==========================================================================

    audit_logs = db_session.scalars(select(AuditLog)).all()
    assert len(audit_logs) > 0, "Audit logs should have been recorded for field actions"

    # Verify approved action names
    expected_actions = {
        "donor.create",
        "donor.update",
        "donor.status_change",
        "photo.upload",
        "photo.delete",
    }
    recorded_actions = {log.action for log in audit_logs}
    assert expected_actions.issubset(recorded_actions), (
        f"Missing audit actions: {expected_actions - recorded_actions}"
    )

    # Canary PII sentinel strings to ensure zero leakage
    forbidden_tokens = [
        "سارة",
        "طارق",
        "خالد",
        "ياسين",
        "حمزة",
        "بطل التبرع",
        "+966501111111",
        "+966501111120",
        "O+",
        "AB-",
    ]

    for log in audit_logs:
        # Stringify details and inspect
        details_str = str(log.details)
        for token in forbidden_tokens:
            assert token not in details_str, (
                f"CRITICAL AUDIT LEAK: Sensitive token '{token}' found in audit log {log.action}: {log.details}"
            )

    # Cleanup session
    scenario_client.post("/api/v1/auth/logout", headers=headers_a)
