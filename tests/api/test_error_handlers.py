"""Unit and contract tests for API error handlers.

Traces to:
- P1.4 (docs/12-implementation-plan.md §4)
- docs/04-api.md §3 (unified error envelope and status codes)
- BR-GEN-06 (extra fields -> 422 unknown_field)
- BR-GEN-07 (reserved fields injection -> 422 unknown_field + audit)
- T-GEN-06-a (every write route rejecting extra fields with unknown_field)
"""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.api.error_handlers import register_error_handlers
from app.core.errors import (
    CampaignNotLiveError,
    NotFoundError,
    PhoneRequiresConsentError,
    RateLimitedError,
)


class DummyStrictModel(BaseModel):
    """Pydantic model with strict extra='forbid' per BR-GEN-06."""

    model_config = ConfigDict(extra="forbid")
    name: str
    count: int


def create_test_app() -> FastAPI:
    """Create a minimal FastAPI instance with registered error handlers and test routes."""
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/test/strict-body")
    def post_strict_body(payload: DummyStrictModel) -> dict[str, str]:
        return {"status": "ok", "name": payload.name}

    @app.get("/test/domain-not-found")
    def get_not_found() -> None:
        raise NotFoundError("Custom item not found.")

    @app.get("/test/domain-campaign-not-live")
    def get_campaign_not_live() -> None:
        raise CampaignNotLiveError()

    @app.get("/test/domain-rate-limited")
    def get_rate_limited() -> None:
        raise RateLimitedError("Too many attempts.", retry_after=30)

    @app.get("/test/domain-consent-missing")
    def get_consent_missing() -> None:
        raise PhoneRequiresConsentError(details=[{"field": "phone", "issue": "requires_consent"}])

    @app.get("/test/http-exception-403")
    def get_http_forbidden() -> None:
        raise HTTPException(status_code=403, detail="Forbidden route access.")

    @app.get("/test/unhandled-crash")
    def get_unhandled_crash() -> None:
        raise RuntimeError("CRITICAL: Secret DB column password_hash leak!")

    return app


@pytest.fixture
def test_client() -> TestClient:
    """Fixture providing a TestClient configured with error handlers."""
    app = create_test_app()
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
@pytest.mark.api
def test_domain_error_not_found_envelope(test_client: TestClient) -> None:
    """Verify AppError subclasses serialize to the standard unified envelope."""
    response = test_client.get("/test/domain-not-found")

    assert response.status_code == 404
    data = response.json()

    assert "error" in data
    assert data["error"]["code"] == "not_found"
    assert data["error"]["message"] == "Custom item not found."
    assert "request_id" in data["error"]
    assert len(data["error"]["request_id"]) > 0


@pytest.mark.unit
@pytest.mark.api
def test_domain_error_with_headers(test_client: TestClient) -> None:
    """Verify AppError headers (e.g. Retry-After) are attached to HTTP response."""
    response = test_client.get("/test/domain-rate-limited")

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "30"

    data = response.json()
    assert data["error"]["code"] == "rate_limited"
    assert data["error"]["message"] == "Too many attempts."


@pytest.mark.unit
@pytest.mark.api
def test_domain_error_with_details(test_client: TestClient) -> None:
    """Verify AppError details field is rendered when supplied."""
    response = test_client.get("/test/domain-consent-missing")

    assert response.status_code == 422
    data = response.json()

    assert data["error"]["code"] == "phone_requires_consent"
    assert data["error"]["details"] == [{"field": "phone", "issue": "requires_consent"}]


@pytest.mark.unit
@pytest.mark.api
def test_validation_error_on_standard_invalid_type(test_client: TestClient) -> None:
    """Verify standard schema failure returns 422 validation_error with details."""
    response = test_client.post(
        "/test/strict-body",
        json={"name": "test", "count": "not_an_int"},
    )

    assert response.status_code == 422
    data = response.json()

    assert data["error"]["code"] == "validation_error"
    assert data["error"]["message"] == "Request validation failed."
    assert "details" in data["error"]

    fields = [d["field"] for d in data["error"]["details"]]
    assert "count" in fields


@pytest.mark.unit
@pytest.mark.api
def test_unknown_field_rejected_per_br_gen_06(test_client: TestClient) -> None:
    """Verify extra fields trigger 422 unknown_field per BR-GEN-06 / T-GEN-06-a."""
    response = test_client.post(
        "/test/strict-body",
        json={"name": "valid", "count": 10, "zzz_extra": "malicious_payload"},
    )

    assert response.status_code == 422
    data = response.json()

    assert data["error"]["code"] == "unknown_field"
    assert data["error"]["message"] == "Unknown or forbidden field in request payload."
    assert "details" in data["error"]

    extra_fields = [
        d["field"] for d in data["error"]["details"] if d.get("issue") == "extra_forbidden"
    ]
    assert "zzz_extra" in extra_fields


@pytest.mark.unit
@pytest.mark.api
def test_reserved_field_triggers_security_log_per_br_gen_07(
    test_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify client-injected reserved fields trigger 422 unknown_field and security audit log."""
    with caplog.at_level(logging.WARNING, logger="app.api.errors"):
        response = test_client.post(
            "/test/strict-body",
            json={
                "name": "valid",
                "count": 5,
                "role": "admin",
                "status": "attended",
            },
        )

    assert response.status_code == 422
    data = response.json()

    assert data["error"]["code"] == "unknown_field"

    # Verify security audit event logged
    matching_logs = [
        record for record in caplog.records if "security.forbidden_field" in record.message
    ]
    assert len(matching_logs) > 0
    assert "role" in matching_logs[0].message
    assert "status" in matching_logs[0].message


@pytest.mark.unit
@pytest.mark.api
def test_http_exception_envelope(test_client: TestClient) -> None:
    """Verify standard HTTPException is transformed into unified error envelope."""
    response = test_client.get("/test/http-exception-403")

    assert response.status_code == 403
    data = response.json()

    assert data["error"]["code"] == "forbidden"
    assert data["error"]["message"] == "Forbidden route access."
    assert "request_id" in data["error"]


@pytest.mark.unit
@pytest.mark.api
def test_unhandled_crash_never_leaks_secrets_or_stacktrace(
    test_client: TestClient,
) -> None:
    """Verify unexpected internal exception returns 500 internal_error with no sensitive leakage."""
    response = test_client.get("/test/unhandled-crash")

    assert response.status_code == 500
    data = response.json()

    assert data["error"]["code"] == "internal_error"
    assert data["error"]["message"] == "An unexpected internal error occurred."
    assert "request_id" in data["error"]

    # Verify NO internal details leak
    body_text = response.text
    assert "CRITICAL" not in body_text
    assert "password_hash" not in body_text
    assert "Secret" not in body_text
    assert "Traceback" not in body_text


@pytest.mark.unit
@pytest.mark.api
def test_request_id_header_propagation(test_client: TestClient) -> None:
    """Verify client-supplied X-Request-ID is preserved in the error envelope."""
    custom_req_id = "test-client-req-9999"
    response = test_client.get(
        "/test/domain-not-found",
        headers={"x-request-id": custom_req_id},
    )

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["request_id"] == custom_req_id
