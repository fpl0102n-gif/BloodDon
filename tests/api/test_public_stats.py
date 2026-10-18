"""Integration tests for Public Campaign Stats: Counters, Heroes, and Ranking (P6.3).

Per:
- docs/04-api.md §6.5 (GET /public/campaigns/{slug}/counters)
- docs/04-api.md §6.6 (GET /public/campaigns/{slug}/heroes)
- docs/04-api.md §6.7 (GET /public/campaigns/{slug}/ranking)
- docs/06-business-rules.md §9 (BR-HERO-01..06, BR-RANK-01..06, BR-COUNT-01..07)
- docs/07-test-cases.md §3.2 (Canonical Dataset C2: dA1..dA5, dB1)
- docs/07-test-cases.md §5.8 (T-HERO-01..06, T-RANK-01..05, T-COUNT-01..06)
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, Site
from app.models.pledge import Pledge
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor


@pytest.fixture
def client(test_client: TestClient) -> TestClient:
    """Anonymous client fixture."""
    return test_client


@pytest.fixture
def canonical_dataset(db_session: Session) -> dict[str, object]:
    """Provision Canonical Dataset C2 per docs/07-test-cases.md §3.2.

    Sites:
    - Site A: Active participant
    - Site B: Active participant
    - Site C: Active non-participant / participating with 0 donors

    Campaign C2:
    - Live, published, current

    Donors in C2:
    - dA1: Site A, attended, keep=F, hero=F, photo=F, public=F
    - dA2: Site A, donated, O+, keep=T, hero=T, photo=T, public=T (donated at T1)
    - dA3: Site A, donated, A+, keep=F, hero=T, photo=F, public=F (donated at T2 > T1, newest)
    - dA4: Site A, donated, O+, keep=F, hero=F, photo=F, public=T (donated at T0 < T1, oldest)
    - dA5: Site A, attended, B+, keep=T, hero=F, photo=F, public=F
    - dB1: Site B, donated, O+, keep=T, hero=T, photo=T, public=T (donated at T1)
    """
    site_a = create_site(db_session, is_active=True, name="موقع أ - المستشفى الجامعي")
    site_b = create_site(db_session, is_active=True, name="موقع ب - المركز الطبي")
    site_c = create_site(db_session, is_active=True, name="موقع ج - ساحة الشهداء")

    c2 = create_campaign(
        db_session,
        slug="c2-live-campaign",
        title="اليوم الموحد للتبرع بالدم C2",
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(db_session, campaign_id=c2.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=c2.id, site_id=site_b.id)

    # 3 Pledges for C2
    p1 = Pledge(campaign_id=c2.id, site_id=site_a.id, first_name="متعهد 1", phone="0550111111")
    p2 = Pledge(campaign_id=c2.id, site_id=site_a.id, first_name="متعهد 2", phone="0550111112")
    p3 = Pledge(campaign_id=c2.id, site_id=site_b.id, first_name="متعهد 3", phone="0550111113")
    db_session.add_all([p1, p2, p3])
    db_session.flush()

    base_time = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
    t0 = base_time - timedelta(hours=2)
    t1 = base_time - timedelta(hours=1)
    t2 = base_time  # Newest donation

    da1 = create_donor(
        db_session,
        campaign_id=c2.id,
        site_id=site_a.id,
        first_name="متبرع_dA1",
        status="attended",
        consent_keep_phone=False,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
    )
    da2 = create_donor(
        db_session,
        campaign_id=c2.id,
        site_id=site_a.id,
        first_name="متبرع_dA2",
        phone="0550000002",
        blood_type="O+",
        status="donated",
        donated_at=t1,
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
    )
    da3 = create_donor(
        db_session,
        campaign_id=c2.id,
        site_id=site_a.id,
        first_name="متبرع_dA3",
        phone=None,
        blood_type="A+",
        status="donated",
        donated_at=t2,
        consent_keep_phone=False,
        consent_hero_name=True,
        consent_photo=False,
        consent_public_phone=False,
    )
    da4 = create_donor(
        db_session,
        campaign_id=c2.id,
        site_id=site_a.id,
        first_name="متبرع_dA4",
        phone="0550000004",
        blood_type="O+",
        status="donated",
        donated_at=t0,
        consent_keep_phone=False,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=True,
    )
    da5 = create_donor(
        db_session,
        campaign_id=c2.id,
        site_id=site_a.id,
        first_name="متبرع_dA5",
        phone="0550000005",
        blood_type="B+",
        status="attended",
        consent_keep_phone=True,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
    )
    db1 = create_donor(
        db_session,
        campaign_id=c2.id,
        site_id=site_b.id,
        first_name="متبرع_dB1",
        phone="0550000011",
        blood_type="O+",
        status="donated",
        donated_at=t1,
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
    )

    return {
        "site_a": site_a,
        "site_b": site_b,
        "site_c": site_c,
        "c2": c2,
        "da1": da1,
        "da2": da2,
        "da3": da3,
        "da4": da4,
        "da5": da5,
        "db1": db1,
    }


# ==============================================================================
# 1. Heroes Tests (T-HERO-01..06, BR-HERO-01..06)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-HERO-01")
def test_t_hero_01_a_heroes_roster_exact_matches(
    client: TestClient,
    canonical_dataset: dict[str, object],
) -> None:
    """T-HERO-01-a (★): Heroes on C2 returns EXACTLY dA2, dA3, and dB1."""
    c2 = canonical_dataset["c2"]
    assert isinstance(c2, Campaign)

    res = client.get(f"/api/v1/public/campaigns/{c2.slug}/heroes")
    assert res.status_code == 200

    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] == 3

    hero_names = [item["first_name"] for item in data["items"]]
    assert set(hero_names) == {"متبرع_dA2", "متبرع_dA3", "متبرع_dB1"}
    assert "متبرع_dA1" not in hero_names  # Attended
    assert "متبرع_dA4" not in hero_names  # Donated without hero consent
    assert "متبرع_dA5" not in hero_names  # Attended


@pytest.mark.critical
@pytest.mark.br("BR-HERO-01")
def test_t_hero_01_b_attended_donor_with_hero_consent_omitted(
    client: TestClient,
    db_session: Session,
    canonical_dataset: dict[str, object],
) -> None:
    """T-HERO-01-b (★): Donor with status=attended and consent_hero_name=True does NOT appear in heroes."""
    c2 = canonical_dataset["c2"]
    site_a = canonical_dataset["site_a"]
    assert isinstance(c2, Campaign)
    assert isinstance(site_a, Site)

    # Add attended donor with consent_hero_name=True
    create_donor(
        db_session,
        campaign_id=c2.id,
        site_id=site_a.id,
        first_name="متبرع_حاضر_بموافقة",
        status="attended",
        consent_hero_name=True,
    )

    res = client.get(f"/api/v1/public/campaigns/{c2.slug}/heroes")
    assert res.status_code == 200
    names = [item["first_name"] for item in res.json()["items"]]
    assert "متبرع_حاضر_بموافقة" not in names


@pytest.mark.critical
@pytest.mark.br("BR-HERO-01")
def test_t_hero_01_c_heroes_on_draft_campaign_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-HERO-01-c (★): Requesting heroes on draft/unpublished campaign returns 404 not_found."""
    draft_camp = create_campaign(db_session, slug="c5-draft", is_published=False, status="upcoming")

    res = client.get(f"/api/v1/public/campaigns/{draft_camp.slug}/heroes")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


@pytest.mark.critical
@pytest.mark.br("BR-HERO-02")
def test_t_hero_02_a_hero_response_keys_strict_privacy(
    client: TestClient,
    canonical_dataset: dict[str, object],
) -> None:
    """T-HERO-02-a (★): Hero items expose strictly first_name, site_id, and site_name (no phone, blood_type, or IDs)."""
    c2 = canonical_dataset["c2"]
    assert isinstance(c2, Campaign)

    res = client.get(f"/api/v1/public/campaigns/{c2.slug}/heroes")
    assert res.status_code == 200

    items = res.json()["items"]
    assert len(items) > 0

    expected_keys = {"first_name", "site_id", "site_name"}
    for item in items:
        assert set(item.keys()) == expected_keys
        # Verify zero leakage of sensitive attributes
        assert "id" not in item
        assert "last_name" not in item
        assert "phone" not in item
        assert "blood_type" not in item
        assert "registered_by" not in item
        assert "anonymized_at" not in item
        assert "photo" not in item


@pytest.mark.br("BR-HERO-03")
def test_t_hero_03_a_heroes_ordered_by_newest_donation_first(
    client: TestClient,
    canonical_dataset: dict[str, object],
) -> None:
    """T-HERO-03-a: Heroes are ordered by donated_at desc (newest donation first)."""
    c2 = canonical_dataset["c2"]
    assert isinstance(c2, Campaign)

    res = client.get(f"/api/v1/public/campaigns/{c2.slug}/heroes")
    assert res.status_code == 200

    items = res.json()["items"]
    # In canonical dataset, dA3 donated at T2, while dA2 and dB1 donated at T1 (T2 > T1)
    assert items[0]["first_name"] == "متبرع_dA3"


@pytest.mark.br("BR-HERO-04")
def test_t_hero_04_a_heroes_filter_by_site_id(
    client: TestClient,
    canonical_dataset: dict[str, object],
) -> None:
    """T-HERO-04-a: Query param ?site_id filters heroes to the specified site only."""
    c2 = canonical_dataset["c2"]
    site_a = canonical_dataset["site_a"]
    assert isinstance(c2, Campaign)
    assert isinstance(site_a, Site)

    res = client.get(f"/api/v1/public/campaigns/{c2.slug}/heroes?site_id={site_a.id}")
    assert res.status_code == 200

    data = res.json()
    assert data["total"] == 2
    names = [item["first_name"] for item in data["items"]]
    assert set(names) == {"متبرع_dA2", "متبرع_dA3"}
    assert "متبرع_dB1" not in names


@pytest.mark.br("BR-HERO-06")
def test_t_hero_06_a_donor_without_hero_consent_omitted_from_heroes_but_counted(
    client: TestClient,
    canonical_dataset: dict[str, object],
) -> None:
    """T-HERO-06-a: dA4 (donated without hero consent) is omitted from heroes but counted in donated_count."""
    c2 = canonical_dataset["c2"]
    assert isinstance(c2, Campaign)

    # 1. Verify absent from heroes
    res_heroes = client.get(f"/api/v1/public/campaigns/{c2.slug}/heroes")
    assert res_heroes.status_code == 200
    hero_names = [item["first_name"] for item in res_heroes.json()["items"]]
    assert "متبرع_dA4" not in hero_names

    # 2. Verify counted in counters donated_count
    res_counters = client.get(f"/api/v1/public/campaigns/{c2.slug}/counters")
    assert res_counters.status_code == 200
    assert res_counters.json()["donated_count"] == 4  # dA2, dA3, dA4, dB1


# ==============================================================================
# 2. Ranking Tests (T-RANK-01..05, BR-RANK-01..05)
# ==============================================================================


@pytest.mark.br("BR-RANK-01")
def test_t_rank_01_a_live_campaign_ranking_includes_all_donated(
    client: TestClient,
    canonical_dataset: dict[str, object],
) -> None:
    """T-RANK-01-a: Site ranking counts all donated donors regardless of appearance consent."""
    c2 = canonical_dataset["c2"]
    site_a = canonical_dataset["site_a"]
    site_b = canonical_dataset["site_b"]
    assert isinstance(c2, Campaign)
    assert isinstance(site_a, Site)
    assert isinstance(site_b, Site)

    res = client.get(f"/api/v1/public/campaigns/{c2.slug}/ranking")
    assert res.status_code == 200

    ranking = res.json()
    assert len(ranking) == 2

    # Site A: dA2, dA3, dA4 (3 donated) -> Rank 1
    assert ranking[0]["site_id"] == str(site_a.id)
    assert ranking[0]["rank"] == 1
    assert ranking[0]["donors_count"] == 3

    # Site B: dB1 (1 donated) -> Rank 2
    assert ranking[1]["site_id"] == str(site_b.id)
    assert ranking[1]["rank"] == 2
    assert ranking[1]["donors_count"] == 1


@pytest.mark.br("BR-RANK-02")
def test_t_rank_02_a_participating_site_with_zero_donors_appears_with_zero(
    client: TestClient,
    db_session: Session,
    canonical_dataset: dict[str, object],
) -> None:
    """T-RANK-02-a: A third participating site with 0 donors appears in ranking with donors_count=0."""
    c2 = canonical_dataset["c2"]
    site_c = canonical_dataset["site_c"]
    assert isinstance(c2, Campaign)
    assert isinstance(site_c, Site)

    # Add Site C to participating campaign sites
    create_campaign_site(db_session, campaign_id=c2.id, site_id=site_c.id)

    res = client.get(f"/api/v1/public/campaigns/{c2.slug}/ranking")
    assert res.status_code == 200

    ranking = res.json()
    assert len(ranking) == 3

    site_c_entry = next((r for r in ranking if r["site_id"] == str(site_c.id)), None)
    assert site_c_entry is not None
    assert site_c_entry["donors_count"] == 0
    assert site_c_entry["rank"] == 3


@pytest.mark.br("BR-RANK-03")
def test_t_rank_03_a_ranking_ties_receive_same_rank_secondary_sort_by_name(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-RANK-03-a: Sites with identical counts receive the same rank (1, 1, 3) and secondary sort by name."""
    camp = create_campaign(db_session, slug="c-tie-ranking", is_published=True, status="live")

    # Three sites: Site A and Site B with 2 donors each, Site C with 1 donor
    site_alpha = create_site(db_session, is_active=True, name="أحمد - موقع")
    site_beta = create_site(db_session, is_active=True, name="بلال - موقع")
    site_gamma = create_site(db_session, is_active=True, name="جمال - موقع")

    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_alpha.id)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_beta.id)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_gamma.id)

    # 2 donors for site_alpha
    create_donor(db_session, campaign_id=camp.id, site_id=site_alpha.id, status="donated")
    create_donor(db_session, campaign_id=camp.id, site_id=site_alpha.id, status="donated")

    # 2 donors for site_beta
    create_donor(db_session, campaign_id=camp.id, site_id=site_beta.id, status="donated")
    create_donor(db_session, campaign_id=camp.id, site_id=site_beta.id, status="donated")

    # 1 donor for site_gamma
    create_donor(db_session, campaign_id=camp.id, site_id=site_gamma.id, status="donated")

    res = client.get(f"/api/v1/public/campaigns/{camp.slug}/ranking")
    assert res.status_code == 200

    ranking = res.json()
    assert len(ranking) == 3

    # Rank 1: site_alpha (count 2)
    assert ranking[0]["site_id"] == str(site_alpha.id)
    assert ranking[0]["rank"] == 1
    assert ranking[0]["donors_count"] == 2

    # Rank 1: site_beta (count 2, tied with site_alpha)
    assert ranking[1]["site_id"] == str(site_beta.id)
    assert ranking[1]["rank"] == 1
    assert ranking[1]["donors_count"] == 2

    # Rank 3: site_gamma (count 1, rank jumps to 3)
    assert ranking[2]["site_id"] == str(site_gamma.id)
    assert ranking[2]["rank"] == 3
    assert ranking[2]["donors_count"] == 1


@pytest.mark.br("BR-RANK-04")
def test_t_rank_04_a_ranking_on_draft_campaign_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-RANK-04-a: Ranking on unpublished/draft campaign returns 404 not_found."""
    draft = create_campaign(
        db_session, slug="c5-draft-ranking", is_published=False, status="upcoming"
    )

    res = client.get(f"/api/v1/public/campaigns/{draft.slug}/ranking")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


@pytest.mark.critical
@pytest.mark.br("BR-RANK-05")
def test_t_rank_05_a_ended_campaign_ranking_uses_frozen_final_stats(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-RANK-05-a (★): Ended campaign with final_stats reads counts from frozen snapshot and names from sites table."""
    site_a = create_site(db_session, is_active=True, name="موقع أ الأصلي")
    site_b = create_site(db_session, is_active=True, name="موقع ب الأصلي")

    now_utc = datetime.now(UTC)
    c3 = create_campaign(
        db_session,
        slug="c3-ended-ranking",
        status="ended",
        is_published=True,
        final_stats={
            "pledges": 120,
            "present": 65,
            "donated": 55,
            "heroes": 35,
            "by_site": [
                {
                    "site_id": str(site_a.id),
                    "name": "الاسم القديم لموقع أ",
                    "pledges": 70,
                    "present": 35,
                    "donated": 30,
                },
                {
                    "site_id": str(site_b.id),
                    "name": "الاسم القديم لموقع ب",
                    "pledges": 50,
                    "present": 30,
                    "donated": 25,
                },
            ],
            "frozen_at": now_utc.isoformat(),
        },
    )

    create_campaign_site(db_session, campaign_id=c3.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=c3.id, site_id=site_b.id)

    # Subsequent DB modification: update site name in sites table
    site_a.name = "موقع أ بعد التحديث"
    db_session.flush()

    # Add a rogue donor in DB for C3 after it ended
    create_donor(db_session, campaign_id=c3.id, site_id=site_b.id, status="donated")

    res = client.get(f"/api/v1/public/campaigns/{c3.slug}/ranking")
    assert res.status_code == 200

    ranking = res.json()
    assert len(ranking) == 2

    # Counts MUST come from frozen snapshot (30 and 25), rogue donor must NOT alter count!
    assert ranking[0]["site_id"] == str(site_a.id)
    assert ranking[0]["donors_count"] == 30
    # Site name MUST come from current sites table
    assert ranking[0]["site_name"] == "موقع أ بعد التحديث"

    assert ranking[1]["site_id"] == str(site_b.id)
    assert ranking[1]["donors_count"] == 25


@pytest.mark.critical
@pytest.mark.br("BR-RANK-05")
def test_t_rank_05_b_live_campaign_ranking_updates_live(
    client: TestClient,
    db_session: Session,
    canonical_dataset: dict[str, object],
) -> None:
    """T-RANK-05-b (★): Live campaign ranking computes live and updates immediately after a donation."""
    c2 = canonical_dataset["c2"]
    site_b = canonical_dataset["site_b"]
    assert isinstance(c2, Campaign)
    assert isinstance(site_b, Site)

    # Initial ranking: Site B has 1 donor
    res1 = client.get(f"/api/v1/public/campaigns/{c2.slug}/ranking")
    assert res1.status_code == 200
    ranking1 = res1.json()
    b_entry = next(r for r in ranking1 if r["site_id"] == str(site_b.id))
    assert b_entry["donors_count"] == 1

    # Record 3 new donations for Site B
    for _ in range(3):
        create_donor(db_session, campaign_id=c2.id, site_id=site_b.id, status="donated")

    # Site B now has 1 + 3 = 4 donors (surpassing Site A's 3 donors!)
    res2 = client.get(f"/api/v1/public/campaigns/{c2.slug}/ranking")
    assert res2.status_code == 200
    ranking2 = res2.json()
    assert ranking2[0]["site_id"] == str(site_b.id)
    assert ranking2[0]["rank"] == 1
    assert ranking2[0]["donors_count"] == 4


# ==============================================================================
# 3. Counters Tests (T-COUNT-01..06, BR-COUNT-01..06)
# ==============================================================================


@pytest.mark.br("BR-COUNT-01")
def test_t_count_01_a_counters_returns_accurate_metrics(
    client: TestClient,
    canonical_dataset: dict[str, object],
) -> None:
    """T-COUNT-01-a: Counters on C2 returns present=6, donated=4, heroes=3, pledges=3."""
    c2 = canonical_dataset["c2"]
    assert isinstance(c2, Campaign)

    res = client.get(f"/api/v1/public/campaigns/{c2.slug}/counters")
    assert res.status_code == 200

    counters = res.json()
    assert counters["pledges_count"] == 3
    assert counters["present_count"] == 6  # dA1..dA5, dB1
    assert counters["donated_count"] == 4  # dA2, dA3, dA4, dB1
    assert counters["heroes_count"] == 3  # dA2, dA3, dB1

    # Invariants (BR-COUNT-02)
    assert counters["donated_count"] <= counters["present_count"]
    assert counters["heroes_count"] <= counters["donated_count"]


@pytest.mark.critical
@pytest.mark.br("BR-COUNT-03")
def test_t_count_03_a_ended_campaign_counters_uses_frozen_final_stats(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-COUNT-03-a (★): Ended campaign with final_stats returns frozen numbers even after subsequent DB changes."""
    now_utc = datetime.now(UTC)
    c3 = create_campaign(
        db_session,
        slug="c3-ended-counters",
        status="ended",
        is_published=True,
        final_stats={
            "pledges": 120,
            "present": 65,
            "donated": 55,
            "heroes": 35,
            "frozen_at": now_utc.isoformat(),
        },
    )
    site = create_site(db_session, is_active=True)
    create_campaign_site(db_session, campaign_id=c3.id, site_id=site.id)

    # Rogue addition of donor and pledge
    create_donor(
        db_session, campaign_id=c3.id, site_id=site.id, status="donated", consent_hero_name=True
    )
    p = Pledge(campaign_id=c3.id, site_id=site.id, first_name="متعهد متأخر", phone="0550999999")
    db_session.add(p)
    db_session.flush()

    res = client.get(f"/api/v1/public/campaigns/{c3.slug}/counters")
    assert res.status_code == 200

    counters = res.json()
    # Must match frozen snapshot exactly
    assert counters["pledges_count"] == 120
    assert counters["present_count"] == 65
    assert counters["donated_count"] == 55
    assert counters["heroes_count"] == 35


@pytest.mark.critical
@pytest.mark.br("BR-COUNT-03")
def test_t_count_03_b_live_campaign_counters_updates_live(
    client: TestClient,
    db_session: Session,
    canonical_dataset: dict[str, object],
) -> None:
    """T-COUNT-03-b (★): Live campaign counters update live after a new donation."""
    c2 = canonical_dataset["c2"]
    site_a = canonical_dataset["site_a"]
    assert isinstance(c2, Campaign)
    assert isinstance(site_a, Site)

    # Initially: present=6, donated=4, heroes=3
    res1 = client.get(f"/api/v1/public/campaigns/{c2.slug}/counters")
    assert res1.status_code == 200
    assert res1.json()["donated_count"] == 4
    assert res1.json()["heroes_count"] == 3

    # Add a new hero donor
    create_donor(
        db_session,
        campaign_id=c2.id,
        site_id=site_a.id,
        status="donated",
        consent_hero_name=True,
    )

    res2 = client.get(f"/api/v1/public/campaigns/{c2.slug}/counters")
    assert res2.status_code == 200
    assert res2.json()["present_count"] == 7
    assert res2.json()["donated_count"] == 5
    assert res2.json()["heroes_count"] == 4


@pytest.mark.br("BR-COUNT-06")
def test_t_count_06_a_cache_control_headers_on_counters_heroes_ranking(
    client: TestClient,
    canonical_dataset: dict[str, object],
) -> None:
    """T-COUNT-06-a: Cache-Control: public, max-age=30 header is set on counters, heroes, and ranking."""
    c2 = canonical_dataset["c2"]
    assert isinstance(c2, Campaign)

    endpoints = [
        f"/api/v1/public/campaigns/{c2.slug}/counters",
        f"/api/v1/public/campaigns/{c2.slug}/heroes",
        f"/api/v1/public/campaigns/{c2.slug}/ranking",
    ]

    for url in endpoints:
        res = client.get(url)
        assert res.status_code == 200
        assert res.headers.get("Cache-Control") == "public, max-age=30"


@pytest.mark.br("BR-COUNT-04")
def test_t_count_04_a_purged_campaign_retains_frozen_counters(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-COUNT-04-a: After retention purge, counters remain identical (pledges preserved from final_stats)."""
    now_utc = datetime.now(UTC)
    site = create_site(db_session, is_active=True, name="موقع حملة ممسوحة")
    c4 = create_campaign(
        db_session,
        slug="c4-purged-counters",
        status="ended",
        is_published=True,
        personal_data_purged_at=now_utc,
        final_stats={
            "pledges": 95,
            "present": 60,
            "donated": 50,
            "heroes": 30,
            "frozen_at": now_utc.isoformat(),
        },
    )
    create_campaign_site(db_session, campaign_id=c4.id, site_id=site.id)

    # In DB, zero pledges exist (simulating post-purge state)
    res = client.get(f"/api/v1/public/campaigns/{c4.slug}/counters")
    assert res.status_code == 200

    counters = res.json()
    assert counters["pledges_count"] == 95
    assert counters["present_count"] == 60
    assert counters["donated_count"] == 50
    assert counters["heroes_count"] == 30


@pytest.mark.br("BR-SITE-02")
def test_t_site_02_c_deactivated_site_preserves_historical_ranking_in_ended_campaign(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-SITE-02-c: Site with donors in ended campaign that is subsequently deactivated preserves history and name."""
    site_a = create_site(db_session, is_active=True, name="موقع للتاريخ")
    now_utc = datetime.now(UTC)
    c3 = create_campaign(
        db_session,
        slug="c3-history-preserved",
        status="ended",
        is_published=True,
        final_stats={
            "pledges": 50,
            "present": 30,
            "donated": 20,
            "heroes": 10,
            "by_site": [
                {
                    "site_id": str(site_a.id),
                    "name": "موقع للتاريخ",
                    "pledges": 50,
                    "present": 30,
                    "donated": 20,
                    "heroes": 10,
                }
            ],
            "frozen_at": now_utc.isoformat(),
        },
    )
    create_campaign_site(db_session, campaign_id=c3.id, site_id=site_a.id)

    # Deactivate the site post-campaign
    site_a.is_active = False
    db_session.flush()

    # Query public ranking: Site A must still appear with rank 1, donors_count 20, and its name!
    res = client.get(f"/api/v1/public/campaigns/{c3.slug}/ranking")
    assert res.status_code == 200
    ranking = res.json()
    assert len(ranking) == 1
    assert ranking[0]["site_id"] == str(site_a.id)
    assert ranking[0]["site_name"] == "موقع للتاريخ"
    assert ranking[0]["donors_count"] == 20
    assert ranking[0]["rank"] == 1
