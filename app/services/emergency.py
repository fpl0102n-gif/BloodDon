"""Emergency Service for managing the public emergency donor board and reveal operations (P7.1 / P7.2).

Per:
- docs/06-business-rules.md §10 (BR-EMERG-01..14)
- docs/04-api.md §8 (GET /public/emergency/donors, POST /public/emergency/donors/{id}/reveal)
- docs/05-architecture.md §12 (Services layer: domain logic, no FastAPI/HTTP imports, no commits)
- docs/07-test-cases.md §5.9 (T-EMERG-01..14)
- docs/11-decisions.md (D-37 rest days, D-62 kill switch)
"""

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.clock import Clock, get_clock
from app.core.config import Settings
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.models.audit import PhoneRevealLog
from app.repositories.audit import PhoneRevealRepository
from app.repositories.donors import DonorRepository
from app.schemas.emergency import EmergencyDonorItemOut, EmergencyRevealOut
from app.services.audit import AuditService


class EmergencyService:
    """Domain service orchestrating emergency donor board queries and reveal operations."""

    donor_repo: DonorRepository
    phone_reveal_repo: PhoneRevealRepository
    audit_service: AuditService
    settings: Settings
    clock: Clock

    def __init__(
        self,
        donor_repo: DonorRepository,
        phone_reveal_repo: PhoneRevealRepository,
        audit_service: AuditService,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self.donor_repo = donor_repo
        self.phone_reveal_repo = phone_reveal_repo
        self.audit_service = audit_service
        self.settings = settings
        self.clock = clock or get_clock()

    def list_emergency_donors(
        self,
        *,
        blood_type: str,
        site_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[EmergencyDonorItemOut]:
        """List eligible donors on the public emergency board (BR-EMERG-01..04, 11).

        Rules:
        - D-62 / BR-EMERG-01: If emergency_enabled is false, raise ServiceUnavailableError (503).
        - BR-EMERG-01 (★): Eligible donors have consent_public_phone=True, status='donated',
          phone is not None, and anonymized_at is None.
        - BR-EMERG-02 (★): STRICT PRIVACY: The emergency list NEVER reveals phone numbers!
        - BR-EMERG-03: blood_type is required, site_id is optional filter.
        - BR-EMERG-04: Ordered by oldest donation first (donated_at ASC), capped at 50 results.
        - BR-EMERG-11 & D-37: days_since_donation is calculated in APP_TIMEZONE.
          If emergency_min_days_since_donation > 0, donors with days_since_donation < min_days
          are excluded. Default is 0 (no automatic exclusion).
        """
        # 1. Kill switch enforcement (D-62, docs/04 §8)
        if not self.settings.emergency_enabled:
            raise ServiceUnavailableError("Emergency service is temporarily unavailable.")

        # 2. Timezone-aware date calculations (BR-GEN-04, BR-EMERG-11)
        tz = ZoneInfo(self.settings.app_timezone)
        now_local = self.clock.now().astimezone(tz)
        current_date = now_local.date()

        # 3. Minimum rest days cutoff filter (BR-EMERG-11, D-37)
        min_days = self.settings.emergency_min_days_since_donation
        max_donated_at: datetime | None = None
        if min_days > 0:
            max_donation_date = current_date - timedelta(days=min_days)
            # End of that donation date in local timezone
            max_donated_at = datetime.combine(
                max_donation_date + timedelta(days=1),
                time.min,
                tzinfo=tz,
            ) - timedelta(microseconds=1)

        # 4. Fetch candidates from repository (capped at 50)
        capped_limit = min(max(1, limit), 50)
        rows = self.donor_repo.list_emergency_donors(
            blood_type=blood_type,
            site_id=site_id,
            max_donated_at=max_donated_at,
            limit=capped_limit,
        )

        # 5. Transform into output items with days_since_donation
        items: list[EmergencyDonorItemOut] = []
        for donor, site_name in rows:
            if donor.donated_at is None:
                continue
            donation_date = donor.donated_at.astimezone(tz).date()
            days_since = max(0, (current_date - donation_date).days)
            if min_days > 0 and days_since < min_days:
                continue
            items.append(
                EmergencyDonorItemOut(
                    donor_id=donor.id,
                    first_name=donor.first_name,
                    blood_type=donor.blood_type,  # type: ignore[arg-type]
                    site_name=site_name,
                    days_since_donation=days_since,
                )
            )

        return items[:capped_limit]

    def reveal_donor_phone(
        self,
        donor_id: uuid.UUID,
        *,
        ip_hash: str,
    ) -> EmergencyRevealOut:
        """Reveal contact phone for an eligible volunteer on the emergency board (BR-EMERG-05..08, 11).

        Rules:
        - D-62: If emergency_enabled is false, raise ServiceUnavailableError (503).
        - BR-EMERG-06 (★): Non-existent, unconsented, non-donated, anonymized, or rest-period donors
          MUST raise an IDENTICAL NotFoundError (404) to prevent enumeration/discrimination.
        - BR-EMERG-08: Log reveal to phone_reveal_log with ip_hash and donor_id, and log
          audit event emergency.reveal with donor_id (NO phone number).
        - BR-EMERG-13 / T-EMERG-13-a: Check for reveal spike across the system.
        """
        # 1. Kill switch enforcement (D-62)
        if not self.settings.emergency_enabled:
            raise ServiceUnavailableError("Emergency service is temporarily unavailable.")

        # 2. Fetch donor by ID
        donor = self.donor_repo.get_by_id(donor_id)

        # 3. Indistinguishable eligibility verification (BR-EMERG-06 ★, T-EMERG-06-a ★)
        not_found_error = NotFoundError("Donor not found or not eligible for emergency contact.")

        if donor is None:
            raise not_found_error
        if donor.anonymized_at is not None:
            raise not_found_error
        if not donor.consent_public_phone:
            raise not_found_error
        if donor.status != "donated":
            raise not_found_error
        if not donor.phone:
            raise not_found_error
        if donor.donated_at is None:
            raise not_found_error

        # 4. Minimum rest days cutoff check (BR-EMERG-11, D-37)
        tz = ZoneInfo(self.settings.app_timezone)
        now_local = self.clock.now().astimezone(tz)
        current_date = now_local.date()
        donation_date = donor.donated_at.astimezone(tz).date()
        days_since = max(0, (current_date - donation_date).days)

        min_days = self.settings.emergency_min_days_since_donation
        if min_days > 0 and days_since < min_days:
            raise not_found_error

        now_utc = self.clock.now()

        # 5. Record reveal in phone_reveal_log (BR-EMERG-08)
        reveal_entry = PhoneRevealLog(
            ip_hash=ip_hash,
            donor_id=donor.id,
            created_at=now_utc,
        )
        self.phone_reveal_repo.create(reveal_entry)

        # 6. Audit event logging (BR-EMERG-08, BR-AUDIT-01 ★)
        # STRICT PRIVACY: actor_user_id is None, details contains ONLY ip_hash (NO phone!)
        self.audit_service.log_event(
            action="emergency.reveal",
            entity_type="donor",
            entity_id=donor.id,
            actor_user_id=None,
            details={"ip_hash": ip_hash},
        )

        # 7. Spike detection check (BR-EMERG-13, T-EMERG-13-a)
        one_hour_ago = now_utc - timedelta(hours=1)
        total_recent_reveals = self.phone_reveal_repo.count_total_since(one_hour_ago)
        spike_threshold = self.settings.rate_emergency_reveal_per_hour * 3
        if total_recent_reveals > spike_threshold:
            self.audit_service.log_event(
                action="security.reveal_spike",
                entity_type="system",
                actor_user_id=None,
                details={
                    "total_reveals_1h": total_recent_reveals,
                    "threshold": spike_threshold,
                },
            )

        # 8. Return revealed phone
        return EmergencyRevealOut(phone=donor.phone)
