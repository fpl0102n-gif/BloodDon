"""Unit tests for CampaignService enforcing business rules (P4.2).

Per:
- docs/06-business-rules.md §3 (BR-CAMP-01, 02, 03, 12, 13, 14, 15)
- docs/07-test-cases.md §5.2 (T-CAMP-01-a, T-CAMP-02-b, T-CAMP-12-a, T-CAMP-12-b, T-CAMP-13, T-CAMP-14, T-CAMP-15-a)
"""

import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest

from app.core.errors import (
    InUseError,
    InvalidStateTransitionError,
    NotFoundError,
    SlugTakenError,
)
from app.models.campaign import Campaign
from app.repositories.campaigns import CampaignRepository
from app.schemas.campaign import CampaignCreate, CampaignUpdate
from app.services.campaigns import CampaignService


@pytest.fixture
def mock_campaign_repo() -> MagicMock:
    return MagicMock(spec=CampaignRepository)


@pytest.fixture
def campaign_service(mock_campaign_repo: MagicMock) -> CampaignService:
    return CampaignService(repository=mock_campaign_repo)


@pytest.mark.br("BR-CAMP-01")
def test_t_camp_01_a_create_campaign_initial_state(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-01-a: Created campaign defaults to upcoming, unpublished, not current (BR-CAMP-01)."""
    mock_campaign_repo.get_by_slug.return_value = None
    mock_campaign_repo.create.side_effect = lambda c: c

    payload = CampaignCreate(
        title="اليوم الوطني للتبرع بالدم 2026",
        slug="national-blood-drive-2026",
        campaign_date=date(2026, 11, 20),
        summary="ملخص الحملة",
        content="# المحتوى الرئيسي",
    )

    created = campaign_service.create_campaign(payload)

    assert created.status == "upcoming"
    assert created.is_published is False
    assert created.is_current is False
    assert created.title == "اليوم الوطني للتبرع بالدم 2026"
    assert created.slug == "national-blood-drive-2026"
    mock_campaign_repo.create.assert_called_once()


@pytest.mark.br("BR-CAMP-02")
def test_t_camp_02_b_duplicate_slug_raises_slug_taken(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-02-b: Existing slug rejects creation with 409 slug_taken (BR-CAMP-02)."""
    existing_campaign = Campaign(
        id=uuid.uuid4(),
        title="حملة موجودة",
        slug="first-day",
        campaign_date=date(2026, 11, 20),
        status="upcoming",
    )
    mock_campaign_repo.get_by_slug.return_value = existing_campaign

    payload = CampaignCreate(
        title="حملة ثانية",
        slug="first-day",
        campaign_date=date(2026, 11, 21),
    )

    with pytest.raises(SlugTakenError):
        campaign_service.create_campaign(payload)


@pytest.mark.br("BR-CAMP-03")
def test_t_camp_03_a_unpublished_campaign_hidden_from_public(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-03-a ★: Unpublished campaign raises NotFoundError in public view (BR-CAMP-03)."""
    unpublished = Campaign(
        id=uuid.uuid4(),
        title="حملة غير منشورة",
        slug="draft-campaign",
        campaign_date=date(2026, 11, 20),
        status="upcoming",
        is_published=False,
    )
    mock_campaign_repo.get_by_slug.return_value = unpublished

    with pytest.raises(NotFoundError):
        campaign_service.get_public_campaign("draft-campaign")


@pytest.mark.br("BR-CAMP-12")
@pytest.mark.critical
def test_t_camp_12_a_unpublish_live_campaign_forbidden(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-12-a ★: Unpublishing a live campaign raises InvalidStateTransitionError (BR-CAMP-12)."""
    live_campaign = Campaign(
        id=uuid.uuid4(),
        title="حملة جارية",
        slug="live-campaign",
        campaign_date=date(2026, 11, 20),
        status="live",
        is_published=True,
    )
    mock_campaign_repo.get_by_id.return_value = live_campaign

    with pytest.raises(InvalidStateTransitionError, match="Cannot unpublish"):
        campaign_service.update_campaign(
            live_campaign.id,
            CampaignUpdate(is_published=False),
        )


@pytest.mark.br("BR-CAMP-12")
def test_t_camp_12_b_unpublish_current_campaign_resets_is_current(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-12-b: Unpublishing an upcoming current campaign clears is_current (BR-CAMP-12)."""
    current_campaign = Campaign(
        id=uuid.uuid4(),
        title="حملة حالية قادمة",
        slug="current-campaign",
        campaign_date=date(2026, 11, 20),
        status="upcoming",
        is_published=True,
        is_current=True,
    )
    mock_campaign_repo.get_by_id.return_value = current_campaign
    mock_campaign_repo.update.side_effect = lambda c: c

    updated = campaign_service.update_campaign(
        current_campaign.id,
        CampaignUpdate(is_published=False),
    )

    assert updated.is_published is False
    assert updated.is_current is False


@pytest.mark.br("BR-CAMP-13")
def test_t_camp_13_a_delete_used_campaign_raises_in_use(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-13-a: Deleting campaign with existing data raises InUseError (BR-CAMP-13)."""
    campaign = Campaign(
        id=uuid.uuid4(),
        title="حملة ذات بيانات",
        slug="busy-campaign",
        campaign_date=date(2026, 11, 20),
        status="upcoming",
    )
    mock_campaign_repo.get_by_id.return_value = campaign
    mock_campaign_repo.is_campaign_in_use.return_value = True

    with pytest.raises(InUseError, match="Cannot delete campaign"):
        campaign_service.delete_campaign(campaign.id)


@pytest.mark.br("BR-CAMP-13")
def test_t_camp_13_b_delete_unused_campaign_succeeds(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-13-b: Deleting empty campaign succeeds (BR-CAMP-13)."""
    campaign = Campaign(
        id=uuid.uuid4(),
        title="حملة فارغة",
        slug="empty-campaign",
        campaign_date=date(2026, 11, 20),
        status="upcoming",
    )
    mock_campaign_repo.get_by_id.return_value = campaign
    mock_campaign_repo.is_campaign_in_use.return_value = False

    campaign_service.delete_campaign(campaign.id)

    mock_campaign_repo.delete.assert_called_once_with(campaign)


@pytest.mark.br("BR-CAMP-14")
def test_t_camp_14_a_modify_campaign_date_on_live_or_ended_rejected(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-14-a: Modifying campaign_date on live or ended campaign is rejected (BR-CAMP-14)."""
    live_campaign = Campaign(
        id=uuid.uuid4(),
        title="حملة جارية",
        slug="live-campaign",
        campaign_date=date(2026, 11, 20),
        status="live",
        is_published=True,
    )
    mock_campaign_repo.get_by_id.return_value = live_campaign

    with pytest.raises(InvalidStateTransitionError, match="Cannot modify campaign_date"):
        campaign_service.update_campaign(
            live_campaign.id,
            CampaignUpdate(campaign_date=date(2026, 12, 1)),
        )

    ended_campaign = Campaign(
        id=uuid.uuid4(),
        title="حملة منتهية",
        slug="ended-campaign",
        campaign_date=date(2026, 11, 20),
        status="ended",
        is_published=True,
    )
    mock_campaign_repo.get_by_id.return_value = ended_campaign

    with pytest.raises(InvalidStateTransitionError, match="Cannot modify campaign_date"):
        campaign_service.update_campaign(
            ended_campaign.id,
            CampaignUpdate(campaign_date=date(2026, 12, 1)),
        )


@pytest.mark.br("BR-CAMP-14")
def test_t_camp_14_b_modify_campaign_date_on_upcoming_succeeds(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-14-b: Modifying campaign_date on upcoming campaign succeeds (BR-CAMP-14)."""
    upcoming_campaign = Campaign(
        id=uuid.uuid4(),
        title="حملة قادمة",
        slug="upcoming-campaign",
        campaign_date=date(2026, 11, 20),
        status="upcoming",
        is_published=True,
    )
    mock_campaign_repo.get_by_id.return_value = upcoming_campaign
    mock_campaign_repo.update.side_effect = lambda c: c

    new_date = date(2026, 12, 25)
    updated = campaign_service.update_campaign(
        upcoming_campaign.id,
        CampaignUpdate(campaign_date=new_date),
    )

    assert updated.campaign_date == new_date


@pytest.mark.br("BR-CAMP-15")
def test_t_camp_15_a_content_size_exceeding_100kb_rejected(
    campaign_service: CampaignService,
) -> None:
    """T-CAMP-15-a: Content size exceeding 100 KB is rejected with ValidationError (BR-CAMP-15)."""
    huge_content = "A" * (100 * 1024 + 1)

    # In schema validation
    with pytest.raises(ValueError, match="exceeds maximum limit"):
        CampaignCreate(
            title="حملة ضخمة",
            slug="huge-campaign",
            campaign_date=date(2026, 11, 20),
            content=huge_content,
        )

    with pytest.raises(ValueError, match="exceeds maximum limit"):
        CampaignUpdate(content=huge_content)
