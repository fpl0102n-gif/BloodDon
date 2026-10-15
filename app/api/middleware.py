"""HTTP security and operational middleware stack.

Implements P1.7 per docs/05-architecture.md §16, docs/06-business-rules.md (BR-AUTH-18),
docs/08-setup-env.md §10, and docs/10-testing-security-deployment.md §15.3:

1. RequestIDMiddleware:
   - Injects or propagates correlation request ID.
   - Binds request ID to logging ContextVar for unified log tracing.
   - Attaches X-Request-ID response header.

2. ClientIPMiddleware:
   - Resolves genuine client IP using trusted-proxy inspection (T-RATE-10).
   - Populates request.state.client_ip and request.state.client_ip_hash.

3. SecurityHeadersMiddleware:
   - Injects modern security headers on all responses:
     - Content-Security-Policy (strict, self-only, no inline scripts D-34/D-71).
     - X-Content-Type-Options: nosniff
     - X-Frame-Options: DENY (clickjacking protection)
     - Referrer-Policy: strict-origin-when-cross-origin
     - Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
     - Strict-Transport-Security (HSTS in production / HTTPS)

4. OriginCheckMiddleware:
   - Evaluates state-changing requests (POST, PUT, PATCH, DELETE).
   - If an Origin or Referer header is present, validates that it matches ALLOWED_ORIGINS.
   - Rejects unauthorized or foreign origins immediately with HTTP 403 and ErrorCode.CSRF_FAILED (T-AUTH-18-b).
"""

import re
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import Settings, get_settings
from app.core.errors import CsrfFailedError
from app.core.logging import anonymize_ip, get_current_request_id, request_id_ctx
from app.core.ratelimit import resolve_client_ip

# Validation regex for correlation IDs passed from reverse proxies (e.g. Caddy/Nginx)
REQUEST_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{8,128}$")
REQUEST_ID_HEADER = "X-Request-ID"

STATE_CHANGING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Strict CSP directive set enforcing self-hosted assets and blocking inline execution (D-34, D-71)
DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'"
)

DEFAULT_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"
HSTS_HEADER_VALUE = "max-age=63072000; includeSubDomains; preload"


def normalize_origin(origin: str) -> str:
    """Normalize origin string to lowercase scheme and netloc without trailing slash."""
    origin = origin.strip().rstrip("/")
    parsed = urlparse(origin)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return origin.lower()


# ==============================================================================
# 1. Request ID Middleware
# ==============================================================================


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Correlation Request ID middleware.

    Accepts safe incoming X-Request-ID from upstream reverse proxy or generates a
    fresh UUIDv4. Propagates ID via contextvars for structured logging and attaches
    X-Request-ID to the final HTTP response.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        incoming_id = request.headers.get("x-request-id")
        if incoming_id and REQUEST_ID_REGEX.match(incoming_id):
            req_id = incoming_id
        else:
            req_id = str(uuid.uuid4())

        request.state.request_id = req_id
        token = request_id_ctx.set(req_id)

        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = req_id
            return response
        finally:
            request_id_ctx.reset(token)


# ==============================================================================
# 2. Client IP Resolution Middleware
# ==============================================================================


class ClientIPMiddleware(BaseHTTPMiddleware):
    """Client IP extraction middleware respecting trusted reverse proxy boundaries."""

    def __init__(
        self,
        app: Any,
        settings: Settings | None = None,
        trusted_proxies: list[str] | str | None = None,
    ) -> None:
        super().__init__(app)
        self.settings = settings
        self.trusted_proxies = trusted_proxies

    def _get_trusted_proxies(self) -> list[str] | str:
        if self.trusted_proxies is not None:
            return self.trusted_proxies
        active_settings = self.settings or get_settings()
        return active_settings.trusted_proxies

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        proxies = self._get_trusted_proxies()
        client_ip = resolve_client_ip(request, trusted_proxies=proxies)
        request.state.client_ip = client_ip
        request.state.client_ip_hash = anonymize_ip(client_ip)
        return await call_next(request)


# ==============================================================================
# 3. Security Headers Middleware
# ==============================================================================


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce strict defensive security headers on every response.

    Applies:
    - Content-Security-Policy (strict, self-hosted only, no inline code)
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Referrer-Policy: strict-origin-when-cross-origin
    - Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
    - Strict-Transport-Security: applied in production or HTTPS
    """

    def __init__(
        self,
        app: Any,
        settings: Settings | None = None,
        csp: str = DEFAULT_CSP,
    ) -> None:
        super().__init__(app)
        self.settings = settings
        self.csp = csp

    def _is_prod_or_https(self, request: Request) -> bool:
        active_settings = self.settings or get_settings()
        if active_settings.app_env == "prod":
            return True
        # Check standard scheme and X-Forwarded-Proto header
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        return proto.lower() == "https"

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)

        response.headers.setdefault("Content-Security-Policy", self.csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", DEFAULT_PERMISSIONS_POLICY)

        if self._is_prod_or_https(request):
            response.headers.setdefault("Strict-Transport-Security", HSTS_HEADER_VALUE)

        return response


# ==============================================================================
# 4. Origin & Cross-Origin Check Middleware
# ==============================================================================


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """Validate Origin/Referer on state-changing requests to block cross-origin writes.

    Per BR-AUTH-18 and docs/05-architecture.md §16:
    - Non-state changing requests (GET, HEAD, OPTIONS) bypass Origin verification.
    - If Origin header is provided on POST/PUT/PATCH/DELETE, it must match ALLOWED_ORIGINS.
    - If Origin is absent, Referer header's origin is verified if present.
    - Disallowed origins receive immediate HTTP 403 with code `csrf_failed`.
    """

    def __init__(
        self,
        app: Any,
        settings: Settings | None = None,
        allowed_origins: list[str] | set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.settings = settings
        self._custom_allowed_origins = (
            {normalize_origin(o) for o in allowed_origins} if allowed_origins is not None else None
        )

    def _get_allowed_origins(self) -> set[str]:
        if self._custom_allowed_origins is not None:
            return self._custom_allowed_origins

        active_settings = self.settings or get_settings()
        raw_origins = active_settings.allowed_origins
        if isinstance(raw_origins, str):
            origin_list = [o.strip() for o in raw_origins.split(",") if o.strip()]
        else:
            origin_list = list(raw_origins)

        # Always include normalized public_base_url
        origin_list.append(active_settings.public_base_url)
        return {normalize_origin(o) for o in origin_list if o}

    def _reject_untrusted_origin(self, request: Request) -> JSONResponse:
        req_id = getattr(request.state, "request_id", None) or get_current_request_id()
        err = CsrfFailedError("Origin is not allowed for state-changing requests.")
        headers: dict[str, str] = {}
        if req_id:
            headers[REQUEST_ID_HEADER] = req_id
        return JSONResponse(
            status_code=403,
            content=err.to_dict(request_id=req_id),
            headers=headers,
        )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.method in STATE_CHANGING_METHODS:
            allowed = self._get_allowed_origins()
            origin = request.headers.get("origin")

            if origin is not None:
                # Opaque or null origin (e.g. sandboxed iframe or file://)
                if origin.lower() == "null" or normalize_origin(origin) not in allowed:
                    return self._reject_untrusted_origin(request)
            else:
                # If Origin is absent, check Referer if present
                referer = request.headers.get("referer")
                if referer:
                    parsed_ref = urlparse(referer)
                    if parsed_ref.scheme and parsed_ref.netloc:
                        ref_origin = f"{parsed_ref.scheme.lower()}://{parsed_ref.netloc.lower()}"
                        if ref_origin not in allowed:
                            return self._reject_untrusted_origin(request)

        return await call_next(request)


# ==============================================================================
# Helper to register all middlewares in required execution order
# ==============================================================================


def register_middleware(
    app: FastAPI,
    settings: Settings | None = None,
) -> None:
    """Register core security and operational middleware stack in required order.

    Order of execution (outermost to innermost):
    1. RequestIDMiddleware (outermost: ensures all responses have correlation ID)
    2. ClientIPMiddleware (resolves client IP before business processing)
    3. SecurityHeadersMiddleware (applies security headers to all responses)
    4. OriginCheckMiddleware (blocks disallowed cross-origin write requests)
    """
    app.add_middleware(OriginCheckMiddleware, settings=settings)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(ClientIPMiddleware, settings=settings)
    app.add_middleware(RequestIDMiddleware)
