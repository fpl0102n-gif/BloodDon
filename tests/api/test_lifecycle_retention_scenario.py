"""End-to-End Lifecycle Scenario Test (P8.7 / T-PRIV-12).

Per:
- docs/09-roadmap.md §6 & docs/12-implementation-plan.md §11 (P8.7):
  "Lifecycle scenario: open → donors → end → frozen → fake clock +366 days → purge
   → public data intact, sensitive data gone | tests/api/ | T-PRIV-12 | Scenario green; purge twice = no change"
- docs/06-business-rules.md §3 (BR-CAMP-06, BR-CAMP-09 ★, BR-CAMP-10, BR-CAMP-11)
- docs/06-business-rules.md §6 (BR-DONOR-06, BR-DONOR-12, BR-DONOR-15)
- docs/06-business-rules.md §7 (BR-PHOTO-03, BR-PHOTO-10, BR-PHOTO-11)
- docs/06-business-rules.md §9 (BR-COUNT-03 ★, BR-RANK-05 ★, BR-HERO-01)
- docs/06-business-rules.md §12 (BR-PRIV-01..07 ★, BR-PRIV-12)
- docs/06-business-rules.md §13 (BR-AUDIT-01 ★, BR-AUDIT-05: Zero PII)
- docs/06-business-rules.md §15 (BR-FILE-01..03)
- docs/07-test-cases.md §5.10 (T-PRIV-01..07, T-PRIV-12-a)
"""

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_db,
    get_session_service,
    get_settings,
    get_storage_dep,
    record_session_reauth,
)
from app.core.clock import FakeClock
from app.main import create_app
from app.models.audit import AuditLog
from app.models.campaign import Campaign
from app.models.donor import Donor
from app.models.photo import FileDeletionQueue, Photo
from app.models.pledge import Pledge
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.repositories.file_queue import FileQueueRepository
from app.repositories.sessions import SessionRepository
from app.services.audit import AuditService
from app.services.file_queue import FileQueueService
from app.services.retention import RetentionService
from app.services.sessions import SessionService
from app.storage.base import FakeStorage
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo
from tests.factories.pledge import create_pledge
from tests.factories.user import create_user


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage(public_base_url="/media")


@pytest.fixture
def fake_clock() -> FakeClock:
    """Initialize clock on campaign day: 2026-11-20 09:00:00 UTC."""
    return FakeClock(datetime(2026, 11, 20, 9, 0, 0, tzinfo=UTC))


@pytest.fixture
def scenario_client(
    db_session: Session,
    fake_clock: FakeClock,
    fake_storage: FakeStorage,
) -> TestClient:
    app = create_app()

    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def _override_clock() -> FakeClock:
        return fake_clock

    def _override_storage() -> FakeStorage:
        return fake_storage

    def _override_session_service() -> SessionService:
        return SessionService(
            SessionRepository(db_session),
            clock=fake_clock,
            settings=get_settings(),
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_clock] = _override_clock
    app.dependency_overrides[get_storage_dep] = _override_storage
    app.dependency_overrides[get_session_service] = _override_session_service

    return TestClient(app, base_url="http://testserver")


def auth_headers(
    client: TestClient,
    db: Session,
    user: User,
    clock: FakeClock,
    *,
    mfa_verified: bool = True,
    fresh_reauth: bool = False,
) -> dict[str, str]:
    session_service = SessionService(
        SessionRepository(db),
        clock=clock,
        settings=get_settings(),
    )
    raw_token, session_obj = session_service.create_session(
        user_id=user.id,
        role=user.role,
    )
    session_obj.mfa_verified = mfa_verified
    db.flush()

    if fresh_reauth:
        record_session_reauth(session_obj.id, clock.now())
    else:
        clear_session_reauth(session_obj.id)

    client.cookies.set("session", raw_token)
    csrf_token = generate_csrf_token(raw_token)
    return {
        "X-CSRF-Token": csrf_token,
    }


# ==============================================================================
# Master End-to-End Lifecycle Scenario: P8.7 / T-PRIV-12
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PRIV-12")
@pytest.mark.br("BR-CAMP-09")
@pytest.mark.br("BR-COUNT-03")
@pytest.mark.br("BR-RANK-05")
def test_full_campaign_lifecycle_scenario_to_purge_and_frozen_stats(
    scenario_client: TestClient,
    db_session: Session,
    fake_clock: FakeClock,
    fake_storage: FakeStorage,
) -> None:
    """T-PRIV-12-a ★: Comprehensive end-to-end lifecycle scenario.

    Lifecycle stages:
    1. Setup: Live campaign with two participating sites (Central, Coastal).
    2. Operations: Public pledges, donors check-in (Hero, Non-Hero, Attended), photo uploads.
    3. Live Public Metrics: Counters, Ranking, Heroes, Photos.
    4. End Campaign: Admin ends campaign with Condition R; final_stats frozen atomically.
    5. Frozen Verification: Public stats served from frozen stats; supervisor writes rejected.
    6. Time Travel: Advance clock +366 days past the annual retention schedule (> 365 days).
    7. Retention Purge: Pledges hard deleted, donor PII wiped, Hero first name preserved,
       individual photos queued to file_deletion_queue, atmosphere photos kept.
    8. Post-Purge Invariants: Public counters, ranking, heroes, atmosphere photos 100% intact.
    9. Purge Twice = No Change: Idempotent re-run leaves identical database state.
    10. Physical Storage Deletion: Queue unlinks individual files; atmosphere photos remain.
    """
    # --------------------------------------------------------------------------
    # 1. SETUP: Users, Sites, and Campaign
    # --------------------------------------------------------------------------
    admin = create_user(
        db_session,
        email="admin.lifecycle@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )

    site_central = create_site(
        db_session,
        name="مركز الجزائر الوسطى",
        address="شارع ديدوش مراد",
        is_active=True,
    )
    site_coastal = create_site(
        db_session,
        name="مركز وهران الساحلي",
        address="شارع الواجهة البحرية",
        is_active=True,
    )

    campaign = create_campaign(
        db_session,
        slug="nov-2026-lifecycle",
        title="اليوم الموحد لدورة الحياة 2026",
        campaign_date=date(2026, 11, 20),
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site_central.id)
    create_campaign_site(db_session, campaign_id=campaign.id, site_id=site_coastal.id)

    # --------------------------------------------------------------------------
    # 2. OPERATIONAL PHASE: Pledges, Donors, and Photos
    # --------------------------------------------------------------------------
    # 3 Public pledges
    create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site_central.id,
        first_name="متعهد 1",
        phone="0550111111",
    )
    create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site_central.id,
        first_name="متعهد 2",
        phone="0550111112",
    )
    create_pledge(
        db_session,
        campaign_id=campaign.id,
        site_id=site_coastal.id,
        first_name="متعهد 3",
        phone="0550222221",
    )

    # Donors at Site Central:
    # Donor 1: Hero Donor (donated, hero_consent=True, photo_consent=True)
    hero_donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_central.id,
        first_name="فؤاد",
        last_name="البطل",
        phone="0550333331",
        blood_type="A+",
        status="donated",
        donated_at=fake_clock.now() - timedelta(hours=2),
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
    )
    # Donor 2: Non-Hero Donor (donated, hero_consent=False)
    non_hero_donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_central.id,
        first_name="مراد",
        last_name="المتبرع",
        phone="0550333332",
        blood_type="O+",
        status="donated",
        donated_at=fake_clock.now() - timedelta(hours=1),
        consent_keep_phone=True,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
    )

    # Donors at Site Coastal:
    # Donor 3: Attended only (status="attended", hero_consent=True but didn't donate)
    attended_donor = create_donor(
        db_session,
        campaign_id=campaign.id,
        site_id=site_coastal.id,
        first_name="نورة",
        last_name="الحاضرة",
        phone="0550444441",
        blood_type="B+",
        status="attended",
        donated_at=None,
        consent_keep_phone=True,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
    )

    # Photos:
    # 1. Individual photo for hero donor
    k_hero_photo = f"campaigns/{campaign.id}/sites/{site_central.id}/hero_{hero_donor.id}.jpg"
    fake_storage.save(k_hero_photo, b"hero_image_bytes", content_type="image/jpeg")
    create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site_central.id,
        kind="individual",
        donor_id=hero_donor.id,
        storage_key=k_hero_photo,
    )

    # 2. General atmosphere photo at Site Central
    k_atmo_central = f"campaigns/{campaign.id}/sites/{site_central.id}/atmosphere_central.jpg"
    fake_storage.save(k_atmo_central, b"atmo_central_bytes", content_type="image/jpeg")
    create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site_central.id,
        kind="general",
        donor_id=None,
        storage_key=k_atmo_central,
    )

    # 3. General atmosphere photo at Site Coastal
    k_atmo_coastal = f"campaigns/{campaign.id}/sites/{site_coastal.id}/atmosphere_coastal.jpg"
    fake_storage.save(k_atmo_coastal, b"atmo_coastal_bytes", content_type="image/jpeg")
    create_photo(
        db_session,
        campaign_id=campaign.id,
        site_id=site_coastal.id,
        kind="general",
        donor_id=None,
        storage_key=k_atmo_coastal,
    )

    db_session.flush()

    # --------------------------------------------------------------------------
    # 3. VERIFY LIVE PUBLIC METRICS
    # --------------------------------------------------------------------------
    # A. Public counters
    scenario_client.cookies.clear()
    res_counters = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/counters")
    assert res_counters.status_code == 200
    counters_data = res_counters.json()
    assert counters_data["pledges_count"] == 3
    assert counters_data["present_count"] == 3
    assert counters_data["donated_count"] == 2
    assert counters_data["heroes_count"] == 1

    # B. Public ranking (Site Central has 2 donors, Site Coastal has 0 donors)
    res_ranking = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/ranking")
    assert res_ranking.status_code == 200
    ranking_data = res_ranking.json()
    assert len(ranking_data) == 2
    assert ranking_data[0]["site_id"] == str(site_central.id)
    assert ranking_data[0]["donors_count"] == 2
    assert ranking_data[0]["rank"] == 1
    assert ranking_data[1]["site_id"] == str(site_coastal.id)
    assert ranking_data[1]["donors_count"] == 0
    assert ranking_data[1]["rank"] == 2

    # C. Public heroes list (Only Hero Donor appears; attended donor omitted per BR-HERO-01)
    res_heroes = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/heroes")
    assert res_heroes.status_code == 200
    heroes_data = res_heroes.json()
    assert heroes_data["total"] == 1
    assert heroes_data["items"][0]["first_name"] == "فؤاد"
    assert heroes_data["items"][0]["site_name"] == "مركز الجزائر الوسطى"
    # Ensure no sensitive PII exposed in public heroes response
    assert "last_name" not in heroes_data["items"][0]
    assert "phone" not in heroes_data["items"][0]
    assert "blood_type" not in heroes_data["items"][0]

    # D. Public photos (3 photos total)
    res_photos = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/photos")
    assert res_photos.status_code == 200
    assert res_photos.json()["total"] == 3

    # --------------------------------------------------------------------------
    # 4. END CAMPAIGN (Admin step-up Condition R)
    # --------------------------------------------------------------------------
    admin_hdrs = auth_headers(
        scenario_client, db_session, admin, fake_clock, mfa_verified=True, fresh_reauth=True
    )
    res_end = scenario_client.post(
        f"/api/v1/admin/campaigns/{campaign.id}/end",
        headers=admin_hdrs,
    )
    assert res_end.status_code == 200
    end_data = res_end.json()
    assert end_data["pledges"] == 3
    assert end_data["present"] == 3
    assert end_data["donated"] == 2
    assert end_data["heroes"] == 1
    assert len(end_data["by_site"]) == 2

    # Verify audit event emitted (BR-CAMP-09 ★, BR-AUDIT-01 ★)
    audit_end = db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_id == campaign.id,
            AuditLog.action == "campaign.end",
        )
    )
    assert audit_end is not None

    db_session.expire_all()
    refreshed_camp = db_session.get(Campaign, campaign.id)
    assert refreshed_camp is not None
    assert refreshed_camp.status == "ended"
    assert refreshed_camp.final_stats is not None

    # --------------------------------------------------------------------------
    # 5. VERIFY FROZEN METRICS (BR-COUNT-03 ★, BR-RANK-05 ★)
    # --------------------------------------------------------------------------
    scenario_client.cookies.clear()
    res_counters_frozen = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/counters")
    assert res_counters_frozen.status_code == 200
    assert res_counters_frozen.json() == counters_data

    res_ranking_frozen = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/ranking")
    assert res_ranking_frozen.status_code == 200
    assert res_ranking_frozen.json() == ranking_data

    # --------------------------------------------------------------------------
    # 6. TIME TRAVEL: Advance Clock Past Annual Retention Cutoff (> 365 Days)
    # --------------------------------------------------------------------------
    # Campaign date was 2026-11-20. Advance to 2027-11-22 (+367 days).
    fake_clock.set_time(datetime(2027, 11, 22, 12, 0, 0, tzinfo=UTC))

    audit_repo = AuditRepository(db_session)
    audit_svc = AuditService(audit_repo, clock=fake_clock)
    retention_svc = RetentionService(
        session=db_session,
        clock=fake_clock,
        audit_service=audit_svc,
    )

    eligible = retention_svc.get_eligible_campaigns()
    assert len(eligible) >= 1
    assert any(c.id == campaign.id for c in eligible)

    # --------------------------------------------------------------------------
    # 7. RETENTION PURGE EXECUTION (BR-PRIV-01..06 ★)
    # --------------------------------------------------------------------------
    purge_result = retention_svc.purge(dry_run=False)
    assert purge_result.campaigns_purged >= 1

    camp_result = next(cr for cr in purge_result.campaign_results if cr.campaign_id == campaign.id)
    assert camp_result.status == "purged"
    assert camp_result.pledges_deleted == 3
    assert camp_result.donors_anonymized == 3
    assert camp_result.heroes_retained == 1
    assert camp_result.photos_enqueued == 1

    db_session.flush()
    db_session.expire_all()

    # --------------------------------------------------------------------------
    # 8. VERIFY SENSITIVE DATA IS GONE FROM DATABASE
    # --------------------------------------------------------------------------
    # A. All pledges for the campaign are hard deleted
    campaign_pledges = db_session.scalars(
        select(Pledge).where(Pledge.campaign_id == campaign.id)
    ).all()
    assert len(campaign_pledges) == 0

    # B. Hero donor PII is wiped, but first name and hero consent are retained (BR-PRIV-03, BR-PRIV-12)
    db_hero = db_session.get(Donor, hero_donor.id)
    assert db_hero is not None
    assert db_hero.first_name == "فؤاد"  # Preserved!
    assert db_hero.consent_hero_name is True  # Preserved!
    assert db_hero.last_name is None
    assert db_hero.phone is None
    assert db_hero.blood_type is None
    assert db_hero.consent_keep_phone is False
    assert db_hero.consent_photo is False
    assert db_hero.consent_public_phone is False
    assert db_hero.anonymized_at is not None

    # C. Non-hero donated donor PII is completely wiped (first_name becomes 'محذوف')
    db_non_hero = db_session.get(Donor, non_hero_donor.id)
    assert db_non_hero is not None
    assert db_non_hero.first_name == "محذوف"
    assert db_non_hero.last_name is None
    assert db_non_hero.phone is None
    assert db_non_hero.blood_type is None
    assert db_non_hero.consent_hero_name is False
    assert db_non_hero.anonymized_at is not None

    # D. Attended-only donor PII is completely wiped (first_name becomes 'محذوف')
    db_attended = db_session.get(Donor, attended_donor.id)
    assert db_attended is not None
    assert db_attended.first_name == "محذوف"
    assert db_attended.last_name is None
    assert db_attended.phone is None
    assert db_attended.blood_type is None
    assert db_attended.consent_hero_name is False
    assert db_attended.anonymized_at is not None

    # E. Individual photos are deleted from photos table and queued in file_deletion_queue
    individual_photos = db_session.scalars(
        select(Photo).where(
            Photo.campaign_id == campaign.id,
            Photo.kind == "individual",
        )
    ).all()
    assert len(individual_photos) == 0

    queued_hero_photo = db_session.scalar(
        select(FileDeletionQueue).where(FileDeletionQueue.storage_key == k_hero_photo)
    )
    assert queued_hero_photo is not None
    assert queued_hero_photo.deleted_at is None  # Staged for physical deletion

    # F. General atmosphere photos are preserved in photos table (BR-PRIV-04 ★, BR-PRIV-12)
    atmo_photos = db_session.scalars(
        select(Photo).where(
            Photo.campaign_id == campaign.id,
            Photo.kind == "general",
        )
    ).all()
    assert len(atmo_photos) == 2

    # G. Campaign marked with personal_data_purged_at
    db_camp_post_purge = db_session.get(Campaign, campaign.id)
    assert db_camp_post_purge is not None
    assert db_camp_post_purge.personal_data_purged_at is not None

    # --------------------------------------------------------------------------
    # 9. VERIFY PUBLIC INVARIANTS REMAIN 100% INTACT (T-PRIV-12-a, BR-PRIV-12)
    # --------------------------------------------------------------------------
    scenario_client.cookies.clear()

    # A. Public counters match original frozen stats
    res_counters_post_purge = scenario_client.get(
        f"/api/v1/public/campaigns/{campaign.slug}/counters"
    )
    assert res_counters_post_purge.status_code == 200
    assert res_counters_post_purge.json() == counters_data

    # B. Public ranking matches original frozen ranking
    res_ranking_post_purge = scenario_client.get(
        f"/api/v1/public/campaigns/{campaign.slug}/ranking"
    )
    assert res_ranking_post_purge.status_code == 200
    assert res_ranking_post_purge.json() == ranking_data

    # C. Public heroes list still serves hero first name and site name
    res_heroes_post_purge = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/heroes")
    assert res_heroes_post_purge.status_code == 200
    heroes_post = res_heroes_post_purge.json()
    assert heroes_post["total"] == 1
    assert heroes_post["items"][0]["first_name"] == "فؤاد"
    assert heroes_post["items"][0]["site_name"] == "مركز الجزائر الوسطى"
    assert "last_name" not in heroes_post["items"][0]
    assert "phone" not in heroes_post["items"][0]

    # D. Public photos serves the 2 preserved atmosphere photos
    res_photos_post_purge = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/photos")
    assert res_photos_post_purge.status_code == 200
    photos_items = res_photos_post_purge.json()["items"]
    assert res_photos_post_purge.json()["total"] == 2
    assert len(photos_items) == 2
    assert all(p["kind"] == "general" for p in photos_items)

    # --------------------------------------------------------------------------
    # 10. IDEMPOTENCY: Purge Twice = No Change (BR-PRIV-07 ★, T-PRIV-07-a ★)
    # --------------------------------------------------------------------------
    second_purge = retention_svc.purge(dry_run=False)
    # The campaign is already purged (personal_data_purged_at is set), so it is not purged again
    campaigns_purged_second = [
        cr for cr in second_purge.campaign_results if cr.campaign_id == campaign.id
    ]
    assert len(campaigns_purged_second) == 0

    # Re-verify identical public invariants
    res_counters_recheck = scenario_client.get(f"/api/v1/public/campaigns/{campaign.slug}/counters")
    assert res_counters_recheck.json() == counters_data

    # --------------------------------------------------------------------------
    # 11. PHYSICAL FILE QUEUE PROCESSING (BR-FILE-02, BR-FILE-03, T-FILE-03-a)
    # --------------------------------------------------------------------------
    file_queue_repo = FileQueueRepository(db_session)
    file_queue_service = FileQueueService(
        file_queue_repo=file_queue_repo,
        storage=fake_storage,
        clock=fake_clock,
        audit_service=audit_svc,
    )

    # Before processing: hero photo exists in physical storage
    assert fake_storage.exists(k_hero_photo)
    assert fake_storage.exists(k_atmo_central)
    assert fake_storage.exists(k_atmo_coastal)

    # Process queue
    processed_count = file_queue_service.process_queue(batch_size=50)
    assert processed_count >= 1

    # After processing: individual photo unlinked, atmosphere photos remain
    assert not fake_storage.exists(k_hero_photo)
    assert fake_storage.exists(k_atmo_central)
    assert fake_storage.exists(k_atmo_coastal)

    # Queue item marked deleted
    db_session.refresh(queued_hero_photo)
    assert queued_hero_photo.deleted_at == fake_clock.now()
