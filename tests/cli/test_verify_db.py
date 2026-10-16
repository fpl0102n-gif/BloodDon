"""Unit tests for app/cli/verify_db.py (GATE-G3 compliance auditor).

Verifies that the CLI tool properly audits:
- PostgreSQL version compatibility.
- SSL enforcement.
- Role privilege separation (app_migrator DDL vs app_user DML/read-only audit).
- Exit codes and sanitized output.
"""

from unittest.mock import MagicMock, patch

from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

from app.cli.__main__ import cli_app
from app.cli.verify_db import (
    check_app_user_privilege_isolation,
    check_migrator_ddl,
    check_postgres_version,
    check_ssl_enforcement,
    mask_db_url,
)

runner = CliRunner()


def test_mask_db_url() -> None:
    """Password must be replaced with asterisks and never leaked in logs or CLI."""
    url = "postgresql+psycopg://app_migrator:SuperSecretPass123@db.supabase.co:5432/postgres?sslmode=require"  # pragma: allowlist secret
    masked = mask_db_url(url)
    assert "SuperSecretPass123" not in masked
    assert ":****@" in masked
    assert "db.supabase.co:5432/postgres?sslmode=require" in masked


def test_mask_db_url_no_password() -> None:
    """URLs without passwords remain valid without corruption."""
    url = "postgresql+psycopg://app_user@localhost:5432/bloodday"
    assert mask_db_url(url) == url


def test_check_postgres_version_success() -> None:
    """PostgreSQL 15 and 16 pass version check."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (
        160001,
        "PostgreSQL 16.1 (Debian 16.1-1.pgdg120+1)",
    )
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    ok, msg = check_postgres_version(mock_engine)
    assert ok is True
    assert "16.1" in msg


def test_check_postgres_version_too_old() -> None:
    """PostgreSQL versions below 15 fail with actionable error."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (
        140005,
        "PostgreSQL 14.5 on x86_64",
    )
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    ok, msg = check_postgres_version(mock_engine)
    assert ok is False
    assert "does not meet requirement" in msg


def test_check_ssl_enforcement_local() -> None:
    """Local connections accept optional SSL."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = Exception("No SSL table")
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    ok, msg = check_ssl_enforcement(
        "postgresql+psycopg://app_user:pass@localhost:5432/bloodday",  # pragma: allowlist secret
        mock_engine,
    )
    assert ok is True
    assert "Local connection" in msg


def test_check_ssl_enforcement_remote_without_ssl() -> None:
    """Remote connections without sslmode=require fail."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = False
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    ok, msg = check_ssl_enforcement(
        "postgresql+psycopg://app_user:pass@db.example.com:5432/bloodday",  # pragma: allowlist secret
        mock_engine,
    )
    assert ok is False
    assert "sslmode=require" in msg


def test_check_ssl_enforcement_remote_with_ssl() -> None:
    """Remote connections with sslmode=require pass."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = True
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    ok, msg = check_ssl_enforcement(
        "postgresql+psycopg://app_user:pass@db.example.com:5432/bloodday?sslmode=require",  # pragma: allowlist secret
        mock_engine,
    )
    assert ok is True
    assert "SSL/TLS encrypted" in msg


def test_check_migrator_ddl_success() -> None:
    """app_migrator can execute DDL."""
    with patch("app.cli.verify_db.create_engine") as mock_create_engine:
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_conn
        mock_create_engine.return_value = mock_engine

        ok, msg = check_migrator_ddl(
            "postgresql+psycopg://app_migrator:pass@localhost:5432/bloodday"  # pragma: allowlist secret
        )
        assert ok is True
        assert "DDL permissions" in msg


def test_check_app_user_privilege_isolation_enforced() -> None:
    """app_user is denied CREATE TABLE and UPDATE on audit_log."""
    with patch("app.cli.verify_db.create_engine") as mock_create_engine:
        mock_engine = MagicMock()
        mock_conn = MagicMock()

        # DDL throws permission denied
        ddl_error = DBAPIError(
            "CREATE TABLE ...", {}, Exception("permission denied for schema public")
        )
        # Audit update throws permission denied
        audit_error = DBAPIError(
            "UPDATE audit_log ...", {}, Exception("permission denied for table audit_log")
        )
        mock_conn.execute.side_effect = [ddl_error, audit_error]
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_create_engine.return_value = mock_engine

        ok, msg = check_app_user_privilege_isolation(
            "postgresql+psycopg://app_user:pass@localhost:5432/bloodday"  # pragma: allowlist secret
        )
        assert ok is True
        assert "audit_log append-only enforced" in msg


def test_check_app_user_privilege_isolation_leak() -> None:
    """If app_user can CREATE TABLE, check fails with security drift warning."""
    with patch("app.cli.verify_db.create_engine") as mock_create_engine:
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value = None  # Succeeds
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_create_engine.return_value = mock_engine

        ok, msg = check_app_user_privilege_isolation(
            "postgresql+psycopg://app_user:pass@localhost:5432/bloodday"  # pragma: allowlist secret
        )
        assert ok is False
        assert "SECURITY DRIFT" in msg


def test_verify_database_cmd_success() -> None:
    """CLI exits with 0 when all compliance checks pass."""
    compliant_results = {
        "postgres_version": {"ok": True, "msg": "PostgreSQL 16.1"},
        "ssl_encryption": {"ok": True, "msg": "Active"},
        "migration_sync": {"ok": True, "msg": "Synced to 008"},
        "migrator_ddl": {"ok": True, "msg": "CREATE/DROP table OK"},
        "app_user_isolation": {"ok": True, "msg": "Isolation enforced"},
        "compliant": True,
    }

    with patch("app.cli.verify_db.verify_database_compliance", return_value=compliant_results):
        result = runner.invoke(
            cli_app,
            [
                "verify-database",
                "--migration-url",
                "postgresql+psycopg://migrator:pass@localhost:5432/db",  # pragma: allowlist secret
                "--app-url",
                "postgresql+psycopg://user:pass@localhost:5432/db",  # pragma: allowlist secret
            ],
        )
        assert result.exit_code == 0
        assert "GATE-G3 AUDIT PASSED" in result.stdout


def test_verify_database_cmd_failure() -> None:
    """CLI exits with 1 when any compliance check fails."""
    failing_results = {
        "postgres_version": {"ok": False, "msg": "Version too old"},
        "ssl_encryption": {"ok": True, "msg": "Active"},
        "migration_sync": {"ok": True, "msg": "Synced to 008"},
        "migrator_ddl": {"ok": True, "msg": "CREATE/DROP table OK"},
        "app_user_isolation": {"ok": True, "msg": "Isolation enforced"},
        "compliant": False,
    }

    with patch("app.cli.verify_db.verify_database_compliance", return_value=failing_results):
        result = runner.invoke(
            cli_app,
            [
                "verify-database",
                "--migration-url",
                "postgresql+psycopg://migrator:pass@localhost:5432/db",  # pragma: allowlist secret
                "--app-url",
                "postgresql+psycopg://user:pass@localhost:5432/db",  # pragma: allowlist secret
            ],
        )
        assert result.exit_code == 1
        assert "GATE-G3 AUDIT FAILED" in result.stdout
