"""Local PostgreSQL setup and bootstrapping command.

Bootstraps databases, roles, schema grants, and migrations on a locally installed
native PostgreSQL instance running on port 5432 (or specified port) without Docker.
"""

import contextlib
from pathlib import Path
from typing import Any

import psycopg
import typer
from psycopg import sql

from app.cli.migrate import upgrade_to_head
from app.cli.verify_db import verify_database_compliance


def get_pg_connection(
    user: str, password: str, dbname: str = "postgres", port: int = 5432
) -> psycopg.Connection[Any]:
    """Establish connection to PostgreSQL superuser using psycopg."""
    return psycopg.connect(
        host="localhost",
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        autocommit=True,
    )


def create_databases(conn: psycopg.Connection[Any], databases: list[str]) -> None:
    """Create target databases if they do not exist."""
    with conn.cursor() as cur:
        for db in databases:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (db,))
            if not cur.fetchone():
                typer.echo(f"Creating database '{db}'...")
                cur.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(db)))
            else:
                typer.echo(f"Database '{db}' already exists.")


def create_or_update_roles(conn: psycopg.Connection[Any]) -> None:
    """Create app_migrator and app_user with development passwords."""
    roles = [
        ("app_migrator", "app_migrator_dev_password"),  # pragma: allowlist secret
        ("app_user", "app_user_dev_password"),  # pragma: allowlist secret
    ]
    with conn.cursor() as cur:
        for role_name, role_pass in roles:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s;", (role_name,))
            if not cur.fetchone():
                typer.echo(f"Creating role '{role_name}'...")
                cur.execute(
                    sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {};").format(
                        sql.Identifier(role_name),
                        sql.Literal(role_pass),
                    )
                )
            else:
                typer.echo(f"Role '{role_name}' exists; updating password...")
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {};").format(
                        sql.Identifier(role_name),
                        sql.Literal(role_pass),
                    )
                )


def setup_database_grants(user: str, password: str, dbname: str, port: int = 5432) -> None:
    """Connect to specific database and apply role schema grants."""
    typer.echo(f"Applying schema and default privilege grants on '{dbname}'...")
    with (
        get_pg_connection(user, password, dbname=dbname, port=port) as conn,
        conn.cursor() as cur,
    ):
        # Grant connect
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO app_migrator, app_user;").format(
                sql.Identifier(dbname)
            )
        )
        # Schema public
        cur.execute("GRANT ALL ON SCHEMA public TO app_migrator;")
        cur.execute("GRANT USAGE ON SCHEMA public TO app_user;")

        # Inheritance for default privileges
        with contextlib.suppress(Exception):
            cur.execute(sql.SQL("GRANT app_migrator TO {};").format(sql.Identifier(user)))

        # Default privileges
        cur.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;"
        )
        cur.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO app_user;"
        )


def update_env_file(project_root: Path, port: int = 5432) -> None:
    """Update .env to point local database URLs to local PostgreSQL port."""
    env_path = project_root / ".env"
    if not env_path.exists():
        return

    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    has_migration_url = False

    for line in lines:
        if line.strip().startswith("TEST_DATABASE_URL="):
            new_lines.append(
                f"TEST_DATABASE_URL=postgresql+psycopg://app_user:app_user_dev_password@localhost:{port}/bloodday_test"  # pragma: allowlist secret
            )
        elif line.strip().startswith("MIGRATION_DATABASE_URL="):
            has_migration_url = True
            new_lines.append(
                f"MIGRATION_DATABASE_URL=postgresql+psycopg://app_migrator:app_migrator_dev_password@localhost:{port}/bloodday"  # pragma: allowlist secret
            )
        elif line.strip().startswith("DATABASE_URL="):
            new_lines.append(
                f"DATABASE_URL=postgresql+psycopg://app_user:app_user_dev_password@localhost:{port}/bloodday"  # pragma: allowlist secret
            )
        else:
            new_lines.append(line)

    if not has_migration_url:
        new_lines.append(
            f"MIGRATION_DATABASE_URL=postgresql+psycopg://app_migrator:app_migrator_dev_password@localhost:{port}/bloodday"  # pragma: allowlist secret
        )

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    typer.echo(f"Updated .env file: local database URLs now point to localhost:{port}")


def setup_postgres_cmd(
    user: str = typer.Option("postgres", "--user", "-u", help="Superuser username."),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="Superuser password (prompts securely if omitted).",
    ),
    port: int = typer.Option(5432, "--port", help="PostgreSQL server port."),
) -> None:
    """Bootstrap and configure local PostgreSQL databases, roles, and migrations."""
    admin_password = password
    if not admin_password:
        admin_password = typer.prompt(f"Enter password for superuser '{user}'", hide_input=True)

    typer.echo(f"Connecting to local PostgreSQL as '{user}' on port {port}...")
    try:
        with get_pg_connection(user, admin_password, dbname="postgres", port=port) as conn:
            typer.secho("Authenticated successfully as superuser.", fg=typer.colors.GREEN)
            create_databases(conn, ["bloodday", "bloodday_test", "bloodday_staging"])
            create_or_update_roles(conn)
    except Exception as exc:
        typer.secho(f"Failed to connect as superuser: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    # Apply grants to each database
    for db in ["bloodday", "bloodday_test", "bloodday_staging"]:
        setup_database_grants(user, admin_password, dbname=db, port=port)

    # Update .env
    project_root = Path.cwd()
    update_env_file(project_root, port=port)

    # Run Alembic migrations
    migrator_url = f"postgresql+psycopg://app_migrator:app_migrator_dev_password@localhost:{port}/bloodday"  # pragma: allowlist secret
    test_migrator_url = f"postgresql+psycopg://app_migrator:app_migrator_dev_password@localhost:{port}/bloodday_test"  # pragma: allowlist secret
    app_url = f"postgresql+psycopg://app_user:app_user_dev_password@localhost:{port}/bloodday"  # pragma: allowlist secret

    typer.echo("\nRunning Alembic migrations on 'bloodday'...")
    rev = upgrade_to_head(migrator_url)
    typer.secho(f"Development database upgraded to revision: {rev}", fg=typer.colors.GREEN)

    typer.echo("Running Alembic migrations on 'bloodday_test'...")
    test_rev = upgrade_to_head(test_migrator_url)
    typer.secho(f"Test database upgraded to revision: {test_rev}", fg=typer.colors.GREEN)

    typer.echo("\nAuditing database compliance...")
    results = verify_database_compliance(migrator_url, app_url)
    if results.get("compliant"):
        typer.secho(
            "\nSUCCESS: Local PostgreSQL is 100% configured, migrated, and verified!",
            fg=typer.colors.GREEN,
            bold=True,
        )
    else:
        typer.secho(
            f"\nWarning: Compliance audit reported issues: {results}", fg=typer.colors.YELLOW
        )
