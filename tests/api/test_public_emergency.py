"""Integration tests for Public Emergency Donor Board (P7.1).

Per:
- docs/04-api.md §8.1 (GET /public/emergency/donors)
- docs/06-business-rules.md §10 (BR-EMERG-01..04, BR-EMERG-09, BR-EMERG-10, BR-EMERG-11)
- docs/07-test-cases.md §5.9 (T-EMERG-01..04, T-EMERG-09-a, T-EMERG-10-a, T-EMERG-11-a, T-EMERG-11-b)
- docs/11-decisions.md (D-37 rest days, D-62 kill switch)
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import FakeClock, get_clock
from app.core.config import get_settings
from app.core.ratelimit import InMemoryRateLimiter, get_rate_limiter
from app.models.audit import AuditLog, PhoneRevealLog
from app.models.donor import Donor
from app.schemas.emergency import EmergencyDonorItemOut, EmergencyRevealOut
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor


@pytest.fixture
def client(test_client: TestClient) -> TestClient:
    """Anonymous client fixture."""
    return test_client


@pytest.fixture
def emergency_dataset(db_session: Session) -> dict[str, Any]:
    """Provision a rich dataset with sites, campaigns, and donors of various states."""
    site_alpha = create_site(db_session, is_active=True, name="مركز نقل الدم - العاصمة")
    site_beta = create_site(db_session, is_active=True, name="مستشفى باب الواد الجامعي")

    camp = create_campaign(
        db_session,
        slug=f"emergency-camp-{uuid.uuid4().hex[:8]}",
        title="حملة الطوارئ المركزية",
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_alpha.id)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_beta.id)

    now = datetime.now(UTC)

    # 1. da4: Eligible O+, donated 100 days ago (Oldest O+)
    da4 = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_alpha.id,
        first_name="سالم",
        phone="+213550000004",
        blood_type="O+",
        status="donated",
        consent_keep_phone=False,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=True,
        donated_at=now - timedelta(days=100),
    )

    # 2. da2: Eligible O+, donated 40 days ago (Middle O+)
    da2 = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_alpha.id,
        first_name="فاطمة",
        phone="+213550000002",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
        donated_at=now - timedelta(days=40),
    )

    # 3. db1: Eligible O+, donated 20 days ago at site Beta (Newest O+)
    db1 = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_beta.id,
        first_name="يوسف",
        phone="+213550000005",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
        donated_at=now - timedelta(days=20),
    )

    # 4. Ineligible: attended only (not donated), but public=True
    d_attended = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_alpha.id,
        first_name="عمر",
        phone="+213550000001",
        blood_type="O+",
        status="attended",
        consent_keep_phone=True,
        consent_public_phone=False,
    )

    # 5. Ineligible: donated, but public=False
    d_no_consent = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_alpha.id,
        first_name="أمينة",
        phone="+213550000003",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_public_phone=False,
        donated_at=now - timedelta(days=50),
    )

    # 6. Ineligible: donated, public=True, but anonymized/purged
    d_anonymized = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_alpha.id,
        first_name="محذوف",
        phone="+213550000006",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_public_phone=True,
        donated_at=now - timedelta(days=120),
        anonymized_at=now - timedelta(days=5),
    )

    # 7. Eligible A+ donor (different blood type)
    da_plus = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_alpha.id,
        first_name="كريم",
        phone="+213550000007",
        blood_type="A+",
        status="donated",
        consent_keep_phone=True,
        consent_public_phone=True,
        donated_at=now - timedelta(days=30),
    )

    db_session.flush()

    return {
        "campaign": camp,
        "site_alpha": site_alpha,
        "site_beta": site_beta,
        "dA4": da4,
        "dA2": da2,
        "dB1": db1,
        "d_attended": d_attended,
        "d_no_consent": d_no_consent,
        "d_anonymized": d_anonymized,
        "dA_plus": da_plus,
    }


# ==============================================================================
# T-EMERG-01 Test Cases: Eligibility Filter
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-01")
def test_t_emerg_01_a_eligible_donor_appears_in_emergency_list(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-01-a (★): Donor with status=donated, consent_public_phone=True, and active phone appears."""
    res = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res.status_code == 200, res.text
    data = res.json()
    donor_ids = [d["donor_id"] for d in data]

    assert str(emergency_dataset["dA2"].id) in donor_ids
    assert str(emergency_dataset["dA4"].id) in donor_ids
    assert str(emergency_dataset["dB1"].id) in donor_ids


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-01")
def test_t_emerg_01_b_attended_donor_is_absent(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-01-b (★): Donor who attended but did not donate (status=attended) MUST NOT appear."""
    res = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res.status_code == 200, res.text
    donor_ids = [d["donor_id"] for d in data_json(res)]
    assert str(emergency_dataset["d_attended"].id) not in donor_ids


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-01")
def test_t_emerg_01_c_donated_without_public_consent_is_absent(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-01-c (★): Donor who donated but did NOT consent to public phone MUST NOT appear."""
    res = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res.status_code == 200, res.text
    donor_ids = [d["donor_id"] for d in data_json(res)]
    assert str(emergency_dataset["d_no_consent"].id) not in donor_ids


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-01")
def test_t_emerg_01_d_anonymized_donor_is_absent(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-01-d (★): Anonymized / purged donor MUST NOT appear in emergency board."""
    res = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res.status_code == 200, res.text
    donor_ids = [d["donor_id"] for d in data_json(res)]
    assert str(emergency_dataset["d_anonymized"].id) not in donor_ids


# ==============================================================================
# T-EMERG-02 Test Cases: Strict Privacy & Exact Contract
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-02")
def test_t_emerg_02_a_exact_contract_keys_and_no_phone(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-02-a (★): Exact keys in response: donor_id, first_name, blood_type, site_name, days_since_donation.

    STRICT PRIVACY: Absolutely NO phone key or value exists anywhere in the JSON response payload.
    """
    res = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res.status_code == 200, res.text
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 3

    expected_keys = {
        "donor_id",
        "first_name",
        "blood_type",
        "site_name",
        "days_since_donation",
    }
    assert expected_keys == set(EmergencyDonorItemOut.model_fields.keys())

    # Check raw text for sentinel phone numbers
    raw_text = res.text
    assert "+213550000004" not in raw_text
    assert "+213550000002" not in raw_text
    assert "+213550000005" not in raw_text
    assert "phone" not in raw_text.lower()
    assert "last_name" not in raw_text.lower()

    for idx, item in enumerate(data):
        item_keys = set(item.keys())
        assert item_keys == expected_keys, (
            f"Item [{idx}] key mismatch: extra={item_keys - expected_keys}, missing={expected_keys - item_keys}"
        )
        assert "phone" not in item
        assert "last_name" not in item
        assert "status" not in item
        assert "id" not in item
        assert "registered_by" not in item


# ==============================================================================
# T-EMERG-03 Test Cases: Mandatory Parameters & Validation
# ==============================================================================


@pytest.mark.br("BR-EMERG-03")
def test_t_emerg_03_a_missing_or_invalid_blood_type_returns_422(
    client: TestClient,
) -> None:
    """T-EMERG-03-a: Missing blood_type or invalid blood group raises 422 validation_error."""
    # 1. Missing blood_type parameter
    res_missing = client.get("/api/v1/public/emergency/donors")
    assert res_missing.status_code == 422, res_missing.text
    err_missing = res_missing.json()
    assert err_missing["error"]["code"] == "validation_error"

    # 2. Invalid blood group value
    res_invalid = client.get("/api/v1/public/emergency/donors?blood_type=XYZ")
    assert res_invalid.status_code == 422, res_invalid.text
    err_invalid = res_invalid.json()
    assert err_invalid["error"]["code"] == "validation_error"


# ==============================================================================
# T-EMERG-04 Test Cases: Ordering (Oldest First) & Hard Limit of 50
# ==============================================================================


@pytest.mark.br("BR-EMERG-04")
def test_t_emerg_04_a_oldest_donation_first_and_capped_at_50(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-EMERG-04-a: Ordered by oldest donation first (donated_at ASC), capped at 50 results."""
    site = create_site(db_session, is_active=True, name="مركز الاختبار 50")
    camp = create_campaign(
        db_session,
        slug=f"cap-50-camp-{uuid.uuid4().hex[:8]}",
        title="حملة اختبار سقف 50",
        status="live",
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site.id)

    # Clean existing AB- donors to test clean cap
    db_session.query(Donor).filter(Donor.blood_type == "AB-").delete()
    db_session.flush()

    now = datetime.now(UTC)
    # Create 60 eligible AB- donors with staggered donation dates (day 60 down to day 1)
    for i in range(60, 0, -1):
        create_donor(
            db_session,
            campaign_id=camp.id,
            site_id=site.id,
            first_name=f"متبرع {i}",
            phone=f"+213550{i:06d}",
            blood_type="AB-",
            status="donated",
            consent_public_phone=True,
            donated_at=now - timedelta(days=i),
        )
    db_session.flush()

    res = client.get("/api/v1/public/emergency/donors?blood_type=AB-")
    assert res.status_code == 200, res.text
    data = res.json()

    # Exact cap of 50 results
    assert len(data) == 50

    # Oldest donation first: days_since_donation should be DESCENDING (e.g. 60, 59, 58...)
    days = [item["days_since_donation"] for item in data]
    assert days == sorted(days, reverse=True), "Expected oldest donations (highest days) first"
    assert days[0] >= days[-1]
    assert data[0]["first_name"] == "متبرع 60"


# ==============================================================================
# T-EMERG-09 Test Cases: Cache-Control: no-store
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-09")
def test_t_emerg_09_a_no_store_cache_control_header(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-09-a (★): Cache-Control header on emergency list response MUST be 'no-store'."""
    res = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res.status_code == 200, res.text
    cache_control = res.headers.get("Cache-Control", "")
    assert "no-store" in cache_control


# ==============================================================================
# T-EMERG-10 Test Cases: Real-Time Withdrawal of Public Phone Consent
# ==============================================================================


@pytest.mark.br("BR-EMERG-10")
def test_t_emerg_10_a_withdrawal_of_consent_removes_donor_immediately(
    client: TestClient,
    emergency_dataset: dict[str, Any],
    db_session: Session,
) -> None:
    """T-EMERG-10-a: Withdrawing consent_public_phone immediately excludes donor from query results."""
    donor = emergency_dataset["dA4"]
    donor_id_str = str(donor.id)

    # 1. Initially present
    res1 = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res1.status_code == 200
    assert donor_id_str in [d["donor_id"] for d in res1.json()]

    # 2. Withdraw public phone consent
    donor_db = db_session.get(Donor, donor.id)
    assert donor_db is not None
    donor_db.consent_public_phone = False
    donor_db.consent_keep_phone = True
    db_session.flush()

    # 3. Immediately absent
    res2 = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res2.status_code == 200
    assert donor_id_str not in [d["donor_id"] for d in res2.json()]


# ==============================================================================
# T-EMERG-11 Test Cases: Rest Days & days_since_donation Calculation
# ==============================================================================


@pytest.mark.br("BR-EMERG-11")
def test_t_emerg_11_a_days_since_donation_calculation(
    db_session: Session,
    test_app: FastAPI,
) -> None:
    """T-EMERG-11-a: days_since_donation calculated accurately in operational timezone."""
    site = create_site(db_session, is_active=True, name="مركز عين النعجة")
    camp = create_campaign(
        db_session,
        slug=f"tz-camp-{uuid.uuid4().hex[:8]}",
        title="حملة التوقيت المحلي",
        status="live",
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site.id)

    # Fixed base date
    base_time = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    donor = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site.id,
        first_name="زهير",
        phone="+213550999140",
        blood_type="B-",
        status="donated",
        consent_public_phone=True,
        donated_at=base_time,
    )
    db_session.flush()

    # Advance clock by exactly 140 days
    future_time = base_time + timedelta(days=140)
    fake_clock = FakeClock(future_time)

    test_app.dependency_overrides[get_clock] = lambda: fake_clock
    try:
        with TestClient(test_app) as custom_client:
            res = custom_client.get("/api/v1/public/emergency/donors?blood_type=B-")
            assert res.status_code == 200, res.text
            data = res.json()
            match = next(d for d in data if d["donor_id"] == str(donor.id))
            assert match["days_since_donation"] == 140
    finally:
        test_app.dependency_overrides.pop(get_clock, None)


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-11")
def test_t_emerg_11_b_min_days_since_donation_filter(
    db_session: Session,
    test_app: FastAPI,
) -> None:
    """T-EMERG-11-b (★): With EMERGENCY_MIN_DAYS_SINCE_DONATION=90, donors with <90 days are excluded."""
    site = create_site(db_session, is_active=True, name="مركز باب الزوار")
    camp = create_campaign(
        db_session,
        slug=f"min-days-camp-{uuid.uuid4().hex[:8]}",
        title="حملة أيام الراحة",
        status="live",
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site.id)

    now = datetime.now(UTC)

    # Donor A: donated 60 days ago (< 90 days) -> MUST be excluded
    d_60 = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site.id,
        first_name="متبرع ستين",
        phone="+213550000060",
        blood_type="AB+",
        status="donated",
        consent_public_phone=True,
        donated_at=now - timedelta(days=60),
    )

    # Donor B: donated 95 days ago (>= 90 days) -> MUST be included with days_since_donation=95
    d_95 = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site.id,
        first_name="متبرع خمسة وتسعين",
        phone="+213550000095",
        blood_type="AB+",
        status="donated",
        consent_public_phone=True,
        donated_at=now - timedelta(days=95),
    )
    db_session.flush()

    # Configure app with emergency_min_days_since_donation=90
    settings = get_settings().model_copy(update={"emergency_min_days_since_donation": 90})
    test_app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(test_app) as min_days_client:
            res = min_days_client.get("/api/v1/public/emergency/donors?blood_type=AB+")
            assert res.status_code == 200, res.text
            data = res.json()
            donor_ids = [d["donor_id"] for d in data]

            assert str(d_60.id) not in donor_ids, "Donor with <90 days must be excluded"
            assert str(d_95.id) in donor_ids, "Donor with >=90 days must be included"

            match = next(d for d in data if d["donor_id"] == str(d_95.id))
            assert match["days_since_donation"] == 95
    finally:
        test_app.dependency_overrides.pop(get_settings, None)


# ==============================================================================
# Operational Tests: Kill Switch, Rate Limiting, Site Filter & Route Aliases
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-01")
def test_t_emerg_12_a_list_kill_switch_503(
    test_app: FastAPI,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-12-a ★ / Decision D-62: When EMERGENCY_ENABLED=False, list returns 503 service_unavailable.

    Verifies:
    1. Both /api/v1/public/emergency/donors and alias /public/emergency return 503 service_unavailable.
    2. Toggle works: switching back to True immediately restores 200 OK.
    3. Requests while disabled do not consume rate limit quota or trigger 429.
    """
    settings_disabled = get_settings().model_copy(
        update={"emergency_enabled": False, "rate_emergency_list_per_hour": 3}
    )
    test_app.dependency_overrides[get_settings] = lambda: settings_disabled

    try:
        with TestClient(test_app) as kill_client:
            # 1. Primary path and alias both yield 503
            res1 = kill_client.get("/api/v1/public/emergency/donors?blood_type=O+")
            assert res1.status_code == 503, res1.text
            assert res1.json()["error"]["code"] == "service_unavailable"

            res2 = kill_client.get("/public/emergency?blood_type=O+")
            assert res2.status_code == 503, res2.text
            assert res2.json()["error"]["code"] == "service_unavailable"

            # 2. Repeated calls while disabled continue to return 503 without burning rate limit
            for _ in range(5):
                res = kill_client.get("/api/v1/public/emergency/donors?blood_type=O+")
                assert res.status_code == 503
                assert res.json()["error"]["code"] == "service_unavailable"

            # 3. Toggle back to True: service is immediately restored to 200 OK
            settings_enabled = get_settings().model_copy(
                update={"emergency_enabled": True, "rate_emergency_list_per_hour": 3}
            )
            test_app.dependency_overrides[get_settings] = lambda: settings_enabled

            res_restored = kill_client.get("/api/v1/public/emergency/donors?blood_type=O+")
            assert res_restored.status_code == 200, res_restored.text
            assert len(res_restored.json()) > 0
    finally:
        test_app.dependency_overrides.pop(get_settings, None)


def test_emergency_list_rate_limiting_429(test_app: FastAPI) -> None:
    """BR-RATE-04: Exceeding 60 requests/hour from same IP returns 429 rate_limited with Retry-After."""
    settings = get_settings().model_copy(update={"rate_emergency_list_per_hour": 3})
    test_app.dependency_overrides[get_settings] = lambda: settings
    limiter = InMemoryRateLimiter()
    test_app.dependency_overrides[get_rate_limiter] = lambda: limiter
    try:
        with TestClient(test_app) as rl_client:
            # First 3 succeed
            for _ in range(3):
                res = rl_client.get("/api/v1/public/emergency/donors?blood_type=O+")
                assert res.status_code == 200

            # 4th request exceeds rate limit
            res_blocked = rl_client.get("/api/v1/public/emergency/donors?blood_type=O+")
            assert res_blocked.status_code == 429, res_blocked.text
            assert res_blocked.json()["error"]["code"] == "rate_limited"
            assert "Retry-After" in res_blocked.headers
    finally:
        test_app.dependency_overrides.pop(get_settings, None)
        test_app.dependency_overrides.pop(get_rate_limiter, None)


def test_emergency_filter_by_site_id(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """Filtering by site_id restricts returned donors strictly to that site."""
    site_alpha_id = emergency_dataset["site_alpha"].id
    site_beta_id = emergency_dataset["site_beta"].id

    # Query site Alpha
    res_alpha = client.get(f"/api/v1/public/emergency/donors?blood_type=O+&site_id={site_alpha_id}")
    assert res_alpha.status_code == 200
    alpha_ids = [d["donor_id"] for d in res_alpha.json()]
    assert str(emergency_dataset["dA4"].id) in alpha_ids
    assert str(emergency_dataset["dA2"].id) in alpha_ids
    assert str(emergency_dataset["dB1"].id) not in alpha_ids

    # Query site Beta
    res_beta = client.get(f"/api/v1/public/emergency/donors?blood_type=O+&site_id={site_beta_id}")
    assert res_beta.status_code == 200
    beta_ids = [d["donor_id"] for d in res_beta.json()]
    assert str(emergency_dataset["dB1"].id) in beta_ids
    assert str(emergency_dataset["dA4"].id) not in beta_ids
    assert str(emergency_dataset["dA2"].id) not in beta_ids


def test_emergency_route_aliases_and_prefixes(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """Verify that root /public/emergency, /public/emergency/donors, and /api/v1/* behave identically."""
    # 1. /api/v1/public/emergency/donors
    r1 = client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert r1.status_code == 200

    # 2. /public/emergency/donors
    r2 = client.get("/public/emergency/donors?blood_type=O+")
    assert r2.status_code == 200
    assert r1.json() == r2.json()

    # 3. /public/emergency (root alias)
    r3 = client.get("/public/emergency?blood_type=O+")
    assert r3.status_code == 200
    assert r1.json() == r3.json()

    # 4. /api/v1/public/emergency (v1 root alias)
    r4 = client.get("/api/v1/public/emergency?blood_type=O+")
    assert r4.status_code == 200
    assert r1.json() == r4.json()


# Helper function
def data_json(res: Any) -> list[dict[str, Any]]:
    return res.json()  # type: ignore[no-any-return]


# ==============================================================================
# Phase P7.2: Reveal Operations (BR-EMERG-05..09, 10, 11, 13; T-EMERG-05..09, 13)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-05")
def test_t_emerg_05_a_reveal_eligible_donor_phone(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-05-a ★: POST /public/emergency/donors/{da2.id}/reveal returns 200 {"phone": ...}."""
    da2 = emergency_dataset["dA2"]

    res = client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
    assert res.status_code == 200, res.text

    payload = res.json()
    validated = EmergencyRevealOut.model_validate(payload)
    assert validated.phone == da2.phone
    assert validated.phone == "+213550000002"
    # Exact single-field payload
    assert list(payload.keys()) == ["phone"]


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-06")
def test_t_emerg_06_a_indistinguishable_404_for_ineligible_donors(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-06-a ★: Non-existent, attended-only, unconsented, and anonymized return IDENTICAL 404."""
    non_existent_id = uuid.uuid4()
    d_attended = emergency_dataset["d_attended"]
    d_no_consent = emergency_dataset["d_no_consent"]
    d_anonymized = emergency_dataset["d_anonymized"]

    test_targets = [
        ("non_existent", non_existent_id),
        ("attended_only", d_attended.id),
        ("no_public_consent", d_no_consent.id),
        ("anonymized", d_anonymized.id),
    ]

    responses: list[dict[str, Any]] = []

    for label, donor_id in test_targets:
        res = client.post(f"/api/v1/public/emergency/donors/{donor_id}/reveal")
        assert res.status_code == 404, f"{label} expected 404, got {res.status_code}: {res.text}"
        body = res.json()
        assert "error" in body
        assert body["error"]["code"] == "not_found"
        assert "request_id" in body["error"]
        responses.append(body["error"])

    # Verify that error messages are completely indistinguishable
    messages = [e["message"] for e in responses]
    assert len(set(messages)) == 1, (
        f"Error messages must be identical across all ineligible cases: {messages}"
    )
    assert messages[0] == "Donor not found or not eligible for emergency contact."


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-07")
def test_t_emerg_07_a_reveal_rate_limit_10_per_hour(
    test_app: FastAPI,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-07-a ★ / BR-RATE-05: 11th reveal from same IP receives 429 rate_limited + Retry-After."""
    da2 = emergency_dataset["dA2"]

    settings = get_settings().model_copy(update={"rate_emergency_reveal_per_hour": 10})
    test_app.dependency_overrides[get_settings] = lambda: settings
    limiter = InMemoryRateLimiter()
    test_app.dependency_overrides[get_rate_limiter] = lambda: limiter

    try:
        with TestClient(test_app) as rl_client:
            # 10 successful reveals
            for i in range(10):
                res = rl_client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
                assert res.status_code == 200, f"Request {i + 1} failed: {res.text}"

            # 11th reveal must receive 429
            blocked = rl_client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
            assert blocked.status_code == 429, (
                f"11th request expected 429, got {blocked.status_code}"
            )
            assert blocked.json()["error"]["code"] == "rate_limited"
            assert "Retry-After" in blocked.headers
    finally:
        test_app.dependency_overrides.pop(get_settings, None)
        test_app.dependency_overrides.pop(get_rate_limiter, None)


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-07")
def test_t_emerg_07_b_reveal_rate_limit_per_ip(
    test_app: FastAPI,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-07-b ★: Rate limit is enforced per genuine client IP; other IPs succeed."""
    da2 = emergency_dataset["dA2"]

    settings = get_settings().model_copy(update={"rate_emergency_reveal_per_hour": 2})
    test_app.dependency_overrides[get_settings] = lambda: settings
    limiter = InMemoryRateLimiter()
    test_app.dependency_overrides[get_rate_limiter] = lambda: limiter

    try:
        # Client 1 uses default test client IP
        with TestClient(test_app) as c1:
            for _ in range(2):
                r = c1.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
                assert r.status_code == 200

            # 3rd request from c1 is blocked
            blocked = c1.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
            assert blocked.status_code == 429

        # Client 2 connects from a distinct peer IP
        with TestClient(test_app, client=("198.51.100.25", 50000)) as c2:
            r2 = c2.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
            assert r2.status_code == 200, f"Different IP must succeed: {r2.text}"
    finally:
        test_app.dependency_overrides.pop(get_settings, None)
        test_app.dependency_overrides.pop(get_rate_limiter, None)


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-07")
def test_t_emerg_07_c_spoofed_x_forwarded_for_from_untrusted_proxy(
    test_app: FastAPI,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-07-c ★ / T-RATE-10: Spoofed X-Forwarded-For from untrusted peer cannot bypass rate limit."""
    da2 = emergency_dataset["dA2"]

    # Configure trusted_proxies to strictly NOT trust the attacker peer
    settings = get_settings().model_copy(
        update={
            "rate_emergency_reveal_per_hour": 3,
            "trusted_proxies": ["10.0.0.1/32"],
        }
    )
    test_app.dependency_overrides[get_settings] = lambda: settings
    limiter = InMemoryRateLimiter()
    test_app.dependency_overrides[get_rate_limiter] = lambda: limiter

    try:
        # Untrusted peer 192.168.1.100 attempts spoofing via rotating X-Forwarded-For
        with TestClient(test_app, client=("192.168.1.100", 50000)) as attacker_client:
            for i in range(3):
                headers = {"X-Forwarded-For": f"203.0.113.{i + 1}"}
                r = attacker_client.post(
                    f"/api/v1/public/emergency/donors/{da2.id}/reveal",
                    headers=headers,
                )
                assert r.status_code == 200, f"Request {i + 1} failed: {r.text}"

            # 4th request with yet another spoofed IP header
            spoofed_4th = attacker_client.post(
                f"/api/v1/public/emergency/donors/{da2.id}/reveal",
                headers={"X-Forwarded-For": "203.0.113.99"},
            )
            assert spoofed_4th.status_code == 429, (
                f"Expected 429 due to untrusted proxy ignoring XFF, got {spoofed_4th.status_code}"
            )
            assert spoofed_4th.json()["error"]["code"] == "rate_limited"
    finally:
        test_app.dependency_overrides.pop(get_settings, None)
        test_app.dependency_overrides.pop(get_rate_limiter, None)


@pytest.mark.br("BR-EMERG-08")
def test_t_emerg_08_a_reveal_side_effects_phone_reveal_log_and_audit(
    client: TestClient,
    db_session: Session,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-08-a: Successful reveal logs to phone_reveal_log (hashed IP) and audit_log (NO phone number)."""
    da2 = emergency_dataset["dA2"]

    res = client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
    assert res.status_code == 200

    # 1. Inspect phone_reveal_log in database
    reveal_logs = (
        db_session.execute(select(PhoneRevealLog).where(PhoneRevealLog.donor_id == da2.id))
        .scalars()
        .all()
    )
    assert len(reveal_logs) >= 1
    latest_reveal = reveal_logs[-1]

    # Verify ip_hash is a salted hash and NEVER a raw IP
    assert latest_reveal.ip_hash.startswith("ip_hash_")
    assert len(latest_reveal.ip_hash) > 10
    assert "127.0.0.1" not in latest_reveal.ip_hash

    # 2. Inspect audit_log in database
    audit_logs = (
        db_session.execute(
            select(AuditLog)
            .where(AuditLog.action == "emergency.reveal")
            .where(AuditLog.entity_id == da2.id)
        )
        .scalars()
        .all()
    )
    assert len(audit_logs) >= 1
    latest_audit = audit_logs[-1]

    assert latest_audit.entity_type == "donor"
    assert latest_audit.actor_user_id is None
    assert latest_audit.details is not None
    assert "ip_hash" in latest_audit.details

    # STRICT PRIVACY GUARD (BR-AUDIT-01 ★, BR-EMERG-08):
    # Absolute zero leakage of phone number or donor name in audit details!
    details_str = str(latest_audit.details)
    assert da2.phone not in details_str, "Donor phone number leaked in audit_log details!"
    assert da2.first_name not in details_str, "Donor name leaked in audit_log details!"
    assert "phone" not in latest_audit.details, "Phone key must NOT exist in audit details!"


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-09")
def test_t_emerg_09_a_reveal_cache_control_no_store(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-09-a ★ / BR-EMERG-09: POST reveal response includes Cache-Control: no-store header."""
    da2 = emergency_dataset["dA2"]

    res = client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "no-store"


@pytest.mark.br("BR-EMERG-10")
def test_t_emerg_10_a_consent_withdrawal_blocks_reveal(
    client: TestClient,
    db_session: Session,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-10-a / BR-EMERG-10: Withdrawing consent_public_phone immediately causes reveal to return 404."""
    da2 = emergency_dataset["dA2"]

    # 1. Initially eligible
    res_before = client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
    assert res_before.status_code == 200

    # 2. Donor withdraws public consent (keeps supervisor phone consent per BR-CONSENT-03)
    da2.consent_public_phone = False
    db_session.add(da2)
    db_session.commit()

    # 3. Subsequent reveal returns identical 404
    res_after = client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
    assert res_after.status_code == 404
    assert res_after.json()["error"]["code"] == "not_found"
    assert (
        res_after.json()["error"]["message"]
        == "Donor not found or not eligible for emergency contact."
    )


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-11")
def test_t_emerg_11_b_reveal_with_min_days_since_donation(
    test_app: FastAPI,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-11-b ★ / BR-EMERG-11: With min_days=90, da4 (100d) reveals, da2 (40d) returns identical 404."""
    da4 = emergency_dataset["dA4"]  # donated 100 days ago
    da2 = emergency_dataset["dA2"]  # donated 40 days ago

    settings = get_settings().model_copy(update={"emergency_min_days_since_donation": 90})
    test_app.dependency_overrides[get_settings] = lambda: settings

    try:
        with TestClient(test_app) as cutoff_client:
            # da4 has 100 days >= 90 days -> eligible
            r_da4 = cutoff_client.post(f"/api/v1/public/emergency/donors/{da4.id}/reveal")
            assert r_da4.status_code == 200
            assert r_da4.json()["phone"] == da4.phone

            # da2 has 40 days < 90 days -> ineligible (identical 404)
            r_da2 = cutoff_client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
            assert r_da2.status_code == 404
            assert r_da2.json()["error"]["code"] == "not_found"
            assert (
                r_da2.json()["error"]["message"]
                == "Donor not found or not eligible for emergency contact."
            )
    finally:
        test_app.dependency_overrides.pop(get_settings, None)


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-01")
def test_t_emerg_12_a_reveal_kill_switch_503(
    test_app: FastAPI,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-12-a ★ / Decision D-62: Reveal endpoint returns 503 when EMERGENCY_ENABLED=False.

    Verifies:
    1. Both /api/v1/public/emergency/donors/{id}/reveal and alias /public/emergency/{id}/reveal return 503.
    2. Toggle works: switching back to True immediately restores 200 OK.
    3. Exceeding reveal quota while disabled does not burn reveal limits or trigger 429.
    """
    da2 = emergency_dataset["dA2"]

    settings_disabled = get_settings().model_copy(
        update={"emergency_enabled": False, "rate_emergency_reveal_per_hour": 3}
    )
    test_app.dependency_overrides[get_settings] = lambda: settings_disabled

    try:
        with TestClient(test_app) as kill_client:
            # 1. Primary path and alias both yield 503
            res1 = kill_client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
            assert res1.status_code == 503
            assert res1.json()["error"]["code"] == "service_unavailable"

            res2 = kill_client.post(f"/public/emergency/{da2.id}/reveal")
            assert res2.status_code == 503
            assert res2.json()["error"]["code"] == "service_unavailable"

            # 2. Repeated calls while disabled continue to return 503 without burning rate limit
            for _ in range(5):
                res = kill_client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
                assert res.status_code == 503
                assert res.json()["error"]["code"] == "service_unavailable"

            # 3. Toggle back to True: service is immediately restored to 200 OK
            settings_enabled = get_settings().model_copy(
                update={"emergency_enabled": True, "rate_emergency_reveal_per_hour": 3}
            )
            test_app.dependency_overrides[get_settings] = lambda: settings_enabled

            res_restored = kill_client.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
            assert res_restored.status_code == 200
            assert res_restored.json()["phone"] == da2.phone
    finally:
        test_app.dependency_overrides.pop(get_settings, None)


def test_reveal_route_aliases_and_prefixes(
    client: TestClient,
    emergency_dataset: dict[str, Any],
) -> None:
    """Verify route aliases: /api/v1/public/emergency/donors/{id}/reveal and /public/emergency/{id}/reveal."""
    da2 = emergency_dataset["dA2"]

    # 1. Root /public/emergency/donors/{id}/reveal
    r1 = client.post(f"/public/emergency/donors/{da2.id}/reveal")
    assert r1.status_code == 200
    assert r1.json()["phone"] == da2.phone

    # 2. Root alias /public/emergency/{id}/reveal
    r2 = client.post(f"/public/emergency/{da2.id}/reveal")
    assert r2.status_code == 200
    assert r2.json()["phone"] == da2.phone

    # 3. /api/v1 alias /api/v1/public/emergency/{id}/reveal
    r3 = client.post(f"/api/v1/public/emergency/{da2.id}/reveal")
    assert r3.status_code == 200
    assert r3.json()["phone"] == da2.phone


@pytest.mark.br("BR-EMERG-13")
def test_t_emerg_13_a_reveal_spike_audit_event(
    test_app: FastAPI,
    db_session: Session,
    emergency_dataset: dict[str, Any],
) -> None:
    """T-EMERG-13-a / BR-EMERG-13: System-wide reveal spike triggers security.reveal_spike audit event."""
    da2 = emergency_dataset["dA2"]

    # Set rate limit high so we don't hit 429, but spike threshold is low
    settings = get_settings().model_copy(update={"rate_emergency_reveal_per_hour": 2})
    test_app.dependency_overrides[get_settings] = lambda: settings

    # Insert existing reveals in phone_reveal_log to simulate past reveals in the current hour
    now = datetime.now(UTC)
    for _ in range(6):
        db_session.add(
            PhoneRevealLog(
                ip_hash=f"ip_hash_preexisting_{uuid.uuid4().hex[:8]}",
                donor_id=da2.id,
                created_at=now - timedelta(minutes=10),
            )
        )
    db_session.commit()

    try:
        # A new reveal crosses the spike threshold (2 * 3 = 6 threshold)
        with TestClient(test_app) as client_spike:
            res = client_spike.post(f"/api/v1/public/emergency/donors/{da2.id}/reveal")
            assert res.status_code == 200

            # Verify security.reveal_spike event in audit_log
            spike_logs = (
                db_session.execute(
                    select(AuditLog).where(AuditLog.action == "security.reveal_spike")
                )
                .scalars()
                .all()
            )
            assert len(spike_logs) >= 1
            latest_spike = spike_logs[-1]
            assert latest_spike.entity_type == "system"
            assert latest_spike.details is not None
            assert "total_reveals_1h" in latest_spike.details
    finally:
        test_app.dependency_overrides.pop(get_settings, None)
