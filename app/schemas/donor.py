"""Donor schemas for serialization, query filtering, and supervisor dashboard.

Per:
- docs/04-api.md §11 (Supervisor Context & Donors API)
- docs/06-business-rules.md §6 (BR-DONOR-01..18)
- docs/06-business-rules.md §7 (BR-CONSENT-01..11)
- docs/06-business-rules.md §9.3 (BR-COUNT-07)
- docs/07-test-cases.md §5.5 (T-DONOR-14, T-DONOR-17)
"""

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from app.core.errors import PhoneRequiresConsentError, PublicPhoneRequiresDataError
from app.schemas.common import (
    AppBaseModel,
    BloodType,
    NormalizedName,
    PaginatedResponse,
    PhoneNumber,
)

# ==============================================================================
# 1. Summary & Dashboard Context Schemas
# ==============================================================================


class DonorSummaryOut(AppBaseModel):
    """Real-time attendance and donation counters for site supervisor (BR-COUNT-07)."""

    present: int = Field(ge=0, description="Total present donors checked in at this site")
    donated: int = Field(ge=0, description="Total confirmed blood donations at this site")


class SupervisorContextCampaignOut(AppBaseModel):
    """Active live campaign descriptor for supervisor dashboard initialization."""

    id: uuid.UUID
    title: str
    status: str


class SupervisorContextSiteOut(AppBaseModel):
    """Assigned donation site descriptor for supervisor dashboard."""

    id: uuid.UUID
    name: str


class SupervisorContextOut(AppBaseModel):
    """Supervisor dashboard context payload (docs/04-api.md §11.1)."""

    campaign: SupervisorContextCampaignOut
    site: SupervisorContextSiteOut
    summary: DonorSummaryOut


# ==============================================================================
# 2. Donor List & Detail Schemas
# ==============================================================================


class DonorListItemOut(AppBaseModel):
    """Single donor item in supervisor collection view (docs/04-api.md §11.2).

    CRITICAL PRIVACY RULE (BR-DONOR-14 ★, T-DONOR-14-a ★):
    Phone numbers MUST NOT be returned in list endpoints. The phone attribute
    is deliberately omitted from this model.
    """

    id: uuid.UUID
    first_name: str
    last_name: str | None = None
    blood_type: BloodType | None = None
    status: str
    created_at: datetime


class DonorListResponse(PaginatedResponse[DonorListItemOut]):
    """Paginated collection response for supervisor donor list."""


class DonorDetailOut(AppBaseModel):
    """Complete donor details including private phone for assigned site supervisor.

    Per docs/04-api.md §11.7 and BR-DONOR-14:
    Phone is exposed ONLY in single-resource detail views to authorized supervisors
    within their assigned site or system administrators.
    """

    id: uuid.UUID
    campaign_id: uuid.UUID
    site_id: uuid.UUID
    first_name: str
    last_name: str | None = None
    phone: str | None = None
    blood_type: BloodType | None = None
    status: str
    consent_keep_phone: bool
    consent_hero_name: bool
    consent_photo: bool
    consent_public_phone: bool
    registered_by: uuid.UUID | None = None
    donated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# 3. Donor Input Schemas (docs/04-api.md §11.5)
# ==============================================================================


class DonorCreateIn(AppBaseModel):
    """Payload for creating a new donor record by a supervisor (docs/04-api.md §11.5).

    Client-supplied UUID enables idempotent retries after network disconnections (BR-DONOR-05).
    """

    id: uuid.UUID | None = Field(
        default=None,
        description="Client-generated UUID for safe idempotency replay (BR-DONOR-05)",
    )
    first_name: NormalizedName = Field(
        min_length=1,
        max_length=40,
        description="Donor first name (1-40 chars, normalized per BR-GEN-02)",
    )
    last_name: NormalizedName | None = Field(
        default=None,
        max_length=40,
        description="Optional donor last name (up to 40 chars, normalized per BR-GEN-02)",
    )
    phone: PhoneNumber | None = Field(
        default=None,
        description="Optional contact phone number (8-15 digits, normalized per BR-GEN-01)",
    )
    blood_type: BloodType | None = Field(
        default=None,
        description="Optional validated blood type from the 8 human types (BR-DONOR-04)",
    )
    consent_keep_phone: bool = Field(
        default=False,
        description="Explicit consent to retain phone for future campaign outreach (BR-CONSENT-01/02)",
    )
    consent_hero_name: bool = Field(
        default=False,
        description="Explicit consent to publish first name in the public heroes roster (BR-CONSENT-01)",
    )
    consent_photo: bool = Field(
        default=False,
        description="Explicit consent to take and publish donor photographs (BR-CONSENT-01/07)",
    )
    consent_public_phone: bool = Field(
        default=False,
        description="Explicit consent to display phone publicly in emergency requests (BR-CONSENT-01/03)",
    )

    @model_validator(mode="after")
    def validate_consents(self) -> Self:
        """Enforce consent rules at schema boundary (BR-CONSENT-02, BR-CONSENT-03)."""
        # BR-CONSENT-02: Phone requires consent_keep_phone or consent_public_phone
        if self.phone is not None and not (self.consent_keep_phone or self.consent_public_phone):
            raise PhoneRequiresConsentError(
                "Phone number cannot be stored without explicit donor consent."
            )

        # BR-CONSENT-03: consent_public_phone requires phone and blood_type
        if self.consent_public_phone and not (
            self.phone is not None and self.blood_type is not None
        ):
            raise PublicPhoneRequiresDataError(
                "Public phone consent requires both a valid phone number and blood type."
            )

        return self


class DonorUpdateIn(AppBaseModel):
    """Payload for updating demographic details and consent flags of a donor (docs/04-api.md §11.8).

    Evaluated on the final state at the service layer (BR-CONSENT-04).
    Reserved fields (site_id, campaign_id, registered_by, status, donated_at, anonymized_at, role)
    are strictly rejected with 422 unknown_field (BR-GEN-06, BR-GEN-07, BR-DONOR-10).
    """

    first_name: NormalizedName | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        description="Donor first name (1-40 chars, normalized per BR-GEN-02)",
    )
    last_name: NormalizedName | None = Field(
        default=None,
        max_length=40,
        description="Optional donor last name (up to 40 chars, normalized per BR-GEN-02) or null to clear",
    )
    phone: PhoneNumber | None = Field(
        default=None,
        description="Optional contact phone number (8-15 digits, normalized per BR-GEN-01) or null to clear",
    )
    blood_type: BloodType | None = Field(
        default=None,
        description="Optional blood type from the 8 human types (BR-DONOR-04) or null to clear",
    )
    consent_keep_phone: bool | None = Field(
        default=None,
        description="Explicit consent to retain phone for future campaign outreach (BR-CONSENT-01/02)",
    )
    consent_hero_name: bool | None = Field(
        default=None,
        description="Explicit consent to publish first name in the public heroes roster (BR-CONSENT-01)",
    )
    consent_photo: bool | None = Field(
        default=None,
        description="Explicit consent to take and publish donor photographs (BR-CONSENT-01/07)",
    )
    consent_public_phone: bool | None = Field(
        default=None,
        description="Explicit consent to display phone publicly in emergency requests (BR-CONSENT-01/03)",
    )

    @field_validator(
        "first_name",
        "consent_keep_phone",
        "consent_hero_name",
        "consent_photo",
        "consent_public_phone",
        mode="after",
    )
    @classmethod
    def validate_not_explicit_null(cls, v: Any, info: ValidationInfo) -> Any:
        """Disallow explicitly setting first_name or boolean consents to null."""
        if v is None:
            raise ValueError(f"Field '{info.field_name}' cannot be null.")
        return v


class DonorStatusUpdateIn(AppBaseModel):
    """Payload for toggling donor status between attended and donated (docs/04-api.md §11.9).

    Per BR-DONOR-06..08:
    - Status must be 'attended' or 'donated'.
    - 'rejected' or 'deferred' are strictly forbidden (BR-DONOR-08 ★).
    """

    status: Literal["attended", "donated"] = Field(
        description="Updated attendance/donation status ('attended' or 'donated')"
    )


class DonorCheckPhoneIn(AppBaseModel):
    """Payload to check if a phone number is already registered at the supervisor's site (BR-DONOR-13).

    In the request body so the phone number does NOT leak in URLs or HTTP logs (T-DONOR-13-d).
    """

    phone: PhoneNumber = Field(
        description="Contact phone number to verify for soft duplicate check (BR-GEN-01)"
    )


class DonorCheckPhoneOut(AppBaseModel):
    """Advisory response indicating whether phone is already recorded at the site (BR-DONOR-13)."""

    exists: bool = Field(
        description="Whether a donor with this phone already exists in the current site and campaign"
    )


class DonorRelocateIn(AppBaseModel):
    """Payload for relocating a donor to another participating site in the same campaign (BR-DONOR-12)."""

    site_id: uuid.UUID = Field(description="Target site ID participating in the same campaign")
