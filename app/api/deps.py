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
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import CsrfFailedError, NotAuthenticatedError
from app.mail import get_mailer
from app.models.user import AuthSession, User
from app.repositories.sessions import SessionRepository
from app.repositories.user_tokens import UserTokenRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService
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
