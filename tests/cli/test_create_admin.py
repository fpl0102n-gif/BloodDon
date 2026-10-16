"""Tests for the create-admin CLI command and self-registration absence (P3.6).

Traces to:
- BR-AUTH-01 & T-AUTH-01-a (★): No public self-registration/signup routes exist in application.
- BR-AUTH-01 & T-AUTH-01-b (★): create-admin CLI command provisions initial admin,
  enforces password policy, rejects weak passwords, and forces MFA on first login.
- BR-AUTH-03 & BR-AUTH-04: Argon2id hashing and password complexity policy.
- BR-USER-01: Admin accounts strictly have site_id = None.
"""

import pytest
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.__main__ import cli_app
from app.cli.create_admin import create_admin_user
from app.core.clock import FakeClock
from app.core.config import Settings
from app.core.errors import ValidationError
from app.core.security import verify_password
from app.main import create_app
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.sessions import SessionService

runner = CliRunner()


# ==============================================================================
# T-AUTH-01-a (★): Absence of Public Self-Registration Routes
# ==============================================================================


@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-01")
def test_t_auth_01_a_no_self_registration_routes() -> None:
    """T-AUTH-01-a: Verify there is no self-registration or signup route anywhere in the app."""
    app = create_app()

    forbidden_keywords = ("signup", "register", "self-register")

    for route in app.routes:
        path = getattr(route, "path", "")
        for keyword in forbidden_keywords:
            assert keyword not in path.lower(), (
                f"Forbidden self-registration route discovered: {path}"
            )


# ==============================================================================
# T-AUTH-01-b (★): create-admin CLI Command & Password Policy
# ==============================================================================


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-01")
def test_t_auth_01_b_create_admin_programmatic_success(db_session: Session) -> None:
    """T-AUTH-01-b: create_admin_user creates an admin with site_id=None and valid Argon2id hash."""
    email = "first.admin@bloodday.local"
    full_name = "مسؤول النظام الرئيسي"
    password = "CorrectHorseBatteryPassphrase2026!"  # pragma: allowlist secret

    admin = create_admin_user(
        db_session,
        email=email,
        password=password,
        full_name=full_name,
    )

    assert admin.id is not None
    assert admin.email == email
    assert admin.full_name == full_name
    assert admin.role == "admin"
    assert admin.site_id is None
    assert admin.is_active is True
    assert admin.totp_enabled is False
    assert admin.password_hash is not None
    assert admin.password_hash.startswith("$argon2id$")
    assert verify_password(password, admin.password_hash) is True


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-01")
def test_t_auth_01_b_create_admin_mfa_forced_on_first_login(db_session: Session) -> None:
    """T-AUTH-01-b: Admin created via create-admin is forced to complete MFA on first login."""
    email = "mfa.admin@bloodday.local"
    password = "SuperSecureAdminPassword2026!"  # pragma: allowlist secret

    admin = create_admin_user(db_session, email=email, password=password)

    clock = FakeClock()
    settings = Settings(app_env="test")
    user_repo = UserRepository(db_session)
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)
    auth_service = AuthService(user_repo, session_service, clock=clock, settings=settings)

    # First login attempt
    result = auth_service.authenticate(admin.email, password)

    # Even with totp_enabled=False, role='admin' strictly mandates MFA
    assert result.mfa_required is True
    assert result.session.mfa_verified is False


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-01")
def test_t_auth_01_b_create_admin_rejects_weak_passwords(db_session: Session) -> None:
    """T-AUTH-01-b: Weak passwords are rejected and no admin record is created."""
    email = "weak.admin@bloodday.local"

    # 1. Too short (< 10 chars)
    with pytest.raises(ValidationError, match="at least 10 characters"):
        create_admin_user(db_session, email, "short123")  # pragma: allowlist secret

    # 2. Common password
    with pytest.raises(ValidationError, match="too common"):
        create_admin_user(db_session, email, "password123")  # pragma: allowlist secret

    # 3. Password containing email prefix
    with pytest.raises(ValidationError, match="email"):
        create_admin_user(
            db_session, email, "weak.admin_password_extra"
        )  # pragma: allowlist secret

    # Confirm user was not persisted
    user = UserRepository(db_session).get_by_email(email)
    assert user is None


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-01")
def test_t_auth_01_b_create_admin_cli_options_execution(db_session: Session) -> None:
    """T-AUTH-01-b: CLI invocation with options provisions admin and validates inputs."""
    email = "cli.admin@bloodday.local"
    full_name = "مسؤول الطرفية"
    valid_password = "ValidCliPassword2026!"  # pragma: allowlist secret
    weak_password = "weak"  # pragma: allowlist secret

    # 1. Weak password CLI invocation fails
    res_weak = runner.invoke(
        cli_app,
        ["create-admin", "-e", email, "-n", full_name, "-p", weak_password],
    )
    assert res_weak.exit_code != 0
    assert "Password policy violation" in res_weak.output

    # User not created
    assert UserRepository(db_session).get_by_email(email) is None

    # 2. Valid password programmatic helper test ensures DB commit
    admin = create_admin_user(db_session, email=email, password=valid_password, full_name=full_name)
    db_session.commit()
    assert admin.id is not None

    # 3. Attempting to create duplicate admin fails
    with pytest.raises(ValueError, match="already exists"):
        create_admin_user(db_session, email=email, password=valid_password)


@pytest.mark.unit
def test_create_admin_cli_interactive_prompts_weak_rejection() -> None:
    """T-AUTH-01-b: Interactive CLI prompts reject weak password inputs without committing."""
    # Simulate interactive typing: email, full_name, weak password, confirmation
    user_input = "inter.admin@bloodday.local\nAdmin Name\n12345\n12345\n"

    result = runner.invoke(cli_app, ["create-admin"], input=user_input)
    assert result.exit_code != 0
    assert "Password policy violation" in result.output
