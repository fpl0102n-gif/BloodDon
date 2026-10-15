"""Tests for application factory, system monitoring endpoints, and OpenAPI documentation gating.

Traces to:
- P1.8 (docs/12-implementation-plan.md §4)
- docs/04-api.md §21 (/health and /ready endpoints)
- docs/04-api.md §20 / decision D-25 (/docs disabled in production via DOCS_ENABLED)
- docs/07-test-cases.md (T-STRUCT-08: production settings and documentation gating)
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app as main_app
from app.main import create_app


@pytest.fixture
def dev_client() -> TestClient:
    """Test client for standard dev app with default settings."""
    app = create_app()
    return TestClient(app)


# ==============================================================================
# 1. /health Endpoint (Liveness Probe)
# ==============================================================================


@pytest.mark.api
def test_health_check_returns_200_and_security_headers(dev_client: TestClient) -> None:
    """Verify /health returns 200 OK without database query, with security headers and request-id."""
    response = dev_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Must contain correlation request ID
    assert response.headers.get("X-Request-ID") is not None

    # Must enforce security headers from middleware pipeline
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert "default-src 'self'" in response.headers.get("Content-Security-Policy", "")


# ==============================================================================
# 2. /ready Endpoint (Readiness Probe)
# ==============================================================================


@pytest.mark.api
def test_ready_check_database_connected(dev_client: TestClient) -> None:
    """Verify /ready returns 200 when database connectivity test succeeds."""
    with patch("app.api.routers.system.check_database_connection", return_value=True):
        response = dev_client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": "connected"}
        assert response.headers.get("X-Request-ID") is not None


@pytest.mark.api
def test_ready_check_database_disconnected(dev_client: TestClient) -> None:
    """Verify /ready returns 503 when database is unreachable or fails connectivity test."""
    with patch("app.api.routers.system.check_database_connection", return_value=False):
        response = dev_client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "database": "disconnected"}
        assert response.headers.get("X-Request-ID") is not None


# ==============================================================================
# 3. Documentation Gating (D-25, T-STRUCT-08)
# ==============================================================================


@pytest.mark.api
def test_docs_accessible_when_enabled() -> None:
    """Verify /docs and /openapi.json are accessible when docs_enabled is True."""
    dev_settings = Settings(app_env="dev", docs_enabled=True)
    app = create_app(settings=dev_settings)
    client = TestClient(app)

    docs_resp = client.get("/docs")
    assert docs_resp.status_code == 200

    openapi_resp = client.get("/openapi.json")
    assert openapi_resp.status_code == 200
    assert "openapi" in openapi_resp.json()


@pytest.mark.api
@pytest.mark.critical
def test_docs_disabled_in_production_config_t_struct_08() -> None:
    """T-STRUCT-08: Verify /docs, /redoc, and /openapi.json return 404 when docs_enabled is False."""
    prod_settings = Settings(
        app_env="prod",
        secret_key="a" * 64,  # pragma: allowlist secret
        totp_encryption_key="Z8jQL92Z6rJ3nJ-0OgCcO3J3Q_QuW722y3o8ohEiTV4=",  # pragma: allowlist secret
        ip_hash_salt="e" * 64,  # pragma: allowlist secret
        public_base_url="https://blood.example.com",
        allowed_origins=["https://blood.example.com"],
        docs_enabled=False,
        cookie_secure=True,
        database_url="postgresql+psycopg://user:pass@managed-db:5432/prod",  # pragma: allowlist secret
    )
    app = create_app(settings=prod_settings)
    client = TestClient(app)

    # /docs must return 404
    docs_resp = client.get("/docs")
    assert docs_resp.status_code == 404

    # /redoc must return 404
    redoc_resp = client.get("/redoc")
    assert redoc_resp.status_code == 404

    # /openapi.json must return 404
    openapi_resp = client.get("/openapi.json")
    assert openapi_resp.status_code == 404


# ==============================================================================
# 4. App Entrypoint Verification
# ==============================================================================


@pytest.mark.api
def test_main_app_singleton_is_importable_and_functional() -> None:
    """Verify default 'app' exported from app.main functions correctly for Uvicorn."""
    client = TestClient(main_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
