"""Public Emergency API router (P7.1 / P7.2).

Per:
- docs/04-api.md §8.1 (GET /public/emergency/donors)
- docs/06-business-rules.md §10 (BR-EMERG-01..14)
- docs/07-test-cases.md §5.9 (T-EMERG-01..14)
- docs/03-auth-permissions.md §11.1 (Permission.EMERGENCY_LIST, Permission.EMERGENCY_REVEAL)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import (
    check_emergency_list_rate_limit,
    check_emergency_reveal_rate_limit,
    get_client_ip_hash,
    get_emergency_service,
    require,
)
from app.policy import Permission
from app.schemas.emergency import BloodTypeQuery, EmergencyDonorItemOut, EmergencyRevealOut
from app.services.emergency import EmergencyService

router = APIRouter(prefix="/public/emergency", tags=["Public Emergency"])


@router.get(
    "/donors",
    response_model=list[EmergencyDonorItemOut],
    status_code=status.HTTP_200_OK,
    summary="List eligible emergency blood donors (without phone numbers)",
    dependencies=[Depends(check_emergency_list_rate_limit)],
)
def list_emergency_donors(
    response: Response,
    blood_type: Annotated[
        BloodTypeQuery,
        Query(description="Required ABO/Rh blood group (e.g. 'O+', 'A-', ...)"),
    ],
    emergency_service: Annotated[EmergencyService, Depends(get_emergency_service)],
    _user: Annotated[Any, Depends(require(Permission.EMERGENCY_LIST))],
    site_id: Annotated[
        uuid.UUID | None,
        Query(description="Optional filter by donation site ID"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=50, description="Result limit (hard cap 50 per BR-EMERG-04)"),
    ] = 50,
) -> list[EmergencyDonorItemOut]:
    """Retrieve public emergency donor board (BR-EMERG-01..04, 09, 11).

    STRICT PRIVACY GUARD:
    Per BR-EMERG-02 ★, this endpoint NEVER reveals phone numbers!
    Ordered by oldest donation first (donated_at ASC) per BR-EMERG-04.
    Cache-Control header is strictly 'no-store' per BR-EMERG-09 ★.
    """
    response.headers["Cache-Control"] = "no-store"
    return emergency_service.list_emergency_donors(
        blood_type=blood_type.value,
        site_id=site_id,
        limit=limit,
    )


@router.get(
    "",
    response_model=list[EmergencyDonorItemOut],
    status_code=status.HTTP_200_OK,
    summary="Alias for listing eligible emergency blood donors",
    include_in_schema=False,
    dependencies=[Depends(check_emergency_list_rate_limit)],
)
def list_emergency_donors_alias(
    response: Response,
    blood_type: Annotated[
        BloodTypeQuery,
        Query(description="Required ABO/Rh blood group (e.g. 'O+', 'A-', ...)"),
    ],
    emergency_service: Annotated[EmergencyService, Depends(get_emergency_service)],
    _user: Annotated[Any, Depends(require(Permission.EMERGENCY_LIST))],
    site_id: Annotated[
        uuid.UUID | None,
        Query(description="Optional filter by donation site ID"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=50, description="Result limit (hard cap 50 per BR-EMERG-04)"),
    ] = 50,
) -> list[EmergencyDonorItemOut]:
    """Alias for /public/emergency/donors supporting root /public/emergency queries."""
    response.headers["Cache-Control"] = "no-store"
    return emergency_service.list_emergency_donors(
        blood_type=blood_type.value,
        site_id=site_id,
        limit=limit,
    )


@router.post(
    "/donors/{donor_id}/reveal",
    response_model=EmergencyRevealOut,
    status_code=status.HTTP_200_OK,
    summary="Reveal phone number of an eligible emergency volunteer",
    dependencies=[Depends(check_emergency_reveal_rate_limit)],
)
def reveal_emergency_donor(
    donor_id: uuid.UUID,
    request: Request,
    response: Response,
    emergency_service: Annotated[EmergencyService, Depends(get_emergency_service)],
    _user: Annotated[Any, Depends(require(Permission.EMERGENCY_REVEAL))],
) -> EmergencyRevealOut:
    """Reveal volunteer phone number for emergency contact (BR-EMERG-05..08).

    STRICT SECURITY CONTROLS:
    - Rate limited to 10 reveals per hour per genuine IP hash (BR-RATE-05, BR-EMERG-07 ★).
    - Ineligible/unconsented/nonexistent donors yield an IDENTICAL 404 (BR-EMERG-06 ★).
    - Every reveal is logged to phone_reveal_log and audit_log (BR-EMERG-08).
    - Cache-Control header is strictly 'no-store' (BR-EMERG-09 ★).
    """
    response.headers["Cache-Control"] = "no-store"
    client_ip_hash = get_client_ip_hash(request)
    return emergency_service.reveal_donor_phone(donor_id=donor_id, ip_hash=client_ip_hash)


@router.post(
    "/{donor_id}/reveal",
    response_model=EmergencyRevealOut,
    status_code=status.HTTP_200_OK,
    summary="Alias for revealing emergency volunteer phone number",
    include_in_schema=False,
    dependencies=[Depends(check_emergency_reveal_rate_limit)],
)
def reveal_emergency_donor_alias(
    donor_id: uuid.UUID,
    request: Request,
    response: Response,
    emergency_service: Annotated[EmergencyService, Depends(get_emergency_service)],
    _user: Annotated[Any, Depends(require(Permission.EMERGENCY_REVEAL))],
) -> EmergencyRevealOut:
    """Alias supporting /public/emergency/{donor_id}/reveal path variation."""
    response.headers["Cache-Control"] = "no-store"
    client_ip_hash = get_client_ip_hash(request)
    return emergency_service.reveal_donor_phone(donor_id=donor_id, ip_hash=client_ip_hash)
