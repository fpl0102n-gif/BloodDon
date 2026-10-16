"""Database migration inspection and staging/production upgrade runner.

Supports P2.8 and D-57 requirements:
- Inspect active database revision against Alembic head revision.
- Verify whether staging/production database schema is up-to-date with dev.
- Apply pending migrations to head when requested.
"""

from typing import Any

import typer
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.core.config import get_settings


def get_alembic_config(database_url: str | None = None) -> Config:
    """Build Alembic configuration pointing to alembic.ini with optional URL override."""
    cfg = Config("alembic.ini")
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def get_target_migration_url(database_url: str | None = None) -> str:
    """Determine database URL for migration operations.

    Prioritizes explicit argument, then MIGRATION_DATABASE_URL (app_migrator),
    then DATABASE_URL.
    """
    if database_url and database_url.strip():
        return database_url.strip()

    settings = get_settings()
    return settings.migration_database_url or settings.database_url


def get_migration_status(database_url: str | None = None) -> dict[str, Any]:
    """Inspect current database schema revision and compare with Alembic head.

    Returns dict containing:
    - head_revision: The latest revision ID defined in migrations/
    - current_revision: The revision ID recorded in alembic_version table (or None)
    - is_up_to_date: True if current_revision == head_revision
    - database_url: The sanitized target URL
    """
    target_url = get_target_migration_url(database_url)
    alembic_cfg = get_alembic_config(target_url)
    script_dir = ScriptDirectory.from_config(alembic_cfg)
    head_rev = script_dir.get_current_head()

    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_rev = ctx.get_current_revision()
    finally:
        engine.dispose()

    return {
        "head_revision": head_rev,
        "current_revision": current_rev,
        "is_up_to_date": (current_rev == head_rev) and (head_rev is not None),
        "target_url": target_url,
    }


def upgrade_to_head(database_url: str | None = None) -> str | None:
    """Run Alembic upgrade to head on the specified database URL.

    Returns the new current revision ID after upgrade.
    """
    target_url = get_target_migration_url(database_url)
    alembic_cfg = get_alembic_config(target_url)

    command.upgrade(alembic_cfg, "head")

    status = get_migration_status(target_url)
    rev = status.get("current_revision")
    return str(rev) if rev is not None else None


def check_migrations_cmd(
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        "-u",
        help="Optional database URL override (defaults to MIGRATION_DATABASE_URL or DATABASE_URL).",
    ),
    upgrade: bool = typer.Option(
        False,
        "--upgrade",
        "-y",
        help="Automatically apply pending migrations to head before verifying status.",
    ),
) -> None:
    """Check whether database schema matches the latest migration head (P2.8 / D-57)."""
    target_url = get_target_migration_url(database_url)
    # Mask password for secure logging / display
    sanitized_url = target_url.split("@")[-1] if "@" in target_url else target_url

    typer.echo(f"Inspecting database schema at: ...@{sanitized_url}")

    if upgrade:
        typer.secho("Applying pending Alembic migrations to head...", fg=typer.colors.YELLOW)
        try:
            upgrade_to_head(target_url)
            typer.secho("Migration upgrade completed successfully.", fg=typer.colors.GREEN)
        except Exception as exc:
            typer.secho(f"Migration upgrade FAILED: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

    try:
        status = get_migration_status(target_url)
    except Exception as exc:
        typer.secho(
            f"Database connection or inspection FAILED: {exc}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=1) from exc

    head_rev = status["head_revision"]
    cur_rev = status["current_revision"]
    is_synced = status["is_up_to_date"]

    typer.echo(f"Alembic Head Revision:   {head_rev}")
    typer.echo(f"Database Current Revision: {cur_rev or '(none / empty database)'}")

    if is_synced:
        typer.secho(
            f"SUCCESS: Database schema is up-to-date with head revision ({head_rev}).",
            fg=typer.colors.GREEN,
            bold=True,
        )
    else:
        typer.secho(
            f"DRIFT DETECTED: Database revision ({cur_rev}) does not match head revision ({head_rev}). "
            "Run with --upgrade to apply pending migrations.",
            fg=typer.colors.RED,
            bold=True,
            err=True,
        )
        raise typer.Exit(code=1)
