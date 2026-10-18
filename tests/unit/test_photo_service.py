"""Unit tests for PhotoService domain logic (P5.6).

Per:
- docs/06-business-rules.md §8 (BR-PHOTO-01..12)
  - BR-PHOTO-01: Two kinds: 'general' and 'individual'.
  - BR-PHOTO-02: 'individual' requires donor_id, 'general' has donor_id=None.
  - BR-PHOTO-03 (★): Individual photo requires same site + live campaign AND consent_photo=true.
  - BR-PHOTO-04: Supervisor uploads for assigned site during live campaign.
  - BR-PHOTO-05 (★): Verified image content format.
  - BR-PHOTO-06: 5MB size limit.
  - BR-PHOTO-07 (★): Strip EXIF, downscale <= 1600px.
  - BR-PHOTO-08: Server-generated UUID key, client filename discarded.
  - BR-PHOTO-09: DB failure cleans up storage file immediately (no orphans).
  - BR-PHOTO-12: Caption <= 200 chars, rejects '<' and '>'.
- docs/07-test-cases.md §5.7 (T-PHOTO-03..12)
"""

import io
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    CampaignPurgedError,
    NoLiveCampaignError,
    NotFoundError,
    PhotoConsentMissingError,
    ValidationError,
)
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.user import User
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.photos import PhotoRepository
from app.repositories.sites import SiteRepository
from app.services.audit import AuditService
from app.services.photos import PhotoService
from app.storage.base import FakeStorage


def make_test_image(
    fmt: str = "JPEG",
    size: tuple[int, int] = (100, 100),
    color: tuple[int, int, int] = (200, 50, 50),
) -> bytes:
    """Helper creating simple test image bytes."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def fake_storage() -> FakeStorage:
    """In-memory FakeStorage instance."""
    return FakeStorage(public_base_url="/media")


@pytest.fixture
def mock_audit_service() -> MagicMock:
    """Mock AuditService for verifying zero PII event logging."""
    return MagicMock(spec=AuditService)


@pytest.fixture
def live_campaign(db_session: Session) -> Campaign:
    """Fixture providing a live campaign entity."""
    campaign = Campaign(
        id=uuid.uuid4(),
        slug="live-photo-camp",
        title="Live Photo Campaign",
        status="live",
        is_published=True,
        is_current=True,
        campaign_date=datetime.now(UTC).date(),
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


@pytest.fixture
def site_a(db_session: Session, live_campaign: Campaign) -> Site:
    """Participating site A."""
    site = Site(
        id=uuid.uuid4(),
        name="موقع أ",
        address="شارع الجامعة",
        is_active=True,
    )
    db_session.add(site)
    db_session.flush()

    cs = CampaignSite(
        campaign_id=live_campaign.id,
        site_id=site.id,
    )
    db_session.add(cs)
    db_session.flush()
    return site


@pytest.fixture
def site_b(db_session: Session, live_campaign: Campaign) -> Site:
    """Participating site B."""
    site = Site(
        id=uuid.uuid4(),
        name="موقع ب",
        address="شارع المستشفى",
        is_active=True,
    )
    db_session.add(site)
    db_session.flush()

    cs = CampaignSite(
        campaign_id=live_campaign.id,
        site_id=site.id,
    )
    db_session.add(cs)
    db_session.flush()
    return site


@pytest.fixture
def supervisor_user(db_session: Session, site_a: Site) -> User:
    """Active supervisor user assigned to site A."""
    user = User(
        id=uuid.uuid4(),
        email="sup.photos@blood.local",
        full_name="مشرف الصور",
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
    """Active administrator user."""
    user = User(
        id=uuid.uuid4(),
        email="admin.photos@blood.local",
        full_name="أدمن الصور",
        role="admin",
        site_id=None,
        is_active=True,
        password_hash="argon2id$mocked",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def donor_with_photo_consent(
    db_session: Session,
    live_campaign: Campaign,
    site_a: Site,
) -> Donor:
    """Donor at site A with consent_photo=True (dA2)."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="سامي",
        last_name="علي",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()
    return donor


@pytest.fixture
def donor_without_photo_consent(
    db_session: Session,
    live_campaign: Campaign,
    site_a: Site,
) -> Donor:
    """Donor at site A with consent_photo=False (dA1)."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_a.id,
        first_name="عمر",
        last_name="خالد",
        status="attended",
        consent_keep_phone=False,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()
    return donor


@pytest.fixture
def donor_site_b(
    db_session: Session,
    live_campaign: Campaign,
    site_b: Site,
) -> Donor:
    """Donor at foreign site B (dB1)."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=live_campaign.id,
        site_id=site_b.id,
        first_name="بلال",
        last_name="حسن",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=False,
    )
    db_session.add(donor)
    db_session.flush()
    return donor


@pytest.fixture
def photo_service(
    db_session: Session,
    fake_storage: FakeStorage,
    mock_audit_service: MagicMock,
) -> PhotoService:
    """Wired PhotoService with database repositories and test doubles."""
    return PhotoService(
        photo_repo=PhotoRepository(db_session),
        campaign_repo=CampaignRepository(db_session),
        site_repo=SiteRepository(db_session),
        donor_repo=DonorRepository(db_session),
        storage=fake_storage,
        audit_service=mock_audit_service,
    )


# ==============================================================================
# Unit Test Cases
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-03")
def test_t_photo_03_a_photo_consent_missing(
    photo_service: PhotoService,
    supervisor_user: User,
    donor_without_photo_consent: Donor,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-03-a (★): Individual photo for donor without photo consent raises 422 photo_consent_missing."""
    raw_img = make_test_image()
    initial_files_count = len(fake_storage._files)

    with pytest.raises(PhotoConsentMissingError, match="requires explicit donor photo consent"):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="individual",
            donor_id=donor_without_photo_consent.id,
        )

    # Verify no file saved to storage
    assert len(fake_storage._files) == initial_files_count


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-03")
def test_t_photo_03_b_individual_photo_with_consent_succeeds(
    photo_service: PhotoService,
    supervisor_user: User,
    donor_with_photo_consent: Donor,
    fake_storage: FakeStorage,
    mock_audit_service: MagicMock,
) -> None:
    """T-PHOTO-03-b (★): Individual photo for donor with consent_photo=True succeeds."""
    raw_img = make_test_image()
    photo = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=raw_img,
        kind="individual",
        donor_id=donor_with_photo_consent.id,
        caption="متبرع بطل",
    )

    assert photo.id is not None
    assert photo.kind == "individual"
    assert photo.donor_id == donor_with_photo_consent.id
    assert photo.caption == "متبرع بطل"
    assert fake_storage.exists(photo.storage_key)

    # Verify audit event photo.upload logged without PII
    mock_audit_service.log_event.assert_called_once()
    call_args = mock_audit_service.log_event.call_args[1]
    assert call_args["action"] == "photo.upload"
    assert call_args["entity_type"] == "photo"
    assert call_args["entity_id"] == photo.id
    assert call_args["actor_user_id"] == supervisor_user.id
    assert "first_name" not in call_args["details"]
    assert "phone" not in call_args["details"]


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-03")
def test_t_photo_03_c_foreign_site_donor_returns_not_found(
    photo_service: PhotoService,
    supervisor_user: User,
    donor_site_b: Donor,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-03-c (★): sup_a uploading individual photo for dB1 (site B) returns 404 not_found, never 403."""
    raw_img = make_test_image()
    with pytest.raises(NotFoundError, match=f"Donor with ID '{donor_site_b.id}' not found"):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="individual",
            donor_id=donor_site_b.id,
        )


@pytest.mark.br("BR-PHOTO-04")
def test_t_photo_04_a_general_photo_for_site_succeeds(
    photo_service: PhotoService,
    supervisor_user: User,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-04-a: Supervisor uploads general atmosphere photo for their site during live campaign."""
    raw_img = make_test_image()
    photo = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=raw_img,
        kind="general",
        donor_id=None,
        caption="أجواء رائعة في خيمة التبرع",
    )

    assert photo.kind == "general"
    assert photo.donor_id is None
    assert photo.caption == "أجواء رائعة في خيمة التبرع"
    assert fake_storage.exists(photo.storage_key)


@pytest.mark.br("BR-PHOTO-04")
def test_t_photo_04_c_upload_on_ended_campaign_rejected(
    photo_service: PhotoService,
    supervisor_user: User,
    live_campaign: Campaign,
    db_session: Session,
) -> None:
    """T-PHOTO-04-c: sup_a attempting photo upload after campaign ends raises 409 no_live_campaign."""
    live_campaign.status = "ended"
    db_session.flush()

    raw_img = make_test_image()
    with pytest.raises(NoLiveCampaignError, match="No active campaign is currently live"):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="general",
        )


@pytest.mark.br("BR-PHOTO-08")
def test_t_photo_08_a_storage_key_is_server_uuid(
    photo_service: PhotoService,
    supervisor_user: User,
) -> None:
    """T-PHOTO-08-a: Server-generated UUID storage key is used; client filename is discarded."""
    raw_img = make_test_image(fmt="PNG")
    photo = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=raw_img,
        kind="general",
    )

    assert photo.storage_key.startswith("photos/")
    assert photo.storage_key.endswith(".png")
    # Must not contain arbitrary client strings or path traversal sequences
    assert ".." not in photo.storage_key


@pytest.mark.br("BR-PHOTO-09")
def test_t_photo_09_a_db_failure_cleans_up_storage_file(
    photo_service: PhotoService,
    supervisor_user: User,
    fake_storage: FakeStorage,
) -> None:
    """T-PHOTO-09-a: If database record creation fails, the stored file is deleted immediately (no orphans)."""
    # Mock photo_repo.create to inject a database failure
    photo_service.photo_repo.create = MagicMock(side_effect=RuntimeError("Injected DB failure"))  # type: ignore[assignment]

    raw_img = make_test_image()
    with pytest.raises(RuntimeError, match="Injected DB failure"):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="general",
        )

    # Verify zero orphaned files left in storage
    assert len(fake_storage._files) == 0


@pytest.mark.br("BR-PHOTO-12")
def test_t_photo_12_a_caption_too_long_rejected(
    photo_service: PhotoService,
    supervisor_user: User,
) -> None:
    """T-PHOTO-12-a: Caption with >200 characters raises 422 validation_error."""
    raw_img = make_test_image()
    long_caption = "أ" * 201

    with pytest.raises(ValidationError, match="Caption must not exceed 200 characters"):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="general",
            caption=long_caption,
        )


@pytest.mark.br("BR-PHOTO-12")
def test_t_photo_12_b_caption_with_html_rejected(
    photo_service: PhotoService,
    supervisor_user: User,
) -> None:
    """T-PHOTO-12-b: Caption containing HTML characters ('<' or '>') raises 422 validation_error."""
    raw_img = make_test_image()
    with pytest.raises(ValidationError, match="HTML tags"):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="general",
            caption="<script>alert(1)</script>",
        )


@pytest.mark.br("BR-PHOTO-02")
def test_br_photo_02_general_with_donor_id_rejected(
    photo_service: PhotoService,
    supervisor_user: User,
    donor_with_photo_consent: Donor,
) -> None:
    """BR-PHOTO-02: General photo specifying a donor_id raises ValidationError."""
    raw_img = make_test_image()
    with pytest.raises(
        ValidationError, match="General atmosphere photos must not specify a donor_id"
    ):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="general",
            donor_id=donor_with_photo_consent.id,
        )


@pytest.mark.br("BR-PHOTO-02")
def test_br_photo_02_individual_without_donor_id_rejected(
    photo_service: PhotoService,
    supervisor_user: User,
) -> None:
    """BR-PHOTO-02: Individual photo missing a donor_id raises ValidationError."""
    raw_img = make_test_image()
    with pytest.raises(ValidationError, match="Individual photos require a valid donor_id"):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="individual",
            donor_id=None,
        )


@pytest.mark.br("BR-DONOR-15")
def test_individual_photo_anonymized_donor_rejected(
    photo_service: PhotoService,
    supervisor_user: User,
    donor_with_photo_consent: Donor,
    db_session: Session,
) -> None:
    """BR-DONOR-15: Individual photo for an anonymized donor raises 409 campaign_purged."""
    donor_with_photo_consent.anonymized_at = datetime.now(UTC)
    db_session.flush()

    raw_img = make_test_image()
    with pytest.raises(CampaignPurgedError, match="anonymized and cannot be modified"):
        photo_service.upload_supervisor_photo(
            user=supervisor_user,
            raw_bytes=raw_img,
            kind="individual",
            donor_id=donor_with_photo_consent.id,
        )


@pytest.mark.br("BR-PHOTO-04")
def test_admin_photo_upload_for_participating_site(
    photo_service: PhotoService,
    admin_user: User,
    live_campaign: Campaign,
    site_b: Site,
    fake_storage: FakeStorage,
) -> None:
    """BR-PHOTO-04 & T-PHOTO-04-b: Administrator uploads general photo for participating site B."""
    raw_img = make_test_image(fmt="WEBP")
    photo = photo_service.upload_admin_photo(
        user=admin_user,
        raw_bytes=raw_img,
        campaign_id=live_campaign.id,
        site_id=site_b.id,
        kind="general",
        caption="فريق موقع ب",
    )

    assert photo.campaign_id == live_campaign.id
    assert photo.site_id == site_b.id
    assert photo.kind == "general"
    assert fake_storage.exists(photo.storage_key)


@pytest.mark.br("BR-PHOTO-10")
def test_br_photo_10_supervisor_delete_success(
    photo_service: PhotoService,
    supervisor_user: User,
    live_campaign: Campaign,
    site_a: Site,
    db_session: Session,
) -> None:
    """BR-PHOTO-10 & T-PHOTO-10-a: Supervisor deletes photo at own site, enqueuing to file_deletion_queue."""
    from app.models.photo import FileDeletionQueue

    raw_img = make_test_image()
    photo = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=raw_img,
        kind="general",
    )
    photo_id = photo.id
    storage_key = photo.storage_key

    photo_service.delete_supervisor_photo(supervisor_user, photo_id)

    # Verify photo entity is deleted from database
    assert photo_service.photo_repo.get_by_id(photo_id) is None

    # Verify storage_key was enqueued into file_deletion_queue in the same transaction
    queue_item = db_session.scalar(
        select(FileDeletionQueue).where(FileDeletionQueue.storage_key == storage_key)
    )
    assert queue_item is not None
    assert queue_item.deleted_at is None


@pytest.mark.br("BR-PHOTO-10")
@pytest.mark.critical
def test_br_photo_10_supervisor_delete_foreign_site_yields_404(
    photo_service: PhotoService,
    supervisor_user: User,
    admin_user: User,
    live_campaign: Campaign,
    site_b: Site,
) -> None:
    """BR-PHOTO-10 & T-PHOTO-10-b: Supervisor deleting photo at foreign site yields 404 (never 403)."""
    raw_img = make_test_image()
    photo_b = photo_service.upload_admin_photo(
        user=admin_user,
        raw_bytes=raw_img,
        campaign_id=live_campaign.id,
        site_id=site_b.id,
        kind="general",
    )

    with pytest.raises(NotFoundError, match=f"Photo with ID '{photo_b.id}' not found"):
        photo_service.delete_supervisor_photo(supervisor_user, photo_b.id)


@pytest.mark.br("BR-PHOTO-10")
def test_br_photo_10_supervisor_delete_ended_campaign_yields_409(
    photo_service: PhotoService,
    supervisor_user: User,
    live_campaign: Campaign,
    db_session: Session,
) -> None:
    """BR-PHOTO-10 & BR-CAMP-16 ★: Supervisor deleting photo on ended campaign yields 409 no_live_campaign."""
    raw_img = make_test_image()
    photo = photo_service.upload_supervisor_photo(
        user=supervisor_user,
        raw_bytes=raw_img,
        kind="general",
    )

    # Transition campaign to ended
    live_campaign.status = "ended"
    db_session.flush()

    with pytest.raises(NoLiveCampaignError, match="No active campaign is currently live"):
        photo_service.delete_supervisor_photo(supervisor_user, photo.id)


@pytest.mark.br("BR-PHOTO-10")
def test_br_photo_10_admin_delete_any_photo(
    photo_service: PhotoService,
    admin_user: User,
    live_campaign: Campaign,
    site_b: Site,
    db_session: Session,
) -> None:
    """BR-PHOTO-10: Administrator can delete photos across all sites and campaigns."""
    from app.models.photo import FileDeletionQueue

    raw_img = make_test_image()
    photo_b = photo_service.upload_admin_photo(
        user=admin_user,
        raw_bytes=raw_img,
        campaign_id=live_campaign.id,
        site_id=site_b.id,
        kind="general",
    )
    photo_id = photo_b.id
    storage_key = photo_b.storage_key

    photo_service.delete_admin_photo(admin_user, photo_id)

    assert photo_service.photo_repo.get_by_id(photo_id) is None
    queue_item = db_session.scalar(
        select(FileDeletionQueue).where(FileDeletionQueue.storage_key == storage_key)
    )
    assert queue_item is not None
    assert queue_item.deleted_at is None
