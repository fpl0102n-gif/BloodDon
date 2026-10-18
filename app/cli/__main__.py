"""Unified command-line interface entry point.

Implements CLI commands specified in CLAUDE.md §4:
- seed: Database seeding with reference dataset (refuses in prod per T-STRUCT-09).
- check-config: Production and environment configuration check.
"""

import typer

from app.cli.create_admin import create_admin_cmd
from app.cli.file_queue import process_file_queue_cmd
from app.cli.migrate import check_migrations_cmd
from app.cli.purge import purge_cmd
from app.cli.seed import seed_cmd
from app.cli.setup_postgres import setup_postgres_cmd
from app.cli.verify_db import verify_database_cmd
from app.core.config import check_config

cli_app = typer.Typer(
    name="bloodday",
    help="Blood Donation Platform Administrative CLI",
    no_args_is_help=True,
)

# Register CLI commands
cli_app.command(name="create-admin")(create_admin_cmd)
cli_app.command(name="seed")(seed_cmd)
cli_app.command(name="purge")(purge_cmd)
cli_app.command(name="process-file-queue")(process_file_queue_cmd)
cli_app.command(name="check-migrations")(check_migrations_cmd)

cli_app.command(name="verify-database")(verify_database_cmd)
cli_app.command(name="verify-gate3")(verify_database_cmd)
cli_app.command(name="setup-postgres")(setup_postgres_cmd)


@cli_app.command(name="check-config")
def check_config_cmd() -> None:
    """Validate active configuration settings and secrets against environment requirements."""
    try:
        check_config()
        typer.secho(
            "Configuration check PASSED: all required settings and secrets are valid.",
            fg=typer.colors.GREEN,
            bold=True,
        )
    except Exception as exc:
        typer.secho(f"Configuration check FAILED: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def main() -> None:
    """Main CLI execution entry point."""
    cli_app()


if __name__ == "__main__":
    main()
