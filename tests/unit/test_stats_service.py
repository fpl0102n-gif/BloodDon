"""Unit tests for StatsService (P8.2).

Per:
- docs/06-business-rules.md §9 (BR-COUNT-01..07, BR-RANK-01..05, BR-HERO-01..06)
- docs/07-test-cases.md §5.8 (T-COUNT-03, T-COUNT-04, T-RANK-05)
- docs/05-architecture.md §5 (Isolated unit testing with repository mocks)
"""

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.errors import NotFoundError
from app.models.campaign import Campaign, Site
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.pledges import PledgeRepository
from app.repositories.sites import SiteRepository
from app.schemas.stats import CampaignCountersOut
from app.services.stats import StatsService


@pytest.fixture
def mock_campaign_repo() -> MagicMock:
    return MagicMock(spec=CampaignRepository)


@pytest.fixture
def mock_donor_repo() -> MagicMock:
    return MagicMock(spec=DonorRepository)


@pytest.fixture
def mock_pledge_repo() -> MagicMock:
    return MagicMock(spec=PledgeRepository)


@pytest.fixture
def mock_site_repo() -> MagicMock:
    return MagicMock(spec=SiteRepository)


@pytest.fixture
def stats_service(
    mock_campaign_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_pledge_repo: MagicMock,
    mock_site_repo: MagicMock,
) -> StatsService:
    return StatsService(
        campaign_repository=mock_campaign_repo,
        donor_repository=mock_donor_repo,
        pledge_repository=mock_pledge_repo,
        site_repository=mock_site_repo,
    )


# ==============================================================================
# 1. Counters (BR-COUNT-01..04, T-COUNT-03, T-COUNT-04)
# ==============================================================================


@pytest.mark.br("BR-CAMP-03")
def test_get_campaign_counters_unpublished_raises_not_found(
    stats_service: StatsService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Unpublished/draft campaign counters request raises NotFoundError."""
    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=uuid.uuid4(), slug="draft-campaign", is_published=False, status="upcoming"
    )

    with pytest.raises(NotFoundError):
        stats_service.get_campaign_counters("draft-campaign")


@pytest.mark.critical
@pytest.mark.br("BR-COUNT-03")
def test_get_campaign_counters_ended_uses_frozen_final_stats_without_live_queries(
    stats_service: StatsService,
    mock_campaign_repo: MagicMock,
    mock_pledge_repo: MagicMock,
    mock_donor_repo: MagicMock,
) -> None:
    """T-COUNT-03-a (★): Ended campaign with final_stats uses frozen numbers without querying pledges or donors."""
    c_id = uuid.uuid4()
    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=c_id,
        slug="ended-c",
        is_published=True,
        status="ended",
        final_stats={
            "pledges": 45,
            "present": 30,
            "donated": 25,
            "heroes": 18,
            "frozen_at": "2026-11-20T18:00:00Z",
        },
    )

    res = stats_service.get_campaign_counters("ended-c")

    assert isinstance(res, CampaignCountersOut)
    assert res.pledges_count == 45
    assert res.present_count == 30
    assert res.donated_count == 25
    assert res.heroes_count == 18

    # INVARIANT: Repository live count methods MUST NOT be called!
    mock_pledge_repo.count_by_campaign.assert_not_called()
    mock_donor_repo.count_campaign_counters.assert_not_called()


@pytest.mark.br("BR-COUNT-04")
def test_get_campaign_counters_purged_campaign_retains_frozen_stats(
    stats_service: StatsService,
    mock_campaign_repo: MagicMock,
    mock_pledge_repo: MagicMock,
    mock_donor_repo: MagicMock,
) -> None:
    """T-COUNT-04: Purged campaign retains frozen stats even after personal data/pledges are purged."""
    c_id = uuid.uuid4()
    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=c_id,
        slug="purged-c",
        is_published=True,
        status="ended",
        final_stats={
            "pledges_count": 80,
            "present_count": 60,
            "donated_count": 50,
            "heroes_count": 30,
        },
    )

    res = stats_service.get_campaign_counters("purged-c")
    assert res.pledges_count == 80
    assert res.present_count == 60
    assert res.donated_count == 50
    assert res.heroes_count == 30

    mock_pledge_repo.count_by_campaign.assert_not_called()
    mock_donor_repo.count_campaign_counters.assert_not_called()


@pytest.mark.critical
@pytest.mark.br("BR-COUNT-03")
def test_get_campaign_counters_live_calculates_dynamically(
    stats_service: StatsService,
    mock_campaign_repo: MagicMock,
    mock_pledge_repo: MagicMock,
    mock_donor_repo: MagicMock,
) -> None:
    """T-COUNT-03-b (★): Live campaign queries live repositories."""
    c_id = uuid.uuid4()
    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=c_id,
        slug="live-c",
        is_published=True,
        status="live",
    )
    mock_pledge_repo.count_by_campaign.return_value = 12
    mock_donor_repo.count_campaign_counters.return_value = (10, 8, 5)

    res = stats_service.get_campaign_counters("live-c")
    assert res.pledges_count == 12
    assert res.present_count == 10
    assert res.donated_count == 8
    assert res.heroes_count == 5

    mock_pledge_repo.count_by_campaign.assert_called_once_with(c_id)
    mock_donor_repo.count_campaign_counters.assert_called_once_with(c_id)


# ==============================================================================
# 2. Ranking (BR-RANK-01..05, T-RANK-05)
# ==============================================================================


@pytest.mark.br("BR-CAMP-03")
def test_get_campaign_ranking_unpublished_raises_not_found(
    stats_service: StatsService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Unpublished/draft campaign ranking request raises NotFoundError."""
    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=uuid.uuid4(), slug="draft-c", is_published=False, status="upcoming"
    )

    with pytest.raises(NotFoundError):
        stats_service.get_campaign_ranking("draft-c")


@pytest.mark.critical
@pytest.mark.br("BR-RANK-05")
def test_get_campaign_ranking_ended_uses_frozen_by_site_and_current_site_info(
    stats_service: StatsService,
    mock_campaign_repo: MagicMock,
    mock_donor_repo: MagicMock,
    mock_site_repo: MagicMock,
) -> None:
    """T-RANK-05-a (★): Ended campaign with final_stats uses frozen counts and current site names."""
    site1_id = uuid.uuid4()
    site2_id = uuid.uuid4()
    site3_id = uuid.uuid4()

    final_stats: dict[str, Any] = {
        "pledges": 100,
        "present": 80,
        "donated": 70,
        "heroes": 40,
        "by_site": [
            {
                "site_id": str(site1_id),
                "name": "اسم قديم لموقع 1",
                "donated": 40,
            },
            {
                "site_id": str(site2_id),
                "name": "اسم قديم لموقع 2",
                "donated": 40,
            },
            {
                "site_id": str(site3_id),
                "name": "اسم قديم لموقع 3",
                "donated": 10,
            },
        ],
    }

    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=uuid.uuid4(),
        slug="ended-ranking-c",
        is_published=True,
        status="ended",
        final_stats=final_stats,
    )

    # Current sites in database have updated names
    site1 = Site(id=site1_id, name="موقع ب بعد التعديل", logo_key="logos/b.png", is_active=True)
    site2 = Site(id=site2_id, name="موقع أ بعد التعديل", logo_key="logos/a.png", is_active=False)
    # site3 is deleted from DB
    mock_site_repo.get_by_id.side_effect = lambda sid: {
        site1_id: site1,
        site2_id: site2,
    }.get(sid)

    ranking = stats_service.get_campaign_ranking("ended-ranking-c")

    # Invariant: Live donor ranking query is NOT called
    mock_donor_repo.get_live_site_ranking.assert_not_called()

    assert len(ranking) == 3

    # Tie between site 1 and site 2 (both 40 donors) -> ranks 1, 1 (sorted alphabetically by current name)
    # "موقع أ بعد التعديل" comes before "موقع ب بعد التعديل"
    assert ranking[0].site_id == site2_id
    assert ranking[0].site_name == "موقع أ بعد التعديل"
    assert ranking[0].rank == 1
    assert ranking[0].donors_count == 40
    assert ranking[0].logo_url == "logos/a.png"

    assert ranking[1].site_id == site1_id
    assert ranking[1].site_name == "موقع ب بعد التعديل"
    assert ranking[1].rank == 1
    assert ranking[1].donors_count == 40
    assert ranking[1].logo_url == "logos/b.png"

    # Site 3: rank 3 (standard competition ranking: 1, 1, 3 per BR-RANK-03)
    assert ranking[2].site_id == site3_id
    assert ranking[2].site_name == "اسم قديم لموقع 3"  # Fallback to snapshot name
    assert ranking[2].rank == 3
    assert ranking[2].donors_count == 10
    assert ranking[2].logo_url is None


@pytest.mark.br("BR-RANK-02")
def test_get_campaign_ranking_participating_site_with_zero_donors_appears_with_zero(
    stats_service: StatsService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
) -> None:
    """BR-RANK-02: Participating site with zero donors appears with donors_count=0."""
    site_zero_id = uuid.uuid4()
    site_active_id = uuid.uuid4()

    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=uuid.uuid4(),
        slug="c-zero-donors",
        is_published=True,
        status="ended",
        final_stats={
            "by_site": [
                {"site_id": str(site_zero_id), "name": "موقع الصفر", "donated": 0},
                {"site_id": str(site_active_id), "name": "موقع النشط", "donated": 5},
            ]
        },
    )

    mock_site_repo.get_by_id.return_value = None

    ranking = stats_service.get_campaign_ranking("c-zero-donors")
    assert len(ranking) == 2
    assert ranking[0].site_name == "موقع النشط"
    assert ranking[0].donors_count == 5
    assert ranking[0].rank == 1

    assert ranking[1].site_name == "موقع الصفر"
    assert ranking[1].donors_count == 0
    assert ranking[1].rank == 2


@pytest.mark.critical
@pytest.mark.br("BR-RANK-05")
def test_get_campaign_ranking_live_uses_live_donor_ranking(
    stats_service: StatsService,
    mock_campaign_repo: MagicMock,
    mock_donor_repo: MagicMock,
) -> None:
    """T-RANK-05-b (★): Live campaign delegates to donor_repository.get_live_site_ranking."""
    c_id = uuid.uuid4()
    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=c_id,
        slug="live-ranking-c",
        is_published=True,
        status="live",
    )
    s1 = uuid.uuid4()
    s2 = uuid.uuid4()
    mock_donor_repo.get_live_site_ranking.return_value = [
        (s1, "موقع 1", "logo1.png", 20),
        (s2, "موقع 2", None, 10),
    ]

    ranking = stats_service.get_campaign_ranking("live-ranking-c")
    mock_donor_repo.get_live_site_ranking.assert_called_once_with(c_id)

    assert len(ranking) == 2
    assert ranking[0].rank == 1
    assert ranking[0].site_id == s1
    assert ranking[0].donors_count == 20
    assert ranking[1].rank == 2
    assert ranking[1].site_id == s2
    assert ranking[1].donors_count == 10
