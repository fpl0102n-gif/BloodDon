"""Supervisor API endpoints for dashboard context, live counters, and donor management.

Per:
- docs/04-api.md §11 (Supervisor Donors API)
- docs/05-architecture.md §7 (Routers: validation, require(perm), single service call, response modeling)
- docs/06-business-rules.md §6 (BR-DONOR-01..18)
- docs/06-business-rules.md §7 (BR-CONSENT-01..11)
- docs/06-business-rules.md §9.3 (BR-COUNT-07)
- docs/07-test-cases.md §5.5 (T-DONOR-09, T-DONOR-14, T-DONOR-17)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import get_donor_service, require
from app.core.errors import ForbiddenError
from app.policy.permissions import Permission
from app.schemas.donor import (
    DonorCheckPhoneIn,
    DonorCheckPhoneOut,
    DonorCreateIn,
    DonorDetailOut,
    DonorListResponse,
    DonorStatusUpdateIn,
    DonorSummaryOut,
    DonorUpdateIn,
    SupervisorContextOut,
)
from app.services.donors import DonorService

router = APIRouter(
    prefix="/supervisor",
    tags=["Supervisor Field Flow"],
)


@router.get(
    "/context",
    response_model=SupervisorContextOut,
    summary="Initialize supervisor dashboard with live campaign, assigned site, and real-time counts",
)
def get_supervisor_context(
    current_user: Annotated[Any, Depends(require(Permission.DONOR_READ))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> SupervisorContextOut:
    """Retrieve supervisor operational context (live campaign, site details, and attendance counters)."""
    return donor_service.get_supervisor_context(current_user)


@router.get(
    "/donors/summary",
    response_model=DonorSummaryOut,
    summary="Retrieve real-time attendance and donation counters for supervisor's assigned site",
)
def get_supervisor_donors_summary(
    current_user: Annotated[Any, Depends(require(Permission.DONOR_READ))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorSummaryOut:
    """Retrieve real-time attendance and confirmed donation counts for assigned site in live campaign."""
    return donor_service.get_supervisor_summary(current_user)


@router.post(
    "/donors/check-phone",
    response_model=DonorCheckPhoneOut,
    summary="Check if a phone number is already registered at the supervisor's site (advisory duplicate check)",
)
def check_supervisor_donor_phone(
    payload: DonorCheckPhoneIn,
    current_user: Annotated[Any, Depends(require(Permission.DONOR_READ))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorCheckPhoneOut:
    """Check if a phone number already exists at this site during the active live campaign (BR-DONOR-13).

    In the request body so phone numbers do NOT leak in URLs or HTTP logs (T-DONOR-13-d).
    """
    return donor_service.check_phone(current_user, payload)


@router.post(
    "/donors",
    response_model=DonorDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a new donor check-in at supervisor site during live campaign (idempotent)",
)
def create_supervisor_donor(
    payload: DonorCreateIn,
    response: Response,
    current_user: Annotated[Any, Depends(require(Permission.DONOR_CREATE))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorDetailOut:
    """Record a new donor check-in at supervisor site.

    Per:
    - BR-DONOR-01..05 & docs/04-api.md §11.5 / §11.6:
      - Derives campaign_id, site_id, and registered_by from caller session and active live campaign.
      - If payload.id already exists with identical data: returns 200 OK with the existing record (safe replay).
      - If payload.id already exists with conflicting data or foreign site: raises 409 duplicate_id_conflict.
      - New record returns 201 Created.
    """
    donor, created = donor_service.create_donor(current_user, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
    return DonorDetailOut.model_validate(donor, from_attributes=True)


@router.get(
    "/donors",
    response_model=DonorListResponse,
    summary="List donors at supervisor site during live campaign (without phone numbers)",
)
def list_supervisor_donors(
    current_user: Annotated[Any, Depends(require(Permission.DONOR_READ))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
    q: Annotated[
        str | None,
        Query(
            description="Prefix search by donor first name or last name (case-insensitive)",
            max_length=100,
        ),
    ] = None,
    status: Annotated[
        str | None,
        Query(
            pattern="^(attended|donated)$",
            description="Filter by donor attendance status ('attended' or 'donated')",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Maximum donors to return per page (hard cap 100 per BR-GEN-05)",
        ),
    ] = 25,
    offset: Annotated[
        int,
        Query(
            ge=0,
            description="Number of donors to skip for pagination",
        ),
    ] = 0,
) -> DonorListResponse:
    """List donors registered at supervisor's site.

    Per BR-DONOR-14 ★: Phone numbers are strictly omitted from list representations.
    """
    return donor_service.list_supervisor_donors(
        current_user,
        status=status,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/donors/{donor_id}",
    response_model=DonorDetailOut,
    summary="Retrieve complete donor details including phone for assigned site supervisor",
)
def get_supervisor_donor(
    donor_id: uuid.UUID,
    current_user: Annotated[Any, Depends(require(Permission.DONOR_READ))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorDetailOut:
    """Retrieve complete donor record including contact phone for the assigned site supervisor.

    Per BR-DONOR-09 ★: Donors from other sites or historical campaigns return 404 not_found.
    """
    donor = donor_service.get_supervisor_donor(current_user, donor_id)
    return DonorDetailOut.model_validate(donor, from_attributes=True)


@router.patch(
    "/donors/{donor_id}",
    response_model=DonorDetailOut,
    summary="Update demographic information and consent flags for a donor (evaluates final state)",
)
def update_supervisor_donor(
    donor_id: uuid.UUID,
    payload: DonorUpdateIn,
    current_user: Annotated[Any, Depends(require(Permission.DONOR_UPDATE))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorDetailOut:
    """Update donor demographic details and consent flags.

    Per:
    - docs/04-api.md §11.8: Demographic and consent updates.
    - BR-GEN-07 & BR-DONOR-10 ★: Reserved fields rejected as 422 unknown_field.
    - BR-DONOR-09 ★: Supervisor restricted to assigned site and live campaign (404 on foreign).
    - BR-DONOR-15: Anonymized records immutable (409 campaign_purged).
    - BR-DONOR-16: Update allowed during live campaign only for supervisors.
    - BR-CONSENT-02 & BR-CONSENT-03: Final-state evaluation of phone and public consents.
    - BR-CONSENT-04: Enforce consent consistency on complete final state.
    - BR-CONSENT-06 ★: Withdrawing keep consent requires explicit phone: null if public is false.
    - BR-CONSENT-07 ★: Withdrawing photo consent immediately purges individual donor photos.
    - BR-DONOR-18 & BR-AUDIT-01 ★: Audit event donor.update logged with updated_fields (zero PII).
    """
    if current_user.role != "supervisor" or not current_user.site_id:
        raise ForbiddenError("This operation is only available to assigned site supervisors.")
    donor = donor_service.update_donor(current_user, donor_id, payload)
    return DonorDetailOut.model_validate(donor, from_attributes=True)


@router.put(
    "/donors/{donor_id}/status",
    response_model=DonorDetailOut,
    summary="Update donor attendance status between attended and donated (idempotent)",
)
def update_supervisor_donor_status(
    donor_id: uuid.UUID,
    payload: DonorStatusUpdateIn,
    current_user: Annotated[Any, Depends(require(Permission.DONOR_UPDATE))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorDetailOut:
    """Toggle donor status between 'attended' and 'donated'.

    Per:
    - docs/04-api.md §11.9: Status update (PUT /supervisor/donors/{id}/status).
    - BR-DONOR-06: Status transitions allowed in both directions (attended ⇄ donated).
    - BR-DONOR-07: donated_at populated on donated, cleared on attended.
    - BR-DONOR-08 ★: No 'rejected' or 'deferred' status (rejected via schema).
    - BR-DONOR-09 ★: Foreign site access yields 404, never 403.
    - BR-DONOR-15: Anonymized records cannot be modified (409 campaign_purged).
    - BR-CAMP-16 ★: Supervisors can only change status during a live campaign.
    - BR-DONOR-18 & BR-AUDIT-01 ★: Logs donor.status_change audit event (zero PII).
    """
    if current_user.role != "supervisor" or not current_user.site_id:
        raise ForbiddenError("This operation is only available to assigned site supervisors.")
    donor = donor_service.change_donor_status(current_user, donor_id, payload)
    return DonorDetailOut.model_validate(donor, from_attributes=True)


@router.delete(
    "/donors/{donor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a donor record and stage individual photos into file deletion queue",
)
def delete_supervisor_donor(
    donor_id: uuid.UUID,
    current_user: Annotated[Any, Depends(require(Permission.DONOR_DELETE))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> Response:
    """Delete an erroneous donor check-in.

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
    if current_user.role != "supervisor" or not current_user.site_id:
        raise ForbiddenError("This operation is only available to assigned site supervisors.")
    donor_service.delete_donor(current_user, donor_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
