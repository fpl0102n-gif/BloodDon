"""CLI command to provision the initial system administrator account.

Implements P3.6 per:
- docs/03-auth-permissions.md §3 & §4 (First admin creation via CLI).
- docs/06-business-rules.md §11 (BR-AUTH-01 no self-registration, BR-AUTH-03 password policy, BR-USER-01).
- docs/07-test-cases.md §5.10 (T-AUTH-01-b: creates admin, rejects weak password).
- docs/08-setup-env.md §8 (Interactive prompt for email, name, and password with hidden input).
"""

import typer
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionLocal, create_db_engine
from app.core.errors import ValidationError
from app.core.security import hash_password, validate_password
from app.models.user import User
from app.repositories.users import UserRepository

create_admin_app = typer.Typer(help="Administrative account provisioning commands.")


def create_admin_user(
    session: Session,
    email: str,
    password: str,
    full_name: str = "مسؤول النظام",
) -> User:
    """Programmatic helper to create an administrator account enforcing all security rules.

    Per BR-AUTH-01, BR-AUTH-03, BR-AUTH-04, and BR-USER-01:
    - Normalizes email address to lowercase.
    - Validates password against BR-AUTH-03 policy (10-128 chars, non-common, no name/email tokens).
    - Checks that email is not already taken.
    - Hashes password using Argon2id.
    - Creates User with role='admin', site_id=None, is_active=True, totp_enabled=False.
    """
    clean_email = email.strip().lower()
    clean_name = full_name.strip()

    # 1. Enforce password policy (BR-AUTH-03 / T-AUTH-01-b)
    validate_password(password, user_email=clean_email, user_name=clean_name)

    # 2. Check for existing account
    user_repo = UserRepository(session)
    existing = user_repo.get_by_email(clean_email)
    if existing is not None:
        raise ValueError(f"User with email '{clean_email}' already exists.")

    # 3. Hash password using Argon2id (BR-AUTH-04)
    hashed = hash_password(password)

    # 4. Construct admin entity (site_id=None required for admin per BR-USER-01)
    admin_user = User(
        email=clean_email,
        full_name=clean_name,
        password_hash=hashed,
        role="admin",
        site_id=None,
        is_active=True,
        must_change_password=False,
        totp_enabled=False,
        totp_secret_enc=None,
    )
    session.add(admin_user)
    session.flush()

    return admin_user


def create_admin_cmd(
    email: str | None = typer.Option(
        None,
        "--email",
        "-e",
        help="Administrator email address (prompted interactively if omitted).",
    ),
    full_name: str | None = typer.Option(
        None,
        "--full-name",
        "-n",
        help="Administrator full name (prompted interactively if omitted).",
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="Administrator password (prompted securely if omitted).",
        hide_input=True,
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Optional database URL override.",
    ),
) -> None:
    """Create the initial or emergency platform administrator account interactively."""
    # 1. Interactive prompts when options are not provided
    if not email:
        email = typer.prompt("Enter administrator email").strip()
    if not full_name:
        full_name = typer.prompt("Enter administrator full name", default="مسؤول النظام").strip()
    if not password:
        password = typer.prompt(
            "Enter administrator password",
            hide_input=True,
            confirmation_prompt=True,
        )

    # 2. Password policy validation check
    try:
        clean_email = email.strip().lower()
        clean_name = full_name.strip()
        validate_password(password, user_email=clean_email, user_name=clean_name)
    except ValidationError as exc:
        typer.secho(
            f"Error: Password policy violation: {exc}",
            fg=typer.colors.RED,
            bold=True,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    # 3. Persist admin user
    session_maker = sessionmaker_from_url(database_url) if database_url else SessionLocal
    with session_maker() as session:
        try:
            admin = create_admin_user(
                session,
                email=clean_email,
                password=password,
                full_name=clean_name,
            )
            session.commit()
            typer.secho(
                f"Administrator '{admin.email}' created successfully! "
                "(MFA setup will be required upon first login).",
                fg=typer.colors.GREEN,
                bold=True,
            )
        except Exception as exc:
            session.rollback()
            typer.secho(
                f"Error: Failed to create admin user: {exc}",
                fg=typer.colors.RED,
                bold=True,
                err=True,
            )
            raise typer.Exit(code=1) from exc


def sessionmaker_from_url(database_url: str) -> sessionmaker[Session]:
    """Build a temporary sessionmaker for a specific database URL override."""
    custom_engine = create_db_engine(database_url=database_url)
    return sessionmaker(bind=custom_engine, autocommit=False, autoflush=False)
