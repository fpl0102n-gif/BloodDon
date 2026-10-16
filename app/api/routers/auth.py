"""Authentication API router implementing login, MFA verification, step-up reauth, and tokens.

Per docs/04-api.md §9, docs/03-auth-permissions.md §5-§9, and docs/06-business-rules.md:
- POST /auth/login: Email and password authentication with uniform timing and progressive lockout.
- POST /auth/mfa/verify: Second-factor TOTP and recovery code verification for interim sessions.
- POST /auth/reauth: Password and MFA confirmation refreshing step-up (R) window for 10 minutes.
- POST /auth/logout: Revocation of active session cookie and step-up confirmation.
- POST /auth/logout-all: Invalidation of all sessions across all devices.
- POST /auth/password/forgot: Uniform enumeration-resistant password reset request.
- POST /auth/password/reset: Single-use token password reset invalidating all active sessions.
- POST /auth/invite/accept: Initial staff account activation setting password via 72h token.
- GET /auth/csrf: Issues CSRF token bound to current session or fresh anonymous cookie.

Architectural Rule (T-STRUCT-04): No direct imports of models or repositories in router layer.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    check_rate_limit,
    clear_session_cookie,
    clear_session_reauth,
    generate_csrf_token,
    get_auth_service,
    get_current_session_and_user,
    get_mfa_service,
    get_session_service,
    get_session_token,
    record_session_reauth,
    set_session_cookie,
)
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.errors import InvalidCredentialsError, ValidationError
from app.core.security import generate_secure_token, verify_password
from app.schemas.auth import (
    AcceptInviteIn,
    AcceptInviteOut,
    CsrfOut,
    ForgotPasswordIn,
    LoginIn,
    LoginOut,
    MfaVerifyIn,
    ReauthIn,
    ResetPasswordIn,
    UserAuthOut,
)
from app.services.auth import AuthService
from app.services.mfa import MfaService
from app.services.sessions import SessionService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get(
    "/csrf",
    response_model=CsrfOut,
    summary="Get CSRF token",
    description="Returns cryptographically signed CSRF token bound to current or anonymous session.",
)
def get_csrf(
    response: Response,
    session_token: Annotated[str | None, Depends(get_session_token)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CsrfOut:
    """Retrieve CSRF token, issuing anonymous session cookie if no cookie is currently attached."""
    active_token = session_token
    if not active_token:
        # Issue fresh anonymous session token to anchor CSRF protection
        active_token = generate_secure_token(32)
        set_session_cookie(response, active_token, settings=settings)

    csrf_token = generate_csrf_token(active_token, secret_key=settings.secret_key)
    return CsrfOut(csrf_token=csrf_token)


@router.post(
    "/login",
    response_model=LoginOut,
    dependencies=[Depends(check_rate_limit("login", limit=10, window_seconds=900))],
    summary="User login",
    description="Authenticate staff credentials, set hardened session cookie, and check MFA status.",
)
def login(
    credentials: LoginIn,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginOut:
    """Authenticate email and password credentials, returning user profile and session cookie."""
    client_ip_hash = getattr(request.state, "client_ip_hash", None)
    user_agent = request.headers.get("user-agent")

    result = auth_service.authenticate(
        credentials.email,
        credentials.password,
        ip_hash=client_ip_hash,
        user_agent=user_agent,
    )

    # Attach hardened session cookie (__Host- in prod, HttpOnly, Secure, SameSite=Lax)
    set_session_cookie(response, result.raw_session_token, settings=settings)

    return LoginOut(
        user=UserAuthOut(
            id=result.user.id,
            email=result.user.email,
            full_name=result.user.full_name,
            role=result.user.role,
            site_id=result.user.site_id,
        ),
        mfa_required=result.mfa_required,
    )


@router.post(
    "/mfa/verify",
    response_model=LoginOut,
    summary="Verify MFA during login",
    description="Verify TOTP code or backup recovery code, promoting interim session to verified status.",
)
def verify_mfa(
    payload: MfaVerifyIn,
    session_and_user: Annotated[tuple[Any, Any], Depends(get_current_session_and_user)],
    mfa_service: Annotated[MfaService, Depends(get_mfa_service)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> LoginOut:
    """Verify second-factor code and promote interim session."""
    session, user = session_and_user

    # Verify 6-digit TOTP code or backup recovery code
    mfa_service.verify_mfa(
        user.id,
        code=payload.code,
        recovery_code=payload.recovery_code,
        session=session,
    )

    # Fresh login with MFA grants immediate valid step-up confirmation (R)
    record_session_reauth(session.id, clock.now())

    return LoginOut(
        user=UserAuthOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            site_id=user.site_id,
        ),
        mfa_required=False,
    )


@router.post(
    "/reauth",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Step-up re-authentication",
    description="Confirm user password (and MFA code for admin) to renew 10-minute sensitive operation window.",
)
def reauth(
    payload: ReauthIn,
    session_and_user: Annotated[tuple[Any, Any], Depends(get_current_session_and_user)],
    mfa_service: Annotated[MfaService, Depends(get_mfa_service)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Response:
    """Re-authenticate current user to refresh step-up window (Condition R)."""
    session, user = session_and_user

    # 1. Verify current password
    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise InvalidCredentialsError("Invalid password.")

    # 2. If user is admin or has TOTP enabled, verify TOTP code
    is_admin = user.role == "admin"
    has_totp = bool(getattr(user, "totp_enabled", False))

    if is_admin or has_totp:
        if not payload.code:
            raise ValidationError("MFA verification code is required for re-authentication.")
        mfa_service.verify_totp(user.id, payload.code, session=session)

    # 3. Renew step-up confirmation timestamp
    record_session_reauth(session.id, clock.now())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="User logout",
    description="Revoke the active session and remove session cookie from client browser.",
)
def logout(
    response: Response,
    session_token: Annotated[str | None, Depends(get_session_token)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Revoke caller's current session and delete cookie."""
    if session_token:
        session_service.revoke_session(session_token)

    clear_session_cookie(response, settings=settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout all devices",
    description="Revoke all active sessions for the current user across all devices.",
)
def logout_all(
    response: Response,
    session_and_user: Annotated[tuple[Any, Any], Depends(get_current_session_and_user)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Revoke all sessions belonging to the user and clear cookie."""
    session, user = session_and_user
    session_service.revoke_all_user_sessions(user.id)
    clear_session_reauth(session.id)
    clear_session_cookie(response, settings=settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password/forgot",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(check_rate_limit("password_forgot", limit=5, window_seconds=3600))],
    summary="Request password reset",
    description="Request a single-use password reset link via email (uniform response for all emails).",
)
def forgot_password(
    payload: ForgotPasswordIn,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    """Send password reset token if account exists, returning identical 204 response."""
    auth_service.request_password_reset(payload.email)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reset password",
    description="Reset account password using valid one-time token and invalidate all active sessions.",
)
def reset_password(
    payload: ResetPasswordIn,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Complete password reset and invalidate all existing sessions."""
    auth_service.reset_password(payload.token, payload.new_password)
    clear_session_cookie(response, settings=settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/invite/accept",
    response_model=AcceptInviteOut,
    dependencies=[Depends(check_rate_limit("invite_accept", limit=10, window_seconds=3600))],
    summary="Accept staff invitation",
    description="Accept invitation token and configure initial account password to activate staff member.",
)
def accept_invite(
    payload: AcceptInviteIn,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AcceptInviteOut:
    """Accept invitation and activate account."""
    user = auth_service.accept_invitation(payload.token, payload.password)
    return AcceptInviteOut(
        id=user.id,
        email=user.email,
        role=user.role,
        status="activated",
    )
