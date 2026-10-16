"""API integration tests for /auth routes (P3.9).

Traces to:
- docs/04-api.md §9 & docs/03-auth-permissions.md §5-§9.
- BR-AUTH-05 (uniform login error), BR-AUTH-06 (lockout), BR-AUTH-08 (MFA requirement),
  BR-AUTH-11 (server sessions), BR-AUTH-13 (session revocation triggers),
  BR-AUTH-14 (step-up reauth), BR-AUTH-15 (password reset).
- T-AUTH-13 (★): Session revocation on logout, logout-all, and password reset.
"""

from datetime import datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    generate_csrf_token,
)
from app.core.clock import FakeClock
from app.core.config import get_settings
from app.core.security import encrypt_totp_secret, hash_password
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user

TEST_PASSWORD = "ValidPassword123!"  # pragma: allowlist secret


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0))


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    site = create_site(db_session, name="Centre Test AuthRouter")
    return create_user(
        db_session,
        email="supervisor.router@bloodday.local",
        password_hash=hash_password(TEST_PASSWORD),
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def admin_user(db_session: Session) -> User:
    raw_secret = pyotp.random_base32()
    enc_secret = encrypt_totp_secret(raw_secret, get_settings().totp_encryption_key)
    user = create_user(
        db_session,
        email="admin.router@bloodday.local",
        password_hash=hash_password(TEST_PASSWORD),
        role="admin",
        site_id=None,
        is_active=True,
    )
    user.totp_secret_enc = enc_secret
    user.totp_enabled = True
    db_session.flush()
    return user


# ==============================================================================
# 1. CSRF Endpoint Tests
# ==============================================================================


@pytest.mark.api
def test_get_csrf_sets_cookie_and_returns_token(test_client: TestClient) -> None:
    """GET /auth/csrf returns valid CSRF token and sets session cookie if absent."""
    resp = test_client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    data = resp.json()
    assert "csrf_token" in data
    assert len(data["csrf_token"]) == 64

    # Cookie is attached to the response
    cookie_name = get_settings().cookie_name
    assert cookie_name in resp.cookies or f"__Host-{cookie_name}" in resp.cookies


# ==============================================================================
# 2. Login Flow Tests
# ==============================================================================


@pytest.mark.api
def test_login_supervisor_success(
    test_client: TestClient,
    supervisor_user: User,
) -> None:
    """POST /auth/login with valid supervisor credentials returns 200 and mfa_required=False."""
    resp = test_client.post(
        "/api/v1/auth/login",
        json={"email": supervisor_user.email, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == supervisor_user.email
    assert data["user"]["role"] == "supervisor"
    assert data["mfa_required"] is False

    # Cookie is set
    assert get_settings().cookie_name in resp.cookies


@pytest.mark.api
def test_login_admin_requires_mfa(
    test_client: TestClient,
    admin_user: User,
) -> None:
    """POST /auth/login for administrator returns mfa_required=True and issues interim session."""
    resp = test_client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == admin_user.email
    assert data["user"]["role"] == "admin"
    assert data["mfa_required"] is True


@pytest.mark.api
def test_login_invalid_credentials_returns_uniform_401(
    test_client: TestClient,
    supervisor_user: User,
) -> None:
    """POST /auth/login with bad password returns uniform 401 invalid_credentials."""
    resp = test_client.post(
        "/api/v1/auth/login",
        json={
            "email": supervisor_user.email,
            "password": "WrongPassword123!",  # pragma: allowlist secret
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


# ==============================================================================
# 3. MFA Verification Flow Tests
# ==============================================================================


@pytest.mark.api
def test_mfa_verify_promotes_interim_session(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """POST /auth/mfa/verify promotes interim session to mfa_verified=True."""
    # 1. Login to obtain interim session
    login_resp = test_client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200
    assert login_resp.json()["mfa_required"] is True

    # 2. Generate valid TOTP code
    raw_secret = pyotp.random_base32()
    admin_user.totp_secret_enc = encrypt_totp_secret(raw_secret, get_settings().totp_encryption_key)
    db_session.flush()
    totp = pyotp.TOTP(raw_secret)
    valid_code = totp.now()

    # 3. Verify MFA code
    csrf_token = generate_csrf_token(
        test_client.cookies.get(get_settings().cookie_name, ""),
        secret_key=get_settings().secret_key,
    )
    verify_resp = test_client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": valid_code},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert verify_resp.status_code == 200
    assert verify_resp.json()["mfa_required"] is False


# ==============================================================================
# 4. Step-Up Re-Authentication Tests
# ==============================================================================


@pytest.mark.api
def test_reauth_renews_step_up_window(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """POST /auth/reauth confirms password and refreshes step-up timestamp."""
    # Login
    login_resp = test_client.post(
        "/api/v1/auth/login",
        json={"email": supervisor_user.email, "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200
    session_token = test_client.cookies.get(get_settings().cookie_name, "")
    csrf_token = generate_csrf_token(session_token, secret_key=get_settings().secret_key)

    # Reauth call
    reauth_resp = test_client.post(
        "/api/v1/auth/reauth",
        json={"password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert reauth_resp.status_code == 204


# ==============================================================================
# 5. Logout & Revocation Tests (T-AUTH-13 ★)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
def test_t_auth_13_single_logout_revokes_current_session(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """T-AUTH-13 (★): Single logout revokes the caller session immediately."""
    # 1. Login
    login_resp = test_client.post(
        "/api/v1/auth/login",
        json={"email": supervisor_user.email, "password": TEST_PASSWORD},
    )
    assert login_resp.status_code == 200
    session_token = test_client.cookies.get(get_settings().cookie_name, "")
    csrf_token = generate_csrf_token(session_token, secret_key=get_settings().secret_key)

    # 2. Logout
    logout_resp = test_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert logout_resp.status_code == 204

    # 3. Subsequent request with the same session token fails
    test_client.cookies.set(get_settings().cookie_name, session_token)
    check_resp = test_client.get("/api/v1/me")
    assert check_resp.status_code == 401


@pytest.mark.api
@pytest.mark.critical
def test_t_auth_13_logout_all_revokes_all_devices(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """T-AUTH-13 (★): Logout-all revokes all sessions for the user across all devices."""
    repo = SessionRepository(db_session)
    svc = SessionService(repo, settings=get_settings())

    # Create session 1 (e.g. mobile)
    token1, _session1 = svc.create_session(supervisor_user.id, "supervisor")
    # Create session 2 (e.g. laptop)
    token2, _session2 = svc.create_session(supervisor_user.id, "supervisor")

    # Use session 1 to call logout-all
    test_client.cookies.set(get_settings().cookie_name, token1)
    csrf_token = generate_csrf_token(token1, secret_key=get_settings().secret_key)
    resp = test_client.post(
        "/api/v1/auth/logout-all",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 204

    # Session 2 is also dead
    test_client.cookies.set(get_settings().cookie_name, token2)
    check_resp = test_client.get("/api/v1/me")
    assert check_resp.status_code == 401


# ==============================================================================
# 6. Password Reset Flow Tests
# ==============================================================================


@pytest.mark.api
def test_forgot_password_uniform_response(
    test_client: TestClient,
    supervisor_user: User,
) -> None:
    """POST /auth/password/forgot returns 204 for both registered and unregistered emails."""
    # Existing user
    resp1 = test_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": supervisor_user.email},
    )
    assert resp1.status_code == 204

    # Non-existing user
    resp2 = test_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": "nonexistent@bloodday.local"},
    )
    assert resp2.status_code == 204


@pytest.mark.api
@pytest.mark.critical
def test_t_auth_13_password_reset_revokes_all_sessions(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-13 (★): Resetting password via token invalidates all existing user sessions."""
    repo = SessionRepository(db_session)
    svc = SessionService(repo, clock=fake_clock, settings=get_settings())
    raw_token, _session = svc.create_session(supervisor_user.id, "supervisor")

    # Generate reset token in DB directly
    from app.core.security import generate_secure_token, hash_token
    from app.repositories.user_tokens import UserTokenRepository

    token_repo = UserTokenRepository(db_session)
    reset_token_raw = generate_secure_token(32)
    token_repo.create_token(
        user_id=supervisor_user.id,
        purpose="password_reset",
        token_hash=hash_token(reset_token_raw),
        expires_at=fake_clock.now() + timedelta(hours=1),
    )
    db_session.flush()

    new_password = "BrandNewPassword123!"  # pragma: allowlist secret
    reset_resp = test_client.post(
        "/api/v1/auth/password/reset",
        json={"token": reset_token_raw, "new_password": new_password},
    )
    assert reset_resp.status_code == 204

    # Prior active session is now revoked
    test_client.cookies.set(get_settings().cookie_name, raw_token)
    check_resp = test_client.get("/api/v1/me")
    assert check_resp.status_code == 401

    test_client.cookies.clear()

    # Can now login with new password
    login_resp = test_client.post(
        "/api/v1/auth/login",
        json={"email": supervisor_user.email, "password": new_password},
    )
    assert login_resp.status_code == 200
