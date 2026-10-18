"""Unit tests for CampaignSite participation business rules in CampaignService (P4.3).

Per:
- docs/06-business-rules.md §4 (BR-CSITE-01..06)
- docs/07-test-cases.md §5.3 (T-CSITE-01..06)
"""

import uuid
from datetime import time
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import InUseError, NotFoundError, ValidationError
from app.models.campaign import Campaign, CampaignSite, Site
from app.repositories.campaigns import CampaignRepository
from app.repositories.sites import SiteRepository
from app.schemas.campaign_site import CampaignSitePut
from app.services.audit import AuditService
from app.services.campaigns import CampaignService


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
def campaign_service(
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> CampaignService:
    return CampaignService(
        repository=mock_campaign_repo,
        site_repository=mock_site_repo,
        audit_service=mock_audit_service,
    )


@pytest.mark.br("BR-CSITE-02")
def test_t_csite_02_a_cannot_add_inactive_site(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
) -> None:
    """T-CSITE-02-a: Inactive site cannot participate in a campaign (422 validation_error)."""
    campaign_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_campaign_repo.get_by_id.return_value = Campaign(id=campaign_id, title="حملة اختبارية")
    inactive_site = Site(id=site_id, name="موقع معطل", is_active=False)
    mock_site_repo.get_by_id.return_value = inactive_site

    payload = CampaignSitePut(
        start_time=time(9, 0),
        end_time=time(17, 0),
    )

    with pytest.raises(ValidationError, match="Cannot add deactivated site"):
        campaign_service.add_or_update_campaign_site(campaign_id, site_id, payload)


@pytest.mark.br("BR-CSITE-02")
def test_t_csite_02_b_add_active_site_succeeds(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-CSITE-02-b: Active site can participate, recording audit event."""
    campaign_id = uuid.uuid4()
    site_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    mock_campaign_repo.get_by_id.return_value = Campaign(id=campaign_id, title="حملة اختبارية")
    active_site = Site(id=site_id, name="موقع نشط", is_active=True)
    mock_site_repo.get_by_id.return_value = active_site
    mock_campaign_repo.get_campaign_site.return_value = None
    mock_campaign_repo.save_campaign_site.side_effect = lambda cs: cs

    payload = CampaignSitePut(
        start_time=time(9, 0),
        end_time=time(17, 0),
        supervisors_text="د. أحمد و د. سارة",
        notes="ملاحظات تنظيمية",
    )

    result = campaign_service.add_or_update_campaign_site(
        campaign_id, site_id, payload, actor_user_id=actor_id
    )

    assert result.campaign_id == campaign_id
    assert result.site_id == site_id
    assert result.start_time == time(9, 0)
    assert result.end_time == time(17, 0)
    assert result.supervisors_text == "د. أحمد و د. سارة"
    assert result.notes == "ملاحظات تنظيمية"
    mock_campaign_repo.save_campaign_site.assert_called_once()
    mock_audit_service.log_event.assert_called_once_with(
        action="campaign_site.add",
        entity_type="campaign_site",
        entity_id=site_id,
        actor_user_id=actor_id,
        details={"campaign_id": str(campaign_id), "site_id": str(site_id)},
    )


@pytest.mark.br("BR-CSITE-03")
def test_t_csite_03_a_time_order_schema_validation() -> None:
    """T-CSITE-03-a: end_time must be strictly greater than start_time."""
    with pytest.raises(PydanticValidationError):
        CampaignSitePut(
            start_time=time(17, 0),
            end_time=time(9, 0),
        )

    with pytest.raises(PydanticValidationError):
        CampaignSitePut(
            start_time=time(10, 0),
            end_time=time(10, 0),
        )


@pytest.mark.br("BR-CSITE-03")
def test_t_csite_03_b_valid_times_or_null() -> None:
    """T-CSITE-03-b: start_time < end_time or optional times are accepted."""
    p1 = CampaignSitePut(start_time=time(8, 30), end_time=time(16, 30))
    assert p1.start_time == time(8, 30)
    assert p1.end_time == time(16, 30)

    p2 = CampaignSitePut(start_time=time(8, 30), end_time=None)
    assert p2.start_time == time(8, 30)
    assert p2.end_time is None

    p3 = CampaignSitePut()
    assert p3.start_time is None
    assert p3.end_time is None


@pytest.mark.br("BR-CSITE-04")
def test_t_csite_04_a_delete_rejected_when_in_use(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """T-CSITE-04-a: Removing site participation rejected with 409 in_use when donors or photos exist (★)."""
    campaign_id = uuid.uuid4()
    site_id = uuid.uuid4()

    cs = CampaignSite(campaign_id=campaign_id, site_id=site_id)
    mock_campaign_repo.get_campaign_site.return_value = cs
    mock_campaign_repo.is_campaign_site_in_use.return_value = True

    with pytest.raises(InUseError, match="existing donors or photos are recorded"):
        campaign_service.remove_campaign_site(campaign_id, site_id)

    mock_campaign_repo.delete_campaign_site.assert_not_called()


@pytest.mark.br("BR-CSITE-04")
def test_t_csite_04_c_delete_allowed_when_not_in_use(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-CSITE-04-c: Removing site participation succeeds and cascades pledges when no donors/photos exist."""
    campaign_id = uuid.uuid4()
    site_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    cs = CampaignSite(campaign_id=campaign_id, site_id=site_id)
    mock_campaign_repo.get_campaign_site.return_value = cs
    mock_campaign_repo.is_campaign_site_in_use.return_value = False

    campaign_service.remove_campaign_site(campaign_id, site_id, actor_user_id=actor_id)

    mock_campaign_repo.delete_campaign_site.assert_called_once_with(cs)
    mock_audit_service.log_event.assert_called_once_with(
        action="campaign_site.remove",
        entity_type="campaign_site",
        entity_id=site_id,
        actor_user_id=actor_id,
        details={"campaign_id": str(campaign_id), "site_id": str(site_id)},
    )


@pytest.mark.br("BR-CSITE-05")
def test_t_csite_05_update_participation_details(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-CSITE-05: supervisors_text and notes update presentation details with audit event."""
    campaign_id = uuid.uuid4()
    site_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    mock_campaign_repo.get_by_id.return_value = Campaign(id=campaign_id, title="حملة اختبارية")
    active_site = Site(id=site_id, name="موقع نشط", is_active=True)
    mock_site_repo.get_by_id.return_value = active_site

    existing_cs = CampaignSite(
        campaign_id=campaign_id,
        site_id=site_id,
        start_time=time(9, 0),
        end_time=time(15, 0),
        supervisors_text="قديم",
        notes="قديم",
    )
    mock_campaign_repo.get_campaign_site.return_value = existing_cs
    mock_campaign_repo.save_campaign_site.side_effect = lambda cs: cs

    payload = CampaignSitePut(
        start_time=time(8, 0),
        end_time=time(18, 0),
        supervisors_text="د. جديد",
        notes="ملاحظات جديدة",
    )

    updated = campaign_service.add_or_update_campaign_site(
        campaign_id, site_id, payload, actor_user_id=actor_id
    )

    assert updated.supervisors_text == "د. جديد"
    assert updated.notes == "ملاحظات جديدة"
    assert updated.start_time == time(8, 0)
    assert updated.end_time == time(18, 0)
    mock_audit_service.log_event.assert_called_once_with(
        action="campaign_site.update",
        entity_type="campaign_site",
        entity_id=site_id,
        actor_user_id=actor_id,
        details={"campaign_id": str(campaign_id), "site_id": str(site_id)},
    )


@pytest.mark.br("BR-CAMP-03")
def test_public_sites_requires_published_campaign(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Unpublished campaign rejects public site listing with 404 (BR-CAMP-03)."""
    mock_campaign_repo.get_by_slug.return_value = Campaign(
        id=uuid.uuid4(),
        slug="unpublished-drive",
        is_published=False,
    )

    with pytest.raises(NotFoundError, match="was not found"):
        campaign_service.list_public_campaign_sites("unpublished-drive")


def test_get_campaign_site_not_found(
    campaign_service: CampaignService,
    mock_campaign_repo: MagicMock,
) -> None:
    """Getting non-existent campaign site raises NotFoundError."""
    campaign_id = uuid.uuid4()
    site_id = uuid.uuid4()
    mock_campaign_repo.get_campaign_site.return_value = None

    with pytest.raises(NotFoundError, match="Site participation not found"):
        campaign_service.get_campaign_site(campaign_id, site_id)
