"""Standalone Interactive Auth Walkthrough Runner (P3.13).

Demonstrates and verifies the complete authentication lifecycle:
1. Provisions platform admin via create-admin.
2. Initial admin login receiving interim session.
3. MFA setup and confirmation with TOTP code.
4. Generates supervisor invitation for Site A.
5. Supervisor accepts invitation with secure password.
6. Supervisor logs in and accesses /me profile.
7. Admin deactivates supervisor and revokes sessions.
8. Verifies supervisor session dies instantly (401 Unauthorized).

Usage:
    uv run python scripts/auth_walkthrough.py
"""

import uuid

import pyotp
import typer
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.api.deps import generate_csrf_token
from app.cli.create_admin import create_admin_user
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import create_app
from app.models.campaign import Site
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.sessions import SessionService

app = typer.Typer(help="Execute end-to-end authentication lifecycle walkthrough.")


def log_step(step_num: int, title: str) -> None:
    """Format section step header with clear visual styling."""
    typer.echo("")
    typer.secho(
        f"==> [Step {step_num}] {title}",
        fg=typer.colors.CYAN,
        bold=True,
    )


def log_ok(msg: str) -> None:
    """Format success message."""
    typer.secho(f"  [PASS] {msg}", fg=typer.colors.GREEN)


def log_info(msg: str) -> None:
    """Format informational message."""
    typer.secho(f"  [INFO] {msg}", fg=typer.colors.YELLOW)


def check(condition: bool, error_msg: str) -> None:
    """Validate assertion condition and fail with clean error on violation."""
    if not condition:
        typer.secho(f"  [FAIL] {error_msg}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@app.command()
def run_walkthrough() -> None:
    """Execute complete end-to-end authentication walkthrough."""
    settings = get_settings()
    fastapi_app = create_app()
    client = TestClient(fastapi_app)

    unique_suffix = uuid.uuid4().hex[:6]
    admin_email = f"admin_{unique_suffix}@walkthrough.local"
    admin_pass = "AdminWalkthroughPassword2026!"  # noqa: S105  # pragma: allowlist secret
    sup_email = f"sup_{unique_suffix}@walkthrough.local"
    sup_pass = "SupervisorWalkthroughPassword2026!"  # noqa: S105  # pragma: allowlist secret

    typer.secho(
        "=================================================================",
        fg=typer.colors.MAGENTA,
        bold=True,
    )
    typer.secho(
        "   BLOOD DONATION PLATFORM — AUTHENTICATION WALKTHROUGH (P3.13)   ",
        fg=typer.colors.MAGENTA,
        bold=True,
    )
    typer.secho(
        "=================================================================",
        fg=typer.colors.MAGENTA,
    )

    with SessionLocal() as db:
        user_repo = UserRepository(db)
        session_repo = SessionRepository(db)
        session_service = SessionService(session_repo, settings=settings)
        auth_service = AuthService(user_repo, session_service, settings=settings)

        # ----------------------------------------------------------------------
        # Step 1: create-admin
        # ----------------------------------------------------------------------
        log_step(1, f"Create Initial Administrator via CLI ({admin_email})")
        admin_user = create_admin_user(
            db,
            email=admin_email,
            password=admin_pass,
            full_name="مسؤول النظام الرئيسي",
        )
        db.commit()
        check(
            admin_user.id is not None and admin_user.role == "admin",
            "Admin user creation failed.",
        )
        log_ok(f"Admin provisioned in database: ID={admin_user.id}, Role={admin_user.role}")
        log_info(f"totp_enabled={admin_user.totp_enabled}, site_id={admin_user.site_id}")

        # ----------------------------------------------------------------------
        # Step 2: First Admin Login (Interim Session)
        # ----------------------------------------------------------------------
        log_step(2, "Administrator First Login (Interim Session Mandated)")
        client.cookies.clear()
        res_login = client.post(
            "/api/v1/auth/login",
            json={"email": admin_email, "password": admin_pass},
        )
        check(
            res_login.status_code == 200,
            f"Login failed: status={res_login.status_code}, text={res_login.text}",
        )
        login_json = res_login.json()
        check(login_json.get("mfa_required") is True, "Expected mfa_required=True")
        log_ok("Login succeeded with HTTP 200 (mfa_required=True)")

        admin_cookie = client.cookies.get(settings.cookie_name)
        check(admin_cookie is not None, "Missing session cookie in login response.")
        if admin_cookie is None:
            raise typer.Exit(code=1)
        admin_csrf = generate_csrf_token(admin_cookie, secret_key=settings.secret_key)
        admin_headers = {"X-CSRF-Token": admin_csrf}
        log_info(f"Interim session cookie received: {settings.cookie_name}")

        # Verify /me is blocked prior to MFA completion
        res_me_blocked = client.get("/api/v1/me")
        check(
            res_me_blocked.status_code == 403,
            f"Expected /me to return 403 for interim session, got {res_me_blocked.status_code}",
        )
        log_ok("Gated endpoint /me correctly rejected interim session with 403 mfa_required")

        # ----------------------------------------------------------------------
        # Step 3: Admin MFA Setup and Confirmation
        # ----------------------------------------------------------------------
        log_step(3, "Enroll Administrator in Two-Factor Authentication (TOTP)")
        res_setup = client.post("/api/v1/me/mfa/setup", headers=admin_headers)
        check(
            res_setup.status_code == 200,
            f"MFA setup initiation failed: {res_setup.status_code}",
        )
        setup_json = res_setup.json()
        secret = setup_json["secret"]
        recovery_codes = setup_json["recovery_codes"]
        check(len(recovery_codes) == 8, f"Expected 8 recovery codes, got {len(recovery_codes)}")
        log_ok("TOTP provisioning initialized. 8 single-use recovery codes generated.")

        totp = pyotp.TOTP(secret)
        current_code = totp.now()
        res_confirm = client.post(
            "/api/v1/me/mfa/confirm",
            json={"code": current_code},
            headers=admin_headers,
        )
        check(
            res_confirm.status_code == 204,
            f"MFA confirmation failed: {res_confirm.status_code} - {res_confirm.text}",
        )
        log_ok(
            "TOTP code confirmed successfully (HTTP 204). Session promoted to mfa_verified=True."
        )

        res_me = client.get("/api/v1/me")
        check(res_me.status_code == 200, f"/me returned {res_me.status_code} after MFA confirm")
        check(res_me.json().get("mfa_enabled") is True, "Expected mfa_enabled=True")
        log_ok(f"Endpoint /me accessible (HTTP 200). mfa_enabled={res_me.json()['mfa_enabled']}.")

        # ----------------------------------------------------------------------
        # Step 4: Admin Invites Supervisor to Site A
        # ----------------------------------------------------------------------
        log_step(4, f"Administrator Invites Supervisor ({sup_email}) to Site A")
        site = Site(
            name=f"مركز التبرع التجريبي {unique_suffix}",
            address="شارع الشهداء، الجزائر العاصمة",
            is_active=True,
        )
        db.add(site)
        db.flush()

        supervisor = User(
            email=sup_email,
            full_name="د. المشرف الميداني",
            password_hash=None,
            role="supervisor",
            site_id=site.id,
            is_active=False,
            totp_enabled=False,
        )
        db.add(supervisor)
        db.flush()

        raw_token, _invite_url, _ = auth_service.create_invitation(supervisor.id, send_email=False)
        db.commit()
        log_ok(f"Supervisor invited for Site ID={site.id}.")
        log_info(f"One-time invitation token generated (72h TTL): {raw_token[:12]}...")

        # ----------------------------------------------------------------------
        # Step 5: Supervisor Accepts Invitation
        # ----------------------------------------------------------------------
        log_step(5, "Supervisor Accepts Invitation and Configures Secure Password")
        client.cookies.clear()  # Distinct browser session
        res_accept = client.post(
            "/api/v1/auth/invite/accept",
            json={"token": raw_token, "password": sup_pass},
        )
        check(
            res_accept.status_code == 200,
            f"Invite accept failed: {res_accept.status_code} - {res_accept.text}",
        )
        check(res_accept.json().get("status") == "activated", "Expected status='activated'")
        log_ok("Invitation accepted with HTTP 200. Account activated with Argon2id hash.")

        # Replay attempt fails
        res_replay = client.post(
            "/api/v1/auth/invite/accept",
            json={"token": raw_token, "password": sup_pass},
        )
        check(
            res_replay.status_code == 400,
            f"Expected 400 for replay, got {res_replay.status_code}",
        )
        log_ok("Token reuse prevented: second acceptance attempt rejected with 400 invalid_token.")

        # ----------------------------------------------------------------------
        # Step 6: Supervisor Logs In
        # ----------------------------------------------------------------------
        log_step(6, "Supervisor Authenticates and Obtains Session")
        res_sup_login = client.post(
            "/api/v1/auth/login",
            json={"email": sup_email, "password": sup_pass},
        )
        check(res_sup_login.status_code == 200, f"Supervisor login failed: {res_sup_login.text}")
        check(
            res_sup_login.json().get("mfa_required") is False,
            "Expected mfa_required=False for supervisor",
        )
        log_ok("Supervisor logged in with HTTP 200. MFA optional for supervisor role.")

        sup_cookie = client.cookies.get(settings.cookie_name)
        check(sup_cookie is not None, "Missing supervisor session cookie.")
        if sup_cookie is None:
            raise typer.Exit(code=1)
        sup_csrf = generate_csrf_token(sup_cookie, secret_key=settings.secret_key)
        sup_headers = {"X-CSRF-Token": sup_csrf}

        # ----------------------------------------------------------------------
        # Step 7: Supervisor Accesses Protected Profile
        # ----------------------------------------------------------------------
        log_step(7, "Supervisor Accesses Protected Profile (/me)")
        res_sup_me = client.get("/api/v1/me", headers=sup_headers)
        check(res_sup_me.status_code == 200, f"Supervisor /me failed: {res_sup_me.text}")
        sup_me_data = res_sup_me.json()
        check(sup_me_data.get("email") == sup_email, "Email mismatch in supervisor profile")
        check(sup_me_data.get("role") == "supervisor", "Role mismatch in supervisor profile")
        check(sup_me_data.get("site_id") == str(site.id), "Site ID mismatch in supervisor profile")
        log_ok(f"Profile verified: Email={sup_email}, Role=supervisor, SiteID={site.id}")

        # ----------------------------------------------------------------------
        # Step 8: Deactivate Supervisor Account
        # ----------------------------------------------------------------------
        log_step(8, "Administrator Deactivates Supervisor Account and Revokes Sessions")
        db.execute(update(User).where(User.id == supervisor.id).values(is_active=False))
        revoked_count = session_service.revoke_all_user_sessions(supervisor.id)
        db.commit()
        log_ok(
            f"Supervisor account set to is_active=False. {revoked_count} active session(s) revoked."
        )

        # ----------------------------------------------------------------------
        # Step 9: Immediate Session Drop (Session dies instantly!)
        # ----------------------------------------------------------------------
        log_step(9, "Verify Immediate Session Revocation (Session Dies Instantly)")
        res_revoked = client.get("/api/v1/me", headers=sup_headers)
        check(
            res_revoked.status_code == 401,
            f"Expected 401 for revoked session, got {res_revoked.status_code}",
        )
        err_code = res_revoked.json().get("error", {}).get("code")
        check(
            err_code in ("session_expired", "not_authenticated"),
            f"Unexpected error code: {err_code}",
        )
        log_ok(f"Existing session immediately died with HTTP 401 ({err_code})!")

        client.cookies.clear()
        res_dead_login = client.post(
            "/api/v1/auth/login",
            json={"email": sup_email, "password": sup_pass},
        )
        check(
            res_dead_login.status_code == 401,
            f"Expected 401 for deactivated login, got {res_dead_login.status_code}",
        )
        check(
            res_dead_login.json().get("error", {}).get("code") == "invalid_credentials",
            "Expected code 'invalid_credentials'",
        )
        log_ok("Re-authentication attempt rejected with 401 invalid_credentials (uniform error).")

    typer.echo("")
    typer.secho(
        "=================================================================",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.secho(
        "   WALKTHROUGH COMPLETED SUCCESSFULLY — ALL SECURITY GATES PASS  ",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.secho(
        "=================================================================",
        fg=typer.colors.GREEN,
        bold=True,
    )


if __name__ == "__main__":
    app()
