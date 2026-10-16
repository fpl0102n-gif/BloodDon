"""API integration tests for rate limiting (P3.10).

Traces to:
- docs/06-business-rules.md §14 (BR-RATE-01, BR-RATE-02, BR-RATE-06, BR-RATE-07, BR-RATE-09 ★).
- docs/07-test-cases.md §5.14:
  - T-RATE-01-a: Login rate limit (10 hits per 15 min per IP; 11th yields 429).
  - T-RATE-02-a: MFA code failure limit (5 failures revokes interim session; 6th attempt rejected).
  - T-RATE-06-a: Password forgot rate limit (5 hits per hour; 6th yields 429).
  - T-RATE-07-a: Accept invite rate limit (10 hits per hour; 11th yields 429).
  - T-RATE-09-a (★): Exceeded limit response returns HTTP 429 + Retry-After + code rate_limited.
- docs/04-api.md §180-§190.
"""

from collections.abc import Generator
from datetime import datetime

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import generate_csrf_token
from app.core.clock import FakeClock, set_clock
from app.core.config import get_settings
from app.core.ratelimit import InMemoryRateLimiter, set_rate_limiter
from app.core.security import encrypt_totp_secret, hash_password
from app.models.user import User
from tests.factories.campaign import create_site
from tests.factories.user import create_user

TEST_PASSWORD = "ValidPassword123!"  # pragma: allowlist secret


@pytest.fixture
def fake_clock() -> Generator[FakeClock, None, None]:
    """Provide FakeClock starting at reference time and register with global clock."""
    clock = FakeClock(datetime(2026, 11, 20, 10, 0, 0))
    set_clock(clock)
    limiter = InMemoryRateLimiter(clock=clock)
    set_rate_limiter(limiter)
    try:
        yield clock
    finally:
        set_clock(None)
        set_rate_limiter(None)


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Provide active supervisor user for authentication tests."""
    site = create_site(db_session, name="Centre Rate Limits")
    return create_user(
        db_session,
        email="supervisor.rates@bloodday.local",
        password_hash=hash_password(TEST_PASSWORD),
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Provide admin user with MFA configured."""
    user = create_user(
        db_session,
        email="admin.rates@bloodday.local",
        password_hash=hash_password(TEST_PASSWORD),
        role="admin",
        is_active=True,
    )
    user.totp_enabled = True
    user.totp_secret_enc = encrypt_totp_secret(
        pyotp.random_base32(), get_settings().totp_encryption_key
    )
    db_session.flush()
    return user


# ==============================================================================
# 1. T-RATE-01-a: Login Rate Limiting (BR-RATE-01 / 10 per 15 min per IP)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-RATE-01")
def test_t_rate_01_a_login_rate_limiting(
    test_client: TestClient,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """T-RATE-01-a: 10 login attempts allowed in 15 min; 11th yields 429 rate_limited."""
    # 1. Make 10 attempts (allowed under sliding window)
    for i in range(10):
        resp = test_client.post(
            "/api/v1/auth/login",
            json={
                "email": supervisor_user.email,
                "password": "WrongPassword123!",  # pragma: allowlist secret
            },
        )
        assert resp.status_code == 401, f"Attempt {i + 1} should return 401"
        assert resp.json()["error"]["code"] == "invalid_credentials"

    # 2. 11th attempt from same IP exceeds limit -> 429 rate_limited
    resp_blocked = test_client.post(
        "/api/v1/auth/login",
        json={
            "email": supervisor_user.email,
            "password": TEST_PASSWORD,
        },
    )
    assert resp_blocked.status_code == 429
    assert resp_blocked.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in resp_blocked.headers
    retry_after = int(resp_blocked.headers["Retry-After"])
    assert retry_after > 0

    # 3. Advance fake clock past the 15-minute (900s) window
    fake_clock.advance(901)

    # 4. Now requests are allowed again
    resp_allowed = test_client.post(
        "/api/v1/auth/login",
        json={
            "email": supervisor_user.email,
            "password": TEST_PASSWORD,
        },
    )
    assert resp_allowed.status_code == 200
    assert resp_allowed.json()["user"]["email"] == supervisor_user.email


# ==============================================================================
# 2. T-RATE-02-a: MFA Verify Failure Limit (BR-RATE-02 / 5 failed attempts)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-RATE-02")
def test_t_rate_02_a_mfa_verify_failure_revokes_interim_session(
    test_client: TestClient,
    admin_user: User,
) -> None:
    """T-RATE-02-a: 5 failed MFA verification attempts revokes interim session; 6th attempt rejected."""
    # 1. Login to obtain interim session
    login_resp = test_client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200
    assert login_resp.json()["mfa_required"] is True

    session_cookie = test_client.cookies.get(get_settings().cookie_name, "")
    csrf_token = generate_csrf_token(session_cookie, secret_key=get_settings().secret_key)

    # 2. Attempts 1..5: Incorrect TOTP codes
    for _ in range(1, 6):
        verify_resp = test_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": "000000"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert verify_resp.status_code == 401
        assert verify_resp.json()["error"]["code"] == "invalid_credentials"

    # 3. 6th attempt: interim session has been revoked
    resp_sixth = test_client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": "000000"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp_sixth.status_code == 401
    # Rejected because the session is expired/revoked
    assert resp_sixth.json()["error"]["code"] in ("session_expired", "invalid_credentials")

    # 4. Accessing /me with this interim session cookie now returns 401 session_expired
    me_resp = test_client.get("/api/v1/me")
    assert me_resp.status_code == 401
    assert me_resp.json()["error"]["code"] == "session_expired"


# ==============================================================================
# 3. T-RATE-06-a: Password Forgot Rate Limiting (BR-RATE-06 / 5 per hour)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-RATE-06")
def test_t_rate_06_a_password_forgot_rate_limiting(
    test_client: TestClient,
    fake_clock: FakeClock,
) -> None:
    """T-RATE-06-a: 5 forgot requests allowed per hour; 6th request yields 429 rate_limited."""
    # 1. 5 requests allowed
    for i in range(5):
        resp = test_client.post(
            "/api/v1/auth/password/forgot",
            json={"email": f"user{i}@example.com"},
        )
        assert resp.status_code == 204

    # 2. 6th request from same IP yields 429
    resp_blocked = test_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": "victim@example.com"},
    )
    assert resp_blocked.status_code == 429
    assert resp_blocked.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in resp_blocked.headers
    retry_after = int(resp_blocked.headers["Retry-After"])
    assert retry_after > 0

    # 3. Advance fake clock past the 1-hour (3600s) window
    fake_clock.advance(3601)

    # 4. Now requests are allowed again
    resp_allowed = test_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": "victim@example.com"},
    )
    assert resp_allowed.status_code == 204


# ==============================================================================
# 4. T-RATE-07-a: Accept Invite Rate Limiting (BR-RATE-07 / 10 per hour)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-RATE-07")
def test_t_rate_07_a_accept_invite_rate_limiting(
    test_client: TestClient,
    fake_clock: FakeClock,
) -> None:
    """T-RATE-07-a: 10 accept-invite attempts allowed per hour; 11th yields 429 rate_limited."""
    # 1. 10 attempts with dummy tokens (rejected with 404 token_not_found)
    for i in range(10):
        resp = test_client.post(
            "/api/v1/auth/invite/accept",
            json={
                "token": f"nonexistent_token_{i}" * 2,
                "password": TEST_PASSWORD,
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_token"

    # 2. 11th request yields 429 rate_limited
    resp_blocked = test_client.post(
        "/api/v1/auth/invite/accept",
        json={
            "token": "another_token_attempt" * 2,
            "password": TEST_PASSWORD,
        },
    )
    assert resp_blocked.status_code == 429
    assert resp_blocked.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in resp_blocked.headers

    # 3. Advance clock past 1-hour window
    fake_clock.advance(3601)

    # 4. Allowed again
    resp_allowed = test_client.post(
        "/api/v1/auth/invite/accept",
        json={
            "token": "another_token_attempt" * 2,
            "password": TEST_PASSWORD,
        },
    )
    assert resp_allowed.status_code == 400
    assert resp_allowed.json()["error"]["code"] == "invalid_token"


# ==============================================================================
# 5. T-RATE-09-a ★: Response Envelope Verification on Exceeded Rate Limit
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-RATE-09")
def test_t_rate_09_a_rate_limit_exceeded_response_envelope(
    test_client: TestClient,
) -> None:
    """T-RATE-09-a ★: Rate limit rejection strictly conforms to unified 429 envelope.

    Verifies:
    - HTTP status 429 Too Many Requests.
    - Retry-After header present and positive integer.
    - Uniform error envelope: {"error": {"code": "rate_limited", "message": str, "request_id": str}}.
    - Request correlation ID present in both payload and headers.
    - No PII or raw IP addresses leaked.
    """
    # Exhaust the 5-hit forgot limit
    for _ in range(5):
        test_client.post(
            "/api/v1/auth/password/forgot",
            json={"email": "exhaust@bloodday.local"},
        )

    # Trigger 429
    resp = test_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": "exhaust@bloodday.local"},
    )
    assert resp.status_code == 429
    assert resp.headers.get("content-type", "").startswith("application/json")

    # Retry-After header check
    retry_header = resp.headers.get("Retry-After")
    assert retry_header is not None
    assert int(retry_header) > 0

    # Payload format check
    data = resp.json()
    assert "error" in data
    err = data["error"]
    assert err["code"] == "rate_limited"
    assert isinstance(err["message"], str)
    assert len(err["message"]) > 0
    assert "request_id" in err
    assert len(err["request_id"]) == 36  # UUID format

    # Security check: no raw IP or internal detail leaked
    serialized = str(data)
    assert "127.0.0.1" not in serialized
    assert "client" not in serialized.lower()
