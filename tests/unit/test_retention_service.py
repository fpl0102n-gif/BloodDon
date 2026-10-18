"""Isolated unit tests for RetentionService (P8.3).

Per:
- docs/06-business-rules.md §12 (BR-PRIV-01..08, BR-PRIV-11)
- docs/07-test-cases.md §5.10 (T-PRIV-01..08, T-PRIV-11)
- docs/05-architecture.md §5 (Layer boundaries: mock dependencies)
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.models.campaign import Campaign
from app.repositories.audit import PhoneRevealRepository
from app.repositories.campaigns import CampaignRepository
from app.repositories.sessions import SessionRepository
from app.repositories.user_tokens import UserTokenRepository
from app.services.audit import AuditService
from app.services.retention import RetentionService


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=Session)


@pytest.fixture
def mock_campaign_repo() -> MagicMock:
    return MagicMock(spec=CampaignRepository)


@pytest.fixture
def mock_audit_service() -> MagicMock:
    return MagicMock(spec=AuditService)


@pytest.fixture
def mock_phone_reveal_repo() -> MagicMock:
    return MagicMock(spec=PhoneRevealRepository)


@pytest.fixture
def mock_user_token_repo() -> MagicMock:
    return MagicMock(spec=UserTokenRepository)


@pytest.fixture
def mock_session_repo() -> MagicMock:
    return MagicMock(spec=SessionRepository)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2027, 11, 20, 10, 0, 0, tzinfo=UTC))


@pytest.fixture
def retention_service(
    mock_session: MagicMock,
    mock_campaign_repo: MagicMock,
    fake_clock: FakeClock,
    mock_audit_service: MagicMock,
    mock_phone_reveal_repo: MagicMock,
    mock_user_token_repo: MagicMock,
    mock_session_repo: MagicMock,
) -> RetentionService:
    return RetentionService(
        session=mock_session,
        campaign_repo=mock_campaign_repo,
        clock=fake_clock,
        audit_service=mock_audit_service,
        phone_reveal_repo=mock_phone_reveal_repo,
        user_token_repo=mock_user_token_repo,
        session_repo=mock_session_repo,
    )


# ==============================================================================
# Unit Tests
# ==============================================================================


@pytest.mark.br("BR-PRIV-01")
def test_unit_get_cutoff_date_is_strictly_more_than_365_days(
    retention_service: RetentionService,
    fake_clock: FakeClock,
) -> None:
    """Cutoff date is exactly today - 366 days (strictly > 365 days ago per T-PRIV-01-b)."""
    today = fake_clock.today()  # 2027-11-20
    expected_cutoff = today - timedelta(days=366)  # 2026-11-19
    assert retention_service.get_cutoff_date() == expected_cutoff


@pytest.mark.br("BR-PRIV-01")
def test_unit_get_eligible_campaigns_delegates_to_repo(
    retention_service: RetentionService,
    mock_campaign_repo: MagicMock,
) -> None:
    """get_eligible_campaigns delegates to campaign_repo with cutoff date."""
    cutoff = retention_service.get_cutoff_date()
    mock_campaign_repo.get_eligible_for_purge.return_value = []

    res = retention_service.get_eligible_campaigns()
    assert res == []
    mock_campaign_repo.get_eligible_for_purge.assert_called_once_with(cutoff)


@pytest.mark.br("BR-PRIV-11")
def test_unit_cleanup_periodic_data(
    retention_service: RetentionService,
    mock_phone_reveal_repo: MagicMock,
    mock_user_token_repo: MagicMock,
    mock_session_repo: MagicMock,
    fake_clock: FakeClock,
) -> None:
    """cleanup_periodic_data triggers reveal log, token, and session purge."""
    now = fake_clock.now()
    mock_phone_reveal_repo.delete_older_than.return_value = 5
    mock_user_token_repo.delete_expired_tokens.return_value = 3
    mock_session_repo.delete_expired.return_value = 8

    res = retention_service.cleanup_periodic_data(now)

    assert res == (5, 3, 8)
    mock_phone_reveal_repo.delete_older_than.assert_called_once_with(now - timedelta(days=7))
    mock_user_token_repo.delete_expired_tokens.assert_called_once_with(now)
    mock_session_repo.delete_expired.assert_called_once_with(now)


def test_unit_purge_dry_run_does_not_modify_anything(
    retention_service: RetentionService,
    mock_campaign_repo: MagicMock,
    mock_session: MagicMock,
) -> None:
    """dry_run=True returns simulation without mutating database."""
    c = Campaign(
        id=uuid.uuid4(),
        slug="c-dry",
        status="ended",
        campaign_date=date(2026, 1, 1),
        final_stats={"donated": 10},
    )
    mock_campaign_repo.get_eligible_for_purge.return_value = [c]
    mock_session.scalar.return_value = 0

    res = retention_service.purge(dry_run=True)

    assert res.dry_run is True
    assert res.campaigns_evaluated == 1
    assert len(res.campaign_results) == 1
    assert res.campaign_results[0].status == "dry_run"

    # Invariant: session.begin_nested() is NEVER called in dry-run
    mock_session.begin_nested.assert_not_called()
