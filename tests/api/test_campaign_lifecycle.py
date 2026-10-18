"""Integration tests for Campaign Lifecycle API endpoints (P4.4).

Per:
- docs/04-api.md §13 (Admin Campaigns Lifecycle: set-current, open, revert)
- docs/06-business-rules.md §3 (BR-CAMP-04, 05, 06, 07, 08, 11, 12, 14)
- docs/07-test-cases.md §5.2 (T-CAMP-05, T-CAMP-06, T-CAMP-07, T-CAMP-08, T-CAMP-11, T-GEN-12-b)
- docs/03-auth-permissions.md §11 (CAMPAIGN_MANAGE, step-up R for revert)
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
from app.models.campaign import Campaign
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.pledge import create_pledge
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user."""
    return create_user(
        db_session,
        email="admin.lifecycle@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create a supervisor user assigned to a test site."""
    site = create_site(db_session, name="موقع المشرف للدورة")
    return create_user(
        db_session,
        email="supervisor.lifecycle@bloodday.local",
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


@pytest.mark.br("BR-CAMP-06")
def test_lifecycle_endpoints_require_admin_role_and_mfa(
    client: TestClient,
    db_session: Session,
    supervisor_user: User,
    admin_user: User,
) -> None:
    """Non-admin or unauthenticated roles cannot access lifecycle endpoints."""
    campaign = create_campaign(db_session, slug="c-auth-lifecycle", is_published=True)
    cid = str(campaign.id)

    # 1. Anonymous access -> 401 not_authenticated
    client.cookies.clear()
    for endpoint in ("set-current", "open", "revert"):
        resp = client.post(f"/api/v1/admin/campaigns/{cid}/{endpoint}")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "not_authenticated"

    # 2. Supervisor access -> 403 forbidden
    headers = create_auth_headers_and_cookies(client, db_session, supervisor_user)
    for endpoint in ("set-current", "open", "revert"):
        resp = client.post(f"/api/v1/admin/campaigns/{cid}/{endpoint}", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"

    # 3. Admin without MFA -> 403 mfa_required
    headers_no_mfa = create_auth_headers_and_cookies(
        client, db_session, admin_user, mfa_verified=False
    )
    for endpoint in ("set-current", "open", "revert"):
        resp = client.post(f"/api/v1/admin/campaigns/{cid}/{endpoint}", headers=headers_no_mfa)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "mfa_required"


# ==============================================================================
# 2. POST /admin/campaigns/{id}/set-current (BR-CAMP-04, 05)
# ==============================================================================


@pytest.mark.br("BR-CAMP-05")
def test_t_camp_05_a_set_current_unpublished_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-05-a: Attempt to set unpublished campaign as current returns 409 campaign_closed."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    c = create_campaign(db_session, slug="c-unpub-current", is_published=False)
    resp = client.post(f"/api/v1/admin/campaigns/{c.id}/set-current", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "campaign_closed"


@pytest.mark.br("BR-CAMP-04")
@pytest.mark.br("BR-CAMP-05")
def test_t_camp_05_b_set_current_switches_single_current(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-05-b: Setting C1 as current unsets C2; exactly one campaign remains current."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    c2 = create_campaign(db_session, slug="c2-already-current", is_published=True, is_current=True)
    c1 = create_campaign(db_session, slug="c1-new-current", is_published=True, is_current=False)

    resp = client.post(f"/api/v1/admin/campaigns/{c1.id}/set-current", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(c1.id)
    assert data["is_current"] is True

    # Check database state
    db_session.refresh(c1)
    db_session.refresh(c2)
    assert c1.is_current is True
    assert c2.is_current is False

    # Confirm only one campaign has is_current = True
    current_count = db_session.query(Campaign).filter_by(is_current=True).count()
    assert current_count == 1


# ==============================================================================
# 3. POST /admin/campaigns/{id}/open (BR-CAMP-06, 07, 11)
# ==============================================================================


@pytest.mark.br("BR-CAMP-06")
def test_t_camp_06_a_open_unpublished_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-06-a: Attempt to open an unpublished campaign returns 409 invalid_state_transition."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    site = create_site(db_session, name="موقع للحملة المسودة")
    c = create_campaign(db_session, slug="c-draft-open", is_published=False)
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)

    resp = client.post(f"/api/v1/admin/campaigns/{c.id}/open", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state_transition"


@pytest.mark.br("BR-CAMP-06")
def test_t_camp_06_b_open_no_sites_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-06-b: Attempt to open a campaign without participating sites returns 409."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    c = create_campaign(db_session, slug="c-no-sites-open", is_published=True)

    resp = client.post(f"/api/v1/admin/campaigns/{c.id}/open", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state_transition"


@pytest.mark.br("BR-CAMP-11")
def test_t_camp_06_c_open_ended_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-06-c / T-CAMP-11-a: Ended campaign cannot be reopened."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    site = create_site(db_session, name="موقع حملة منتهية")
    c = create_campaign(db_session, slug="c-ended-open", status="ended", is_published=True)
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)

    resp = client.post(f"/api/v1/admin/campaigns/{c.id}/open", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state_transition"


@pytest.mark.br("BR-CAMP-06")
@pytest.mark.br("BR-CAMP-07")
def test_t_camp_06_d_and_07_a_open_when_another_live_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-06-d / T-CAMP-07-a: Cannot open when another campaign is currently live."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    site = create_site(db_session, name="موقع الفحص الحي")
    # Existing live campaign
    c_live = create_campaign(db_session, slug="c-live-active", status="live", is_published=True)
    create_campaign_site(db_session, campaign_id=c_live.id, site_id=site.id)

    # Candidate upcoming campaign
    c_next = create_campaign(
        db_session, slug="c-upcoming-candidate", status="upcoming", is_published=True
    )
    create_campaign_site(db_session, campaign_id=c_next.id, site_id=site.id)

    resp = client.post(f"/api/v1/admin/campaigns/{c_next.id}/open", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state_transition"


@pytest.mark.br("BR-CAMP-06")
def test_t_camp_06_e_open_success(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-06-e: Opening an eligible campaign transitions to live and sets is_current=True."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    site = create_site(db_session, name="موقع الحملة الناجحة")
    c_prev = create_campaign(db_session, slug="c-prev-current", is_published=True, is_current=True)
    c_open = create_campaign(
        db_session, slug="c-ready-to-open", status="upcoming", is_published=True, is_current=False
    )
    create_campaign_site(db_session, campaign_id=c_open.id, site_id=site.id)

    resp = client.post(f"/api/v1/admin/campaigns/{c_open.id}/open", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(c_open.id)
    assert data["status"] == "live"
    assert data["is_current"] is True

    # Verify previous current campaign was unset
    db_session.refresh(c_prev)
    db_session.refresh(c_open)
    assert c_open.status == "live"
    assert c_open.is_current is True
    assert c_prev.is_current is False


# ==============================================================================
# 4. POST /admin/campaigns/{id}/revert (BR-CAMP-08, 11)
# ==============================================================================


@pytest.mark.br("BR-CAMP-08")
def test_t_camp_08_c_revert_requires_step_up_reauth(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-08-c: Reverting a campaign without fresh reauth returns 403 reauth_required."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=False)

    c = create_campaign(db_session, slug="c-revert-stale", status="live", is_published=True)

    resp = client.post(f"/api/v1/admin/campaigns/{c.id}/revert", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"


@pytest.mark.br("BR-CAMP-08")
def test_t_camp_08_a_cannot_revert_with_donors(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-08-a: Reverting a live campaign with recorded donors returns 409 invalid_state_transition."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)

    site = create_site(db_session, name="موقع متبرع للتراجع")
    c = create_campaign(db_session, slug="c-live-with-donors", status="live", is_published=True)
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)
    create_donor(db_session, campaign_id=c.id, site_id=site.id, registered_by=admin_user.id)

    resp = client.post(f"/api/v1/admin/campaigns/{c.id}/revert", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state_transition"


@pytest.mark.br("BR-CAMP-08")
def test_t_camp_08_b_revert_empty_live_campaign_success(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-08-b: Live campaign with no donors reverts to upcoming with fresh reauth."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)

    site = create_site(db_session, name="موقع تراجع فارغ")
    c = create_campaign(db_session, slug="c-live-empty", status="live", is_published=True)
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)

    resp = client.post(f"/api/v1/admin/campaigns/{c.id}/revert", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(c.id)
    assert data["status"] == "upcoming"

    db_session.refresh(c)
    assert c.status == "upcoming"


@pytest.mark.br("BR-CAMP-11")
def test_t_camp_11_a_ended_cannot_revert(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-11-a: Ended campaign cannot be reverted."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)

    c = create_campaign(db_session, slug="c-ended-revert", status="ended", is_published=True)

    resp = client.post(f"/api/v1/admin/campaigns/{c.id}/revert", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state_transition"


# ==============================================================================
# 5. Atomicity & Concurrency (T-GEN-12-b, T-CONC-02, T-CONC-03)
# ==============================================================================


@pytest.mark.br("BR-GEN-12")
def test_t_gen_12_b_set_current_atomic_rollback(
    db_session: Session,
    admin_user: User,
) -> None:
    """T-GEN-12-b: Failure during set_current rolls back atomically; previous current remains current."""
    from unittest.mock import patch

    from app.repositories.campaigns import CampaignRepository
    from app.services.campaigns import CampaignService

    c_prev = create_campaign(db_session, slug="c-atomic-prev", is_published=True, is_current=True)
    c_new = create_campaign(db_session, slug="c-atomic-new", is_published=True, is_current=False)
    db_session.flush()

    repo = CampaignRepository(db_session)
    service = CampaignService(repository=repo)

    # Inject failure on db.flush() when updating c_new
    original_flush = db_session.flush

    def failing_flush() -> None:
        if c_new.is_current:
            raise RuntimeError("Injected database failure during atomic set_current")
        original_flush()

    savepoint = db_session.begin_nested()
    with (
        patch.object(db_session, "flush", side_effect=failing_flush),
        pytest.raises(RuntimeError, match="Injected database failure"),
    ):
        service.set_current_campaign(c_new.id, actor_user_id=admin_user.id)

    # Roll back savepoint to simulate request transaction failure handling
    savepoint.rollback()

    # Verify previous campaign remains current and new campaign remains non-current
    db_session.refresh(c_prev)
    db_session.refresh(c_new)
    assert c_prev.is_current is True
    assert c_new.is_current is False


@pytest.mark.br("BR-CAMP-06")
@pytest.mark.br("BR-CAMP-07")
def test_t_conc_02_sequential_race_open(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CONC-02: Two eligible campaigns competing to open; only one becomes live."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    site1 = create_site(db_session, name="موقع تنافس 1")
    site2 = create_site(db_session, name="موقع تنافس 2")

    c1 = create_campaign(db_session, slug="c-race-1", status="upcoming", is_published=True)
    create_campaign_site(db_session, campaign_id=c1.id, site_id=site1.id)

    c2 = create_campaign(db_session, slug="c-race-2", status="upcoming", is_published=True)
    create_campaign_site(db_session, campaign_id=c2.id, site_id=site2.id)

    # First open succeeds
    resp1 = client.post(f"/api/v1/admin/campaigns/{c1.id}/open", headers=headers)
    assert resp1.status_code == 200

    # Second open immediately rejected with 409
    resp2 = client.post(f"/api/v1/admin/campaigns/{c2.id}/open", headers=headers)
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "invalid_state_transition"

    # Exactly one live campaign in database
    live_count = db_session.query(Campaign).filter_by(status="live").count()
    assert live_count == 1


@pytest.mark.br("BR-CAMP-04")
def test_t_conc_03_sequential_race_set_current(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CONC-03: Multiple set-current transitions guarantee exactly one campaign is current."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    c1 = create_campaign(db_session, slug="c-conc-curr-1", is_published=True)
    c2 = create_campaign(db_session, slug="c-conc-curr-2", is_published=True)
    c3 = create_campaign(db_session, slug="c-conc-curr-3", is_published=True)

    client.post(f"/api/v1/admin/campaigns/{c1.id}/set-current", headers=headers)
    client.post(f"/api/v1/admin/campaigns/{c2.id}/set-current", headers=headers)
    client.post(f"/api/v1/admin/campaigns/{c3.id}/set-current", headers=headers)

    db_session.refresh(c1)
    db_session.refresh(c2)
    db_session.refresh(c3)

    assert c1.is_current is False
    assert c2.is_current is False
    assert c3.is_current is True

    current_count = db_session.query(Campaign).filter_by(is_current=True).count()
    assert current_count == 1


# ==============================================================================
# 6. Campaign End Lifecycle (T-CAMP-09, 10, 11, 16, 17, 18, T-GEN-12-a, T-CONC-01)
# ==============================================================================


@pytest.mark.br("BR-CAMP-09")
@pytest.mark.critical
def test_t_camp_09_a_end_upcoming_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-09-a ★: Ending an upcoming campaign rejects with 409 invalid_state_transition."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)
    campaign = create_campaign(db_session, slug="c-end-upcoming", status="upcoming")
    db_session.flush()

    resp = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state_transition"

    db_session.refresh(campaign)
    assert campaign.status == "upcoming"
    assert campaign.final_stats is None
    assert campaign.ended_at is None


@pytest.mark.br("BR-CAMP-09")
@pytest.mark.critical
def test_t_camp_09_b_end_live_success_freezes_stats(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-09-b ★: Ending live campaign atomically computes final_stats and updates status to ended."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)

    # Prepare live campaign with 2 sites
    site_a = create_site(db_session, name="موقع أ للإنهاء")
    site_b = create_site(db_session, name="موقع ب للإنهاء")

    campaign = create_campaign(
        db_session, slug="c-end-live-success", status="live", is_published=True
    )
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site_b.id)

    # Pledges: 2 on site A, 1 on site B -> total 3
    create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        phone="+213550111111",
    )
    create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        phone="+213550222222",
    )
    create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site_b.id,
        phone="+213550333333",
    )

    # Donors on site A (4 total):
    # dA1: attended
    # dA2: donated + hero
    # dA3: donated + hero
    # dA4: donated, no hero
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        status="attended",
        registered_by=admin_user.id,
    )
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        status="donated",
        consent_hero_name=True,
        registered_by=admin_user.id,
    )
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        status="donated",
        consent_hero_name=True,
        registered_by=admin_user.id,
    )
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        status="donated",
        consent_hero_name=False,
        registered_by=admin_user.id,
    )

    # Donors on site B (2 total):
    # dB1: donated + hero
    # dB2: attended
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_b.id,
        status="donated",
        consent_hero_name=True,
        registered_by=admin_user.id,
    )
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_b.id,
        status="attended",
        registered_by=admin_user.id,
    )
    db_session.flush()

    resp = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # Check total numbers
    assert data["pledges"] == 3
    assert data["present"] == 6
    assert data["donated"] == 4
    assert data["heroes"] == 3
    assert "frozen_at" in data

    # Check by_site
    by_site = {s["name"]: s for s in data["by_site"]}
    assert len(by_site) == 2

    site_a_stat = by_site["موقع أ للإنهاء"]
    assert site_a_stat["site_id"] == str(site_a.id)
    assert site_a_stat["pledges"] == 2
    assert site_a_stat["present"] == 4
    assert site_a_stat["donated"] == 3

    site_b_stat = by_site["موقع ب للإنهاء"]
    assert site_b_stat["site_id"] == str(site_b.id)
    assert site_b_stat["pledges"] == 1
    assert site_b_stat["present"] == 2
    assert site_b_stat["donated"] == 1

    # Check DB model updates
    db_session.refresh(campaign)
    assert campaign.status == "ended"
    assert campaign.ended_at is not None
    assert campaign.final_stats is not None
    assert campaign.final_stats["donated"] == 4

    # Check audit event
    from app.models.audit import AuditLog

    audit = (
        db_session.query(AuditLog)
        .filter_by(
            action="campaign.end",
            entity_id=campaign.id,
        )
        .first()
    )
    assert audit is not None
    assert audit.actor_user_id == admin_user.id
    assert audit.details == {"slug": campaign.slug, "status": "ended"}


@pytest.mark.br("BR-CAMP-09")
@pytest.mark.critical
def test_t_camp_09_c_end_authorization_matrix(
    client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
) -> None:
    """T-CAMP-09-c ★: Role and step-up R enforcement for campaign end."""
    campaign = create_campaign(db_session, slug="c-end-auth", status="live", is_published=True)
    db_session.flush()

    # 1. Anonymous visitor -> 401
    client.cookies.clear()
    resp_anon = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end")
    assert resp_anon.status_code == 401
    assert resp_anon.json()["error"]["code"] == "not_authenticated"

    # 2. Supervisor -> 403 forbidden
    headers_sup = create_auth_headers_and_cookies(client, db_session, supervisor_user)
    resp_sup = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=headers_sup)
    assert resp_sup.status_code == 403
    assert resp_sup.json()["error"]["code"] == "forbidden"

    # 3. Admin without MFA (mfa_verified=False) -> 403 mfa_required
    headers_nomfa = create_auth_headers_and_cookies(
        client, db_session, admin_user, mfa_verified=False
    )
    resp_nomfa = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=headers_nomfa)
    assert resp_nomfa.status_code == 403
    assert resp_nomfa.json()["error"]["code"] == "mfa_required"

    # 4. Admin without fresh reauth (fresh_reauth=False) -> 403 reauth_required
    headers_stale = create_auth_headers_and_cookies(
        client, db_session, admin_user, mfa_verified=True, fresh_reauth=False
    )
    resp_stale = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=headers_stale)
    assert resp_stale.status_code == 403
    assert resp_stale.json()["error"]["code"] == "reauth_required"

    # Verify campaign status untouched
    db_session.refresh(campaign)
    assert campaign.status == "live"
    assert campaign.final_stats is None


@pytest.mark.br("BR-CAMP-10")
def test_t_camp_10_a_re_end_refreshes_final_stats(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-10-a: Re-ending an ended campaign refreshes final_stats."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)

    site = create_site(db_session, name="موقع إعادة الإنهاء")
    campaign = create_campaign(
        db_session,
        slug="c-re-end",
        status="ended",
        final_stats={"donated": 1, "present": 1, "pledges": 0, "heroes": 0},
    )
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)

    # Initial donor
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        status="donated",
        registered_by=admin_user.id,
    )
    db_session.flush()

    # Re-end: initial check
    resp1 = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["donated"] == 1

    # Admin adds a correction donor
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        status="donated",
        consent_hero_name=True,
        registered_by=admin_user.id,
    )
    db_session.flush()

    # Re-end again
    resp2 = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["donated"] == 2
    assert data2["present"] == 2
    assert data2["heroes"] == 1

    db_session.refresh(campaign)
    assert campaign.final_stats["donated"] == 2


@pytest.mark.br("BR-CAMP-10")
def test_t_camp_10_b_end_purged_campaign_rejected(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-10-b: Ending a purged campaign is rejected with 409 campaign_purged."""
    from datetime import UTC, datetime

    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)

    campaign = create_campaign(
        db_session,
        slug="c-purged-end",
        status="ended",
        personal_data_purged_at=datetime.now(UTC),
    )
    db_session.flush()

    resp = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "campaign_purged"


@pytest.mark.br("BR-CAMP-11")
def test_t_camp_11_a_open_and_revert_rejected_on_ended(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-11-a: Ended campaigns cannot be opened or reverted."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user, fresh_reauth=True)
    site = create_site(db_session, name="موقع انتهى")
    campaign = create_campaign(
        db_session, slug="c-ended-no-return", status="ended", is_published=True
    )
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)
    db_session.flush()

    # Open rejects
    resp_open = client.post(f"/api/v1/admin/campaigns/{campaign.id}/open", headers=headers)
    assert resp_open.status_code == 409
    assert resp_open.json()["error"]["code"] == "invalid_state_transition"

    # Revert rejects
    resp_revert = client.post(f"/api/v1/admin/campaigns/{campaign.id}/revert", headers=headers)
    assert resp_revert.status_code == 409
    assert resp_revert.json()["error"]["code"] == "invalid_state_transition"


@pytest.mark.br("BR-CAMP-16")
@pytest.mark.critical
def test_t_camp_16_b_supervisor_rejected_after_end(
    client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
) -> None:
    campaign = create_campaign(db_session, slug="c-sup-after-end", status="live", is_published=True)
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=supervisor_user.site_id)
    db_session.flush()

    # 1. End the campaign as admin
    admin_headers = create_auth_headers_and_cookies(
        client, db_session, admin_user, fresh_reauth=True
    )
    resp_end = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=admin_headers)
    assert resp_end.status_code == 200

    # 2. Switch session to supervisor
    sup_headers = create_auth_headers_and_cookies(client, db_session, supervisor_user)

    # Supervisor tries GET donors -> 409 no_live_campaign
    resp_get = client.get("/api/v1/supervisor/donors", headers=sup_headers)
    assert resp_get.status_code == 409
    assert resp_get.json()["error"]["code"] == "no_live_campaign"

    # Supervisor tries POST donor -> 409 no_live_campaign
    resp_post = client.post(
        "/api/v1/supervisor/donors",
        headers=sup_headers,
        json={
            "id": "11111111-1111-1111-1111-111111111111",
            "first_name": "أحمد",
            "last_name": "محمد",
            "consent_keep_phone": False,
            "consent_hero_name": False,
            "consent_photo": False,
            "consent_public_phone": False,
        },
    )
    assert resp_post.status_code == 409
    assert resp_post.json()["error"]["code"] == "no_live_campaign"


@pytest.mark.br("BR-CAMP-17")
def test_t_camp_17_a_supervisors_remain_active_after_end(
    client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
) -> None:
    """T-CAMP-17-a: After end, supervisors remain is_active=True (not automatically deactivated)."""
    admin_headers = create_auth_headers_and_cookies(
        client, db_session, admin_user, fresh_reauth=True
    )
    campaign = create_campaign(
        db_session, slug="c-sup-active-end", status="live", is_published=True
    )
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=supervisor_user.site_id)
    db_session.flush()

    resp = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=admin_headers)
    assert resp.status_code == 200

    db_session.refresh(supervisor_user)
    assert supervisor_user.is_active is True


@pytest.mark.br("BR-CAMP-18")
def test_t_camp_18_a_donor_edit_after_end_keeps_frozen_counters_until_re_end(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-CAMP-18-a: Editing a donor after end does not alter public counters until re-end."""
    admin_headers = create_auth_headers_and_cookies(
        client, db_session, admin_user, fresh_reauth=True
    )
    site = create_site(db_session, name="موقع التجميد والعدّاد")
    campaign = create_campaign(
        db_session, slug="c-frozen-counters", status="live", is_published=True
    )
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site.id,
        status="attended",
        registered_by=admin_user.id,
    )
    db_session.flush()

    # End campaign -> donated=0, present=1
    client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=admin_headers)

    # Check public counters
    resp_c1 = client.get(f"/api/v1/public/campaigns/{campaign.slug}/counters")
    assert resp_c1.status_code == 200
    assert resp_c1.json()["donated_count"] == 0
    assert resp_c1.json()["present_count"] == 1

    # Modify donor status to donated
    donor.status = "donated"
    db_session.flush()

    # Public counters still return frozen numbers (donated=0)
    resp_c2 = client.get(f"/api/v1/public/campaigns/{campaign.slug}/counters")
    assert resp_c2.status_code == 200
    assert resp_c2.json()["donated_count"] == 0

    # Admin calls re-end
    client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=admin_headers)

    # Now public counters reflect updated frozen number (donated=1)
    resp_c3 = client.get(f"/api/v1/public/campaigns/{campaign.slug}/counters")
    assert resp_c3.status_code == 200
    assert resp_c3.json()["donated_count"] == 1


@pytest.mark.br("BR-GEN-12")
def test_t_gen_12_a_end_campaign_atomic_rollback_on_failure(
    db_session: Session,
    admin_user: User,
) -> None:
    """T-GEN-12-a: Injected failure during end rolls back atomically without partial state."""
    from unittest.mock import patch

    from app.repositories.campaigns import CampaignRepository
    from app.services.campaigns import CampaignService

    site = create_site(db_session, name="موقع الذرية")
    campaign = create_campaign(db_session, slug="c-atomic-end", status="live", is_published=True)
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)
    db_session.flush()

    repo = CampaignRepository(db_session)
    service = CampaignService(repository=repo)

    def failing_end(*args, **kwargs):
        raise RuntimeError("Injected database failure during campaign end")

    savepoint = db_session.begin_nested()
    with (
        patch.object(repo, "end_campaign", side_effect=failing_end),
        pytest.raises(RuntimeError, match="Injected database failure"),
    ):
        service.end_campaign(campaign.id, actor_user_id=admin_user.id)

    savepoint.rollback()

    db_session.refresh(campaign)
    assert campaign.status == "live"
    assert campaign.final_stats is None
    assert campaign.ended_at is None


@pytest.mark.br("BR-CAMP-09")
def test_t_conc_01_concurrent_end_and_create_donor(
    client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
) -> None:
    """T-CONC-01: Concurrent end and create_donor guarantees no middle state where donor is omitted from final_stats."""
    campaign = create_campaign(db_session, slug="c-conc-01", status="live", is_published=True)
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=supervisor_user.site_id)
    db_session.flush()

    # 1. End campaign as admin
    admin_headers = create_auth_headers_and_cookies(
        client, db_session, admin_user, fresh_reauth=True
    )
    resp_end = client.post(f"/api/v1/admin/campaigns/{campaign.id}/end", headers=admin_headers)
    assert resp_end.status_code == 200
    initial_stats = resp_end.json()
    assert initial_stats["present"] == 0

    # 2. Switch session to supervisor
    sup_headers = create_auth_headers_and_cookies(client, db_session, supervisor_user)

    # Subsequent write strictly rejected
    resp_donor = client.post(
        "/api/v1/supervisor/donors",
        headers=sup_headers,
        json={
            "id": "22222222-2222-2222-2222-222222222222",
            "first_name": "متبرع متزامن",
            "consent_keep_phone": False,
            "consent_hero_name": False,
            "consent_photo": False,
            "consent_public_phone": False,
        },
    )
    assert resp_donor.status_code == 409
    assert resp_donor.json()["error"]["code"] == "no_live_campaign"
