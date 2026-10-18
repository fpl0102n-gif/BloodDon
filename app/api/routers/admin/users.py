"""Admin Users API router (P4.5).

Per:
- docs/04-api.md §15 (Users Administration endpoints contracts)
- docs/06-business-rules.md §11.2 (BR-USER-01..10, BR-AUTH-02, BR-AUTH-13)
- docs/03-auth-permissions.md §11.3 (Admin governance permissions & step-up R)
- docs/05-architecture.md §12 (Routers layer rules: no direct repo/model imports)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import get_user_service, require, verify_csrf
from app.policy import Permission
from app.schemas.user import (
    BulkDeactivateOut,
    UserAdminUpdateIn,
    UserInviteIn,
    UserInviteOut,
    UserOut,
)
from app.services.users import UserService

router = APIRouter(prefix="/admin/users", tags=["Admin Users"])


@router.get(
    "",
    response_model=list[UserOut],
    status_code=status.HTTP_200_OK,
    summary="List staff users",
)
def list_users(
    response: Response,
    user_service: Annotated[UserService, Depends(get_user_service)],
    _user: Annotated[Any, Depends(require(Permission.USER_INVITE))],
    role: Annotated[
        str | None,
        Query(description="Filter by role ('admin', 'supervisor')"),
    ] = None,
    site_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter by assigned site"),
    ] = None,
    is_active: Annotated[
        bool | None,
        Query(description="Filter by active status"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Page limit (max 100)"),
    ] = 100,
    offset: Annotated[
        int,
        Query(ge=0, description="Page offset"),
    ] = 0,
) -> list[UserOut]:
    """Retrieve staff users matching criteria. Warns if single active admin exists."""
    warning = user_service.get_admin_warning()
    if warning:
        response.headers["X-Admin-Warning"] = warning

    users = user_service.list_users(
        role=role,
        site_id=site_id,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return [UserOut.model_validate(u) for u in users]


@router.post(
    "",
    response_model=UserInviteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a new staff user",
    dependencies=[Depends(verify_csrf)],
)
def invite_user(
    payload: UserInviteIn,
    user_service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[Any, Depends(require(Permission.USER_INVITE, require_reauth=True))],
) -> UserInviteOut:
    """Invite a new admin or site supervisor with a 72-hour one-time token (BR-USER-01)."""
    user, fallback_url = user_service.invite_user(
        payload,
        actor_user_id=current_user.id,
    )
    return UserInviteOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        site_id=user.site_id,
        is_active=user.is_active,
        created_at=user.created_at,
        invite_url=fallback_url,
    )


@router.post(
    "/deactivate-supervisors",
    response_model=BulkDeactivateOut,
    status_code=status.HTTP_200_OK,
    summary="Bulk deactivate all supervisors",
    dependencies=[Depends(verify_csrf)],
)
def bulk_deactivate_supervisors(
    user_service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[Any, Depends(require(Permission.USER_DEACTIVATE, require_reauth=True))],
) -> BulkDeactivateOut:
    """Deactivate all supervisors in bulk after campaign completion, dropping their sessions (BR-USER-05)."""
    deactivated = user_service.bulk_deactivate_supervisors(
        actor_user_id=current_user.id,
    )
    return BulkDeactivateOut(deactivated_count=deactivated)


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    status_code=status.HTTP_200_OK,
    summary="Update staff user details",
    dependencies=[Depends(verify_csrf)],
)
def update_user(
    user_id: uuid.UUID,
    payload: UserAdminUpdateIn,
    user_service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[Any, Depends(require(Permission.USER_UPDATE, require_reauth=True))],
) -> UserOut:
    """Update user role, site, or full name, dropping active sessions if access changed (BR-USER-03, BR-AUTH-13)."""
    user = user_service.update_user(
        user_id,
        payload,
        actor_user_id=current_user.id,
    )
    return UserOut.model_validate(user)


@router.post(
    "/{user_id}/resend-invite",
    response_model=UserInviteOut,
    status_code=status.HTTP_200_OK,
    summary="Resend invitation email",
    dependencies=[Depends(verify_csrf)],
)
def resend_invite(
    user_id: uuid.UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[Any, Depends(require(Permission.USER_INVITE, require_reauth=True))],
) -> UserInviteOut:
    """Resend user invitation, invalidating previous active tokens (BR-AUTH-02, BR-USER-09)."""
    user, fallback_url = user_service.resend_invite(
        user_id,
        actor_user_id=current_user.id,
    )
    return UserInviteOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        site_id=user.site_id,
        is_active=user.is_active,
        created_at=user.created_at,
        invite_url=fallback_url,
    )


@router.post(
    "/{user_id}/deactivate",
    response_model=UserOut,
    status_code=status.HTTP_200_OK,
    summary="Deactivate user account",
    dependencies=[Depends(verify_csrf)],
)
def deactivate_user(
    user_id: uuid.UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[Any, Depends(require(Permission.USER_DEACTIVATE, require_reauth=True))],
) -> UserOut:
    """Deactivate user account, dropping sessions immediately with last-admin guard (BR-USER-03, BR-USER-04)."""
    user = user_service.deactivate_user(
        user_id,
        actor_user_id=current_user.id,
    )
    return UserOut.model_validate(user)


@router.post(
    "/{user_id}/activate",
    response_model=UserOut,
    status_code=status.HTTP_200_OK,
    summary="Reactivate user account",
    dependencies=[Depends(verify_csrf)],
)
def activate_user(
    user_id: uuid.UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[Any, Depends(require(Permission.USER_DEACTIVATE, require_reauth=True))],
) -> UserOut:
    """Reactivate a previously deactivated staff user account."""
    user = user_service.activate_user(
        user_id,
        actor_user_id=current_user.id,
    )
    return UserOut.model_validate(user)


@router.post(
    "/{user_id}/unlock",
    response_model=UserOut,
    status_code=status.HTTP_200_OK,
    summary="Unlock locked account",
    dependencies=[Depends(verify_csrf)],
)
def unlock_user(
    user_id: uuid.UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[Any, Depends(require(Permission.USER_UNLOCK, require_reauth=True))],
) -> UserOut:
    """Administratively clear login failure lockout state (BR-AUTH-06)."""
    user = user_service.unlock_user(
        user_id,
        actor_user_id=current_user.id,
    )
    return UserOut.model_validate(user)


@router.post(
    "/{user_id}/reset-mfa",
    response_model=UserOut,
    status_code=status.HTTP_200_OK,
    summary="Reset user two-factor authentication",
    dependencies=[Depends(verify_csrf)],
)
def reset_mfa(
    user_id: uuid.UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[Any, Depends(require(Permission.USER_RESET_MFA, require_reauth=True))],
) -> UserOut:
    """Reset MFA configuration, deleting recovery codes and dropping sessions (BR-AUTH-13)."""
    user = user_service.reset_mfa(
        user_id,
        actor_user_id=current_user.id,
    )
    return UserOut.model_validate(user)
