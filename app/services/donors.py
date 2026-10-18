"""Domain service for donor operations and supervisor field workflows.

Per:
- docs/04-api.md §11 (Supervisor Context and Donors API)
- docs/05-architecture.md §12 (Services layer rules: domain logic, policy enforcement, no HTTP concepts)
- docs/06-business-rules.md §6 (BR-DONOR-01..18)
- docs/06-business-rules.md §7 (BR-CONSENT-01..11)
- docs/06-business-rules.md §9.3 (BR-COUNT-07)
- docs/07-test-cases.md §5.5 (T-DONOR-09, T-DONOR-14, T-DONOR-17, T-CAMP-16)
"""

import logging
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.clock import Clock, get_clock
from app.core.errors import (
    CampaignPurgedError,
    DuplicateIdConflictError,
    ForbiddenError,
    NoLiveCampaignError,
    NotFoundError,
    PhoneRequiresConsentError,
    PublicPhoneRequiresDataError,
    SiteNotInCampaignError,
    UnknownFieldError,
    ValidationError,
)
from app.models.campaign import Campaign, Site
from app.models.donor import Donor
from app.models.user import User
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.sites import SiteRepository
from app.schemas.donor import (
    DonorCheckPhoneIn,
    DonorCheckPhoneOut,
    DonorCreateIn,
    DonorListItemOut,
    DonorListResponse,
    DonorRelocateIn,
    DonorStatusUpdateIn,
    DonorSummaryOut,
    DonorUpdateIn,
    SupervisorContextCampaignOut,
    SupervisorContextOut,
    SupervisorContextSiteOut,
)
from app.services.audit import AuditService

logger = logging.getLogger("app.services.donors")


class DonorService:
    """Service governing donor entity lifecycles, supervisor dashboard context, and field isolation."""

    def __init__(
        self,
        donor_repo: DonorRepository,
        campaign_repo: CampaignRepository,
        site_repo: SiteRepository,
        *,
        audit_service: AuditService | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.donor_repo = donor_repo
        self.campaign_repo = campaign_repo
        self.site_repo = site_repo
        self.audit_service = audit_service
        self.clock = clock or get_clock()

    def _ensure_supervisor_live_context(
        self,
        user: User,
        for_write: bool = False,
    ) -> tuple[Campaign, Site]:
        """Validate that the caller is an active supervisor with an assigned site and a live campaign.

        Per:
        - BR-DONOR-01 & BR-DONOR-09: Restricted to supervisors within their assigned site.
        - BR-CAMP-16: Supervisor field workflows are permitted only during an active live campaign.
        - docs/04-api.md §11: Missing live campaign returns 409 no_live_campaign.
        - T-CONC-01: When for_write=True, acquires row share lock on live campaign preventing
          race conditions with campaign end.
        """
        if user.role != "supervisor" or not user.site_id:
            raise ForbiddenError("This operation is only available to assigned site supervisors.")

        live_campaign = self.campaign_repo.get_live_campaign(for_share=for_write)
        if live_campaign is None:
            raise NoLiveCampaignError("There is currently no live campaign.")

        site = self.site_repo.get_by_id(user.site_id)
        if site is None:
            raise NotFoundError(f"Assigned site '{user.site_id}' not found.")

        return live_campaign, site

    def get_supervisor_context(self, user: User) -> SupervisorContextOut:
        """Initialize supervisor dashboard with live campaign, assigned site, and real-time counts.

        Per docs/04-api.md §11.1 and BR-COUNT-07.
        """
        live_campaign, site = self._ensure_supervisor_live_context(user)
        present, donated = self.donor_repo.count_summary(live_campaign.id, site.id)

        return SupervisorContextOut(
            campaign=SupervisorContextCampaignOut(
                id=live_campaign.id,
                title=live_campaign.title,
                status=live_campaign.status,
            ),
            site=SupervisorContextSiteOut(
                id=site.id,
                name=site.name,
            ),
            summary=DonorSummaryOut(
                present=present,
                donated=donated,
            ),
        )

    def get_supervisor_summary(self, user: User) -> DonorSummaryOut:
        """Retrieve real-time counters (present, donated) for the supervisor's site (BR-COUNT-07)."""
        live_campaign, site = self._ensure_supervisor_live_context(user)
        present, donated = self.donor_repo.count_summary(live_campaign.id, site.id)

        return DonorSummaryOut(present=present, donated=donated)

    def list_supervisor_donors(
        self,
        user: User,
        *,
        status: str | None = None,
        q: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> DonorListResponse:
        """List donors at the supervisor's assigned site during the live campaign.

        Per:
        - BR-DONOR-09: Scoped strictly to caller's assigned site.
        - BR-DONOR-14 ★: List items contain NO phone numbers.
        - BR-DONOR-17: Name search matching beginning of first_name or last_name (case-insensitive).
        """
        live_campaign, site = self._ensure_supervisor_live_context(user)

        if status is not None and status not in ("attended", "donated"):
            raise ValidationError(
                f"Invalid donor status '{status}'. Status must be 'attended' or 'donated'."
            )

        donors, total = self.donor_repo.list_for_supervisor(
            campaign_id=live_campaign.id,
            site_id=site.id,
            status=status,
            q=q,
            limit=limit,
            offset=offset,
        )

        items = [DonorListItemOut.model_validate(donor, from_attributes=True) for donor in donors]

        return DonorListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_supervisor_donor(self, user: User, donor_id: uuid.UUID) -> Donor:
        """Retrieve complete donor record including phone for assigned site supervisor.

        Per:
        - BR-DONOR-09 ★ & T-DONOR-09-a ★: Accessing a donor outside supervisor's site or
          from another campaign MUST return 404 not_found, NEVER 403.
        - BR-DONOR-14 ★ & T-DONOR-14-b ★: Detail view exposes the donor's private phone.
        - docs/07 §4 line 137: Admins have no supervisor site context and receive 403 forbidden.
        """
        live_campaign, site = self._ensure_supervisor_live_context(user)

        donor = self.donor_repo.get_by_id(donor_id)
        if donor is None:
            raise NotFoundError(f"Donor with ID '{donor_id}' not found.")

        # Enforce strict site isolation: foreign site or non-current campaign yields 404
        if donor.site_id != site.id or donor.campaign_id != live_campaign.id:
            raise NotFoundError(f"Donor with ID '{donor_id}' not found.")

        return donor

    def get_admin_donor(self, user: User, donor_id: uuid.UUID) -> Donor:
        """Retrieve complete donor record for administrator across all campaigns and sites."""
        if user.role != "admin":
            raise ForbiddenError("This operation is restricted to system administrators.")
        donor = self.donor_repo.get_by_id(donor_id)
        if donor is None:
            raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
        return donor

    def create_donor(self, user: User, data: DonorCreateIn) -> tuple[Donor, bool]:
        """Record a new donor check-in at supervisor site.

        Per:
        - BR-DONOR-01..05 & docs/04-api.md §11.5 / §11.6:
          - Derives campaign_id, site_id, and registered_by from caller session and active live campaign.
          - Initial status is 'attended', donated_at is None (BR-DONOR-03).
          - Enforces consent and phone consistency rules (BR-CONSENT-01..03).
          - Handles client-supplied UUID idempotency:
            - If data.id already exists with identical data for same supervisor/site/campaign: returns (existing, False) -> 200 OK.
            - If data.id already exists with conflicting data, different supervisor, or foreign site/campaign: raises DuplicateIdConflictError -> 409 Conflict.
            - If new ID: creates record, logs audit event, returns (donor, True) -> 201 Created.
        """
        live_campaign, site = self._ensure_supervisor_live_context(user, for_write=True)

        # Defense-in-depth: Enforce consent rules at service layer (BR-CONSENT-02, BR-CONSENT-03)
        if data.phone is not None and not (data.consent_keep_phone or data.consent_public_phone):
            raise PhoneRequiresConsentError(
                "Phone number cannot be stored without explicit donor consent."
            )
        if data.consent_public_phone and not (
            data.phone is not None and data.blood_type is not None
        ):
            raise PublicPhoneRequiresDataError(
                "Public phone consent requires both a valid phone number and blood type."
            )

        blood_type_str = data.blood_type.value if data.blood_type is not None else None

        # Idempotency check if client supplied a UUID (BR-DONOR-05)
        if data.id is not None:
            existing = self.donor_repo.get_by_id(data.id)
            if existing is not None:
                # Compare supervisor, site, campaign, and content
                is_identical = (
                    existing.registered_by == user.id
                    and existing.site_id == site.id
                    and existing.campaign_id == live_campaign.id
                    and existing.first_name == data.first_name
                    and existing.last_name == data.last_name
                    and existing.phone == data.phone
                    and existing.blood_type == blood_type_str
                    and existing.consent_keep_phone == data.consent_keep_phone
                    and existing.consent_hero_name == data.consent_hero_name
                    and existing.consent_photo == data.consent_photo
                    and existing.consent_public_phone == data.consent_public_phone
                )
                if is_identical:
                    # Safe idempotent replay (BR-DONOR-05, T-DONOR-05-b)
                    return existing, False

                # ID exists but with different supervisor, site, campaign, or content
                raise DuplicateIdConflictError(
                    "ID conflict: record with this identifier already exists with different data."
                )

        donor_id = data.id or uuid.uuid4()
        donor = Donor(
            id=donor_id,
            campaign_id=live_campaign.id,
            site_id=site.id,
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            blood_type=blood_type_str,
            status="attended",
            consent_keep_phone=data.consent_keep_phone,
            consent_hero_name=data.consent_hero_name,
            consent_photo=data.consent_photo,
            consent_public_phone=data.consent_public_phone,
            registered_by=user.id,
            donated_at=None,
        )

        try:
            with self.donor_repo.db.begin_nested():
                self.donor_repo.create(donor)
        except IntegrityError as err:
            # Handles concurrent insert race condition (T-CONC-04)
            existing = self.donor_repo.get_by_id(donor_id)
            if existing is not None:
                is_identical = (
                    existing.registered_by == user.id
                    and existing.site_id == site.id
                    and existing.campaign_id == live_campaign.id
                    and existing.first_name == data.first_name
                    and existing.last_name == data.last_name
                    and existing.phone == data.phone
                    and existing.blood_type == blood_type_str
                    and existing.consent_keep_phone == data.consent_keep_phone
                    and existing.consent_hero_name == data.consent_hero_name
                    and existing.consent_photo == data.consent_photo
                    and existing.consent_public_phone == data.consent_public_phone
                )
                if is_identical:
                    return existing, False
                raise DuplicateIdConflictError(
                    "ID conflict: record with this identifier already exists with different data."
                ) from err
            raise

        if self.audit_service:
            self.audit_service.log_event(
                action="donor.create",
                entity_type="donor",
                entity_id=donor.id,
                actor_user_id=user.id,
                details={
                    "site_id": str(site.id),
                    "campaign_id": str(live_campaign.id),
                    "status": "attended",
                },
            )

        return donor, True

    def update_donor(
        self,
        user: User,
        donor_id: uuid.UUID,
        data: DonorUpdateIn | dict[str, Any],
    ) -> Donor:
        """Update donor demographic information and consent flags.

        Per:
        - docs/04-api.md §11.8: Demographic and consent updates.
        - BR-GEN-07 & BR-DONOR-10 ★: Reserved fields (site_id, campaign_id, registered_by, status,
          donated_at, anonymized_at, role) rejected as unknown_field.
        - BR-DONOR-09 ★: Supervisor is restricted to their assigned site and live campaign (404 on foreign).
        - BR-DONOR-15: Anonymized records are immutable (409 campaign_purged).
        - BR-DONOR-16: Supervisors can only update during live campaign; admins can update in any campaign.
        - BR-CONSENT-02 & BR-CONSENT-03: Final-state evaluation of phone and public consents.
        - BR-CONSENT-04: Enforce consent consistency on complete final state, not isolated deltas.
        - BR-CONSENT-06 ★: Withdrawing keep consent requires explicit phone: null if public is false.
        - BR-CONSENT-07 ★: Withdrawing photo consent immediately purges individual donor photos and
          enqueues their storage keys in the same transaction.
        - BR-DONOR-18 & BR-AUDIT-01 ★: Emits donor.update audit event with changed field names (zero PII).
        """
        # Defense-in-depth: Rejection of reserved fields (BR-GEN-07, BR-DONOR-10, T-DONOR-10-b)
        reserved_fields = {
            "site_id",
            "campaign_id",
            "registered_by",
            "anonymized_at",
            "status",
            "role",
            "donated_at",
        }
        if isinstance(data, dict):
            for field in reserved_fields:
                if field in data and data[field] is not None:
                    raise UnknownFieldError(
                        f"Modifying reserved field '{field}' is strictly forbidden."
                    )
        else:
            for field in reserved_fields:
                if hasattr(data, field) and getattr(data, field, None) is not None:
                    raise UnknownFieldError(
                        f"Modifying reserved field '{field}' is strictly forbidden."
                    )

        # Supervisor vs Admin authorization and scope check (BR-DONOR-09, BR-DONOR-16)
        if user.role == "supervisor":
            live_campaign, site = self._ensure_supervisor_live_context(user, for_write=True)
            donor = self.donor_repo.get_by_id(donor_id)
            if donor is None:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
            if donor.site_id != site.id or donor.campaign_id != live_campaign.id:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
        elif user.role == "admin":
            donor = self.donor_repo.get_by_id(donor_id)
            if donor is None:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
        else:
            raise ForbiddenError(
                "Only assigned supervisors or administrators can update donor records."
            )

        # BR-DONOR-15 & T-DONOR-15-a: Anonymized records cannot be modified
        if donor.anonymized_at is not None:
            raise CampaignPurgedError("Donor record has been anonymized and cannot be modified.")

        # Extract fields present in update payload
        if isinstance(data, dict):
            fields_set = set(data.keys())
            first_name_in = data.get("first_name")
            last_name_in = data.get("last_name")
            phone_in = data.get("phone")
            blood_type_in = data.get("blood_type")
            keep_in = data.get("consent_keep_phone")
            hero_in = data.get("consent_hero_name")
            photo_in = data.get("consent_photo")
            public_in = data.get("consent_public_phone")
        else:
            fields_set = data.model_fields_set
            first_name_in = data.first_name
            last_name_in = data.last_name
            phone_in = data.phone
            blood_type_in = data.blood_type.value if data.blood_type is not None else None
            keep_in = data.consent_keep_phone
            hero_in = data.consent_hero_name
            photo_in = data.consent_photo
            public_in = data.consent_public_phone

        # Compute candidate final state (BR-CONSENT-04)
        final_first_name = first_name_in if "first_name" in fields_set else donor.first_name
        final_last_name = last_name_in if "last_name" in fields_set else donor.last_name
        final_phone = phone_in if "phone" in fields_set else donor.phone
        final_blood_type = blood_type_in if "blood_type" in fields_set else donor.blood_type
        final_keep = keep_in if "consent_keep_phone" in fields_set else donor.consent_keep_phone
        final_hero = hero_in if "consent_hero_name" in fields_set else donor.consent_hero_name
        final_photo = photo_in if "consent_photo" in fields_set else donor.consent_photo
        final_public = (
            public_in if "consent_public_phone" in fields_set else donor.consent_public_phone
        )

        # Evaluate Consent Truth Table on final state (BR-CONSENT-02, BR-CONSENT-06)
        if final_phone is not None and not (final_keep or final_public):
            raise PhoneRequiresConsentError(
                "Phone number cannot be stored without explicit donor consent."
            )

        # BR-CONSENT-03: Public phone requires both phone and blood_type
        if final_public and not (final_phone is not None and final_blood_type is not None):
            raise PublicPhoneRequiresDataError(
                "Public phone consent requires both a valid phone number and blood type."
            )

        # BR-CONSENT-07 ★: Photo consent withdrawal cleanup in same transaction
        if donor.consent_photo and final_photo is False:
            photos = self.donor_repo.get_photos_by_donor(donor.id)
            for photo in photos:
                self.donor_repo.delete_photo_and_enqueue(photo)

        # Apply updates and track changed field names
        changed_fields: list[str] = []
        if "first_name" in fields_set and final_first_name and donor.first_name != final_first_name:
            donor.first_name = str(final_first_name)
            changed_fields.append("first_name")

        if "last_name" in fields_set and donor.last_name != final_last_name:
            donor.last_name = final_last_name
            changed_fields.append("last_name")

        if "phone" in fields_set and donor.phone != final_phone:
            donor.phone = final_phone
            changed_fields.append("phone")

        if "blood_type" in fields_set and donor.blood_type != final_blood_type:
            donor.blood_type = final_blood_type
            changed_fields.append("blood_type")

        if "consent_keep_phone" in fields_set and donor.consent_keep_phone != final_keep:
            donor.consent_keep_phone = bool(final_keep)
            changed_fields.append("consent_keep_phone")

        if "consent_hero_name" in fields_set and donor.consent_hero_name != final_hero:
            donor.consent_hero_name = bool(final_hero)
            changed_fields.append("consent_hero_name")

        if "consent_photo" in fields_set and donor.consent_photo != final_photo:
            donor.consent_photo = bool(final_photo)
            changed_fields.append("consent_photo")

        if "consent_public_phone" in fields_set and donor.consent_public_phone != final_public:
            donor.consent_public_phone = bool(final_public)
            changed_fields.append("consent_public_phone")

        self.donor_repo.update(donor)

        # Audit event with zero PII (BR-DONOR-18, BR-AUDIT-01 ★)
        if changed_fields and self.audit_service:
            self.audit_service.log_event(
                action="donor.update",
                entity_type="donor",
                entity_id=donor.id,
                actor_user_id=user.id,
                details={
                    "updated_fields": changed_fields,
                },
            )

        # Deleting emergency phone on request per BR-PRIV-10 & docs/04 §17 note
        if (
            "consent_public_phone" in changed_fields
            and not donor.consent_public_phone
            and donor.phone is None
            and self.audit_service
        ):
            self.audit_service.log_event(
                action="data.deleted_on_request",
                entity_type="donor",
                entity_id=donor.id,
                actor_user_id=user.id,
                details={
                    "type": "emergency_phone",
                    "donor_id": str(donor.id),
                    "site_id": str(donor.site_id),
                    "campaign_id": str(donor.campaign_id),
                },
            )

        return donor

    def change_donor_status(
        self,
        user: User,
        donor_id: uuid.UUID,
        data: DonorStatusUpdateIn,
    ) -> Donor:
        """Update donor attendance status between attended and donated.

        Per:
        - docs/04-api.md §11.9: Status update (PUT /supervisor/donors/{id}/status).
        - BR-DONOR-06: Status transitions allowed in both directions (attended ⇄ donated).
        - BR-DONOR-07: donated_at populated on donated, cleared on attended.
        - BR-DONOR-08 ★: No 'rejected' or 'deferred' status (rejected via schema).
        - BR-DONOR-09 ★: Foreign site access yields 404, never 403.
        - BR-DONOR-15: Anonymized records cannot be modified (409 campaign_purged).
        - BR-CAMP-16 ★: Supervisors can only change status during a live campaign.
        - BR-DONOR-18 & BR-AUDIT-01 ★: Emits donor.status_change audit event with old/new status.
        """
        if user.role == "supervisor":
            live_campaign, site = self._ensure_supervisor_live_context(user, for_write=True)
            donor = self.donor_repo.get_by_id(donor_id)
            if donor is None:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
            if donor.site_id != site.id or donor.campaign_id != live_campaign.id:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
        elif user.role == "admin":
            donor = self.donor_repo.get_by_id(donor_id)
            if donor is None:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
        else:
            raise ForbiddenError(
                "Only assigned supervisors or administrators can change donor status."
            )

        if donor.anonymized_at is not None:
            raise CampaignPurgedError("Donor record has been anonymized and cannot be modified.")

        old_status = donor.status
        new_status = data.status

        if new_status == "donated":
            if donor.status != "donated":
                donor.status = "donated"
                donor.donated_at = self.clock.now()
            # If already donated, idempotent: donated_at is preserved unchanged (T-DONOR-06-b)
        elif new_status == "attended":
            donor.status = "attended"
            donor.donated_at = None

        self.donor_repo.update(donor)

        if old_status != new_status and self.audit_service:
            self.audit_service.log_event(
                action="donor.status_change",
                entity_type="donor",
                entity_id=donor.id,
                actor_user_id=user.id,
                details={
                    "old_status": old_status,
                    "new_status": new_status,
                },
            )

        return donor

    def delete_donor(
        self,
        user: User,
        donor_id: uuid.UUID,
    ) -> None:
        """Delete a donor record and cascade individual photos into file deletion queue.

        Per:
        - docs/04-api.md §11.10: Hard delete donor record (DELETE /supervisor/donors/{id}).
        - BR-GEN-08: Hard deletion; no soft-delete column.
        - BR-DONOR-09 ★: Foreign site access yields 404, never 403.
        - BR-DONOR-11: Supervisors can delete during live campaign only; individual photos deleted
          and their storage keys enqueued into file_deletion_queue in same transaction.
        - BR-DONOR-15: Anonymized records cannot be deleted (409 campaign_purged).
        - BR-CAMP-16 ★: Non-live campaign yields 409 no_live_campaign.
        - BR-DONOR-18 & BR-AUDIT-01 ★: Logs donor.delete audit event (zero PII).
        """
        if user.role == "supervisor":
            live_campaign, site = self._ensure_supervisor_live_context(user, for_write=True)
            donor = self.donor_repo.get_by_id(donor_id)
            if donor is None:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
            if donor.site_id != site.id or donor.campaign_id != live_campaign.id:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
        elif user.role == "admin":
            donor = self.donor_repo.get_by_id(donor_id)
            if donor is None:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")
        else:
            raise ForbiddenError(
                "Only assigned supervisors or administrators can delete donor records."
            )

        if donor.anonymized_at is not None:
            raise CampaignPurgedError("Donor record has been anonymized and cannot be modified.")

        # Cascade deletion of individual photos into file_deletion_queue (BR-DONOR-11, BR-PHOTO-10, T-DONOR-11-b ★)
        photos = self.donor_repo.get_photos_by_donor(donor.id)
        for photo in photos:
            self.donor_repo.delete_photo_and_enqueue(photo)

        site_id_str = str(donor.site_id)
        campaign_id_str = str(donor.campaign_id)

        self.donor_repo.delete_donor(donor)

        if self.audit_service:
            self.audit_service.log_event(
                action="donor.delete",
                entity_type="donor",
                entity_id=donor_id,
                actor_user_id=user.id,
                details={
                    "site_id": site_id_str,
                    "campaign_id": campaign_id_str,
                },
            )

    def check_phone(
        self,
        user: User,
        data: DonorCheckPhoneIn,
    ) -> DonorCheckPhoneOut:
        """Check if a phone number is already registered at the supervisor's site (advisory duplicate check).

        Per:
        - docs/04-api.md §11.4: Soft duplicate check (POST /supervisor/donors/check-phone).
        - Decision D-11: Advisory only, does NOT prevent donor registration (BR-DONOR-13).
        - T-DONOR-13-d: Phone is in POST body to prevent leaking in URLs or HTTP logs.
        - Scoped strictly to caller's assigned site and the current live campaign.
        """
        live_campaign, site = self._ensure_supervisor_live_context(user)
        exists = self.donor_repo.check_phone_exists(
            campaign_id=live_campaign.id,
            site_id=site.id,
            phone=data.phone,
        )
        return DonorCheckPhoneOut(exists=exists)

    def anonymize_donor(
        self,
        user: User,
        donor_id: uuid.UUID,
    ) -> Donor:
        """Anonymize a donor record immediately (right to erasure / individual withdrawal).

        Per:
        - BR-PRIV-09 ★: Admin operation (MFA + step-up reauth required at policy layer).
        - Changes first_name to 'محذوف', clears last_name and phone, sets consent_hero_name=False,
          consent_photo=False, consent_public_phone=False.
        - Sets anonymized_at to current timestamp.
        - Cascades deletion of all individual donor photos into file_deletion_queue in same transaction
          (consistent with BR-CONSENT-07 and T-FILE-01-a).
        - Emits donor.anonymize audit event (zero PII).
        """
        if user.role != "admin":
            raise ForbiddenError("Only administrators can anonymize donor records.")

        donor = self.donor_repo.get_by_id(donor_id)
        if donor is None:
            raise NotFoundError(f"Donor with ID '{donor_id}' not found.")

        if donor.anonymized_at is not None:
            raise CampaignPurgedError("Donor record has already been anonymized.")

        # Cascade deletion of all individual photos into file_deletion_queue in same transaction
        photos = self.donor_repo.get_photos_by_donor(donor.id)
        for photo in photos:
            self.donor_repo.delete_photo_and_enqueue(photo)

        donor.first_name = "محذوف"
        donor.last_name = None
        donor.phone = None
        donor.blood_type = None
        donor.consent_keep_phone = False
        donor.consent_hero_name = False
        donor.consent_photo = False
        donor.consent_public_phone = False
        donor.anonymized_at = self.clock.now()

        self.donor_repo.update(donor)

        if self.audit_service:
            self.audit_service.log_event(
                action="donor.anonymize",
                entity_type="donor",
                entity_id=donor_id,
                actor_user_id=user.id,
                details={
                    "site_id": str(donor.site_id),
                    "campaign_id": str(donor.campaign_id),
                },
            )

        return donor

    def delete_phone_on_request(
        self,
        user: User,
        donor_id: uuid.UUID,
    ) -> Donor:
        """Delete/clear a donor's phone number upon owner request.

        Per:
        - BR-PRIV-10: Immediate hard deletion on request, audited as data.deleted_on_request.
        - BR-CONSENT-02 & BR-CONSENT-06: Clearing phone requires revoking public_phone and keep_phone consents.
        - BR-DONOR-15: Anonymized records cannot be modified (campaign_purged).
        """
        if user.role != "admin":
            raise ForbiddenError("Only administrators can delete donor data on request.")

        donor = self.donor_repo.get_by_id(donor_id)
        if donor is None:
            raise NotFoundError(f"Donor with ID '{donor_id}' not found.")

        if donor.anonymized_at is not None:
            raise CampaignPurgedError("Donor record has been anonymized and cannot be modified.")

        donor.phone = None
        donor.consent_keep_phone = False
        donor.consent_public_phone = False

        self.donor_repo.update(donor)

        if self.audit_service:
            self.audit_service.log_event(
                action="data.deleted_on_request",
                entity_type="donor",
                entity_id=donor.id,
                actor_user_id=user.id,
                details={
                    "type": "phone",
                    "donor_id": str(donor.id),
                    "site_id": str(donor.site_id),
                    "campaign_id": str(donor.campaign_id),
                },
            )

        return donor

    def relocate_donor(
        self,
        user: User,
        donor_id: uuid.UUID,
        data: DonorRelocateIn,
    ) -> Donor:
        """Relocate a donor to another participating site in the same campaign.

        Per:
        - docs/04-api.md §17 (POST /admin/donors/{id}/relocate)
        - BR-DONOR-12 ★: Admin operation with verified MFA and recent re-authentication (Condition R).
          Target site must participate in the same campaign (otherwise 409 site_not_in_campaign).
          Atomically moves all individual donor photos to the new site (BR-PHOTO-11).
        - BR-DONOR-15: Anonymized records cannot be modified or relocated (409 campaign_purged).
        - BR-AUDIT-01 ★ & BR-AUDIT-05: Emits donor.relocate audit event (zero PII).
        """
        if user.role != "admin":
            raise ForbiddenError("Only administrators can relocate donor records.")

        donor = self.donor_repo.get_by_id(donor_id)
        if donor is None:
            raise NotFoundError(f"Donor with ID '{donor_id}' not found.")

        if donor.anonymized_at is not None:
            raise CampaignPurgedError("Donor record has been anonymized and cannot be modified.")

        new_site_id = data.site_id
        old_site_id = donor.site_id

        # Verify target site is participating in the same campaign (BR-DONOR-12 ★, BR-CSITE-06)
        campaign_site = self.campaign_repo.get_campaign_site(donor.campaign_id, new_site_id)
        if campaign_site is None:
            raise SiteNotInCampaignError("Target site is not participating in this campaign.")

        if old_site_id == new_site_id:
            return donor

        donor.site_id = new_site_id
        self.donor_repo.update(donor)

        # Atomically relocate all individual photos to the new site in the same transaction (BR-DONOR-12, BR-PHOTO-11)
        relocated_photos = self.donor_repo.relocate_donor_photos(donor.id, new_site_id)

        if self.audit_service:
            self.audit_service.log_event(
                action="donor.relocate",
                entity_type="donor",
                entity_id=donor.id,
                actor_user_id=user.id,
                details={
                    "old_site_id": str(old_site_id),
                    "new_site_id": str(new_site_id),
                    "campaign_id": str(donor.campaign_id),
                    "photos_relocated": len(relocated_photos),
                },
            )

        return donor
