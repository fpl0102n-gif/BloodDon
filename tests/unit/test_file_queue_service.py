"""Unit tests for FileQueueService and file deletion queue paths (P5.8).

Per:
- docs/06-business-rules.md §8 (BR-PHOTO-10: Asynchronous physical file deletion via queue)
- docs/06-business-rules.md §7 (BR-CONSENT-07: consent_photo withdrawal deletes photos in same transaction)
- docs/06-business-rules.md §6 (BR-DONOR-11: donor deletion cascades photos to queue in same transaction)
- docs/06-business-rules.md §11 (BR-PRIV-09: donor anonymization cascades photos to queue in same transaction)
- docs/07-test-cases.md §5.15:
  - T-FILE-01-a: The four deletion paths (manual, donor cascade, consent withdrawal, anonymize)
    each create a row in file_deletion_queue in the SAME transaction.
  - T-FILE-02-a: mark_deleted populates deleted_at timestamp.
  - T-FILE-03-a: Physical storage deletion occurs via queue processing; no deletion without queue row.
"""

import io
import uuid
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import FileDeletionQueue
from app.models.user import User
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.file_queue import FileQueueRepository
from app.repositories.photos import PhotoRepository
from app.repositories.sites import SiteRepository
from app.schemas.donor import DonorUpdateIn
from app.services.donors import DonorService
from app.services.file_queue import FileQueueService
from app.services.photos import PhotoService
from app.storage.base import FakeStorage


def make_test_image() -> bytes:
    """Helper generating minimal test JPEG."""
    img = Image.new("RGB", (100, 100), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def test_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage(public_base_url="/media")


@pytest.fixture
def mock_audit_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def live_campaign(db_session: Session) -> Campaign:
    campaign = Campaign(
        id=uuid.uuid4(),
        slug="live-file-camp",
        title="حملة الطابور الحية",
        campaign_date=date(2026, 11, 20),
        status="live",
        is_current=True,
        is_published=True,
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


@pytest.fixture
def site_a(db_session: Session, live_campaign: Campaign) -> Site:
    site = Site(
        id=uuid.uuid4(),
        name="موقع أ",
        address="شارع المستشفى",
        is_active=True,
    )
    db_session.add(site)
    db_session.flush()

    cs = CampaignSite(campaign_id=live_campaign.id, site_id=site.id)
    db_session.add(cs)
    db_session.flush()
    return site


@pytest.fixture
def supervisor_user(db_session: Session, site_a: Site) -> User:
    user = User(
        id=uuid.uuid4(),
        email="sup_file_queue@blood.local",
        full_name="مشرف طابور الملفات",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
        password_hash="argon2id$mocked",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin_file_queue@blood.local",
        full_name="أدمن طابور الملفات",
        role="admin",
        site_id=None,
        is_active=True,
        password_hash="argon2id$mocked",
        totp_enabled=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def file_queue_service(
    db_session: Session,
    fake_storage: FakeStorage,
    test_clock: FakeClock,
    mock_audit_service: MagicMock,
) -> FileQueueService:
    return FileQueueService(
        file_queue_repo=FileQueueRepository(db_session),
        storage=fake_storage,
        clock=test_clock,
        audit_service=mock_audit_service,
    )


@pytest.fixture
def photo_service(
    db_session: Session,
    fake_storage: FakeStorage,
    mock_audit_service: MagicMock,
) -> PhotoService:
    return PhotoService(
        photo_repo=PhotoRepository(db_session),
        campaign_repo=CampaignRepository(db_session),
        site_repo=SiteRepository(db_session),
        donor_repo=DonorRepository(db_session),
        storage=fake_storage,
        audit_service=mock_audit_service,
    )


@pytest.fixture
def donor_service(
    db_session: Session,
    fake_storage: FakeStorage,
    test_clock: FakeClock,
    mock_audit_service: MagicMock,
) -> DonorService:
    return DonorService(
        donor_repo=DonorRepository(db_session),
        campaign_repo=CampaignRepository(db_session),
        site_repo=SiteRepository(db_session),
        clock=test_clock,
        audit_service=mock_audit_service,
    )


# ==============================================================================
# T-FILE-01-a: The Four File Deletion Paths (BR-PHOTO-10, BR-DONOR-11, BR-CONSENT-07, BR-PRIV-09)
# ==============================================================================


@pytest.mark.br("BR-PHOTO-10")
def test_t_file_01_a_path_1_manual_deletion(
    photo_service: PhotoService,
    supervisor_user: User,
    db_session: Session,
) -> None:
    """Path 1: Manual deletion of a photo stages its storage_key into file_deletion_queue in same transaction."""
    photo = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=make_test_image(),
        kind="general",
    )
    photo_id = photo.id
    storage_key = photo.storage_key

    # Execute manual delete
    photo_service.delete_supervisor_photo(supervisor_user, photo_id)

    # Verify photo entity is deleted
    assert photo_service.photo_repo.get_by_id(photo_id) is None

    # Verify queue entry exists in same transaction
    queue_item = db_session.scalar(
        select(FileDeletionQueue).where(FileDeletionQueue.storage_key == storage_key)
    )
    assert queue_item is not None
    assert queue_item.deleted_at is None


@pytest.mark.br("BR-DONOR-11")
def test_t_file_01_a_path_2_donor_cascade_deletion(
    photo_service: PhotoService,
    donor_service: DonorService,
    supervisor_user: User,
    live_campaign: Campaign,
    site_a: Site,
    db_session: Session,
) -> None:
    """Path 2: Donor deletion cascades all individual photos to file_deletion_queue in same transaction."""
    # Create donor with photo consent
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="يوسف",
        last_name="المتبرع",
        status="attended",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()

    # Upload two individual photos for this donor
    p1 = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=make_test_image(),
        kind="individual",
        donor_id=donor.id,
    )
    p2 = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=make_test_image(),
        kind="individual",
        donor_id=donor.id,
    )
    k1, k2 = p1.storage_key, p2.storage_key

    # Delete donor
    donor_service.delete_donor(supervisor_user, donor.id)

    # Verify donor is gone
    assert donor_service.donor_repo.get_by_id(donor.id) is None

    # Verify both photos are enqueued in file_deletion_queue
    q1 = db_session.scalar(select(FileDeletionQueue).where(FileDeletionQueue.storage_key == k1))
    q2 = db_session.scalar(select(FileDeletionQueue).where(FileDeletionQueue.storage_key == k2))
    assert q1 is not None
    assert q2 is not None


@pytest.mark.br("BR-CONSENT-07")
@pytest.mark.critical
def test_t_file_01_a_path_3_consent_withdrawal(
    photo_service: PhotoService,
    donor_service: DonorService,
    supervisor_user: User,
    live_campaign: Campaign,
    site_a: Site,
    db_session: Session,
) -> None:
    """Path 3: Withdrawing consent_photo deletes individual photos and stages to queue in same transaction."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="طارق",
        last_name="سالم",
        status="attended",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()

    photo = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=make_test_image(),
        kind="individual",
        donor_id=donor.id,
    )
    storage_key = photo.storage_key

    # Withdraw consent_photo via update_donor
    donor_service.update_donor(
        user=supervisor_user,
        donor_id=donor.id,
        data=DonorUpdateIn(consent_photo=False),
    )

    # Verify donor updated
    updated_donor = donor_service.donor_repo.get_by_id(donor.id)
    assert updated_donor is not None
    assert updated_donor.consent_photo is False

    # Verify photo entity was purged
    assert photo_service.photo_repo.get_by_id(photo.id) is None

    # Verify storage key is queued
    q_item = db_session.scalar(
        select(FileDeletionQueue).where(FileDeletionQueue.storage_key == storage_key)
    )
    assert q_item is not None


@pytest.mark.br("BR-PRIV-09")
@pytest.mark.critical
def test_t_file_01_a_path_4_anonymization_purge(
    photo_service: PhotoService,
    donor_service: DonorService,
    admin_user: User,
    supervisor_user: User,
    live_campaign: Campaign,
    site_a: Site,
    db_session: Session,
) -> None:
    """Path 4: Donor anonymization cascades individual photos to file_deletion_queue in same transaction."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="كريم",
        last_name="راضي",
        phone="0550123456",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()

    photo = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=make_test_image(),
        kind="individual",
        donor_id=donor.id,
    )
    storage_key = photo.storage_key

    # Admin anonymizes donor (BR-PRIV-09 ★)
    anonymized = donor_service.anonymize_donor(admin_user, donor.id)
    assert anonymized.first_name == "محذوف"
    assert anonymized.last_name is None
    assert anonymized.phone is None
    assert anonymized.blood_type is None
    assert anonymized.consent_keep_phone is False
    assert anonymized.consent_hero_name is False
    assert anonymized.consent_photo is False
    assert anonymized.consent_public_phone is False
    assert anonymized.anonymized_at is not None

    # Verify photo entity removed
    assert photo_service.photo_repo.get_by_id(photo.id) is None

    # Verify queue entry exists
    q_item = db_session.scalar(
        select(FileDeletionQueue).where(FileDeletionQueue.storage_key == storage_key)
    )
    assert q_item is not None


# ==============================================================================
# T-FILE-02-a: Mark Deleted
# ==============================================================================


def test_t_file_02_a_mark_deleted(
    file_queue_service: FileQueueService,
    admin_user: User,
    test_clock: FakeClock,
    db_session: Session,
) -> None:
    """T-FILE-02-a: Marking queue entry as deleted populates deleted_at timestamp."""
    entry = FileDeletionQueue(storage_key="photos/test/fake.jpg")
    db_session.add(entry)
    db_session.flush()

    assert entry.deleted_at is None

    updated = file_queue_service.mark_deleted(entry.id, admin_user)
    assert updated.deleted_at == test_clock.now()

    # Verify in DB
    refreshed = db_session.scalar(select(FileDeletionQueue).where(FileDeletionQueue.id == entry.id))
    assert refreshed is not None
    assert refreshed.deleted_at == test_clock.now()


# ==============================================================================
# T-FILE-03-a: Process Queue & Physical Storage Unlinking
# ==============================================================================


def test_t_file_03_a_process_queue_unlinks_storage_and_stamps_deleted_at(
    file_queue_service: FileQueueService,
    fake_storage: FakeStorage,
    test_clock: FakeClock,
    db_session: Session,
) -> None:
    """T-FILE-03-a: process_queue physically unlinks files and updates deleted_at timestamp."""
    # Place two files in storage
    k1 = "photos/batch/file1.jpg"
    k2 = "photos/batch/file2.jpg"
    fake_storage.save(k1, b"bytes1", content_type="image/jpeg")
    fake_storage.save(k2, b"bytes2", content_type="image/jpeg")

    assert fake_storage.exists(k1)
    assert fake_storage.exists(k2)

    # Enqueue both
    q1 = FileDeletionQueue(storage_key=k1)
    q2 = FileDeletionQueue(storage_key=k2)
    db_session.add_all([q1, q2])
    db_session.flush()

    # Process queue
    processed = file_queue_service.process_queue(batch_size=10)
    assert processed == 2

    # Verify files deleted from physical storage
    assert not fake_storage.exists(k1)
    assert not fake_storage.exists(k2)

    # Verify both queue entries have deleted_at stamped
    db_session.refresh(q1)
    db_session.refresh(q2)
    assert q1.deleted_at == test_clock.now()
    assert q2.deleted_at == test_clock.now()


def test_t_file_03_a_dry_run_does_not_delete(
    file_queue_service: FileQueueService,
    fake_storage: FakeStorage,
    db_session: Session,
) -> None:
    """Dry run processes count without modifying storage or database."""
    k = "photos/batch/dry_run.jpg"
    fake_storage.save(k, b"bytes", content_type="image/jpeg")

    q = FileDeletionQueue(storage_key=k)
    db_session.add(q)
    db_session.flush()

    processed = file_queue_service.process_queue(batch_size=10, dry_run=True)
    assert processed == 1

    # Verify file still exists and queue item not marked deleted
    assert fake_storage.exists(k)
    db_session.refresh(q)
    assert q.deleted_at is None
