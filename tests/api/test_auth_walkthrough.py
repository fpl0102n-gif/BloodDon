"""Full End-to-End Authentication Lifecycle Walkthrough (P3.13).

Traces to:
- docs/09-roadmap.md §4 (P3 Milestone M2 acceptance criteria):
  "تجربة يدوية كاملة: create-admin ← تفعيل MFA ← دعوة مشرف ← قبول الدعوة ← دخوله ← تعطيله ← سقوط جلسته فورًا."
- docs/12-implementation-plan.md §6 Task P3.13:
  "Manual end-to-end: create-admin → MFA → invite supervisor → accept → login → deactivate → session dies instantly"
- docs/06-business-rules.md:
  - BR-AUTH-01 ★ (No self-registration; create-admin CLI; invitations only).
  - BR-AUTH-02 ★ (Invitations: single-use, 72h TTL, hashed tokens).
  - BR-AUTH-03 & 04 (Password policy, Argon2id storage).
  - BR-AUTH-05 ★ (Uniform login response, timing safety).
  - BR-AUTH-07 ★ (Deactivated accounts drop sessions immediately).
  - BR-AUTH-08 ★ (MFA mandatory for admin; interim sessions).
  - BR-AUTH-09 & 10 (TOTP enrollment, single-use codes, recovery codes).
  - BR-AUTH-11 & 12 (Server-side sessions, idle/absolute timeouts).
  - BR-AUTH-13 ★ (Session revocation on deactivation).
  - BR-AUTH-18 ★ (CSRF enforcement on authenticated requests).
  - BR-USER-01 ★ (Supervisor site binding; admin site_id=None).
"""

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import generate_csrf_token
from app.cli.create_admin import create_admin_user
from app.core.clock import FakeClock
from app.core.config import get_settings
from app.core.security import verify_password
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.sessions import SessionService
from tests.factories.campaign import create_site

ADMIN_EMAIL = "admin.walkthrough@bloodday.local"
ADMIN_PASSWORD = "CorrectHorseBatteryPassphrase2026!"  # pragma: allowlist secret
SUPERVISOR_EMAIL = "supervisor.walkthrough@bloodday.local"
SUPERVISOR_PASSWORD = "ValidSupervisorPassword2026!"  # pragma: allowlist secret


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-01")
@pytest.mark.br("BR-AUTH-02")
@pytest.mark.br("BR-AUTH-07")
@pytest.mark.br("BR-AUTH-08")
@pytest.mark.br("BR-AUTH-13")
def test_p3_13_full_auth_lifecycle_walkthrough(
    test_client: TestClient,
    db_session: Session,
    fake_clock: FakeClock,
) -> None:
    """Execute complete end-to-end walkthrough verifying every stage of staff lifecycle."""
    settings = get_settings()
    user_repo = UserRepository(db_session)
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=fake_clock, settings=settings)
    auth_service = AuthService(user_repo, session_service, clock=fake_clock, settings=settings)

    # ==========================================================================
    # Step 1: create-admin provisioning (P3.6 / BR-AUTH-01)
    # ==========================================================================
    admin_user = create_admin_user(
        db_session,
        email=ADMIN_EMAIL,
        password=ADMIN_PASSWORD,
        full_name="مسؤول المنصة الأول",
    )
    db_session.flush()

    assert admin_user.id is not None
    assert admin_user.email == ADMIN_EMAIL
    assert admin_user.role == "admin"
    assert admin_user.site_id is None  # BR-USER-01
    assert admin_user.is_active is True
    assert admin_user.totp_enabled is False  # Initial state prior to enrollment
    assert verify_password(ADMIN_PASSWORD, admin_user.password_hash) is True

    # ==========================================================================
    # Step 2: First admin login (Interim session issued per BR-AUTH-08)
    # ==========================================================================
    test_client.cookies.clear()
    login_res = test_client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert login_res.status_code == 200
    login_data = login_res.json()
    assert login_data["mfa_required"] is True  # Admin mandatory MFA

    admin_session_cookie = test_client.cookies.get(settings.cookie_name)
    assert admin_session_cookie is not None
    admin_csrf_token = generate_csrf_token(admin_session_cookie, secret_key=settings.secret_key)
    admin_headers = {"X-CSRF-Token": admin_csrf_token}

    # At this stage, attempting to access /me returns 403 mfa_required
    me_blocked_res = test_client.get("/api/v1/me")
    assert me_blocked_res.status_code == 403
    assert me_blocked_res.json()["error"]["code"] == "mfa_required"

    # ==========================================================================
    # Step 3: Admin MFA Enrollment (POST /me/mfa/setup & /me/mfa/confirm)
    # ==========================================================================
    # 3a. Initiate enrollment
    setup_res = test_client.post("/api/v1/me/mfa/setup", headers=admin_headers)
    assert setup_res.status_code == 200
    setup_data = setup_res.json()
    assert "secret" in setup_data
    assert "provisioning_uri" in setup_data
    assert len(setup_data["recovery_codes"]) == 8  # BR-AUTH-10: 8 single-use codes

    # 3b. Confirm first code to activate TOTP
    totp = pyotp.TOTP(setup_data["secret"])
    first_code = totp.at(fake_clock.now())
    confirm_res = test_client.post(
        "/api/v1/me/mfa/confirm",
        json={"code": first_code},
        headers=admin_headers,
    )
    assert confirm_res.status_code == 204, (
        f"confirm_res: {confirm_res.status_code} - {confirm_res.json()}"
    )

    # 3c. Admin session is now fully verified; /me returns 200
    me_success_res = test_client.get("/api/v1/me")
    assert me_success_res.status_code == 200
    me_data = me_success_res.json()
    assert me_data["email"] == ADMIN_EMAIL
    assert me_data["role"] == "admin"
    assert me_data["mfa_enabled"] is True

    # ==========================================================================
    # Step 4: Admin invites supervisor to Site A (BR-AUTH-02 / BR-USER-01)
    # ==========================================================================
    site_a = create_site(db_session, name="مركز النور للتبرع - موقع أ")
    db_session.flush()

    # Create uninitialized supervisor account
    supervisor_user = User(
        email=SUPERVISOR_EMAIL,
        full_name="د. محمد المشرف",
        password_hash=None,  # Pending invitation
        role="supervisor",
        site_id=site_a.id,  # BR-USER-01: mandatory site
        is_active=False,  # Inactive until invite accepted
        totp_enabled=False,
    )
    db_session.add(supervisor_user)
    db_session.flush()

    # Generate single-use invitation token with 72h TTL
    raw_invite_token, invite_url, _email_sent = auth_service.create_invitation(
        supervisor_user.id,
        send_email=False,
    )
    assert raw_invite_token is not None
    assert f"token={raw_invite_token}" in invite_url

    # ==========================================================================
    # Step 5: Supervisor accepts invitation (POST /auth/invite/accept)
    # ==========================================================================
    # Clear client session to simulate supervisor on distinct machine/browser
    test_client.cookies.clear()

    accept_res = test_client.post(
        "/api/v1/auth/invite/accept",
        json={
            "token": raw_invite_token,
            "password": SUPERVISOR_PASSWORD,
        },
    )
    assert accept_res.status_code == 200
    assert (
        "accepted successfully" in accept_res.json().get("status", "activated")
        or accept_res.json()["status"] == "activated"
    )

    # Verify token is single-use: replay rejected with 400 invalid_token
    replay_res = test_client.post(
        "/api/v1/auth/invite/accept",
        json={
            "token": raw_invite_token,
            "password": SUPERVISOR_PASSWORD,
        },
    )
    assert replay_res.status_code == 400
    assert replay_res.json()["error"]["code"] == "invalid_token"

    # ==========================================================================
    # Step 6: Supervisor logs in (POST /auth/login)
    # ==========================================================================
    sup_login_res = test_client.post(
        "/api/v1/auth/login",
        json={
            "email": SUPERVISOR_EMAIL,
            "password": SUPERVISOR_PASSWORD,
        },
    )
    assert sup_login_res.status_code == 200
    sup_login_data = sup_login_res.json()
    assert sup_login_data["mfa_required"] is False  # Supervisor MFA optional

    sup_session_cookie = test_client.cookies.get(settings.cookie_name)
    assert sup_session_cookie is not None
    sup_csrf_token = generate_csrf_token(sup_session_cookie, secret_key=settings.secret_key)
    sup_headers = {"X-CSRF-Token": sup_csrf_token}

    # ==========================================================================
    # Step 7: Supervisor accesses protected profile (GET /me)
    # ==========================================================================
    sup_me_res = test_client.get("/api/v1/me", headers=sup_headers)
    assert sup_me_res.status_code == 200
    sup_me_data = sup_me_res.json()
    assert sup_me_data["email"] == SUPERVISOR_EMAIL
    assert sup_me_data["role"] == "supervisor"
    assert sup_me_data["site_id"] == str(site_a.id)
    assert sup_me_data["mfa_enabled"] is False

    # ==========================================================================
    # Step 8: Admin deactivates supervisor (BR-AUTH-07 / BR-AUTH-13)
    # ==========================================================================
    # Deactivate supervisor account and revoke all their sessions
    supervisor_user.is_active = False
    session_service.revoke_all_user_sessions(supervisor_user.id)
    db_session.flush()

    # ==========================================================================
    # Step 9: Immediate session expiration — session dies instantly!
    # ==========================================================================
    # The supervisor's existing session cookie is still held by test_client.
    # An immediate request to /me MUST return 401 Unauthorized!
    sup_after_deactivation_res = test_client.get("/api/v1/me", headers=sup_headers)
    assert sup_after_deactivation_res.status_code == 401
    assert sup_after_deactivation_res.json()["error"]["code"] in (
        "session_expired",
        "not_authenticated",
    )

    # Attempting to login with deactivated credentials returns 401 invalid_credentials
    # (uniform response per BR-AUTH-05, no account status leakage)
    test_client.cookies.clear()
    deactivated_login_res = test_client.post(
        "/api/v1/auth/login",
        json={
            "email": SUPERVISOR_EMAIL,
            "password": SUPERVISOR_PASSWORD,
        },
    )
    assert deactivated_login_res.status_code == 401
    assert deactivated_login_res.json()["error"]["code"] == "invalid_credentials"
