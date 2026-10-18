"""Adversarial Scraping Attempt Tool for Emergency Contact Reveal Board (P7.5).

Simulates an adversarial harvester/crawler targeting the emergency donor list
and reveal endpoints. Verifies anti-scraping defenses:
1. Crawls emergency donor lists across blood types and confirms ZERO phone numbers leaked.
2. Executes rapid sequential reveals from a single IP and confirms strict throttling after 10 reveals (HTTP 429).
3. Attempts header spoofing (X-Forwarded-For) and confirms untrusted proxies cannot bypass the rate limit.
4. Probes ineligible/non-existent IDs and confirms uniform 404 responses (zero enumeration oracle).
5. Reports comprehensive metrics and exits with code 0 on security success, non-zero on failure.

Per:
- docs/09-roadmap.md §4 Phase 7 (Milestone M4 acceptance criteria)
- docs/12-implementation-plan.md §10 Task P7.5
- docs/06-business-rules.md §10 (BR-EMERG-01..14, BR-RATE-04/05/10)
- docs/07-test-cases.md §5.9 (T-EMERG-01..14, T-RATE-05/10)

Usage:
    uv run python scripts/scraping_attempt_emergency.py
    uv run python scripts/scraping_attempt_emergency.py --target-count 15 --ip 192.168.1.100
"""

from __future__ import annotations

import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import typer
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_rate_limiter_dep
from app.core.config import get_settings
from app.core.database import create_db_engine, get_db
from app.core.ratelimit import InMemoryRateLimiter
from app.main import create_app
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor

cli_app = typer.Typer(
    help="Execute simulated adversarial scraping attempt against the emergency donor board."
)


@dataclass
class ScrapingAttemptResult:
    """Detailed metrics and security verification outcome of the scraping attempt."""

    # Discovery stage metrics
    blood_types_queried: list[str] = field(default_factory=list)
    donors_discovered: int = 0
    phone_leak_in_list_detected: bool = False
    list_responses_count: int = 0

    # Harvesting / reveal attack metrics
    reveal_attempts_total: int = 0
    reveals_successful_200: int = 0
    reveals_throttled_429: int = 0
    reveals_not_found_404: int = 0
    revealed_phones: list[str] = field(default_factory=list)

    # Security defenses verification
    rate_limit_strictly_enforced_at_10: bool = False
    retry_after_header_present: bool = False
    spoofing_bypass_prevented: bool = False
    uniform_404_verified: bool = False
    cache_control_no_store_verified: bool = False

    # Errors or anomalies
    security_violations: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return True if all anti-scraping and privacy controls held without failure."""
        return (
            not self.phone_leak_in_list_detected
            and self.rate_limit_strictly_enforced_at_10
            and self.retry_after_header_present
            and self.spoofing_bypass_prevented
            and self.uniform_404_verified
            and self.cache_control_no_store_verified
            and len(self.security_violations) == 0
        )


def seed_emergency_test_donors(
    session: Session,
    count: int = 15,
) -> tuple[Campaign, Site, list[Donor], Donor, Donor]:
    """Seed a rich dataset of donors for scraping attempt simulation."""
    # Ensure a single live current campaign
    session.execute(update(Campaign).where(Campaign.is_current.is_(True)).values(is_current=False))
    session.flush()

    site = create_site(
        session,
        name=f"مركز طوارئ العاصمة {uuid.uuid4().hex[:6]}",
        address="شارع الشهداء، الجزائر",
        is_active=True,
    )
    camp = create_campaign(
        session,
        slug=f"emergency-scraping-camp-{uuid.uuid4().hex[:6]}",
        title="حملة الطوارئ الشاملة",
        summary="حملة لتأمين مخزون الدم للحالات الحرجة",
        content="# طوارئ\nقائمة المتبرعين للطوارئ.",  # noqa: RUF001
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(session, campaign_id=camp.id, site_id=site.id)

    now = datetime.now(UTC)
    blood_groups = ["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"]

    # 1. Seed eligible donors with public consent and donated status
    eligible_donors: list[Donor] = []
    for i in range(count):
        bg = blood_groups[i % len(blood_groups)]
        d = create_donor(
            session,
            campaign_id=camp.id,
            site_id=site.id,
            first_name=f"متطوع_{i + 1}",
            phone=f"+213550{900000 + i:06d}",
            blood_type=bg,
            status="donated",
            consent_keep_phone=True,
            consent_hero_name=True,
            consent_photo=False,
            consent_public_phone=True,
            donated_at=now - timedelta(days=20 + i),
        )
        eligible_donors.append(d)

    # 2. Control donor: attended only (not donated) even though consent_public_phone is True
    attended_control = create_donor(
        session,
        campaign_id=camp.id,
        site_id=site.id,
        first_name="حاضر_فقط",
        phone="+213550888001",
        blood_type="O+",
        status="attended",
        consent_keep_phone=True,
        consent_public_phone=True,
        donated_at=None,
    )

    # 3. Control donor: donated but consent_public_phone is False
    no_consent_control = create_donor(
        session,
        campaign_id=camp.id,
        site_id=site.id,
        first_name="متبرع_بلا_موافقة_طوارئ",
        phone="+213550888002",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_public_phone=False,
        donated_at=now - timedelta(days=30),
    )

    session.flush()
    return camp, site, eligible_donors, attended_control, no_consent_control


def execute_scraping_attempt(
    test_app: FastAPI,
    eligible_donors: list[Donor],
    attended_control: Donor,
    no_consent_control: Donor,
    adversary_ip: str = "198.51.100.77",
    total_reveal_attempts: int = 15,
) -> ScrapingAttemptResult:
    """Execute the full simulated adversarial scraping campaign."""
    res = ScrapingAttemptResult()
    blood_types = ["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"]
    res.blood_types_queried = blood_types

    # Phone regex to detect any unmasked phone leakage in payloads
    phone_pattern = re.compile(r"(\+213|0)[5-7][0-9]{8}")

    adversary_client = TestClient(test_app, client=(adversary_ip, 50000))

    # =========================================================================
    # STAGE 1: Automated Crawling of Emergency Donor Lists across Blood Types
    # =========================================================================
    discovered_donor_ids: list[str] = []

    for bt in blood_types:
        resp = adversary_client.get(f"/public/emergency/donors?blood_type={bt}")
        res.list_responses_count += 1

        if resp.status_code != 200:
            res.security_violations.append(
                f"List query for blood_type={bt} returned status {resp.status_code} instead of 200."
            )
            continue

        # Check Cache-Control header
        cc = resp.headers.get("Cache-Control", "")
        if "no-store" not in cc:
            res.security_violations.append(
                f"List query for blood_type={bt} missing 'Cache-Control: no-store' (got '{cc}')."
            )

        # Inspect raw response text and JSON payload for phone numbers (stripping UUIDs to prevent false positives)
        raw_text = resp.text
        text_without_uuids = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "", raw_text
        )
        if phone_pattern.search(text_without_uuids) or '"phone"' in raw_text:
            res.phone_leak_in_list_detected = True
            res.security_violations.append(
                f"CRITICAL PRIVACY VIOLATION: Phone number or 'phone' key leaked in emergency list for {bt}!"
            )

        items = resp.json()
        if not isinstance(items, list):
            res.security_violations.append(f"Unexpected response format for {bt}: {type(items)}")
            continue

        for item in items:
            if "phone" in item:
                res.phone_leak_in_list_detected = True
                res.security_violations.append(
                    "CRITICAL: 'phone' field present in donor item dictionary!"
                )
            donor_id = item.get("donor_id")
            if donor_id and donor_id not in discovered_donor_ids:
                discovered_donor_ids.append(donor_id)

    res.donors_discovered = len(discovered_donor_ids)

    # Verify no-store on list responses
    if res.list_responses_count > 0 and len(res.security_violations) == 0:
        res.cache_control_no_store_verified = True

    # =========================================================================
    # STAGE 2: Automated Contact Reveal Harvesting (Adversary POST loop)
    # =========================================================================
    # Combine discovered IDs with eligible donor IDs to have at least total_reveal_attempts
    target_ids = list(discovered_donor_ids)
    for d in eligible_donors:
        sid = str(d.id)
        if sid not in target_ids:
            target_ids.append(sid)

    # Execute reveals sequentially from adversary IP
    for i in range(total_reveal_attempts):
        res.reveal_attempts_total += 1
        target_id = target_ids[i % len(target_ids)]

        reveal_resp = adversary_client.post(f"/public/emergency/donors/{target_id}/reveal")

        # Verify Cache-Control: no-store
        cc_reveal = reveal_resp.headers.get("Cache-Control", "")
        if "no-store" not in cc_reveal:
            res.security_violations.append(
                f"Reveal response #{i + 1} missing 'Cache-Control: no-store' header."
            )

        if reveal_resp.status_code == 200:
            res.reveals_successful_200 += 1
            body = reveal_resp.json()
            phone = body.get("phone")
            if phone:
                res.revealed_phones.append(phone)
            else:
                res.security_violations.append(
                    f"Reveal #{i + 1} succeeded (200) but returned no phone key."
                )
            # Verify Cache-Control: no-store on successful reveal
            cc_reveal = reveal_resp.headers.get("Cache-Control", "")
            if "no-store" not in cc_reveal:
                res.security_violations.append(
                    f"Reveal response #{i + 1} missing 'Cache-Control: no-store' header."
                )
        elif reveal_resp.status_code == 429:
            res.reveals_throttled_429 += 1
            retry_after = reveal_resp.headers.get("Retry-After")
            if retry_after and int(retry_after) > 0:
                res.retry_after_header_present = True
            else:
                res.security_violations.append(
                    f"Throttled reveal #{i + 1} missing valid Retry-After header."
                )

            # Ensure throttled response leaks NO phone
            if phone_pattern.search(reveal_resp.text):
                res.security_violations.append(
                    f"CRITICAL PRIVACY VIOLATION: Throttled response #{i + 1} leaked a phone number!"
                )
        else:
            res.security_violations.append(
                f"Unexpected status {reveal_resp.status_code} on reveal #{i + 1}."
            )

    # Verify that exactly 10 requests were permitted and remaining were throttled (BR-RATE-05)
    if res.reveals_successful_200 == 10 and res.reveals_throttled_429 == (
        total_reveal_attempts - 10
    ):
        res.rate_limit_strictly_enforced_at_10 = True
    else:
        res.security_violations.append(
            f"Rate limit anomaly: expected 10 successes and {total_reveal_attempts - 10} throttled, "
            f"got {res.reveals_successful_200} successes and {res.reveals_throttled_429} throttled."
        )

    # =========================================================================
    # STAGE 3: IP Spoofing & Header Tampering Attack Test (BR-RATE-10)
    # =========================================================================
    # Adversary from the throttled IP attempts to bypass 429 by spoofing headers
    spoof_headers_cases = [
        {"X-Forwarded-For": "203.0.113.195"},
        {"X-Forwarded-For": "198.51.100.42, 10.0.0.1"},
        {"X-Real-IP": "192.0.2.1"},
        {"Client-IP": "172.16.0.5"},
    ]
    spoofing_bypassed = False
    for headers in spoof_headers_cases:
        target_id = target_ids[0]
        spoof_resp = adversary_client.post(
            f"/public/emergency/donors/{target_id}/reveal",
            headers=headers,
        )
        if spoof_resp.status_code != 429:
            spoofing_bypassed = True
            res.security_violations.append(
                f"CRITICAL SECURITY HOLE: Untrusted client bypassed rate limit using headers: {headers}!"
            )
            break

    if not spoofing_bypassed:
        res.spoofing_bypass_prevented = True

    # =========================================================================
    # STAGE 4: Probing Ineligible / Non-Existent Donors (Uniform 404 Verification)
    # =========================================================================
    # Use a fresh, unthrottled adversary IP to test eligibility probe discrimination
    probe_ip = "198.51.100.99"
    probe_client = TestClient(test_app, client=(probe_ip, 50000))
    random_uuid = uuid.uuid4()

    probe_cases = [
        ("non_existent_donor", random_uuid),
        ("attended_only_donor", attended_control.id),
        ("unconsented_donor", no_consent_control.id),
    ]

    responses: list[dict[str, Any]] = []
    all_404 = True

    for label, donor_id in probe_cases:
        probe_resp = probe_client.post(f"/public/emergency/donors/{donor_id}/reveal")
        if probe_resp.status_code != 404:
            all_404 = False
            res.security_violations.append(
                f"Probing {label} ({donor_id}) returned status {probe_resp.status_code} instead of 404."
            )
        else:
            res.reveals_not_found_404 += 1
            body = probe_resp.json()
            responses.append(body)

    # Verify uniform error code and message parity across all 404 responses
    if all_404 and len(responses) == len(probe_cases):
        first_error = responses[0].get("error", {})
        first_code = first_error.get("code")
        first_msg = first_error.get("message")
        if all(
            r.get("error", {}).get("code") == first_code
            and r.get("error", {}).get("message") == first_msg
            for r in responses[1:]
        ):
            res.uniform_404_verified = True
        else:
            res.security_violations.append(
                "Indistinguishable 404 check failed: error code or message differs between ineligible states!"
            )

    return res


def run_scraping_attempt_test() -> ScrapingAttemptResult:
    """Run simulated scraping attempt with full local in-memory FastAPI TestClient harness."""
    settings = get_settings()
    url = settings.test_database_url or settings.database_url
    engine = create_db_engine(url)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Fresh in-memory rate limiter for isolated verification
    isolated_limiter = InMemoryRateLimiter()

    test_app = create_app()

    def get_isolated_db() -> Any:
        db = session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    test_app.dependency_overrides[get_db] = get_isolated_db
    test_app.dependency_overrides[get_rate_limiter_dep] = lambda: isolated_limiter

    with session_factory() as session:
        try:
            _camp, _site, eligible_donors, attended, no_consent = seed_emergency_test_donors(
                session, count=15
            )
            session.commit()

            result = execute_scraping_attempt(
                test_app=test_app,
                eligible_donors=eligible_donors,
                attended_control=attended,
                no_consent_control=no_consent,
                adversary_ip="198.51.100.77",
                total_reveal_attempts=15,
            )
        finally:
            camp_ids = list(
                session.scalars(
                    select(Campaign.id).where(Campaign.slug.like("emergency-scraping-camp-%"))
                ).all()
            )
            if camp_ids:
                session.execute(delete(Donor).where(Donor.campaign_id.in_(camp_ids)))
                session.execute(delete(CampaignSite).where(CampaignSite.campaign_id.in_(camp_ids)))
                session.execute(delete(Campaign).where(Campaign.id.in_(camp_ids)))
            session.execute(delete(Site).where(Site.name.like("مركز طوارئ العاصمة %")))
            session.commit()

    return result


@cli_app.command()
def main(
    target_count: int = typer.Option(15, help="Number of reveal attempts to execute sequentially."),
    adversary_ip: str = typer.Option(
        "198.51.100.77", help="Source IP address of simulated scraper."
    ),
) -> None:
    """Execute adversarial scraping attempt against emergency contact endpoints."""
    typer.echo("=" * 80)
    typer.echo("[!] BLOOD DONATION PLATFORM -- ADVERSARIAL SCRAPING ATTEMPT (TASK P7.5)")
    typer.echo(f"   Target reveal attempts: {target_count} | Source IP: {adversary_ip}")
    typer.echo("=" * 80)

    result = run_scraping_attempt_test()

    typer.echo("\n[1] Crawling Stage:")
    typer.echo(f"    - Blood groups searched: {len(result.blood_types_queried)}")
    typer.echo(f"    - Discovered donors: {result.donors_discovered}")
    typer.echo(
        f"    - Phone leakage in emergency list: {'[FAIL] DETECTED!' if result.phone_leak_in_list_detected else '[PASS] ZERO (STRICTLY BLOCKED)'}"
    )

    typer.echo("\n[2] Reveal Harvesting Attack Stage:")
    typer.echo(f"    - Total reveal requests: {result.reveal_attempts_total}")
    typer.echo(
        f"    - Successful reveals (200 OK): {result.reveals_successful_200} (Maximum permitted: 10)"
    )
    typer.echo(f"    - Throttled reveals (429 Rate Limited): {result.reveals_throttled_429}")
    typer.echo(
        f"    - Rate limit enforced strictly after 10: {'[PASS] YES' if result.rate_limit_strictly_enforced_at_10 else '[FAIL]'}"
    )
    typer.echo(
        f"    - Retry-After header present: {'[PASS] YES' if result.retry_after_header_present else '[FAIL]'}"
    )

    typer.echo("\n[3] Evasion & Header Spoofing Attack Stage:")
    typer.echo(
        f"    - X-Forwarded-For spoofing prevented: {'[PASS] YES (PEER IP BOUND)' if result.spoofing_bypass_prevented else '[FAIL] (BYPASSED)'}"
    )

    typer.echo("\n[4] Donor Enumeration Probe Stage:")
    typer.echo(
        f"    - Uniform indistinguishable 404s: {'[PASS] VERIFIED (ZERO ORACLE LEAK)' if result.uniform_404_verified else '[FAIL]'}"
    )
    typer.echo(
        f"    - Cache-Control no-store enforced: {'[PASS] VERIFIED' if result.cache_control_no_store_verified else '[FAIL]'}"
    )

    if result.security_violations:
        typer.echo("\n[!] SECURITY VIOLATIONS DETECTED:")
        for v in result.security_violations:
            typer.echo(f"    - {v}")
        typer.echo("\n[OVERALL RESULT]: [FAIL] -- SECURITY DEFENSES COMPROMISED")
        sys.exit(1)
    else:
        typer.echo("\n" + "=" * 80)
        typer.echo("[OVERALL RESULT]: [PASS] -- ALL ANTI-SCRAPING CONTROLS VERIFIED")
        typer.echo(
            "   The platform successfully throttled the scraping attack after exactly 10 reveals,"
        )
        typer.echo(
            "   leaked 0 phone numbers in public lists, resisted IP spoofing, and exposed no oracle."
        )
        typer.echo("=" * 80)
        sys.exit(0)


if __name__ == "__main__":
    cli_app()
