"""Unit tests for SiteService (P4.1).

Per:
- docs/06-business-rules.md §4 (BR-SITE-01..04)
- docs/07-test-cases.md §5.3 (T-SITE-03-a, T-SITE-03-b, T-SITE-04-a)
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.clock import FakeClock
from app.core.errors import InUseError, NotFoundError
from app.models.campaign import Site
from app.repositories.sites import SiteRepository
from app.schemas.site import SiteCreate, SiteUpdate
from app.services.audit import AuditService
from app.services.sites import SiteService


@pytest.fixture
def mock_site_repo() -> MagicMock:
    return MagicMock(spec=SiteRepository)


@pytest.fixture
def mock_audit_service() -> MagicMock:
    return MagicMock(spec=AuditService)


@pytest.fixture
def site_service(mock_site_repo: MagicMock, mock_audit_service: MagicMock) -> SiteService:
    clock = FakeClock()
    return SiteService(
        repo=mock_site_repo,
        audit_service=mock_audit_service,
        clock=clock,
    )


def test_create_site_persists_and_audits(
    site_service: SiteService,
    mock_site_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    data = SiteCreate(
        name="نادي ابن سينا الطبي",
        address="كلية الطب، الجزائر العاصمة",
        map_url="https://maps.google.com/?q=36.75,3.05",
        latitude=Decimal("36.750000"),
        longitude=Decimal("3.050000"),
        description="مقر النادي الرئيسي للتبرع",
    )
    actor_id = uuid.uuid4()

    def _create_side_effect(site: Site) -> Site:
        site.id = uuid.uuid4()
        return site

    mock_site_repo.create.side_effect = _create_side_effect

    created = site_service.create_site(data, actor_user_id=actor_id)

    assert created.name == "نادي ابن سينا الطبي"
    assert created.address == "كلية الطب، الجزائر العاصمة"
    assert created.is_active is True
    mock_site_repo.create.assert_called_once()
    mock_audit_service.log_event.assert_called_once_with(
        action="site.create",
        entity_type="site",
        entity_id=created.id,
        actor_user_id=actor_id,
    )


def test_get_site_found(site_service: SiteService, mock_site_repo: MagicMock) -> None:
    site_id = uuid.uuid4()
    mock_site = Site(
        id=site_id,
        name="موقع أ",
        address="عنوان أ",
        is_active=True,
    )
    mock_site_repo.get_by_id.return_value = mock_site

    retrieved = site_service.get_site(site_id)
    assert retrieved.id == site_id
    assert retrieved.name == "موقع أ"


def test_get_site_not_found(site_service: SiteService, mock_site_repo: MagicMock) -> None:
    site_id = uuid.uuid4()
    mock_site_repo.get_by_id.return_value = None

    with pytest.raises(NotFoundError) as exc_info:
        site_service.get_site(site_id)
    assert str(site_id) in str(exc_info.value)


def test_list_sites_respects_filter(
    site_service: SiteService,
    mock_site_repo: MagicMock,
) -> None:
    site_service.list_sites(include_inactive=True)
    mock_site_repo.list_all.assert_called_with(include_inactive=True)

    site_service.list_sites(include_inactive=False)
    mock_site_repo.list_all.assert_called_with(include_inactive=False)


def test_update_site_fields_and_deactivation(
    site_service: SiteService,
    mock_site_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    site_id = uuid.uuid4()
    existing_site = Site(
        id=site_id,
        name="الاسم القديم",
        address="العنوان القديم",
        is_active=True,
    )
    mock_site_repo.get_by_id.return_value = existing_site
    mock_site_repo.update.side_effect = lambda s: s

    update_payload = SiteUpdate(
        name="الاسم المحدّث",
        is_active=False,
    )
    actor_id = uuid.uuid4()

    updated = site_service.update_site(site_id, update_payload, actor_user_id=actor_id)

    assert updated.name == "الاسم المحدّث"
    assert updated.is_active is False
    assert updated.address == "العنوان القديم"
    mock_site_repo.update.assert_called_once_with(existing_site)
    mock_audit_service.log_event.assert_called_once()


def test_delete_site_unused_success(
    site_service: SiteService,
    mock_site_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-SITE-03-b: Unused site can be hard deleted."""
    site_id = uuid.uuid4()
    existing_site = Site(
        id=site_id,
        name="موقع جديد",
        address="عنوان",
        is_active=True,
    )
    mock_site_repo.get_by_id.return_value = existing_site
    mock_site_repo.is_site_in_use.return_value = False
    actor_id = uuid.uuid4()

    site_service.delete_site(site_id, actor_user_id=actor_id)

    mock_site_repo.delete.assert_called_once_with(existing_site)
    mock_audit_service.log_event.assert_called_once_with(
        action="site.delete",
        entity_type="site",
        entity_id=site_id,
        actor_user_id=actor_id,
    )


def test_delete_site_in_use_raises_conflict(
    site_service: SiteService,
    mock_site_repo: MagicMock,
) -> None:
    """T-SITE-03-a: Site used in any campaign rejects deletion with InUseError (409)."""
    site_id = uuid.uuid4()
    existing_site = Site(
        id=site_id,
        name="موقع مستعمل",
        address="عنوان",
        is_active=True,
    )
    mock_site_repo.get_by_id.return_value = existing_site
    mock_site_repo.is_site_in_use.return_value = True

    with pytest.raises(InUseError) as exc_info:
        site_service.delete_site(site_id)

    assert "used in campaigns" in str(exc_info.value).lower()
    mock_site_repo.delete.assert_not_called()
