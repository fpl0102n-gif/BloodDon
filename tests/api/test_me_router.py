"""API integration tests for /me routes (P3.9).

Traces to:
- docs/04-api.md §10 & docs/03-auth-permissions.md §11.4.
- T-AUTH-13 (★): Password change invalidates other sessions while preserving current session.
- T-AUTH-16: Authenticated password change inside /me/password.
- T-AUTH-17: MFA disable restricted to supervisor with step-up confirmation (R); admin prohibited.
"""

from datetime import UTC, datetime

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    generate_csrf_token,
    record_session_reauth,
)
from app.core.clock import FakeClock
from app.core.config import get_settings
from app.core.security import encrypt_totp_secret, hash_password
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user

TEST_PASSWORD = "OldPassword123!"  # pragma: allowlist secret


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0))


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    site = create_site(db_session, name="Centre Test MeRouter")
    return create_user(
        db_session,
        email="supervisor.me@bloodday.local",
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
        email="admin.me@bloodday.local",
        password_hash=hash_password(TEST_PASSWORD),
        role="admin",
        site_id=None,
        is_active=True,
    )
    user.totp_secret_enc = enc_secret
    user.totp_enabled = True
    db_session.flush()
    return user


def create_authenticated_session(
    db_session: Session,
    user: User,
    *,
    mfa_verified: bool = True,
) -> str:
    repo = SessionRepository(db_session)
    svc = SessionService(repo, settings=get_settings())
    raw_token, _session = svc.create_session(
        user.id,
        user.role,
        mfa_verified=mfa_verified,
    )
    return raw_token


# ==============================================================================
# 1. Profile Inspection Tests (GET /me)
# ==============================================================================


@pytest.mark.api
def test_get_me_unauthenticated_returns_401(test_client: TestClient) -> None:
    """GET /me without session cookie returns 401 not_authenticated."""
    resp = test_client.get("/api/v1/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.api
def test_get_me_authenticated_supervisor(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """GET /me returns caller profile without leaking secrets or password hashes."""
    token = create_authenticated_session(db_session, supervisor_user)
    test_client.cookies.set(get_settings().cookie_name, token)

    resp = test_client.get("/api/v1/me")
    assert resp.status_code == 200
    data = resp.json()

    assert data["email"] == supervisor_user.email
    assert data["role"] == "supervisor"
    assert data["site_id"] == str(supervisor_user.site_id)
    assert data["mfa_enabled"] is False

    # Security verification: no password hash or internal secret fields returned
    assert "password_hash" not in data
    assert "totp_secret_enc" not in data


# ==============================================================================
# 2. Password Change Tests (POST /me/password - T-AUTH-16 & T-AUTH-13)
# ==============================================================================


@pytest.mark.api
def test_change_password_invalid_current_password(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """POST /me/password with wrong current password returns 401 invalid_credentials."""
    token = create_authenticated_session(db_session, supervisor_user)
    test_client.cookies.set(get_settings().cookie_name, token)
    csrf_token = generate_csrf_token(token, secret_key=get_settings().secret_key)

    resp = test_client.post(
        "/api/v1/me/password",
        json={
            "current_password": "WrongPassword123!",  # pragma: allowlist secret
            "new_password": "ValidNewPassword123!",  # pragma: allowlist secret
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.api
def test_change_password_weak_password_rejected(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """POST /me/password with short/weak new password returns 422 validation_error."""
    token = create_authenticated_session(db_session, supervisor_user)
    test_client.cookies.set(get_settings().cookie_name, token)
    csrf_token = generate_csrf_token(token, secret_key=get_settings().secret_key)

    resp = test_client.post(
        "/api/v1/me/password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": "short",  # pragma: allowlist secret
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 422


@pytest.mark.api
@pytest.mark.critical
def test_t_auth_16_change_password_revokes_other_sessions(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """T-AUTH-16: Successful password change revokes other sessions; current session remains active."""
    repo = SessionRepository(db_session)
    svc = SessionService(repo, settings=get_settings())

    # Create 2 sessions for supervisor
    token1, _session1 = svc.create_session(supervisor_user.id, "supervisor")
    token2, _session2 = svc.create_session(supervisor_user.id, "supervisor")

    new_password = "UpdatedPassword123!"  # pragma: allowlist secret

    # Use session 1 to change password
    test_client.cookies.set(get_settings().cookie_name, token1)
    csrf_token = generate_csrf_token(token1, secret_key=get_settings().secret_key)

    resp = test_client.post(
        "/api/v1/me/password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": new_password,
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 204

    # Current session (session 1) remains valid
    resp_curr = test_client.get("/api/v1/me")
    assert resp_curr.status_code == 200

    # Other session (session 2) is revoked
    test_client.cookies.set(get_settings().cookie_name, token2)
    resp_other = test_client.get("/api/v1/me")
    assert resp_other.status_code == 401

    # Clear revoked cookie
    test_client.cookies.clear()

    # Login with new password succeeds
    login_resp = test_client.post(
        "/api/v1/auth/login",
        json={"email": supervisor_user.email, "password": new_password},
    )
    assert login_resp.status_code == 200


# ==============================================================================
# 3. MFA Setup & Confirmation Tests
# ==============================================================================


@pytest.mark.api
def test_mfa_setup_and_confirmation_flow(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """POST /me/mfa/setup followed by POST /me/mfa/confirm enrolls supervisor in TOTP."""
    token = create_authenticated_session(db_session, supervisor_user)
    test_client.cookies.set(get_settings().cookie_name, token)
    csrf_token = generate_csrf_token(token, secret_key=get_settings().secret_key)

    # 1. Setup
    setup_resp = test_client.post(
        "/api/v1/me/mfa/setup",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert setup_resp.status_code == 200
    setup_data = setup_resp.json()
    assert "secret" in setup_data
    assert "provisioning_uri" in setup_data
    assert len(setup_data["recovery_codes"]) == 8

    # 2. Confirm with valid code
    totp = pyotp.TOTP(setup_data["secret"])
    confirm_resp = test_client.post(
        "/api/v1/me/mfa/confirm",
        json={"code": totp.now()},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert confirm_resp.status_code == 204

    # 3. Check /me reflects mfa_enabled=True
    me_resp = test_client.get("/api/v1/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["mfa_enabled"] is True


# ==============================================================================
# 4. MFA Disable Tests (T-AUTH-17)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
def test_t_auth_17_admin_cannot_disable_mfa(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-AUTH-17: Admin attempting to disable MFA is strictly rejected with 403 forbidden."""
    token = create_authenticated_session(db_session, admin_user, mfa_verified=True)
    test_client.cookies.set(get_settings().cookie_name, token)
    csrf_token = generate_csrf_token(token, secret_key=get_settings().secret_key)

    # Disable endpoint demands step-up R -> give fresh reauth
    repo = SessionRepository(db_session)
    from app.core.security import hash_token

    session_entity = repo.get_by_id(hash_token(token))
    if session_entity:
        record_session_reauth(session_entity.id, datetime.now(UTC))

    resp = test_client.post(
        "/api/v1/me/mfa/disable",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.api
def test_t_auth_17_supervisor_can_disable_mfa_with_fresh_reauth(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """T-AUTH-17: Supervisor can disable MFA when step-up confirmation (R) is valid."""
    supervisor_user.totp_enabled = True
    supervisor_user.totp_secret_enc = encrypt_totp_secret(
        pyotp.random_base32(), get_settings().totp_encryption_key
    )
    db_session.flush()

    token = create_authenticated_session(db_session, supervisor_user)
    test_client.cookies.set(get_settings().cookie_name, token)
    csrf_token = generate_csrf_token(token, secret_key=get_settings().secret_key)

    # Set fresh reauth timestamp
    repo = SessionRepository(db_session)
    from app.core.security import hash_token

    session_entity = repo.get_by_id(hash_token(token))
    assert session_entity is not None
    record_session_reauth(session_entity.id, datetime.now(UTC))

    resp = test_client.post(
        "/api/v1/me/mfa/disable",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 204

    # Verify MFA is now disabled on account
    me_resp = test_client.get("/api/v1/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["mfa_enabled"] is False
