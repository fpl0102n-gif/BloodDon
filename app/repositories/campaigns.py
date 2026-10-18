"""Campaign database repository.

Per:
- docs/05-architecture.md §4 & §5 (Layer boundaries: queries only, no commit/rollback)
- docs/02-database.md §3 & §5.1 (Campaigns schema and constraints)
- docs/06-business-rules.md §3 (BR-CAMP-01..18)
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, extract, func, select, text, update
from sqlalchemy.orm import Session, joinedload

from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.pledge import Pledge


class CampaignRepository:
    """Repository handling SQL persistence for Campaign entities and CampaignSite participation."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, campaign_id: uuid.UUID) -> Campaign | None:
        """Fetch a campaign by its primary UUID."""
        stmt = select(Campaign).where(Campaign.id == campaign_id)
        return self.db.scalars(stmt).first()

    def get_by_slug(self, slug: str) -> Campaign | None:
        """Fetch a campaign by its unique slug."""
        stmt = select(Campaign).where(Campaign.slug == slug)
        return self.db.scalars(stmt).first()

    def get_current_public(self) -> Campaign | None:
        """Fetch the single published current campaign (BR-CAMP-04, 05)."""
        stmt = select(Campaign).where(
            Campaign.is_current.is_(True),
            Campaign.is_published.is_(True),
        )
        return self.db.scalars(stmt).first()

    def get_current(self) -> Campaign | None:
        """Fetch the campaign currently marked is_current (regardless of published status)."""
        stmt = select(Campaign).where(Campaign.is_current.is_(True))
        return self.db.scalars(stmt).first()

    def get_live_campaign(self, for_share: bool = False) -> Campaign | None:
        """Fetch the active live campaign, if any (BR-CAMP-07: at most one live campaign)."""
        stmt = select(Campaign).where(Campaign.status == "live")
        if for_share and self.db.bind and self.db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(read=True)
        return self.db.scalars(stmt).first()

    def list_all(
        self,
        *,
        status: str | None = None,
        is_published: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Campaign]:
        """List campaigns for administrative management with optional filtering."""
        stmt = select(Campaign)
        if status is not None:
            stmt = stmt.where(Campaign.status == status)
        if is_published is not None:
            stmt = stmt.where(Campaign.is_published == is_published)

        stmt = (
            stmt.order_by(Campaign.campaign_date.desc(), Campaign.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return self.db.scalars(stmt).all()

    def list_public(
        self,
        *,
        status: str | None = None,
        year: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Campaign]:
        """List published campaigns for public consumption, newest date first (BR-CAMP-03)."""
        stmt = select(Campaign).where(Campaign.is_published.is_(True))
        if status is not None:
            stmt = stmt.where(Campaign.status == status)
        if year is not None:
            stmt = stmt.where(extract("year", Campaign.campaign_date) == year)

        stmt = (
            stmt.order_by(Campaign.campaign_date.desc(), Campaign.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return self.db.scalars(stmt).all()

    def create(self, campaign: Campaign) -> Campaign:
        """Insert and flush a new campaign entity."""
        self.db.add(campaign)
        self.db.flush()
        return campaign

    def update(self, campaign: Campaign) -> Campaign:
        """Flush and refresh an updated campaign entity."""
        self.db.flush()
        self.db.refresh(campaign)
        return campaign

    def delete(self, campaign: Campaign) -> None:
        """Delete a campaign entity."""
        self.db.delete(campaign)
        self.db.flush()

    def is_campaign_in_use(self, campaign_id: uuid.UUID) -> bool:
        """Check whether campaign has associated pledges, donors, or photos (BR-CAMP-13)."""
        stmt_pledge = select(Pledge.id).where(Pledge.campaign_id == campaign_id).limit(1)
        if self.db.scalars(stmt_pledge).first() is not None:
            return True

        stmt_donor = select(Donor.id).where(Donor.campaign_id == campaign_id).limit(1)
        if self.db.scalars(stmt_donor).first() is not None:
            return True

        stmt_photo = select(Photo.id).where(Photo.campaign_id == campaign_id).limit(1)
        return self.db.scalars(stmt_photo).first() is not None

    def acquire_lifecycle_lock(self) -> None:
        """Acquire transaction-scoped advisory lock in PostgreSQL for lifecycle concurrency control."""
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            self.db.execute(text("SELECT pg_advisory_xact_lock(74219)"))

    def count_campaign_sites(self, campaign_id: uuid.UUID) -> int:
        """Count participating sites for a campaign."""
        stmt = (
            select(func.count())
            .select_from(CampaignSite)
            .where(CampaignSite.campaign_id == campaign_id)
        )
        return self.db.scalar(stmt) or 0

    def has_donors(self, campaign_id: uuid.UUID) -> bool:
        """Check if any donors have been registered in this campaign."""
        stmt = select(Donor.id).where(Donor.campaign_id == campaign_id).limit(1)
        return self.db.scalars(stmt).first() is not None

    def set_current(self, campaign: Campaign) -> Campaign:
        """Set the campaign as the single current campaign in one atomic operation (BR-CAMP-04, 05)."""
        self.acquire_lifecycle_lock()
        # 1. Unset is_current on any existing current campaign and flush
        stmt_unset = (
            update(Campaign)
            .where(Campaign.is_current.is_(True), Campaign.id != campaign.id)
            .values(is_current=False)
        )
        self.db.execute(stmt_unset)
        self.db.flush()

        # 2. Set target campaign as current
        campaign.is_current = True
        self.db.flush()
        self.db.refresh(campaign)
        return campaign

    def open_campaign(self, campaign: Campaign) -> Campaign:
        """Open an upcoming campaign to live and make it current within one atomic operation (BR-CAMP-06)."""
        self.acquire_lifecycle_lock()
        # 1. Unset is_current on any existing current campaign and flush
        stmt_unset = (
            update(Campaign)
            .where(Campaign.is_current.is_(True), Campaign.id != campaign.id)
            .values(is_current=False)
        )
        self.db.execute(stmt_unset)
        self.db.flush()

        # 2. Set target campaign to live and current
        campaign.status = "live"
        campaign.is_current = True
        self.db.flush()
        self.db.refresh(campaign)
        return campaign

    def revert_campaign(self, campaign: Campaign) -> Campaign:
        """Revert a live campaign with no donors back to upcoming (BR-CAMP-08)."""
        self.acquire_lifecycle_lock()
        campaign.status = "upcoming"
        self.db.flush()
        self.db.refresh(campaign)
        return campaign

    def compute_final_stats(
        self,
        campaign_id: uuid.UUID,
        frozen_at: datetime,
    ) -> dict[str, Any]:
        """Atomically compute aggregated metrics and site breakdowns for final_stats (BR-CAMP-09)."""
        # 1. Total pledges for this campaign
        stmt_pledges = select(func.count(Pledge.id)).where(Pledge.campaign_id == campaign_id)
        pledges_count = int(self.db.scalar(stmt_pledges) or 0)

        # 2. Campaign-wide donor counts (present, donated, heroes)
        stmt_donors = select(
            func.count(Donor.id),
            func.count().filter(Donor.status == "donated"),
            func.count().filter(
                Donor.status == "donated",
                Donor.consent_hero_name.is_(True),
            ),
        ).where(Donor.campaign_id == campaign_id)
        donor_row = self.db.execute(stmt_donors).first()
        present_count = int(donor_row[0] or 0) if donor_row else 0
        donated_count = int(donor_row[1] or 0) if donor_row else 0
        heroes_count = int(donor_row[2] or 0) if donor_row else 0

        # 3. By-site breakdown for all participating sites
        pledges_subq = (
            select(
                Pledge.site_id.label("site_id"),
                func.count(Pledge.id).label("pledges_count"),
            )
            .where(Pledge.campaign_id == campaign_id)
            .group_by(Pledge.site_id)
            .subquery()
        )

        donors_subq = (
            select(
                Donor.site_id.label("site_id"),
                func.count(Donor.id).label("present_count"),
                func.count().filter(Donor.status == "donated").label("donated_count"),
            )
            .where(Donor.campaign_id == campaign_id)
            .group_by(Donor.site_id)
            .subquery()
        )

        stmt_sites = (
            select(
                CampaignSite.site_id,
                Site.name,
                func.coalesce(pledges_subq.c.pledges_count, 0).label("pledges"),
                func.coalesce(donors_subq.c.present_count, 0).label("present"),
                func.coalesce(donors_subq.c.donated_count, 0).label("donated"),
            )
            .join(Site, CampaignSite.site_id == Site.id)
            .outerjoin(pledges_subq, CampaignSite.site_id == pledges_subq.c.site_id)
            .outerjoin(donors_subq, CampaignSite.site_id == donors_subq.c.site_id)
            .where(CampaignSite.campaign_id == campaign_id)
            .order_by(
                func.coalesce(donors_subq.c.donated_count, 0).desc(),
                Site.name.asc(),
            )
        )

        site_rows = self.db.execute(stmt_sites).all()

        by_site = [
            {
                "site_id": str(r[0]),
                "name": r[1],
                "pledges": int(r[2]),
                "present": int(r[3]),
                "donated": int(r[4]),
            }
            for r in site_rows
        ]

        return {
            "pledges": pledges_count,
            "present": present_count,
            "donated": donated_count,
            "heroes": heroes_count,
            "by_site": by_site,
            "frozen_at": frozen_at.isoformat(),
        }

    def end_campaign(
        self,
        campaign: Campaign,
        final_stats: dict[str, Any],
        ended_at: datetime,
    ) -> Campaign:
        """Transition campaign to ended status and store frozen final_stats within single transaction (BR-CAMP-09)."""
        self.acquire_lifecycle_lock()
        campaign.status = "ended"
        if campaign.ended_at is None:
            campaign.ended_at = ended_at
        campaign.final_stats = final_stats
        self.db.flush()
        self.db.refresh(campaign)
        return campaign

    # --------------------------------------------------------------------------
    # CampaignSite Participation Queries (BR-CSITE-01..06)
    # --------------------------------------------------------------------------

    def get_campaign_site(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
    ) -> CampaignSite | None:
        """Fetch specific campaign-site participation link joined with Site."""
        stmt = (
            select(CampaignSite)
            .options(joinedload(CampaignSite.site))
            .where(
                CampaignSite.campaign_id == campaign_id,
                CampaignSite.site_id == site_id,
            )
        )
        return self.db.scalars(stmt).first()

    def list_campaign_sites(self, campaign_id: uuid.UUID) -> Sequence[CampaignSite]:
        """List all participating sites for a campaign joined with Site entity."""
        stmt = (
            select(CampaignSite)
            .options(joinedload(CampaignSite.site))
            .join(CampaignSite.site)
            .where(CampaignSite.campaign_id == campaign_id)
            .order_by(Site.name.asc())
        )
        return self.db.scalars(stmt).all()

    def list_public_campaign_sites_with_pledges(
        self,
        campaign_id: uuid.UUID,
    ) -> Sequence[tuple[CampaignSite, Site, int]]:
        """List active participating sites for public campaign page with pledges counts (BR-SITE-02)."""
        pledge_count_subq = (
            select(func.count(Pledge.id))
            .where(
                Pledge.campaign_id == CampaignSite.campaign_id,
                Pledge.site_id == CampaignSite.site_id,
            )
            .scalar_subquery()
        )
        stmt = (
            select(CampaignSite, Site, pledge_count_subq.label("pledges_count"))
            .join(Site, CampaignSite.site_id == Site.id)
            .where(
                CampaignSite.campaign_id == campaign_id,
                Site.is_active.is_(True),
            )
            .order_by(Site.name.asc())
        )
        rows = self.db.execute(stmt).all()
        return [(r[0], r[1], int(r[2] or 0)) for r in rows]

    def save_campaign_site(self, campaign_site: CampaignSite) -> CampaignSite:
        """Insert or flush a campaign-site participation record."""
        self.db.add(campaign_site)
        self.db.flush()
        return campaign_site

    def delete_campaign_site(self, campaign_site: CampaignSite) -> None:
        """Delete campaign-site participation and cascade-remove its pledges (BR-CSITE-04)."""
        # Explicitly delete any pledges belonging to this campaign-site link per BR-CSITE-04
        stmt_del_pledges = delete(Pledge).where(
            Pledge.campaign_id == campaign_site.campaign_id,
            Pledge.site_id == campaign_site.site_id,
        )
        self.db.execute(stmt_del_pledges)
        self.db.delete(campaign_site)
        self.db.flush()

    def is_campaign_site_in_use(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
    ) -> bool:
        """Check whether campaign site has donors or photos (BR-CSITE-04)."""
        stmt_donor = (
            select(Donor.id)
            .where(
                Donor.campaign_id == campaign_id,
                Donor.site_id == site_id,
            )
            .limit(1)
        )
        if self.db.scalars(stmt_donor).first() is not None:
            return True

        stmt_photo = (
            select(Photo.id)
            .where(
                Photo.campaign_id == campaign_id,
                Photo.site_id == site_id,
            )
            .limit(1)
        )
        return self.db.scalars(stmt_photo).first() is not None

    def get_eligible_for_purge(
        self,
        cutoff_date: Any,
    ) -> list[Campaign]:
        """Fetch ended campaigns eligible for retention purge (BR-PRIV-01).

        Eligibility:
        - status == 'ended'
        - personal_data_purged_at IS NULL
        - campaign_date <= cutoff_date (where cutoff_date is strictly > 365 days ago)
        """
        stmt = (
            select(Campaign)
            .where(
                Campaign.status == "ended",
                Campaign.personal_data_purged_at.is_(None),
                Campaign.campaign_date <= cutoff_date,
            )
            .order_by(Campaign.campaign_date.asc(), Campaign.id.asc())
        )
        return list(self.db.scalars(stmt).all())
