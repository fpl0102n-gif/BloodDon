"""Database repository for physical donation Site entities (P4.1).

Per docs/05-architecture.md §11 and docs/06-business-rules.md §4:
- Encapsulates SQLAlchemy database queries for sites.
- Does NOT perform business rule validation, authorization, or commits.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.pledge import Pledge


class SiteRepository:
    """Repository managing Site persistence, queries, and usage checks."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, site_id: uuid.UUID) -> Site | None:
        """Fetch a site by primary key UUID."""
        return self.db.get(Site, site_id)

    def get_by_name(self, name: str) -> Site | None:
        """Fetch a site by name."""
        stmt = select(Site).where(Site.name == name)
        return self.db.scalars(stmt).first()

    def list_all(self, *, include_inactive: bool = True) -> Sequence[Site]:
        """List sites, optionally filtering out deactivated sites."""
        stmt = select(Site).order_by(Site.name)
        if not include_inactive:
            stmt = stmt.where(Site.is_active.is_(True))
        return self.db.scalars(stmt).all()

    def create(self, site: Site) -> Site:
        """Persist a new Site entity."""
        self.db.add(site)
        self.db.flush()
        return site

    def update(self, site: Site) -> Site:
        """Flush modifications to an existing Site entity."""
        self.db.flush()
        return site

    def delete(self, site: Site) -> None:
        """Hard delete a Site entity."""
        self.db.delete(site)
        self.db.flush()

    def is_site_in_use(self, site_id: uuid.UUID) -> bool:
        """Check whether a site has been used in any campaign per BR-SITE-03."""
        # 1. Campaign participation
        stmt_cs = select(CampaignSite.site_id).where(CampaignSite.site_id == site_id).limit(1)
        if self.db.scalars(stmt_cs).first() is not None:
            return True

        # 2. Donors associated with site
        stmt_d = select(Donor.id).where(Donor.site_id == site_id).limit(1)
        if self.db.scalars(stmt_d).first() is not None:
            return True

        # 3. Pledges associated with site
        stmt_pl = select(Pledge.id).where(Pledge.site_id == site_id).limit(1)
        if self.db.scalars(stmt_pl).first() is not None:
            return True

        # 4. Photos associated with site
        stmt_ph = select(Photo.id).where(Photo.site_id == site_id).limit(1)
        return self.db.scalars(stmt_ph).first() is not None
