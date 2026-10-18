"""Security and Contract Tests for Public API Endpoints (P6.4).

Per:
- docs/06-business-rules.md §2 (BR-GEN-09 ★: No secret or internal fields in any public response)
- docs/06-business-rules.md §2 (BR-GEN-10: Exact schema enforcement, no ORM leakage)
- docs/06-business-rules.md §5 (BR-PLEDGE-08 ★: Pledges never visible to public, counts only)
- docs/06-business-rules.md §5 (BR-PLEDGE-09 ★: Pledge phone numbers never public, strict privacy)
- docs/06-business-rules.md §9 (BR-HERO-02 ★: Heroes return first_name, site_id, site_name ONLY)
- docs/07-test-cases.md §5.1 (T-GEN-09-a ★, T-GEN-10-a)
- docs/07-test-cases.md §5.4 (T-PLEDGE-08-a ★, T-PLEDGE-08-b ★, T-PLEDGE-09-a ★)
- docs/07-test-cases.md §3.3 (Sentinels: ZZFIRST, ZZLAST, 0550123456)
- docs/04-api.md §6 & §24 (Public API Contract Requirements)
"""

import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    get_clock,
    get_db,
    get_session_service,
    require,
)
from app.api.error_handlers import register_error_handlers
from app.core.clock import FakeClock
from app.core.config import get_settings
from app.policy import Condition, Permission, get_required_conditions, is_permission_granted
from app.repositories.sessions import SessionRepository
from app.schemas.campaign import CampaignPublicCardOut, CampaignPublicOut
from app.schemas.campaign_site import CampaignSitePublicOut
from app.schemas.emergency import EmergencyDonorItemOut
from app.schemas.photo import PhotoPublicListOut, PhotoPublicOut
from app.schemas.site import SitePublicOut
from app.schemas.stats import (
    CampaignCountersOut,
    HeroPublicListOut,
    HeroPublicOut,
    SiteRankingOut,
)
from app.services.sessions import SessionService
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo
from tests.factories.pledge import create_pledge
from tests.factories.user import create_user

# Forbidden secret and internal fields that MUST NEVER appear in any public response
FORBIDDEN_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "password_hash",
        "totp_secret",
        "totp_secret_enc",
        "token_hash",
        "ip_hash",
        "code_hash",
        "raw_token",
        "registered_by",
        "password",
        "salt",
    }
)


def _collect_nested_keys(data: Any) -> set[str]:
    """Recursively collect all keys present at any level in a JSON-like data structure."""
    keys: set[str] = set()
    if isinstance(data, dict):
        for k, v in data.items():
            keys.add(k)
            keys.update(_collect_nested_keys(v))
    elif isinstance(data, list):
        for item in data:
            keys.update(_collect_nested_keys(item))
    return keys


# ==============================================================================
# Fixture: Rich Seeded Environment for Contract and Privacy Verification
# ==============================================================================


@pytest.fixture
def public_contract_env(db_session: Session) -> dict[str, Any]:
    """Provision a rich dataset with live and ended campaigns, sites, donors, photos, and pledges."""
    # 1. Sites
    site_alpha = create_site(
        db_session,
        name="موقع ألف المركزي",
        address="شارع ديدوش مراد، الجزائر العاصمة",
        map_url="https://maps.google.com/?q=36.77,3.06",
        description="موقع رئيسي مزود بـ 8 أسرّة",
        is_active=True,
    )
    site_beta = create_site(
        db_session,
        name="موقع باء الطبي",
        address="حي النور، بئر مراد رايس",
        map_url="https://maps.google.com/?q=36.73,3.04",
        description="مركز طبي جامعي",
        is_active=True,
    )
    site_inactive = create_site(
        db_session,
        name="موقع معطّل تاريخي",
        address="المقر القديم",
        is_active=False,
    )

    # 2. Supervisor User (to verify registered_by is populated in DB but hidden publicly)
    supervisor = create_user(
        db_session,
        email="supervisor.central@bloodday.local",
        role="supervisor",
        site_id=site_alpha.id,
        is_active=True,
    )

    # 3. Live Published Current Campaign
    live_camp = create_campaign(
        db_session,
        slug="live-blood-drive-contract",
        title="اليوم الموحد للتبرع بالدم 2026",
        summary="أكبر حملة تبرع بالدم",
        content="# تعليمات التبرع\nنرجو الحضور بالبطاقة الوطنية.",
        cover_url="https://storage.local/covers/live-contract.webp",
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_alpha.id,
        supervisors_text="د. إبراهيم",
    )
    create_campaign_site(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_beta.id,
        supervisors_text="د. حنان",
    )

    # 4. Ended Published Campaign with frozen final_stats
    ended_camp = create_campaign(
        db_session,
        slug="ended-blood-drive-contract",
        title="حملة الخريف السابقة",
        summary="حملة ناجحة سابقة",
        content="تم اختتام الحملة بنجاح.",
        status="ended",
        is_published=True,
        is_current=False,
        ended_at=datetime.now(UTC) - timedelta(days=30),
        final_stats={
            "pledges": 50,
            "present": 30,
            "donated": 25,
            "heroes": 18,
            "by_site": [
                {
                    "site_id": str(site_alpha.id),
                    "name": "موقع ألف المركزي",
                    "pledges": 30,
                    "present": 18,
                    "donated": 15,
                },
                {
                    "site_id": str(site_beta.id),
                    "name": "موقع باء الطبي",
                    "pledges": 20,
                    "present": 12,
                    "donated": 10,
                },
            ],
            "frozen_at": datetime.now(UTC).isoformat(),
        },
    )
    create_campaign_site(db_session, campaign_id=ended_camp.id, site_id=site_alpha.id)
    create_campaign_site(db_session, campaign_id=ended_camp.id, site_id=site_beta.id)

    # 5. Donors in Live Campaign
    # Hero Donor: donated + consent_hero_name
    hero_donor = create_donor(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_alpha.id,
        first_name="يوسف",
        last_name="البطل",
        phone="0550998877",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
        registered_by=supervisor.id,
        donated_at=datetime.now(UTC) - timedelta(hours=2),
    )
    # Non-hero Donated Donor: donated + hero=False
    create_donor(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_beta.id,
        first_name="كريم",
        last_name="مخفي",
        phone="0550112233",
        blood_type="A+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
        registered_by=supervisor.id,
        donated_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # Attended Donor: attended
    create_donor(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_alpha.id,
        first_name="سمير",
        status="attended",
        consent_keep_phone=False,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
        registered_by=supervisor.id,
    )

    # 6. Photos in Live Campaign
    # General photo
    create_photo(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_alpha.id,
        kind="general",
        caption="لقطة عامة للأجواء",
    )
    # Individual photo with donor consent
    create_photo(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_alpha.id,
        kind="individual",
        donor_id=hero_donor.id,
        caption="المتبرع يوسف أثناء التبرع",
    )

    # 7. Pledges with Sensitive Sentinel Values
    create_pledge(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_alpha.id,
        first_name="ZZFIRST_PLEDGE",
        phone="0550123456",
    )
    create_pledge(
        db_session,
        campaign_id=live_camp.id,
        site_id=site_beta.id,
        first_name="ZZFIRST_ANON_PLEDGE",
        phone=None,
    )

    db_session.flush()

    return {
        "live_campaign": live_camp,
        "ended_campaign": ended_camp,
        "site_alpha": site_alpha,
        "site_beta": site_beta,
        "site_inactive": site_inactive,
        "supervisor": supervisor,
        "hero_donor": hero_donor,
    }


# ==============================================================================
# Group 1: Contract Tests (T-GEN-10-a, BR-GEN-10)
# Exact response keys matching declared Pydantic schemas without extra/missing keys
# ==============================================================================


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
def test_t_gen_10_a_contract_get_current_campaign(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a: GET /public/campaigns/current returns exact CampaignPublicOut keys."""
    res = test_client.get("/api/v1/public/campaigns/current")
    assert res.status_code == 200, res.text
    data = res.json()

    expected_keys = set(CampaignPublicOut.model_fields.keys())
    actual_keys = set(data.keys())

    assert actual_keys == expected_keys, (
        f"Contract mismatch on GET /public/campaigns/current:\n"
        f"Extra keys: {actual_keys - expected_keys}\n"
        f"Missing keys: {expected_keys - actual_keys}"
    )


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
def test_t_gen_10_a_contract_list_public_campaigns(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a: GET /public/campaigns returns list of exact CampaignPublicCardOut keys."""
    res = test_client.get("/api/v1/public/campaigns")
    assert res.status_code == 200, res.text
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 2, "Expected at least live and ended campaigns in list."

    expected_keys = set(CampaignPublicCardOut.model_fields.keys())
    for idx, item in enumerate(data):
        actual_keys = set(item.keys())
        assert actual_keys == expected_keys, (
            f"Contract mismatch on GET /public/campaigns item [{idx}]:\n"
            f"Extra keys: {actual_keys - expected_keys}\n"
            f"Missing keys: {expected_keys - actual_keys}"
        )


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
def test_t_gen_10_a_contract_get_campaign_by_slug(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a: GET /public/campaigns/{slug} returns exact CampaignPublicOut keys."""
    slug = public_contract_env["live_campaign"].slug
    res = test_client.get(f"/api/v1/public/campaigns/{slug}")
    assert res.status_code == 200, res.text
    data = res.json()

    expected_keys = set(CampaignPublicOut.model_fields.keys())
    actual_keys = set(data.keys())

    assert actual_keys == expected_keys, (
        f"Contract mismatch on GET /public/campaigns/{slug}:\n"
        f"Extra keys: {actual_keys - expected_keys}\n"
        f"Missing keys: {expected_keys - actual_keys}"
    )


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
def test_t_gen_10_a_contract_get_campaign_sites(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a: GET /public/campaigns/{slug}/sites returns list of exact CampaignSitePublicOut keys."""
    slug = public_contract_env["live_campaign"].slug
    res = test_client.get(f"/api/v1/public/campaigns/{slug}/sites")
    assert res.status_code == 200, res.text
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    expected_keys = set(CampaignSitePublicOut.model_fields.keys())
    for idx, item in enumerate(data):
        actual_keys = set(item.keys())
        assert actual_keys == expected_keys, (
            f"Contract mismatch on GET /public/campaigns/{slug}/sites item [{idx}]:\n"
            f"Extra keys: {actual_keys - expected_keys}\n"
            f"Missing keys: {expected_keys - actual_keys}"
        )


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
def test_t_gen_10_a_contract_get_campaign_photos(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a: GET /public/campaigns/{slug}/photos returns exact PhotoPublicListOut and PhotoPublicOut keys."""
    slug = public_contract_env["live_campaign"].slug
    res = test_client.get(f"/api/v1/public/campaigns/{slug}/photos")
    assert res.status_code == 200, res.text
    data = res.json()

    # Outer container schema keys
    expected_outer_keys = set(PhotoPublicListOut.model_fields.keys())
    actual_outer_keys = set(data.keys())
    assert actual_outer_keys == expected_outer_keys, (
        f"Contract mismatch on GET /public/campaigns/{slug}/photos outer container:\n"
        f"Extra keys: {actual_outer_keys - expected_outer_keys}\n"
        f"Missing keys: {expected_outer_keys - actual_outer_keys}"
    )

    # Inner items schema keys
    items = data["items"]
    assert len(items) >= 1
    expected_item_keys = set(PhotoPublicOut.model_fields.keys())
    for idx, item in enumerate(items):
        actual_item_keys = set(item.keys())
        assert actual_item_keys == expected_item_keys, (
            f"Contract mismatch on GET /public/campaigns/{slug}/photos item [{idx}]:\n"
            f"Extra keys: {actual_item_keys - expected_item_keys}\n"
            f"Missing keys: {expected_item_keys - actual_item_keys}"
        )


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
def test_t_gen_10_a_contract_get_campaign_counters(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a: GET /public/campaigns/{slug}/counters returns exact CampaignCountersOut keys."""
    expected_keys = set(CampaignCountersOut.model_fields.keys())

    # 1. Live campaign counters
    live_slug = public_contract_env["live_campaign"].slug
    res_live = test_client.get(f"/api/v1/public/campaigns/{live_slug}/counters")
    assert res_live.status_code == 200, res_live.text
    data_live = res_live.json()
    assert set(data_live.keys()) == expected_keys

    # 2. Ended campaign frozen counters
    ended_slug = public_contract_env["ended_campaign"].slug
    res_ended = test_client.get(f"/api/v1/public/campaigns/{ended_slug}/counters")
    assert res_ended.status_code == 200, res_ended.text
    data_ended = res_ended.json()
    assert set(data_ended.keys()) == expected_keys


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
@pytest.mark.br("BR-HERO-02")
def test_t_gen_10_a_contract_get_campaign_heroes(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a & BR-HERO-02 (★): GET /public/campaigns/{slug}/heroes returns exact HeroPublicListOut and HeroPublicOut keys."""
    slug = public_contract_env["live_campaign"].slug
    res = test_client.get(f"/api/v1/public/campaigns/{slug}/heroes")
    assert res.status_code == 200, res.text
    data = res.json()

    # Outer container keys
    expected_outer_keys = set(HeroPublicListOut.model_fields.keys())
    assert set(data.keys()) == expected_outer_keys

    # Hero item keys: strictly first_name, site_id, site_name per BR-HERO-02 ★
    items = data["items"]
    assert len(items) >= 1
    expected_item_keys = set(HeroPublicOut.model_fields.keys())
    assert expected_item_keys == {"first_name", "site_id", "site_name"}
    for idx, item in enumerate(items):
        actual_item_keys = set(item.keys())
        assert actual_item_keys == expected_item_keys, (
            f"Hero contract violation on item [{idx}]:\n"
            f"Expected strictly: {expected_item_keys}\n"
            f"Got: {actual_item_keys}"
        )
        # Explicitly verify no PII leakage in hero records
        assert "last_name" not in item
        assert "phone" not in item
        assert "blood_type" not in item
        assert "photo" not in item
        assert "registered_by" not in item
        assert "id" not in item


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
def test_t_gen_10_a_contract_get_campaign_ranking(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a: GET /public/campaigns/{slug}/ranking returns list of exact SiteRankingOut keys."""
    expected_keys = set(SiteRankingOut.model_fields.keys())

    # 1. Live campaign ranking
    live_slug = public_contract_env["live_campaign"].slug
    res_live = test_client.get(f"/api/v1/public/campaigns/{live_slug}/ranking")
    assert res_live.status_code == 200, res_live.text
    data_live = res_live.json()
    assert isinstance(data_live, list)
    assert len(data_live) >= 1
    for idx, item in enumerate(data_live):
        assert set(item.keys()) == expected_keys, f"Live ranking item [{idx}] key mismatch"

    # 2. Ended campaign ranking
    ended_slug = public_contract_env["ended_campaign"].slug
    res_ended = test_client.get(f"/api/v1/public/campaigns/{ended_slug}/ranking")
    assert res_ended.status_code == 200, res_ended.text
    data_ended = res_ended.json()
    assert isinstance(data_ended, list)
    assert len(data_ended) >= 1
    for idx, item in enumerate(data_ended):
        assert set(item.keys()) == expected_keys, f"Ended ranking item [{idx}] key mismatch"


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
def test_t_gen_10_a_contract_list_public_sites(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a: GET /public/sites returns list of exact SitePublicOut keys."""
    res = test_client.get("/api/v1/public/sites")
    assert res.status_code == 200, res.text
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 2

    expected_keys = set(SitePublicOut.model_fields.keys())
    for idx, item in enumerate(data):
        actual_keys = set(item.keys())
        assert actual_keys == expected_keys, (
            f"Contract mismatch on GET /public/sites item [{idx}]:\n"
            f"Extra keys: {actual_keys - expected_keys}\n"
            f"Missing keys: {expected_keys - actual_keys}"
        )
        # Ensure inactive site is never exposed per BR-SITE-02
        assert item["id"] != str(public_contract_env["site_inactive"].id)


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-10")
@pytest.mark.br("BR-EMERG-02")
def test_t_gen_10_a_contract_get_emergency_donors(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-10-a & BR-EMERG-02 (★): GET /public/emergency/donors returns list of exact EmergencyDonorItemOut keys."""
    res = test_client.get("/api/v1/public/emergency/donors?blood_type=O+")
    assert res.status_code == 200, res.text
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    expected_keys = set(EmergencyDonorItemOut.model_fields.keys())
    assert expected_keys == {
        "donor_id",
        "first_name",
        "blood_type",
        "site_name",
        "days_since_donation",
    }
    for idx, item in enumerate(data):
        actual_keys = set(item.keys())
        assert actual_keys == expected_keys, (
            f"Contract mismatch on GET /public/emergency/donors item [{idx}]:\n"
            f"Extra keys: {actual_keys - expected_keys}\n"
            f"Missing keys: {expected_keys - actual_keys}"
        )
        assert "phone" not in item
        assert "last_name" not in item
        assert "status" not in item


# ==============================================================================
# Group 2: Secret & Internal Field Absence (T-GEN-09-a ★, BR-GEN-09 ★)
# ==============================================================================


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-GEN-09")
def test_t_gen_09_a_no_secret_or_internal_fields_in_any_public_response(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-GEN-09-a (★): Recursive inspection verifies zero secret or internal keys in public endpoints.

    Forbidden keys: password_hash, totp_secret_enc, token_hash, ip_hash, code_hash,
    raw_token, registered_by, password, salt.
    """
    live_slug = public_contract_env["live_campaign"].slug
    ended_slug = public_contract_env["ended_campaign"].slug

    public_endpoints = [
        "/api/v1/public/campaigns/current",
        "/api/v1/public/campaigns",
        f"/api/v1/public/campaigns/{live_slug}",
        f"/api/v1/public/campaigns/{live_slug}/sites",
        f"/api/v1/public/campaigns/{live_slug}/photos",
        f"/api/v1/public/campaigns/{live_slug}/counters",
        f"/api/v1/public/campaigns/{live_slug}/heroes",
        f"/api/v1/public/campaigns/{live_slug}/ranking",
        f"/api/v1/public/campaigns/{ended_slug}",
        f"/api/v1/public/campaigns/{ended_slug}/counters",
        f"/api/v1/public/campaigns/{ended_slug}/ranking",
        "/api/v1/public/sites",
        "/api/v1/public/emergency/donors?blood_type=O+",
    ]

    for endpoint in public_endpoints:
        res = test_client.get(endpoint)
        assert res.status_code == 200, f"Expected 200 on {endpoint}, got {res.status_code}"
        payload = res.json()

        all_keys = _collect_nested_keys(payload)
        leaked_forbidden_keys = all_keys & FORBIDDEN_SECRET_KEYS
        assert not leaked_forbidden_keys, (
            f"Security violation! Forbidden keys leaked in {endpoint}: {leaked_forbidden_keys}"
        )


# ==============================================================================
# Group 3: Pledge Data Confidentiality (T-PLEDGE-08-a ★, T-PLEDGE-09-a ★)
# ==============================================================================


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-PLEDGE-08")
@pytest.mark.br("BR-PLEDGE-09")
def test_t_pledge_08_09_a_pledge_sentinels_never_exposed_in_any_public_response(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """T-PLEDGE-08-a (★) & T-PLEDGE-09-a (★): Pledge names and phone numbers NEVER appear in public responses.

    Verified by querying all public routes and performing both raw textual and deep value
    sweeps against sentinel strings:
    - First name: ZZFIRST_PLEDGE, ZZFIRST_ANON_PLEDGE
    - Phone: 0550123456, +213550123456
    """
    live_slug = public_contract_env["live_campaign"].slug

    sentinels = [
        "ZZFIRST_PLEDGE",
        "ZZFIRST_ANON_PLEDGE",
        "0550123456",
        "+213550123456",
        "550123456",
    ]

    endpoints_to_inspect = [
        "/api/v1/public/campaigns/current",
        "/api/v1/public/campaigns",
        f"/api/v1/public/campaigns/{live_slug}",
        f"/api/v1/public/campaigns/{live_slug}/sites",
        f"/api/v1/public/campaigns/{live_slug}/photos",
        f"/api/v1/public/campaigns/{live_slug}/counters",
        f"/api/v1/public/campaigns/{live_slug}/heroes",
        f"/api/v1/public/campaigns/{live_slug}/ranking",
        "/api/v1/public/sites",
    ]

    for endpoint in endpoints_to_inspect:
        res = test_client.get(endpoint)
        assert res.status_code == 200, f"Failed GET {endpoint}: {res.text}"

        raw_text = res.text
        for sentinel in sentinels:
            assert sentinel not in raw_text, (
                f"Confidentiality breach! Pledge sentinel '{sentinel}' leaked in public endpoint {endpoint}:\n"
                f"{raw_text}"
            )

    # Verify that pledges are properly aggregated into numeric counts without leaking identities
    counters_res = test_client.get(f"/api/v1/public/campaigns/{live_slug}/counters")
    assert counters_res.status_code == 200
    counters_data = counters_res.json()
    # 2 pledges were created in live_campaign
    assert counters_data["pledges_count"] == 2

    sites_res = test_client.get(f"/api/v1/public/campaigns/{live_slug}/sites")
    assert sites_res.status_code == 200
    sites_data = sites_res.json()
    alpha_site_row = next(
        s for s in sites_data if s["site_id"] == str(public_contract_env["site_alpha"].id)
    )
    beta_site_row = next(
        s for s in sites_data if s["site_id"] == str(public_contract_env["site_beta"].id)
    )
    assert alpha_site_row["pledges_count"] == 1
    assert beta_site_row["pledges_count"] == 1


# ==============================================================================
# Group 4: Protection of Admin Pledge Operations (T-PLEDGE-08-b ★)
# ==============================================================================


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-PLEDGE-08")
def test_t_pledge_08_b_policy_matrix_rejects_anon_and_supervisor() -> None:
    """T-PLEDGE-08-b (★): Policy matrix strictly denies PLEDGE_READ and PLEDGE_DELETE to anon and supervisor."""
    # 1. Anonymous visitor
    assert not is_permission_granted("anon", Permission.PLEDGE_READ)
    assert not is_permission_granted("anon", Permission.PLEDGE_DELETE)

    # 2. Site supervisor
    assert not is_permission_granted("supervisor", Permission.PLEDGE_READ)
    assert not is_permission_granted("supervisor", Permission.PLEDGE_DELETE)

    # 3. Admin requires active account and MFA verification for PLEDGE_READ
    read_conds = get_required_conditions("admin", Permission.PLEDGE_READ)
    assert read_conds is not None
    assert Condition.ACTIVE_ACCOUNT in read_conds
    assert Condition.MFA_VERIFIED in read_conds

    # 4. Admin requires active account, MFA verification, and fresh step-up (R) for PLEDGE_DELETE
    del_conds = get_required_conditions("admin", Permission.PLEDGE_DELETE)
    assert del_conds is not None
    assert Condition.ACTIVE_ACCOUNT in del_conds
    assert Condition.MFA_VERIFIED in del_conds
    assert Condition.RECENT_REAUTH in del_conds


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-PLEDGE-08")
def test_t_pledge_08_b_admin_pledges_dependency_enforcement(
    db_session: Session,
    fake_clock: FakeClock,
) -> None:
    """T-PLEDGE-08-b (★): HTTP endpoint protected by require(Permission.PLEDGE_READ) rejects anon (401) and supervisor (403)."""
    test_app = FastAPI()
    register_error_handlers(test_app)

    session_service = SessionService(
        SessionRepository(db_session),
        clock=fake_clock,
        settings=get_settings(),
    )

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def override_get_clock() -> FakeClock:
        return fake_clock

    def override_get_session_service() -> SessionService:
        return session_service

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_clock] = override_get_clock
    test_app.dependency_overrides[get_session_service] = override_get_session_service

    # Route mirroring GET /admin/campaigns/{id}/pledges
    @test_app.get(
        "/api/v1/admin/campaigns/{campaign_id}/pledges",
        dependencies=[Depends(require(Permission.PLEDGE_READ))],
    )
    def get_campaign_pledges(campaign_id: uuid.UUID) -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(test_app)
    cookie_name = get_settings().cookie_name
    camp_id = uuid.uuid4()

    # 1. Anonymous visitor -> 401 not_authenticated
    res_anon = client.get(f"/api/v1/admin/campaigns/{camp_id}/pledges")
    assert res_anon.status_code == 401
    assert res_anon.json()["error"]["code"] == "not_authenticated"

    # 2. Site supervisor -> 403 forbidden
    site = create_site(db_session, is_active=True)
    supervisor = create_user(
        db_session,
        email="sup_pledges_test@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )
    sup_token, _ = session_service.create_session(supervisor.id, "supervisor")
    client.cookies.set(cookie_name, sup_token)

    res_sup = client.get(f"/api/v1/admin/campaigns/{camp_id}/pledges")
    assert res_sup.status_code == 403
    assert res_sup.json()["error"]["code"] == "forbidden"

    # 3. Admin without MFA verification -> 403 mfa_required
    admin_nomfa = create_user(
        db_session,
        email="admin_nomfa_pledges@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )
    admin_nomfa_token, sess_nomfa = session_service.create_session(admin_nomfa.id, "admin")
    sess_nomfa.mfa_verified = False
    db_session.flush()

    client.cookies.set(cookie_name, admin_nomfa_token)
    res_nomfa = client.get(f"/api/v1/admin/campaigns/{camp_id}/pledges")
    assert res_nomfa.status_code == 403
    assert res_nomfa.json()["error"]["code"] == "mfa_required"

    # 4. Verified Admin -> 200 OK
    admin_verified = create_user(
        db_session,
        email="admin_verified_pledges@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )
    admin_verified_token, sess_ver = session_service.create_session(admin_verified.id, "admin")
    sess_ver.mfa_verified = True
    db_session.flush()

    client.cookies.set(cookie_name, admin_verified_token)
    res_verified = client.get(f"/api/v1/admin/campaigns/{camp_id}/pledges")
    assert res_verified.status_code == 200
    assert res_verified.json() == {"status": "ok"}


# ==============================================================================
# Group 5: Strict Hero Privacy Schema (BR-HERO-02 ★, T-HERO-02)
# ==============================================================================


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-HERO-02")
def test_t_hero_02_strict_hero_privacy_only_first_name_and_site(
    test_client: TestClient,
    public_contract_env: dict[str, Any],
) -> None:
    """BR-HERO-02 (★): Hero response contains first_name, site_id, site_name ONLY.

    Even if donor record in DB has last_name, phone, blood_type, photos, and registered_by,
    NONE of these may ever appear in HeroPublicOut.
    """
    slug = public_contract_env["live_campaign"].slug
    res = test_client.get(f"/api/v1/public/campaigns/{slug}/heroes")
    assert res.status_code == 200, res.text
    heroes = res.json()["items"]

    assert len(heroes) >= 1
    hero = heroes[0]

    assert hero["first_name"] == "يوسف"
    assert hero["site_id"] == str(public_contract_env["site_alpha"].id)
    assert hero["site_name"] == public_contract_env["site_alpha"].name

    # Strictly forbidden properties
    forbidden_hero_properties = [
        "last_name",
        "phone",
        "blood_type",
        "registered_by",
        "id",
        "donor_id",
        "photo",
        "photo_url",
        "donated_at",
        "created_at",
        "status",
        "consent_hero_name",
        "consent_photo",
        "consent_keep_phone",
        "consent_public_phone",
    ]
    for prop in forbidden_hero_properties:
        assert prop not in hero, f"Strict hero privacy breach: '{prop}' found in hero response!"
