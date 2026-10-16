"""Unit tests for app/cli/setup_postgres.py (local PostgreSQL bootstrapper).

Tests:
- Database creation idempotency.
- Role creation and password configuration.
- Schema grants execution.
- .env file endpoint updates.
- CLI execution with mocked PostgreSQL server.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from app.cli.__main__ import cli_app
from app.cli.setup_postgres import (
    create_databases,
    create_or_update_roles,
    setup_database_grants,
    update_env_file,
)

runner = CliRunner()


def test_create_databases_creates_missing() -> None:
    """Databases that do not exist are created via CREATE DATABASE."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.side_effect = [None, (1,), None]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    create_databases(mock_conn, ["db1", "db2", "db3"])
    assert mock_cur.execute.call_count == 5  # 3 checks + 2 creates


def test_create_or_update_roles() -> None:
    """Roles are created if missing or updated if already existing."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # First role exists, second role is missing
    mock_cur.fetchone.side_effect = [(1,), None]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    create_or_update_roles(mock_conn)
    assert mock_cur.execute.call_count == 4  # 2 checks + 1 update + 1 create


def test_setup_database_grants() -> None:
    """Grants schema and default privileges on specified database."""
    with patch("app.cli.setup_postgres.get_pg_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        setup_database_grants("postgres", "pass", "bloodday", port=5432)  # pragma: allowlist secret
        assert mock_cur.execute.call_count >= 5


def test_update_env_file(tmp_path: Path) -> None:
    """Updates TEST_DATABASE_URL and adds MIGRATION_DATABASE_URL to specified port."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=dev\n"
        "DATABASE_URL=postgresql+psycopg://app_user:pass@localhost:5432/bloodday\n"  # pragma: allowlist secret
        "TEST_DATABASE_URL=postgresql+psycopg://app_user:pass@localhost:5433/bloodday_test\n"  # pragma: allowlist secret
    )

    update_env_file(tmp_path, port=5432)
    content = env_file.read_text()
    assert "localhost:5432/bloodday_test" in content
    assert "MIGRATION_DATABASE_URL=" in content
    assert "app_migrator:app_migrator_dev_password@localhost:5432/bloodday" in content


def test_setup_postgres_cmd_success(tmp_path: Path) -> None:
    """setup-postgres command executes end-to-end with mocked services."""
    with (
        patch("app.cli.setup_postgres.get_pg_connection") as mock_get_conn,
        patch("app.cli.setup_postgres.upgrade_to_head", return_value="008"),
        patch(
            "app.cli.setup_postgres.verify_database_compliance",
            return_value={"compliant": True},
        ),
        patch("pathlib.Path.cwd", return_value=tmp_path),
    ):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1,)  # Already exists
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        result = runner.invoke(
            cli_app,
            [
                "setup-postgres",
                "--user",
                "postgres",
                "--password",
                "test_pass",
                "--port",
                "5432",
            ],  # pragma: allowlist secret
        )
        assert result.exit_code == 0
        assert "SUCCESS: Local PostgreSQL is 100% configured" in result.stdout
