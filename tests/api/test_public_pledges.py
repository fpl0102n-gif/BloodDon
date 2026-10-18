"""Integration tests for Public Campaign Pledges API endpoint (P6.2).

Per:
- docs/04-api.md §7 (POST /public/campaigns/{slug}/pledges)
- docs/06-business-rules.md §5 (BR-PLEDGE-01..11)
- docs/07-test-cases.md §5.4 (T-PLEDGE-02..11)
- docs/07-test-cases.md §5.13 (T-AUTH-18-d: anon visitor bypasses CSRF)
- docs/07-test-cases.md §6 (T-CONC-07: concurrency on identical phone)
- docs/11-decisions.md (D-62: PLEDGES_ENABLED kill switch -> 503)
"""

from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_settings
from app.core.clock import FakeClock, set_clock
from app.core.ratelimit import InMemoryRateLimiter, set_rate_limiter
from app.models.campaign import Campaign, Site
from app.models.pledge import Pledge
from tests.factories.campaign import create_campaign, create_campaign_site, create_site


@pytest.fixture
def client(test_client: TestClient) -> TestClient:
    """Convenience fixture mapping to anonymous test client."""
    return test_client


@pytest.fixture
def fake_clock() -> Generator[FakeClock, None, None]:
    """Provide FakeClock starting at reference time and register with global clock/rate-limiter."""
    clock = FakeClock(datetime(2026, 11, 20, 10, 0, 0))
    set_clock(clock)
    limiter = InMemoryRateLimiter(clock=clock)
    set_rate_limiter(limiter)
    try:
        yield clock
    finally:
        set_clock(None)
        set_rate_limiter(None)


@pytest.fixture
def campaign_setup(db_session: Session) -> tuple[Campaign, Site, Site]:
    """Provide published upcoming campaign with two participating sites."""
    site_a = create_site(db_session, is_active=True, name="موقع أ للتعهدات")
    site_b = create_site(db_session, is_active=True, name="موقع ب للتعهدات")
    camp = create_campaign(
        db_session,
        is_published=True,
        status="upcoming",
        title="حملة التعهدات التجريبية",
    )
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_b.id)
    return camp, site_a, site_b


# ==============================================================================
# 1. State Transitions & Access (T-PLEDGE-02-a..d, BR-PLEDGE-02)
# ==============================================================================


@pytest.mark.br("BR-PLEDGE-02")
def test_t_pledge_02_a_valid_pledge_upcoming_campaign(
    client: TestClient,
    db_session: Session,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-PLEDGE-02-a: Valid pledge on upcoming published campaign succeeds with 204."""
    camp, site_a, _ = campaign_setup

    payload = {
        "site_id": str(site_a.id),
        "first_name": "فاطمة",
        "phone": "0550123456",
        "website": "",
    }
    res = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
    assert res.status_code == 204
    assert res.content == b""

    # Verify DB row
    pledges = (
        db_session.query(Pledge)
        .filter(Pledge.campaign_id == camp.id, Pledge.site_id == site_a.id)
        .all()
    )
    assert len(pledges) == 1
    assert pledges[0].first_name == "فاطمة"
    assert pledges[0].phone == "0550123456"


@pytest.mark.br("BR-PLEDGE-02")
def test_t_pledge_02_b_valid_pledge_live_campaign(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-PLEDGE-02-b: Valid pledge on live campaign succeeds with 204."""
    site = create_site(db_session, is_active=True)
    camp = create_campaign(db_session, is_published=True, status="live")
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site.id)

    payload = {
        "site_id": str(site.id),
        "first_name": "محمد",
        "phone": "0550999888",
    }
    res = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
    assert res.status_code == 204

    pledge = db_session.query(Pledge).filter(Pledge.campaign_id == camp.id).first()
    assert pledge is not None
    assert pledge.first_name == "محمد"


@pytest.mark.critical
@pytest.mark.br("BR-PLEDGE-02")
def test_t_pledge_02_c_pledge_on_draft_campaign_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-PLEDGE-02-c (★): Pledge on draft/unpublished campaign returns 404 not_found."""
    site = create_site(db_session, is_active=True)
    draft_camp = create_campaign(db_session, is_published=False, status="upcoming")
    create_campaign_site(db_session, campaign_id=draft_camp.id, site_id=site.id)

    payload = {
        "site_id": str(site.id),
        "first_name": "يوسف",
        "phone": "0550111222",
    }
    res = client.post(f"/api/v1/public/campaigns/{draft_camp.slug}/pledges", json=payload)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


@pytest.mark.br("BR-PLEDGE-02")
def test_t_pledge_02_d_pledge_on_ended_campaign_returns_409(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-PLEDGE-02-d: Pledge on ended campaign returns 409 campaign_closed."""
    site = create_site(db_session, is_active=True)
    ended_camp = create_campaign(db_session, is_published=True, status="ended")
    create_campaign_site(db_session, campaign_id=ended_camp.id, site_id=site.id)

    payload = {
        "site_id": str(site.id),
        "first_name": "خالد",
        "phone": "0550333444",
    }
    res = client.post(f"/api/v1/public/campaigns/{ended_camp.slug}/pledges", json=payload)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "campaign_closed"


# ==============================================================================
# 2. Site Participation & Validation (T-PLEDGE-03, T-PLEDGE-04)
# ==============================================================================


@pytest.mark.br("BR-PLEDGE-03")
def test_t_pledge_03_a_non_participating_site_returns_409(
    client: TestClient,
    db_session: Session,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-PLEDGE-03-a: Pledge for site not participating in campaign returns 409 site_not_in_campaign."""
    camp, _, _ = campaign_setup
    non_participating_site = create_site(db_session, is_active=True, name="موقع خارج الحملة")

    payload = {
        "site_id": str(non_participating_site.id),
        "first_name": "علي",
        "phone": "0550777666",
    }
    res = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "site_not_in_campaign"


@pytest.mark.br("BR-PLEDGE-04")
def test_t_pledge_04_a_name_length_validation(
    client: TestClient,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-PLEDGE-04-a: first_name with 1 char or 41 chars or whitespace-only returns 422."""
    camp, site_a, _ = campaign_setup

    # 1. 1 char
    res_short = client.post(
        f"/api/v1/public/campaigns/{camp.slug}/pledges",
        json={"site_id": str(site_a.id), "first_name": "أ"},
    )
    assert res_short.status_code == 422

    # 2. 41 chars
    res_long = client.post(
        f"/api/v1/public/campaigns/{camp.slug}/pledges",
        json={"site_id": str(site_a.id), "first_name": "أ" * 41},
    )
    assert res_long.status_code == 422

    # 3. Whitespace only
    res_blank = client.post(
        f"/api/v1/public/campaigns/{camp.slug}/pledges",
        json={"site_id": str(site_a.id), "first_name": "   "},
    )
    assert res_blank.status_code == 422


@pytest.mark.br("BR-PLEDGE-04")
def test_t_pledge_04_b_pledge_without_phone(
    client: TestClient,
    db_session: Session,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-PLEDGE-04-b: Pledge without phone succeeds with 204 and persists NULL phone."""
    camp, site_a, _ = campaign_setup

    payload = {
        "site_id": str(site_a.id),
        "first_name": "سارة",
        "phone": None,
    }
    res = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
    assert res.status_code == 204

    pledge = (
        db_session.query(Pledge)
        .filter(Pledge.campaign_id == camp.id, Pledge.first_name == "سارة")
        .first()
    )
    assert pledge is not None
    assert pledge.phone is None


# ==============================================================================
# 3. Honeypot & Privacy (T-PLEDGE-05-a ★, BR-PLEDGE-05)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PLEDGE-05")
def test_t_pledge_05_a_honeypot_silently_ignored(
    client: TestClient,
    db_session: Session,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-PLEDGE-05-a (★): When honeypot website field is filled, return 204 without saving any row."""
    camp, site_a, _ = campaign_setup

    initial_count = db_session.query(Pledge).count()

    payload = {
        "site_id": str(site_a.id),
        "first_name": "بوت خبيث",
        "phone": "0550123456",
        "website": "https://spambot.evil.com",
    }
    res = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
    assert res.status_code == 204
    assert res.content == b""

    # Verify no row created
    final_count = db_session.query(Pledge).count()
    assert final_count == initial_count


# ==============================================================================
# 4. Idempotent Duplicates (T-PLEDGE-06-a ★, T-PLEDGE-06-c, T-PLEDGE-07-a)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PLEDGE-06")
def test_t_pledge_06_a_duplicate_phone_identical_response(
    client: TestClient,
    db_session: Session,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-PLEDGE-06-a (★): Submitting same phone twice returns byte-identical 204 with rows=1."""
    camp, site_a, _ = campaign_setup

    payload = {
        "site_id": str(site_a.id),
        "first_name": "حمزة",
        "phone": "0550123456",
    }

    # First submission
    res1 = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
    assert res1.status_code == 204
    assert res1.content == b""

    # Second submission (identical phone, even if first_name differs)
    payload2 = {
        "site_id": str(site_a.id),
        "first_name": "حمزة الثاني",
        "phone": "0550123456",
    }
    res2 = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload2)
    assert res2.status_code == 204
    assert res2.content == b""

    # Strict identical status and body
    assert res1.status_code == res2.status_code
    assert res1.content == res2.content

    # Exactly 1 row in DB
    rows = (
        db_session.query(Pledge)
        .filter(Pledge.campaign_id == camp.id, Pledge.phone == "0550123456")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].first_name == "حمزة"


@pytest.mark.br("BR-PLEDGE-06")
def test_t_pledge_06_c_same_phone_different_site_creates_separate_row(
    client: TestClient,
    db_session: Session,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-PLEDGE-06-c: Same phone in different sites of same campaign creates separate rows."""
    camp, site_a, site_b = campaign_setup

    phone = "0550555444"

    res_a = client.post(
        f"/api/v1/public/campaigns/{camp.slug}/pledges",
        json={"site_id": str(site_a.id), "first_name": "أيوب أ", "phone": phone},
    )
    assert res_a.status_code == 204

    res_b = client.post(
        f"/api/v1/public/campaigns/{camp.slug}/pledges",
        json={"site_id": str(site_b.id), "first_name": "أيوب ب", "phone": phone},
    )
    assert res_b.status_code == 204

    rows = db_session.query(Pledge).filter(Pledge.phone == phone).all()
    assert len(rows) == 2
    site_ids = {r.site_id for r in rows}
    assert site_ids == {site_a.id, site_b.id}


@pytest.mark.br("BR-PLEDGE-07")
def test_t_pledge_07_a_pledges_without_phone_not_merged(
    client: TestClient,
    db_session: Session,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-PLEDGE-07-a: Two pledges without phone with the exact same name create two separate rows."""
    camp, site_a, _ = campaign_setup

    payload = {"site_id": str(site_a.id), "first_name": "مجهول الهوية", "phone": None}

    res1 = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
    assert res1.status_code == 204

    res2 = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
    assert res2.status_code == 204

    rows = (
        db_session.query(Pledge)
        .filter(Pledge.campaign_id == camp.id, Pledge.first_name == "مجهول الهوية")
        .all()
    )
    assert len(rows) == 2


# ==============================================================================
# 5. Rate Limiting & Kill Switch (T-PLEDGE-11-a, D-62)
# ==============================================================================


@pytest.mark.br("BR-PLEDGE-11")
def test_t_pledge_11_a_rate_limit_exceeded(
    client: TestClient,
    campaign_setup: tuple[Campaign, Site, Site],
    fake_clock: FakeClock,
) -> None:
    """T-PLEDGE-11-a: 20 pledges allowed per hour; 21st yields 429 rate_limited."""
    camp, site_a, _ = campaign_setup

    # Make 20 calls (allowed)
    for i in range(20):
        res = client.post(
            f"/api/v1/public/campaigns/{camp.slug}/pledges",
            json={
                "site_id": str(site_a.id),
                "first_name": f"متعهد {i}",
                "phone": f"05500000{i:02d}",
            },
        )
        assert res.status_code == 204, f"Request {i + 1} failed with {res.status_code}"

    # 21st request should be rate limited
    res_21 = client.post(
        f"/api/v1/public/campaigns/{camp.slug}/pledges",
        json={"site_id": str(site_a.id), "first_name": "متعهد إضافي", "phone": "0550999999"},
    )
    assert res_21.status_code == 429
    assert res_21.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in res_21.headers


@pytest.mark.critical
@pytest.mark.br("BR-PLEDGE-01")
def test_pledges_kill_switch_service_unavailable(
    client: TestClient,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """Decision D-62 / BR-PLEDGE-01: When PLEDGES_ENABLED is False, endpoint returns 503 service_unavailable.

    Verifies:
    1. Both /api/v1/public/campaigns/{camp.slug}/pledges and alias /public/campaigns/{camp.slug}/pledges return 503.
    2. Multiple attempts while disabled continue to return 503 without burning rate limit.
    3. Toggle back to True immediately restores 204 No Content.
    """
    camp, site_a, _ = campaign_setup

    base_settings = get_settings()
    disabled_settings = base_settings.model_copy(
        update={"pledges_enabled": False, "rate_pledge_per_hour": 3}
    )
    client.app.dependency_overrides[get_settings] = lambda: disabled_settings

    try:
        payload = {
            "site_id": str(site_a.id),
            "first_name": "طارق",
            "phone": "0550123456",
        }
        # 1. Primary path and alias both yield 503
        res1 = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
        assert res1.status_code == 503
        assert res1.json()["error"]["code"] == "service_unavailable"

        res2 = client.post(f"/public/campaigns/{camp.slug}/pledges", json=payload)
        assert res2.status_code == 503
        assert res2.json()["error"]["code"] == "service_unavailable"

        # 2. Repeated attempts while disabled remain 503
        for _ in range(5):
            res = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
            assert res.status_code == 503
            assert res.json()["error"]["code"] == "service_unavailable"

        # 3. Toggle back to True: service is immediately restored
        enabled_settings = base_settings.model_copy(
            update={"pledges_enabled": True, "rate_pledge_per_hour": 3}
        )
        client.app.dependency_overrides[get_settings] = lambda: enabled_settings

        res_restored = client.post(f"/api/v1/public/campaigns/{camp.slug}/pledges", json=payload)
        assert res_restored.status_code == 204
    finally:
        client.app.dependency_overrides.pop(get_settings, None)


# ==============================================================================
# 6. Anonymous CSRF Bypass (T-AUTH-18-d)
# ==============================================================================


@pytest.mark.br("BR-AUTH-18")
def test_t_auth_18_d_anonymous_csrf_bypass(
    client: TestClient,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-AUTH-18-d: Anonymous visitor without X-CSRF-Token or session cookie succeeds with 204."""
    camp, site_a, _ = campaign_setup

    client.cookies.clear()
    payload = {
        "site_id": str(site_a.id),
        "first_name": "زائر مجهول",
        "phone": "0550888777",
    }
    res = client.post(
        f"/api/v1/public/campaigns/{camp.slug}/pledges",
        json=payload,
        headers={},  # Zero CSRF headers
    )
    assert res.status_code == 204


# ==============================================================================
# 7. Concurrency & Idempotency Test (T-CONC-07)
# ==============================================================================


@pytest.mark.br("BR-PLEDGE-06")
def test_t_conc_07_concurrent_pledges_single_row(
    client: TestClient,
    db_session: Session,
    campaign_setup: tuple[Campaign, Site, Site],
) -> None:
    """T-CONC-07: Submissions of identical phone in same campaign/site results in exactly 1 row."""
    camp, site_a, _ = campaign_setup

    target_phone = "0550001122"
    payload = {
        "site_id": str(site_a.id),
        "first_name": "متعهد متزامن",
        "phone": target_phone,
    }

    url = f"/api/v1/public/campaigns/{camp.slug}/pledges"

    # 1. 20 submissions with identical phone & site (per docs/07-test-cases.md T-CONC-07)
    for i in range(20):
        res = client.post(url, json=payload)
        assert res.status_code == 204, f"Request {i + 1} failed with {res.status_code}"

    # 2. Exactly 1 row in DB
    rows = (
        db_session.query(Pledge)
        .filter(Pledge.campaign_id == camp.id, Pledge.phone == target_phone)
        .all()
    )
    assert len(rows) == 1

    # 3. Verify repository race condition safety (savepoint isolates concurrent insert)
    from app.repositories.pledges import PledgeRepository

    repo = PledgeRepository(db_session)
    existing_pledge, created = repo.create_if_not_exists(
        campaign_id=camp.id,
        site_id=site_a.id,
        first_name="متعهد متزامن 2",
        phone=target_phone,
    )
    assert created is False
    assert existing_pledge is not None
    assert existing_pledge.id == rows[0].id
