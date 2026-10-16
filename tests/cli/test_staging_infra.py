"""Tests for Staging Environment Infrastructure, Configuration, and Migration Verification.

Implements requirements for P2.8 and D-57 per:
- docs/12-implementation-plan.md (P2.8: Staging DB setup, Staging at same migration head).
- docs/08-setup-env.md §13 (Staging profile = production behavior, isolated DB, separate secrets).
- docs/10-testing-security-deployment.md (Staging matches production behavior and settings).
"""

import contextlib
import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from typer.testing import CliRunner

from app.cli.__main__ import cli_app
from app.cli.migrate import (
    get_alembic_config,
    get_target_migration_url,
)
from app.core.config import Settings

runner = CliRunner()


@pytest.mark.unit
def test_get_alembic_config_custom_url() -> None:
    """Verify get_alembic_config correctly overrides sqlalchemy.url."""
    custom_url = "postgresql+psycopg://custom_user:pass@staging_host:5432/staging_db"  # pragma: allowlist secret
    cfg = get_alembic_config(custom_url)
    assert cfg.get_main_option("sqlalchemy.url") == custom_url


@pytest.mark.unit
def test_staging_env_example_matches_production_invariants() -> None:
    """Verify .env.staging.example satisfies all strict production requirements per 08 §13."""
    staging_file = Path(".env.staging.example")
    assert staging_file.exists(), "Missing .env.staging.example configuration file"

    content = staging_file.read_text(encoding="utf-8")
    lines = [
        line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")
    ]

    env_map = {}
    for line in lines:
        if "=" in line:
            key, val = line.split("=", 1)
            env_map[key.strip()] = val.strip()

    # Verify staging matches production behavior per 08 §13
    assert env_map.get("APP_ENV") == "prod"
    assert env_map.get("COOKIE_SECURE") == "true"
    assert env_map.get("DOCS_ENABLED") == "false"
    assert env_map.get("LOG_FORMAT") == "json"
    assert env_map.get("DB_SSLMODE") == "require"

    # Verify database isolation and distinct roles
    db_url = env_map.get("DATABASE_URL", "")
    mig_url = env_map.get("MIGRATION_DATABASE_URL", "")
    assert "app_user" in db_url
    assert "app_migrator" in mig_url
    assert "staging" in db_url.lower()
    assert "staging" in mig_url.lower()

    # Validate Settings instantiation with valid production-grade secrets
    test_settings = Settings(
        app_env="prod",
        secret_key="a" * 64,
        ip_hash_salt="b" * 32,
        totp_encryption_key="c" * 43 + "=",
        database_url=db_url,
        migration_database_url=mig_url,
        cookie_secure=True,
        docs_enabled=False,
        log_format="json",
        trusted_proxies=["127.0.0.1"],
        allowed_origins=["https://staging.bloodday.dz"],
        public_base_url="https://staging.bloodday.dz",
        mail_backend="console",
    )
    assert test_settings.app_env == "prod"
    assert test_settings.cookie_secure is True


@pytest.mark.unit
def test_alembic_offline_sql_generation_with_staging_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that Alembic offline SQL generation succeeds with staging migration_database_url."""
    mock_settings = type(
        "MockSettings",
        (),
        {
            "migration_database_url": "postgresql+psycopg://app_migrator:pass@staging:5432/bloodday_staging",  # pragma: allowlist secret
            "database_url": "postgresql+psycopg://app_user:pass@staging:5432/bloodday_staging",  # pragma: allowlist secret
        },
    )()
    monkeypatch.setattr("app.core.config.get_settings", lambda: mock_settings)

    buf = io.StringIO()
    cfg = Config("alembic.ini")
    with contextlib.redirect_stdout(buf):
        command.upgrade(cfg, "head", sql=True)

    sql = buf.getvalue()
    assert "CREATE TABLE campaigns" in sql
    assert "CREATE TABLE users" in sql
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM app_user" in sql


@pytest.mark.unit
def test_get_target_migration_url_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify precedence order: explicit argument > migration_database_url > database_url."""
    mock_settings = type(
        "MockSettings",
        (),
        {
            "migration_database_url": "postgresql+psycopg://migrator:pass@host/db",  # pragma: allowlist secret
            "database_url": "postgresql+psycopg://user:pass@host/db",  # pragma: allowlist secret
        },
    )()
    monkeypatch.setattr("app.cli.migrate.get_settings", lambda: mock_settings)

    # 1. Explicit argument overrides all
    assert (
        get_target_migration_url("postgresql+psycopg://custom/db")
        == "postgresql+psycopg://custom/db"
    )

    # 2. Defaults to migration_database_url
    expected_mig = "postgresql+psycopg://migrator:pass@host/db"  # pragma: allowlist secret
    assert get_target_migration_url() == expected_mig

    # 3. Falls back to database_url
    mock_settings.migration_database_url = None
    expected_user = "postgresql+psycopg://user:pass@host/db"  # pragma: allowlist secret
    assert get_target_migration_url() == expected_user


@pytest.mark.unit
def test_check_migrations_cli_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify check-migrations exits 0 when database revision matches head."""
    mock_status = {
        "head_revision": "008",
        "current_revision": "008",
        "is_up_to_date": True,
        "target_url": "postgresql+psycopg://app_migrator:pass@staging/db",  # pragma: allowlist secret
    }
    monkeypatch.setattr("app.cli.migrate.get_migration_status", lambda _url: mock_status)

    result = runner.invoke(cli_app, ["check-migrations"])
    assert result.exit_code == 0
    assert "SUCCESS: Database schema is up-to-date with head revision (008)" in result.output


@pytest.mark.unit
def test_check_migrations_cli_drift_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify check-migrations exits 1 when drift is detected."""
    mock_status = {
        "head_revision": "008",
        "current_revision": "006",
        "is_up_to_date": False,
        "target_url": "postgresql+psycopg://app_migrator:pass@staging/db",  # pragma: allowlist secret
    }
    monkeypatch.setattr("app.cli.migrate.get_migration_status", lambda _url: mock_status)

    result = runner.invoke(cli_app, ["check-migrations"])
    assert result.exit_code == 1
    assert (
        "DRIFT DETECTED: Database revision (006) does not match head revision (008)"
        in result.output
    )


@pytest.mark.unit
def test_check_migrations_cli_upgrade_invokes_alembic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify check-migrations --upgrade runs upgrade and re-checks status."""
    upgrade_called = False

    def mock_upgrade(_url: str | None = None) -> str:
        nonlocal upgrade_called
        upgrade_called = True
        return "008"

    mock_status = {
        "head_revision": "008",
        "current_revision": "008",
        "is_up_to_date": True,
        "target_url": "postgresql+psycopg://app_migrator:pass@staging/db",  # pragma: allowlist secret
    }
    monkeypatch.setattr("app.cli.migrate.upgrade_to_head", mock_upgrade)
    monkeypatch.setattr("app.cli.migrate.get_migration_status", lambda _url: mock_status)

    result = runner.invoke(cli_app, ["check-migrations", "--upgrade"])
    assert result.exit_code == 0
    assert upgrade_called is True
    assert "Applying pending Alembic migrations to head..." in result.output
    assert "Migration upgrade completed successfully." in result.output
    assert "SUCCESS: Database schema is up-to-date with head revision (008)" in result.output
