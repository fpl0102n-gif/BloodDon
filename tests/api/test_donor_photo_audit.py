"""Integration tests for donor, photo, and file queue audit events (P5.9).

Per:
- docs/06-business-rules.md §6 (BR-DONOR-18: audit for create, status_change, update, delete, relocate)
- docs/06-business-rules.md §8 (BR-PHOTO-10: photo deletion queue & audit)
- docs/06-business-rules.md §13:
  - BR-AUDIT-01 (★): Zero PII in audit log details (no names, phones, or blood types).
  - BR-AUDIT-02: Append-only audit log.
  - BR-AUDIT-03 (★): Same transaction; failure aborts operation.
  - BR-AUDIT-04: Actor attribution (null for system CLI operations).
  - BR-AUDIT-05 (★): Completeness of audit events catalogue.
- docs/07-test-cases.md §5.5 (T-DONOR-18-a: audit logged for all donor actions without PII)
- docs/07-test-cases.md §5.13:
  - T-AUDIT-01-a (★): Comprehensive sentinel canary sweep across database audit records.
  - T-AUDIT-03-a (★): Failure injection in audit persistence rolls back the business operation atomically.
  - T-AUDIT-05-a: All approved donor/photo/queue action names catalogued.
"""

import io
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
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
from app.models.photo import FileDeletionQueue, Photo
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.file_queue import FileQueueRepository
from app.repositories.photos import PhotoRepository
from app.repositories.sessions import SessionRepository
from app.repositories.sites import SiteRepository
from app.schemas.donor import DonorCreateIn
from app.services.audit import (
    FORBIDDEN_BLOOD_TYPES,
    FORBIDDEN_DETAIL_KEYS,
    PHONE_REGEX,
    AuditService,
)
from app.services.donors import DonorService
from app.services.file_queue import FileQueueService
from app.services.photos import PhotoService
from app.services.sessions import SessionService
from app.storage.base import FakeStorage

SENTINEL_FIRST_NAME = "ZZFIRST"
SENTINEL_LAST_NAME = "ZZLAST"
SENTINEL_PHONE = "0550123456"
SENTINEL_BLOOD_TYPE = "O+"


def make_test_image(
    fmt: str = "JPEG",
    size: tuple[int, int] = (100, 100),
    color: tuple[int, int, int] = (200, 50, 50),
) -> bytes:
    """Create test image bytes."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage(public_base_url="/media")


@pytest.fixture
def test_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC))


@pytest.fixture
def test_site(db_session: Session) -> Site:
    site = Site(
        id=uuid.uuid4(),
        name="موقع التدقيق الميداني",
        address="شارع ديدوش مراد",
        is_active=True,
    )
    db_session.add(site)
    db_session.flush()
    return site


@pytest.fixture
def live_campaign(db_session: Session, test_site: Site) -> Campaign:
    camp = Campaign(
        id=uuid.uuid4(),
        title="حملة التدقيق الميدانية",
        slug=f"audit-camp-{uuid.uuid4().hex[:6]}",
        campaign_date=datetime(2026, 11, 20, 0, 0, 0, tzinfo=UTC).date(),
        status="live",
        is_current=True,
    )
    db_session.add(camp)
    db_session.flush()

    csite = CampaignSite(
        campaign_id=camp.id,
        site_id=test_site.id,
        start_time=datetime(2026, 11, 20, 8, 0, 0, tzinfo=UTC),
        end_time=datetime(2026, 11, 20, 18, 0, 0, tzinfo=UTC),
    )
    db_session.add(csite)
    db_session.flush()
    return camp


@pytest.fixture
def supervisor_user(db_session: Session, test_site: Site) -> User:
    user = User(
        id=uuid.uuid4(),
        email="sup_audit@blood.local",
        full_name="مشرف التدقيق الميداني",
        role="supervisor",
        site_id=test_site.id,
        is_active=True,
        password_hash="argon2id$mocked",
        totp_enabled=False,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin_audit@blood.local",
        full_name="أدمن التدقيق",
        role="admin",
        site_id=None,
        is_active=True,
        password_hash="argon2id$mocked",
        totp_enabled=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def setup_session(
    client: TestClient,
    db: Session,
    user: User,
    clock: FakeClock,
    *,
    mfa_verified: bool = False,
    reauth: bool = False,
) -> dict[str, str]:
    """Helper attaching valid session cookie and CSRF token for a given user."""
    settings = get_settings()
    session_repo = SessionRepository(db)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    raw_token, session_obj = session_service.create_session(
        user.id,
        user.role,
        mfa_verified=mfa_verified,
    )
    if reauth:
        record_session_reauth(str(session_obj.id), clock.now())

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    client.cookies.set(settings.cookie_name, raw_token)
    return {"X-CSRF-Token": csrf_token}


@pytest.fixture
def audit_client(
    db_session: Session,
    fake_storage: FakeStorage,
    test_clock: FakeClock,
) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_clock] = lambda: test_clock
    app.dependency_overrides[get_storage_dep] = lambda: fake_storage
    app.dependency_overrides[get_session_service] = lambda: SessionService(
        SessionRepository(db_session),
        clock=test_clock,
        settings=get_settings(),
    )

    with TestClient(app, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


# ==============================================================================
# 1. T-DONOR-18-a: Complete Donor Actions Audit Coverage (Zero PII)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-18")
@pytest.mark.br("BR-AUDIT-01")
def test_t_donor_18_a_all_donor_actions_audited_zero_pii(
    audit_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    live_campaign: Campaign,
    test_site: Site,
    supervisor_user: User,
    admin_user: User,
) -> None:
    """Verify that every donor lifecycle action records an audit event with IDs only (T-DONOR-18-a).

    Actions verified:
    1. donor.create -> site_id, campaign_id, status (strictly no names/phone/blood type)
    2. donor.update -> updated_fields list of field names only (strictly no values)
    3. donor.status_change -> old_status, new_status
    4. donor.delete -> site_id, campaign_id
    5. donor.anonymize -> site_id, campaign_id
    """
    sup_headers = setup_session(audit_client, db_session, supervisor_user, test_clock)
    donor_id = uuid.uuid4()

    # 1. donor.create
    create_payload = {
        "id": str(donor_id),
        "first_name": SENTINEL_FIRST_NAME,
        "last_name": SENTINEL_LAST_NAME,
        "phone": SENTINEL_PHONE,
        "blood_type": SENTINEL_BLOOD_TYPE,
        "consent_keep_phone": True,
        "consent_hero_name": True,
        "consent_photo": True,
        "consent_public_phone": True,
    }
    resp = audit_client.post("/api/v1/supervisor/donors", json=create_payload, headers=sup_headers)
    assert resp.status_code == 201

    db_session.flush()
    create_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "donor.create",
            AuditLog.entity_id == donor_id,
        )
    ).scalar_one_or_none()

    assert create_event is not None
    assert create_event.actor_user_id == supervisor_user.id
    assert create_event.entity_type == "donor"
    assert create_event.details == {
        "site_id": str(test_site.id),
        "campaign_id": str(live_campaign.id),
        "status": "attended",
    }
    # Verify zero PII
    for pii in [SENTINEL_FIRST_NAME, SENTINEL_LAST_NAME, SENTINEL_PHONE, SENTINEL_BLOOD_TYPE]:
        assert pii not in str(create_event.details)

    # 2. donor.update
    update_payload = {
        "last_name": "ZZUPDATED",
        "consent_hero_name": False,
    }
    resp = audit_client.patch(
        f"/api/v1/supervisor/donors/{donor_id}",
        json=update_payload,
        headers=sup_headers,
    )
    assert resp.status_code == 200

    db_session.flush()
    update_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "donor.update",
            AuditLog.entity_id == donor_id,
        )
    ).scalar_one_or_none()

    assert update_event is not None
    assert update_event.actor_user_id == supervisor_user.id
    assert update_event.entity_type == "donor"
    # details must contain ONLY changed field names, not new or old values
    assert update_event.details is not None
    assert "updated_fields" in update_event.details
    assert set(update_event.details["updated_fields"]) == {"last_name", "consent_hero_name"}
    assert "ZZUPDATED" not in str(update_event.details)

    # 3. donor.status_change
    status_payload = {"status": "donated"}
    resp = audit_client.put(
        f"/api/v1/supervisor/donors/{donor_id}/status",
        json=status_payload,
        headers=sup_headers,
    )
    assert resp.status_code == 200

    db_session.flush()
    status_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "donor.status_change",
            AuditLog.entity_id == donor_id,
        )
    ).scalar_one_or_none()

    assert status_event is not None
    assert status_event.actor_user_id == supervisor_user.id
    assert status_event.entity_type == "donor"
    assert status_event.details == {
        "old_status": "attended",
        "new_status": "donated",
    }

    # 4. donor.delete
    resp = audit_client.delete(f"/api/v1/supervisor/donors/{donor_id}", headers=sup_headers)
    assert resp.status_code == 204

    db_session.flush()
    delete_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "donor.delete",
            AuditLog.entity_id == donor_id,
        )
    ).scalar_one_or_none()

    assert delete_event is not None
    assert delete_event.actor_user_id == supervisor_user.id
    assert delete_event.entity_type == "donor"
    assert delete_event.details == {
        "site_id": str(test_site.id),
        "campaign_id": str(live_campaign.id),
    }

    # 5. donor.anonymize
    # Create donor 2 and trigger anonymization
    donor_repo = DonorRepository(db_session)
    campaign_repo = CampaignRepository(db_session)
    site_repo = SiteRepository(db_session)
    audit_service = AuditService(AuditRepository(db_session), clock=test_clock)
    donor_service = DonorService(
        donor_repo,
        campaign_repo,
        site_repo,
        audit_service=audit_service,
        clock=test_clock,
    )

    donor_2, _ = donor_service.create_donor(
        supervisor_user,
        DonorCreateIn(
            id=uuid.uuid4(),
            first_name="كريم",
            last_name="بن",
            consent_keep_phone=False,
            consent_hero_name=False,
            consent_photo=False,
            consent_public_phone=False,
        ),
    )
    db_session.flush()

    donor_service.anonymize_donor(admin_user, donor_2.id)
    db_session.flush()

    anon_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "donor.anonymize",
            AuditLog.entity_id == donor_2.id,
        )
    ).scalar_one_or_none()

    assert anon_event is not None
    assert anon_event.actor_user_id == admin_user.id
    assert anon_event.entity_type == "donor"
    assert anon_event.details == {
        "site_id": str(test_site.id),
        "campaign_id": str(live_campaign.id),
    }


# ==============================================================================
# 2. Photo Actions Audit Coverage (Zero PII)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-10")
@pytest.mark.br("BR-AUDIT-01")
def test_photo_actions_audited_zero_pii(
    audit_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    live_campaign: Campaign,
    test_site: Site,
    supervisor_user: User,
    admin_user: User,
) -> None:
    """Verify that photo upload and deletion record audit events with IDs and storage key only."""
    # 1. Supervisor photo.upload (general atmosphere photo)
    sup_headers = setup_session(audit_client, db_session, supervisor_user, test_clock)
    img_data = make_test_image("JPEG")
    files = {"file": ("atmosphere.jpg", img_data, "image/jpeg")}
    data = {"kind": "general", "caption": "أجواء التبرع الميداني"}

    resp = audit_client.post(
        "/api/v1/supervisor/photos",
        data=data,
        files=files,
        headers=sup_headers,
    )
    assert resp.status_code == 201
    photo_id_gen = uuid.UUID(resp.json()["id"])

    db_session.flush()
    upload_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "photo.upload",
            AuditLog.entity_id == photo_id_gen,
        )
    ).scalar_one_or_none()

    assert upload_event is not None
    assert upload_event.actor_user_id == supervisor_user.id
    assert upload_event.entity_type == "photo"
    assert upload_event.details == {
        "kind": "general",
        "site_id": str(test_site.id),
        "campaign_id": str(live_campaign.id),
    }

    # 2. Supervisor photo.delete
    resp = audit_client.delete(f"/api/v1/supervisor/photos/{photo_id_gen}", headers=sup_headers)
    assert resp.status_code == 204

    db_session.flush()
    delete_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "photo.delete",
            AuditLog.entity_id == photo_id_gen,
        )
    ).scalar_one_or_none()

    assert delete_event is not None
    assert delete_event.actor_user_id == supervisor_user.id
    assert delete_event.entity_type == "photo"
    assert delete_event.details is not None
    assert delete_event.details["site_id"] == str(test_site.id)
    assert delete_event.details["campaign_id"] == str(live_campaign.id)
    assert "storage_key" in delete_event.details

    # 3. Admin photo upload
    admin_headers = setup_session(
        audit_client, db_session, admin_user, test_clock, mfa_verified=True, reauth=True
    )
    files_admin = {"file": ("admin_photo.jpg", img_data, "image/jpeg")}
    data_admin = {"site_id": str(test_site.id), "kind": "general"}
    resp = audit_client.post(
        f"/api/v1/admin/campaigns/{live_campaign.id}/photos",
        data=data_admin,
        files=files_admin,
        headers=admin_headers,
    )
    assert resp.status_code == 201
    photo_id_admin = uuid.UUID(resp.json()["id"])

    db_session.flush()
    admin_upload_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "photo.upload",
            AuditLog.entity_id == photo_id_admin,
        )
    ).scalar_one_or_none()
    assert admin_upload_event is not None
    assert admin_upload_event.actor_user_id == admin_user.id

    # 4. Admin photo delete
    resp = audit_client.delete(f"/api/v1/admin/photos/{photo_id_admin}", headers=admin_headers)
    assert resp.status_code == 204

    db_session.flush()
    admin_delete_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "photo.delete",
            AuditLog.entity_id == photo_id_admin,
        )
    ).scalar_one_or_none()
    assert admin_delete_event is not None
    assert admin_delete_event.actor_user_id == admin_user.id


# ==============================================================================
# 3. File Queue Actions Audit Coverage (Zero PII)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-10")
@pytest.mark.br("BR-AUDIT-01")
def test_file_queue_actions_audited_zero_pii(
    audit_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
    admin_user: User,
) -> None:
    """Verify that file queue operations record audit events with IDs and counts only."""
    admin_headers = setup_session(
        audit_client, db_session, admin_user, test_clock, mfa_verified=True, reauth=True
    )

    # Stage a queue item
    queue_item = FileDeletionQueue(storage_key="photos/camp1/site1/test_queue.jpg")
    db_session.add(queue_item)
    db_session.flush()

    # 1. file_queue.mark_deleted via API
    resp = audit_client.post(
        f"/api/v1/admin/file-queue/{queue_item.id}/mark-deleted",
        headers=admin_headers,
    )
    assert resp.status_code == 200

    db_session.flush()
    mark_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "file_queue.mark_deleted",
        )
    ).scalar_one_or_none()

    assert mark_event is not None
    assert mark_event.actor_user_id == admin_user.id
    assert mark_event.entity_type == "file_deletion_queue"
    assert mark_event.details == {
        "queue_id": queue_item.id,
        "storage_key": "photos/camp1/site1/test_queue.jpg",
    }

    # 2. file_queue.batch_process via FileQueueService
    item2 = FileDeletionQueue(storage_key="photos/camp1/site1/item2.jpg")
    fake_storage.save("photos/camp1/site1/item2.jpg", b"fake image bytes")
    db_session.add(item2)
    db_session.flush()

    file_queue_repo = FileQueueRepository(db_session)
    audit_service = AuditService(AuditRepository(db_session), clock=test_clock)
    file_queue_service = FileQueueService(
        file_queue_repo,
        storage=fake_storage,
        clock=test_clock,
        audit_service=audit_service,
    )

    processed = file_queue_service.process_queue(
        batch_size=10,
        actor_user_id=admin_user.id,
    )
    assert processed == 1

    db_session.flush()
    batch_event = db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "file_queue.batch_process",
        )
    ).scalar_one_or_none()

    assert batch_event is not None
    assert batch_event.actor_user_id == admin_user.id
    assert batch_event.entity_type == "file_deletion_queue"
    assert batch_event.details == {
        "processed_count": 1,
        "batch_size": 10,
    }


# ==============================================================================
# 4. T-AUDIT-01-a ★: Comprehensive Sentinel Canary Sweep Across Database
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-AUDIT-01")
def test_t_audit_01_a_comprehensive_sentinel_sweep_zero_pii(
    audit_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    live_campaign: Campaign,
    test_site: Site,
    supervisor_user: User,
) -> None:
    """Execute complete field flow with sentinel values, then verify zero PII in audit_log.

    Per docs/07 §3.3 & T-AUDIT-01-a ★:
    - Flow executes with canary values: ZZFIRST, ZZLAST, 0550123456, O+.
    - Scan ALL rows in audit_log table.
    - 100% absence of sentinels, blood types, phone numbers, and emails in any audit details.
    """
    sup_headers = setup_session(audit_client, db_session, supervisor_user, test_clock)
    donor_id = uuid.uuid4()

    # Step 1: Create donor with canary data
    resp_create = audit_client.post(
        "/api/v1/supervisor/donors",
        json={
            "id": str(donor_id),
            "first_name": SENTINEL_FIRST_NAME,
            "last_name": SENTINEL_LAST_NAME,
            "phone": SENTINEL_PHONE,
            "blood_type": SENTINEL_BLOOD_TYPE,
            "consent_keep_phone": True,
            "consent_hero_name": True,
            "consent_photo": True,
            "consent_public_phone": True,
        },
        headers=sup_headers,
    )
    assert resp_create.status_code == 201

    # Step 2: Update donor
    resp_patch = audit_client.patch(
        f"/api/v1/supervisor/donors/{donor_id}",
        json={"consent_hero_name": False},
        headers=sup_headers,
    )
    assert resp_patch.status_code == 200

    # Step 3: Change status
    resp_status = audit_client.put(
        f"/api/v1/supervisor/donors/{donor_id}/status",
        json={"status": "donated"},
        headers=sup_headers,
    )
    assert resp_status.status_code == 200

    # Step 4: Upload individual photo
    img_data = make_test_image("JPEG")
    resp_photo = audit_client.post(
        "/api/v1/supervisor/photos",
        data={"donor_id": str(donor_id), "kind": "individual", "caption": "صورة فردية للمتبرع"},
        files={"file": ("sentinel_photo.jpg", img_data, "image/jpeg")},
        headers=sup_headers,
    )
    assert resp_photo.status_code == 201

    # Step 5: Delete donor (cascades individual photo deletion)
    resp_delete = audit_client.delete(
        f"/api/v1/supervisor/donors/{donor_id}",
        headers=sup_headers,
    )
    assert resp_delete.status_code == 204

    db_session.flush()

    # Sweep every single audit log record in the database
    all_logs = db_session.execute(select(AuditLog)).scalars().all()
    assert len(all_logs) >= 5

    for row in all_logs:
        details_str = str(row.details or {})

        # Assert no canary sentinels
        assert SENTINEL_FIRST_NAME not in details_str, (
            f"Leak detected in row {row.id} action {row.action}: {SENTINEL_FIRST_NAME}"
        )
        assert SENTINEL_LAST_NAME not in details_str, (
            f"Leak detected in row {row.id} action {row.action}: {SENTINEL_LAST_NAME}"
        )
        assert SENTINEL_PHONE not in details_str, (
            f"Leak detected in row {row.id} action {row.action}: {SENTINEL_PHONE}"
        )

        # Assert no blood types
        for bt in FORBIDDEN_BLOOD_TYPES:
            assert f"'{bt}'" not in details_str and f'"{bt}"' not in details_str, (
                f"Blood type leak detected in row {row.id} action {row.action}: {bt}"
            )

        # Assert no phone regex match
        if row.details:
            for k, v in row.details.items():
                assert k.lower() not in FORBIDDEN_DETAIL_KEYS, (
                    f"Forbidden key {k} in row {row.id} action {row.action}"
                )
                if isinstance(v, str):
                    assert not PHONE_REGEX.match(v), (
                        f"Phone regex match in row {row.id} action {row.action}: {v}"
                    )
                    assert "@" not in v, f"Email leak in row {row.id} action {row.action}: {v}"


# ==============================================================================
# 5. T-AUDIT-03-a ★: Failure Injection Rolls Back Atomic Operations
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-AUDIT-03")
def test_t_audit_03_a_failure_injection_rolls_back_atomic_operation(
    db_session: Session,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
    live_campaign: Campaign,
    test_site: Site,
    supervisor_user: User,
) -> None:
    """Verify that failure during audit log insertion aborts and rolls back the operation (BR-AUDIT-03 ★).

    Per docs/07 §5.13 & T-AUDIT-03-a ★:
    "حقن فشل في إدراج التدقيق: تفشل العملية كلها ولا أثر"
    """
    donor_repo = DonorRepository(db_session)
    campaign_repo = CampaignRepository(db_session)
    site_repo = SiteRepository(db_session)
    audit_repo = AuditRepository(db_session)
    audit_service = AuditService(audit_repo, clock=test_clock)

    donor_service = DonorService(
        donor_repo,
        campaign_repo,
        site_repo,
        audit_service=audit_service,
        clock=test_clock,
    )

    # Setup donor
    donor_id = uuid.uuid4()
    _donor, _ = donor_service.create_donor(
        supervisor_user,
        DonorCreateIn(
            id=donor_id,
            first_name="أحمد",
            consent_keep_phone=False,
            consent_hero_name=False,
            consent_photo=False,
            consent_public_phone=False,
        ),
    )
    db_session.flush()

    # Case 1: Failure during delete_donor rolls back deletion
    savepoint = db_session.begin_nested()
    with (
        patch.object(
            audit_repo,
            "create",
            side_effect=RuntimeError("Simulated database audit disk write error"),
        ),
        pytest.raises(RuntimeError, match="Simulated database audit disk write error"),
    ):
        donor_service.delete_donor(supervisor_user, donor_id)

    savepoint.rollback()

    # Donor must still exist!
    donor_check = donor_repo.get_by_id(donor_id)
    assert donor_check is not None
    assert donor_check.id == donor_id

    # Case 2: Failure during photo.upload cleans up storage and rolls back DB
    photo_repo = PhotoRepository(db_session)
    photo_service = PhotoService(
        photo_repo,
        campaign_repo,
        site_repo,
        donor_repo,
        storage=fake_storage,
        audit_service=audit_service,
    )

    savepoint2 = db_session.begin_nested()
    img_bytes = make_test_image("JPEG")
    with (
        patch.object(
            audit_repo,
            "create",
            side_effect=RuntimeError("Simulated database audit disk write error"),
        ),
        pytest.raises(RuntimeError, match="Simulated database audit disk write error"),
    ):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=img_bytes,
            kind="general",
        )

    savepoint2.rollback()

    # Verify zero photos were stored in DB
    stored_photos = db_session.execute(select(Photo)).scalars().all()
    assert len(stored_photos) == 0
    # Verify file was cleaned up from storage
    assert len(fake_storage._files) == 0
