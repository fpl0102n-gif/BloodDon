"""Integration tests for Single-Donor Anonymization and Privacy Deletion on Request (P8.4).

Per:
- docs/04-api.md §16 (DELETE /admin/pledges/{id})
- docs/04-api.md §17 (POST /admin/donors/{id}/anonymize, PATCH /admin/donors/{id})
- docs/06-business-rules.md §6 (BR-DONOR-15: Anonymized records are read-only)
- docs/06-business-rules.md §7 (BR-CONSENT-07: Photo deletion on consent revocation)
- docs/06-business-rules.md §12 (BR-PRIV-09 ★: Single-donor withdrawal on request)
- docs/06-business-rules.md §12 (BR-PRIV-10: Immediate hard delete of pledge/phone on request)
- docs/06-business-rules.md §13 (BR-AUDIT-01 ★, BR-AUDIT-05: Zero PII in audit details)
- docs/07-test-cases.md §5.12 (T-PRIV-09-a ★, T-PRIV-09-b ★, T-PRIV-10-a)
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_settings,
    record_session_reauth,
)
from app.models.audit import AuditLog
from app.models.campaign import Campaign, Site
from app.models.donor import Donor
from app.models.photo import FileDeletionQueue, Photo
from app.models.pledge import Pledge
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.sessions import SessionRepository
from app.repositories.sites import SiteRepository
from app.services.audit import AuditService
from app.services.donors import DonorService
from app.services.sessions import SessionService
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo
from tests.factories.pledge import create_pledge
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user."""
    return create_user(
        db_session,
        email="admin.privacy@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create a supervisor user assigned to a test site."""
    site = create_site(db_session, name="موقع إشراف اختبار الخصوصية")
    return create_user(
        db_session,
        email="supervisor.privacy@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def test_setup(db_session: Session) -> tuple[Campaign, Site]:
    """Create a live campaign with an active participating site."""
    site = create_site(db_session, name="مركز التبرع أ")
    campaign = create_campaign(
        db_session,
        is_published=True,
        status="live",
        title="حملة الخصوصية الميدانية",
    )
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)
    return campaign, site


def create_auth_headers_and_cookies(
    test_client: TestClient,
    db_session: Session,
    user: User,
    *,
    mfa_verified: bool = True,
    fresh_reauth: bool = False,
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

    if fresh_reauth:
        record_session_reauth(session.id, clock.now())
    else:
        clear_session_reauth(session.id)
        session.created_at = clock.now() - timedelta(minutes=15)
        db_session.flush()

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    test_client.cookies.set(settings.cookie_name, raw_token)

    return {"X-CSRF-Token": csrf_token}


# ==============================================================================
# 1. T-PRIV-09: Single-Donor Anonymization on Request (BR-PRIV-09 ★)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-09")
def test_t_priv_09_a_single_donor_anonymize_success(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """T-PRIV-09-a ★: Admin with fresh reauth immediately anonymizes a hero donor.

    - first_name becomes 'محذوف'
    - consent_hero_name becomes False
    - last_name, phone, blood_type become None
    - all consents revoked
    - individual photos deleted and storage keys enqueued in file_deletion_queue
    - audit event 'donor.anonymize' emitted (zero PII)
    """
    campaign, site = test_setup

    # Given: hero dA2 with an individual photo
    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="سالم",
        last_name="البطل",
        phone="+213550123456",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
    )
    photo = create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        kind="individual",
        donor_id=donor.id,
        storage_key=f"photos/donors/{donor.id}/photo1.jpg",
    )
    general_photo = create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        kind="general",
        storage_key=f"photos/campaigns/{campaign.id}/atmosphere.jpg",
    )

    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=True,
    )

    # When: anonymize called from admin_fresh
    resp = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/anonymize",
        headers=headers,
    )

    # Then: HTTP 200 with anonymized payload
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["first_name"] == "محذوف"
    assert data["last_name"] is None
    assert data["phone"] is None
    assert data["blood_type"] is None
    assert data["consent_hero_name"] is False
    assert data["consent_keep_phone"] is False
    assert data["consent_photo"] is False
    assert data["consent_public_phone"] is False

    # Verify DB persistence
    db_session.expire_all()
    db_donor = db_session.get(Donor, donor.id)
    assert db_donor is not None
    assert db_donor.first_name == "محذوف"
    assert db_donor.anonymized_at is not None

    # Verify individual photo row is deleted from photos table
    db_photo = db_session.get(Photo, photo.id)
    assert db_photo is None

    # Verify general atmosphere photo is preserved (BR-PRIV-12)
    db_general = db_session.get(Photo, general_photo.id)
    assert db_general is not None

    # Verify storage key enqueued into file_deletion_queue
    queue_stmt = select(FileDeletionQueue).where(FileDeletionQueue.storage_key == photo.storage_key)
    queue_entry = db_session.scalar(queue_stmt)
    assert queue_entry is not None

    # Verify audit log
    audit_stmt = (
        select(AuditLog)
        .where(
            AuditLog.action == "donor.anonymize",
            AuditLog.entity_id == donor.id,
        )
        .order_by(AuditLog.at.desc())
    )
    audit_entry = db_session.scalar(audit_stmt)
    assert audit_entry is not None
    assert audit_entry.actor_user_id == admin_user.id
    # Zero PII in details
    assert "phone" not in str(audit_entry.details)
    assert "سالم" not in str(audit_entry.details)
    assert "البطل" not in str(audit_entry.details)
    assert "O+" not in str(audit_entry.details)


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-09")
def test_t_priv_09_b_single_donor_anonymize_requires_fresh_reauth(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """T-PRIV-09-b ★: Admin without fresh reauth receives 403 reauth_required."""
    campaign, site = test_setup
    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="كريم",
        last_name="المتبرع",
    )

    # Admin without fresh reauth (> 10 minutes)
    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=False,
    )

    resp = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/anonymize",
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"


@pytest.mark.br("BR-PRIV-09")
def test_single_donor_anonymize_forbidden_for_supervisor_and_anon(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """Supervisor and anonymous visitors cannot anonymize donors."""
    campaign, site = test_setup
    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="فاطمة",
    )

    # Anonymous visitor
    resp_anon = test_client.post(f"/api/v1/admin/donors/{donor.id}/anonymize")
    assert resp_anon.status_code == 401
    assert resp_anon.json()["error"]["code"] == "not_authenticated"

    # Supervisor
    headers_sup = create_auth_headers_and_cookies(
        test_client,
        db_session,
        supervisor_user,
        fresh_reauth=True,
    )
    resp_sup = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/anonymize",
        headers=headers_sup,
    )
    assert resp_sup.status_code == 403
    assert resp_sup.json()["error"]["code"] == "forbidden"


@pytest.mark.br("BR-DONOR-15")
def test_anonymized_donor_is_read_only(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """BR-DONOR-15: Already anonymized donor cannot be re-anonymized or modified (409 campaign_purged)."""
    campaign, site = test_setup
    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="محذوف",
        anonymized_at=datetime.now(UTC),
    )

    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=True,
    )

    # 1. Re-anonymize attempt
    resp1 = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/anonymize",
        headers=headers,
    )
    assert resp1.status_code == 409
    assert resp1.json()["error"]["code"] == "campaign_purged"

    # 2. Update attempt
    resp2 = test_client.patch(
        f"/api/v1/admin/donors/{donor.id}",
        json={"first_name": "محاولة تعديل"},
        headers=headers,
    )
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "campaign_purged"


# ==============================================================================
# 2. T-PRIV-10: Delete Pledge and Phone on Request (BR-PRIV-10)
# ==============================================================================


@pytest.mark.br("BR-PRIV-10")
def test_t_priv_10_a_delete_pledge_on_request_success(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """T-PRIV-10-a: Admin with fresh reauth hard-deletes pledge on request and logs data.deleted_on_request."""
    campaign, site = test_setup
    pledge = create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="طارق",
        phone="+213550999888",
    )

    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=True,
    )

    resp = test_client.delete(
        f"/api/v1/admin/pledges/{pledge.id}",
        headers=headers,
    )
    assert resp.status_code == 204

    # Verify hard deletion from DB
    db_session.expire_all()
    assert db_session.get(Pledge, pledge.id) is None

    # Verify audit log
    audit_stmt = (
        select(AuditLog)
        .where(
            AuditLog.action == "data.deleted_on_request",
            AuditLog.entity_id == pledge.id,
        )
        .order_by(AuditLog.at.desc())
    )
    audit_entry = db_session.scalar(audit_stmt)
    assert audit_entry is not None
    assert audit_entry.actor_user_id == admin_user.id
    # Zero PII in details
    assert "طارق" not in str(audit_entry.details)
    assert "+213550999888" not in str(audit_entry.details)


@pytest.mark.br("BR-PRIV-10")
def test_delete_pledge_requires_fresh_reauth(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """Pledge deletion requires fresh reauthentication (reauth_required)."""
    campaign, site = test_setup
    pledge = create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="نبيل",
    )

    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=False,
    )

    resp = test_client.delete(
        f"/api/v1/admin/pledges/{pledge.id}",
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"


@pytest.mark.br("BR-PRIV-10")
def test_t_priv_10_a_delete_emergency_phone_via_patch_logs_audit(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """T-PRIV-10-a: Deleting emergency phone via PATCH logs data.deleted_on_request audit event."""
    campaign, site = test_setup
    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="يوسف",
        phone="+213550111222",
        blood_type="A+",
        consent_keep_phone=True,
        consent_public_phone=True,
    )

    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=True,
    )

    # Withdraw public phone consent and clear phone
    resp = test_client.patch(
        f"/api/v1/admin/donors/{donor.id}",
        json={
            "phone": None,
            "consent_keep_phone": False,
            "consent_public_phone": False,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["phone"] is None
    assert data["consent_public_phone"] is False

    # Verify data.deleted_on_request audit event
    audit_stmt = (
        select(AuditLog)
        .where(
            AuditLog.action == "data.deleted_on_request",
            AuditLog.entity_id == donor.id,
        )
        .order_by(AuditLog.at.desc())
    )
    audit_entry = db_session.scalar(audit_stmt)
    assert audit_entry is not None
    assert audit_entry.details.get("type") == "emergency_phone"
    assert "+213550111222" not in str(audit_entry.details)


@pytest.mark.br("BR-PRIV-10")
def test_delete_phone_on_request_domain_service(
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """DonorService.delete_phone_on_request clears phone, revokes consents, and logs audit."""
    campaign, site = test_setup
    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="منى",
        phone="+213550333444",
        consent_keep_phone=True,
        consent_public_phone=True,
    )

    donor_repo = DonorRepository(db_session)
    campaign_repo = CampaignRepository(db_session)
    site_repo = SiteRepository(db_session)
    audit_repo = AuditRepository(db_session)
    audit_service = AuditService(audit_repo, clock=get_clock())
    service = DonorService(
        donor_repo=donor_repo,
        campaign_repo=campaign_repo,
        site_repo=site_repo,
        audit_service=audit_service,
        clock=get_clock(),
    )
    updated = service.delete_phone_on_request(admin_user, donor.id)

    assert updated.phone is None
    assert updated.consent_keep_phone is False
    assert updated.consent_public_phone is False

    # Verify audit event
    audit_stmt = (
        select(AuditLog)
        .where(
            AuditLog.action == "data.deleted_on_request",
            AuditLog.entity_id == donor.id,
        )
        .order_by(AuditLog.at.desc())
    )
    audit_entry = db_session.scalar(audit_stmt)
    assert audit_entry is not None
    assert audit_entry.details.get("type") == "phone"
    assert "+213550333444" not in str(audit_entry.details)


# ==============================================================================
# 3. Sentinel Leak Check across Privacy & Anonymization Audit Logs (BR-AUDIT-01 ★)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-AUDIT-01")
def test_audit_log_sentinel_leak_sweep(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site],
) -> None:
    """T-AUDIT-01-a ★: Sweep audit logs for sentinel canary values (names, phones, blood types)."""
    campaign, site = test_setup

    canary_first = "ZZCANARYFIRST"
    canary_last = "ZZCANARYLAST"
    canary_phone = "+213550999111"
    canary_blood = "AB-"

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name=canary_first,
        last_name=canary_last,
        phone=canary_phone,
        blood_type=canary_blood,
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
    )
    create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        kind="individual",
        donor_id=donor.id,
        storage_key=f"photos/donors/{donor.id}/canary.jpg",
    )
    pledge = create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name=canary_first,
        phone=canary_phone,
    )

    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=True,
    )

    # 1. Anonymize donor
    resp_anon = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/anonymize",
        headers=headers,
    )
    assert resp_anon.status_code == 200

    # 2. Delete pledge
    resp_pledge = test_client.delete(
        f"/api/v1/admin/pledges/{pledge.id}",
        headers=headers,
    )
    assert resp_pledge.status_code == 204

    # 3. Sweep all audit entries created in this test
    entries = db_session.scalars(select(AuditLog)).all()
    for entry in entries:
        details_str = str(entry.details or {})
        assert canary_first not in details_str, f"Sentinel name leaked in audit entry: {entry.id}"
        assert canary_last not in details_str, f"Sentinel name leaked in audit entry: {entry.id}"
        assert canary_phone not in details_str, f"Sentinel phone leaked in audit entry: {entry.id}"
        assert canary_blood not in details_str, (
            f"Sentinel blood type leaked in audit entry: {entry.id}"
        )
