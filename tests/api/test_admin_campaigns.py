"""Integration tests for Admin Campaigns and Public Campaigns API endpoints (P4.2).

Per:
- docs/04-api.md §13 (Admin Campaigns: GET, POST, PATCH, DELETE)
- docs/04-api.md §6 (Public Campaigns: GET /public/campaigns, /current, /{slug})
- docs/06-business-rules.md §3 (BR-CAMP-01, 02, 03, 12, 13, 14, 15)
- docs/07-test-cases.md §5.2 (T-CAMP-01-a, T-CAMP-02, T-CAMP-03, T-CAMP-12, T-CAMP-13, T-CAMP-14, T-CAMP-15)
- docs/03-auth-permissions.md §11 (CAMPAIGN_MANAGE, CAMPAIGN_READ, step-up R)
"""

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
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo
from tests.factories.pledge import create_pledge
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user."""
    return create_user(
        db_session,
        email="admin.campaigns@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create a supervisor user assigned to a test site."""
    site = create_site(db_session, name="موقع المشرف للحملات")
    return create_user(
        db_session,
        email="supervisor.campaigns@bloodday.local",
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
# 1. Authorization & Role Access
# ==============================================================================


@pytest.mark.br("BR-CAMP-01")
def test_campaign_endpoints_require_admin_role(
    client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """Non-admin roles cannot create, edit, or delete campaigns (403 forbidden)."""
    # 1. Anonymous access
    client.cookies.clear()
    res_anon = client.get("/api/v1/admin/campaigns")
    assert res_anon.status_code == 401
    assert res_anon.json()["error"]["code"] == "not_authenticated"

    # 2. Supervisor access
    headers = create_auth_headers_and_cookies(client, db_session, supervisor_user)
    res_sup = client.get("/api/v1/admin/campaigns", headers=headers)
    assert res_sup.status_code == 403
    assert res_sup.json()["error"]["code"] == "forbidden"


# ==============================================================================
# 2. Campaign Creation & Validation (T-CAMP-01-a, T-CAMP-02, T-CAMP-15-a)
# ==============================================================================


@pytest.mark.br("BR-CAMP-01")
def test_t_camp_01_a_admin_creates_campaign(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-01-a: Admin creates campaign -> upcoming, unpublished, not current (BR-CAMP-01)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    payload = {
        "title": "حملة خريف 2026",
        "slug": "autumn-blood-drive-2026",
        "campaign_date": "2026-10-25",
        "summary": "حملة الخريف للتبرع بالدم",
        "content": "# تفاصيل الحملة\n\nنرحب بجميع المتبرعين.",
        "cover_url": "https://storage.bloodday.local/covers/autumn.jpg",
    }

    res = client.post("/api/v1/admin/campaigns", json=payload, headers=headers)
    assert res.status_code == 201

    data = res.json()
    assert data["title"] == "حملة خريف 2026"
    assert data["slug"] == "autumn-blood-drive-2026"
    assert data["status"] == "upcoming"
    assert data["is_published"] is False
    assert data["is_current"] is False
    assert data["campaign_date"] == "2026-10-25"
    assert "id" in data


@pytest.mark.br("BR-CAMP-02")
def test_t_camp_02_a_invalid_slug_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-02-a: Slug with invalid format rejected with 422 validation_error (BR-CAMP-02)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    invalid_slugs = ["Ab", "a b", "عربي", "a--b", "-a", "a-", ""]

    for invalid_slug in invalid_slugs:
        payload = {
            "title": "حملة بتنسيق خاطئ",
            "slug": invalid_slug,
            "campaign_date": "2026-10-25",
        }
        res = client.post("/api/v1/admin/campaigns", json=payload, headers=headers)
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "validation_error"


@pytest.mark.br("BR-CAMP-02")
def test_t_camp_02_b_duplicate_slug_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-02-b: Existing slug rejects second creation with 409 slug_taken (BR-CAMP-02)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    create_campaign(db_session, slug="first-day")

    payload = {
        "title": "حملة مكررة الرمز",
        "slug": "first-day",
        "campaign_date": "2026-10-25",
    }
    res = client.post("/api/v1/admin/campaigns", json=payload, headers=headers)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "slug_taken"


@pytest.mark.br("BR-GEN-06")
def test_campaign_rejects_unknown_fields(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Unknown or reserved fields in payload reject with 422 unknown_field (BR-GEN-06)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    payload = {
        "title": "حملة مشبوهة",
        "slug": "suspicious-campaign",
        "campaign_date": "2026-10-25",
        "status": "live",  # Reserved field
        "is_current": True,  # Reserved field
    }
    res = client.post("/api/v1/admin/campaigns", json=payload, headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "unknown_field"


@pytest.mark.br("BR-CAMP-15")
def test_t_camp_15_a_content_size_limit_100kb(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-15-a: Content size exceeding 100 KB rejected with 422 validation_error."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    huge_content = "X" * (100 * 1024 + 1)
    payload = {
        "title": "حملة كبيرة جدا",
        "slug": "huge-content-campaign",
        "campaign_date": "2026-10-25",
        "content": huge_content,
    }
    res = client.post("/api/v1/admin/campaigns", json=payload, headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


# ==============================================================================
# 3. Public vs Draft Visibility (T-CAMP-03-a, T-CAMP-03-b, T-CAMP-03-c)
# ==============================================================================


@pytest.mark.br("BR-CAMP-03")
@pytest.mark.critical
def test_t_camp_03_a_and_b_draft_campaign_hidden_from_public(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-CAMP-03-a & b ★: Unpublished draft campaign C5 is 404 to public and absent from lists."""
    c5 = create_campaign(
        db_session,
        slug="c5-draft-campaign",
        title="حملة مسودة غير منشورة",
        is_published=False,
    )

    client.cookies.clear()

    # 1. Public single GET -> 404 not_found
    res_slug = client.get(f"/api/v1/public/campaigns/{c5.slug}")
    assert res_slug.status_code == 404
    assert res_slug.json()["error"]["code"] == "not_found"

    # 2. Public catalog list -> does not contain C5
    res_list = client.get("/api/v1/public/campaigns")
    assert res_list.status_code == 200
    slugs = [item["slug"] for item in res_list.json()]
    assert c5.slug not in slugs


@pytest.mark.br("BR-CAMP-03")
def test_t_camp_03_c_publishing_campaign_makes_it_visible(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-03-c: Publishing campaign makes it visible on public routes (BR-CAMP-03)."""
    c5 = create_campaign(
        db_session,
        slug="c5-published",
        title="حملة C5 منشورة",
        is_published=False,
    )

    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    # 1. Admin publishes C5
    res_patch = client.patch(
        f"/api/v1/admin/campaigns/{c5.id}",
        json={"is_published": True},
        headers=headers,
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["is_published"] is True

    # 2. Public visitor fetches C5 -> 200 OK
    client.cookies.clear()
    res_slug = client.get(f"/api/v1/public/campaigns/{c5.slug}")
    assert res_slug.status_code == 200
    assert res_slug.json()["slug"] == "c5-published"
    assert res_slug.json()["title"] == "حملة C5 منشورة"

    # 3. Public catalog contains C5
    res_list = client.get("/api/v1/public/campaigns")
    assert res_list.status_code == 200
    slugs = [item["slug"] for item in res_list.json()]
    assert c5.slug in slugs


# ==============================================================================
# 4. State Transitions & Publication Rules (T-CAMP-12-a, T-CAMP-12-b, T-CAMP-14)
# ==============================================================================


@pytest.mark.br("BR-CAMP-12")
@pytest.mark.critical
def test_t_camp_12_a_cannot_unpublish_live_campaign(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-12-a ★: Live campaign cannot be unpublished (409 invalid_state_transition)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    c_live = create_campaign(
        db_session,
        slug="c-live-now",
        status="live",
        is_published=True,
    )

    res = client.patch(
        f"/api/v1/admin/campaigns/{c_live.id}",
        json={"is_published": False},
        headers=headers,
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "invalid_state_transition"


@pytest.mark.br("BR-CAMP-12")
def test_t_camp_12_b_unpublish_current_resets_is_current(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-12-b: Unpublishing an upcoming current campaign resets is_current to false (BR-CAMP-12)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    c_current = create_campaign(
        db_session,
        slug="c-current-upcoming",
        status="upcoming",
        is_published=True,
        is_current=True,
    )

    res = client.patch(
        f"/api/v1/admin/campaigns/{c_current.id}",
        json={"is_published": False},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["is_published"] is False
    assert data["is_current"] is False


@pytest.mark.br("BR-CAMP-14")
def test_t_camp_14_a_cannot_modify_date_when_live_or_ended(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-14-a: Modifying campaign_date when live or ended rejected with 409 (BR-CAMP-14)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    c_live = create_campaign(db_session, status="live", is_published=True)
    c_ended = create_campaign(db_session, status="ended", is_published=True)

    # 1. Live rejection
    res_live = client.patch(
        f"/api/v1/admin/campaigns/{c_live.id}",
        json={"campaign_date": "2026-12-01"},
        headers=headers,
    )
    assert res_live.status_code == 409
    assert res_live.json()["error"]["code"] == "invalid_state_transition"

    # 2. Ended rejection
    res_ended = client.patch(
        f"/api/v1/admin/campaigns/{c_ended.id}",
        json={"campaign_date": "2026-12-01"},
        headers=headers,
    )
    assert res_ended.status_code == 409
    assert res_ended.json()["error"]["code"] == "invalid_state_transition"


@pytest.mark.br("BR-CAMP-14")
def test_t_camp_14_b_modify_date_when_upcoming_succeeds(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-14-b: Modifying campaign_date when upcoming succeeds (BR-CAMP-14)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    c_upcoming = create_campaign(db_session, status="upcoming", is_published=False)

    new_date = "2026-12-25"
    res = client.patch(
        f"/api/v1/admin/campaigns/{c_upcoming.id}",
        json={"campaign_date": new_date},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["campaign_date"] == new_date


# ==============================================================================
# 5. Conditional Deletion (T-CAMP-13-a, T-CAMP-13-b)
# ==============================================================================


@pytest.mark.br("BR-CAMP-13")
def test_t_camp_13_a_delete_campaign_with_data_rejected_with_409_in_use(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-13-a: Campaign with donor, pledge, or photo rejects deletion with 409 in_use (BR-CAMP-13)."""
    site = create_site(db_session)
    c_pledge = create_campaign(db_session)
    c_donor = create_campaign(db_session)
    c_photo = create_campaign(db_session)

    # Attach participation
    create_campaign_site(db_session, campaign_id=c_pledge.id, site_id=site.id)
    create_campaign_site(db_session, campaign_id=c_donor.id, site_id=site.id)
    create_campaign_site(db_session, campaign_id=c_photo.id, site_id=site.id)

    # 1. Attach pledge
    create_pledge(db_session, campaign_id=c_pledge.id, site_id=site.id)

    # 2. Attach donor
    create_donor(db_session, campaign_id=c_donor.id, site_id=site.id)

    # 3. Attach photo
    create_photo(db_session, campaign_id=c_photo.id, site_id=site.id)

    # Reauthenticate with fresh step-up R
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)

    for camp in [c_pledge, c_donor, c_photo]:
        res = client.delete(f"/api/v1/admin/campaigns/{camp.id}", headers=headers)
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "in_use"


@pytest.mark.br("BR-CAMP-13")
def test_t_camp_13_b_delete_unused_campaign_succeeds(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-13-b: Deleting empty campaign with fresh reauth succeeds with 204 (BR-CAMP-13)."""
    empty_campaign = create_campaign(db_session)

    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)

    res = client.delete(f"/api/v1/admin/campaigns/{empty_campaign.id}", headers=headers)
    assert res.status_code == 204

    # Verify deleted
    get_res = client.get(f"/api/v1/admin/campaigns/{empty_campaign.id}", headers=headers)
    assert get_res.status_code == 404


@pytest.mark.br("BR-AUTH-14")
def test_delete_campaign_requires_recent_reauth(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """DELETE /admin/campaigns/{id} requires step-up confirmation R (403 reauth_required)."""
    empty_campaign = create_campaign(db_session)

    # Admin session WITHOUT fresh reauth (> 10m)
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=False)

    res = client.delete(f"/api/v1/admin/campaigns/{empty_campaign.id}", headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "reauth_required"
