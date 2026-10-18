"""Integration tests for Public Campaign & Site Endpoints (P6.1).

Per:
- docs/04-api.md §5 (Public caching: Cache-Control: public, max-age=300)
- docs/04-api.md §6 (Public Campaigns: /current, list, /{slug}, /{slug}/sites, /{slug}/photos)
- docs/04-api.md §8 (Public Sites: GET /public/sites)
- docs/06-business-rules.md §3 (BR-CAMP-03: draft/unpublished campaigns 404 everywhere public)
- docs/06-business-rules.md §4 (BR-SITE-02: deactivated sites hidden from public views)
- docs/06-business-rules.md §8 (BR-PHOTO-11: photos only visible for published campaigns)
- docs/07-test-cases.md §5.2 (T-CAMP-03-a ★, T-CAMP-03-b ★, T-CAMP-03-c)
- docs/07-test-cases.md §5.7 (T-PHOTO-11-a)
- docs/07-test-cases.md §5.1 (T-GEN-05-a, T-GEN-05-b, T-GEN-09-a)
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo
from tests.factories.pledge import create_pledge


@pytest.fixture
def client(test_client: TestClient) -> TestClient:
    """Convenience fixture mapping to test_client for anonymous public access."""
    return test_client


# ==============================================================================
# 1. Unpublished Campaign Invisibility (T-CAMP-03-a ★, T-CAMP-03-b ★, T-CAMP-03-c)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-CAMP-03")
def test_t_camp_03_a_draft_campaign_returns_404_on_all_public_endpoints(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-CAMP-03-a (★): Draft campaign (is_published=False) is 404 everywhere public.

    GET /public/campaigns/{slug} -> 404
    GET /public/campaigns/{slug}/sites -> 404
    GET /public/campaigns/{slug}/photos -> 404
    """
    site = create_site(db_session, is_active=True)
    draft_campaign = create_campaign(
        db_session,
        is_published=False,
        is_current=False,
        status="upcoming",
    )
    create_campaign_site(db_session, campaign_id=draft_campaign.id, site_id=site.id)
    create_photo(db_session, campaign_id=draft_campaign.id, site_id=site.id, kind="general")

    slug = draft_campaign.slug

    # 1. Campaign detail
    res_detail = client.get(f"/api/v1/public/campaigns/{slug}")
    assert res_detail.status_code == 404
    assert res_detail.json()["error"]["code"] == "not_found"

    # 2. Participating sites
    res_sites = client.get(f"/api/v1/public/campaigns/{slug}/sites")
    assert res_sites.status_code == 404
    assert res_sites.json()["error"]["code"] == "not_found"

    # 3. Public photos gallery
    res_photos = client.get(f"/api/v1/public/campaigns/{slug}/photos")
    assert res_photos.status_code == 404
    assert res_photos.json()["error"]["code"] == "not_found"


@pytest.mark.critical
@pytest.mark.br("BR-CAMP-03")
def test_t_camp_03_b_draft_campaign_excluded_from_catalog_and_current(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-CAMP-03-b (★): Draft campaign is excluded from GET /public/campaigns and /current."""
    draft_campaign = create_campaign(
        db_session,
        is_published=False,
        is_current=False,
        status="upcoming",
    )

    # 1. Excluded from public catalog list
    res_list = client.get("/api/v1/public/campaigns")
    assert res_list.status_code == 200
    catalog_items = res_list.json()
    listed_ids = [c["id"] for c in catalog_items]
    assert str(draft_campaign.id) not in listed_ids

    # 2. If no published current campaign exists, /current returns 404
    res_current = client.get("/api/v1/public/campaigns/current")
    if res_current.status_code == 200:
        assert res_current.json()["id"] != str(draft_campaign.id)
    else:
        assert res_current.status_code == 404
        assert res_current.json()["error"]["code"] == "not_found"


@pytest.mark.br("BR-CAMP-03")
def test_t_camp_03_c_publishing_campaign_makes_it_visible(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-CAMP-03-c: Once published, campaign becomes available on public endpoints."""
    site = create_site(db_session, is_active=True)
    campaign = create_campaign(
        db_session,
        is_published=True,
        is_current=True,
        status="upcoming",
    )
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site.id)
    create_photo(db_session, campaign_id=campaign.id, site_id=site.id, kind="general")

    slug = campaign.slug

    # 1. Detail returns 200
    res_detail = client.get(f"/api/v1/public/campaigns/{slug}")
    assert res_detail.status_code == 200
    assert res_detail.json()["id"] == str(campaign.id)
    assert res_detail.json()["slug"] == slug

    # 2. Current returns 200
    res_current = client.get("/api/v1/public/campaigns/current")
    assert res_current.status_code == 200
    assert res_current.json()["id"] == str(campaign.id)

    # 3. Catalog includes it
    res_list = client.get("/api/v1/public/campaigns")
    assert res_list.status_code == 200
    catalog_ids = [c["id"] for c in res_list.json()]
    assert str(campaign.id) in catalog_ids

    # 4. Sites endpoint returns 200
    res_sites = client.get(f"/api/v1/public/campaigns/{slug}/sites")
    assert res_sites.status_code == 200
    sites_items = res_sites.json()
    assert len(sites_items) == 1
    assert sites_items[0]["site_id"] == str(site.id)

    # 5. Photos endpoint returns 200
    res_photos = client.get(f"/api/v1/public/campaigns/{slug}/photos")
    assert res_photos.status_code == 200
    assert len(res_photos.json()["items"]) == 1


# ==============================================================================
# 2. Public Photos Gallery & Privacy (T-PHOTO-11-a, BR-PHOTO-11)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-11")
def test_t_photo_11_a_public_photos_gallery_and_privacy_guards(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-PHOTO-11-a: GET /public/campaigns/{slug}/photos.

    Guards:
    - Published campaign returns 200.
    - General photos are included.
    - Individual photos are included ONLY if donor has consent_photo=True and anonymized_at is None.
    - Individual photos are excluded if donor has consent_photo=False.
    - Individual photos are excluded if donor has anonymized_at is not None.
    - site_id query filter works.
    - No PII or forbidden fields in response schema.
    """
    site_a = create_site(db_session, is_active=True, name="موقع أ")
    site_b = create_site(db_session, is_active=True, name="موقع ب")

    camp = create_campaign(db_session, is_published=True, status="live")
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site_b.id)

    # 1. General photo on site A
    photo_gen_a = create_photo(
        db_session,
        campaign_id=camp.id,
        site_id=site_a.id,
        kind="general",
        caption="صورة عامة لموقع أ",
    )

    # 2. General photo on site B
    photo_gen_b = create_photo(
        db_session,
        campaign_id=camp.id,
        site_id=site_b.id,
        kind="general",
        caption="صورة عامة لموقع ب",
    )

    # 3. Consented donor on site A
    donor_consented = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_a.id,
        consent_photo=True,
    )
    photo_indiv_consented = create_photo(
        db_session,
        campaign_id=camp.id,
        site_id=site_a.id,
        kind="individual",
        donor_id=donor_consented.id,
        caption="صورة متبرع موافق",
    )

    # 4. Non-consented donor on site A (e.g. consent revoked or direct db insertion)
    donor_unconsented = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_a.id,
        consent_photo=False,
    )
    photo_indiv_unconsented = create_photo(
        db_session,
        campaign_id=camp.id,
        site_id=site_a.id,
        kind="individual",
        donor_id=donor_unconsented.id,
        caption="صورة غير موافق",
    )

    # 5. Anonymized donor on site A
    donor_anonymized = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=site_a.id,
        consent_photo=True,
        anonymized_at=datetime.now(UTC),
    )
    photo_indiv_anonymized = create_photo(
        db_session,
        campaign_id=camp.id,
        site_id=site_a.id,
        kind="individual",
        donor_id=donor_anonymized.id,
        caption="صورة متبرع مخفي الهوية",
    )

    # Fetch all photos for campaign
    res = client.get(f"/api/v1/public/campaigns/{camp.slug}/photos")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    items = data["items"]

    returned_ids = {p["id"] for p in items}

    # Verify inclusions
    assert str(photo_gen_a.id) in returned_ids
    assert str(photo_gen_b.id) in returned_ids
    assert str(photo_indiv_consented.id) in returned_ids

    # Verify exclusions (privacy guard)
    assert str(photo_indiv_unconsented.id) not in returned_ids
    assert str(photo_indiv_anonymized.id) not in returned_ids

    # Verify response schema fields: no donor_id, no storage_key, no registered_by
    for photo_item in items:
        assert set(photo_item.keys()) == {"id", "url", "caption", "site_id", "kind"}
        assert photo_item["url"].startswith("/") or photo_item["url"].startswith("http")

    # Verify site_id filter
    res_site_a = client.get(f"/api/v1/public/campaigns/{camp.slug}/photos?site_id={site_a.id}")
    assert res_site_a.status_code == 200
    site_a_ids = {p["id"] for p in res_site_a.json()["items"]}
    assert str(photo_gen_a.id) in site_a_ids
    assert str(photo_indiv_consented.id) in site_a_ids
    assert str(photo_gen_b.id) not in site_a_ids


@pytest.mark.br("BR-GEN-05")
def test_public_photos_pagination_and_limits(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-GEN-05: Limit parameter validation for public photos."""
    camp = create_campaign(db_session, is_published=True)

    # Valid limit: 1..100
    res_valid = client.get(f"/api/v1/public/campaigns/{camp.slug}/photos?limit=50")
    assert res_valid.status_code == 200

    # Invalid limits: 0, 101, all
    res_zero = client.get(f"/api/v1/public/campaigns/{camp.slug}/photos?limit=0")
    assert res_zero.status_code == 422

    res_too_large = client.get(f"/api/v1/public/campaigns/{camp.slug}/photos?limit=101")
    assert res_too_large.status_code == 422

    res_all = client.get(f"/api/v1/public/campaigns/{camp.slug}/photos?limit=all")
    assert res_all.status_code == 422


# ==============================================================================
# 3. Deactivated Sites Hidden from Public (T-SITE-02-a, BR-SITE-02)
# ==============================================================================


@pytest.mark.br("BR-SITE-02")
def test_t_site_02_a_deactivated_site_hidden_from_public(
    client: TestClient,
    db_session: Session,
) -> None:
    """T-SITE-02-a: Deactivated site is excluded from public endpoints.

    1. GET /public/sites excludes deactivated site.
    2. GET /public/campaigns/{slug}/sites excludes deactivated site even if registered in campaign_sites.
    """
    active_site = create_site(db_session, is_active=True, name="موقع نشط")
    deactivated_site = create_site(db_session, is_active=False, name="موقع معطل")

    camp = create_campaign(db_session, is_published=True)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=active_site.id)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=deactivated_site.id)

    # 1. GET /public/sites
    res_sites = client.get("/api/v1/public/sites")
    assert res_sites.status_code == 200
    public_site_ids = [s["id"] for s in res_sites.json()]
    assert str(active_site.id) in public_site_ids
    assert str(deactivated_site.id) not in public_site_ids

    # 2. GET /public/campaigns/{slug}/sites
    res_camp_sites = client.get(f"/api/v1/public/campaigns/{camp.slug}/sites")
    assert res_camp_sites.status_code == 200
    camp_site_ids = [s["site_id"] for s in res_camp_sites.json()]
    assert str(active_site.id) in camp_site_ids
    assert str(deactivated_site.id) not in camp_site_ids


# ==============================================================================
# 4. Public Sites with Pledges Count
# ==============================================================================


@pytest.mark.br("BR-PLEDGE-08")
def test_public_campaign_sites_includes_pledges_count(
    client: TestClient,
    db_session: Session,
) -> None:
    """Public campaign sites endpoint includes pledges_count without leaking pledge data."""
    site = create_site(db_session, is_active=True)
    camp = create_campaign(db_session, is_published=True)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site.id)

    # Add 2 pledges
    create_pledge(db_session, campaign_id=camp.id, site_id=site.id)
    create_pledge(db_session, campaign_id=camp.id, site_id=site.id)

    res = client.get(f"/api/v1/public/campaigns/{camp.slug}/sites")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["site_id"] == str(site.id)
    assert items[0]["pledges_count"] == 2


# ==============================================================================
# 5. Cache-Control: public, max-age=300 (docs/04-api.md §5)
# ==============================================================================


def test_public_endpoints_cache_control_header(
    client: TestClient,
    db_session: Session,
) -> None:
    """All public campaign and site content endpoints return Cache-Control: public, max-age=300."""
    site = create_site(db_session, is_active=True)
    camp = create_campaign(db_session, is_published=True, is_current=True)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site.id)

    endpoints = [
        "/api/v1/public/campaigns/current",
        "/api/v1/public/campaigns",
        f"/api/v1/public/campaigns/{camp.slug}",
        f"/api/v1/public/campaigns/{camp.slug}/sites",
        f"/api/v1/public/campaigns/{camp.slug}/photos",
        "/api/v1/public/sites",
    ]
    for url in endpoints:
        res = client.get(url)
        assert res.status_code == 200, f"Expected 200 for {url}, got {res.status_code}"
        assert res.headers.get("Cache-Control") == "public, max-age=300", (
            f"Expected Cache-Control: public, max-age=300 for {url}, got {res.headers.get('Cache-Control')}"
        )
