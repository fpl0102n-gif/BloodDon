"""Public statistics, heroes roster, live counters, and site ranking domain service (P6.3).

Per:
- docs/04-api.md §6.5 (GET /public/campaigns/{slug}/counters)
- docs/04-api.md §6.6 (GET /public/campaigns/{slug}/heroes)
- docs/04-api.md §6.7 (GET /public/campaigns/{slug}/ranking)
- docs/06-business-rules.md §9 (BR-HERO-*, BR-RANK-*, BR-COUNT-*)
- docs/05-architecture.md §5 (Layer boundaries: no direct commits, no HTTP/FastAPI imports)
"""

import uuid
from typing import Any

from app.core.errors import NotFoundError
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.pledges import PledgeRepository
from app.repositories.sites import SiteRepository
from app.schemas.stats import (
    CampaignCountersOut,
    HeroPublicOut,
    SiteRankingOut,
)


class StatsService:
    """Domain service managing public counters, heroes gallery, and competition ranking."""

    def __init__(
        self,
        campaign_repository: CampaignRepository,
        donor_repository: DonorRepository,
        pledge_repository: PledgeRepository,
        site_repository: SiteRepository,
    ) -> None:
        self.campaign_repository = campaign_repository
        self.donor_repository = donor_repository
        self.pledge_repository = pledge_repository
        self.site_repository = site_repository

    def get_campaign_counters(self, slug: str) -> CampaignCountersOut:
        """Retrieve aggregated counter metrics for a published campaign.

        Per:
        - BR-CAMP-03: draft/unpublished campaigns return 404 not_found.
        - BR-COUNT-01 & BR-COUNT-02: live metrics (pledges, present, donated, heroes).
        - BR-COUNT-03 ★: ended campaigns with frozen final_stats return frozen snapshot directly.
        """
        campaign = self.campaign_repository.get_by_slug(slug)
        if campaign is None or not campaign.is_published:
            raise NotFoundError(f"Campaign with slug '{slug}' was not found")

        # BR-COUNT-03 ★: Frozen snapshot branch for ended campaigns
        if campaign.status == "ended" and campaign.final_stats:
            fs: dict[str, Any] = campaign.final_stats
            pledges = int(fs.get("pledges", fs.get("pledges_count", 0)))
            present = int(fs.get("present", fs.get("present_count", 0)))
            donated = int(fs.get("donated", fs.get("donated_count", 0)))
            heroes = int(fs.get("heroes", fs.get("heroes_count", 0)))
            return CampaignCountersOut(
                pledges_count=pledges,
                present_count=present,
                donated_count=donated,
                heroes_count=heroes,
            )

        # Live calculation branch
        pledges_count = self.pledge_repository.count_by_campaign(campaign.id)
        present, donated, heroes = self.donor_repository.count_campaign_counters(campaign.id)

        return CampaignCountersOut(
            pledges_count=pledges_count,
            present_count=present,
            donated_count=donated,
            heroes_count=heroes,
        )

    def list_public_heroes(
        self,
        slug: str,
        *,
        site_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[HeroPublicOut], int]:
        """List public heroes roster for a published campaign.

        Per:
        - BR-CAMP-03: draft/unpublished campaigns return 404 not_found.
        - BR-HERO-01 ★: donor must have status='donated' AND consent_hero_name=True.
        - BR-HERO-02 ★: exactly first_name, site_id, and site_name are exposed.
        - BR-HERO-03: newest donation first (donated_at desc).
        - BR-HERO-04: optional site_id filter.
        - BR-GEN-05: maximum limit is capped at 200.
        """
        campaign = self.campaign_repository.get_by_slug(slug)
        if campaign is None or not campaign.is_published:
            raise NotFoundError(f"Campaign with slug '{slug}' was not found")

        capped_limit = min(max(1, limit), 200)

        raw_heroes, total = self.donor_repository.list_public_heroes(
            campaign_id=campaign.id,
            site_id=site_id,
            limit=capped_limit,
            offset=offset,
        )

        items = [
            HeroPublicOut(
                first_name=h[0],
                site_id=h[1],
                site_name=h[2],
            )
            for h in raw_heroes
        ]
        return items, total

    def get_campaign_ranking(self, slug: str) -> list[SiteRankingOut]:
        """Compute standard competition ranking for participating sites in a campaign.

        Per:
        - BR-CAMP-03: draft/unpublished campaigns return 404 not_found.
        - BR-RANK-01: metric is count of donated donors per site.
        - BR-RANK-02: participating sites with zero donors appear with donors_count=0.
        - BR-RANK-03: ties get identical rank (1, 1, 3...), secondary sort by site name asc.
        - BR-RANK-05 ★: ended campaigns with final_stats use frozen by_site counts, joining sites
          for current name and logo without querying donors table.
        """
        campaign = self.campaign_repository.get_by_slug(slug)
        if campaign is None or not campaign.is_published:
            raise NotFoundError(f"Campaign with slug '{slug}' was not found")

        raw_items: list[tuple[uuid.UUID, str, str | None, int]] = []

        # BR-RANK-05 ★: Frozen stats branch for ended campaigns
        if (
            campaign.status == "ended"
            and campaign.final_stats
            and "by_site" in campaign.final_stats
        ):
            by_site_entries = campaign.final_stats.get("by_site", [])
            for entry in by_site_entries:
                site_id_val = entry["site_id"]
                s_id = uuid.UUID(str(site_id_val))
                donors_count = int(entry.get("donated", entry.get("donors_count", 0)))

                site = self.site_repository.get_by_id(s_id)
                if site is not None:
                    site_name = site.name
                    logo_url = site.logo_key
                else:
                    site_name = str(entry.get("name", "Unknown Site"))
                    logo_url = None

                raw_items.append((s_id, site_name, logo_url, donors_count))

            # BR-RANK-03: Sort by donors_count desc, site_name asc
            raw_items.sort(key=lambda item: (-item[3], item[1]))
        else:
            # Live calculation branch
            live_rows = self.donor_repository.get_live_site_ranking(campaign.id)
            raw_items = list(live_rows)

        # BR-RANK-03: Standard competition ranking (1, 1, 3...)
        ranking: list[SiteRankingOut] = []
        current_rank = 1
        for i, (s_id, s_name, logo, count) in enumerate(raw_items):
            if i > 0 and count < raw_items[i - 1][3]:
                current_rank = i + 1
            ranking.append(
                SiteRankingOut(
                    rank=current_rank,
                    site_id=s_id,
                    site_name=s_name,
                    logo_url=logo,
                    donors_count=count,
                )
            )

        return ranking
