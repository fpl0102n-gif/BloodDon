"""Photo domain service for field supervisor and administrative photo management.

Per:
- docs/05-architecture.md §12 (Services layer: business logic, policy checks, zero commit)
- docs/06-business-rules.md §8 (BR-PHOTO-01..14)
- docs/04-api.md §12 (Supervisor photos) & §18 (Admin photos)
- docs/07-test-cases.md §5.7 (T-PHOTO-03..12)
"""

import uuid
from collections.abc import Sequence

from app.core.config import Settings, get_settings
from app.core.errors import (
    CampaignPurgedError,
    ForbiddenError,
    NoLiveCampaignError,
    NotFoundError,
    PhotoConsentMissingError,
    ValidationError,
)
from app.models.campaign import Campaign, Site
from app.models.photo import Photo
from app.models.user import User
from app.repositories.campaigns import CampaignRepository
from app.repositories.donors import DonorRepository
from app.repositories.photos import PhotoRepository
from app.repositories.sites import SiteRepository
from app.schemas.photo import PhotoOut, PhotoPublicOut
from app.services.audit import AuditService
from app.storage.base import Storage
from app.storage.factory import create_storage
from app.storage.images import process_uploaded_image


class PhotoService:
    """Domain service managing supervisor and administrative photo workflows."""

    def __init__(
        self,
        photo_repo: PhotoRepository,
        campaign_repo: CampaignRepository,
        site_repo: SiteRepository,
        donor_repo: DonorRepository,
        storage: Storage | None = None,
        audit_service: AuditService | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.photo_repo = photo_repo
        self.campaign_repo = campaign_repo
        self.site_repo = site_repo
        self.donor_repo = donor_repo
        self.settings = settings or get_settings()
        self.storage = storage or create_storage(self.settings)
        self.audit_service = audit_service

    def _ensure_supervisor_live_context(self, user: User) -> tuple[Campaign, Site]:
        """Derive and validate that the supervisor has an assigned site and a live campaign.

        Per BR-CAMP-16 ★, BR-DONOR-02 ★, and docs/04-api.md §11-§12:
        - Supervisor must have an assigned site_id.
        - Current campaign must exist and have status 'live'.
        - Supervisor's site must actively participate in the live campaign.

        Raises:
            ForbiddenError: If user is not an assigned supervisor.
            NoLiveCampaignError (409): If no campaign is currently live.
            NotFoundError (404): If the assigned site does not participate in the live campaign.
        """
        if user.role != "supervisor" or not user.site_id:
            raise ForbiddenError("This operation is only available to assigned site supervisors.")

        campaign = self.campaign_repo.get_live_campaign()
        if not campaign or campaign.status != "live":
            raise NoLiveCampaignError("No active campaign is currently live.")

        site = self.site_repo.get_by_id(user.site_id)
        if not site or not site.is_active:
            raise NotFoundError(f"Assigned site '{user.site_id}' not found or inactive.")

        participation = self.campaign_repo.get_campaign_site(campaign.id, site.id)
        if not participation:
            raise NotFoundError("Your assigned site is not participating in the active campaign.")

        return campaign, site

    def validate_caption(self, caption: str | None) -> str | None:
        """Validate optional photo caption per BR-PHOTO-12.

        - Max length 200 characters (T-PHOTO-12-a).
        - No HTML characters: '<' and '>' are explicitly rejected with 422 validation_error (T-PHOTO-12-b).
        """
        if caption is None:
            return None

        cleaned = caption.strip()
        if not cleaned:
            return None

        if len(cleaned) > 200:
            raise ValidationError("Caption must not exceed 200 characters.")

        if "<" in cleaned or ">" in cleaned:
            raise ValidationError("Caption must not contain HTML tags or '<' / '>' characters.")

        return cleaned

    def upload_supervisor_photo(
        self,
        user: User,
        raw_bytes: bytes,
        kind: str,
        *,
        donor_id: uuid.UUID | None = None,
        caption: str | None = None,
    ) -> Photo:
        """Upload and process an event or donor photo from a field supervisor.

        Conforms to:
        - BR-PHOTO-01 & BR-PHOTO-02: kind must be 'general' or 'individual'.
          'individual' requires donor_id; 'general' requires donor_id is None.
        - BR-PHOTO-03 (★): Individual photo is accepted ONLY if donor belongs to the same
          site and live campaign AND has consent_photo = True. Missing consent -> 422 photo_consent_missing.
        - BR-PHOTO-04: Supervisor can only upload for their assigned site during a live campaign.
        - BR-PHOTO-05 (★): Verifies actual content format (JPEG, PNG, WebP only). Disguised text -> 422.
        - BR-PHOTO-06: Size <= 5MB, decompression bomb defense.
        - BR-PHOTO-07 (★): Re-encodes, strips all EXIF (including GPS), resizes longest side <= 1600px.
        - BR-PHOTO-08: Server-generated UUID storage key, client filename completely discarded.
        - BR-PHOTO-09: Failure to persist DB record cleans up stored file immediately (no orphans).
        - BR-DONOR-09 (★): Donor from foreign site returns 404 not_found, never leaking existence.
        - BR-DONOR-15: Anonymized donor returns 409 campaign_purged.
        """
        # 1. Enforce supervisor live context
        live_campaign, site = self._ensure_supervisor_live_context(user)

        # 2. Validate kind and donor association (BR-PHOTO-01, BR-PHOTO-02)
        if kind not in ("general", "individual"):
            raise ValidationError("Photo kind must be either 'general' or 'individual'.")

        if kind == "general" and donor_id is not None:
            raise ValidationError("General atmosphere photos must not specify a donor_id.")

        if kind == "individual":
            if donor_id is None:
                raise ValidationError("Individual photos require a valid donor_id.")

            donor = self.donor_repo.get_by_id(donor_id)
            if donor is None or donor.site_id != site.id or donor.campaign_id != live_campaign.id:
                # BR-DONOR-09 ★: Foreign-site or nonexistent donor returns 404 not_found
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")

            if donor.anonymized_at is not None:
                raise CampaignPurgedError(
                    "Donor record has been anonymized and cannot be modified."
                )

            if not donor.consent_photo:
                # BR-PHOTO-03 ★: Photo consent gate
                raise PhotoConsentMissingError(
                    "Individual photo upload requires explicit donor photo consent."
                )

        # 3. Validate caption (BR-PHOTO-12)
        clean_caption = self.validate_caption(caption)

        # 4. Process image (BR-PHOTO-05, BR-PHOTO-06, BR-PHOTO-07)
        processed = process_uploaded_image(raw_bytes, settings=self.settings)

        # 5. Generate server-side UUID storage key (BR-PHOTO-08)
        storage_key = f"photos/{live_campaign.id}/{site.id}/{uuid.uuid4()}{processed.extension}"

        # 6. Save processed image bytes to storage
        self.storage.save(storage_key, processed.content, content_type=processed.content_type)

        # 7. Persist Photo record to database with transactional orphan cleanup (BR-PHOTO-09)
        try:
            photo = Photo(
                campaign_id=live_campaign.id,
                site_id=site.id,
                storage_key=storage_key,
                kind=kind,
                caption=clean_caption,
                donor_id=donor_id if kind == "individual" else None,
                mime_type=processed.content_type,
                size_bytes=len(processed.content),
                uploaded_by=user.id,
            )
            self.photo_repo.create(photo)
        except Exception:
            self.storage.delete(storage_key)
            raise

        # 8. Record audit log event with zero PII (BR-AUDIT-01/05, BR-PHOTO-09)
        try:
            if self.audit_service:
                self.audit_service.log_event(
                    action="photo.upload",
                    entity_type="photo",
                    entity_id=photo.id,
                    actor_user_id=user.id,
                    details={
                        "kind": kind,
                        "site_id": str(site.id),
                        "campaign_id": str(live_campaign.id),
                    },
                )
        except Exception:
            self.storage.delete(storage_key)
            raise

        return photo

    def upload_admin_photo(
        self,
        user: User,
        raw_bytes: bytes,
        campaign_id: uuid.UUID,
        site_id: uuid.UUID,
        kind: str,
        *,
        donor_id: uuid.UUID | None = None,
        caption: str | None = None,
    ) -> Photo:
        """Upload and process a photo as an administrator for any participating site.

        Per BR-PHOTO-04: Admin can upload for any participating site in a campaign.
        """
        if user.role != "admin":
            raise ForbiddenError("Only administrators can perform administrative photo uploads.")

        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise NotFoundError(f"Campaign with ID '{campaign_id}' not found.")

        participation = self.campaign_repo.get_campaign_site(campaign_id, site_id)
        if not participation:
            raise NotFoundError(
                f"Site '{site_id}' is not participating in campaign '{campaign_id}'."
            )

        if kind not in ("general", "individual"):
            raise ValidationError("Photo kind must be either 'general' or 'individual'.")

        if kind == "general" and donor_id is not None:
            raise ValidationError("General atmosphere photos must not specify a donor_id.")

        if kind == "individual":
            if donor_id is None:
                raise ValidationError("Individual photos require a valid donor_id.")

            donor = self.donor_repo.get_by_id(donor_id)
            if donor is None or donor.site_id != site_id or donor.campaign_id != campaign_id:
                raise NotFoundError(f"Donor with ID '{donor_id}' not found.")

            if donor.anonymized_at is not None:
                raise CampaignPurgedError(
                    "Donor record has been anonymized and cannot be modified."
                )

            if not donor.consent_photo:
                raise PhotoConsentMissingError(
                    "Individual photo upload requires explicit donor photo consent."
                )

        clean_caption = self.validate_caption(caption)
        processed = process_uploaded_image(raw_bytes, settings=self.settings)

        storage_key = f"photos/{campaign_id}/{site_id}/{uuid.uuid4()}{processed.extension}"
        self.storage.save(storage_key, processed.content, content_type=processed.content_type)

        try:
            photo = Photo(
                campaign_id=campaign_id,
                site_id=site_id,
                storage_key=storage_key,
                kind=kind,
                caption=clean_caption,
                donor_id=donor_id if kind == "individual" else None,
                mime_type=processed.content_type,
                size_bytes=len(processed.content),
                uploaded_by=user.id,
            )
            self.photo_repo.create(photo)
        except Exception:
            self.storage.delete(storage_key)
            raise

        try:
            if self.audit_service:
                self.audit_service.log_event(
                    action="photo.upload",
                    entity_type="photo",
                    entity_id=photo.id,
                    actor_user_id=user.id,
                    details={
                        "kind": kind,
                        "site_id": str(site_id),
                        "campaign_id": str(campaign_id),
                    },
                )
        except Exception:
            self.storage.delete(storage_key)
            raise

        return photo

    def list_supervisor_photos(
        self,
        user: User,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[Photo], int]:
        """List photos for the supervisor's assigned site in the live campaign."""
        live_campaign, site = self._ensure_supervisor_live_context(user)
        return self.photo_repo.list_by_site(
            live_campaign.id,
            site.id,
            limit=limit,
            offset=offset,
        )

    def delete_supervisor_photo(
        self,
        user: User,
        photo_id: uuid.UUID,
    ) -> None:
        """Delete a photo belonging to the supervisor's site during a live campaign.

        Per:
        - docs/04-api.md §12.3: DELETE /supervisor/photos/{id} -> 204 No Content.
        - BR-PHOTO-10: Record is deleted and storage_key staged into file_deletion_queue.
          Supervisors may only delete photos from their own site while the campaign is live.
        - BR-DONOR-09 ★: Foreign-site photos yield 404 not_found, never 403.
        - BR-CAMP-16 ★: Supervisors can only modify/delete while campaign is live (409 no_live_campaign).
        - BR-AUDIT-01 ★: Logs photo.delete audit event with zero PII.
        """
        live_campaign, site = self._ensure_supervisor_live_context(user)

        photo = self.photo_repo.get_by_id(photo_id)
        if photo is None:
            raise NotFoundError(f"Photo with ID '{photo_id}' not found.")

        # Site isolation: other-site photo returns 404 indistinguishable from nonexistent
        if photo.site_id != site.id:
            raise NotFoundError(f"Photo with ID '{photo_id}' not found.")

        # Campaign isolation: photo must belong to the active live campaign
        if photo.campaign_id != live_campaign.id:
            raise NoLiveCampaignError("Photos cannot be modified outside an active live campaign.")

        site_id_str = str(photo.site_id)
        campaign_id_str = str(photo.campaign_id)
        storage_key = photo.storage_key

        self.photo_repo.delete_photo_and_enqueue(photo)

        if self.audit_service:
            self.audit_service.log_event(
                action="photo.delete",
                entity_type="photo",
                entity_id=photo_id,
                actor_user_id=user.id,
                details={
                    "site_id": site_id_str,
                    "campaign_id": campaign_id_str,
                    "storage_key": storage_key,
                },
            )

    def delete_admin_photo(
        self,
        user: User,
        photo_id: uuid.UUID,
    ) -> None:
        """Delete any photo entity across sites and campaigns as administrator.

        Per:
        - docs/04-api.md §18: DELETE /admin/photos/{id} -> 204 No Content.
        - BR-PHOTO-10: Administrators can delete any photo, staging storage_key into queue.
        - BR-AUDIT-01 ★: Logs photo.delete audit event with zero PII.
        """
        photo = self.photo_repo.get_by_id(photo_id)
        if photo is None:
            raise NotFoundError(f"Photo with ID '{photo_id}' not found.")

        site_id_str = str(photo.site_id)
        campaign_id_str = str(photo.campaign_id)
        storage_key = photo.storage_key

        self.photo_repo.delete_photo_and_enqueue(photo)

        if self.audit_service:
            self.audit_service.log_event(
                action="photo.delete",
                entity_type="photo",
                entity_id=photo_id,
                actor_user_id=user.id,
                details={
                    "site_id": site_id_str,
                    "campaign_id": campaign_id_str,
                    "storage_key": storage_key,
                },
            )

    def get_photo_url(self, photo: Photo) -> str:
        """Resolve public media URL for a stored photo entity."""
        return self.storage.get_url(photo.storage_key)

    def to_photo_out(self, photo: Photo) -> PhotoOut:
        """Convert a Photo ORM instance to a PhotoOut schema with resolved media URL."""
        return PhotoOut(
            id=photo.id,
            kind=photo.kind,
            caption=photo.caption,
            url=self.get_photo_url(photo),
            donor_id=photo.donor_id,
            created_at=photo.created_at,
        )

    def list_public_photos(
        self,
        slug: str,
        *,
        site_id: uuid.UUID | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[Photo], int]:
        """List photos for a published campaign in the public gallery (BR-CAMP-03, BR-PHOTO-11)."""
        campaign = self.campaign_repo.get_by_slug(slug)
        if campaign is None or not campaign.is_published:
            raise NotFoundError(f"Campaign with slug '{slug}' was not found")

        capped_limit = min(limit, 100)
        return self.photo_repo.list_public_by_campaign(
            campaign.id,
            site_id=site_id,
            limit=capped_limit,
            offset=offset,
        )

    def to_photo_public_out(self, photo: Photo) -> PhotoPublicOut:
        """Convert a Photo ORM instance to PhotoPublicOut with resolved media URL (docs/04-api.md §6.8)."""
        return PhotoPublicOut(
            id=photo.id,
            url=self.get_photo_url(photo),
            caption=photo.caption,
            site_id=photo.site_id,
            kind=photo.kind,
        )
