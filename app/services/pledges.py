"""Pledge service for managing visitor declarations of intent.

Per:
- docs/05-architecture.md §10 (Services layer: domain business rules).
- docs/06-business-rules.md §5 (BR-PLEDGE-01..11).
- docs/07-test-cases.md §5.4 (T-PLEDGE-01..11, T-CONC-07).
"""

import uuid

from app.core.config import Settings
from app.core.errors import (
    CampaignClosedError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    SiteNotInCampaignError,
)
from app.models.pledge import Pledge
from app.models.user import User
from app.repositories.campaigns import CampaignRepository
from app.repositories.pledges import PledgeRepository
from app.repositories.sites import SiteRepository
from app.schemas.pledge import PledgeCreateIn
from app.services.audit import AuditService


class PledgeService:
    """Service orchestrating business rules for visitor blood donation pledges."""

    def __init__(
        self,
        repository: PledgeRepository,
        campaign_repository: CampaignRepository,
        site_repository: SiteRepository,
        settings: Settings,
        *,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.campaign_repo = campaign_repository
        self.site_repo = site_repository
        self.settings = settings
        self.audit_service = audit_service

    def create_pledge(self, slug: str, data: PledgeCreateIn) -> Pledge | None:
        """Register a visitor's intention to attend a blood drive.

        Rules:
        - BR-PLEDGE-01 & D-62: If PLEDGES_ENABLED is false, raise 503 service_unavailable.
        - BR-CAMP-03 & BR-PLEDGE-02: Draft/unpublished campaign yields 404 not_found.
        - BR-PLEDGE-02: Campaign must be 'upcoming' or 'live'. If 'ended', raises 409 campaign_closed.
        - BR-PLEDGE-03 & BR-CSITE-06: Site must be participating in campaign; else 409 site_not_in_campaign.
        - BR-PLEDGE-05 (★): Honeypot website field non-empty -> silently return None without saving.
        - BR-PLEDGE-06 (★): Duplicate phone for same (campaign, site) does not create duplicate row.
        - BR-PLEDGE-07: Pledges without phone are not merged or blocked.
        - T-CONC-07: Concurrency race condition on identical phone resolves safely to single row.

        Returns:
            Pledge | None: Created or existing pledge, or None if honeypot triggered.
        """
        # 1. Operational kill switch (D-62, docs/04 §7.1)
        if not self.settings.pledges_enabled:
            raise ServiceUnavailableError("Pledges are temporarily unavailable.")

        # 2. Resolve published campaign (BR-CAMP-03, BR-PLEDGE-02)
        campaign = self.campaign_repo.get_by_slug(slug)
        if campaign is None or not campaign.is_published:
            raise NotFoundError(f"Campaign with slug '{slug}' not found.")

        if campaign.status not in {"upcoming", "live"}:
            raise CampaignClosedError(
                f"Campaign is in '{campaign.status}' state and is no longer accepting pledges."
            )

        # 3. Verify site participation (BR-PLEDGE-03, BR-CSITE-06)
        campaign_site = self.campaign_repo.get_campaign_site(campaign.id, data.site_id)
        if campaign_site is None:
            raise SiteNotInCampaignError("Site is not participating in this campaign.")

        # 4. Honeypot check (BR-PLEDGE-05 ★)
        if data.website and data.website.strip():
            # Bot filled hidden honeypot field -> silent 204 without persisting
            return None

        # 5. Duplicate phone handling (BR-PLEDGE-06 ★, T-CONC-07)
        if data.phone:
            pledge, _created = self.repository.create_if_not_exists(
                campaign_id=campaign.id,
                site_id=data.site_id,
                first_name=data.first_name,
                phone=data.phone,
            )
            return pledge

        # 6. Pledges without phone (BR-PLEDGE-07)
        new_pledge = Pledge(
            campaign_id=campaign.id,
            site_id=data.site_id,
            first_name=data.first_name,
            phone=None,
        )
        return self.repository.create(new_pledge)

    def delete_pledge(self, user: User, pledge_id: uuid.UUID) -> None:
        """Delete a pledge record on donor/owner request.

        Per:
        - docs/04-api.md §16 (DELETE /admin/pledges/{id}).
        - BR-PLEDGE-10 & BR-PRIV-10: Immediate hard deletion on owner request by admin.
        - BR-AUDIT-01 & BR-AUDIT-05: Emits data.deleted_on_request audit event (zero PII).
        """
        if user.role != "admin":
            raise ForbiddenError("Only administrators can delete pledges.")

        pledge = self.repository.get_by_id(pledge_id)
        if pledge is None:
            raise NotFoundError(f"Pledge with ID '{pledge_id}' not found.")

        campaign_id_str = str(pledge.campaign_id)
        site_id_str = str(pledge.site_id)

        self.repository.delete(pledge)

        if self.audit_service:
            self.audit_service.log_event(
                action="data.deleted_on_request",
                entity_type="pledge",
                entity_id=pledge_id,
                actor_user_id=user.id,
                details={
                    "type": "pledge",
                    "campaign_id": campaign_id_str,
                    "site_id": site_id_str,
                },
            )
