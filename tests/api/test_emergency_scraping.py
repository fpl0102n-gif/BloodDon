"""Adversarial Scraping Simulation & Anti-Harvester Defense Integration Tests (P7.5).

Verifies the platform's security and privacy defenses against automated crawlers
and malicious harvesting attacks attempting to scrape donor contact information
from the public emergency donor board.

Per:
- docs/09-roadmap.md §4 Phase 7 (Milestone M4 acceptance criteria)
- docs/12-implementation-plan.md §10 Task P7.5
- docs/06-business-rules.md §10 (BR-EMERG-01..14, BR-RATE-04, BR-RATE-05, BR-RATE-10)
- docs/07-test-cases.md §5.9 (T-EMERG-01..14, T-RATE-05, T-RATE-10)
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from scripts.scraping_attempt_emergency import (
    ScrapingAttemptResult,
    run_scraping_attempt_test,
)
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-07")
def test_p7_5_emergency_scraping_attempt_full_scenario() -> None:
    """Task P7.5: Execute full adversarial scraping simulation and assert all anti-scraping defenses hold.

    Verifications:
    1. Crawling emergency list yields 0 phone numbers across all blood groups (BR-EMERG-02).
    2. Sequential reveal attempts are strictly capped at 10 per hour (BR-RATE-05, BR-EMERG-07).
    3. Throttled responses return HTTP 429 rate_limited with Retry-After header.
    4. IP spoofing attempts via X-Forwarded-For/X-Real-IP are defeated (BR-RATE-10).
    5. Probing invalid/ineligible donors returns uniform 404s without disclosing state (BR-EMERG-06).
    6. All emergency responses enforce Cache-Control: no-store (BR-EMERG-09).
    """
    result: ScrapingAttemptResult = run_scraping_attempt_test()

    # 1. Zero phone numbers leaked in public lists
    assert not result.phone_leak_in_list_detected, (
        "Phone numbers leaked during list crawling stage!"
    )
    assert result.donors_discovered > 0, "No donors discovered during crawling stage."

    # 2. Strict rate limit enforcement at exactly 10 reveals
    assert result.rate_limit_strictly_enforced_at_10, (
        f"Rate limit not enforced at 10 (200 OK: {result.reveals_successful_200}, 429: {result.reveals_throttled_429})"
    )
    assert result.reveals_successful_200 == 10
    assert result.reveals_throttled_429 == 5
    assert len(result.revealed_phones) == 10

    # 3. Retry-After header present on throttled responses
    assert result.retry_after_header_present, "Throttled responses missing Retry-After header!"

    # 4. Anti-spoofing protection
    assert result.spoofing_bypass_prevented, (
        "Attacker successfully bypassed rate limit via IP spoofing!"
    )

    # 5. Indistinguishable 404 responses
    assert result.uniform_404_verified, (
        "Ineligible donor probe returned non-uniform error responses!"
    )

    # 6. Cache-Control: no-store enforced on all responses
    assert result.cache_control_no_store_verified, (
        "Cache-Control: no-store not verified on emergency responses!"
    )

    # 7. No security violations recorded
    assert len(result.security_violations) == 0, (
        f"Security violations: {result.security_violations}"
    )
    assert result.passed is True


@pytest.mark.critical
@pytest.mark.br("BR-EMERG-02")
def test_p7_5_emergency_list_zero_phone_sentinel_sweep(
    test_client: TestClient,
    db_session: Session,
) -> None:
    """T-EMERG-02-a ★: Sweep all blood groups and confirm absolute zero phone leakage via sentinels."""
    # Clear existing is_current flag per BR-CAMP-04 / uq_campaigns_single_current
    db_session.execute(
        update(Campaign).where(Campaign.is_current.is_(True)).values(is_current=False)
    )
    db_session.flush()

    site = create_site(db_session, is_active=True, name="مركز نقل الدم المركزي")
    camp = create_campaign(
        db_session,
        slug=f"emergency-sweep-{uuid.uuid4().hex[:8]}",
        title="حملة الطوارئ الشاملة",
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(db_session, campaign_id=camp.id, site_id=site.id)

    blood_types = ["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"]
    for i, bt in enumerate(blood_types):
        create_donor(
            db_session,
            campaign_id=camp.id,
            site_id=site.id,
            first_name=f"خفر_{i}",
            phone=f"+2135500000{i:02d}",
            blood_type=bt,
            status="donated",
            consent_public_phone=True,
        )
    db_session.flush()

    sentinel_phone_fragments = ["055000000", "+213550"]

    for bt in blood_types:
        res = test_client.get(f"/public/emergency/donors?blood_type={bt}")
        assert res.status_code == 200, res.text
        raw_text = res.text

        # 1. Assert 'phone' key never appears in JSON or raw response
        assert '"phone"' not in raw_text, f"Key 'phone' found in response for blood_type {bt}!"

        # 2. Assert no sentinel phone strings exist anywhere in payload
        for fragment in sentinel_phone_fragments:
            assert fragment not in raw_text, (
                f"Sentinel phone fragment '{fragment}' found in response!"
            )

        # 3. Check JSON structure: exactly the 5 allowed fields
        items = res.json()
        assert isinstance(items, list)
        assert len(items) >= 1
        for item in items:
            assert set(item.keys()) == {
                "donor_id",
                "first_name",
                "blood_type",
                "site_name",
                "days_since_donation",
            }
