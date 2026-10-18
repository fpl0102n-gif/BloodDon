"""Auth audit event integration tests (P3.12).

Per:
- docs/06-business-rules.md §13 (BR-AUDIT-01 no PII, BR-AUDIT-03 same transaction, BR-AUDIT-05 event catalog).
- docs/03-auth-permissions.md §17 (Auth audit events: login_success, login_failure, lockout, mfa_enabled, password_changed, password_reset, session_revoked).
- docs/07-test-cases.md §5.13:
  - T-AUDIT-01-a (★): Sentinel leak test across all recorded audit entries.
  - T-AUDIT-05-a: All approved auth action names verified in database.
"""

import json
from datetime import datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import generate_csrf_token
from app.core.clock import FakeClock
from app.core.config import get_settings
from app.core.security import (
    encrypt_totp_secret,
    generate_secure_token,
    hash_password,
    hash_token,
)
from app.models.audit import AuditLog
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.repositories.user_tokens import UserTokenRepository
from app.services.mfa import MfaService
from tests.factories.campaign import create_site
from tests.factories.user import create_user

TEST_PASSWORD = "ValidPassword123!"  # pragma: allowlist secret
NEW_PASSWORD = "NewValidPassword456!"  # pragma: allowlist secret

# Sentinel values per docs/07 §3.3
SENTINEL_FIRST_NAME = "ZZFIRST"
SENTINEL_LAST_NAME = "ZZLAST"
SENTINEL_PHONE = "0550123456"


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0))


@pytest.fixture
def sentinel_user(db_session: Session) -> User:
    """Create a user with sentinel name fields to test PII leakage prevention (T-AUDIT-01-a)."""
    site = create_site(db_session, name="Centre Sentinel Audit")
    return create_user(
        db_session,
        email="sentinel.user@bloodday.local",
        full_name=f"{SENTINEL_FIRST_NAME} {SENTINEL_LAST_NAME}",
        password_hash=hash_password(TEST_PASSWORD),
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def admin_totp_user(db_session: Session) -> tuple[User, str]:
    """Create an administrator with configured TOTP secret."""
    raw_secret = pyotp.random_base32()
    enc_secret = encrypt_totp_secret(raw_secret, get_settings().totp_encryption_key)
    user = create_user(
        db_session,
        email="admin.audit@bloodday.local",
        full_name="Admin Audit",
        password_hash=hash_password(TEST_PASSWORD),
        role="admin",
        site_id=None,
        is_active=True,
        totp_secret_enc=enc_secret,
        totp_enabled=True,
    )
    return user, raw_secret


# ==============================================================================
# 1. T-AUDIT-01-a ★: Sentinel Leak Detection Across Full Auth Flows
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-AUDIT-01")
def test_t_audit_01_a_sentinel_leak_check_across_full_auth_flows(
    test_client: TestClient,
    db_session: Session,
    sentinel_user: User,
) -> None:
    """Verify that after full authentication flows with sentinel data, NO PII appears in audit details.

    Per BR-AUDIT-01 ★ & T-AUDIT-01-a ★:
    "بعده، مسح audit_log: لا اسم ولا هاتف ولا فصيلة في أي details"
    """
    settings = get_settings()

    # Step 1: Failed login attempt (invalid_password)
    res_fail = test_client.post(
        "/api/v1/auth/login",
        json={
            "email": sentinel_user.email,
            "password": "WrongPassword999!",
        },  # pragma: allowlist secret
    )
    assert res_fail.status_code == 401

    # Step 2: Successful login
    res_login = test_client.post(
        "/api/v1/auth/login",
        json={"email": sentinel_user.email, "password": TEST_PASSWORD},
    )
    assert res_login.status_code == 200
    session_cookie = res_login.cookies.get(settings.cookie_name)
    assert session_cookie is not None

    csrf_token = generate_csrf_token(session_cookie, secret_key=settings.secret_key)
    headers = {"X-CSRF-Token": csrf_token}

    # Step 3: Change password
    res_pw = test_client.post(
        "/api/v1/me/password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": NEW_PASSWORD,
        },
        headers=headers,
    )
    assert res_pw.status_code == 204

    # Step 4: Logout
    res_logout = test_client.post(
        "/api/v1/auth/logout",
        headers=headers,
    )
    assert res_logout.status_code == 204

    # Step 5: Query ALL audit entries generated and verify absolute absence of sentinels & PII
    stmt = select(AuditLog)
    all_audit_logs = db_session.execute(stmt).scalars().all()
    assert len(all_audit_logs) >= 3

    for entry in all_audit_logs:
        if entry.details is not None:
            details_str = json.dumps(entry.details).upper()
            # Assert sentinel first and last names NEVER appear
            assert SENTINEL_FIRST_NAME not in details_str
            assert SENTINEL_LAST_NAME not in details_str
            assert SENTINEL_PHONE not in details_str

            # Assert passwords never appear
            assert TEST_PASSWORD.upper() not in details_str
            assert NEW_PASSWORD.upper() not in details_str

            # Assert blood types never appear
            for b_type in ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]:
                assert f'"{b_type}"' not in details_str


# ==============================================================================
# 2. Login Failure Reasons and Lockout Logging
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-05")
def test_audit_logs_login_failure_reasons_and_lockout(
    test_client: TestClient,
    db_session: Session,
    sentinel_user: User,
) -> None:
    """Verify that auth.login_failure records correct internal reasons without user leak, and auth.lockout records lock_minutes."""
    audit_repo = AuditRepository(db_session)

    # 1. Non-existent user -> user_not_found
    res = test_client.post(
        "/api/v1/auth/login",
        json={
            "email": "nobody@bloodday.local",
            "password": "AnyPassword123!",
        },  # pragma: allowlist secret
    )
    assert res.status_code == 401
    recent = audit_repo.list_events(action="auth.login_failure", limit=1)
    assert len(recent) == 1
    assert recent[0].details is not None
    assert recent[0].details["reason"] == "user_not_found"
    assert "ip_hash" in recent[0].details
    assert recent[0].entity_id is None

    # 2. Inactive user -> account_disabled
    sentinel_user.is_active = False
    db_session.flush()

    res_dis = test_client.post(
        "/api/v1/auth/login",
        json={"email": sentinel_user.email, "password": TEST_PASSWORD},
    )
    assert res_dis.status_code == 401
    recent = audit_repo.list_events(action="auth.login_failure", limit=1)
    assert recent[0].details is not None
    assert recent[0].details["reason"] == "account_disabled"
    assert recent[0].entity_id == sentinel_user.id

    sentinel_user.is_active = True
    db_session.flush()

    # 3. 5 failed login attempts -> auth.lockout + invalid_password
    for _ in range(5):
        res_fail = test_client.post(
            "/api/v1/auth/login",
            json={
                "email": sentinel_user.email,
                "password": "WrongPassword123!",
            },  # pragma: allowlist secret
        )
        assert res_fail.status_code == 401

    lockout_events = audit_repo.list_events(action="auth.lockout", entity_id=sentinel_user.id)
    assert len(lockout_events) >= 1
    assert lockout_events[0].details is not None
    assert lockout_events[0].details["lock_minutes"] == 15
    assert lockout_events[0].details["failed_attempts"] == 5

    # 4. Attempt while locked -> account_locked
    res_locked = test_client.post(
        "/api/v1/auth/login",
        json={"email": sentinel_user.email, "password": TEST_PASSWORD},
    )
    assert res_locked.status_code == 401
    recent = audit_repo.list_events(action="auth.login_failure", limit=1)
    assert recent[0].details is not None
    assert recent[0].details["reason"] == "account_locked"


# ==============================================================================
# 3. MFA Enrollment and Verification Audit Logs
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-05")
def test_audit_logs_mfa_events(
    test_client: TestClient,
    db_session: Session,
    admin_totp_user: tuple[User, str],
) -> None:
    """Verify that auth.login_success is recorded when MFA verification completes for interim session."""
    user, raw_secret = admin_totp_user
    settings = get_settings()
    audit_repo = AuditRepository(db_session)

    # 1. Login with password (interim session, mfa_required=True)
    res_login = test_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": TEST_PASSWORD},
    )
    assert res_login.status_code == 200
    assert res_login.json()["mfa_required"] is True

    session_cookie = res_login.cookies.get(settings.cookie_name)
    assert session_cookie is not None
    csrf_token = generate_csrf_token(session_cookie, secret_key=settings.secret_key)
    headers = {"X-CSRF-Token": csrf_token}

    # 2. Failed MFA verification -> auth.login_failure (invalid_mfa_code)
    res_mfa_bad = test_client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": "000000"},
        headers=headers,
    )
    assert res_mfa_bad.status_code == 401
    recent_fail = audit_repo.list_events(action="auth.login_failure", limit=1)
    assert recent_fail[0].details is not None
    assert recent_fail[0].details["reason"] == "invalid_mfa_code"

    # 3. Valid MFA verification -> auth.login_success
    totp = pyotp.TOTP(raw_secret)
    valid_code = totp.now()

    res_mfa_ok = test_client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": valid_code},
        headers=headers,
    )
    assert res_mfa_ok.status_code == 200
    assert res_mfa_ok.json()["mfa_required"] is False

    recent_success = audit_repo.list_events(action="auth.login_success", entity_id=user.id, limit=1)
    assert len(recent_success) >= 1
    assert recent_success[0].entity_id == user.id
    assert recent_success[0].actor_user_id == user.id


# ==============================================================================
# 4. Session Revocations (Logout-All and Reset)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-05")
def test_audit_logs_session_revoked_logout_all(
    test_client: TestClient,
    db_session: Session,
    sentinel_user: User,
) -> None:
    """Verify auth.session_revoked is logged when logout-all is invoked."""
    settings = get_settings()
    audit_repo = AuditRepository(db_session)

    res_login = test_client.post(
        "/api/v1/auth/login",
        json={"email": sentinel_user.email, "password": TEST_PASSWORD},
    )
    session_cookie = res_login.cookies.get(settings.cookie_name)
    csrf_token = generate_csrf_token(session_cookie, secret_key=settings.secret_key)

    res_logout_all = test_client.post(
        "/api/v1/auth/logout-all",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res_logout_all.status_code == 204

    events = audit_repo.list_events(
        action="auth.session_revoked", entity_id=sentinel_user.id, limit=1
    )
    assert len(events) >= 1
    assert events[0].details is not None
    assert events[0].details["reason"] == "logout_all"
    assert events[0].actor_user_id == sentinel_user.id


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-05")
def test_audit_logs_mfa_enabled_on_confirm(
    test_client: TestClient,
    db_session: Session,
    sentinel_user: User,
) -> None:
    """Verify auth.mfa_enabled is recorded with method=totp when user confirms MFA setup."""
    settings = get_settings()
    audit_repo = AuditRepository(db_session)

    # 1. Login as supervisor
    res_login = test_client.post(
        "/api/v1/auth/login",
        json={"email": sentinel_user.email, "password": TEST_PASSWORD},
    )
    session_cookie = res_login.cookies.get(settings.cookie_name)
    csrf_token = generate_csrf_token(session_cookie, secret_key=settings.secret_key)
    headers = {"X-CSRF-Token": csrf_token}

    # 2. Initiate setup
    res_setup = test_client.post(
        "/api/v1/me/mfa/setup",
        headers=headers,
    )
    assert res_setup.status_code == 200
    secret = res_setup.json()["secret"]

    # 3. Confirm with valid TOTP code
    totp = pyotp.TOTP(secret)
    code = totp.now()

    res_confirm = test_client.post(
        "/api/v1/me/mfa/confirm",
        json={"code": code},
        headers=headers,
    )
    assert res_confirm.status_code == 204

    # 4. Check audit log
    events = audit_repo.list_events(action="auth.mfa_enabled", entity_id=sentinel_user.id, limit=1)
    assert len(events) >= 1
    assert events[0].details == {"method": "totp"}
    assert events[0].actor_user_id == sentinel_user.id


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-05")
def test_audit_logs_password_reset_and_session_revoked(
    test_client: TestClient,
    db_session: Session,
    sentinel_user: User,
    fake_clock: FakeClock,
) -> None:
    """Verify auth.password_reset and auth.session_revoked are logged when completing password reset."""
    audit_repo = AuditRepository(db_session)
    token_repo = UserTokenRepository(db_session)

    # Generate active reset token
    raw_token = generate_secure_token(32)
    token_hash = hash_token(raw_token)
    token_repo.create_token(
        user_id=sentinel_user.id,
        purpose="password_reset",
        token_hash=token_hash,
        expires_at=fake_clock.now() + timedelta(hours=1),
    )

    res = test_client.post(
        "/api/v1/auth/password/reset",
        json={
            "token": raw_token,
            "new_password": NEW_PASSWORD,
        },
    )
    assert res.status_code == 204

    # Check auth.password_reset
    reset_events = audit_repo.list_events(
        action="auth.password_reset", entity_id=sentinel_user.id, limit=1
    )
    assert len(reset_events) >= 1
    assert reset_events[0].details == {"method": "token"}

    # Check auth.session_revoked
    revoked_events = audit_repo.list_events(
        action="auth.session_revoked", entity_id=sentinel_user.id, limit=1
    )
    assert len(revoked_events) >= 1
    assert revoked_events[0].details is not None
    assert revoked_events[0].details["reason"] == "password_reset"


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-05")
def test_audit_logs_session_revoked_on_5_interim_mfa_failures(
    test_client: TestClient,
    db_session: Session,
    admin_totp_user: tuple[User, str],
) -> None:
    """Verify auth.session_revoked with reason='max_mfa_attempts' when 5 consecutive MFA failures occur."""
    user, _ = admin_totp_user
    settings = get_settings()
    audit_repo = AuditRepository(db_session)

    # Clear any previous interim attempts
    MfaService.clear_all_interim_attempts()

    res_login = test_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": TEST_PASSWORD},
    )
    session_cookie = res_login.cookies.get(settings.cookie_name)
    csrf_token = generate_csrf_token(session_cookie, secret_key=settings.secret_key)
    headers = {"X-CSRF-Token": csrf_token}

    # 5 failed MFA verification attempts
    for _ in range(5):
        test_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": "000000"},
            headers=headers,
        )

    # Verify auth.session_revoked is logged with reason='max_mfa_attempts'
    revoked_events = audit_repo.list_events(
        action="auth.session_revoked", entity_id=user.id, limit=5
    )
    max_mfa_event = next(
        (e for e in revoked_events if e.details and e.details.get("reason") == "max_mfa_attempts"),
        None,
    )
    assert max_mfa_event is not None
    assert max_mfa_event.entity_id == user.id
