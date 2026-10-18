"""Unit tests for Campaign Lifecycle domain service business rules (P4.4).

Per:
- docs/06-business-rules.md §3 (BR-CAMP-04, 05, 06, 07, 08, 11)
- docs/07-test-cases.md §5.2 (T-CAMP-05, T-CAMP-06, T-CAMP-07, T-CAMP-08, T-CAMP-11)
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.errors import (
    CampaignClosedError,
    CampaignPurgedError,
    InvalidStateTransitionError,
)
from app.models.campaign import Campaign
from app.repositories.campaigns import CampaignRepository
from app.services.audit import AuditService
from app.services.campaigns import CampaignService


@pytest.fixture
def mock_campaign_repo() -> MagicMock:
    return MagicMock(spec=CampaignRepository)


@pytest.fixture
def mock_audit_service() -> MagicMock:
    return MagicMock(spec=AuditService)


@pytest.fixture
def campaign_service(
    mock_campaign_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> CampaignService:
    return CampaignService(
        repository=mock_campaign_repo,
        audit_service=mock_audit_service,
    )


# ==============================================================================
# 1. set_current_campaign (BR-CAMP-04, 05)
# ==============================================================================


@pytest.mark.br("BR-CAMP-05")
def test_t_camp_05_a_set_current_requires_published(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-05-a: Attempt to set an unpublished campaign as current raises CampaignClosedError (409)."""
    campaign_id = uuid.uuid4()
    draft_campaign = Campaign(
        id=campaign_id,
        slug="draft-campaign",
        is_published=False,
        is_current=False,
    )
    mock_campaign_repo.get_by_id.return_value = draft_campaign

    with pytest.raises(CampaignClosedError, match="must be published"):
        campaign_service.set_current_campaign(campaign_id)

    mock_campaign_repo.set_current.assert_not_called()


@pytest.mark.br("BR-CAMP-05")
def test_t_camp_05_b_set_current_success(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-CAMP-05-b: Setting a published campaign as current updates state and logs audit event."""
    campaign_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    published_campaign = Campaign(
        id=campaign_id,
        slug="published-drive",
        is_published=True,
        is_current=False,
    )
    mock_campaign_repo.get_by_id.return_value = published_campaign
    mock_campaign_repo.set_current.side_effect = lambda c: c

    result = campaign_service.set_current_campaign(campaign_id, actor_user_id=actor_id)

    mock_campaign_repo.set_current.assert_called_once_with(published_campaign)
    mock_audit_service.log_event.assert_called_once_with(
        action="campaign.set_current",
        entity_type="campaign",
        entity_id=result.id,
        actor_user_id=actor_id,
        details={"slug": result.slug},
    )


def test_t_camp_05_c_set_current_idempotent(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Already current campaign returns without re-executing updates."""
    campaign_id = uuid.uuid4()
    current_campaign = Campaign(
        id=campaign_id,
        slug="already-current",
        is_published=True,
        is_current=True,
    )
    mock_campaign_repo.get_by_id.return_value = current_campaign

    result = campaign_service.set_current_campaign(campaign_id)

    assert result.is_current is True
    mock_campaign_repo.set_current.assert_not_called()


# ==============================================================================
# 2. open_campaign (BR-CAMP-06, 07, 11)
# ==============================================================================


@pytest.mark.br("BR-CAMP-06")
def test_t_camp_06_a_open_requires_published(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-06-a: Unpublished campaign cannot be opened (409 invalid_state_transition)."""
    campaign_id = uuid.uuid4()
    c = Campaign(
        id=campaign_id,
        status="upcoming",
        is_published=False,
    )
    mock_campaign_repo.get_by_id.return_value = c

    with pytest.raises(InvalidStateTransitionError, match="Unpublished campaign cannot be opened"):
        campaign_service.open_campaign(campaign_id)


@pytest.mark.br("BR-CAMP-06")
def test_t_camp_06_b_open_requires_participating_sites(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-06-b: Campaign with no participating sites cannot be opened."""
    campaign_id = uuid.uuid4()
    c = Campaign(
        id=campaign_id,
        status="upcoming",
        is_published=True,
    )
    mock_campaign_repo.get_by_id.return_value = c
    mock_campaign_repo.count_campaign_sites.return_value = 0

    with pytest.raises(InvalidStateTransitionError, match="at least one participating site"):
        campaign_service.open_campaign(campaign_id)


@pytest.mark.br("BR-CAMP-06")
def test_t_camp_06_c_open_ended_rejected(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-06-c: Ended campaign cannot be opened."""
    campaign_id = uuid.uuid4()
    c = Campaign(
        id=campaign_id,
        status="ended",
        is_published=True,
    )
    mock_campaign_repo.get_by_id.return_value = c

    with pytest.raises(InvalidStateTransitionError, match="Ended campaign cannot be opened"):
        campaign_service.open_campaign(campaign_id)


@pytest.mark.br("BR-CAMP-06")
@pytest.mark.br("BR-CAMP-07")
def test_t_camp_06_d_open_when_another_live_rejected(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-06-d / T-CAMP-07-a: Cannot open when another campaign is currently live."""
    campaign_id = uuid.uuid4()
    other_live_id = uuid.uuid4()

    c = Campaign(
        id=campaign_id,
        status="upcoming",
        is_published=True,
    )
    mock_campaign_repo.get_by_id.return_value = c
    mock_campaign_repo.count_campaign_sites.return_value = 2
    mock_campaign_repo.get_live_campaign.return_value = Campaign(id=other_live_id, status="live")

    with pytest.raises(InvalidStateTransitionError, match="Another campaign is currently live"):
        campaign_service.open_campaign(campaign_id)


@pytest.mark.br("BR-CAMP-06")
def test_t_camp_06_e_open_success(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-CAMP-06-e: Opening an eligible campaign transitions to live and current, logging audit event."""
    campaign_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    c = Campaign(
        id=campaign_id,
        slug="eligible-campaign",
        status="upcoming",
        is_published=True,
        is_current=False,
    )
    mock_campaign_repo.get_by_id.return_value = c
    mock_campaign_repo.count_campaign_sites.return_value = 2
    mock_campaign_repo.get_live_campaign.return_value = None

    def fake_open(camp: Campaign) -> Campaign:
        camp.status = "live"
        camp.is_current = True
        return camp

    mock_campaign_repo.open_campaign.side_effect = fake_open

    opened = campaign_service.open_campaign(campaign_id, actor_user_id=actor_id)

    assert opened.status == "live"
    assert opened.is_current is True
    mock_campaign_repo.open_campaign.assert_called_once_with(c)
    mock_audit_service.log_event.assert_called_once_with(
        action="campaign.open",
        entity_type="campaign",
        entity_id=opened.id,
        actor_user_id=actor_id,
        details={"slug": opened.slug},
    )


# ==============================================================================
# 3. revert_campaign (BR-CAMP-08, 11)
# ==============================================================================


@pytest.mark.br("BR-CAMP-08")
def test_t_camp_08_a_revert_with_donors_rejected(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-08-a: Live campaign with recorded donors cannot be reverted (409)."""
    campaign_id = uuid.uuid4()
    c = Campaign(
        id=campaign_id,
        status="live",
    )
    mock_campaign_repo.get_by_id.return_value = c
    mock_campaign_repo.has_donors.return_value = True

    with pytest.raises(InvalidStateTransitionError, match="recorded donors"):
        campaign_service.revert_campaign(campaign_id)

    mock_campaign_repo.revert_campaign.assert_not_called()


@pytest.mark.br("BR-CAMP-08")
def test_t_camp_08_b_revert_success(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-CAMP-08-b: Live campaign with no donors reverts to upcoming."""
    campaign_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    c = Campaign(
        id=campaign_id,
        slug="live-revert-drive",
        status="live",
    )
    mock_campaign_repo.get_by_id.return_value = c
    mock_campaign_repo.has_donors.return_value = False

    def fake_revert(camp: Campaign) -> Campaign:
        camp.status = "upcoming"
        return camp

    mock_campaign_repo.revert_campaign.side_effect = fake_revert

    reverted = campaign_service.revert_campaign(campaign_id, actor_user_id=actor_id)

    assert reverted.status == "upcoming"
    mock_campaign_repo.revert_campaign.assert_called_once_with(c)
    mock_audit_service.log_event.assert_called_once_with(
        action="campaign.revert",
        entity_type="campaign",
        entity_id=reverted.id,
        actor_user_id=actor_id,
        details={"slug": reverted.slug},
    )


@pytest.mark.br("BR-CAMP-08")
def test_t_camp_08_d_revert_non_live_rejected(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Reverting a non-live campaign raises InvalidStateTransitionError."""
    campaign_id = uuid.uuid4()
    c = Campaign(
        id=campaign_id,
        status="upcoming",
    )
    mock_campaign_repo.get_by_id.return_value = c

    with pytest.raises(InvalidStateTransitionError, match="Only a live campaign can be reverted"):
        campaign_service.revert_campaign(campaign_id)


@pytest.mark.br("BR-CAMP-11")
def test_t_camp_11_a_ended_cannot_open_or_revert(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CAMP-11-a: Ended campaign cannot be opened or reverted."""
    campaign_id = uuid.uuid4()
    c = Campaign(
        id=campaign_id,
        status="ended",
        is_published=True,
    )
    mock_campaign_repo.get_by_id.return_value = c

    with pytest.raises(InvalidStateTransitionError, match="Ended campaign cannot be opened"):
        campaign_service.open_campaign(campaign_id)

    with pytest.raises(InvalidStateTransitionError, match="Ended campaign cannot be reverted"):
        campaign_service.revert_campaign(campaign_id)


# ==============================================================================
# 4. end_campaign (BR-CAMP-09, 10)
# ==============================================================================


@pytest.mark.br("BR-CAMP-09")
def test_t_camp_09_a_unit_end_upcoming_rejected(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Upcoming campaign cannot be ended."""
    c_id = uuid.uuid4()
    mock_campaign_repo.get_by_id.return_value = Campaign(id=c_id, status="upcoming")

    with pytest.raises(InvalidStateTransitionError, match="Upcoming campaign cannot be ended"):
        campaign_service.end_campaign(c_id)


@pytest.mark.br("BR-CAMP-09")
def test_t_camp_09_b_unit_end_live_success(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """Live campaign ends cleanly, computes final stats, and logs audit event."""
    c_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    c = Campaign(id=c_id, slug="c-unit-end", status="live")
    mock_campaign_repo.get_by_id.return_value = c
    mock_campaign_repo.compute_final_stats.return_value = {
        "pledges": 5,
        "present": 10,
        "donated": 8,
        "heroes": 4,
        "by_site": [],
        "frozen_at": "2026-10-17T12:00:00Z",
    }
    mock_campaign_repo.end_campaign.side_effect = lambda camp, *_args, **_kwargs: camp

    res = campaign_service.end_campaign(c_id, actor_user_id=actor_id)

    mock_campaign_repo.acquire_lifecycle_lock.assert_called_once()
    mock_campaign_repo.compute_final_stats.assert_called_once()
    mock_campaign_repo.end_campaign.assert_called_once()
    mock_audit_service.log_event.assert_called_once_with(
        action="campaign.end",
        entity_type="campaign",
        entity_id=res.id,
        actor_user_id=actor_id,
        details={"slug": res.slug, "status": "ended"},
    )


@pytest.mark.br("BR-CAMP-10")
def test_t_camp_10_a_unit_re_end_refreshes_stats(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Ended campaign can be re-ended to refresh final_stats."""
    c_id = uuid.uuid4()
    c = Campaign(id=c_id, slug="c-unit-re-end", status="ended", personal_data_purged_at=None)
    mock_campaign_repo.get_by_id.return_value = c
    mock_campaign_repo.compute_final_stats.return_value = {"donated": 20}
    mock_campaign_repo.end_campaign.side_effect = lambda camp, *_args, **_kwargs: camp

    res = campaign_service.end_campaign(c_id)
    assert res.id == c_id
    mock_campaign_repo.compute_final_stats.assert_called_once()
    mock_campaign_repo.end_campaign.assert_called_once()


@pytest.mark.br("BR-CAMP-10")
def test_t_camp_10_b_unit_end_purged_rejected(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Purged campaign cannot be re-ended."""
    from datetime import UTC, datetime

    c_id = uuid.uuid4()
    c = Campaign(
        id=c_id,
        slug="c-unit-purged",
        status="ended",
        personal_data_purged_at=datetime.now(UTC),
    )
    mock_campaign_repo.get_by_id.return_value = c

    with pytest.raises(CampaignPurgedError, match="Campaign personal data has been purged"):
        campaign_service.end_campaign(c_id)
