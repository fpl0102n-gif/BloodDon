"""Integration tests for Campaign Sites API endpoints (P4.3).

Per:
- docs/04-api.md §13.1 (Admin Campaign Sites: GET, PUT, DELETE)
- docs/04-api.md §6.4 (Public Campaign Sites: GET /public/campaigns/{slug}/sites)
- docs/06-business-rules.md §4 (BR-CSITE-01..06)
- docs/07-test-cases.md §5.3 (T-CSITE-01, T-CSITE-02-a, T-CSITE-02-b, T-CSITE-03-a, T-CSITE-04-a, T-CSITE-04-b, T-CSITE-04-c)
- docs/03-auth-permissions.md §11 (CAMPAIGN_SITE_MANAGE, CAMPAIGN_SITE_READ)
"""

from datetime import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_settings,
)
from app.models.campaign import CampaignSite
from app.models.pledge import Pledge
from app.models.user import User
from app.repositories.sessions import SessionRepository
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
        email="admin.csites@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create a supervisor user assigned to a test site."""
    site = create_site(db_session, name="موقع المشرف للمواقع")
    return create_user(
        db_session,
        email="supervisor.csites@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def client(test_client: TestClient) -> TestClient:
    """Convenience fixture mapping to test_client."""
    return test_client


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


# ==============================================================================
# 1. Authorization & Role Access
# ==============================================================================


@pytest.mark.br("BR-CSITE-01")
def test_campaign_site_admin_endpoints_require_admin_with_mfa(
    client: TestClient,
    db_session: Session,
    supervisor_user: User,
    admin_user: User,
) -> None:
    """Non-admin or unauthenticated roles cannot access admin campaign site endpoints."""
    campaign = create_campaign(db_session, slug="c-auth-test")
    site = create_site(db_session, name="موقع الاختبار الأمني")
    cid = str(campaign.id)
    sid = str(site.id)

    # 1. Anonymous access -> 401 unauthenticated
    client.cookies.clear()
    resp_get = client.get(f"/api/v1/admin/campaigns/{cid}/sites")
    assert resp_get.status_code == 401
    assert resp_get.json()["error"]["code"] == "not_authenticated"

    resp_put = client.put(f"/api/v1/admin/campaigns/{cid}/sites/{sid}", json={})
    assert resp_put.status_code == 401

    resp_del = client.delete(f"/api/v1/admin/campaigns/{cid}/sites/{sid}")
    assert resp_del.status_code == 401

    # 2. Supervisor access -> 403 forbidden
    headers = create_auth_headers_and_cookies(client, db_session, supervisor_user)
    resp_sup_get = client.get(f"/api/v1/admin/campaigns/{cid}/sites", headers=headers)
    assert resp_sup_get.status_code == 403
    assert resp_sup_get.json()["error"]["code"] == "forbidden"

    # 3. Admin without MFA -> 403 forbidden
    headers_no_mfa = create_auth_headers_and_cookies(
        client, db_session, admin_user, mfa_verified=False
    )
    resp_no_mfa = client.get(f"/api/v1/admin/campaigns/{cid}/sites", headers=headers_no_mfa)
    assert resp_no_mfa.status_code == 403
    assert resp_no_mfa.json()["error"]["code"] == "mfa_required"


# ==============================================================================
# 2. Admin Campaign Sites Management (GET, PUT, DELETE)
# ==============================================================================


@pytest.mark.br("BR-CSITE-01")
def test_admin_list_campaign_sites(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """GET /admin/campaigns/{id}/sites returns all participating sites."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    campaign = create_campaign(db_session, slug="c-list-sites")
    site1 = create_site(db_session, name="موقع أ")
    site2 = create_site(db_session, name="موقع ب")

    create_campaign_site(
        db_session,
        campaign_id=campaign.id,
        site_id=site1.id,
        start_time=time(8, 0),
        end_time=time(16, 0),
        supervisors_text="د. زيد",
        notes="ملاحظة 1",
    )
    create_campaign_site(
        db_session,
        campaign_id=campaign.id,
        site_id=site2.id,
        start_time=time(9, 30),
        end_time=time(17, 30),
        supervisors_text="د. عمر",
        notes="ملاحظة 2",
    )

    resp = client.get(f"/api/v1/admin/campaigns/{campaign.id}/sites", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    site_names = [item["site_name"] for item in data]
    assert "موقع أ" in site_names
    assert "موقع ب" in site_names


@pytest.mark.br("BR-CSITE-02")
def test_t_csite_02_a_cannot_add_inactive_site(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CSITE-02-a: Attempt to add inactive site rejects with 422 validation_error."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    campaign = create_campaign(db_session, slug="c-inactive-site")
    site = create_site(db_session, name="موقع غير نشط", is_active=False)

    payload = {
        "start_time": "09:00:00",
        "end_time": "17:00:00",
    }
    resp = client.put(
        f"/api/v1/admin/campaigns/{campaign.id}/sites/{site.id}",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.br("BR-CSITE-03")
def test_t_csite_03_a_time_order_validation(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CSITE-03-a: Operating end_time must be strictly greater than start_time."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    campaign = create_campaign(db_session, slug="c-time-order")
    site = create_site(db_session, name="موقع فحص التوقيت", is_active=True)

    # 1. end_time before start_time
    payload_inverted = {
        "start_time": "17:00:00",
        "end_time": "09:00:00",
    }
    resp1 = client.put(
        f"/api/v1/admin/campaigns/{campaign.id}/sites/{site.id}",
        json=payload_inverted,
        headers=headers,
    )
    assert resp1.status_code == 422
    assert resp1.json()["error"]["code"] == "validation_error"

    # 2. end_time equal to start_time
    payload_equal = {
        "start_time": "10:00:00",
        "end_time": "10:00:00",
    }
    resp2 = client.put(
        f"/api/v1/admin/campaigns/{campaign.id}/sites/{site.id}",
        json=payload_equal,
        headers=headers,
    )
    assert resp2.status_code == 422
    assert resp2.json()["error"]["code"] == "validation_error"


@pytest.mark.br("BR-CSITE-02")
@pytest.mark.br("BR-CSITE-05")
def test_admin_add_and_update_campaign_site_success(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Add new site participation and then update it successfully."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    campaign = create_campaign(db_session, slug="c-add-update-site")
    site = create_site(db_session, name="موقع كلية الطب", is_active=True)

    # 1. Add participation (PUT creates if not existing)
    payload_add = {
        "start_time": "08:30:00",
        "end_time": "16:00:00",
        "supervisors_text": "د. خالد و د. مريم",
        "notes": "المدخل الشمالي",
    }
    resp_add = client.put(
        f"/api/v1/admin/campaigns/{campaign.id}/sites/{site.id}",
        json=payload_add,
        headers=headers,
    )
    assert resp_add.status_code == 200
    data_add = resp_add.json()
    assert data_add["campaign_id"] == str(campaign.id)
    assert data_add["site_id"] == str(site.id)
    assert data_add["site_name"] == "موقع كلية الطب"
    assert data_add["supervisors_text"] == "د. خالد و د. مريم"
    assert data_add["notes"] == "المدخل الشمالي"

    # 2. Update participation (PUT modifies existing)
    payload_update = {
        "start_time": "09:00:00",
        "end_time": "17:00:00",
        "supervisors_text": "د. خالد فقط",
        "notes": "تم تغيير المدخل إلى الجنوبي",
    }
    resp_update = client.put(
        f"/api/v1/admin/campaigns/{campaign.id}/sites/{site.id}",
        json=payload_update,
        headers=headers,
    )
    assert resp_update.status_code == 200
    data_update = resp_update.json()
    assert data_update["supervisors_text"] == "د. خالد فقط"
    assert data_update["notes"] == "تم تغيير المدخل إلى الجنوبي"


@pytest.mark.br("BR-CSITE-04")
def test_t_csite_04_a_cannot_delete_site_with_donors(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CSITE-04-a: Deletion rejected with 409 in_use when donors exist (★)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    campaign = create_campaign(db_session, slug="c-del-donors")
    site = create_site(db_session, name="موقع متبرعين")
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        registered_by=admin_user.id,
    )

    resp = client.delete(
        f"/api/v1/admin/campaigns/{campaign.id}/sites/{site.id}",
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "in_use"


@pytest.mark.br("BR-CSITE-04")
def test_t_csite_04_b_cannot_delete_site_with_photos(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CSITE-04-b: Deletion rejected with 409 in_use when photos exist (★)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    campaign = create_campaign(db_session, slug="c-del-photos")
    site = create_site(db_session, name="موقع صور")
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)
    create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        uploaded_by=admin_user.id,
    )

    resp = client.delete(
        f"/api/v1/admin/campaigns/{campaign.id}/sites/{site.id}",
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "in_use"


@pytest.mark.br("BR-CSITE-04")
def test_t_csite_04_c_delete_site_with_only_pledges_cascades(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CSITE-04-c: Deletion of site with only pledges succeeds (204) and removes pledges."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    campaign = create_campaign(db_session, slug="c-del-pledges")
    site = create_site(db_session, name="موقع تعهدات فقط")
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)

    # Create pledges associated with this campaign-site
    p1 = create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="محمد",
        phone="+966550000001",
    )
    p2 = create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="أحمد",
        phone="+966550000002",
    )

    resp = client.delete(
        f"/api/v1/admin/campaigns/{campaign.id}/sites/{site.id}",
        headers=headers,
    )
    assert resp.status_code == 204

    # Verify campaign_site record is deleted
    cs_check = (
        db_session.query(CampaignSite).filter_by(campaign_id=campaign.id, site_id=site.id).first()
    )
    assert cs_check is None

    # Verify pledges were deleted per BR-CSITE-04
    pledge1_check = db_session.query(Pledge).filter_by(id=p1.id).first()
    pledge2_check = db_session.query(Pledge).filter_by(id=p2.id).first()
    assert pledge1_check is None
    assert pledge2_check is None


# ==============================================================================
# 3. Public Campaign Sites Endpoint (docs/04 §6.4)
# ==============================================================================


@pytest.mark.br("BR-CAMP-03")
def test_public_campaign_sites_unpublished_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    """Unpublished campaign returns 404 for public sites list (BR-CAMP-03)."""
    campaign = create_campaign(
        db_session,
        slug="c-unpub-sites",
        is_published=False,
    )
    site = create_site(db_session, name="موقع سري")
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)

    client.cookies.clear()
    resp = client.get(f"/api/v1/public/campaigns/{campaign.slug}/sites")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.br("BR-SITE-02")
@pytest.mark.br("BR-CSITE-05")
def test_public_campaign_sites_published_success(
    client: TestClient,
    db_session: Session,
) -> None:
    """Published campaign lists active participating sites with pledges count (BR-SITE-02)."""
    campaign = create_campaign(
        db_session,
        slug="c-pub-sites",
        is_published=True,
    )
    active_site = create_site(
        db_session,
        name="موقع عام نشط",
        is_active=True,
        description="وصف الموقع العام",
        address="شارع الملك فهد",
    )
    inactive_site = create_site(
        db_session,
        name="موقع عام معطل",
        is_active=False,
    )

    create_campaign_site(
        db_session,
        campaign_id=campaign.id,
        site_id=active_site.id,
        start_time=time(9, 0),
        end_time=time(17, 0),
        supervisors_text="د. إبراهيم",
    )
    create_campaign_site(
        db_session,
        campaign_id=campaign.id,
        site_id=inactive_site.id,
        start_time=time(9, 0),
        end_time=time(17, 0),
    )

    # Add 2 pledges to the active site
    create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=active_site.id,
        first_name="سعيد",
        phone="+966551111111",
    )
    create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=active_site.id,
        first_name="عبدالله",
        phone="+966552222222",
    )

    client.cookies.clear()
    resp = client.get(f"/api/v1/public/campaigns/{campaign.slug}/sites")
    assert resp.status_code == 200
    data = resp.json()

    # Only active site should be returned (BR-SITE-02)
    assert len(data) == 1
    item = data[0]
    assert item["site_id"] == str(active_site.id)
    assert item["name"] == "موقع عام نشط"
    assert item["address"] == "شارع الملك فهد"
    assert item["description"] == "وصف الموقع العام"
    assert item["supervisors_text"] == "د. إبراهيم"
    assert item["start_time"] == "09:00:00"
    assert item["end_time"] == "17:00:00"
    assert item["pledges_count"] == 2
