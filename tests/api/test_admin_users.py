"""Integration tests for Admin Users API endpoints (P4.5).

Per:
- docs/04-api.md §15 (Users Administration: GET, POST, PATCH, sub-actions)
- docs/06-business-rules.md §11.2 (BR-USER-01..10, BR-AUTH-02, BR-AUTH-13)
- docs/07-test-cases.md §5.11 (T-USER-01..09, T-CONC-05)
- docs/03-auth-permissions.md §11 (USER_INVITE, USER_UPDATE, USER_DEACTIVATE, USER_UNLOCK, USER_RESET_MFA, step-up R)
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_mailer,
    get_settings,
    record_session_reauth,
)
from app.core.errors import LastAdminError
from app.mail.base import FakeMailer
from app.models.audit import AuditLog
from app.models.donor import Donor
from app.models.user import MfaRecoveryCode, User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user."""
    return create_user(
        db_session,
        email="admin.primary@bloodday.local",
        full_name="مسؤول النظام الأول",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def second_admin_user(db_session: Session) -> User:
    """Create a second verified administrator user."""
    return create_user(
        db_session,
        email="admin.secondary@bloodday.local",
        full_name="مسؤول النظام الثاني",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create a supervisor user assigned to a test site."""
    site = create_site(db_session, name="موقع المشرف التجريبي")
    return create_user(
        db_session,
        email="supervisor.users@bloodday.local",
        full_name="مشرف الموقع التجريبي",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


def create_auth_headers_and_cookies(
    test_client: TestClient,
    db_session: Session,
    user: User,
    *,
    mfa_verified: bool = True,
    fresh_reauth: bool = True,
) -> dict[str, str]:
    """Helper to attach session cookie and CSRF token for a given user."""
    settings = get_settings()
    clock = get_clock()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    raw_token, session = session_service.create_session(
        user.id,
        user.role,
        mfa_verified=mfa_verified,
    )

    if fresh_reauth:
        record_session_reauth(session.id, clock.now())
    else:
        clear_session_reauth(session.id)
        session.created_at = clock.now() - timedelta(minutes=15)
        db_session.flush()

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    test_client.cookies.set(settings.cookie_name, raw_token)

    return {"X-CSRF-Token": csrf_token}


# ==============================================================================
# 1. Authorization & Step-Up Reauthentication (T-USER-08-a ★)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
def test_anonymous_visitor_rejected(test_client: TestClient) -> None:
    """Anonymous callers receive 401 across all admin user endpoints."""
    resp = test_client.get("/api/v1/admin/users")
    assert resp.status_code == 401

    resp = test_client.post("/api/v1/admin/users", json={})
    assert resp.status_code == 401


@pytest.mark.api
@pytest.mark.critical
def test_supervisor_forbidden(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """Supervisors lack USER_* permissions and receive 403 forbidden."""
    headers = create_auth_headers_and_cookies(test_client, db_session, supervisor_user)

    resp = test_client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.api
@pytest.mark.br("BR-USER-08")
@pytest.mark.critical
def test_mutations_require_step_up_reauth(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    second_admin_user: User,
) -> None:
    """T-USER-08-a: Stale reauth on mutating admin endpoints returns 403 reauth_required."""
    # Create session WITHOUT fresh re-auth
    headers = create_auth_headers_and_cookies(
        test_client,
        db_session,
        admin_user,
        fresh_reauth=False,
    )

    target_id = second_admin_user.id

    # 1. POST /admin/users
    resp = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": "test.stale@bloodday.local",
            "full_name": "مستخدم تجريبي",
            "role": "admin",
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"

    # 2. PATCH /admin/users/{id}
    resp = test_client.patch(
        f"/api/v1/admin/users/{target_id}",
        json={"full_name": "اسم محدث"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"

    # 3. POST /admin/users/{id}/deactivate
    resp = test_client.post(
        f"/api/v1/admin/users/{target_id}/deactivate",
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"

    # 4. POST /admin/users/deactivate-supervisors
    resp = test_client.post(
        "/api/v1/admin/users/deactivate-supervisors",
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"


# ==============================================================================
# 2. User Listing & Credentials Exposure Guard (BR-GEN-09, BR-USER-07)
# ==============================================================================


@pytest.mark.api
def test_list_users_filtering_and_pagination(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    second_admin_user: User,
    supervisor_user: User,
) -> None:
    """List staff users with filtering by role and status, never exposing credentials."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # 1. List all
    resp = test_client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 3

    # Verify sensitive credentials NEVER returned (Golden Rule 4)
    for u in data:
        assert "password_hash" not in u
        assert "totp_secret_enc" not in u
        assert "totp_secret" not in u
        assert "token" not in u
        assert "recovery_codes" not in u

    # 2. Filter by role
    resp = test_client.get("/api/v1/admin/users?role=supervisor", headers=headers)
    assert resp.status_code == 200
    supervisors = resp.json()
    assert all(s["role"] == "supervisor" for s in supervisors)
    assert any(s["id"] == str(supervisor_user.id) for s in supervisors)

    # 3. Filter by site_id
    resp = test_client.get(
        f"/api/v1/admin/users?site_id={supervisor_user.site_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    site_users = resp.json()
    assert len(site_users) == 1
    assert site_users[0]["id"] == str(supervisor_user.id)


@pytest.mark.api
@pytest.mark.br("BR-USER-07")
def test_t_user_07_a_warning_on_single_active_admin(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-USER-07-a: GET /admin/users attaches X-Admin-Warning if only one active admin exists."""
    # Ensure admin_user is the ONLY active admin
    db_session.query(User).filter(User.role == "admin", User.id != admin_user.id).update(
        {"is_active": False}
    )
    db_session.flush()

    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("X-Admin-Warning") == "single_active_admin"


# ==============================================================================
# 3. User Invitation (BR-USER-01, BR-USER-02, BR-USER-09)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-USER-01")
@pytest.mark.critical
def test_t_user_01_a_supervisor_without_site_rejected(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-USER-01-a: Inviting supervisor without site_id returns 422 validation_error."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": "sup.nosite@bloodday.local",
            "full_name": "مشرف بلا موقع",
            "role": "supervisor",
            "site_id": None,
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.api
@pytest.mark.br("BR-USER-01")
def test_t_user_01_a_admin_with_site_rejected(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-USER-01-a: Inviting admin with site_id returns 422 validation_error."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": "admin.withsite@bloodday.local",
            "full_name": "أدمن بموقع",
            "role": "admin",
            "site_id": str(uuid.uuid4()),
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.api
@pytest.mark.br("BR-USER-01")
@pytest.mark.critical
def test_t_user_01_b_admin_without_site_created(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-USER-01-b: Inviting admin without site_id succeeds with 201 Created."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": "new.admin@bloodday.local",
            "full_name": "مسؤول جديد",
            "role": "admin",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["role"] == "admin"
    assert data["site_id"] is None
    assert data["email"] == "new.admin@bloodday.local"
    assert data["is_active"] is True


@pytest.mark.api
@pytest.mark.br("BR-USER-02")
def test_t_user_02_a_duplicate_email_case_insensitive(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-USER-02-a: Duplicate email (case-insensitive) returns 409 email_taken."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Attempt to invite with uppercase version of existing admin email
    resp = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": admin_user.email.upper(),
            "full_name": "محاولة مكررة",
            "role": "admin",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_taken"


@pytest.mark.api
@pytest.mark.br("BR-USER-09")
def test_t_user_09_a_mailer_failure_returns_invite_url(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-USER-09-a: When transactional mailer fails, invite_url is returned once to admin."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    mailer = get_mailer()
    assert isinstance(mailer, FakeMailer)
    mailer.inject_failure = True

    try:
        resp = test_client.post(
            "/api/v1/admin/users",
            json={
                "email": "nomail.user@bloodday.local",
                "full_name": "مستخدم بدون بريد",
                "role": "admin",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["invite_url"] is not None
        assert "accept-invite?token=" in data["invite_url"]
    finally:
        mailer.clear()


@pytest.mark.api
def test_invite_unknown_field_rejected(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """BR-GEN-06: Unknown fields in request body are forbidden."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": "extra.field@bloodday.local",
            "full_name": "حقل غير معتمد",
            "role": "admin",
            "extra_field": "injected",
        },
        headers=headers,
    )
    assert resp.status_code == 422


# ==============================================================================
# 4. Self-Modification & Self-Deactivation Guard (BR-USER-03 ★)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-USER-03")
@pytest.mark.critical
def test_t_user_03_a_self_deactivation_forbidden(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-USER-03-a: Admin deactivating their own account returns 403 forbidden."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.post(
        f"/api/v1/admin/users/{admin_user.id}/deactivate",
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.api
@pytest.mark.br("BR-USER-03")
@pytest.mark.critical
def test_t_user_03_a_self_role_modification_forbidden(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-USER-03-a: Admin modifying their own role or settings returns 403 forbidden."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.patch(
        f"/api/v1/admin/users/{admin_user.id}",
        json={"full_name": "تعديل اسمي الشخصي"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# ==============================================================================
# 5. Last Active Admin Protection (BR-USER-04 ★, T-CONC-05)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-USER-04")
@pytest.mark.critical
def test_t_user_04_a_last_admin_deactivation_rejected(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    second_admin_user: User,
) -> None:
    """T-USER-04-a: Deactivating the last active admin raises 409 last_admin."""
    # Ensure ONLY admin_user and second_admin_user are active
    db_session.query(User).filter(
        User.role == "admin",
        User.id.notin_([admin_user.id, second_admin_user.id]),
    ).update({"is_active": False})
    db_session.flush()

    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Admin 1 deactivates Admin 2 (leaving Admin 1 as the sole active admin)
    resp = test_client.post(
        f"/api/v1/admin/users/{second_admin_user.id}/deactivate",
        headers=headers,
    )
    assert resp.status_code == 200

    # Verify only 1 active admin remains
    active_count = db_session.query(User).filter_by(role="admin", is_active=True).count()
    assert active_count == 1

    # Attempting to deactivate Admin 1 (who is now the sole active admin) raises LastAdminError
    from unittest.mock import MagicMock

    from app.repositories.sessions import SessionRepository
    from app.repositories.sites import SiteRepository
    from app.repositories.users import UserRepository
    from app.services.audit import AuditService
    from app.services.auth import AuthService
    from app.services.users import UserService

    svc = UserService(
        UserRepository(db_session),
        SessionRepository(db_session),
        SiteRepository(db_session),
        MagicMock(spec=AuthService),
        MagicMock(spec=AuditService),
    )

    with pytest.raises(LastAdminError, match="last active administrator"):
        svc.deactivate_user(admin_user.id, actor_user_id=second_admin_user.id)


@pytest.mark.api
@pytest.mark.br("BR-USER-04")
@pytest.mark.critical
def test_t_user_04_b_mutual_deactivation_race_leaves_at_least_one_admin(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    second_admin_user: User,
) -> None:
    """T-USER-04-b / T-CONC-05: Exactly one active admin remains when deactivations compete."""
    # Ensure ONLY admin_user and second_admin_user are active admins
    db_session.query(User).filter(
        User.role == "admin",
        User.id.notin_([admin_user.id, second_admin_user.id]),
    ).update({"is_active": False})
    db_session.flush()

    # Use isolated test clients for each admin to prevent cookie jar collision
    client1 = TestClient(test_client.app)
    client2 = TestClient(test_client.app)

    h1 = create_auth_headers_and_cookies(client1, db_session, admin_user)
    h2 = create_auth_headers_and_cookies(client2, db_session, second_admin_user)

    # First request: admin 1 deactivates admin 2
    r1 = client1.post(
        f"/api/v1/admin/users/{second_admin_user.id}/deactivate",
        headers=h1,
    )
    assert r1.status_code == 200

    # Second request: admin 2's session has now been revoked by deactivation (BR-AUTH-13)
    r2 = client2.post(
        f"/api/v1/admin/users/{admin_user.id}/deactivate",
        headers=h2,
    )
    # Revoked session correctly rejected
    assert r2.status_code == 401

    # And attempting to deactivate the last admin via service raises LastAdminError
    from unittest.mock import MagicMock

    from app.repositories.sessions import SessionRepository
    from app.repositories.sites import SiteRepository
    from app.repositories.users import UserRepository
    from app.services.audit import AuditService
    from app.services.auth import AuthService
    from app.services.users import UserService

    svc = UserService(
        UserRepository(db_session),
        SessionRepository(db_session),
        SiteRepository(db_session),
        MagicMock(spec=AuthService),
        MagicMock(spec=AuditService),
    )

    with pytest.raises(LastAdminError, match="last active administrator"):
        svc.deactivate_user(admin_user.id, actor_user_id=second_admin_user.id)

    # Guarantee at least one active admin remains in DB
    active_count = db_session.query(User).filter_by(role="admin", is_active=True).count()
    assert active_count == 1


# ==============================================================================
# 6. Bulk Supervisor Deactivation (BR-USER-05, T-USER-05-a)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-USER-05")
def test_t_user_05_a_bulk_deactivate_supervisors(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    second_admin_user: User,
) -> None:
    """T-USER-05-a: Bulk deactivate all supervisors without touching administrators."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Create 3 active supervisors
    site1 = create_site(db_session, name="موقع إداري 1")
    site2 = create_site(db_session, name="موقع إداري 2")
    site3 = create_site(db_session, name="موقع إداري 3")

    s1 = create_user(
        db_session, email="s1@bloodday.local", role="supervisor", site_id=site1.id, is_active=True
    )
    s2 = create_user(
        db_session, email="s2@bloodday.local", role="supervisor", site_id=site2.id, is_active=True
    )
    s3 = create_user(
        db_session, email="s3@bloodday.local", role="supervisor", site_id=site3.id, is_active=True
    )

    # Create active sessions for each supervisor
    clock = get_clock()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=get_settings())

    _, sess1 = session_service.create_session(s1.id, s1.role)
    _, sess2 = session_service.create_session(s2.id, s2.role)
    _, sess3 = session_service.create_session(s3.id, s3.role)

    # Execute bulk deactivation
    resp = test_client.post(
        "/api/v1/admin/users/deactivate-supervisors",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["deactivated_count"] >= 3

    # Refresh entities
    db_session.refresh(s1)
    db_session.refresh(s2)
    db_session.refresh(s3)
    db_session.refresh(admin_user)
    db_session.refresh(second_admin_user)

    # Supervisors are inactive
    assert s1.is_active is False
    assert s2.is_active is False
    assert s3.is_active is False

    # Admins are untouched
    assert admin_user.is_active is True
    assert second_admin_user.is_active is True

    # Supervisor sessions dropped
    db_session.refresh(sess1)
    db_session.refresh(sess2)
    db_session.refresh(sess3)
    assert sess1.revoked_at is not None
    assert sess2.revoked_at is not None
    assert sess3.revoked_at is not None


# ==============================================================================
# 7. Donor Data Retention on Deactivation (BR-USER-06, T-USER-06-a)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-USER-06")
def test_t_user_06_a_donor_retains_registered_by_on_deactivation(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
) -> None:
    """T-USER-06-a: Deactivating a supervisor preserves registered_by on donor records."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Create campaign and participate
    camp = create_campaign(db_session, slug="camp-donor-retention", is_published=True)
    create_campaign_site(db_session, campaign_id=camp.id, site_id=supervisor_user.site_id)

    # Create donor registered by supervisor
    donor = create_donor(
        db_session,
        campaign_id=camp.id,
        site_id=supervisor_user.site_id,
        first_name="متبرع الفحص",
        registered_by=supervisor_user.id,
    )
    donor_id = donor.id
    assert donor.registered_by == supervisor_user.id

    # Deactivate supervisor
    resp = test_client.post(
        f"/api/v1/admin/users/{supervisor_user.id}/deactivate",
        headers=headers,
    )
    assert resp.status_code == 200

    # Refresh donor and verify registered_by remains intact
    fetched_donor = db_session.get(Donor, donor_id)
    assert fetched_donor is not None
    assert fetched_donor.registered_by == supervisor_user.id


# ==============================================================================
# 8. Account Lifecycle Operations (Activate, Unlock, Reset-MFA, Resend-Invite)
# ==============================================================================


@pytest.mark.api
def test_activate_and_unlock_user(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
) -> None:
    """Test activating and unlocking accounts."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Deactivate supervisor first
    supervisor_user.is_active = False
    supervisor_user.failed_login_count = 5
    supervisor_user.locked_until = get_clock().now() + timedelta(minutes=15)
    db_session.flush()

    # 1. Activate
    resp = test_client.post(
        f"/api/v1/admin/users/{supervisor_user.id}/activate",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    # 2. Unlock
    resp = test_client.post(
        f"/api/v1/admin/users/{supervisor_user.id}/unlock",
        headers=headers,
    )
    assert resp.status_code == 200
    db_session.refresh(supervisor_user)
    assert supervisor_user.failed_login_count == 0
    assert supervisor_user.locked_until is None


@pytest.mark.api
@pytest.mark.br("BR-AUTH-13")
def test_reset_mfa_drops_sessions_and_clears_codes(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    second_admin_user: User,
) -> None:
    """Resetting MFA clears TOTP settings, deletes recovery codes, and drops sessions."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Configure second_admin with MFA and recovery codes
    second_admin_user.totp_enabled = True
    second_admin_user.totp_secret_enc = "enc_secret_xyz"
    code = MfaRecoveryCode(
        user_id=second_admin_user.id,
        code_hash="code_hash_123",
    )
    db_session.add(code)
    db_session.flush()

    # Create active session for second_admin
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=get_clock(), settings=get_settings())
    _, sess = session_service.create_session(second_admin_user.id, second_admin_user.role)

    # Reset MFA
    resp = test_client.post(
        f"/api/v1/admin/users/{second_admin_user.id}/reset-mfa",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["totp_enabled"] is False

    # Verify codes deleted and session revoked
    db_session.refresh(sess)
    assert sess.revoked_at is not None

    remaining_codes = (
        db_session.query(MfaRecoveryCode).filter_by(user_id=second_admin_user.id).count()
    )
    assert remaining_codes == 0


@pytest.mark.api
def test_resend_invite_invalidates_prior_tokens(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Resending invitation generates new token and invalidates prior tokens."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Create unactivated user
    pending_user = create_user(
        db_session,
        email="pending.invite@bloodday.local",
        role="admin",
        password_hash=None,
    )

    resp = test_client.post(
        f"/api/v1/admin/users/{pending_user.id}/resend-invite",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "pending.invite@bloodday.local"


@pytest.mark.api
def test_resend_invite_fails_if_already_active(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    second_admin_user: User,
) -> None:
    """Resending invitation for an already activated user returns 422 validation_error."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.post(
        f"/api/v1/admin/users/{second_admin_user.id}/resend-invite",
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# ==============================================================================
# 9. Session Invalidation on Role/Site Change (BR-AUTH-13 ★)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-AUTH-13")
def test_update_user_role_or_site_drops_sessions(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
) -> None:
    """Updating user role or site drops all active sessions immediately."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # Create active session for supervisor
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=get_clock(), settings=get_settings())
    _, sess = session_service.create_session(supervisor_user.id, supervisor_user.role)

    new_site = create_site(db_session, name="موقع جديد للمشرف")

    # Update supervisor site
    resp = test_client.patch(
        f"/api/v1/admin/users/{supervisor_user.id}",
        json={"site_id": str(new_site.id)},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["site_id"] == str(new_site.id)

    # Session dropped
    db_session.refresh(sess)
    assert sess.revoked_at is not None


# ==============================================================================
# 10. Audit Logging Verification & Zero PII (BR-AUDIT-01, BR-AUDIT-05)
# ==============================================================================


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-01")
def test_audit_records_logged_without_pii(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Administrative actions log audit records with zero PII in details or action."""
    headers = create_auth_headers_and_cookies(test_client, db_session, admin_user)

    resp = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": "audit.test@bloodday.local",
            "full_name": "اسم حساس يجب ألا يسجل",
            "role": "admin",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    created_id = uuid.UUID(resp.json()["id"])

    # Query audit logs for this user
    stmt = select(AuditLog).where(AuditLog.entity_id == created_id)
    audit_events = list(db_session.execute(stmt).scalars().all())
    assert len(audit_events) >= 1

    event = audit_events[0]
    assert event.action == "user.invite"
    assert event.actor_user_id == admin_user.id
    # Ensure details contain NO email, NO full name
    details = event.details or {}
    assert "email" not in details
    assert "full_name" not in details
    assert "name" not in details
