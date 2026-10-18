"""Integration tests for Admin Donor Relocation across participating sites (P8.5).

Per:
- docs/04-api.md §17 (POST /admin/donors/{id}/relocate)
- docs/06-business-rules.md §6 (BR-DONOR-12 ★: Relocate donor and individual photos atomically)
- docs/06-business-rules.md §6 (BR-DONOR-15: Anonymized records cannot be relocated)
- docs/06-business-rules.md §7 (BR-PHOTO-11: Individual photos move with relocated donor)
- docs/06-business-rules.md §13 (BR-AUDIT-01 ★, BR-AUDIT-05: Zero PII in audit details)
- docs/07-test-cases.md §5.5 (T-DONOR-12-a ★, T-DONOR-12-b ★, T-DONOR-12-c ★, T-DONOR-15-a)
"""

import uuid
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
from app.models.photo import Photo
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user."""
    return create_user(
        db_session,
        email="admin.relocate@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session, test_setup: tuple[Campaign, Site, Site, Site]) -> User:
    """Create a supervisor user assigned to site A."""
    _, site_a, _, _ = test_setup
    return create_user(
        db_session,
        email="supervisor.relocate@bloodday.local",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
    )


@pytest.fixture
def test_setup(db_session: Session) -> tuple[Campaign, Site, Site, Site]:
    """Create a live campaign with two participating sites (A and B) and one non-participating site (C)."""
    campaign = create_campaign(
        db_session,
        is_published=True,
        status="live",
        title="حملة نوفمبر الميدانية",
    )
    site_a = create_site(db_session, name="نادي الطب الجزائر أ")
    site_b = create_site(db_session, name="نادي الطب وهران ب")
    site_c = create_site(db_session, name="موقع غير مشارك ج")

    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site_b.id)
    # site_c is purposefully NOT added to campaign_sites

    return campaign, site_a, site_b, site_c


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
# 1. T-DONOR-12-a ★: Relocate Donor and Photos Success
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-DONOR-12")
@pytest.mark.br("BR-PHOTO-11")
def test_t_donor_12_a_relocate_donor_and_photos_success(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site, Site, Site],
) -> None:
    """T-DONOR-12-a ★: Admin with fresh reauth relocates donor to participating site B.

    Verifies:
    - Donor site_id changes from site_a to site_b.
    - Individual donor photo site_id changes to site_b.
    - Atmosphere photo at site_a remains at site_a.
    - Another donor's photo at site_a remains at site_a.
    - donor.relocate audit log created with zero PII.
    """
    campaign, site_a, site_b, _ = test_setup

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="سفيان",
        last_name="المتبرع",
        phone="0550123456",
        blood_type="O+",
        status="donated",
        consent_photo=True,
    )
    # Individual photo for this donor
    donor_photo = create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        kind="individual",
        donor_id=donor.id,
        storage_key=f"photos/{donor.id}_face.jpg",
    )
    # Atmosphere photo at site A (donor_id is None)
    atmosphere_photo = create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        donor_id=None,
        storage_key=f"photos/atmosphere_{site_a.id}.jpg",
    )
    # Another donor and their photo at site A
    other_donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="كريم",
        last_name="الآخر",
        phone="0550999888",
        status="attended",
        consent_photo=True,
    )
    other_photo = create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        kind="individual",
        donor_id=other_donor.id,
        storage_key=f"photos/{other_donor.id}_face.jpg",
    )

    headers = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=True, fresh_reauth=True
    )

    response = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers,
        json={"site_id": str(site_b.id)},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["id"] == str(donor.id)
    assert data["site_id"] == str(site_b.id)
    assert data["campaign_id"] == str(campaign.id)

    db_session.expire_all()

    # Verify donor moved in DB
    refreshed_donor = db_session.get(Donor, donor.id)
    assert refreshed_donor is not None
    assert refreshed_donor.site_id == site_b.id

    # Verify individual photo moved to site_b in DB
    refreshed_photo = db_session.get(Photo, donor_photo.id)
    assert refreshed_photo is not None
    assert refreshed_photo.site_id == site_b.id

    # Verify atmosphere photo remained at site_a
    refreshed_atmosphere = db_session.get(Photo, atmosphere_photo.id)
    assert refreshed_atmosphere is not None
    assert refreshed_atmosphere.site_id == site_a.id

    # Verify other donor's photo remained at site_a
    refreshed_other_photo = db_session.get(Photo, other_photo.id)
    assert refreshed_other_photo is not None
    assert refreshed_other_photo.site_id == site_a.id

    # Verify zero-PII audit event
    audit_event = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.entity_id == donor.id,
            AuditLog.action == "donor.relocate",
        )
        .order_by(AuditLog.at.desc())
    )
    assert audit_event is not None
    assert audit_event.actor_user_id == admin_user.id
    assert audit_event.details["old_site_id"] == str(site_a.id)
    assert audit_event.details["new_site_id"] == str(site_b.id)
    assert audit_event.details["campaign_id"] == str(campaign.id)
    assert audit_event.details["photos_relocated"] == 1

    # Ensure no PII in audit details
    details_str = str(audit_event.details)
    assert "سفيان" not in details_str
    assert "المتبرع" not in details_str
    assert "0550123456" not in details_str
    assert "O+" not in details_str


# ==============================================================================
# 2. T-DONOR-12-b ★: Relocate to Non-Participating Site Fails with 409
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-DONOR-12")
def test_t_donor_12_b_relocate_non_participating_site_409(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site, Site, Site],
) -> None:
    """T-DONOR-12-b ★: Target site not in campaign returns 409 site_not_in_campaign, no changes."""
    campaign, site_a, _, site_c = test_setup

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="نادية",
        status="donated",
        consent_photo=True,
    )
    donor_photo = create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        kind="individual",
        donor_id=donor.id,
        storage_key=f"photos/{donor.id}_face.jpg",
    )

    headers = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=True, fresh_reauth=True
    )

    response = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers,
        json={"site_id": str(site_c.id)},
    )

    assert response.status_code == 409
    err = response.json()
    assert err["error"]["code"] == "site_not_in_campaign"

    db_session.expire_all()

    # Donor and photo must remain at site_a
    refreshed_donor = db_session.get(Donor, donor.id)
    assert refreshed_donor is not None
    assert refreshed_donor.site_id == site_a.id

    refreshed_photo = db_session.get(Photo, donor_photo.id)
    assert refreshed_photo is not None
    assert refreshed_photo.site_id == site_a.id

    # No audit log created
    audit_count = db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_id == donor.id,
            AuditLog.action == "donor.relocate",
        )
    )
    assert audit_count is None


# ==============================================================================
# 3. T-DONOR-12-c ★: Auth and Permission Guards
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-DONOR-12")
def test_t_donor_12_c_relocate_auth_guards(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
    test_setup: tuple[Campaign, Site, Site, Site],
) -> None:
    """T-DONOR-12-c ★: Enforces 401 anon, 403 supervisor, 403 admin no MFA, 403 admin no fresh reauth."""
    campaign, site_a, site_b, _ = test_setup

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="رشيد",
        status="attended",
    )

    # 1. Anonymous visitor -> 401 not_authenticated
    test_client.cookies.clear()
    res_anon = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        json={"site_id": str(site_b.id)},
    )
    assert res_anon.status_code == 401
    assert res_anon.json()["error"]["code"] == "not_authenticated"

    # 2. Supervisor -> 403 forbidden
    headers_sup = create_auth_headers_and_cookies(
        test_client, db_session, supervisor_user, mfa_verified=False
    )
    res_sup = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers_sup,
        json={"site_id": str(site_b.id)},
    )
    assert res_sup.status_code == 403
    assert res_sup.json()["error"]["code"] == "forbidden"

    # 3. Admin without MFA -> 403 mfa_required
    headers_no_mfa = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=False, fresh_reauth=False
    )
    res_nomfa = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers_no_mfa,
        json={"site_id": str(site_b.id)},
    )
    assert res_nomfa.status_code == 403
    assert res_nomfa.json()["error"]["code"] == "mfa_required"

    # 4. Admin with MFA but WITHOUT fresh reauth (Condition R) -> 403 reauth_required
    headers_no_reauth = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=True, fresh_reauth=False
    )
    res_noreauth = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers_no_reauth,
        json={"site_id": str(site_b.id)},
    )
    assert res_noreauth.status_code == 403
    assert res_noreauth.json()["error"]["code"] == "reauth_required"


# ==============================================================================
# 4. T-DONOR-15-a: Anonymized Donor Relocate Rejected with 409
# ==============================================================================


@pytest.mark.br("BR-DONOR-15")
def test_t_donor_15_a_anonymized_donor_relocate_rejected(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site, Site, Site],
) -> None:
    """T-DONOR-15-a: Relocating an anonymized donor returns 409 campaign_purged."""
    campaign, site_a, site_b, _ = test_setup

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="محذوف",
        anonymized_at=datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC),
    )

    headers = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=True, fresh_reauth=True
    )

    response = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers,
        json={"site_id": str(site_b.id)},
    )

    assert response.status_code == 409
    err = response.json()
    assert err["error"]["code"] == "campaign_purged"

    db_session.expire_all()
    refreshed_donor = db_session.get(Donor, donor.id)
    assert refreshed_donor is not None
    assert refreshed_donor.site_id == site_a.id


# ==============================================================================
# 5. Idempotent Relocation to Current Site
# ==============================================================================


@pytest.mark.br("BR-DONOR-12")
def test_relocate_same_site_is_idempotent(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site, Site, Site],
) -> None:
    """Relocating a donor to their current site returns 200 without DB mutations or audit logs."""
    campaign, site_a, _, _ = test_setup

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="جمال",
        status="donated",
    )

    headers = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=True, fresh_reauth=True
    )

    response = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers,
        json={"site_id": str(site_a.id)},
    )

    assert response.status_code == 200
    assert response.json()["site_id"] == str(site_a.id)

    # Ensure no audit log is created for no-op relocation
    audit_count = db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_id == donor.id,
            AuditLog.action == "donor.relocate",
        )
    )
    assert audit_count is None


# ==============================================================================
# 6. Non-existent Donor Returns 404
# ==============================================================================


def test_relocate_nonexistent_donor_404(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site, Site, Site],
) -> None:
    """Relocating a nonexistent donor ID returns 404 not_found."""
    _, _, site_b, _ = test_setup
    nonexistent_id = uuid.uuid4()

    headers = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=True, fresh_reauth=True
    )

    response = test_client.post(
        f"/api/v1/admin/donors/{nonexistent_id}/relocate",
        headers=headers,
        json={"site_id": str(site_b.id)},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ==============================================================================
# 7. Extra Fields Forbidden (BR-GEN-07)
# ==============================================================================


@pytest.mark.br("BR-GEN-07")
def test_relocate_extra_fields_forbidden(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site, Site, Site],
) -> None:
    """Payload with extra fields returns 422 extra_forbidden."""
    campaign, site_a, site_b, _ = test_setup

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="منير",
    )

    headers = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=True, fresh_reauth=True
    )

    response = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers,
        json={
            "site_id": str(site_b.id),
            "campaign_id": str(campaign.id),  # Disallowed extra field
        },
    )

    assert response.status_code == 422


# ==============================================================================
# 8. Sentinel Zero-PII Leak Detection
# ==============================================================================


@pytest.mark.security
@pytest.mark.br("BR-AUDIT-05")
def test_relocate_sentinel_zero_pii_leak_detection(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    test_setup: tuple[Campaign, Site, Site, Site],
) -> None:
    """Verify that sensitive sentinel PII never leaks into audit log during relocation."""
    campaign, site_a, site_b, _ = test_setup

    sentinel_first = "ZZRELOCFIRST"
    sentinel_last = "ZZRELOCLAST"
    sentinel_phone = "0550123999"

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name=sentinel_first,
        last_name=sentinel_last,
        phone=sentinel_phone,
        blood_type="AB+",
        status="donated",
    )

    headers = create_auth_headers_and_cookies(
        test_client, db_session, admin_user, mfa_verified=True, fresh_reauth=True
    )

    response = test_client.post(
        f"/api/v1/admin/donors/{donor.id}/relocate",
        headers=headers,
        json={"site_id": str(site_b.id)},
    )
    assert response.status_code == 200

    audit_logs = db_session.scalars(select(AuditLog).where(AuditLog.entity_id == donor.id)).all()

    for log in audit_logs:
        details_str = str(log.details)
        assert sentinel_first not in details_str, f"Sentinel first name leaked into audit: {log.id}"
        assert sentinel_last not in details_str, f"Sentinel last name leaked into audit: {log.id}"
        assert sentinel_phone not in details_str, f"Sentinel phone leaked into audit: {log.id}"
        assert "AB+" not in details_str, f"Blood type leaked into audit: {log.id}"
