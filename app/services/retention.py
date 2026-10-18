"""Retention, data anonymization, and periodic cleanup domain service (P8.3).

Per:
- docs/06-business-rules.md §12 (BR-PRIV-01..08, BR-PRIV-11, BR-PRIV-12, BR-PRIV-15)
- docs/07-test-cases.md §5.10 (T-PRIV-01..08, T-PRIV-11, T-CONC-08)
- docs/11-decisions.md (Decision D-54: --dry-run option)
- docs/05-architecture.md §4, §5, §17 (Service layer boundaries, no direct commits inside service)
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, SystemClock
from app.models.audit import PhoneRevealLog
from app.models.campaign import Campaign
from app.models.donor import Donor
from app.models.photo import FileDeletionQueue, Photo
from app.models.pledge import Pledge
from app.models.user import AuthSession, UserToken
from app.repositories.audit import PhoneRevealRepository
from app.repositories.campaigns import CampaignRepository
from app.repositories.sessions import SessionRepository
from app.repositories.user_tokens import UserTokenRepository
from app.services.audit import AuditService

logger = logging.getLogger(__name__)


@dataclass
class CampaignPurgeResult:
    """Outcome for an individual campaign retention purge."""

    campaign_id: uuid.UUID
    slug: str
    donors_anonymized: int = 0
    heroes_retained: int = 0
    pledges_deleted: int = 0
    photos_enqueued: int = 0
    final_stats_generated: bool = False
    status: str = "purged"  # "purged", "skipped", "failed", "dry_run"
    error: str | None = None


@dataclass
class RetentionPurgeResult:
    """Consolidated report of a retention purge operation."""

    dry_run: bool
    campaigns_evaluated: int = 0
    campaigns_purged: int = 0
    campaigns_failed: int = 0
    campaign_results: list[CampaignPurgeResult] = field(default_factory=list)
    phone_reveals_deleted: int = 0
    tokens_deleted: int = 0
    sessions_deleted: int = 0


class RetentionService:
    """Domain service managing data retention schedules, donor anonymization, and log cleanup."""

    def __init__(
        self,
        session: Session,
        campaign_repo: CampaignRepository | None = None,
        clock: Clock | None = None,
        audit_service: AuditService | None = None,
        phone_reveal_repo: PhoneRevealRepository | None = None,
        user_token_repo: UserTokenRepository | None = None,
        session_repo: SessionRepository | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or SystemClock()
        self.campaign_repo = campaign_repo or CampaignRepository(session)
        self.audit_service = audit_service
        self.phone_reveal_repo = phone_reveal_repo or PhoneRevealRepository(session)
        self.user_token_repo = user_token_repo or UserTokenRepository(session)
        self.session_repo = session_repo or SessionRepository(session)

    def get_cutoff_date(self) -> date:
        """Return the maximum campaign_date eligible for retention purge.

        Per BR-PRIV-01 & T-PRIV-01-b:
        The retention threshold is strictly more than one year (> 365 elapsed days).
        A campaign exactly 365 days ago is NOT eligible; a campaign 366 days ago IS eligible.
        """
        today = self.clock.today()
        return today - timedelta(days=366)

    def get_eligible_campaigns(self) -> list[Campaign]:
        """Query unpurged ended campaigns older than the retention threshold (BR-PRIV-01)."""
        cutoff = self.get_cutoff_date()
        return self.campaign_repo.get_eligible_for_purge(cutoff)

    def _purge_single_campaign(
        self,
        campaign_id: uuid.UUID,
        now: datetime,
    ) -> CampaignPurgeResult:
        """Execute atomic anonymization and cleanup for a single campaign (BR-PRIV-02..06)."""
        # 1. Acquire row lock on Campaign (T-CONC-08)
        locked_campaign = self.session.scalars(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        ).first()

        if locked_campaign is None or locked_campaign.personal_data_purged_at is not None:
            return CampaignPurgeResult(
                campaign_id=campaign_id,
                slug=locked_campaign.slug if locked_campaign else str(campaign_id),
                status="skipped",
            )

        final_stats_generated = False
        # 2. BR-PRIV-05 ★: Ensure final_stats is computed and stored before any records are purged
        if locked_campaign.final_stats is None:
            locked_campaign.final_stats = self.campaign_repo.compute_final_stats(
                locked_campaign.id, now
            )
            final_stats_generated = True

        # 3. Lock and anonymize all donors of this campaign (BR-PRIV-02, BR-PRIV-03, T-CONC-08)
        donors = list(
            self.session.scalars(
                select(Donor).where(Donor.campaign_id == locked_campaign.id).with_for_update()
            ).all()
        )
        donors_anonymized = len(donors)
        heroes_retained = 0

        for donor in donors:
            donor.last_name = None
            donor.phone = None
            donor.blood_type = None
            donor.consent_keep_phone = False
            donor.consent_public_phone = False
            donor.consent_photo = False
            donor.anonymized_at = now

            if donor.consent_hero_name:
                heroes_retained += 1
                # BR-PRIV-03: first_name retained for heroes
            else:
                # BR-PRIV-03: non-hero donor first_name overwritten to 'محذوف'
                donor.first_name = "محذوف"

        # 4. Individual photos: delete records and enqueue storage keys into file_deletion_queue (BR-PRIV-04 ★)
        # General / atmosphere photos (donor_id is NULL) are kept intact per BR-PRIV-12!
        individual_photos = list(
            self.session.scalars(
                select(Photo)
                .where(
                    Photo.campaign_id == locked_campaign.id,
                    Photo.donor_id.is_not(None),
                )
                .with_for_update()
            ).all()
        )
        photos_enqueued = len(individual_photos)
        for photo in individual_photos:
            self.session.add(FileDeletionQueue(storage_key=photo.storage_key))
            self.session.delete(photo)

        # 5. Hard delete all pledges for the campaign (BR-PRIV-04 ★)
        pledges_del_stmt = delete(Pledge).where(Pledge.campaign_id == locked_campaign.id)
        pledges_res = self.session.execute(pledges_del_stmt)
        pledges_deleted = int(getattr(pledges_res, "rowcount", 0))

        # 6. Set campaigns.personal_data_purged_at timestamp (BR-PRIV-06)
        locked_campaign.personal_data_purged_at = now
        self.session.flush()

        # 7. Audit log event (BR-AUDIT-01 ★, BR-AUDIT-04)
        if self.audit_service is not None:
            self.audit_service.log_event(
                action="retention.purge",
                entity_type="campaign",
                entity_id=locked_campaign.id,
                actor_user_id=None,  # System CLI action
                details={"slug": locked_campaign.slug, "purged": True},
            )

        return CampaignPurgeResult(
            campaign_id=locked_campaign.id,
            slug=locked_campaign.slug,
            donors_anonymized=donors_anonymized,
            heroes_retained=heroes_retained,
            pledges_deleted=pledges_deleted,
            photos_enqueued=photos_enqueued,
            final_stats_generated=final_stats_generated,
            status="purged",
        )

    def _simulate_single_campaign(self, campaign: Campaign) -> CampaignPurgeResult:
        """Simulate purge of a single campaign without modifying the database (D-54)."""
        donors_count = int(
            self.session.scalar(
                select(func.count(Donor.id)).where(Donor.campaign_id == campaign.id)
            )
            or 0
        )
        heroes_count = int(
            self.session.scalar(
                select(func.count(Donor.id)).where(
                    Donor.campaign_id == campaign.id,
                    Donor.consent_hero_name.is_(True),
                )
            )
            or 0
        )
        photos_count = int(
            self.session.scalar(
                select(func.count(Photo.id)).where(
                    Photo.campaign_id == campaign.id,
                    Photo.donor_id.is_not(None),
                )
            )
            or 0
        )
        pledges_count = int(
            self.session.scalar(
                select(func.count(Pledge.id)).where(Pledge.campaign_id == campaign.id)
            )
            or 0
        )
        return CampaignPurgeResult(
            campaign_id=campaign.id,
            slug=campaign.slug,
            donors_anonymized=donors_count,
            heroes_retained=heroes_count,
            pledges_deleted=pledges_count,
            photos_enqueued=photos_count,
            final_stats_generated=(campaign.final_stats is None),
            status="dry_run",
        )

    def cleanup_periodic_data(self, now: datetime) -> tuple[int, int, int]:
        """Perform system-wide periodic cleanup per BR-PRIV-11.

        Cleans up:
        1. Phone reveal logs older than 7 days.
        2. Expired user tokens.
        3. Expired sessions.
        """
        # 1. Phone reveal logs (> 7 days)
        reveals_cutoff = now - timedelta(days=7)
        reveals_deleted = self.phone_reveal_repo.delete_older_than(reveals_cutoff)

        # 2. Expired user tokens
        tokens_deleted = self.user_token_repo.delete_expired_tokens(now)

        # 3. Expired sessions
        sessions_deleted = self.session_repo.delete_expired(now)

        self.session.flush()
        return (reveals_deleted, tokens_deleted, sessions_deleted)

    def _simulate_periodic_cleanup(self, now: datetime) -> tuple[int, int, int]:
        """Simulate periodic cleanup candidate counts without deleting (D-54)."""
        reveals_cutoff = now - timedelta(days=7)
        reveals_count = int(
            self.session.scalar(
                select(func.count(PhoneRevealLog.id)).where(
                    PhoneRevealLog.created_at < reveals_cutoff
                )
            )
            or 0
        )
        tokens_count = int(
            self.session.scalar(select(func.count(UserToken.id)).where(UserToken.expires_at < now))
            or 0
        )
        sessions_count = int(
            self.session.scalar(
                select(func.count(AuthSession.id)).where(AuthSession.expires_at < now)
            )
            or 0
        )
        return (reveals_count, tokens_count, sessions_count)

    def purge(self, *, dry_run: bool = False) -> RetentionPurgeResult:
        """Run the full retention purge schedule and periodic cleanup.

        Per:
        - BR-PRIV-01..08, BR-PRIV-11
        - BR-PRIV-08: Transaction per campaign; failure of one does not stop others.
        - Decision D-54: --dry-run produces complete preview without mutations.
        """
        now = self.clock.now()
        eligible = self.get_eligible_campaigns()

        if dry_run:
            campaign_results = [self._simulate_single_campaign(c) for c in eligible]
            rev_cnt, tok_cnt, sess_cnt = self._simulate_periodic_cleanup(now)
            return RetentionPurgeResult(
                dry_run=True,
                campaigns_evaluated=len(eligible),
                campaigns_purged=len(eligible),
                campaigns_failed=0,
                campaign_results=campaign_results,
                phone_reveals_deleted=rev_cnt,
                tokens_deleted=tok_cnt,
                sessions_deleted=sess_cnt,
            )

        campaign_results = []
        purged_count = 0
        failed_count = 0

        # Process each campaign in its own savepoint/transaction per BR-PRIV-08
        for campaign in eligible:
            try:
                with self.session.begin_nested():
                    result = self._purge_single_campaign(campaign.id, now)
                campaign_results.append(result)
                if result.status == "purged":
                    purged_count += 1
            except Exception as exc:
                logger.error(
                    "Retention purge failed for campaign %s: %s",
                    campaign.slug,
                    exc,
                    exc_info=True,
                )
                campaign_results.append(
                    CampaignPurgeResult(
                        campaign_id=campaign.id,
                        slug=campaign.slug,
                        status="failed",
                        error=str(exc),
                    )
                )
                failed_count += 1

        # Periodic cleanup runs in its own nested savepoint
        with self.session.begin_nested():
            rev_del, tok_del, sess_del = self.cleanup_periodic_data(now)

        return RetentionPurgeResult(
            dry_run=False,
            campaigns_evaluated=len(eligible),
            campaigns_purged=purged_count,
            campaigns_failed=failed_count,
            campaign_results=campaign_results,
            phone_reveals_deleted=rev_del,
            tokens_deleted=tok_del,
            sessions_deleted=sess_del,
        )
