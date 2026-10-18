"""Unit tests for PledgeService business rules (P6.2 & P8.4).

Per:
- docs/04-api.md §16 (DELETE /admin/pledges/{id})
- docs/06-business-rules.md §5 (BR-PLEDGE-10)
- docs/06-business-rules.md §12 (BR-PRIV-10)
- docs/07-test-cases.md §5.12 (T-PRIV-10-a)
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import ForbiddenError, NotFoundError
from app.models.pledge import Pledge
from app.models.user import User
from app.repositories.campaigns import CampaignRepository
from app.repositories.pledges import PledgeRepository
from app.repositories.sites import SiteRepository
from app.services.audit import AuditService
from app.services.pledges import PledgeService


@pytest.fixture
def mock_pledge_repo() -> MagicMock:
    return MagicMock(spec=PledgeRepository)


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
def sample_settings() -> Settings:
    settings = MagicMock(spec=Settings)
    settings.pledges_enabled = True
    return settings


@pytest.fixture
def pledge_service(
    mock_pledge_repo: MagicMock,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    sample_settings: Settings,
    mock_audit_service: MagicMock,
) -> PledgeService:
    return PledgeService(
        repository=mock_pledge_repo,
        campaign_repository=mock_campaign_repo,
        site_repository=mock_site_repo,
        settings=sample_settings,
        audit_service=mock_audit_service,
    )


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


@pytest.fixture
def sample_supervisor() -> User:
    return User(
        id=uuid.uuid4(),
        email="sup@bloodday.local",
        full_name="مشرف الموقع",
        role="supervisor",
        site_id=uuid.uuid4(),
        is_active=True,
    )


@pytest.mark.unit
@pytest.mark.br("BR-PRIV-10")
def test_delete_pledge_success(
    pledge_service: PledgeService,
    mock_pledge_repo: MagicMock,
    mock_audit_service: MagicMock,
    sample_admin: User,
) -> None:
    """T-PRIV-10-a: Admin deletes pledge on request, emits data.deleted_on_request audit event."""
    pledge = Pledge(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        site_id=uuid.uuid4(),
        first_name="كريم",
        phone="0550111222",
    )
    mock_pledge_repo.get_by_id.return_value = pledge

    pledge_service.delete_pledge(sample_admin, pledge.id)

    mock_pledge_repo.delete.assert_called_once_with(pledge)
    mock_audit_service.log_event.assert_called_once_with(
        action="data.deleted_on_request",
        entity_type="pledge",
        entity_id=pledge.id,
        actor_user_id=sample_admin.id,
        details={
            "type": "pledge",
            "campaign_id": str(pledge.campaign_id),
            "site_id": str(pledge.site_id),
        },
    )


@pytest.mark.unit
@pytest.mark.br("BR-PLEDGE-10")
def test_delete_pledge_forbidden_for_non_admin(
    pledge_service: PledgeService,
    sample_supervisor: User,
) -> None:
    """Non-admin callers cannot delete pledges."""
    with pytest.raises(ForbiddenError, match="Only administrators"):
        pledge_service.delete_pledge(sample_supervisor, uuid.uuid4())


@pytest.mark.unit
@pytest.mark.br("BR-PLEDGE-10")
def test_delete_pledge_not_found(
    pledge_service: PledgeService,
    mock_pledge_repo: MagicMock,
    sample_admin: User,
) -> None:
    """Non-existent pledge yields NotFoundError."""
    mock_pledge_repo.get_by_id.return_value = None
    with pytest.raises(NotFoundError, match="not found"):
        pledge_service.delete_pledge(sample_admin, uuid.uuid4())
