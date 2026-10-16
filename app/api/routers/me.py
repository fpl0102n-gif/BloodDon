"""Personal account management API router (/me).

Per docs/04-api.md §10, docs/03-auth-permissions.md §11.4, and docs/06-business-rules.md:
- GET /me: Current user profile without exposing sensitive hashes or keys (Permission ME_READ).
- POST /me/password: Authenticated password change revoking other devices (Permission ME_CHANGE_PASSWORD).
- POST /me/mfa/setup: Initiates TOTP setup, returns secret, QR URI, and backup recovery codes.
- POST /me/mfa/confirm: Confirms first code to activate TOTP on the user account.
- POST /me/mfa/disable: Supervisor-only MFA disabling requiring step-up confirmation (R). Admin blocked.

Architectural Rule (T-STRUCT-04): No direct imports of models or repositories in router layer.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import (
    get_auth_service,
    get_current_session,
    get_mfa_service,
    get_session_token,
    require,
)
from app.policy import Permission
from app.schemas.auth import (
    ChangePasswordIn,
    MeOut,
    MfaConfirmIn,
    MfaSetupOut,
)
from app.services.auth import AuthService
from app.services.mfa import MfaService

router = APIRouter(prefix="/me", tags=["me"])


@router.get(
    "",
    response_model=MeOut,
    summary="Get current user profile",
    description="Retrieve account profile details for the authenticated staff member.",
)
def get_me(
    user: Annotated[Any, Depends(require(Permission.ME_READ))],
) -> MeOut:
    """Return authenticated caller's profile details."""
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        site_id=user.site_id,
        mfa_enabled=bool(getattr(user, "totp_enabled", False)),
        must_change_password=bool(getattr(user, "must_change_password", False)),
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change password",
    description="Change password with verification of current password and invalidate all other sessions.",
)
def change_password(
    payload: ChangePasswordIn,
    user: Annotated[Any, Depends(require(Permission.ME_CHANGE_PASSWORD))],
    session_token: Annotated[str | None, Depends(get_session_token)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    """Change account password and drop other active sessions."""
    auth_service.change_password(
        user.id,
        payload.current_password,
        payload.new_password,
        current_raw_token=session_token,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/mfa/setup",
    response_model=MfaSetupOut,
    summary="Initiate MFA enrollment",
    description="Generate TOTP secret, QR provisioning URI, and backup recovery codes.",
)
def setup_mfa(
    user: Annotated[Any, Depends(require(Permission.ME_SETUP_MFA))],
    mfa_service: Annotated[MfaService, Depends(get_mfa_service)],
) -> MfaSetupOut:
    """Generate TOTP secret, provisioning URI, and recovery codes for enrollment."""
    result = mfa_service.initiate_mfa_setup(user.id)
    return MfaSetupOut(
        secret=result.secret,
        provisioning_uri=result.provisioning_uri,
        recovery_codes=result.recovery_codes,
    )


@router.post(
    "/mfa/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm MFA enrollment",
    description="Confirm enrollment using first 6-digit TOTP code, enabling MFA on account.",
)
def confirm_mfa(
    payload: MfaConfirmIn,
    user: Annotated[Any, Depends(require(Permission.ME_SETUP_MFA))],
    session: Annotated[Any, Depends(get_current_session)],
    mfa_service: Annotated[MfaService, Depends(get_mfa_service)],
) -> Response:
    """Confirm code and enable MFA."""
    mfa_service.confirm_mfa_setup(user.id, payload.code)
    session.mfa_verified = True
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/mfa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable MFA (supervisor only)",
    description="Disable two-factor authentication for supervisor account. Requires fresh re-auth (R). Admin forbidden.",
)
def disable_mfa(
    user: Annotated[Any, Depends(require(Permission.ME_SETUP_MFA, require_reauth=True))],
    mfa_service: Annotated[MfaService, Depends(get_mfa_service)],
) -> Response:
    """Disable MFA on supervisor account after step-up confirmation."""
    mfa_service.disable_mfa(actor=user, target_user=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
