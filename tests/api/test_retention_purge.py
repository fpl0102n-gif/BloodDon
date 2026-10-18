"""Integration tests for Retention Purge, Anonymization, and Periodic Cleanup (P8.3).

Per:
- docs/06-business-rules.md §12 (BR-PRIV-01..08, BR-PRIV-11, BR-PRIV-12, BR-PRIV-15)
- docs/07-test-cases.md §5.10 (T-PRIV-01..08, T-PRIV-11, T-CONC-08)
- docs/11-decisions.md (Decision D-54: --dry-run option)
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.__main__ import cli_app
from app.core.clock import FakeClock
from app.models.audit import PhoneRevealLog
from app.models.photo import FileDeletionQueue, Photo
from app.models.pledge import Pledge
from app.models.user import AuthSession, User, UserToken
from app.repositories.audit import AuditRepository
from app.services.audit import AuditService
from app.services.retention import RetentionService
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor


@pytest.fixture
def fake_clock() -> FakeClock:
    """Fixed reference time: 2027-11-20 12:00:00 UTC."""
    fixed_time = datetime(2027, 11, 20, 12, 0, 0, tzinfo=UTC)
    return FakeClock(fixed_time)


@pytest.fixture
def retention_service(db_session: Session, fake_clock: FakeClock) -> RetentionService:
    audit_repo = AuditRepository(db_session)
    audit_service = AuditService(audit_repo, clock=fake_clock)
    return RetentionService(
        session=db_session,
        clock=fake_clock,
        audit_service=audit_service,
    )


# ==============================================================================
# 1. Eligibility Filtering (BR-PRIV-01, T-PRIV-01-a, T-PRIV-01-b)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-01")
def test_t_priv_01_a_eligible_campaigns_filtering(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-01-a (★): Clock fixed; campaigns: ended 13m ago, ended 11m ago, live, upcoming. Only first is purged."""
    # fake_clock.today() == 2027-11-20
    site = create_site(db_session, is_active=True)

    # 1. Ended 13 months ago (eligible)
    c_old = create_campaign(
        db_session,
        slug="c-old-13m",
        status="ended",
        campaign_date=date(2026, 10, 20),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c_old.id, site_id=site.id)

    # 2. Ended 11 months ago (ineligible - < 1 year)
    c_recent = create_campaign(
        db_session,
        slug="c-recent-11m",
        status="ended",
        campaign_date=date(2026, 12, 20),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c_recent.id, site_id=site.id)

    # 3. Live campaign (ineligible - not ended)
    c_live = create_campaign(
        db_session,
        slug="c-live-old",
        status="live",
        campaign_date=date(2026, 10, 20),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c_live.id, site_id=site.id)

    # 4. Upcoming campaign (ineligible - not ended)
    c_upcoming = create_campaign(
        db_session,
        slug="c-upcoming-old",
        status="upcoming",
        campaign_date=date(2026, 10, 20),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c_upcoming.id, site_id=site.id)

    db_session.flush()

    res = retention_service.purge(dry_run=False)

    assert res.campaigns_evaluated == 1
    assert res.campaigns_purged == 1
    assert res.campaign_results[0].slug == "c-old-13m"

    db_session.refresh(c_old)
    db_session.refresh(c_recent)
    db_session.refresh(c_live)
    db_session.refresh(c_upcoming)

    assert c_old.personal_data_purged_at is not None
    assert c_recent.personal_data_purged_at is None
    assert c_live.personal_data_purged_at is None
    assert c_upcoming.personal_data_purged_at is None


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-01")
def test_t_priv_01_b_boundary_condition_strictly_more_than_one_year(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-01-b (★): Exactly 365 days ago not purged; 366 days ago IS purged."""
    site = create_site(db_session, is_active=True)
    today = fake_clock.today()  # 2027-11-20

    # Exactly 365 days ago (2026-11-20) -> Ineligible (must be strictly > 365 days)
    c_exact = create_campaign(
        db_session,
        slug="c-exact-365d",
        status="ended",
        campaign_date=today - timedelta(days=365),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c_exact.id, site_id=site.id)

    # 366 days ago (2026-11-19) -> Eligible (> 1 year)
    c_366d = create_campaign(
        db_session,
        slug="c-older-366d",
        status="ended",
        campaign_date=today - timedelta(days=366),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c_366d.id, site_id=site.id)

    db_session.flush()

    res = retention_service.purge(dry_run=False)

    assert res.campaigns_evaluated == 1
    assert res.campaigns_purged == 1
    assert res.campaign_results[0].slug == "c-older-366d"

    db_session.refresh(c_exact)
    db_session.refresh(c_366d)

    assert c_exact.personal_data_purged_at is None
    assert c_366d.personal_data_purged_at is not None


# ==============================================================================
# 2. Donor Anonymization & Hero Retention (BR-PRIV-02, BR-PRIV-03)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-02")
def test_t_priv_02_a_donor_fields_anonymized(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-02-a (★): last_name, phone, blood_type set to NULL; consents reset; anonymized_at set."""
    site = create_site(db_session, is_active=True)
    c = create_campaign(
        db_session,
        slug="c-anon-fields",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=400),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)

    donor = create_donor(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        first_name="علي",
        last_name="بن صالح",
        phone="0550123456",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_public_phone=True,
        consent_photo=True,
        consent_hero_name=True,
    )
    db_session.flush()

    res = retention_service.purge(dry_run=False)
    assert res.campaigns_purged == 1

    db_session.refresh(donor)
    assert donor.last_name is None
    assert donor.phone is None
    assert donor.blood_type is None
    assert donor.consent_keep_phone is False
    assert donor.consent_public_phone is False
    assert donor.consent_photo is False
    assert donor.anonymized_at == fake_clock.now()


@pytest.mark.br("BR-PRIV-03")
def test_t_priv_03_a_hero_retains_first_name_non_hero_becomes_deleted(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-03-a: Hero keeps first_name; non-hero first_name overwritten to 'محذوف'."""
    site = create_site(db_session, is_active=True)
    c = create_campaign(
        db_session,
        slug="c-heroes-retention",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=400),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)

    # Hero donor
    hero = create_donor(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        first_name="يوسف",
        status="donated",
        consent_hero_name=True,
    )
    # Non-hero donor
    non_hero = create_donor(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        first_name="كريم",
        status="attended",
        consent_hero_name=False,
    )
    db_session.flush()

    retention_service.purge(dry_run=False)

    db_session.refresh(hero)
    db_session.refresh(non_hero)

    assert hero.first_name == "يوسف"
    assert hero.consent_hero_name is True
    assert non_hero.first_name == "محذوف"


# ==============================================================================
# 3. Pledges, Photos, and Final Stats (BR-PRIV-04, BR-PRIV-05, BR-PRIV-06)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-04")
def test_t_priv_04_a_pledges_deleted_individual_photos_enqueued_general_photos_kept(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-04-a (★): Pledges deleted, individual photos enqueued and deleted, atmosphere photos kept."""
    site = create_site(db_session, is_active=True)
    c = create_campaign(
        db_session,
        slug="c-photos-pledges",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=400),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)

    # 1. Pledges
    p1 = Pledge(campaign_id=c.id, site_id=site.id, first_name="متعهد 1", phone="0550111111")
    p2 = Pledge(campaign_id=c.id, site_id=site.id, first_name="متعهد 2", phone="0550222222")
    db_session.add_all([p1, p2])

    # 2. Donor
    donor = create_donor(
        db_session, campaign_id=c.id, site_id=site.id, status="donated", consent_hero_name=True
    )
    db_session.flush()

    # 3. Individual photo (linked to donor)
    photo_indiv = Photo(
        campaign_id=c.id,
        site_id=site.id,
        donor_id=donor.id,
        kind="individual",
        storage_key="photos/campaign-1/donor-1.jpg",
        mime_type="image/jpeg",
        size_bytes=1024,
    )
    # 4. General atmosphere photo (donor_id is NULL)
    photo_general = Photo(
        campaign_id=c.id,
        site_id=site.id,
        donor_id=None,
        kind="general",
        storage_key="photos/campaign-1/atmosphere.jpg",
        mime_type="image/jpeg",
        size_bytes=2048,
    )
    db_session.add_all([photo_indiv, photo_general])
    db_session.flush()

    res = retention_service.purge(dry_run=False)

    assert res.campaigns_purged == 1
    assert res.campaign_results[0].pledges_deleted == 2
    assert res.campaign_results[0].photos_enqueued == 1

    # In DB: pledges are 0
    remaining_pledges = db_session.scalars(select(Pledge).where(Pledge.campaign_id == c.id)).all()
    assert len(remaining_pledges) == 0

    # In DB: individual photo is deleted
    assert db_session.get(Photo, photo_indiv.id) is None

    # In DB: individual photo's storage key is queued in file_deletion_queue
    queued_item = db_session.scalars(
        select(FileDeletionQueue).where(
            FileDeletionQueue.storage_key == "photos/campaign-1/donor-1.jpg"
        )
    ).first()
    assert queued_item is not None

    # In DB: general atmosphere photo is PRESERVED per BR-PRIV-12
    assert db_session.get(Photo, photo_general.id) is not None


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-05")
def test_t_priv_05_a_final_stats_computed_before_purging_if_null(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-05-a (★): If final_stats is NULL, compute and freeze it before any deletions occur."""
    site = create_site(db_session, is_active=True, name="موقع إحصائيات")
    c = create_campaign(
        db_session,
        slug="c-compute-stats",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=400),
        is_published=True,
        final_stats=None,  # Missing final_stats!
    )
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)

    # 2 Pledges
    p1 = Pledge(campaign_id=c.id, site_id=site.id, first_name="م 1", phone="0550111111")
    p2 = Pledge(campaign_id=c.id, site_id=site.id, first_name="م 2", phone="0550222222")
    db_session.add_all([p1, p2])

    # 1 Hero donor, 1 Attended
    create_donor(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        status="donated",
        consent_hero_name=True,
    )
    create_donor(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        status="attended",
    )
    db_session.flush()

    res = retention_service.purge(dry_run=False)

    assert res.campaigns_purged == 1
    assert res.campaign_results[0].final_stats_generated is True

    db_session.refresh(c)
    assert c.final_stats is not None
    # Frozen stats must accurately capture numbers before pledges/donors were deleted/anonymized!
    assert c.final_stats["pledges"] == 2
    assert c.final_stats["present"] == 2
    assert c.final_stats["donated"] == 1
    assert c.final_stats["heroes"] == 1
    assert len(c.final_stats["by_site"]) == 1


# ==============================================================================
# 4. Idempotency & Per-Campaign Isolation (BR-PRIV-07, BR-PRIV-08)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-07")
def test_t_priv_07_a_purge_is_idempotent(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-07-a (★): Running purge repeatedly causes no errors and zero state mutation on second run."""
    site = create_site(db_session, is_active=True)
    c = create_campaign(
        db_session,
        slug="c-idempotent",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=400),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)
    create_donor(db_session, campaign_id=c.id, site_id=site.id, status="donated")
    db_session.flush()

    # First run
    res1 = retention_service.purge(dry_run=False)
    assert res1.campaigns_purged == 1

    db_session.refresh(c)
    first_purged_at = c.personal_data_purged_at
    assert first_purged_at is not None

    # Second run
    res2 = retention_service.purge(dry_run=False)
    assert res2.campaigns_evaluated == 0
    assert res2.campaigns_purged == 0

    db_session.refresh(c)
    assert c.personal_data_purged_at == first_purged_at


@pytest.mark.br("BR-PRIV-08")
def test_t_priv_08_a_per_campaign_transaction_isolation(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-08-a: Failure in one campaign rolls back only that campaign, allowing other campaigns to succeed."""
    site = create_site(db_session, is_active=True)
    c1 = create_campaign(
        db_session,
        slug="c-failing",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=500),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c1.id, site_id=site.id)
    create_donor(db_session, campaign_id=c1.id, site_id=site.id, status="donated")

    c2 = create_campaign(
        db_session,
        slug="c-succeeding",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=400),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c2.id, site_id=site.id)
    create_donor(db_session, campaign_id=c2.id, site_id=site.id, status="donated")
    db_session.flush()

    orig_purge_single = retention_service._purge_single_campaign

    def mocked_purge_single(campaign_id: uuid.UUID, now: datetime):
        if campaign_id == c1.id:
            raise RuntimeError("Injected database failure on campaign 1")
        return orig_purge_single(campaign_id, now)

    with patch.object(retention_service, "_purge_single_campaign", side_effect=mocked_purge_single):
        res = retention_service.purge(dry_run=False)

    assert res.campaigns_evaluated == 2
    assert res.campaigns_purged == 1
    assert res.campaigns_failed == 1

    db_session.refresh(c1)
    db_session.refresh(c2)

    # c1 failed and was rolled back
    assert c1.personal_data_purged_at is None

    # c2 succeeded
    assert c2.personal_data_purged_at is not None


# ==============================================================================
# 5. Periodic Cleanup (BR-PRIV-11, T-PRIV-11-a)
# ==============================================================================


@pytest.mark.br("BR-PRIV-11")
def test_t_priv_11_a_periodic_cleanup_logs_tokens_sessions(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """T-PRIV-11-a: Cleans reveals > 7 days, expired tokens, and expired sessions; keeps recent/active."""
    now = fake_clock.now()

    # 1. PhoneRevealLog: 8 days ago (expired), 2 days ago (recent)
    reveal_old = PhoneRevealLog(
        ip_hash="hash_old",
        donor_id=uuid.uuid4(),
        created_at=now - timedelta(days=8),
    )
    reveal_recent = PhoneRevealLog(
        ip_hash="hash_recent",
        donor_id=uuid.uuid4(),
        created_at=now - timedelta(days=2),
    )
    db_session.add_all([reveal_old, reveal_recent])

    # 2. UserToken: expired vs active
    user = User(
        email="test_token@bloodday.dz",
        full_name="مستخدم تجريبي",
        password_hash="argon2id$mock",
        role="admin",
        site_id=None,
    )
    db_session.add(user)
    db_session.flush()

    token_expired = UserToken(
        user_id=user.id,
        purpose="invite",
        token_hash="hash_exp",
        expires_at=now - timedelta(hours=2),
    )
    token_active = UserToken(
        user_id=user.id,
        purpose="invite",
        token_hash="hash_act",
        expires_at=now + timedelta(hours=24),
    )
    db_session.add_all([token_expired, token_active])

    # 3. AuthSession: expired vs active
    session_expired = AuthSession(
        id="sess_expired",
        user_id=user.id,
        expires_at=now - timedelta(hours=1),
    )
    session_active = AuthSession(
        id="sess_active",
        user_id=user.id,
        expires_at=now + timedelta(days=7),
    )
    db_session.add_all([session_expired, session_active])
    db_session.flush()

    res = retention_service.purge(dry_run=False)

    assert res.phone_reveals_deleted >= 1
    assert res.tokens_deleted >= 1
    assert res.sessions_deleted >= 1

    # Verify old reveal deleted, recent reveal kept
    assert db_session.get(PhoneRevealLog, reveal_old.id) is None
    assert db_session.get(PhoneRevealLog, reveal_recent.id) is not None

    # Verify expired token deleted, active token kept
    assert db_session.get(UserToken, token_expired.id) is None
    assert db_session.get(UserToken, token_active.id) is not None

    # Verify expired session deleted, active session kept
    assert db_session.get(AuthSession, session_expired.id) is None
    assert db_session.get(AuthSession, session_active.id) is not None


# ==============================================================================
# 6. Dry-run Mode & CLI Execution (Decision D-54)
# ==============================================================================


def test_retention_purge_dry_run_simulation_leaves_db_untouched(
    db_session: Session,
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """Decision D-54: --dry-run produces accurate simulation without committing mutations."""
    site = create_site(db_session, is_active=True)
    c = create_campaign(
        db_session,
        slug="c-dry-run-check",
        status="ended",
        campaign_date=fake_clock.today() - timedelta(days=400),
        is_published=True,
    )
    create_campaign_site(db_session, campaign_id=c.id, site_id=site.id)
    donor = create_donor(
        db_session,
        campaign_id=c.id,
        site_id=site.id,
        first_name="سفيان",
        last_name="قديم",
        status="donated",
    )
    pledge = Pledge(campaign_id=c.id, site_id=site.id, first_name="متعهد", phone="0550000000")
    db_session.add(pledge)
    db_session.flush()

    res = retention_service.purge(dry_run=True)

    assert res.dry_run is True
    assert res.campaigns_evaluated == 1
    assert res.campaign_results[0].donors_anonymized == 1
    assert res.campaign_results[0].pledges_deleted == 1

    db_session.refresh(c)
    db_session.refresh(donor)

    # Invariants: Zero modifications made to DB!
    assert c.personal_data_purged_at is None
    assert donor.last_name == "قديم"
    assert donor.anonymized_at is None
    assert db_session.get(Pledge, pledge.id) is not None


def test_cli_purge_command_execution() -> None:
    """Test CLI purge command invocation via Typer CliRunner."""
    runner = CliRunner()
    res = runner.invoke(cli_app, ["purge", "--dry-run"])
    assert res.exit_code == 0
    assert "[DRY-RUN]" in res.stdout
    assert "Simulation complete" in res.stdout
