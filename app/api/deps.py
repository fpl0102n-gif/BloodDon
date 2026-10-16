"""FastAPI dependency injection providers, cookie helpers, and CSRF enforcement.

Per docs/05-architecture.md §16, docs/06-business-rules.md (BR-AUTH-18, BR-AUTH-19),
and docs/03-auth-permissions.md §8.2, §8.5:
- Injects database session, session service, and auth service into route handlers.
- Sets and clears hardened HTTP session cookies (__Host- in production, HttpOnly, Secure, SameSite=Lax).
- Generates and verifies HMAC-bound CSRF tokens (X-CSRF-Token).
- Provides verify_csrf dependency enforcing CSRF protection on authenticated state-changing requests.
- Validates caller session and resolves authenticated user context.
"""

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import (
    CsrfFailedError,
    ForbiddenError,
    MfaRequiredError,
    NotAuthenticatedError,
    RateLimitedError,
    ReauthRequiredError,
)
from app.core.ratelimit import (
    RateLimiter,
    RateLimitResult,
    get_rate_limiter,
    resolve_client_ip_hash,
)
from app.mail import get_mailer
from app.models.user import AuthSession, User
from app.policy import (
    Condition,
    Permission,
    get_required_conditions,
    is_permission_granted,
)
from app.repositories.mfa import MfaRecoveryCodeRepository
from app.repositories.sessions import SessionRepository
from app.repositories.user_tokens import UserTokenRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.mfa import MfaService
from app.services.sessions import SessionService

# Safe idempotent HTTP methods that do not modify server state
SAFE_HTTP_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


# ==============================================================================
# 1. Cookie Management Helpers (BR-AUTH-19 / docs/03 §8.2)
# ==============================================================================


def set_session_cookie(
    response: Response,
    raw_session_token: str,
    *,
    settings: Settings | None = None,
) -> None:
    """Attach session cookie conforming to BR-AUTH-19 and RFC 6265bis.

    Flags:
    - HttpOnly: True (inaccessible to JavaScript).
    - Secure: True in production (HTTPS transmission only).
    - SameSite: "lax".
    - Path: "/".
    - Domain: None (restricted strictly to host origin, required for __Host- prefix).
    """
    cfg = settings or get_settings()
    cookie_name = cfg.cookie_name
    secure = cfg.cookie_secure or (cfg.app_env == "prod")

    response.set_cookie(
        key=cookie_name,
        value=raw_session_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        domain=None,
    )


def clear_session_cookie(
    response: Response,
    *,
    settings: Settings | None = None,
) -> None:
    """Clear session cookie from client browser upon logout or invalidation."""
    cfg = settings or get_settings()
    cookie_name = cfg.cookie_name
    secure = cfg.cookie_secure or (cfg.app_env == "prod")

    response.delete_cookie(
        key=cookie_name,
        path="/",
        domain=None,
        httponly=True,
        secure=secure,
        samesite="lax",
    )


def get_session_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str | None:
    """Extract raw session token from request cookies.

    Inspects configured cookie name or __Host- prefixed variant.
    """
    cookie_name = settings.cookie_name
    token = request.cookies.get(cookie_name)
    if not token and not cookie_name.startswith("__Host-"):
        token = request.cookies.get(f"__Host-{cookie_name}")
    return token


# ==============================================================================
# 2. CSRF Token Primitives & Verification (BR-AUTH-18 / docs/03 §8.5)
# ==============================================================================


def generate_csrf_token(
    raw_session_token: str,
    *,
    secret_key: str | None = None,
) -> str:
    """Generate cryptographically signed CSRF token bound to raw session token and SECRET_KEY."""
    key = (secret_key or get_settings().secret_key).encode("utf-8")
    return hmac.new(key, raw_session_token.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_csrf_token(
    raw_session_token: str,
    submitted_csrf_token: str | None,
    *,
    secret_key: str | None = None,
) -> bool:
    """Verify submitted CSRF token using constant-time comparison against expected HMAC."""
    if not submitted_csrf_token or not raw_session_token:
        return False
    expected = generate_csrf_token(raw_session_token, secret_key=secret_key)
    return hmac.compare_digest(expected, submitted_csrf_token.strip())


def verify_csrf(
    request: Request,
    session_token: Annotated[str | None, Depends(get_session_token)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Enforce CSRF protection for authenticated cookie-based state-changing requests.

    Per BR-AUTH-18 & docs/03-auth-permissions.md §8.5:
    - Safe methods (GET, HEAD, OPTIONS) bypass CSRF check.
    - Unauthenticated requests (no session cookie) bypass CSRF check (e.g. public pledges).
    - Authenticated state-changing requests (POST, PUT, PATCH, DELETE) MUST supply
      valid X-CSRF-Token matching the authenticated session.
    - Missing or mismatched token raises CsrfFailedError (HTTP 403 csrf_failed).
    """
    if request.method in SAFE_HTTP_METHODS:
        return

    # Unauthenticated requests (e.g. public pledges, anonymous visitors) do not require CSRF
    if session_token is None:
        return

    submitted_csrf = request.headers.get("x-csrf-token")
    if not submitted_csrf:
        raise CsrfFailedError("Missing required X-CSRF-Token header.")

    if not verify_csrf_token(session_token, submitted_csrf, secret_key=settings.secret_key):
        raise CsrfFailedError("Invalid CSRF token.")


# ==============================================================================
# 3. Service & User Context Providers
# ==============================================================================


def get_session_service(
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionService:
    """Provide SessionService instance bound to request DB session and Clock."""
    repo = SessionRepository(db)
    return SessionService(repo, clock=clock, settings=settings)


def get_auth_service(
    db: Annotated[Session, Depends(get_db)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    """Provide AuthService instance bound to request DB session and SessionService."""
    user_repo = UserRepository(db)
    token_repo = UserTokenRepository(db)
    mailer = get_mailer()
    return AuthService(
        user_repo,
        session_service,
        token_repo=token_repo,
        mailer=mailer,
        clock=clock,
        settings=settings,
    )


def get_mfa_service(
    db: Annotated[Session, Depends(get_db)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MfaService:
    """Provide MfaService instance bound to request DB session, repositories, and Clock."""
    user_repo = UserRepository(db)
    recovery_repo = MfaRecoveryCodeRepository(db)
    return MfaService(
        user_repo,
        recovery_repo,
        session_service,
        clock=clock,
        settings=settings,
    )


def get_current_session_and_user(
    session_token: Annotated[str | None, Depends(get_session_token)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
) -> tuple[AuthSession, User]:
    """Retrieve and validate the active session and user entity from cookie.

    Raises:
        NotAuthenticatedError: If session cookie is missing or user is inactive.
        SessionExpiredError: If session has expired or been revoked.
    """
    if not session_token:
        raise NotAuthenticatedError("Authentication required.")
    return session_service.validate_session(session_token)


def get_current_user(
    session_and_user: Annotated[tuple[AuthSession, User], Depends(get_current_session_and_user)],
) -> User:
    """Retrieve current authenticated user."""
    _, user = session_and_user
    return user


def get_current_session(
    session_and_user: Annotated[tuple[AuthSession, User], Depends(get_current_session_and_user)],
) -> AuthSession:
    """Retrieve current active AuthSession."""
    session, _ = session_and_user
    return session


# ==============================================================================
# 4. Step-Up Re-Authentication Tracking (BR-AUTH-14 / Decision D-20)
# ==============================================================================

STEP_UP_TIMEOUT_SECONDS: int = 600  # 10 minutes (docs/03-auth-permissions.md §12 / D-20)

# In-memory registry tracking recent password re-authentication timestamps per session
_SESSION_REAUTH_TIMESTAMPS: dict[str, datetime] = {}


def record_session_reauth(session_id: str, timestamp: datetime) -> None:
    """Record fresh password re-authentication timestamp for an active session."""
    _SESSION_REAUTH_TIMESTAMPS[session_id] = timestamp


def get_session_reauth_time(session: AuthSession) -> datetime:
    """Retrieve the latest re-authentication timestamp for a session.

    Defaults to session.created_at if no explicit re-auth confirmation has occurred.
    """
    return _SESSION_REAUTH_TIMESTAMPS.get(session.id, session.created_at)


def is_session_reauth_valid(
    session: AuthSession,
    now: datetime,
    *,
    timeout_seconds: int = STEP_UP_TIMEOUT_SECONDS,
) -> bool:
    """Check if session has a valid step-up confirmation within the timeout window."""
    last_reauth = get_session_reauth_time(session)
    if now.tzinfo is not None and last_reauth.tzinfo is None:
        last_reauth = last_reauth.replace(tzinfo=UTC)
    elif now.tzinfo is None and last_reauth.tzinfo is not None:
        now = now.replace(tzinfo=UTC)
    elapsed = (now - last_reauth).total_seconds()
    return elapsed <= timeout_seconds


def clear_session_reauth(session_id: str) -> None:
    """Clear re-authentication confirmation for a session."""
    _SESSION_REAUTH_TIMESTAMPS.pop(session_id, None)


# ==============================================================================
# 5. Declarative Authorization Dependency Factory (docs/03 §13 / BR-AUTH-14)
# ==============================================================================


def require(
    permission: Permission,
    *,
    require_reauth: bool = False,
) -> Callable[..., Any]:
    """FastAPI dependency factory enforcing authentication, role matrix, MFA, and step-up (R).

    Per docs/03-auth-permissions.md §11-§13, docs/06-business-rules.md (BR-AUTH-14),
    and docs/04-api.md §3.2, §22:
    - If permission is public (granted to 'anon'):
        - Requests without session return None (anonymous access allowed).
        - Authenticated requests return the active User entity.
    - If permission is protected:
        - Missing session cookie raises NotAuthenticatedError (401 not_authenticated).
        - Inactive user accounts raise NotAuthenticatedError.
        - Lack of role permission in matrix raises ForbiddenError (403 forbidden).
        - Admin or sessions with MFA requirement without session.mfa_verified=True raise
          MfaRequiredError (403 mfa_required).
        - Sensitive operations demanding step-up (R) with confirmation expired (> 600s) raise
          ReauthRequiredError (403 reauth_required).
        - Returns the authenticated User entity.
    """

    def dependency(
        request: Request,
        session_token: Annotated[str | None, Depends(get_session_token)],
        session_service: Annotated[SessionService, Depends(get_session_service)],
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> User | None:
        is_public = is_permission_granted("anon", permission)

        if not session_token:
            if is_public:
                return None
            raise NotAuthenticatedError("Authentication required.")

        session, user = session_service.validate_session(session_token)

        role = user.role
        conditions = get_required_conditions(role, permission)
        if conditions is None:
            raise ForbiddenError("You do not have permission to perform this action.")

        # Check MFA requirement: mandatory for admin role and any session/user with MFA enabled
        has_mfa = bool(getattr(user, "totp_enabled", getattr(user, "mfa_enabled", False)))
        needs_mfa = Condition.MFA_VERIFIED in conditions or role == "admin" or has_mfa
        if needs_mfa and not session.mfa_verified:
            raise MfaRequiredError("Two-factor authentication code required.")

        # Check step-up recent re-authentication (Condition.RECENT_REAUTH or require_reauth)
        needs_step_up = require_reauth or (Condition.RECENT_REAUTH in conditions)
        if needs_step_up:
            now = clock.now()
            if not is_session_reauth_valid(session, now):
                raise ReauthRequiredError("Recent password re-authentication required.")

        # Attach authenticated caller to request state for middleware/handlers
        request.state.user = user
        request.state.session = session
        return user

    return dependency


# ==============================================================================
# 6. Declarative Rate Limiting Dependency Factory (BR-RATE-01..09 / docs/04 §180)
# ==============================================================================


def get_rate_limiter_dep() -> RateLimiter:
    """FastAPI dependency yielding current application RateLimiter instance."""
    return get_rate_limiter()


def get_client_ip_hash(request: Request) -> str:
    """Retrieve or compute salted IP hash from request state or client connection."""
    ip_hash = getattr(request.state, "client_ip_hash", None)
    if not ip_hash:
        ip_hash = resolve_client_ip_hash(request)
        request.state.client_ip_hash = ip_hash
    return ip_hash


def check_rate_limit(
    key_prefix: str,
    limit: int,
    window_seconds: int,
) -> Callable[..., Any]:
    """Declarative dependency factory enforcing sliding-window rate limits per IP.

    Per BR-RATE-01..09 and docs/04 §180-§190:
    - Increments hit counter for key `rate:{key_prefix}:{ip_hash}`.
    - If limit is exceeded: raises RateLimitedError with Retry-After header.
    - Handled centrally to return HTTP 429 with error code `rate_limited`.
    """

    def dependency(
        request: Request,
        rate_limiter: Annotated[RateLimiter, Depends(get_rate_limiter_dep)],
    ) -> RateLimitResult:
        ip_hash = get_client_ip_hash(request)
        rate_key = f"rate:{key_prefix}:{ip_hash}"
        res = rate_limiter.hit(rate_key, limit=limit, window_seconds=window_seconds)
        if not res.allowed:
            raise RateLimitedError(
                "Too many requests. Please try again later.",
                retry_after=res.retry_after,
            )
        return res

    return dependency
