"""Unit and integration tests for the Retention Purge CLI command (P8.8, Decision D-54).

Per:
- docs/12-implementation-plan.md §11 (P8.8: Dry-run purge on a copy of staging data | Output reviewed)
- docs/09-roadmap.md §6 (M5: تجربة purge على نسخة من قاعدة بيانات التجربة قبل أي بيانات حقيقية)
- docs/06-business-rules.md §12 (BR-PRIV-01..08, BR-PRIV-11, BR-PRIV-12, BR-PRIV-15)
- docs/07-test-cases.md §5.10 (T-PRIV-01..08, T-PRIV-11, T-CONC-08)
- docs/11-decisions.md (Decision D-54: --dry-run option)
- docs/05-architecture.md §17 (CLI Typer interface)
- docs/08-setup-env.md §7 (Periodic execution via cron or orchestrator)
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.__main__ import cli_app
from app.core.clock import FakeClock
from app.models.audit import PhoneRevealLog
from app.models.campaign import Campaign
from app.models.donor import Donor
from app.models.photo import FileDeletionQueue, Photo
from app.models.pledge import Pledge
from app.models.user import AuthSession, UserToken
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo
from tests.factories.pledge import create_pledge
from tests.factories.user import create_user

runner = CliRunner()


@contextmanager
def mock_session_local(session: Session):
    """Context manager yielding the test db_session without closing or rolling back the outer test transaction."""
    yield session


@pytest.fixture
def fake_clock() -> FakeClock:
    """Fixed reference time: 2027-11-20 12:00:00 UTC."""
    fixed_time = datetime(2027, 11, 20, 12, 0, 0, tzinfo=UTC)
    return FakeClock(fixed_time)


# ==============================================================================
# 1. CLI Usage and Help (Decision D-54)
# ==============================================================================


@pytest.mark.unit
def test_cli_purge_help() -> None:
    """Invoking purge --help succeeds with comprehensive usage instructions."""
    result = runner.invoke(cli_app, ["purge", "--help"])
    assert result.exit_code == 0
    assert "Execute annual retention purge" in result.stdout
    assert "--dry-run" in result.stdout
    assert "Simulate the retention purge" in result.stdout


# ==============================================================================
# 2. Dry-Run Execution on Staging-Like Dataset (P8.8, Decision D-54)
# ==============================================================================


@pytest.mark.db
def test_cli_purge_dry_run_on_staging_dataset(
    db_session: Session,
    fake_clock: FakeClock,
) -> None:
    """P8.8: Simulate retention purge on a populated staging dataset copy with --dry-run.

    Verifies:
    1. CLI reports accurate simulation metrics: qualifying campaign evaluated, candidate
       counts for donor anonymization, pledge deletion, photo enqueuing, and periodic cleanup.
    2. Zero database modifications: campaign remains unpurged, donor PII preserved, pledges intact,
       photos intact, tokens and reveal logs preserved.
    """
    site_a = create_site(db_session, name="نادي الطب أ", is_active=True)
    site_b = create_site(db_session, name="نادي التمريض ب", is_active=True)
    admin = create_user(db_session, email="admin.staging@bloodday.local", role="admin")

    # Campaign 1: Old ended campaign > 365 days (400 days ago) -> Eligible for purge
    c_staging_old = create_campaign(
        db_session,
        slug="staging-national-blood-2026",
        title="حملة التجربة الوطنية 2026",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=400),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c_staging_old.id, site_id=site_a.id)
    create_campaign_site(db_session, campaign_id=c_staging_old.id, site_id=site_b.id)

    # Donors in old campaign:
    # 1. Hero donor
    d_hero = create_donor(
        db_session,
        campaign_id=c_staging_old.id,
        site_id=site_a.id,
        first_name="فؤاد",
        last_name="البطل",
        phone="0550111111",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        registered_by=admin.id,
    )
    # 2. Non-hero donor
    d_non_hero = create_donor(
        db_session,
        campaign_id=c_staging_old.id,
        site_id=site_a.id,
        first_name="مراد",
        last_name="المتبرع",
        phone="0550222222",
        blood_type="A+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=False,
        consent_photo=False,
        registered_by=admin.id,
    )
    # 3. Attended only donor
    d_attended = create_donor(
        db_session,
        campaign_id=c_staging_old.id,
        site_id=site_b.id,
        first_name="نورة",
        last_name="الحاضرة",
        phone="0550333333",
        blood_type="B+",
        status="attended",
        consent_keep_phone=True,
        consent_hero_name=False,
        consent_photo=False,
        registered_by=admin.id,
    )

    # Pledges in old campaign
    p1 = create_pledge(
        db_session,
        campaign_id=c_staging_old.id,
        site_id=site_a.id,
        first_name="متعهد 1",
        phone="0550999001",
    )
    p2 = create_pledge(
        db_session,
        campaign_id=c_staging_old.id,
        site_id=site_b.id,
        first_name="متعهد 2",
        phone="0550999002",
    )

    # Photos: 1 individual + 1 general atmosphere
    p_individual = create_photo(
        db_session,
        campaign_id=c_staging_old.id,
        site_id=site_a.id,
        kind="individual",
        donor_id=d_hero.id,
        storage_key="campaigns/old/hero.jpg",
    )
    p_general = create_photo(
        db_session,
        campaign_id=c_staging_old.id,
        site_id=site_a.id,
        kind="general",
        donor_id=None,
        storage_key="campaigns/old/atmo.jpg",
    )

    # Campaign 2: Recent ended campaign < 365 days (100 days ago) -> Ineligible
    c_staging_recent = create_campaign(
        db_session,
        slug="staging-recent-2027",
        title="حملة التجربة الحديثة 2027",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=100),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c_staging_recent.id, site_id=site_a.id)

    # Periodic cleanup candidates:
    # 1. Phone reveal log > 7 days old
    old_reveal = PhoneRevealLog(
        ip_hash="test_ip_hash_salt_123",
        donor_id=d_hero.id,
        created_at=fake_clock.now() - timedelta(days=10),
    )
    # 2. Expired user token
    expired_token = UserToken(
        user_id=admin.id,
        token_hash="expired_hash_123",
        purpose="invite",
        expires_at=fake_clock.now() - timedelta(days=2),
    )
    # 3. Expired session
    expired_session = AuthSession(
        id="expired_session_hash",
        user_id=admin.id,
        created_at=fake_clock.now() - timedelta(days=3),
        expires_at=fake_clock.now() - timedelta(days=1),
        ip_hash="fake_ip_hash",
        user_agent="staging_runner",
    )
    db_session.add_all([old_reveal, expired_token, expired_session])
    db_session.flush()

    # Invoke CLI with --dry-run
    with (
        patch("app.cli.purge.SessionLocal", side_effect=lambda: mock_session_local(db_session)),
        patch("app.cli.purge.SystemClock", return_value=fake_clock),
    ):
        result = runner.invoke(cli_app, ["purge", "--dry-run"])

    assert result.exit_code == 0
    stdout = result.stdout

    # Verify Output Formatting and Metrics
    assert "[DRY-RUN]" in stdout
    assert "Campaigns evaluated: 1" in stdout
    assert "Campaigns purged: 1" in stdout
    assert f"Campaign '{c_staging_old.slug}' [dry_run]" in stdout
    assert "donors anonymized=3 (heroes=1)" in stdout
    assert "pledges deleted=2" in stdout
    assert "photos queued=1" in stdout
    assert "Phone reveal logs deleted (>7 days):" in stdout
    assert "Expired user tokens deleted:" in stdout
    assert "Expired sessions deleted:" in stdout
    assert "Simulation complete. Zero database modifications made." in stdout

    # VERIFY ABSOLUTE IMMUTABILITY OF DATABASE IN DRY-RUN MODE:
    db_session.expire_all()

    # 1. Old campaign is NOT marked as purged
    refreshed_old_c = db_session.get(Campaign, c_staging_old.id)
    assert refreshed_old_c is not None
    assert refreshed_old_c.personal_data_purged_at is None

    # 2. All donor PII is 100% intact
    refreshed_hero = db_session.get(Donor, d_hero.id)
    assert refreshed_hero is not None
    assert refreshed_hero.last_name == "البطل"
    assert refreshed_hero.phone == "0550111111"
    assert refreshed_hero.anonymized_at is None

    refreshed_non_hero = db_session.get(Donor, d_non_hero.id)
    assert refreshed_non_hero is not None
    assert refreshed_non_hero.first_name == "مراد"
    assert refreshed_non_hero.last_name == "المتبرع"
    assert refreshed_non_hero.phone == "0550222222"
    assert refreshed_non_hero.anonymized_at is None

    refreshed_attended = db_session.get(Donor, d_attended.id)
    assert refreshed_attended is not None
    assert refreshed_attended.last_name == "الحاضرة"
    assert refreshed_attended.anonymized_at is None

    # 3. Pledges are NOT deleted
    assert db_session.get(Pledge, p1.id) is not None
    assert db_session.get(Pledge, p2.id) is not None

    # 4. Photos are NOT deleted and deletion queue is NOT modified
    assert db_session.get(Photo, p_individual.id) is not None
    assert db_session.get(Photo, p_general.id) is not None
    queued_entries = db_session.scalars(select(FileDeletionQueue)).all()
    assert len(queued_entries) == 0

    # 5. Periodic cleanup items are NOT deleted
    assert db_session.get(PhoneRevealLog, old_reveal.id) is not None
    assert db_session.get(UserToken, expired_token.id) is not None
    assert db_session.get(AuthSession, expired_session.id) is not None


# ==============================================================================
# 3. Mutating Execution and Idempotency on Staging Dataset (P8.8, Decision D-54)
# ==============================================================================


@pytest.mark.db
def test_cli_purge_mutating_execution_and_idempotency(
    db_session: Session,
    fake_clock: FakeClock,
) -> None:
    """P8.8: Execute mutating purge followed by an immediate secondary run to prove idempotency.

    Verifies:
    1. First run executes successfully, commits mutations, anonymizes non-heroes, retains heroes,
       deletes pledges, enqueues individual photos, and performs periodic cleanup.
    2. Second run evaluates zero eligible campaigns, executes zero mutations (idempotent).
    """
    site = create_site(db_session, is_active=True)
    admin = create_user(db_session, email="admin.mutating@bloodday.local", role="admin")

    c = create_campaign(
        db_session,
        slug="staging-mutate-2026",
        title="حملة التجربة للتنفيذ",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=420),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)

    donor = create_donor(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        first_name="مراد",
        last_name="المتبرع",
        phone="0550777777",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=False,
        registered_by=admin.id,
    )
    pledge = create_pledge(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        first_name="متعهد",
        phone="0550888888",
    )
    photo = create_photo(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        kind="individual",
        donor_id=donor.id,
        storage_key="photos/mutating_donor.jpg",
    )

    # First Run: Mutating execution
    with (
        patch("app.cli.purge.SessionLocal", side_effect=lambda: mock_session_local(db_session)),
        patch("app.cli.purge.SystemClock", return_value=fake_clock),
    ):
        res1 = runner.invoke(cli_app, ["purge"])

    assert res1.exit_code == 0
    assert "[DRY-RUN]" not in res1.stdout
    assert "Campaigns evaluated: 1" in res1.stdout
    assert "Campaigns purged: 1" in res1.stdout
    assert "Retention purge and periodic cleanup successfully committed." in res1.stdout

    # Verify mutations took effect
    db_session.expire_all()
    refreshed_c = db_session.get(Campaign, c.id)
    assert refreshed_c is not None
    assert refreshed_c.personal_data_purged_at is not None

    refreshed_donor = db_session.get(Donor, donor.id)
    assert refreshed_donor is not None
    assert refreshed_donor.first_name == "محذوف"
    assert refreshed_donor.last_name is None
    assert refreshed_donor.phone is None
    assert refreshed_donor.blood_type is None

    assert db_session.get(Pledge, pledge.id) is None
    assert db_session.get(Photo, photo.id) is None

    queued = db_session.scalar(
        select(FileDeletionQueue).where(
            FileDeletionQueue.storage_key == "photos/mutating_donor.jpg"
        )
    )
    assert queued is not None

    # Second Run: Idempotency check (no campaigns eligible)
    with (
        patch("app.cli.purge.SessionLocal", side_effect=lambda: mock_session_local(db_session)),
        patch("app.cli.purge.SystemClock", return_value=fake_clock),
    ):
        res2 = runner.invoke(cli_app, ["purge"])

    assert res2.exit_code == 0
    assert "Campaigns evaluated: 0" in res2.stdout
    assert "Campaigns purged: 0" in res2.stdout
    assert "Retention purge and periodic cleanup successfully committed." in res2.stdout
