"""Campaign service orchestrating domain business rules for campaigns (P4.2).

Per:
- docs/06-business-rules.md §3 (BR-CAMP-01..18)
- docs/04-api.md §13 (Admin Campaigns) & §6 (Public Campaigns)
- docs/05-architecture.md §5 (Layer boundaries: no commit/rollback, no HTTP/FastAPI imports)
"""

import uuid
from collections.abc import Sequence

from app.core.clock import Clock, get_clock
from app.core.errors import (
    CampaignClosedError,
    CampaignPurgedError,
    InUseError,
    InvalidStateTransitionError,
    NotFoundError,
    SlugTakenError,
    ValidationError,
)
from app.models.campaign import Campaign, CampaignSite, Site
from app.repositories.campaigns import CampaignRepository
from app.repositories.sites import SiteRepository
from app.schemas.campaign import MAX_CONTENT_BYTES, CampaignCreate, CampaignUpdate
from app.schemas.campaign_site import CampaignSitePut
from app.services.audit import AuditService


class CampaignService:
    """Domain service managing the lifecycle and rules for Campaigns and CampaignSite participation."""

    def __init__(
        self,
        repository: CampaignRepository,
        site_repository: SiteRepository | None = None,
        audit_service: AuditService | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repository = repository
        self.site_repository = site_repository
        self.audit_service = audit_service
        self.clock = clock or get_clock()

    def create_campaign(
        self,
        payload: CampaignCreate,
        actor_user_id: uuid.UUID | None = None,
    ) -> Campaign:
        """Create a new campaign adhering to BR-CAMP-01, BR-CAMP-02, and BR-CAMP-15."""
        # 1. Verify slug uniqueness per BR-CAMP-02 (rejects with 409 slug_taken)
        existing = self.repository.get_by_slug(payload.slug)
        if existing is not None:
            raise SlugTakenError(f"Campaign with slug '{payload.slug}' already exists")

        # 2. Verify content byte length per BR-CAMP-15 (100 KB limit)
        if payload.content is not None:
            content_bytes = len(payload.content.encode("utf-8"))
            if content_bytes > MAX_CONTENT_BYTES:
                raise ValidationError(
                    f"Content size exceeds maximum limit of {MAX_CONTENT_BYTES} bytes"
                )

        # 3. Instantiate campaign enforcing BR-CAMP-01 (upcoming, unpublished, not current)
        campaign = Campaign(
            id=uuid.uuid4(),
            title=payload.title,
            slug=payload.slug,
            campaign_date=payload.campaign_date,
            summary=payload.summary,
            content=payload.content,
            cover_url=payload.cover_url,
            status="upcoming",  # BR-CAMP-01: always starts as upcoming
            is_published=False,  # BR-CAMP-01: unpublished by default
            is_current=False,  # BR-CAMP-01: not current by default
        )

        created = self.repository.create(campaign)

        # 4. Record audit event (no PII per BR-AUDIT-01)
        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="campaign.create",
                entity_type="campaign",
                entity_id=created.id,
                actor_user_id=actor_user_id,
                details={"slug": created.slug},
            )

        return created

    def get_campaign(self, campaign_id: uuid.UUID) -> Campaign:
        """Retrieve full campaign details for admin view."""
        campaign = self.repository.get_by_id(campaign_id)
        if campaign is None:
            raise NotFoundError(f"Campaign with id '{campaign_id}' was not found")
        return campaign

    def get_campaign_by_slug(self, slug: str) -> Campaign:
        """Retrieve campaign by slug for admin view."""
        campaign = self.repository.get_by_slug(slug)
        if campaign is None:
            raise NotFoundError(f"Campaign with slug '{slug}' was not found")
        return campaign

    def list_campaigns(
        self,
        *,
        status: str | None = None,
        is_published: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Campaign]:
        """List campaigns for admin management."""
        capped_limit = min(limit, 100)
        return self.repository.list_all(
            status=status,
            is_published=is_published,
            limit=capped_limit,
            offset=offset,
        )

    def update_campaign(
        self,
        campaign_id: uuid.UUID,
        payload: CampaignUpdate,
        actor_user_id: uuid.UUID | None = None,
    ) -> Campaign:
        """Update campaign attributes enforcing BR-CAMP-02, BR-CAMP-12, BR-CAMP-14, and BR-CAMP-15."""
        campaign = self.repository.get_by_id(campaign_id)
        if campaign is None:
            raise NotFoundError(f"Campaign with id '{campaign_id}' was not found")

        # 1. Slug change check (BR-CAMP-02)
        if payload.slug is not None and payload.slug != campaign.slug:
            existing = self.repository.get_by_slug(payload.slug)
            if existing is not None and existing.id != campaign.id:
                raise SlugTakenError(f"Campaign with slug '{payload.slug}' already exists")
            campaign.slug = payload.slug

        # 2. Content size check (BR-CAMP-15)
        if payload.content is not None:
            content_bytes = len(payload.content.encode("utf-8"))
            if content_bytes > MAX_CONTENT_BYTES:
                raise ValidationError(
                    f"Content size exceeds maximum limit of {MAX_CONTENT_BYTES} bytes"
                )
            campaign.content = payload.content

        # 3. Publish state transitions (BR-CAMP-12)
        if payload.is_published is not None:
            # BR-CAMP-12: Unpublishing is forbidden while the campaign is live
            if not payload.is_published and campaign.status == "live":
                raise InvalidStateTransitionError(
                    "Cannot unpublish a campaign while its status is live"
                )
            # BR-CAMP-12: Unpublishing a current campaign revokes its current status
            if not payload.is_published and campaign.is_current:
                campaign.is_current = False

            campaign.is_published = payload.is_published

        # 4. Campaign date change check (BR-CAMP-14)
        if payload.campaign_date is not None and payload.campaign_date != campaign.campaign_date:
            # BR-CAMP-14: campaign_date cannot be changed after the campaign becomes live or ended
            if campaign.status in ("live", "ended"):
                raise InvalidStateTransitionError(
                    f"Cannot modify campaign_date when campaign status is '{campaign.status}'"
                )
            campaign.campaign_date = payload.campaign_date

        # 5. Apply remaining attributes
        if payload.title is not None:
            campaign.title = payload.title
        if payload.summary is not None:
            campaign.summary = payload.summary
        if payload.cover_url is not None:
            campaign.cover_url = payload.cover_url

        updated = self.repository.update(campaign)

        # 6. Audit event
        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="campaign.update",
                entity_type="campaign",
                entity_id=updated.id,
                actor_user_id=actor_user_id,
                details={
                    "slug": updated.slug,
                    "is_published": updated.is_published,
                },
            )

        return updated

    def delete_campaign(
        self,
        campaign_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        """Conditionally delete campaign per BR-CAMP-13 (rejects with 409 in_use if data exists)."""
        campaign = self.repository.get_by_id(campaign_id)
        if campaign is None:
            raise NotFoundError(f"Campaign with id '{campaign_id}' was not found")

        # BR-CAMP-13: Deletion allowed ONLY if no pledges, donors, or photos exist
        if self.repository.is_campaign_in_use(campaign_id):
            raise InUseError("Cannot delete campaign: associated donors, pledges, or photos exist")

        self.repository.delete(campaign)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="campaign.delete",
                entity_type="campaign",
                entity_id=campaign_id,
                actor_user_id=actor_user_id,
                details={"slug": campaign.slug},
            )

    # --------------------------------------------------------------------------
    # Lifecycle State Transitions (docs/04 §13, BR-CAMP-04..08, 11)
    # --------------------------------------------------------------------------

    def set_current_campaign(
        self,
        campaign_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> Campaign:
        """Set a published campaign as the single active current campaign (BR-CAMP-04, 05)."""
        campaign = self.get_campaign(campaign_id)

        # BR-CAMP-05: Allowed only for a published campaign
        if not campaign.is_published:
            raise CampaignClosedError("Campaign must be published to be set as current")

        # Idempotent return if already current
        if campaign.is_current:
            return campaign

        updated = self.repository.set_current(campaign)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="campaign.set_current",
                entity_type="campaign",
                entity_id=updated.id,
                actor_user_id=actor_user_id,
                details={"slug": updated.slug},
            )

        return updated

    def open_campaign(
        self,
        campaign_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> Campaign:
        """Open an upcoming campaign to live status and set as current (BR-CAMP-06, 07, 11)."""
        self.repository.acquire_lifecycle_lock()

        campaign = self.get_campaign(campaign_id)

        # BR-CAMP-11: Ended campaigns cannot be opened
        if campaign.status == "ended":
            raise InvalidStateTransitionError("Ended campaign cannot be opened")

        # BR-CAMP-06: Must be published
        if not campaign.is_published:
            raise InvalidStateTransitionError("Unpublished campaign cannot be opened")

        # BR-CAMP-06: Must have at least one participating site
        if self.repository.count_campaign_sites(campaign_id) == 0:
            raise InvalidStateTransitionError(
                "Campaign must have at least one participating site to be opened"
            )

        # BR-CAMP-06 & BR-CAMP-07: No other campaign may be live
        live_campaign = self.repository.get_live_campaign()
        if live_campaign is not None and live_campaign.id != campaign.id:
            raise InvalidStateTransitionError("Another campaign is currently live")

        # Idempotent return if already live and current
        if campaign.status == "live" and campaign.is_current:
            return campaign

        opened = self.repository.open_campaign(campaign)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="campaign.open",
                entity_type="campaign",
                entity_id=opened.id,
                actor_user_id=actor_user_id,
                details={"slug": opened.slug},
            )

        return opened

    def revert_campaign(
        self,
        campaign_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> Campaign:
        """Revert a live campaign back to upcoming status (BR-CAMP-08, 11)."""
        self.repository.acquire_lifecycle_lock()

        campaign = self.get_campaign(campaign_id)

        # BR-CAMP-11: Ended campaign cannot be reverted
        if campaign.status == "ended":
            raise InvalidStateTransitionError("Ended campaign cannot be reverted")

        # BR-CAMP-08: Only live campaigns can be reverted
        if campaign.status != "live":
            raise InvalidStateTransitionError("Only a live campaign can be reverted")

        # BR-CAMP-08: Allowed only if no donors are recorded in the campaign
        if self.repository.has_donors(campaign_id):
            raise InvalidStateTransitionError("Cannot revert campaign with recorded donors")

        reverted = self.repository.revert_campaign(campaign)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="campaign.revert",
                entity_type="campaign",
                entity_id=reverted.id,
                actor_user_id=actor_user_id,
                details={"slug": reverted.slug},
            )

        return reverted

    def end_campaign(
        self,
        campaign_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> Campaign:
        """End a live campaign or refresh final_stats for an ended campaign (BR-CAMP-09, BR-CAMP-10).

        Per:
        - BR-CAMP-09 ★: Transactionally compute final_stats, transition status to 'ended',
          record ended_at, and log audit event 'campaign.end'.
        - BR-CAMP-10: Re-ending an ended campaign refreshes final_stats, unless purged.
        - T-CAMP-09-a: Ending upcoming campaign raises 409 invalid_state_transition.
        - T-CAMP-10-b: Ending purged campaign raises 409 campaign_purged.
        """
        self.repository.acquire_lifecycle_lock()

        campaign = self.get_campaign(campaign_id)

        # BR-CAMP-09: Upcoming campaign cannot be ended
        if campaign.status == "upcoming":
            raise InvalidStateTransitionError("Upcoming campaign cannot be ended")

        # BR-CAMP-10: Purged campaign cannot be re-ended
        if campaign.status == "ended" and campaign.personal_data_purged_at is not None:
            raise CampaignPurgedError(
                "Campaign personal data has been purged and cannot be modified"
            )

        if campaign.status not in ("live", "ended"):
            raise InvalidStateTransitionError(f"Cannot end campaign in status '{campaign.status}'")

        now = self.clock.now()
        final_stats = self.repository.compute_final_stats(campaign.id, frozen_at=now)
        ended = self.repository.end_campaign(
            campaign,
            final_stats=final_stats,
            ended_at=now,
        )

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="campaign.end",
                entity_type="campaign",
                entity_id=ended.id,
                actor_user_id=actor_user_id,
                details={
                    "slug": ended.slug,
                    "status": "ended",
                },
            )

        return ended

    # --------------------------------------------------------------------------
    # Public Discovery Methods (docs/04 §6, BR-CAMP-03)
    # --------------------------------------------------------------------------

    def get_public_campaign(self, slug: str) -> Campaign:
        """Retrieve single campaign for public view (BR-CAMP-03: published only)."""
        campaign = self.repository.get_by_slug(slug)
        if campaign is None or not campaign.is_published:
            # BR-CAMP-03: Unpublished campaign is treated as 404 not_found
            raise NotFoundError(f"Campaign with slug '{slug}' was not found")
        return campaign

    def list_public_campaigns(
        self,
        *,
        status: str | None = None,
        year: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Campaign]:
        """List published campaigns for public catalog, newest first (BR-CAMP-03)."""
        capped_limit = min(limit, 100)
        return self.repository.list_public(
            status=status,
            year=year,
            limit=capped_limit,
            offset=offset,
        )

    def get_public_current_campaign(self) -> Campaign:
        """Retrieve current campaign for public homepage (BR-CAMP-03, 04, 05)."""
        campaign = self.repository.get_current_public()
        if campaign is None:
            raise NotFoundError("No active published current campaign is available")
        return campaign

    # --------------------------------------------------------------------------
    # CampaignSite Participation Methods (docs/04 §13.1, §6.4, BR-CSITE-01..06)
    # --------------------------------------------------------------------------

    def list_campaign_sites(self, campaign_id: uuid.UUID) -> Sequence[CampaignSite]:
        """List participating sites for a campaign (admin view)."""
        # Ensure campaign exists
        self.get_campaign(campaign_id)
        return self.repository.list_campaign_sites(campaign_id)

    def get_campaign_site(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
    ) -> CampaignSite:
        """Get specific campaign site participation."""
        cs = self.repository.get_campaign_site(campaign_id, site_id)
        if cs is None:
            raise NotFoundError("Site participation not found in campaign")
        return cs

    def add_or_update_campaign_site(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
        payload: CampaignSitePut,
        actor_user_id: uuid.UUID | None = None,
    ) -> CampaignSite:
        """Add or update site participation in a campaign (BR-CSITE-02, 03, 05)."""
        # 1. Verify campaign exists
        self.get_campaign(campaign_id)

        # 2. Verify site exists and is active (BR-CSITE-02 / T-CSITE-02-a)
        site = None
        if self.site_repository is not None:
            site = self.site_repository.get_by_id(site_id)
            if site is None:
                raise NotFoundError(f"Site with id '{site_id}' was not found")
            if not site.is_active:
                raise ValidationError("Cannot add deactivated site to a campaign")

        # 3. Verify operating time order if both are set (BR-CSITE-03 / T-CSITE-03-a)
        if (
            payload.start_time is not None
            and payload.end_time is not None
            and payload.end_time <= payload.start_time
        ):
            raise ValidationError("end_time must be strictly greater than start_time")

        # 4. Upsert participation
        cs = self.repository.get_campaign_site(campaign_id, site_id)
        if cs is None:
            cs = CampaignSite(
                campaign_id=campaign_id,
                site_id=site_id,
                start_time=payload.start_time,
                end_time=payload.end_time,
                supervisors_text=payload.supervisors_text,
                notes=payload.notes,
            )
            action = "campaign_site.add"
        else:
            cs.start_time = payload.start_time
            cs.end_time = payload.end_time
            cs.supervisors_text = payload.supervisors_text
            cs.notes = payload.notes
            action = "campaign_site.update"

        saved = self.repository.save_campaign_site(cs)
        if getattr(saved, "site", None) is None and site is not None:
            saved.site = site

        # 5. Record audit event (no PII per BR-AUDIT-01)
        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action=action,
                entity_type="campaign_site",
                entity_id=site_id,
                actor_user_id=actor_user_id,
                details={
                    "campaign_id": str(campaign_id),
                    "site_id": str(site_id),
                },
            )

        return saved

    def remove_campaign_site(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        """Remove site participation from campaign (BR-CSITE-04 ★).

        Rejects with 409 in_use if donors or photos exist.
        Cascades removal of pledges.
        """
        cs = self.repository.get_campaign_site(campaign_id, site_id)
        if cs is None:
            raise NotFoundError("Site participation not found in campaign")

        # BR-CSITE-04: Cannot remove participation if donors or photos exist
        if self.repository.is_campaign_site_in_use(campaign_id, site_id):
            raise InUseError(
                "Cannot remove site participation: existing donors or photos are recorded"
            )

        self.repository.delete_campaign_site(cs)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="campaign_site.remove",
                entity_type="campaign_site",
                entity_id=site_id,
                actor_user_id=actor_user_id,
                details={
                    "campaign_id": str(campaign_id),
                    "site_id": str(site_id),
                },
            )

    def list_public_campaign_sites(
        self,
        slug: str,
    ) -> Sequence[tuple[CampaignSite, Site, int]]:
        """List active participating sites with pledges count for a published campaign (BR-CAMP-03, BR-SITE-02)."""
        campaign = self.get_public_campaign(slug)
        return self.repository.list_public_campaign_sites_with_pledges(campaign.id)
