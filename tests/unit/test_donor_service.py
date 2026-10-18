"""Unit tests for DonorService business rules and supervisor field context (P5.1).

Per:
- docs/04-api.md §11 (Supervisor Context and Donors API)
- docs/06-business-rules.md §6 (BR-DONOR-01, BR-DONOR-09, BR-DONOR-14, BR-DONOR-17)
- docs/06-business-rules.md §9.3 (BR-COUNT-07)
- docs/07-test-cases.md §5.5 (T-DONOR-09, T-DONOR-14, T-DONOR-17, T-CAMP-16)
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.core.errors import (
    CampaignPurgedError,
    DuplicateIdConflictError,
    ForbiddenError,
    NoLiveCampaignError,
    NotFoundError,
    PhoneRequiresConsentError,
    PublicPhoneRequiresDataError,
    SiteNotInCampaignError,
    UnknownFieldError,
    ValidationError,
)
from app.models.campaign import Campaign, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.user import User
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.sites import SiteRepository
from app.schemas.donor import (
    DonorCheckPhoneIn,
    DonorCreateIn,
    DonorRelocateIn,
    DonorStatusUpdateIn,
    DonorUpdateIn,
)
from app.services.audit import AuditService
from app.services.donors import DonorService


@pytest.fixture
def mock_donor_repo() -> MagicMock:
    repo = MagicMock(spec=DonorRepository)
    repo.db = MagicMock()
    repo.db.begin_nested.return_value.__enter__.return_value = MagicMock()
    repo.db.begin_nested.return_value.__exit__.return_value = None
    return repo


@pytest.fixture
def mock_campaign_repo() -> MagicMock:
    return MagicMock(spec=CampaignRepository)


@pytest.fixture
def mock_site_repo() -> MagicMock:
    return MagicMock(spec=SiteRepository)


@pytest.fixture
def mock_audit_service() -> MagicMock:
    return MagicMock(spec=AuditService)


@pytest.fixture
def donor_service(
    mock_donor_repo: MagicMock,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> DonorService:
    return DonorService(
        donor_repo=mock_donor_repo,
        campaign_repo=mock_campaign_repo,
        site_repo=mock_site_repo,
        audit_service=mock_audit_service,
    )


@pytest.fixture
def sample_live_campaign() -> Campaign:
    return Campaign(
        id=uuid.uuid4(),
        title="حملة نوفمبر الموحدة",
        slug="nov-2026",
        campaign_date=datetime(2026, 11, 20, 9, 0, 0, tzinfo=UTC),
        status="live",
        is_published=True,
        is_current=True,
    )


@pytest.fixture
def sample_site_a() -> Site:
    return Site(
        id=uuid.uuid4(),
        name="نادي الطب أ",
        address="الجزائر العاصمة",
        is_active=True,
    )


@pytest.fixture
def sample_site_b() -> Site:
    return Site(
        id=uuid.uuid4(),
        name="نادي الطب ب",
        address="وهران",
        is_active=True,
    )


@pytest.fixture
def sample_supervisor(sample_site_a: Site) -> User:
    return User(
        id=uuid.uuid4(),
        email="supervisor.a@bloodday.local",
        full_name="مشرف الموقع أ",
        role="supervisor",
        site_id=sample_site_a.id,
        is_active=True,
    )


# ==============================================================================
# 1. Supervisor Dashboard Context & Summary Tests
# ==============================================================================


@pytest.mark.unit
@pytest.mark.br("BR-COUNT-07")
def test_get_supervisor_context_success(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Supervisor context successfully returns live campaign, site, and attendance counters."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a
    mock_donor_repo.count_summary.return_value = (15, 11)

    context = donor_service.get_supervisor_context(sample_supervisor)

    assert context.campaign.id == sample_live_campaign.id
    assert context.campaign.title == sample_live_campaign.title
    assert context.campaign.status == "live"
    assert context.site.id == sample_site_a.id
    assert context.site.name == sample_site_a.name
    assert context.summary.present == 15
    assert context.summary.donated == 11
    mock_donor_repo.count_summary.assert_called_once_with(sample_live_campaign.id, sample_site_a.id)


@pytest.mark.unit
@pytest.mark.br("BR-CAMP-16")
def test_get_supervisor_context_fails_when_no_live_campaign(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    sample_supervisor: User,
) -> None:
    """Supervisor context returns 409 no_live_campaign when there is no live campaign."""
    mock_campaign_repo.get_live_campaign.return_value = None

    with pytest.raises(NoLiveCampaignError) as exc_info:
        donor_service.get_supervisor_context(sample_supervisor)

    assert exc_info.value.code == "no_live_campaign"


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-01")
def test_get_supervisor_context_fails_for_non_supervisor_or_unassigned_user(
    donor_service: DonorService,
) -> None:
    """Admin or user without site assignment receives 403 forbidden on supervisor endpoints."""
    admin_user = User(
        id=uuid.uuid4(),
        email="admin@bloodday.local",
        full_name="مدير النظام",
        role="admin",
        site_id=None,
        is_active=True,
    )

    with pytest.raises(ForbiddenError):
        donor_service.get_supervisor_context(admin_user)


@pytest.mark.unit
@pytest.mark.br("BR-COUNT-07")
def test_get_supervisor_summary_success(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Summary returns correct present and donated counts for the site in the live campaign."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a
    mock_donor_repo.count_summary.return_value = (5, 3)

    summary = donor_service.get_supervisor_summary(sample_supervisor)
    assert summary.present == 5
    assert summary.donated == 3


# ==============================================================================
# 2. Donor List (No Phone Number) Tests (BR-DONOR-14 ★)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-14")
def test_list_supervisor_donors_omits_phone_numbers(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-14-a: Donor list items MUST NOT contain the phone number field."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor_model = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        last_name="بن علي",
        phone="0550123456",  # Sensitive phone stored in database
        blood_type="A+",
        status="attended",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=False,
        consent_public_phone=False,
        created_at=datetime.now(UTC),
    )
    mock_donor_repo.list_for_supervisor.return_value = ([donor_model], 1)

    result = donor_service.list_supervisor_donors(sample_supervisor, limit=25, offset=0)

    assert result.total == 1
    assert len(result.items) == 1
    item = result.items[0]

    # Verify model fields
    assert item.first_name == "سارة"
    assert item.last_name == "بن علي"
    assert item.status == "attended"

    # Verify that phone is completely absent from serialized payload
    item_dict = item.model_dump()
    assert "phone" not in item_dict, "Violation of BR-DONOR-14: Phone leaked in donor list!"


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-17")
def test_list_supervisor_donors_forwards_search_and_status(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Query parameters q and status are forwarded to repository."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a
    mock_donor_repo.list_for_supervisor.return_value = ([], 0)

    donor_service.list_supervisor_donors(
        sample_supervisor,
        status="donated",
        q="سا",
        limit=10,
        offset=5,
    )

    mock_donor_repo.list_for_supervisor.assert_called_once_with(
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        status="donated",
        q="سا",
        limit=10,
        offset=5,
    )


@pytest.mark.unit
def test_list_supervisor_donors_rejects_invalid_status(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Status must be 'attended' or 'donated', rejecting invalid statuses per BR-DONOR-08."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    with pytest.raises(ValidationError):
        donor_service.list_supervisor_donors(sample_supervisor, status="rejected")


# ==============================================================================
# 3. Donor Detail & Site Isolation Tests (BR-DONOR-09 ★)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-14")
def test_get_supervisor_donor_includes_phone_for_assigned_site(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-14-b: Detail view returns the donor record with phone for assigned site supervisor."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor_id = uuid.uuid4()
    donor = Donor(
        id=donor_id,
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="أحمد",
        phone="0550123456",
        status="donated",
        consent_keep_phone=True,
    )
    mock_donor_repo.get_by_id.return_value = donor

    result = donor_service.get_supervisor_donor(sample_supervisor, donor_id)
    assert result.id == donor_id
    assert result.phone == "0550123456"


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-09")
def test_get_supervisor_donor_returns_404_for_foreign_site(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-09-a: Accessing a donor belonging to another site returns 404 not_found, NEVER 403."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    foreign_site_id = uuid.uuid4()
    foreign_donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=foreign_site_id,  # Different site
        first_name="سامي",
        phone="0550999999",
        status="attended",
    )
    mock_donor_repo.get_by_id.return_value = foreign_donor

    with pytest.raises(NotFoundError) as exc_info:
        donor_service.get_supervisor_donor(sample_supervisor, foreign_donor.id)

    assert exc_info.value.code == "not_found"
    assert exc_info.value.http_status == 404


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-09")
def test_get_supervisor_donor_returns_404_for_foreign_campaign(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Accessing a donor belonging to an old historical campaign returns 404 not_found."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    old_campaign_id = uuid.uuid4()
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=old_campaign_id,  # Historical campaign
        site_id=sample_site_a.id,
        first_name="كريم",
        phone="0550888888",
        status="donated",
    )
    mock_donor_repo.get_by_id.return_value = donor

    with pytest.raises(NotFoundError) as exc_info:
        donor_service.get_supervisor_donor(sample_supervisor, donor.id)

    assert exc_info.value.code == "not_found"
    assert exc_info.value.http_status == 404


# ==============================================================================
# 5. create_donor Unit Tests (BR-DONOR-01..05, BR-CONSENT-01..03, BR-GEN-07)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-01")
@pytest.mark.br("BR-DONOR-02")
@pytest.mark.br("BR-DONOR-03")
def test_create_donor_success_derives_scope(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Verify create_donor derives campaign, site, registered_by, and initial status attended."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a
    mock_donor_repo.get_by_id.return_value = None

    data = DonorCreateIn(
        first_name="سارة",
        last_name="بن علي",
        phone="0550123456",
        blood_type="A+",
        consent_keep_phone=True,
        consent_hero_name=True,
    )

    donor, created = donor_service.create_donor(sample_supervisor, data)

    assert created is True
    assert donor.first_name == "سارة"
    assert donor.last_name == "بن علي"
    assert donor.phone == "0550123456"
    assert donor.blood_type == "A+"
    # Context-derived fields (BR-DONOR-02 ★)
    assert donor.campaign_id == sample_live_campaign.id
    assert donor.site_id == sample_site_a.id
    assert donor.registered_by == sample_supervisor.id
    # Initial status (BR-DONOR-03)
    assert donor.status == "attended"
    assert donor.donated_at is None

    mock_donor_repo.create.assert_called_once_with(donor)
    mock_audit_service.log_event.assert_called_once()
    call_kwargs = mock_audit_service.log_event.call_args.kwargs
    assert call_kwargs["action"] == "donor.create"
    assert call_kwargs["actor_user_id"] == sample_supervisor.id
    assert call_kwargs["entity_id"] == donor.id


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-05")
def test_create_donor_identical_replay_returns_existing(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Verify that submitting an existing client UUID with matching payload returns existing record (200 OK semantics)."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    existing_id = uuid.uuid4()
    existing_donor = Donor(
        id=existing_id,
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        registered_by=sample_supervisor.id,
        first_name="سارة",
        last_name="بن علي",
        phone="0550123456",
        blood_type="A+",
        status="attended",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=False,
        consent_public_phone=False,
    )
    mock_donor_repo.get_by_id.return_value = existing_donor

    data = DonorCreateIn(
        id=existing_id,
        first_name="سارة",
        last_name="بن علي",
        phone="0550123456",
        blood_type="A+",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=False,
        consent_public_phone=False,
    )

    donor, created = donor_service.create_donor(sample_supervisor, data)

    assert created is False
    assert donor == existing_donor
    mock_donor_repo.create.assert_not_called()
    mock_audit_service.log_event.assert_not_called()


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-05")
def test_create_donor_different_data_raises_409(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Submitting an existing client UUID with different content raises DuplicateIdConflictError (409)."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    existing_id = uuid.uuid4()
    existing_donor = Donor(
        id=existing_id,
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        registered_by=sample_supervisor.id,
        first_name="سارة",
        last_name="بن علي",
        phone="0550123456",
        blood_type="A+",
        status="attended",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=False,
        consent_public_phone=False,
    )
    mock_donor_repo.get_by_id.return_value = existing_donor

    # Different first_name
    data = DonorCreateIn(
        id=existing_id,
        first_name="فاطمة",
        last_name="بن علي",
        phone="0550123456",
        blood_type="A+",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=False,
        consent_public_phone=False,
    )

    with pytest.raises(DuplicateIdConflictError) as exc_info:
        donor_service.create_donor(sample_supervisor, data)

    assert exc_info.value.code == "duplicate_id_conflict"
    assert exc_info.value.http_status == 409


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-05")
def test_create_donor_different_supervisor_raises_409(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Submitting an existing client UUID belonging to another supervisor raises DuplicateIdConflictError (409)."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    existing_id = uuid.uuid4()
    existing_donor = Donor(
        id=existing_id,
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        registered_by=uuid.uuid4(),  # Different supervisor
        first_name="سارة",
        last_name="بن علي",
        phone="0550123456",
        blood_type="A+",
        status="attended",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=False,
        consent_public_phone=False,
    )
    mock_donor_repo.get_by_id.return_value = existing_donor

    data = DonorCreateIn(
        id=existing_id,
        first_name="سارة",
        last_name="بن علي",
        phone="0550123456",
        blood_type="A+",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=False,
        consent_public_phone=False,
    )

    with pytest.raises(DuplicateIdConflictError) as exc_info:
        donor_service.create_donor(sample_supervisor, data)

    assert exc_info.value.code == "duplicate_id_conflict"
    assert exc_info.value.http_status == 409


# ==============================================================================
# P5.3 Unit Tests: update_donor, Final State, Consent Rules, and Cleanup
# ==============================================================================


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-16")
def test_update_donor_success_demographics(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Updating allowed demographic fields updates donor and logs audit event."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        last_name="قديم",
        phone=None,
        blood_type=None,
        status="attended",
        consent_keep_phone=False,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorUpdateIn(first_name="مريم", last_name="جديد")
    updated = donor_service.update_donor(sample_supervisor, donor.id, payload)

    assert updated.first_name == "مريم"
    assert updated.last_name == "جديد"
    mock_donor_repo.update.assert_called_once_with(donor)
    mock_audit_service.log_event.assert_called_once()
    event_call = mock_audit_service.log_event.call_args[1]
    assert event_call["action"] == "donor.update"
    assert set(event_call["details"]["updated_fields"]) == {"first_name", "last_name"}


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-10")
def test_update_donor_reserved_field_raises_unknown_field(
    donor_service: DonorService,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
) -> None:
    """T-DONOR-10-b: Attempting to modify reserved field site_id at service layer raises UnknownFieldError."""
    donor_id = uuid.uuid4()
    with pytest.raises(UnknownFieldError) as exc_info:
        donor_service.update_donor(
            sample_supervisor,
            donor_id,
            {"site_id": str(uuid.uuid4())},
        )
    assert exc_info.value.code == "unknown_field"
    assert exc_info.value.http_status == 422


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-09")
def test_update_donor_foreign_site_raises_404(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-09-a: Supervisor cannot update donor belonging to a different site (returns 404)."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=uuid.uuid4(),  # Different site
        first_name="سارة",
    )
    mock_donor_repo.get_by_id.return_value = donor

    with pytest.raises(NotFoundError) as exc_info:
        donor_service.update_donor(sample_supervisor, donor.id, DonorUpdateIn(first_name="مريم"))
    assert exc_info.value.code == "not_found"
    assert exc_info.value.http_status == 404


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-16")
def test_update_donor_no_live_campaign_raises_409(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    sample_supervisor: User,
) -> None:
    """T-DONOR-16-a: Supervisor cannot update donor when there is no live campaign (returns 409)."""
    mock_campaign_repo.get_live_campaign.return_value = None

    with pytest.raises(NoLiveCampaignError) as exc_info:
        donor_service.update_donor(
            sample_supervisor, uuid.uuid4(), DonorUpdateIn(first_name="مريم")
        )
    assert exc_info.value.code == "no_live_campaign"
    assert exc_info.value.http_status == 409


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-16")
def test_update_donor_admin_ended_campaign_succeeds(
    donor_service: DonorService,
    mock_donor_repo: MagicMock,
) -> None:
    """T-DONOR-16-b: Admin can update donor even after campaign ended."""
    admin_user = User(
        id=uuid.uuid4(),
        email="admin@bloodday.local",
        role="admin",
        site_id=None,
        is_active=True,
    )
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        site_id=uuid.uuid4(),
        first_name="سارة",
        last_name="قديم",
        status="donated",
        consent_keep_phone=False,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=False,
    )
    mock_donor_repo.get_by_id.return_value = donor

    updated = donor_service.update_donor(
        admin_user, donor.id, DonorUpdateIn(first_name="سارة المحدثة")
    )
    assert updated.first_name == "سارة المحدثة"
    mock_donor_repo.update.assert_called_once_with(donor)


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-15")
def test_update_donor_anonymized_raises_campaign_purged(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-15-a: Modifying an anonymized donor record raises CampaignPurgedError (409)."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="محذوف",
        anonymized_at=datetime(2027, 11, 20, 0, 0, 0, tzinfo=UTC),
    )
    mock_donor_repo.get_by_id.return_value = donor

    with pytest.raises(CampaignPurgedError) as exc_info:
        donor_service.update_donor(sample_supervisor, donor.id, DonorUpdateIn(first_name="تجربة"))
    assert exc_info.value.code == "campaign_purged"
    assert exc_info.value.http_status == 409


@pytest.mark.unit
@pytest.mark.br("BR-CONSENT-04")
def test_update_donor_withdrawing_keep_without_null_phone_raises_422(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-CONSENT-04-a & T-CONSENT-06-b: Withdrawing consent_keep_phone without explicit phone: null raises 422."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        phone="0550123456",
        consent_keep_phone=True,
        consent_public_phone=False,
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorUpdateIn(consent_keep_phone=False)
    with pytest.raises(PhoneRequiresConsentError) as exc_info:
        donor_service.update_donor(sample_supervisor, donor.id, payload)
    assert exc_info.value.code == "phone_requires_consent"
    assert exc_info.value.http_status == 422


@pytest.mark.unit
@pytest.mark.br("BR-CONSENT-06")
def test_update_donor_withdrawing_keep_with_explicit_null_phone_succeeds(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-CONSENT-06-a: Withdrawing consent_keep_phone with explicit phone: null succeeds."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        phone="0550123456",
        consent_keep_phone=True,
        consent_public_phone=False,
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorUpdateIn(consent_keep_phone=False, phone=None)
    updated = donor_service.update_donor(sample_supervisor, donor.id, payload)

    assert updated.phone is None
    assert updated.consent_keep_phone is False
    mock_donor_repo.update.assert_called_once_with(donor)


@pytest.mark.unit
@pytest.mark.br("BR-CONSENT-06")
def test_update_donor_withdrawing_keep_retains_phone_when_public_true(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-CONSENT-06-c: Withdrawing consent_keep_phone retains phone when consent_public_phone remains True."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        phone="0550123456",
        blood_type="O+",
        consent_keep_phone=True,
        consent_public_phone=True,
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorUpdateIn(consent_keep_phone=False)
    updated = donor_service.update_donor(sample_supervisor, donor.id, payload)

    assert updated.phone == "0550123456"
    assert updated.consent_keep_phone is False
    assert updated.consent_public_phone is True
    mock_donor_repo.update.assert_called_once_with(donor)


@pytest.mark.unit
@pytest.mark.br("BR-CONSENT-04")
def test_update_donor_clearing_blood_type_when_public_true_raises_422(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-CONSENT-04-b: Clearing blood_type when consent_public_phone is True raises PublicPhoneRequiresDataError."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        phone="0550123456",
        blood_type="O+",
        consent_keep_phone=True,
        consent_public_phone=True,
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorUpdateIn(blood_type=None)
    with pytest.raises(PublicPhoneRequiresDataError) as exc_info:
        donor_service.update_donor(sample_supervisor, donor.id, payload)
    assert exc_info.value.code == "public_phone_requires_data"
    assert exc_info.value.http_status == 422


@pytest.mark.unit
@pytest.mark.br("BR-CONSENT-07")
def test_update_donor_withdrawing_photo_consent_cleans_photos(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-CONSENT-07-a: Withdrawing consent_photo deletes photos and enqueues their storage keys."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        consent_photo=True,
    )
    mock_donor_repo.get_by_id.return_value = donor

    photo1 = Photo(
        id=uuid.uuid4(),
        donor_id=donor.id,
        storage_key="photos/photo1.webp",
        kind="individual",
    )
    photo2 = Photo(
        id=uuid.uuid4(),
        donor_id=donor.id,
        storage_key="photos/photo2.webp",
        kind="individual",
    )
    mock_donor_repo.get_photos_by_donor.return_value = [photo1, photo2]

    payload = DonorUpdateIn(consent_photo=False)
    updated = donor_service.update_donor(sample_supervisor, donor.id, payload)

    assert updated.consent_photo is False
    assert mock_donor_repo.delete_photo_and_enqueue.call_count == 2
    mock_donor_repo.delete_photo_and_enqueue.assert_any_call(photo1)
    mock_donor_repo.delete_photo_and_enqueue.assert_any_call(photo2)


@pytest.mark.unit
@pytest.mark.br("BR-CONSENT-07")
def test_update_donor_withdrawing_photo_consent_propagates_error_on_queue_failure(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-CONSENT-07-b: Injected failure during queue insertion aborts transaction."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        consent_photo=True,
    )
    mock_donor_repo.get_by_id.return_value = donor

    photo = Photo(
        id=uuid.uuid4(),
        donor_id=donor.id,
        storage_key="photos/fail.webp",
        kind="individual",
    )
    mock_donor_repo.get_photos_by_donor.return_value = [photo]
    mock_donor_repo.delete_photo_and_enqueue.side_effect = RuntimeError("DB Queue Error")

    payload = DonorUpdateIn(consent_photo=False)
    with pytest.raises(RuntimeError, match="DB Queue Error"):
        donor_service.update_donor(sample_supervisor, donor.id, payload)


# ==============================================================================
# P5.4 Unit Tests: Status Change, Deletion, and Check-Phone
# ==============================================================================


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-06")
@pytest.mark.br("BR-DONOR-07")
def test_change_donor_status_attended_to_donated(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-06-a / T-DONOR-07-a: Status change attended -> donated sets donated_at and logs audit."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="كريم",
        status="attended",
        donated_at=None,
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorStatusUpdateIn(status="donated")
    updated = donor_service.change_donor_status(sample_supervisor, donor.id, payload)

    assert updated.status == "donated"
    assert updated.donated_at is not None
    mock_donor_repo.update.assert_called_once_with(donor)
    mock_audit_service.log_event.assert_called_once_with(
        action="donor.status_change",
        entity_type="donor",
        entity_id=donor.id,
        actor_user_id=sample_supervisor.id,
        details={"old_status": "attended", "new_status": "donated"},
    )


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-06")
def test_change_donor_status_donated_idempotency_preserves_timestamp(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-06-b: Repeated PUT status=donated preserves original donated_at timestamp."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    original_time = datetime(2026, 11, 15, 10, 30, tzinfo=UTC)
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="كريم",
        status="donated",
        donated_at=original_time,
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorStatusUpdateIn(status="donated")
    updated = donor_service.change_donor_status(sample_supervisor, donor.id, payload)

    assert updated.status == "donated"
    assert updated.donated_at == original_time
    mock_donor_repo.update.assert_called_once_with(donor)
    mock_audit_service.log_event.assert_not_called()


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-06")
@pytest.mark.br("BR-DONOR-07")
def test_change_donor_status_donated_to_attended(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-06-a / T-DONOR-07-b: Reverting status donated -> attended clears donated_at to None."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    original_time = datetime(2026, 11, 15, 10, 30, tzinfo=UTC)
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="كريم",
        status="donated",
        donated_at=original_time,
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorStatusUpdateIn(status="attended")
    updated = donor_service.change_donor_status(sample_supervisor, donor.id, payload)

    assert updated.status == "attended"
    assert updated.donated_at is None
    mock_donor_repo.update.assert_called_once_with(donor)
    mock_audit_service.log_event.assert_called_once_with(
        action="donor.status_change",
        entity_type="donor",
        entity_id=donor.id,
        actor_user_id=sample_supervisor.id,
        details={"old_status": "donated", "new_status": "attended"},
    )


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-09")
def test_change_donor_status_foreign_site_returns_404(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-09-a: Supervisor attempting status change on other site donor gets 404, not 403."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    foreign_site_id = uuid.uuid4()
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=foreign_site_id,
        first_name="أجنبي",
        status="attended",
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorStatusUpdateIn(status="donated")
    with pytest.raises(NotFoundError, match="not found"):
        donor_service.change_donor_status(sample_supervisor, donor.id, payload)


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-15")
def test_change_donor_status_anonymized_record_raises_409(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-15-a: Attempting status change on anonymized donor raises CampaignPurgedError (409)."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="مخفي",
        status="attended",
        anonymized_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorStatusUpdateIn(status="donated")
    with pytest.raises(CampaignPurgedError):
        donor_service.change_donor_status(sample_supervisor, donor.id, payload)


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-11")
@pytest.mark.br("BR-PHOTO-10")
def test_delete_donor_cascades_photos_and_logs_audit(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-11-a / T-DONOR-11-b: Deleting donor cascades individual photos into deletion queue."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سالم",
    )
    mock_donor_repo.get_by_id.return_value = donor

    photo1 = Photo(
        id=uuid.uuid4(),
        donor_id=donor.id,
        storage_key="photos/photo1.webp",
        kind="individual",
    )
    photo2 = Photo(
        id=uuid.uuid4(),
        donor_id=donor.id,
        storage_key="photos/photo2.webp",
        kind="individual",
    )
    mock_donor_repo.get_photos_by_donor.return_value = [photo1, photo2]

    donor_service.delete_donor(sample_supervisor, donor.id)

    assert mock_donor_repo.delete_photo_and_enqueue.call_count == 2
    mock_donor_repo.delete_photo_and_enqueue.assert_any_call(photo1)
    mock_donor_repo.delete_photo_and_enqueue.assert_any_call(photo2)
    mock_donor_repo.delete_donor.assert_called_once_with(donor)
    mock_audit_service.log_event.assert_called_once_with(
        action="donor.delete",
        entity_type="donor",
        entity_id=donor.id,
        actor_user_id=sample_supervisor.id,
        details={
            "site_id": str(sample_site_a.id),
            "campaign_id": str(sample_live_campaign.id),
        },
    )


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-09")
def test_delete_donor_foreign_site_returns_404(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-09-a: Supervisor deleting other site donor gets 404, not 403."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    foreign_site_id = uuid.uuid4()
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=foreign_site_id,
        first_name="أجنبي",
    )
    mock_donor_repo.get_by_id.return_value = donor

    with pytest.raises(NotFoundError, match="not found"):
        donor_service.delete_donor(sample_supervisor, donor.id)


@pytest.mark.unit
@pytest.mark.br("BR-CAMP-16")
def test_delete_donor_non_live_campaign_returns_409(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    sample_supervisor: User,
) -> None:
    """T-DONOR-11-c: Deleting donor after campaign has ended returns 409 no_live_campaign."""
    mock_campaign_repo.get_live_campaign.return_value = None

    with pytest.raises(NoLiveCampaignError):
        donor_service.delete_donor(sample_supervisor, uuid.uuid4())


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-13")
def test_check_phone_advisory_lookup(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_supervisor: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-13-a / T-DONOR-13-b: Soft duplicate advisory queries phone in site/campaign scope."""
    mock_campaign_repo.get_live_campaign.return_value = sample_live_campaign
    mock_site_repo.get_by_id.return_value = sample_site_a

    # Case 1: Phone exists
    mock_donor_repo.check_phone_exists.return_value = True
    result_exists = donor_service.check_phone(
        sample_supervisor,
        DonorCheckPhoneIn(phone="0550123456"),
    )
    assert result_exists.exists is True
    mock_donor_repo.check_phone_exists.assert_called_with(
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        phone="0550123456",
    )

    # Case 2: Phone does not exist
    mock_donor_repo.check_phone_exists.return_value = False
    result_not_exists = donor_service.check_phone(
        sample_supervisor,
        DonorCheckPhoneIn(phone="0550999999"),
    )
    assert result_not_exists.exists is False


# ==============================================================================
# 9. Single-Donor Anonymization and Phone Deletion on Request (P8.4)
# ==============================================================================


@pytest.fixture
def sample_admin() -> User:
    return User(
        id=uuid.uuid4(),
        email="admin@bloodday.local",
        full_name="مسؤول النظام",
        role="admin",
        site_id=None,
        is_active=True,
    )


@pytest.mark.unit
@pytest.mark.br("BR-PRIV-09")
def test_anonymize_donor_success(
    donor_service: DonorService,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_admin: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-PRIV-09-a: Admin anonymizes donor, cascades individual photos, and emits audit event."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="أحمد",
        last_name="بطل",
        phone="0550123456",
        blood_type="O+",
        status="donated",
        consent_keep_phone=True,
        consent_hero_name=True,
        consent_photo=True,
        consent_public_phone=True,
        anonymized_at=None,
    )
    photo = Photo(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        donor_id=donor.id,
        storage_key="photos/campaign/site/photo1.jpg",
    )
    mock_donor_repo.get_by_id.return_value = donor
    mock_donor_repo.get_photos_by_donor.return_value = [photo]

    updated_donor = donor_service.anonymize_donor(sample_admin, donor.id)

    assert updated_donor.first_name == "محذوف"
    assert updated_donor.last_name is None
    assert updated_donor.phone is None
    assert updated_donor.blood_type is None
    assert updated_donor.consent_keep_phone is False
    assert updated_donor.consent_hero_name is False
    assert updated_donor.consent_photo is False
    assert updated_donor.consent_public_phone is False
    assert updated_donor.anonymized_at is not None

    mock_donor_repo.delete_photo_and_enqueue.assert_called_once_with(photo)
    mock_donor_repo.update.assert_called_once_with(donor)
    mock_audit_service.log_event.assert_called_once_with(
        action="donor.anonymize",
        entity_type="donor",
        entity_id=donor.id,
        actor_user_id=sample_admin.id,
        details={
            "site_id": str(sample_site_a.id),
            "campaign_id": str(sample_live_campaign.id),
        },
    )


@pytest.mark.unit
@pytest.mark.br("BR-PRIV-09")
def test_anonymize_donor_forbidden_for_supervisor(
    donor_service: DonorService,
    sample_supervisor: User,
) -> None:
    """Non-admin callers cannot anonymize donor records."""
    with pytest.raises(ForbiddenError, match="Only administrators"):
        donor_service.anonymize_donor(sample_supervisor, uuid.uuid4())


@pytest.mark.unit
@pytest.mark.br("BR-PRIV-09")
def test_anonymize_donor_not_found(
    donor_service: DonorService,
    mock_donor_repo: MagicMock,
    sample_admin: User,
) -> None:
    """Non-existent donor returns NotFoundError."""
    mock_donor_repo.get_by_id.return_value = None
    with pytest.raises(NotFoundError, match="not found"):
        donor_service.anonymize_donor(sample_admin, uuid.uuid4())


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-15")
def test_anonymize_donor_already_anonymized_rejected(
    donor_service: DonorService,
    mock_donor_repo: MagicMock,
    sample_admin: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Already anonymized donor cannot be re-anonymized (409 campaign_purged)."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="محذوف",
        anonymized_at=datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC),
    )
    mock_donor_repo.get_by_id.return_value = donor

    with pytest.raises(CampaignPurgedError, match="already been anonymized"):
        donor_service.anonymize_donor(sample_admin, donor.id)


@pytest.mark.unit
@pytest.mark.br("BR-PRIV-10")
def test_delete_phone_on_request_success(
    donor_service: DonorService,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_admin: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-PRIV-10-a: Admin deletes donor phone on request, audits data.deleted_on_request."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="سارة",
        phone="0550123456",
        consent_keep_phone=True,
        consent_public_phone=True,
        anonymized_at=None,
    )
    mock_donor_repo.get_by_id.return_value = donor

    updated = donor_service.delete_phone_on_request(sample_admin, donor.id)

    assert updated.phone is None
    assert updated.consent_keep_phone is False
    assert updated.consent_public_phone is False
    mock_donor_repo.update.assert_called_once_with(donor)
    mock_audit_service.log_event.assert_called_once_with(
        action="data.deleted_on_request",
        entity_type="donor",
        entity_id=donor.id,
        actor_user_id=sample_admin.id,
        details={
            "type": "phone",
            "donor_id": str(donor.id),
            "site_id": str(sample_site_a.id),
            "campaign_id": str(sample_live_campaign.id),
        },
    )


# ==============================================================================
# 11. relocate_donor Unit Tests (BR-DONOR-12 ★, BR-PHOTO-11, BR-DONOR-15)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-12")
@pytest.mark.br("BR-PHOTO-11")
def test_relocate_donor_success_moves_donor_and_photos(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_admin: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
    sample_site_b: Site,
) -> None:
    """T-DONOR-12-a: Admin relocates donor and all individual photos to participating site."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="أحمد",
        last_name="الفلاني",
        phone="0550111222",
        status="donated",
        anonymized_at=None,
    )
    mock_donor_repo.get_by_id.return_value = donor

    # Target site is participating in the campaign
    campaign_site_b = MagicMock()
    mock_campaign_repo.get_campaign_site.return_value = campaign_site_b

    # Mock relocated photos
    photo = Photo(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_b.id,
        donor_id=donor.id,
        storage_key="photos/test.jpg",
    )
    mock_donor_repo.relocate_donor_photos.return_value = [photo]

    payload = DonorRelocateIn(site_id=sample_site_b.id)
    result = donor_service.relocate_donor(sample_admin, donor.id, payload)

    assert result.site_id == sample_site_b.id
    mock_donor_repo.update.assert_called_once_with(donor)
    mock_donor_repo.relocate_donor_photos.assert_called_once_with(donor.id, sample_site_b.id)
    mock_audit_service.log_event.assert_called_once_with(
        action="donor.relocate",
        entity_type="donor",
        entity_id=donor.id,
        actor_user_id=sample_admin.id,
        details={
            "old_site_id": str(sample_site_a.id),
            "new_site_id": str(sample_site_b.id),
            "campaign_id": str(sample_live_campaign.id),
            "photos_relocated": 1,
        },
    )


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-12")
def test_relocate_donor_same_site_is_idempotent(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_admin: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """Relocating to the same site returns donor without modifications or audit log."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="أحمد",
        anonymized_at=None,
    )
    mock_donor_repo.get_by_id.return_value = donor
    mock_campaign_repo.get_campaign_site.return_value = MagicMock()

    payload = DonorRelocateIn(site_id=sample_site_a.id)
    result = donor_service.relocate_donor(sample_admin, donor.id, payload)

    assert result.site_id == sample_site_a.id
    mock_donor_repo.update.assert_not_called()
    mock_donor_repo.relocate_donor_photos.assert_not_called()
    mock_audit_service.log_event.assert_not_called()


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-12")
def test_relocate_donor_site_not_in_campaign_raises_409(
    donor_service: DonorService,
    mock_campaign_repo: MagicMock,
    mock_donor_repo: MagicMock,
    sample_admin: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
) -> None:
    """T-DONOR-12-b: Target site not participating in campaign raises 409 site_not_in_campaign."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="أحمد",
        anonymized_at=None,
    )
    mock_donor_repo.get_by_id.return_value = donor
    mock_campaign_repo.get_campaign_site.return_value = None  # Not participating

    non_participating_site_id = uuid.uuid4()
    payload = DonorRelocateIn(site_id=non_participating_site_id)

    with pytest.raises(SiteNotInCampaignError) as exc_info:
        donor_service.relocate_donor(sample_admin, donor.id, payload)

    assert exc_info.value.code == "site_not_in_campaign"
    assert exc_info.value.http_status == 409


@pytest.mark.unit
@pytest.mark.br("BR-DONOR-15")
def test_relocate_donor_anonymized_donor_raises_409(
    donor_service: DonorService,
    mock_donor_repo: MagicMock,
    sample_admin: User,
    sample_live_campaign: Campaign,
    sample_site_a: Site,
    sample_site_b: Site,
) -> None:
    """T-DONOR-15-a: Anonymized donor cannot be relocated (409 campaign_purged)."""
    donor = Donor(
        id=uuid.uuid4(),
        campaign_id=sample_live_campaign.id,
        site_id=sample_site_a.id,
        first_name="محذوف",
        anonymized_at=datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC),
    )
    mock_donor_repo.get_by_id.return_value = donor

    payload = DonorRelocateIn(site_id=sample_site_b.id)

    with pytest.raises(CampaignPurgedError) as exc_info:
        donor_service.relocate_donor(sample_admin, donor.id, payload)

    assert exc_info.value.code == "campaign_purged"
    assert exc_info.value.http_status == 409


@pytest.mark.unit
def test_relocate_donor_non_admin_raises_forbidden(
    donor_service: DonorService,
    sample_supervisor: User,
    sample_site_b: Site,
) -> None:
    """Supervisor cannot relocate donors (403 forbidden)."""
    donor_id = uuid.uuid4()
    payload = DonorRelocateIn(site_id=sample_site_b.id)

    with pytest.raises(ForbiddenError):
        donor_service.relocate_donor(sample_supervisor, donor_id, payload)


@pytest.mark.unit
def test_relocate_donor_not_found_raises_404(
    donor_service: DonorService,
    mock_donor_repo: MagicMock,
    sample_admin: User,
    sample_site_b: Site,
) -> None:
    """Relocating nonexistent donor raises 404 not_found."""
    mock_donor_repo.get_by_id.return_value = None
    donor_id = uuid.uuid4()
    payload = DonorRelocateIn(site_id=sample_site_b.id)

    with pytest.raises(NotFoundError) as exc_info:
        donor_service.relocate_donor(sample_admin, donor_id, payload)

    assert exc_info.value.code == "not_found"
    assert exc_info.value.http_status == 404
