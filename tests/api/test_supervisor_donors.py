"""Integration tests for Supervisor Donors and Field Dashboard API endpoints (P5.1).

Per:
- docs/04-api.md §11 (Supervisor Context and Donors API)
- docs/06-business-rules.md §6 (BR-DONOR-01..18)
- docs/06-business-rules.md §9.3 (BR-COUNT-07)
- docs/07-test-cases.md §5.5 (T-DONOR-09, T-DONOR-14, T-DONOR-17, T-CAMP-16)
- CLAUDE.md / GEMINI.md Rule 6: Golden Rules for Security and Privacy
"""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    generate_csrf_token,
    get_clock,
    get_settings,
)
from app.models.audit import AuditLog
from app.models.campaign import Campaign, Site
from app.models.donor import Donor
from app.models.photo import FileDeletionQueue, Photo
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.conftest import SENTINEL_PHONE
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.user import create_user

# ==============================================================================
# Test Fixtures & Setup Helpers
# ==============================================================================


@pytest.fixture
def supervisor_test_env(
    db_session: Session,
) -> dict[str, object]:
    """Provision a complete environment with 2 sites, a live campaign, and assigned supervisors."""
    # 1. Create two participating physical sites
    site_a = create_site(db_session, name="نادي الطب أ")
    site_b = create_site(db_session, name="نادي الطب ب")

    # 2. Create a live current published campaign
    live_campaign = create_campaign(
        db_session,
        title="حملة اليوم الموحد نوفمبر 2026",
        slug="unified-nov-2026",
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(db_session, campaign_id=live_campaign.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=live_campaign.id, site_id=site_b.id)

    # 3. Create supervisors for Site A and Site B
    sup_a = create_user(
        db_session,
        email="sup_a@bloodday.local",
        full_name="مشرف الموقع أ",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
    )
    sup_a2 = create_user(
        db_session,
        email="sup_a2@bloodday.local",
        full_name="مشرف ثانٍ للموقع أ",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
    )
    sup_b = create_user(
        db_session,
        email="sup_b@bloodday.local",
        full_name="مشرف الموقع ب",
        role="supervisor",
        site_id=site_b.id,
        is_active=True,
    )
    sup_disabled = create_user(
        db_session,
        email="sup_disabled@bloodday.local",
        full_name="مشرف معطل",
        role="supervisor",
        site_id=site_a.id,
        is_active=False,
    )
    admin_user = create_user(
        db_session,
        email="admin@bloodday.local",
        full_name="مسؤول النظام",
        role="admin",
        site_id=None,
        is_active=True,
        totp_enabled=True,
    )

    return {
        "site_a": site_a,
        "site_b": site_b,
        "campaign": live_campaign,
        "sup_a": sup_a,
        "sup_a2": sup_a2,
        "sup_b": sup_b,
        "sup_disabled": sup_disabled,
        "admin": admin_user,
    }


def authenticate_client(
    test_client: TestClient,
    db_session: Session,
    user: User,
) -> dict[str, str]:
    """Attach session cookie and return CSRF token header for an authenticated user."""
    settings = get_settings()
    clock = get_clock()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    raw_token, _ = session_service.create_session(
        user.id,
        user.role,
        mfa_verified=True,
    )

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    test_client.cookies.set(settings.cookie_name, raw_token)
    return {"X-CSRF-Token": csrf_token}


# ==============================================================================
# 1. T-DONOR-14: Phone Number Privacy (List vs Detail)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-14")
def test_t_donor_14_a_list_strictly_omits_phone_numbers(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-14-a ★: GET /supervisor/donors MUST NOT contain 'phone' in any item."""
    sup_a = supervisor_test_env["sup_a"]
    site_a = supervisor_test_env["site_a"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(site_a, Site) and isinstance(campaign, Campaign)

    # Seed donor with phone
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="سارة",
        last_name="بن علي",
        phone=SENTINEL_PHONE,
        blood_type="O+",
        status="attended",
        consent_keep_phone=True,
    )

    headers = authenticate_client(test_client, db_session, sup_a)
    resp = test_client.get("/api/v1/supervisor/donors", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    items = data["items"]
    assert len(items) == 1
    item = items[0]

    # Verify that phone key is completely absent
    assert "phone" not in item, "CRITICAL PRIVACY VIOLATION: Phone leaked in donor list!"
    assert SENTINEL_PHONE not in resp.text, "Sentinel phone leaked in HTTP response body!"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-14")
def test_t_donor_14_b_detail_includes_phone_for_assigned_site(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-14-b ★: GET /supervisor/donors/{id} includes phone for assigned site supervisor."""
    sup_a = supervisor_test_env["sup_a"]
    site_a = supervisor_test_env["site_a"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(site_a, Site) and isinstance(campaign, Campaign)

    donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="أحمد",
        phone=SENTINEL_PHONE,
        blood_type="A+",
        status="donated",
        consent_keep_phone=True,
    )

    headers = authenticate_client(test_client, db_session, sup_a)
    resp = test_client.get(f"/api/v1/supervisor/donors/{donor.id}", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["id"] == str(donor.id)
    assert data["first_name"] == "أحمد"
    assert data["phone"] == SENTINEL_PHONE


# ==============================================================================
# 2. T-DONOR-17: Name Search & Site Isolation in Search
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-DONOR-17")
def test_t_donor_17_a_name_prefix_search_case_insensitive(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-17-a: Prefix search matches Arabic and Latin names case-insensitively."""
    sup_a = supervisor_test_env["sup_a"]
    site_a = supervisor_test_env["site_a"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(site_a, Site) and isinstance(campaign, Campaign)

    # Seed donors at Site A
    create_donor(db_session, campaign_id=campaign.id, site_id=site_a.id, first_name="سارة")
    create_donor(db_session, campaign_id=campaign.id, site_id=site_a.id, first_name="سامي")
    create_donor(db_session, campaign_id=campaign.id, site_id=site_a.id, first_name="ليلى")
    create_donor(db_session, campaign_id=campaign.id, site_id=site_a.id, first_name="Sarah")

    headers = authenticate_client(test_client, db_session, sup_a)

    # 1. Arabic prefix search
    resp = test_client.get("/api/v1/supervisor/donors?q=سا", headers=headers)
    assert resp.status_code == 200
    names = {item["first_name"] for item in resp.json()["items"]}
    assert "سارة" in names
    assert "سامي" in names
    assert "ليلى" not in names
    assert "Sarah" not in names

    # 2. Latin case-insensitive prefix search (q=SA matches Sarah)
    resp_latin = test_client.get("/api/v1/supervisor/donors?q=SA", headers=headers)
    assert resp_latin.status_code == 200
    latin_names = {item["first_name"] for item in resp_latin.json()["items"]}
    assert "Sarah" in latin_names
    assert "سارة" not in latin_names


@pytest.mark.api
@pytest.mark.br("BR-DONOR-17")
def test_t_donor_17_b_search_does_not_leak_other_sites(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-17-b: Name existing at Site B returns 0 results for Supervisor at Site A."""
    sup_a = supervisor_test_env["sup_a"]
    site_b = supervisor_test_env["site_b"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(site_b, Site) and isinstance(campaign, Campaign)

    # Donor exists at Site B only
    create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_b.id,
        first_name="فريد",
    )

    headers = authenticate_client(test_client, db_session, sup_a)
    resp = test_client.get("/api/v1/supervisor/donors?q=فريد", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert len(resp.json()["items"]) == 0


# ==============================================================================
# 3. T-COUNT-07: Real-Time Counters for Assigned Site Only
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-COUNT-07")
def test_t_count_07_a_supervisor_donors_summary_counts(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-COUNT-07-a: Summary returns present and donated counters for supervisor's site only."""
    sup_a = supervisor_test_env["sup_a"]
    site_a = supervisor_test_env["site_a"]
    site_b = supervisor_test_env["site_b"]
    campaign = supervisor_test_env["campaign"]
    assert (
        isinstance(sup_a, User)
        and isinstance(site_a, Site)
        and isinstance(site_b, Site)
        and isinstance(campaign, Campaign)
    )

    # Site A: 3 attended, 2 donated (present = 5, donated = 2)
    for _ in range(3):
        create_donor(db_session, campaign_id=campaign.id, site_id=site_a.id, status="attended")
    for _ in range(2):
        create_donor(db_session, campaign_id=campaign.id, site_id=site_a.id, status="donated")

    # Site B: 10 donated (must not leak into Site A summary)
    for _ in range(10):
        create_donor(db_session, campaign_id=campaign.id, site_id=site_b.id, status="donated")

    headers = authenticate_client(test_client, db_session, sup_a)
    resp = test_client.get("/api/v1/supervisor/donors/summary", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["present"] == 5
    assert data["donated"] == 2


@pytest.mark.api
@pytest.mark.br("BR-COUNT-07")
def test_supervisor_context_endpoint(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """GET /supervisor/context returns campaign info, site info, and summary counts."""
    sup_a = supervisor_test_env["sup_a"]
    site_a = supervisor_test_env["site_a"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(site_a, Site) and isinstance(campaign, Campaign)

    create_donor(db_session, campaign_id=campaign.id, site_id=site_a.id, status="donated")

    headers = authenticate_client(test_client, db_session, sup_a)
    resp = test_client.get("/api/v1/supervisor/context", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["campaign"]["id"] == str(campaign.id)
    assert data["campaign"]["title"] == campaign.title
    assert data["campaign"]["status"] == "live"
    assert data["site"]["id"] == str(site_a.id)
    assert data["site"]["name"] == site_a.name
    assert data["summary"]["present"] == 1
    assert data["summary"]["donated"] == 1


# ==============================================================================
# 4. T-DONOR-09: Isolation & 404 Behavior (Never 403)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-09")
def test_t_donor_09_a_accessing_donor_from_other_site_returns_404(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-09-a ★: Supervisor A accessing donor at Site B returns 404 not_found, NEVER 403."""
    sup_a = supervisor_test_env["sup_a"]
    site_b = supervisor_test_env["site_b"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(site_b, Site) and isinstance(campaign, Campaign)

    donor_b = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_b.id,
        first_name="طارق",
        phone="0550000000",
        status="attended",
    )

    headers = authenticate_client(test_client, db_session, sup_a)
    resp = test_client.get(f"/api/v1/supervisor/donors/{donor_b.id}", headers=headers)

    # MUST be 404 not_found to avoid leaking existence across sites
    assert resp.status_code == 404, f"Expected 404 but got {resp.status_code}: {resp.text}"
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-09")
def test_t_donor_09_b_list_shows_only_assigned_site(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-09-b ★: GET /supervisor/donors shows records of assigned site only."""
    sup_a = supervisor_test_env["sup_a"]
    site_a = supervisor_test_env["site_a"]
    site_b = supervisor_test_env["site_b"]
    campaign = supervisor_test_env["campaign"]
    assert (
        isinstance(sup_a, User)
        and isinstance(site_a, Site)
        and isinstance(site_b, Site)
        and isinstance(campaign, Campaign)
    )

    create_donor(db_session, campaign_id=campaign.id, site_id=site_a.id, first_name="متبرع موقع أ")
    create_donor(db_session, campaign_id=campaign.id, site_id=site_b.id, first_name="متبرع موقع ب")

    headers = authenticate_client(test_client, db_session, sup_a)
    resp = test_client.get("/api/v1/supervisor/donors", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["first_name"] == "متبرع موقع أ"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-09")
def test_t_donor_09_c_second_supervisor_same_site_succeeds(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-09-c ★: Second active supervisor at Site A (sup_a2) accesses Site A donor with 200."""
    sup_a2 = supervisor_test_env["sup_a2"]
    site_a = supervisor_test_env["site_a"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a2, User) and isinstance(site_a, Site) and isinstance(campaign, Campaign)

    donor_a = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_a.id,
        first_name="متبرع مشترك",
    )

    headers = authenticate_client(test_client, db_session, sup_a2)
    resp = test_client.get(f"/api/v1/supervisor/donors/{donor_a.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "متبرع مشترك"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-09")
def test_t_donor_09_d_error_response_indistinguishable_from_nonexistent(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-09-d ★: Responses for foreign site donors are 100% indistinguishable from nonexistent donors (never 403)."""
    sup_a = supervisor_test_env["sup_a"]
    site_b = supervisor_test_env["site_b"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(site_b, Site) and isinstance(campaign, Campaign)

    # Donor exists at foreign site B
    donor_b = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_b.id,
        first_name="متبرع موقع ب",
        status="attended",
    )
    foreign_id = donor_b.id
    nonexistent_id = uuid.uuid4()

    headers = authenticate_client(test_client, db_session, sup_a)

    actions = [
        (
            "GET",
            f"/api/v1/supervisor/donors/{foreign_id}",
            f"/api/v1/supervisor/donors/{nonexistent_id}",
            None,
        ),
        (
            "PATCH",
            f"/api/v1/supervisor/donors/{foreign_id}",
            f"/api/v1/supervisor/donors/{nonexistent_id}",
            {"first_name": "تعديل"},
        ),
        (
            "PUT",
            f"/api/v1/supervisor/donors/{foreign_id}/status",
            f"/api/v1/supervisor/donors/{nonexistent_id}/status",
            {"status": "donated"},
        ),
        (
            "DELETE",
            f"/api/v1/supervisor/donors/{foreign_id}",
            f"/api/v1/supervisor/donors/{nonexistent_id}",
            None,
        ),
    ]

    for method, foreign_url, nonexistent_url, payload in actions:
        if method == "GET":
            resp_foreign = test_client.get(foreign_url, headers=headers)
            resp_nonexistent = test_client.get(nonexistent_url, headers=headers)
        elif method == "PATCH":
            resp_foreign = test_client.patch(foreign_url, json=payload, headers=headers)
            resp_nonexistent = test_client.patch(nonexistent_url, json=payload, headers=headers)
        elif method == "PUT":
            resp_foreign = test_client.put(foreign_url, json=payload, headers=headers)
            resp_nonexistent = test_client.put(nonexistent_url, json=payload, headers=headers)
        elif method == "DELETE":
            resp_foreign = test_client.delete(foreign_url, headers=headers)
            resp_nonexistent = test_client.delete(nonexistent_url, headers=headers)

        # 1. Both must return exact 404 (NEVER 403)
        assert resp_foreign.status_code == 404, (
            f"{method} foreign site donor returned {resp_foreign.status_code}, expected 404"
        )
        assert resp_nonexistent.status_code == 404, (
            f"{method} nonexistent donor returned {resp_nonexistent.status_code}, expected 404"
        )

        # 2. Both must return identical error code 'not_found'
        body_foreign = resp_foreign.json()
        body_nonexistent = resp_nonexistent.json()
        assert body_foreign["error"]["code"] == "not_found"
        assert body_nonexistent["error"]["code"] == "not_found"

        # 3. Message structure must be identical (no leaking of existence)
        assert body_foreign["error"]["message"] == f"Donor with ID '{foreign_id}' not found."
        assert (
            body_nonexistent["error"]["message"] == f"Donor with ID '{nonexistent_id}' not found."
        )


# ==============================================================================
# 5. T-CAMP-16: Campaign Lifecycle Gate (Only Live Campaign)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CAMP-16")
def test_t_camp_16_a_field_access_fails_when_campaign_not_live(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CAMP-16-a ★: Supervisor field operations return 409 no_live_campaign when status is upcoming."""
    sup_a = supervisor_test_env["sup_a"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(campaign, Campaign)

    # Change campaign status from live to upcoming
    campaign.status = "upcoming"
    db_session.flush()

    headers = authenticate_client(test_client, db_session, sup_a)

    for endpoint in [
        "/api/v1/supervisor/context",
        "/api/v1/supervisor/donors/summary",
        "/api/v1/supervisor/donors",
        f"/api/v1/supervisor/donors/{uuid.uuid4()}",
    ]:
        resp = test_client.get(endpoint, headers=headers)
        assert resp.status_code == 409, f"Endpoint {endpoint} returned {resp.status_code}"
        assert resp.json()["error"]["code"] == "no_live_campaign"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CAMP-16")
def test_t_camp_16_b_field_access_fails_after_campaign_ended(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CAMP-16-b ★: Supervisor field operations return 409 no_live_campaign after campaign ends."""
    sup_a = supervisor_test_env["sup_a"]
    campaign = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User) and isinstance(campaign, Campaign)

    campaign.status = "ended"
    db_session.flush()

    headers = authenticate_client(test_client, db_session, sup_a)
    resp = test_client.get("/api/v1/supervisor/donors", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_live_campaign"


# ==============================================================================
# 6. Access Control & Canonical Roles
# ==============================================================================


@pytest.mark.api
def test_supervisor_endpoints_require_authentication(test_client: TestClient) -> None:
    """Anonymous visitors receive 401 not_authenticated on supervisor endpoints."""
    resp = test_client.get("/api/v1/supervisor/context")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.api
def test_deactivated_supervisor_receives_401(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """Deactivated supervisor cannot access supervisor endpoints."""
    sup_disabled = supervisor_test_env["sup_disabled"]
    assert isinstance(sup_disabled, User)

    headers = authenticate_client(test_client, db_session, sup_disabled)
    resp = test_client.get("/api/v1/supervisor/context", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.api
def test_admin_receives_403_on_supervisor_endpoints(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """Admin calling supervisor endpoints receives 403 forbidden (admin has no site context)."""
    admin_user = supervisor_test_env["admin"]
    assert isinstance(admin_user, User)

    headers = authenticate_client(test_client, db_session, admin_user)
    resp = test_client.get("/api/v1/supervisor/context", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# ==============================================================================
# 7. Donor Creation, Scope Derivation & Idempotency (P5.2)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-01")
def test_br_donor_01_a_anon_cannot_create(test_client: TestClient) -> None:
    """T-DONOR-01-a ★: Anonymous visitors receive 401 when attempting to create a donor."""
    resp = test_client.post(
        "/api/v1/supervisor/donors",
        json={"first_name": "سارة", "consent_hero_name": True},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-01")
@pytest.mark.br("BR-DONOR-02")
@pytest.mark.br("BR-DONOR-03")
def test_br_donor_01_b_and_02_a_and_03_a_sup_creates_donor(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-01-b ★ / T-DONOR-02-a ★ / T-DONOR-03-a:

    Supervisor creates a donor during live campaign:
    - Returns 201 Created.
    - Scope fields (campaign_id, site_id, registered_by) strictly derived from context.
    - Initial status is 'attended' and donated_at is None.
    """
    sup_a = supervisor_test_env["sup_a"]
    site_a = supervisor_test_env["site_a"]
    live_camp = supervisor_test_env["campaign"]
    assert isinstance(sup_a, User)
    assert isinstance(site_a, Site)
    assert isinstance(live_camp, Campaign)

    headers = authenticate_client(test_client, db_session, sup_a)
    payload = {
        "first_name": "سارة",
        "last_name": "بن علي",
        "phone": "0550123456",
        "blood_type": "A+",
        "consent_keep_phone": True,
        "consent_hero_name": True,
        "consent_photo": False,
        "consent_public_phone": False,
    }

    resp = test_client.post("/api/v1/supervisor/donors", json=payload, headers=headers)
    assert resp.status_code == 201
    data = resp.json()

    # Derived context attributes (BR-DONOR-02 ★)
    assert data["campaign_id"] == str(live_camp.id)
    assert data["site_id"] == str(site_a.id)
    assert data["registered_by"] == str(sup_a.id)

    # Initial attendance status (BR-DONOR-03)
    assert data["status"] == "attended"
    assert data["donated_at"] is None

    # Payload attributes
    assert data["first_name"] == "سارة"
    assert data["last_name"] == "بن علي"
    assert data["phone"] == "0550123456"
    assert data["blood_type"] == "A+"
    assert data["consent_keep_phone"] is True
    assert data["consent_hero_name"] is True
    assert data["consent_photo"] is False
    assert data["consent_public_phone"] is False

    # Verify persistence in database
    donor = db_session.query(Donor).filter(Donor.id == uuid.UUID(data["id"])).first()
    assert donor is not None
    assert donor.campaign_id == live_camp.id
    assert donor.site_id == site_a.id
    assert donor.registered_by == sup_a.id
    assert donor.status == "attended"
    assert donor.donated_at is None


@pytest.mark.api
@pytest.mark.br("BR-DONOR-04")
def test_br_donor_04_a_validation_errors(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-04-a: Validate constraint failures on first_name, last_name, and blood_type."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    # 1. Empty first_name
    r1 = test_client.post(
        "/api/v1/supervisor/donors",
        json={"first_name": "   "},
        headers=headers,
    )
    assert r1.status_code == 422
    assert r1.json()["error"]["code"] == "validation_error"

    # 2. first_name exceeding 40 chars
    r2 = test_client.post(
        "/api/v1/supervisor/donors",
        json={"first_name": "أ" * 41},
        headers=headers,
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "validation_error"

    # 3. last_name exceeding 40 chars
    r3 = test_client.post(
        "/api/v1/supervisor/donors",
        json={"first_name": "سارة", "last_name": "ب" * 41},
        headers=headers,
    )
    assert r3.status_code == 422
    assert r3.json()["error"]["code"] == "validation_error"

    # 4. Invalid blood_type (e.g. C+)
    r4 = test_client.post(
        "/api/v1/supervisor/donors",
        json={"first_name": "سارة", "blood_type": "C+"},
        headers=headers,
    )
    assert r4.status_code == 422
    assert r4.json()["error"]["code"] == "validation_error"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-05")
def test_br_donor_05_a_client_id_created_201(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-05-a: Client-supplied UUID is used as the primary key of the created donor."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    custom_id = uuid.uuid4()
    payload = {
        "id": str(custom_id),
        "first_name": "ياسمين",
        "consent_hero_name": True,
    }

    resp = test_client.post("/api/v1/supervisor/donors", json=payload, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["id"] == str(custom_id)

    db_record = db_session.query(Donor).filter(Donor.id == custom_id).first()
    assert db_record is not None
    assert db_record.first_name == "ياسمين"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-05")
def test_br_donor_05_b_identical_replay_returns_200_single_row(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-05-b: Idempotent replay with same UUID and identical payload returns 200 OK without duplication."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    client_id = uuid.uuid4()
    payload = {
        "id": str(client_id),
        "first_name": "طارق",
        "last_name": "عمراني",
        "phone": "0550998877",
        "blood_type": "O+",
        "consent_keep_phone": True,
        "consent_hero_name": True,
        "consent_photo": False,
        "consent_public_phone": False,
    }

    # Initial submission: 201 Created
    r1 = test_client.post("/api/v1/supervisor/donors", json=payload, headers=headers)
    assert r1.status_code == 201
    data1 = r1.json()

    # Replay identical submission: 200 OK (BR-DONOR-05)
    r2 = test_client.post("/api/v1/supervisor/donors", json=payload, headers=headers)
    assert r2.status_code == 200
    data2 = r2.json()

    assert data1["id"] == data2["id"]
    assert data1["first_name"] == data2["first_name"]
    assert data1["phone"] == data2["phone"]

    # Verify strictly 1 row in the database
    count = db_session.query(Donor).filter(Donor.id == client_id).count()
    assert count == 1


@pytest.mark.api
@pytest.mark.br("BR-DONOR-05")
def test_br_donor_05_c_same_id_from_other_supervisor_returns_409(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-05-c: Same UUID submitted by a different supervisor/site returns 409 duplicate_id_conflict."""
    sup_a = supervisor_test_env["sup_a"]
    sup_b = supervisor_test_env["sup_b"]
    assert isinstance(sup_a, User)
    assert isinstance(sup_b, User)

    client_id = uuid.uuid4()
    payload = {
        "id": str(client_id),
        "first_name": "عادل",
        "consent_hero_name": False,
    }

    # Supervisor A creates donor
    h_a = authenticate_client(test_client, db_session, sup_a)
    r_a = test_client.post("/api/v1/supervisor/donors", json=payload, headers=h_a)
    assert r_a.status_code == 201

    # Supervisor B attempts to use the same UUID -> 409 Conflict
    h_b = authenticate_client(test_client, db_session, sup_b)
    r_b = test_client.post("/api/v1/supervisor/donors", json=payload, headers=h_b)
    assert r_b.status_code == 409
    assert r_b.json()["error"]["code"] == "duplicate_id_conflict"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-05")
def test_br_donor_05_d_same_id_with_different_data_returns_409(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-05-d: Same UUID submitted with differing content returns 409 duplicate_id_conflict."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    client_id = uuid.uuid4()
    payload1 = {
        "id": str(client_id),
        "first_name": "خالد",
        "consent_hero_name": False,
    }
    r1 = test_client.post("/api/v1/supervisor/donors", json=payload1, headers=headers)
    assert r1.status_code == 201

    # Replay with different first_name
    payload2 = {
        "id": str(client_id),
        "first_name": "سمير",
        "consent_hero_name": False,
    }
    r2 = test_client.post("/api/v1/supervisor/donors", json=payload2, headers=headers)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "duplicate_id_conflict"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-GEN-07")
def test_br_gen_07_a_reserved_fields_rejected_with_422_and_audited(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T-GEN-07-a ★:

    Submitting reserved fields (site_id, campaign_id, registered_by, status, donated_at)
    each separately returns 422 unknown_field, creates no record, and logs security.forbidden_field.
    """
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    reserved_payloads = [
        {"site_id": str(uuid.uuid4())},
        {"campaign_id": str(uuid.uuid4())},
        {"registered_by": str(uuid.uuid4())},
        {"status": "donated"},
        {"donated_at": "2026-11-20T10:00:00Z"},
    ]

    for reserved_inject in reserved_payloads:
        caplog.clear()
        initial_count = db_session.query(Donor).count()

        payload = {
            "first_name": "متبرع تجريبي",
            **reserved_inject,
        }

        resp = test_client.post("/api/v1/supervisor/donors", json=payload, headers=headers)
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "unknown_field"

        # Verify no row created in DB
        assert db_session.query(Donor).count() == initial_count

        # Verify security audit event logged
        forbidden_field_name = next(iter(reserved_inject.keys()))
        matching_logs = [
            record
            for record in caplog.records
            if "security.forbidden_field" in record.message
            and forbidden_field_name in record.message
        ]
        assert len(matching_logs) > 0, (
            f"Expected security.forbidden_field log for {forbidden_field_name}"
        )


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-02")
@pytest.mark.br("BR-CONSENT-03")
def test_br_consent_02_and_03_enforced_at_creation(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-02-a ★ / T-CONSENT-03-a ★: Enforce consent rules at creation boundary.

    - BR-CONSENT-02: Phone without consent_keep_phone or consent_public_phone returns 422 phone_requires_consent.
    - BR-CONSENT-03: consent_public_phone requires phone and blood_type -> 422 public_phone_requires_data.
    """
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    # 1. Phone provided without either consent -> 422 phone_requires_consent
    r1 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "كريم",
            "phone": "0550123456",
            "consent_keep_phone": False,
            "consent_public_phone": False,
        },
        headers=headers,
    )
    assert r1.status_code == 422
    assert r1.json()["error"]["code"] == "phone_requires_consent"

    # 2. consent_public_phone=True without phone -> 422 public_phone_requires_data
    r2 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "كريم",
            "consent_public_phone": True,
            "blood_type": "O+",
        },
        headers=headers,
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "public_phone_requires_data"

    # 3. consent_public_phone=True with phone but missing blood_type -> 422 public_phone_requires_data
    r3 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "كريم",
            "phone": "0550123456",
            "consent_public_phone": True,
            "blood_type": None,
        },
        headers=headers,
    )
    assert r3.status_code == 422
    assert r3.json()["error"]["code"] == "public_phone_requires_data"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-18")
def test_br_donor_18_a_audit_log_recorded_zero_pii(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-18-a: Creating a donor records a donor.create audit entry strictly containing zero PII."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    sentinel_phone = SENTINEL_PHONE
    resp = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "محمود",
            "last_name": "دريدي",
            "phone": sentinel_phone,
            "blood_type": "B+",
            "consent_keep_phone": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    donor_id = uuid.UUID(resp.json()["id"])

    audit_entry = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == donor_id, AuditLog.action == "donor.create")
        .first()
    )
    assert audit_entry is not None
    assert audit_entry.actor_user_id == sup_a.id
    assert audit_entry.entity_type == "donor"

    details = audit_entry.details or {}
    assert "first_name" not in details
    assert "last_name" not in details
    assert "phone" not in details
    assert "blood_type" not in details
    assert sentinel_phone not in str(details)


@pytest.mark.api
@pytest.mark.br("BR-DONOR-05")
def test_t_conc_04_concurrent_donor_creation_idempotency(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONC-04: Idempotent donor creation with same client UUID guarantees single record."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    client_id = uuid.uuid4()
    payload = {
        "id": str(client_id),
        "first_name": "إلياس",
        "blood_type": "AB+",
        "consent_hero_name": True,
    }

    # First request creates (201)
    res1 = test_client.post("/api/v1/supervisor/donors", json=payload, headers=headers)
    assert res1.status_code == 201

    # Second request with identical ID & payload returns 200
    res2 = test_client.post("/api/v1/supervisor/donors", json=payload, headers=headers)
    assert res2.status_code == 200

    # Exactly 1 row in database
    records = db_session.query(Donor).filter(Donor.id == client_id).all()
    assert len(records) == 1


# ==============================================================================
# P5.3 Tests: PATCH /supervisor/donors/{id}, Consent Truth Table, and Cleanup
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-02")
def test_t_consent_02_a_truth_table_all_layers(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-02-a ★: Enforce consent truth table (docs/06 §7.2) across API and DB layers.

    Combinations:
    1. keep=F, public=F, phone=None -> Valid (201)
    2. keep=F, public=F, phone="0550123456" -> Invalid (422 phone_requires_consent)
    3. keep=T, public=F, phone="0550123456" -> Valid (201)
    4. keep=T, public=F, phone=None -> Valid (201)
    5. keep=F, public=T, phone="0550123456", blood_type="O+" -> Valid (201)
    6. keep=T, public=T, phone="0550123456", blood_type="O+" -> Valid (201)
    """
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    # 1. (F, F, None) -> Valid
    r1 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سفيان",
            "phone": None,
            "consent_keep_phone": False,
            "consent_public_phone": False,
        },
        headers=headers,
    )
    assert r1.status_code == 201

    # 2. (F, F, phone) -> 422 phone_requires_consent
    r2 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سفيان",
            "phone": "0550123456",
            "consent_keep_phone": False,
            "consent_public_phone": False,
        },
        headers=headers,
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "phone_requires_consent"

    # 3. (T, F, phone) -> Valid
    r3 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سفيان",
            "phone": "0550123456",
            "consent_keep_phone": True,
            "consent_public_phone": False,
        },
        headers=headers,
    )
    assert r3.status_code == 201

    # 4. (T, F, None) -> Valid
    r4 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سفيان",
            "phone": None,
            "consent_keep_phone": True,
            "consent_public_phone": False,
        },
        headers=headers,
    )
    assert r4.status_code == 201

    # 5. (F, T, phone + blood_type) -> Valid
    r5 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سفيان",
            "phone": "0550123456",
            "blood_type": "O+",
            "consent_keep_phone": False,
            "consent_public_phone": True,
        },
        headers=headers,
    )
    assert r5.status_code == 201

    # 6. (T, T, phone + blood_type) -> Valid
    r6 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سفيان",
            "phone": "0550123456",
            "blood_type": "O+",
            "consent_keep_phone": True,
            "consent_public_phone": True,
        },
        headers=headers,
    )
    assert r6.status_code == 201


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-03")
def test_t_consent_03_a_public_phone_requires_phone_and_blood_type(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-03-a ★: consent_public_phone requires both phone and blood_type (422 and DB check)."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    # Missing blood type
    r1 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "طارق",
            "phone": "0550123456",
            "blood_type": None,
            "consent_public_phone": True,
        },
        headers=headers,
    )
    assert r1.status_code == 422
    assert r1.json()["error"]["code"] == "public_phone_requires_data"

    # Missing phone
    r2 = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "طارق",
            "phone": None,
            "blood_type": "A+",
            "consent_public_phone": True,
        },
        headers=headers,
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "public_phone_requires_data"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-04")
def test_t_consent_04_a_final_state_evaluation_rejects_withdrawing_keep_without_null_phone(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-04-a ★: Withdrawing keep consent without explicit phone: null rejected per final state evaluation."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    # Create dA5 with phone, keep=True, public=False
    resp = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سارة",
            "phone": "0550123456",
            "consent_keep_phone": True,
            "consent_public_phone": False,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    donor_id = resp.json()["id"]

    # PATCH withdrawing keep without mentioning phone -> 422 phone_requires_consent
    patch_resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor_id}",
        json={"consent_keep_phone": False},
        headers=headers,
    )
    assert patch_resp.status_code == 422
    assert patch_resp.json()["error"]["code"] == "phone_requires_consent"

    # Verify donor phone is NOT silently cleared in database (BR-CONSENT-06)
    donor = db_session.query(Donor).filter(Donor.id == uuid.UUID(donor_id)).first()
    assert donor is not None
    assert donor.phone == "0550123456"
    assert donor.consent_keep_phone is True


@pytest.mark.api
@pytest.mark.br("BR-CONSENT-04")
def test_t_consent_04_b_final_state_clearing_blood_type_with_public_consent_rejected(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-04-b: Clearing blood_type when consent_public_phone is True rejected with 422."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    # Create dA4 with phone, blood_type="A+", public=True
    resp = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "أمين",
            "phone": "0550123456",
            "blood_type": "A+",
            "consent_public_phone": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    donor_id = resp.json()["id"]

    # PATCH blood_type: null -> 422 public_phone_requires_data
    patch_resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor_id}",
        json={"blood_type": None},
        headers=headers,
    )
    assert patch_resp.status_code == 422
    assert patch_resp.json()["error"]["code"] == "public_phone_requires_data"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-05")
def test_t_consent_05_a_withdrawing_hero_and_public_consent(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-05-a ★: Withdrawing hero and public consent immediately takes effect."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    resp = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سفيان",
            "phone": "0550123456",
            "blood_type": "B+",
            "consent_hero_name": True,
            "consent_public_phone": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    donor_id = resp.json()["id"]

    patch_resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor_id}",
        json={
            "consent_hero_name": False,
            "consent_public_phone": False,
            "consent_keep_phone": True,
        },
        headers=headers,
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["consent_hero_name"] is False
    assert data["consent_public_phone"] is False
    assert data["consent_keep_phone"] is True


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-06")
def test_t_consent_06_a_withdrawing_keep_with_explicit_phone_null_succeeds(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-06-a ★: Withdrawing keep consent with explicit phone: null succeeds."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    resp = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سارة",
            "phone": "0550123456",
            "consent_keep_phone": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    donor_id = resp.json()["id"]

    patch_resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor_id}",
        json={"consent_keep_phone": False, "phone": None},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["phone"] is None
    assert data["consent_keep_phone"] is False


@pytest.mark.api
@pytest.mark.br("BR-CONSENT-06")
def test_t_consent_06_c_withdrawing_keep_when_public_remains_retains_phone(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-06-c: Withdrawing keep consent retains phone when consent_public_phone is True."""
    sup_a = supervisor_test_env["sup_a"]
    assert isinstance(sup_a, User)
    headers = authenticate_client(test_client, db_session, sup_a)

    resp = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "سارة",
            "phone": "0550123456",
            "blood_type": "AB+",
            "consent_keep_phone": True,
            "consent_public_phone": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    donor_id = resp.json()["id"]

    patch_resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor_id}",
        json={"consent_keep_phone": False},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["phone"] == "0550123456"
    assert data["consent_keep_phone"] is False
    assert data["consent_public_phone"] is True


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-07")
def test_t_consent_07_a_withdrawing_photo_consent_deletes_individual_photos_and_enqueues(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONSENT-07-a ★: Withdrawing photo consent deletes donor photos and enqueues in same transaction."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    # 1. Create donor with consent_photo=True
    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="أحمد",
        consent_photo=True,
    )

    # 2. Add 2 individual photos
    photo1 = Photo(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        storage_key=f"photos/{uuid.uuid4()}.webp",
        kind="individual",
        donor_id=donor.id,
        mime_type="image/webp",
        size_bytes=1024,
    )
    photo2 = Photo(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        storage_key=f"photos/{uuid.uuid4()}.webp",
        kind="individual",
        donor_id=donor.id,
        mime_type="image/webp",
        size_bytes=2048,
    )
    db_session.add_all([photo1, photo2])
    db_session.commit()

    # 3. Withdraw consent_photo via PATCH
    patch_resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor.id}",
        json={"consent_photo": False},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["consent_photo"] is False

    # 4. Verify photos removed from DB
    remaining_photos = db_session.query(Photo).filter(Photo.donor_id == donor.id).all()
    assert len(remaining_photos) == 0

    # 5. Verify file deletion queue entries
    queue_keys = {
        item.storage_key
        for item in db_session.query(FileDeletionQueue)
        .filter(FileDeletionQueue.storage_key.in_([photo1.storage_key, photo2.storage_key]))
        .all()
    }
    assert queue_keys == {photo1.storage_key, photo2.storage_key}


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-09")
def test_t_donor_09_a_supervisor_cannot_patch_foreign_site_donor(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-09-a ★: Supervisor accessing donor outside assigned site returns 404 not_found, NEVER 403."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_b = supervisor_test_env["site_b"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_b, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    # Donor belongs to Site B
    donor_b = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_b.id,
        first_name="نبيل",
    )

    resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor_b.id}",
        json={"first_name": "اسم جديد"},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-10")
def test_t_donor_10_a_reserved_fields_rejected_on_patch(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-10-a ★: Modifying reserved fields on PATCH returns 422 unknown_field."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="يونس",
    )

    reserved_payloads = [
        {"site_id": str(uuid.uuid4())},
        {"campaign_id": str(uuid.uuid4())},
        {"registered_by": str(uuid.uuid4())},
        {"status": "donated"},
        {"donated_at": "2026-11-20T12:00:00Z"},
        {"anonymized_at": "2026-11-20T12:00:00Z"},
        {"role": "admin"},
    ]

    for payload in reserved_payloads:
        resp = test_client.patch(
            f"/api/v1/supervisor/donors/{donor.id}",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "unknown_field"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-15")
def test_t_donor_15_a_patch_anonymized_donor_returns_409(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-15-a: Modifying an anonymized donor record returns 409 campaign_purged."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="محذوف",
    )
    donor.anonymized_at = datetime(2027, 11, 20, 0, 0, 0, tzinfo=UTC)
    db_session.commit()

    resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor.id}",
        json={"first_name": "تعديل"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "campaign_purged"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-16")
def test_t_donor_16_a_patch_when_campaign_not_live_returns_409(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-16-a: Supervisor attempting PATCH after campaign ended returns 409 no_live_campaign."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="حميد",
    )

    # End the campaign
    live_campaign.status = "ended"
    db_session.commit()

    resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor.id}",
        json={"first_name": "تعديل"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_live_campaign"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-16")
def test_t_donor_16_b_patch_admin_on_ended_campaign_succeeds(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-16-b: Admin can PATCH a donor record even after the campaign ended."""
    admin_user = supervisor_test_env["admin"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(admin_user, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, admin_user)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="حميد",
    )

    # End the campaign
    live_campaign.status = "ended"
    db_session.commit()

    # 1. Admin updates donor via admin endpoint: /admin/donors/{id} (BR-DONOR-16)
    resp = test_client.patch(
        f"/api/v1/admin/donors/{donor.id}",
        json={"first_name": "حميد المصحح"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "حميد المصحح"

    # 2. Admin attempting to PATCH via supervisor route gets 403 forbidden (docs/07 §4 line 137)
    resp_sup = test_client.patch(
        f"/api/v1/supervisor/donors/{donor.id}",
        json={"first_name": "محاولة مرفوضة"},
        headers=headers,
    )
    assert resp_sup.status_code == 403
    assert resp_sup.json()["error"]["code"] == "forbidden"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-18")
def test_t_donor_18_a_patch_audit_log_zero_pii(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-18-a: Updating a donor logs donor.update audit event with zero PII in details."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="مراد",
        consent_keep_phone=True,
    )

    sentinel_phone = SENTINEL_PHONE
    resp = test_client.patch(
        f"/api/v1/supervisor/donors/{donor.id}",
        json={"phone": sentinel_phone},
        headers=headers,
    )
    assert resp.status_code == 200

    audit_entry = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == donor.id, AuditLog.action == "donor.update")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit_entry is not None
    assert audit_entry.actor_user_id == sup_a.id
    assert audit_entry.details == {"updated_fields": ["phone"]}
    assert sentinel_phone not in str(audit_entry.details)


# ==============================================================================
# P5.4 API Integration Tests: Status Change, Deletion, and Check-Phone
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-DONOR-06")
@pytest.mark.br("BR-DONOR-07")
def test_t_donor_06_a_status_toggle_both_directions(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-06-a: Status change attended -> donated then back to attended."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="سعيد",
        status="attended",
    )

    # 1. Transition attended -> donated
    resp_donated = test_client.put(
        f"/api/v1/supervisor/donors/{donor.id}/status",
        json={"status": "donated"},
        headers=headers,
    )
    assert resp_donated.status_code == 200
    data_donated = resp_donated.json()
    assert data_donated["status"] == "donated"
    assert data_donated["donated_at"] is not None

    # Verify in DB
    db_session.refresh(donor)
    assert donor.status == "donated"
    assert donor.donated_at is not None

    # 2. Transition donated -> attended
    resp_attended = test_client.put(
        f"/api/v1/supervisor/donors/{donor.id}/status",
        json={"status": "attended"},
        headers=headers,
    )
    assert resp_attended.status_code == 200
    data_attended = resp_attended.json()
    assert data_attended["status"] == "attended"
    assert data_attended["donated_at"] is None

    # Verify in DB
    db_session.refresh(donor)
    assert donor.status == "attended"
    assert donor.donated_at is None


@pytest.mark.api
@pytest.mark.br("BR-DONOR-06")
def test_t_donor_06_b_status_donated_idempotency_preserves_timestamp(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-06-b: Repeated PUT status=donated returns 200 without changing donated_at."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="ماهر",
        status="attended",
    )

    # First PUT
    resp1 = test_client.put(
        f"/api/v1/supervisor/donors/{donor.id}/status",
        json={"status": "donated"},
        headers=headers,
    )
    assert resp1.status_code == 200
    donated_at_first = resp1.json()["donated_at"]
    assert donated_at_first is not None

    # Second PUT with identical status="donated"
    resp2 = test_client.put(
        f"/api/v1/supervisor/donors/{donor.id}/status",
        json={"status": "donated"},
        headers=headers,
    )
    assert resp2.status_code == 200
    donated_at_second = resp2.json()["donated_at"]
    assert donated_at_second == donated_at_first


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-08")
def test_t_donor_08_a_rejected_and_deferred_statuses_rejected_with_422(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-08-a ★: Attempting PUT status=rejected or status=deferred yields 422 validation_error."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="عادل",
        status="attended",
    )

    for invalid_status in ("rejected", "deferred", "cancelled", "unknown"):
        resp = test_client.put(
            f"/api/v1/supervisor/donors/{donor.id}/status",
            json={"status": invalid_status},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-08")
def test_t_donor_08_b_schemas_contain_no_rejection_or_deferral_fields() -> None:
    """T-DONOR-08-b ★: Static inspection of all donor schemas ensures zero rejection/deferral fields."""
    from app.schemas.donor import (
        DonorCreateIn,
        DonorDetailOut,
        DonorListItemOut,
        DonorStatusUpdateIn,
        DonorUpdateIn,
    )

    forbidden_keywords = ("reject", "defer", "ineligible", "medical_reason", "refusal")

    for schema_cls in (
        DonorCreateIn,
        DonorDetailOut,
        DonorListItemOut,
        DonorStatusUpdateIn,
        DonorUpdateIn,
    ):
        for field_name in schema_cls.model_fields:
            for kw in forbidden_keywords:
                assert kw not in field_name.lower(), (
                    f"Forbidden keyword '{kw}' found in field '{field_name}' of schema {schema_cls.__name__}"
                )


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-09")
def test_t_donor_09_a_status_and_delete_foreign_site_returns_404(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-09-a ★: Supervisor accessing foreign site donor for status or delete gets 404, never 403."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_b = supervisor_test_env["site_b"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_b, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor_b = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_b.id,
        first_name="نبيل",
        status="attended",
    )

    # Status update on foreign donor
    resp_status = test_client.put(
        f"/api/v1/supervisor/donors/{donor_b.id}/status",
        json={"status": "donated"},
        headers=headers,
    )
    assert resp_status.status_code == 404
    assert resp_status.json()["error"]["code"] == "not_found"

    # Delete on foreign donor
    resp_delete = test_client.delete(
        f"/api/v1/supervisor/donors/{donor_b.id}",
        headers=headers,
    )
    assert resp_delete.status_code == 404
    assert resp_delete.json()["error"]["code"] == "not_found"

    # Verify donor_b was not modified or deleted
    db_session.refresh(donor_b)
    assert donor_b.status == "attended"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-11")
def test_t_donor_11_a_supervisor_delete_donor_succeeds(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-11-a: Supervisor deletes donor record during live campaign -> 204 No Content."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="جمال",
    )
    donor_id = donor.id

    resp = test_client.delete(
        f"/api/v1/supervisor/donors/{donor_id}",
        headers=headers,
    )
    assert resp.status_code == 204

    # Verify row is deleted
    assert db_session.get(Donor, donor_id) is None


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-11")
@pytest.mark.br("BR-PHOTO-10")
def test_t_donor_11_b_delete_donor_purges_photos_and_enqueues(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-11-b ★: Deleting donor cascades individual photos into file_deletion_queue."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="سفيان",
        consent_photo=True,
    )

    photo1 = Photo(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        donor_id=donor.id,
        storage_key="photos/sofiane_1.webp",
        kind="individual",
        mime_type="image/webp",
        size_bytes=1024,
    )
    photo2 = Photo(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        donor_id=donor.id,
        storage_key="photos/sofiane_2.webp",
        kind="individual",
        mime_type="image/webp",
        size_bytes=2048,
    )
    db_session.add_all([photo1, photo2])
    db_session.commit()

    resp = test_client.delete(
        f"/api/v1/supervisor/donors/{donor.id}",
        headers=headers,
    )
    assert resp.status_code == 204

    # Photos removed
    assert db_session.get(Photo, photo1.id) is None
    assert db_session.get(Photo, photo2.id) is None

    # Keys enqueued in file_deletion_queue
    queued_keys = {
        row.storage_key
        for row in db_session.query(FileDeletionQueue)
        .filter(
            FileDeletionQueue.storage_key.in_(["photos/sofiane_1.webp", "photos/sofiane_2.webp"])
        )
        .all()
    }
    assert queued_keys == {"photos/sofiane_1.webp", "photos/sofiane_2.webp"}


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-CAMP-16")
@pytest.mark.br("BR-DONOR-11")
def test_t_donor_11_c_supervisor_delete_after_campaign_ended_returns_409(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-11-c ★: Supervisor cannot delete donor after campaign has ended -> 409 no_live_campaign."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="ياسين",
    )

    # End the campaign
    live_campaign.status = "ended"
    db_session.commit()

    resp = test_client.delete(
        f"/api/v1/supervisor/donors/{donor.id}",
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_live_campaign"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-13")
def test_t_donor_13_a_b_c_d_check_phone_advisory(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-13: Soft duplicate check advisory (a: exists, b: not exists, c: allows creation, d: body only)."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    phone_num = "0550123456"

    # Case b: Non-registered phone returns exists: false
    resp_b = test_client.post(
        "/api/v1/supervisor/donors/check-phone",
        json={"phone": phone_num},
        headers=headers,
    )
    assert resp_b.status_code == 200
    assert resp_b.json() == {"exists": False}

    # Register donor with that phone
    create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="عصام",
        phone=phone_num,
        consent_keep_phone=True,
    )

    # Case a: Registered phone returns exists: true
    resp_a = test_client.post(
        "/api/v1/supervisor/donors/check-phone",
        json={"phone": phone_num},
        headers=headers,
    )
    assert resp_a.status_code == 200
    assert resp_a.json() == {"exists": True}

    # Case c: Creating duplicate phone is permitted (advisory only per D-11)
    resp_c = test_client.post(
        "/api/v1/supervisor/donors",
        json={
            "first_name": "أخ عصام",
            "phone": phone_num,
            "consent_keep_phone": True,
        },
        headers=headers,
    )
    assert resp_c.status_code == 201
    assert resp_c.json()["first_name"] == "أخ عصام"

    # Case d: Security check: check-phone is POST with phone in body, never in URL
    assert "/0550123456" not in resp_a.request.url.path
    assert not resp_a.request.url.query or b"phone" not in resp_a.request.url.query


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-13")
def test_t_donor_13_e_check_phone_foreign_site_isolation(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-13-e ★: check-phone is strictly isolated to supervisor's site; foreign site phones return exists: false."""
    sup_a = supervisor_test_env["sup_a"]
    sup_b = supervisor_test_env["sup_b"]
    live_campaign = supervisor_test_env["campaign"]
    site_b = supervisor_test_env["site_b"]
    assert isinstance(sup_a, User) and isinstance(sup_b, User)
    assert isinstance(live_campaign, Campaign) and isinstance(site_b, Site)

    foreign_phone = "0550999888"

    # Register donor at Site B with foreign_phone
    create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_b.id,
        first_name="متبرع موقع ب",
        phone=foreign_phone,
        consent_keep_phone=True,
    )

    # 1. Supervisor at Site A checks that phone -> MUST return False (cannot probe Site B)
    headers_sup_a = authenticate_client(test_client, db_session, sup_a)
    resp_sup_a = test_client.post(
        "/api/v1/supervisor/donors/check-phone",
        json={"phone": foreign_phone},
        headers=headers_sup_a,
    )
    assert resp_sup_a.status_code == 200
    assert resp_sup_a.json() == {"exists": False}

    # 2. Supervisor at Site B checks that same phone -> Returns True (exists at Site B)
    headers_sup_b = authenticate_client(test_client, db_session, sup_b)
    resp_sup_b = test_client.post(
        "/api/v1/supervisor/donors/check-phone",
        json={"phone": foreign_phone},
        headers=headers_sup_b,
    )
    assert resp_sup_b.status_code == 200
    assert resp_sup_b.json() == {"exists": True}


@pytest.mark.api
@pytest.mark.br("BR-DONOR-15")
def test_t_donor_15_a_anonymized_donor_immutable_on_status_and_delete(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-15-a: Anonymized donor record rejects status update and deletion with 409 campaign_purged."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="مخفي",
        anonymized_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    resp_status = test_client.put(
        f"/api/v1/supervisor/donors/{donor.id}/status",
        json={"status": "donated"},
        headers=headers,
    )
    assert resp_status.status_code == 409
    assert resp_status.json()["error"]["code"] == "campaign_purged"

    resp_delete = test_client.delete(
        f"/api/v1/supervisor/donors/{donor.id}",
        headers=headers,
    )
    assert resp_delete.status_code == 409
    assert resp_delete.json()["error"]["code"] == "campaign_purged"


@pytest.mark.api
@pytest.mark.br("BR-DONOR-18")
def test_t_donor_18_a_status_and_delete_audit_zero_pii(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-DONOR-18-a: Status change and delete actions log audit events with zero PII."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="حكيم",
        status="attended",
    )

    # 1. Status change audit
    resp_status = test_client.put(
        f"/api/v1/supervisor/donors/{donor.id}/status",
        json={"status": "donated"},
        headers=headers,
    )
    assert resp_status.status_code == 200

    audit_status = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == donor.id, AuditLog.action == "donor.status_change")
        .first()
    )
    assert audit_status is not None
    assert audit_status.actor_user_id == sup_a.id
    assert audit_status.details == {"old_status": "attended", "new_status": "donated"}

    # 2. Delete audit
    donor_id = donor.id
    resp_del = test_client.delete(
        f"/api/v1/supervisor/donors/{donor_id}",
        headers=headers,
    )
    assert resp_del.status_code == 204

    audit_del = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == donor_id, AuditLog.action == "donor.delete")
        .first()
    )
    assert audit_del is not None
    assert audit_del.actor_user_id == sup_a.id
    assert audit_del.details == {
        "site_id": str(site_a.id),
        "campaign_id": str(live_campaign.id),
    }


@pytest.mark.api
@pytest.mark.br("BR-DONOR-07")
def test_t_conc_06_concurrent_status_updates(
    test_client: TestClient,
    db_session: Session,
    supervisor_test_env: dict[str, object],
) -> None:
    """T-CONC-06: Status updates result in consistent status and donated_at (DB check constraint)."""
    sup_a = supervisor_test_env["sup_a"]
    live_campaign = supervisor_test_env["campaign"]
    site_a = supervisor_test_env["site_a"]
    assert isinstance(sup_a, User)
    assert isinstance(live_campaign, Campaign)
    assert isinstance(site_a, Site)
    headers = authenticate_client(test_client, db_session, sup_a)

    donor = create_donor(
        db_session,
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="كمال",
        status="attended",
    )

    # Alternate status updates multiple times
    for status_target in ["donated", "attended", "donated", "attended", "donated"]:
        resp = test_client.put(
            f"/api/v1/supervisor/donors/{donor.id}/status",
            json={"status": status_target},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == status_target
        if status_target == "donated":
            assert data["donated_at"] is not None
        else:
            assert data["donated_at"] is None

    # Check database constraint remains satisfied
    db_session.refresh(donor)
    if donor.status == "donated":
        assert donor.donated_at is not None
    else:
        assert donor.donated_at is None
