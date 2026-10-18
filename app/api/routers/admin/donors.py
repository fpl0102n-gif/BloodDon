"""Admin API endpoints for donor management.

Per:
- docs/04-api.md §17 (Admin Donors API)
- docs/06-business-rules.md §6 (BR-DONOR-16)
- docs/07-test-cases.md §4 (Matrix Table Row 127: PATCH /admin/donors/{id})
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import get_donor_service, require
from app.core.errors import ForbiddenError
from app.policy.permissions import Permission
from app.schemas.donor import DonorDetailOut, DonorRelocateIn, DonorUpdateIn
from app.services.donors import DonorService

router = APIRouter(prefix="/admin/donors", tags=["admin-donors"])


@router.patch(
    "/{donor_id}",
    response_model=DonorDetailOut,
    summary="Update demographic information and consent flags for a donor as administrator",
)
def update_admin_donor(
    donor_id: uuid.UUID,
    payload: DonorUpdateIn,
    current_user: Annotated[Any, Depends(require(Permission.DONOR_UPDATE))],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorDetailOut:
    """Update donor demographic details and consent flags across any campaign or site as admin.

    Per docs/04-api.md §17 and BR-DONOR-16: Admin updates permitted on ended campaigns.
    """
    if current_user.role != "admin":
        raise ForbiddenError("This operation is restricted to system administrators.")
    donor = donor_service.update_donor(current_user, donor_id, payload)
    return DonorDetailOut.model_validate(donor, from_attributes=True)


@router.post(
    "/{donor_id}/anonymize",
    response_model=DonorDetailOut,
    summary="Immediately anonymize a single donor record on owner request",
)
def anonymize_admin_donor(
    donor_id: uuid.UUID,
    current_user: Annotated[
        Any,
        Depends(require(Permission.DATA_DELETE_ON_REQUEST, require_reauth=True)),
    ],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorDetailOut:
    """Anonymize a single donor record immediately (right to erasure / individual withdrawal).

    Per:
    - docs/04-api.md §17 (POST /admin/donors/{id}/anonymize)
    - BR-PRIV-09 ★: Admin operation with verified MFA and recent re-authentication (Condition R).
    - Clears PII (last_name, phone, blood_type to NULL; first_name to 'محذوف'), revokes all consents.
    - Cascades deletion of all individual photos into file_deletion_queue in same transaction.
    - Emits donor.anonymize audit event (zero PII).
    """
    if current_user.role != "admin":
        raise ForbiddenError("This operation is restricted to system administrators.")
    donor = donor_service.anonymize_donor(current_user, donor_id)
    return DonorDetailOut.model_validate(donor, from_attributes=True)


@router.post(
    "/{donor_id}/relocate",
    response_model=DonorDetailOut,
    summary="Relocate a donor between participating sites in the same campaign",
)
def relocate_admin_donor(
    donor_id: uuid.UUID,
    payload: DonorRelocateIn,
    current_user: Annotated[
        Any,
        Depends(require(Permission.DONOR_RELOCATE, require_reauth=True)),
    ],
    donor_service: Annotated[DonorService, Depends(get_donor_service)],
) -> DonorDetailOut:
    """Relocate a donor and atomically transfer all individual photos to the new site.

    Per:
    - docs/04-api.md §17 (POST /admin/donors/{id}/relocate)
    - BR-DONOR-12 ★: Admin operation with verified MFA and recent re-authentication (Condition R).
    - BR-PHOTO-11: Moves all individual photos of the donor atomically in the same transaction.
    - BR-DONOR-15: Anonymized records cannot be relocated (409 campaign_purged).
    - Emits donor.relocate audit event (zero PII).
    """
    if current_user.role != "admin":
        raise ForbiddenError("This operation is restricted to system administrators.")
    donor = donor_service.relocate_donor(current_user, donor_id, payload)
    return DonorDetailOut.model_validate(donor, from_attributes=True)
