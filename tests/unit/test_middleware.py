"""Unit and integration tests for security and operational middleware stack.

Traces to:
- P1.7 (docs/12-implementation-plan.md §4)
- docs/05-architecture.md §16 (middleware pipeline, headers, Origin checking)
- docs/06-business-rules.md (BR-AUTH-18)
- docs/07-test-cases.md (T-AUTH-18-b, T-WEB-04-a, headers verification)
"""

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.error_handlers import register_error_handlers
from app.api.middleware import (
    DEFAULT_CSP,
    HSTS_HEADER_VALUE,
    ClientIPMiddleware,
    OriginCheckMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    register_middleware,
)
from app.core.config import Settings
from app.core.logging import get_current_request_id


def create_test_app(
    settings: Settings | None = None,
    allowed_origins: list[str] | None = None,
    trusted_proxies: list[str] | None = None,
) -> FastAPI:
    """Helper to create an isolated FastAPI app wired with the middleware stack."""
    app = FastAPI()
    register_error_handlers(app)

    # Register individual middlewares in test if custom overrides needed,
    # or register standard middleware stack
    if allowed_origins is not None or trusted_proxies is not None:
        app.add_middleware(
            OriginCheckMiddleware,
            settings=settings,
            allowed_origins=allowed_origins,
        )
        app.add_middleware(SecurityHeadersMiddleware, settings=settings)
        app.add_middleware(
            ClientIPMiddleware,
            settings=settings,
            trusted_proxies=trusted_proxies,
        )
        app.add_middleware(RequestIDMiddleware)
    else:
        register_middleware(app, settings=settings)

    @app.get("/test")
    async def get_endpoint(request: Request) -> dict[str, str]:
        req_id = getattr(request.state, "request_id", "")
        ctx_id = get_current_request_id() or ""
        client_ip = getattr(request.state, "client_ip", "")
        client_ip_hash = getattr(request.state, "client_ip_hash", "")
        return {
            "status": "ok",
            "request_id": req_id,
            "context_request_id": ctx_id,
            "client_ip": client_ip,
            "client_ip_hash": client_ip_hash,
        }

    @app.post("/test")
    async def post_endpoint(request: Request) -> dict[str, str]:
        req_id = getattr(request.state, "request_id", "")
        return {"status": "created", "request_id": req_id}

    @app.put("/test")
    async def put_endpoint() -> dict[str, str]:
        return {"status": "updated"}

    @app.delete("/test")
    async def delete_endpoint() -> dict[str, str]:
        return {"status": "deleted"}

    @app.get("/custom-header")
    async def custom_header_endpoint() -> JSONResponse:
        return JSONResponse(
            content={"status": "ok"},
            headers={"X-Custom-Header": "preset-value"},
        )

    return app


# ==============================================================================
# 1. Request ID Middleware Tests
# ==============================================================================


@pytest.mark.unit
def test_request_id_generated_when_absent() -> None:
    """Verify fresh valid UUID4 is generated and returned when X-Request-ID is absent."""
    app = create_test_app()
    client = TestClient(app)

    response = client.get("/test")
    assert response.status_code == 200

    resp_header = response.headers.get("X-Request-ID")
    assert resp_header is not None
    # Validate UUID4 structure
    parsed_uuid = uuid.UUID(resp_header)
    assert parsed_uuid.version == 4

    body = response.json()
    assert body["request_id"] == resp_header
    assert body["context_request_id"] == resp_header


@pytest.mark.unit
def test_request_id_propagated_when_valid() -> None:
    """Verify safe incoming X-Request-ID from reverse proxy is preserved and propagated."""
    app = create_test_app()
    client = TestClient(app)

    incoming_id = "upstream-trace-12345_abc"
    response = client.get("/test", headers={"X-Request-ID": incoming_id})

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == incoming_id
    body = response.json()
    assert body["request_id"] == incoming_id
    assert body["context_request_id"] == incoming_id


@pytest.mark.unit
def test_request_id_sanitized_when_malformed() -> None:
    """Verify malformed or unsafe incoming X-Request-ID is ignored and replaced with a UUID4."""
    app = create_test_app()
    client = TestClient(app)

    # Contains CRLF / illegal characters
    malformed_id = "bad\r\nid<script>alert(1)</script>"
    response = client.get("/test", headers={"X-Request-ID": malformed_id})

    assert response.status_code == 200
    resp_header = response.headers.get("X-Request-ID")
    assert resp_header is not None
    assert resp_header != malformed_id
    # Must be valid UUID4
    assert uuid.UUID(resp_header).version == 4


# ==============================================================================
# 2. Security Headers Middleware Tests
# ==============================================================================


@pytest.mark.unit
def test_security_headers_present_on_all_responses() -> None:
    """Verify all standard defensive security headers are attached by default."""
    app = create_test_app()
    client = TestClient(app)

    response = client.get("/test")
    assert response.status_code == 200

    assert response.headers.get("Content-Security-Policy") == DEFAULT_CSP
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers.get("Permissions-Policy", "")

    # In default dev mode over HTTP, HSTS should not be set
    assert "Strict-Transport-Security" not in response.headers


@pytest.mark.unit
def test_security_headers_hsts_in_production() -> None:
    """Verify HSTS header is attached when app_env is 'prod'."""
    valid_totp = "Z8jQL92Z6rJ3nJ-0OgCcO3J3Q_QuW722y3o8ohEiTV4="  # pragma: allowlist secret
    prod_settings = Settings(
        app_env="prod",
        secret_key="a" * 64,  # pragma: allowlist secret
        totp_encryption_key=valid_totp,
        ip_hash_salt="e" * 64,  # pragma: allowlist secret
        public_base_url="https://blood.example.com",
        allowed_origins=["https://blood.example.com"],
        docs_enabled=False,
        cookie_secure=True,
        database_url="postgresql+psycopg://user:pass@managed-db:5432/prod",  # pragma: allowlist secret
    )
    app = create_test_app(settings=prod_settings)
    client = TestClient(app)

    response = client.get("/test")
    assert response.status_code == 200
    assert response.headers.get("Strict-Transport-Security") == HSTS_HEADER_VALUE


@pytest.mark.unit
def test_security_headers_hsts_on_https_forwarded_proto() -> None:
    """Verify HSTS is attached if X-Forwarded-Proto indicates HTTPS even in dev."""
    app = create_test_app()
    client = TestClient(app)

    response = client.get("/test", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assert response.headers.get("Strict-Transport-Security") == HSTS_HEADER_VALUE


@pytest.mark.unit
def test_security_headers_preserves_custom_endpoint_headers() -> None:
    """Verify custom response headers set by endpoints are preserved alongside security headers."""
    app = create_test_app()
    client = TestClient(app)

    response = client.get("/custom-header")
    assert response.status_code == 200
    assert response.headers.get("X-Custom-Header") == "preset-value"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


# ==============================================================================
# 3. Origin & Cross-Origin Check Middleware Tests
# ==============================================================================


@pytest.mark.unit
def test_origin_check_safe_methods_allowed_regardless_of_origin() -> None:
    """Verify read-only methods (GET, HEAD, OPTIONS) bypass Origin rejection."""
    app = create_test_app(allowed_origins=["http://localhost:8000"])
    client = TestClient(app)

    # Foreign origin on GET request must succeed
    response = client.get("/test", headers={"Origin": "https://evil.com"})
    assert response.status_code == 200


@pytest.mark.unit
def test_origin_check_write_matching_origin_allowed() -> None:
    """Verify state-changing POST succeeds when Origin matches ALLOWED_ORIGINS."""
    app = create_test_app(allowed_origins=["http://localhost:8000"])
    client = TestClient(app)

    response = client.post(
        "/test",
        headers={"Origin": "http://localhost:8000"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "created",
        "request_id": response.headers.get("X-Request-ID"),
    }


@pytest.mark.unit
@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_origin_check_write_foreign_origin_rejected(method: str) -> None:
    """Verify state-changing requests with foreign Origin are rejected with 403 csrf_failed (T-AUTH-18-b)."""
    app = create_test_app(allowed_origins=["http://localhost:8000"])
    client = TestClient(app)

    client_method = getattr(client, method)
    response = client_method(
        "/test",
        headers={"Origin": "https://attacker.org"},
    )

    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "csrf_failed"
    assert "Origin is not allowed" in payload["error"]["message"]
    assert "request_id" in payload["error"]

    # Security headers and X-Request-ID must still be attached to the rejected response
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Request-ID") == payload["error"]["request_id"]


@pytest.mark.unit
def test_origin_check_write_null_origin_rejected() -> None:
    """Verify state-changing requests with 'Origin: null' are rejected with 403 csrf_failed."""
    app = create_test_app(allowed_origins=["http://localhost:8000"])
    client = TestClient(app)

    response = client.post("/test", headers={"Origin": "null"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


@pytest.mark.unit
def test_origin_check_write_referer_matching_allowed() -> None:
    """Verify state-changing request without Origin header succeeds if Referer origin matches."""
    app = create_test_app(allowed_origins=["http://localhost:8000"])
    client = TestClient(app)

    response = client.post(
        "/test",
        headers={"Referer": "http://localhost:8000/supervisor/donors/new"},
    )
    assert response.status_code == 200


@pytest.mark.unit
def test_origin_check_write_referer_foreign_rejected() -> None:
    """Verify state-changing request without Origin but with foreign Referer is rejected."""
    app = create_test_app(allowed_origins=["http://localhost:8000"])
    client = TestClient(app)

    response = client.post(
        "/test",
        headers={"Referer": "https://malicious.com/phish"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


@pytest.mark.unit
def test_origin_check_write_direct_no_origin_allowed_at_middleware_level() -> None:
    """Verify direct API requests without Origin or Referer pass middleware (token checked in P3)."""
    app = create_test_app(allowed_origins=["http://localhost:8000"])
    client = TestClient(app)

    response = client.post("/test")
    assert response.status_code == 200


# ==============================================================================
# 4. Client IP Middleware Tests
# ==============================================================================


@pytest.mark.unit
def test_client_ip_middleware_direct_peer() -> None:
    """Verify ClientIPMiddleware correctly extracts direct peer IP and salted hash."""
    app = create_test_app()
    client = TestClient(app)

    response = client.get("/test")
    assert response.status_code == 200
    body = response.json()
    assert body["client_ip"] in ("testclient", "127.0.0.1")
    assert body["client_ip_hash"].startswith("ip_hash_")


@pytest.mark.unit
def test_client_ip_middleware_trusted_proxy_traversal() -> None:
    """Verify ClientIPMiddleware resolves client from X-Forwarded-For when peer is a trusted proxy."""
    # TestClient sets client.host to 'testclient'. We configure 'testclient' as trusted proxy.
    app = create_test_app(trusted_proxies=["testclient"])
    client = TestClient(app)

    response = client.get(
        "/test",
        headers={"X-Forwarded-For": "198.51.100.22, testclient"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["client_ip"] == "198.51.100.22"
    assert body["client_ip_hash"].startswith("ip_hash_")
