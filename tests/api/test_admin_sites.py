"""Integration tests for Admin Sites and Public Sites API endpoints (P4.1).

Per:
- docs/04-api.md §14 (Admin Sites API: GET, POST, PATCH, DELETE)
- docs/04-api.md §6.9 (Public Sites API: GET /public/sites)
- docs/06-business-rules.md §4 (BR-SITE-01..04)
- docs/07-test-cases.md §5.3 (T-SITE-02-a, T-SITE-03-a, T-SITE-03-b, T-SITE-04-a)
- docs/03-auth-permissions.md §11 (SITE_MANAGE, SITE_READ, step-up R)
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_settings,
    record_session_reauth,
)
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user."""
    return create_user(
        db_session,
        email="admin.sites@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create a supervisor user assigned to a test site."""
    site = create_site(db_session, name="موقع المشرف التجريبي")
    return create_user(
        db_session,
        email="supervisor.sites@bloodday.local",
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
        # Set created_at to 15 minutes ago to ensure step-up is expired
        session.created_at = clock.now() - timedelta(minutes=15)
        db_session.flush()

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    test_client.cookies.set(settings.cookie_name, raw_token)

    return {"X-CSRF-Token": csrf_token}


# ==============================================================================
# 1. Authorization & Role Matrix
# ==============================================================================


@pytest.mark.api
def test_admin_sites_requires_authentication(test_client: TestClient) -> None:
    """Anonymous visitors receive 401 on /admin/sites."""
    resp = test_client.get("/api/v1/admin/sites")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.api
def test_admin_sites_forbidden_for_supervisor(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """Supervisors cannot manage sites (403 forbidden)."""
    headers = create_auth_headers_and_cookies(test_client, db_session, supervisor_user)
    resp = test_client.get("/api/v1/admin/sites", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.api
def test_admin_sites_requires_mfa_for_admin(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Admin with interim session (no MFA) receives 403 mfa_required."""
    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        mfa_verified=False,
    )
    resp = test_client.get("/api/v1/admin/sites", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "mfa_required"


# ==============================================================================
# 2. Site Creation & Input Validation
# ==============================================================================


@pytest.mark.api
def test_create_site_success_and_normalization(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Admin can create a new site; whitespace in name is normalized (BR-GEN-02)."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    payload = {
        "name": "   نادي   ابن رشد    الطبي   ",
        "address": "كلية الطب، جامعة الجزائر 1",
        "map_url": "https://maps.google.com/?q=36.75,3.05",
        "latitude": 36.752500,
        "longitude": 3.041970,
        "description": "موقع مجهز بكامل الوسائل",
    }

    resp = test_client.post("/api/v1/admin/sites", json=payload, headers=headers)
    assert resp.status_code == 201
    data = resp.json()

    assert data["name"] == "نادي ابن رشد الطبي"
    assert data["address"] == "كلية الطب، جامعة الجزائر 1"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


@pytest.mark.api
def test_create_site_rejects_unknown_fields(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Unknown or forbidden fields are strictly rejected with 422 unknown_field (BR-GEN-06)."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    payload = {
        "name": "نادي صالح",
        "address": "العاصمة",
        "unexpected_extra_field": "injected_value",
    }

    resp = test_client.post("/api/v1/admin/sites", json=payload, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_field"


@pytest.mark.api
def test_create_site_validates_coordinates(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Latitude outside -90..90 or longitude outside -180..180 rejected with 422 validation_error."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Invalid latitude
    resp = test_client.post(
        "/api/v1/admin/sites",
        json={"name": "موقع تجريبي", "address": "عنوان", "latitude": 95.0},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"

    # Invalid longitude
    resp = test_client.post(
        "/api/v1/admin/sites",
        json={"name": "موقع تجريبي", "address": "عنوان", "longitude": 200.0},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# ==============================================================================
# 3. Retrieval & Listing
# ==============================================================================


@pytest.mark.api
def test_get_site_by_id_and_not_found(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Existing site is returned; non-existent ID yields 404 not_found."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)
    site = create_site(db_session, name="موقع الفحص الخاص")

    # Found
    resp = test_client.get(f"/api/v1/admin/sites/{site.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == str(site.id)
    assert resp.json()["name"] == "موقع الفحص الخاص"

    # Not found
    random_id = uuid.uuid4()
    resp_404 = test_client.get(f"/api/v1/admin/sites/{random_id}", headers=headers)
    assert resp_404.status_code == 404
    assert resp_404.json()["error"]["code"] == "not_found"


# ==============================================================================
# 4. Updates & Deactivation (BR-SITE-02 / T-SITE-02-a)
# ==============================================================================


@pytest.mark.api
def test_patch_site_and_t_site_02_a_deactivation_hides_from_public(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-SITE-02-a: Deactivating site hides it from GET /public/sites but preserves it in admin."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    site_active = create_site(db_session, name="الموقع النشط أ", is_active=True)
    site_to_deactivate = create_site(db_session, name="الموقع المعطل ز", is_active=True)

    # Deactivate site_to_deactivate via PATCH
    patch_resp = test_client.patch(
        f"/api/v1/admin/sites/{site_to_deactivate.id}",
        json={"is_active": False, "description": "تم إغلاق الموقع مؤقتاً"},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_active"] is False
    assert patch_resp.json()["description"] == "تم إغلاق الموقع مؤقتاً"

    # Admin list includes both active and inactive sites
    admin_list = test_client.get("/api/v1/admin/sites", headers=headers).json()
    admin_site_ids = {s["id"] for s in admin_list}
    assert str(site_active.id) in admin_site_ids
    assert str(site_to_deactivate.id) in admin_site_ids

    # Public list (/public/sites) EXCLUDES deactivated site Z per T-SITE-02-a
    # Public endpoint is accessible anonymously
    test_client.cookies.clear()
    public_resp = test_client.get("/api/v1/public/sites")
    assert public_resp.status_code == 200
    public_sites = public_resp.json()
    public_site_ids = {s["id"] for s in public_sites}

    assert str(site_active.id) in public_site_ids
    assert str(site_to_deactivate.id) not in public_site_ids


# ==============================================================================
# 5. Conditional Deletion (BR-SITE-03 / T-SITE-03-a / T-SITE-03-b)
# ==============================================================================


@pytest.mark.api
def test_t_site_03_a_delete_site_in_use_rejected(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-SITE-03-a: Site used in a campaign rejects deletion with 409 in_use (BR-SITE-03)."""
    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=True,
    )

    campaign = create_campaign(db_session, title="حملة الربيع")
    site = create_site(db_session, name="موقع مشارك بالحملة")
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)

    resp = test_client.delete(f"/api/v1/admin/sites/{site.id}", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "in_use"


@pytest.mark.api
def test_t_site_03_b_delete_unused_site_succeeds(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-SITE-03-b: Brand new unused site is hard deleted successfully with 204."""
    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=True,
    )
    unused_site = create_site(db_session, name="موقع جديد غير مستعمل إطلاقاً")

    resp = test_client.delete(f"/api/v1/admin/sites/{unused_site.id}", headers=headers)
    assert resp.status_code == 204

    # Verify site is gone
    check_resp = test_client.get(f"/api/v1/admin/sites/{unused_site.id}", headers=headers)
    assert check_resp.status_code == 404


@pytest.mark.api
def test_delete_site_requires_recent_reauthentication_r(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """DELETE /admin/sites/{id} requires step-up confirmation (R); expired reauth yields 403."""
    # Auth without fresh reauth (< 10 minutes)
    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=False,
    )
    site = create_site(db_session, name="موقع تجربة التأكيد الحديث")

    resp = test_client.delete(f"/api/v1/admin/sites/{site.id}", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"


# ==============================================================================
# 6. Independence of Supervisors (BR-SITE-04 / T-SITE-04-a)
# ==============================================================================


@pytest.mark.api
def test_t_site_04_a_deactivating_site_leaves_supervisors_active(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-SITE-04-a: Deactivating Site A leaves its assigned supervisors active (BR-SITE-04)."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    site_a = create_site(db_session, name="موقع أ المشرفين")
    supervisor = create_user(
        db_session,
        email="supervisor.site_a@bloodday.local",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
    )

    # Deactivate site A
    resp = test_client.patch(
        f"/api/v1/admin/sites/{site_a.id}",
        json={"is_active": False},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Check supervisor in database
    db_session.refresh(supervisor)
    assert supervisor.is_active is True
