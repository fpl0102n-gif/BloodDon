"""Pledge repository for intention to attend records.

Per:
- docs/05-architecture.md §11 (Repositories layer: SQLAlchemy queries only).
- docs/02-database.md §5.8 (pledges table).
- docs/06-business-rules.md §5 (BR-PLEDGE-01..11).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.pledge import Pledge


class PledgeRepository:
    """Repository managing database persistence and queries for Pledge entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, pledge: Pledge) -> Pledge:
        """Add and flush a new pledge entity."""
        self.db.add(pledge)
        self.db.flush()
        return pledge

    def get_by_id(self, pledge_id: uuid.UUID) -> Pledge | None:
        """Fetch pledge by primary key ID."""
        stmt = select(Pledge).where(Pledge.id == pledge_id)
        return self.db.scalar(stmt)

    def get_by_campaign_site_phone(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
        phone: str,
    ) -> Pledge | None:
        """Fetch existing pledge matching (campaign_id, site_id, phone) per BR-PLEDGE-06."""
        stmt = select(Pledge).where(
            Pledge.campaign_id == campaign_id,
            Pledge.site_id == site_id,
            Pledge.phone == phone,
        )
        return self.db.scalar(stmt)

    def create_if_not_exists(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
        first_name: str,
        phone: str,
    ) -> tuple[Pledge | None, bool]:
        """Atomically create a pledge or resolve an existing duplicate under concurrency (T-CONC-07).

        Returns:
            tuple[Pledge | None, bool]: (pledge_entity, created_flag)
        """
        # 1. Fast pre-check
        existing = self.get_by_campaign_site_phone(campaign_id, site_id, phone)
        if existing is not None:
            return existing, False

        # 2. Attempt insert inside savepoint to isolate potential concurrent UniqueViolation
        try:
            with self.db.begin_nested():
                pledge = Pledge(
                    campaign_id=campaign_id,
                    site_id=site_id,
                    first_name=first_name,
                    phone=phone,
                )
                self.db.add(pledge)
                self.db.flush()
                return pledge, True
        except IntegrityError:
            # Race condition: concurrent transaction committed with identical (campaign_id, site_id, phone)
            existing = self.get_by_campaign_site_phone(campaign_id, site_id, phone)
            return existing, False

    def count_by_campaign(self, campaign_id: uuid.UUID) -> int:
        """Count total pledges registered for a campaign."""
        stmt = select(func.count(Pledge.id)).where(Pledge.campaign_id == campaign_id)
        return self.db.scalar(stmt) or 0

    def count_by_campaign_site(self, campaign_id: uuid.UUID, site_id: uuid.UUID) -> int:
        """Count total pledges registered for a site in a campaign."""
        stmt = select(func.count(Pledge.id)).where(
            Pledge.campaign_id == campaign_id,
            Pledge.site_id == site_id,
        )
        return self.db.scalar(stmt) or 0

    def delete(self, pledge: Pledge) -> None:
        """Hard delete a pledge entity."""
        self.db.delete(pledge)
        self.db.flush()
