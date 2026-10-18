"""Donor repository for database persistence and querying.

Per:
- docs/05-architecture.md §4 & §5 (Repositories: queries only, no direct commits, no policy logic)
- docs/02-database.md §5.9 (Donors table and indexes)
- docs/06-business-rules.md §6 (BR-DONOR-09, BR-DONOR-14, BR-DONOR-17)
- docs/06-business-rules.md §9.3 (BR-COUNT-07)
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import FileDeletionQueue, Photo


class DonorRepository:
    """Repository handling SQL persistence and scoped querying for Donor entities."""

    db: Session

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, donor_id: uuid.UUID) -> Donor | None:
        """Fetch a single donor by their primary UUID."""
        stmt = select(Donor).where(Donor.id == donor_id)
        return self.db.scalars(stmt).first()

    def list_for_supervisor(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
        *,
        status: str | None = None,
        q: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[Donor], int]:
        """List donors belonging strictly to a site and campaign with optional status and name search.

        Per BR-DONOR-09 (Site Isolation) and BR-DONOR-17 (Prefix name search).
        """
        base_where = [
            Donor.campaign_id == campaign_id,
            Donor.site_id == site_id,
        ]

        if status is not None:
            base_where.append(Donor.status == status)

        if q is not None:
            clean_q = q.strip().lower()
            if clean_q:
                name_filter = or_(
                    func.lower(Donor.first_name).startswith(clean_q),
                    and_(
                        Donor.last_name.is_not(None),
                        func.lower(Donor.last_name).startswith(clean_q),
                    ),
                )
                base_where.append(name_filter)

        count_stmt = select(func.count(Donor.id)).where(*base_where)
        total = self.db.scalar(count_stmt) or 0

        items_stmt = (
            select(Donor)
            .where(*base_where)
            .order_by(Donor.created_at.desc(), Donor.id.desc())
            .limit(limit)
            .offset(offset)
        )
        items = self.db.scalars(items_stmt).all()

        return items, total

    def count_summary(self, campaign_id: uuid.UUID, site_id: uuid.UUID) -> tuple[int, int]:
        """Compute present and donated donor counts for a specific site in a campaign.

        Per BR-COUNT-07:
        - present: total donors checked in (any status)
        - donated: donors with status = 'donated'
        """
        stmt = select(
            func.count(Donor.id),
            func.count().filter(Donor.status == "donated"),
        ).where(
            Donor.campaign_id == campaign_id,
            Donor.site_id == site_id,
        )
        row = self.db.execute(stmt).first()
        if not row:
            return 0, 0
        return int(row[0] or 0), int(row[1] or 0)

    def count_site_counters(
        self, campaign_id: uuid.UUID, site_id: uuid.UUID
    ) -> tuple[int, int, int]:
        """Compute (present, donated, heroes) counts for a specific site in a campaign.

        Per BR-COUNT-01 & BR-COUNT-02:
        - present: total donors checked in (any status)
        - donated: donors with status = 'donated'
        - heroes: donors with status = 'donated' AND consent_hero_name = True
        """
        stmt = select(
            func.count(Donor.id),
            func.count().filter(Donor.status == "donated"),
            func.count().filter(
                Donor.status == "donated",
                Donor.consent_hero_name.is_(True),
            ),
        ).where(
            Donor.campaign_id == campaign_id,
            Donor.site_id == site_id,
        )
        row = self.db.execute(stmt).first()
        if not row:
            return 0, 0, 0
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

    def count_campaign_counters(self, campaign_id: uuid.UUID) -> tuple[int, int, int]:
        """Compute (present, donated, heroes) counts for the entire campaign.

        Per BR-COUNT-01 & BR-COUNT-02:
        - present: total donors checked in (any status)
        - donated: donors with status = 'donated'
        - heroes: donors with status = 'donated' AND consent_hero_name = True
        """
        stmt = select(
            func.count(Donor.id),
            func.count().filter(Donor.status == "donated"),
            func.count().filter(
                Donor.status == "donated",
                Donor.consent_hero_name.is_(True),
            ),
        ).where(
            Donor.campaign_id == campaign_id,
        )
        row = self.db.execute(stmt).first()
        if not row:
            return 0, 0, 0
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

    def create(self, donor: Donor) -> Donor:
        """Add and flush a new donor record."""
        self.db.add(donor)
        self.db.flush()
        return donor

    def update(self, donor: Donor) -> Donor:
        """Flush changes to an existing donor record."""
        self.db.flush()
        return donor

    def get_photos_by_donor(self, donor_id: uuid.UUID) -> Sequence[Photo]:
        """Fetch all individual photos associated with a donor record."""
        stmt = select(Photo).where(Photo.donor_id == donor_id)
        return self.db.scalars(stmt).all()

    def relocate_donor_photos(self, donor_id: uuid.UUID, new_site_id: uuid.UUID) -> Sequence[Photo]:
        """Update site_id for all individual photos associated with a donor record (BR-DONOR-12, BR-PHOTO-11)."""
        photos = list(self.get_photos_by_donor(donor_id))
        for photo in photos:
            photo.site_id = new_site_id
        self.db.flush()
        return photos

    def delete_photo_and_enqueue(self, photo: Photo) -> None:
        """Stage photo storage key into file deletion queue and delete photo record (BR-CONSENT-07)."""
        queue_entry = FileDeletionQueue(storage_key=photo.storage_key)
        self.db.add(queue_entry)
        self.db.delete(photo)
        self.db.flush()

    def delete_donor(self, donor: Donor) -> None:
        """Hard delete a donor entity from the database (BR-GEN-08, BR-DONOR-11)."""
        self.db.delete(donor)
        self.db.flush()

    def check_phone_exists(
        self,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
        phone: str,
    ) -> bool:
        """Check whether a donor with the given phone number already exists in this site and campaign (BR-DONOR-13)."""
        stmt = (
            select(Donor.id)
            .where(
                Donor.campaign_id == campaign_id,
                Donor.site_id == site_id,
                Donor.phone == phone,
            )
            .limit(1)
        )
        return self.db.scalar(stmt) is not None

    def list_public_heroes(
        self,
        campaign_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[tuple[str, uuid.UUID, str]], int]:
        """List public heroes (status=donated, consent_hero_name=True) with site metadata.

        Per:
        - BR-HERO-01: status='donated' AND consent_hero_name=True.
        - BR-HERO-02: first_name, site_id, site_name only.
        - BR-HERO-03: newest donation first (donated_at desc).
        - BR-HERO-04: optional site_id filter.
        - BR-GEN-05: pagination limit up to 200.
        """
        base_where = [
            Donor.campaign_id == campaign_id,
            Donor.status == "donated",
            Donor.consent_hero_name.is_(True),
        ]
        if site_id is not None:
            base_where.append(Donor.site_id == site_id)

        count_stmt = select(func.count(Donor.id)).where(*base_where)
        total = self.db.scalar(count_stmt) or 0

        items_stmt = (
            select(Donor.first_name, Donor.site_id, Site.name)
            .join(Site, Donor.site_id == Site.id)
            .where(*base_where)
            .order_by(Donor.donated_at.desc(), Donor.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = self.db.execute(items_stmt).all()
        items = [(str(r[0]), r[1], str(r[2])) for r in rows]
        return items, total

    def get_live_site_ranking(
        self,
        campaign_id: uuid.UUID,
    ) -> Sequence[tuple[uuid.UUID, str, str | None, int]]:
        """Compute live site ranking for all active participating sites in a campaign.

        Per:
        - BR-RANK-01: metric is count of donated donors per site.
        - BR-RANK-02: participating sites with zero donors MUST appear with 0.
        - BR-RANK-03: secondary sort by site name asc.
        """
        stmt = (
            select(
                Site.id,
                Site.name,
                Site.logo_key,
                func.count(Donor.id).label("donors_count"),
            )
            .join(CampaignSite, CampaignSite.site_id == Site.id)
            .outerjoin(
                Donor,
                and_(
                    Donor.campaign_id == campaign_id,
                    Donor.site_id == Site.id,
                    Donor.status == "donated",
                ),
            )
            .where(
                CampaignSite.campaign_id == campaign_id,
                Site.is_active.is_(True),
            )
            .group_by(Site.id, Site.name, Site.logo_key)
            .order_by(func.count(Donor.id).desc(), Site.name.asc())
        )
        rows = self.db.execute(stmt).all()
        return [(r[0], str(r[1]), r[2], int(r[3] or 0)) for r in rows]

    def list_emergency_donors(
        self,
        *,
        blood_type: str,
        site_id: uuid.UUID | None = None,
        max_donated_at: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[tuple[Donor, str]]:
        """List eligible emergency donors with donation site name (P7.1).

        Per:
        - BR-EMERG-01 (★): consent_public_phone=True, status='donated', phone is not None, anonymized_at is None.
        - BR-EMERG-02 (★): No phone in list. Returns donor entity and site name.
        - BR-EMERG-03: blood_type is mandatory filter, site_id is optional filter.
        - BR-EMERG-04: Oldest donation first (donated_at asc, id asc), hard limit of 50.
        - BR-EMERG-11: Optional cutoff timestamp for minimum rest days.
        """
        base_where = [
            Donor.blood_type == blood_type,
            Donor.consent_public_phone.is_(True),
            Donor.status == "donated",
            Donor.phone.is_not(None),
            Donor.anonymized_at.is_(None),
            Donor.donated_at.is_not(None),
        ]
        if site_id is not None:
            base_where.append(Donor.site_id == site_id)
        if max_donated_at is not None:
            base_where.append(Donor.donated_at <= max_donated_at)

        capped_limit = min(max(1, limit), 50)

        stmt = (
            select(Donor, Site.name)
            .join(Site, Donor.site_id == Site.id)
            .where(*base_where)
            .order_by(Donor.donated_at.asc(), Donor.id.asc())
            .limit(capped_limit)
        )
        rows = self.db.execute(stmt).all()
        return [(r[0], str(r[1])) for r in rows]
