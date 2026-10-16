"""Tests for Alembic migrations structure, reversible execution, and schema integrity.

Implements T-STRUCT-06 per docs/07-test-cases.md §7, docs/02-database.md §12,
and docs/12-implementation-plan.md P2.5:
- Verify linear migration chain from base to head (001 -> 006) with no branches.
- Verify every migration defines both upgrade() and downgrade() functions.
- Verify offline static SQL generation for both upgrade and downgrade.
- When live PostgreSQL is available:
  - upgrade head from scratch.
  - downgrade base cleanly drops all objects.
  - upgrade head again to verify full idempotence and reversibility.
  - alembic check clean (no model drift).
"""

import contextlib
import io

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.core.config import get_settings
from app.core.database import check_database_connection, create_db_engine
from app.models.base import Base


def _get_alembic_config(engine_url: str | None = None) -> Config:
    """Build Alembic configuration pointing to alembic.ini."""
    cfg = Config("alembic.ini")
    if engine_url:
        cfg.set_main_option("sqlalchemy.url", engine_url)
    return cfg


@pytest.fixture
def alembic_config() -> Config:
    """Fixture providing base Alembic configuration."""
    return _get_alembic_config()


@pytest.mark.unit
def test_t_struct_06_migration_chain_continuity(alembic_config: Config) -> None:
    """T-STRUCT-06: Verify linear, unbroken migration chain from 001 to 006."""
    script_dir = ScriptDirectory.from_config(alembic_config)
    head_rev = script_dir.get_current_head()
    assert head_rev == "006", f"Expected head revision to be '006', got '{head_rev}'"

    revisions = list(script_dir.walk_revisions())
    rev_ids = [r.revision for r in revisions]
    expected_order = ["006", "005", "004", "003", "002", "001"]
    assert rev_ids == expected_order, f"Unexpected migration order: {rev_ids}"

    # Verify linear down_revision chain
    expected_down = {
        "006": "005",
        "005": "004",
        "004": "003",
        "003": "002",
        "002": "001",
        "001": None,
    }
    for rev in revisions:
        assert rev.down_revision == expected_down[rev.revision], (
            f"Revision {rev.revision} down_revision mismatch: expected {expected_down[rev.revision]}, got {rev.down_revision}"
        )


@pytest.mark.unit
def test_t_struct_06_every_migration_has_upgrade_and_downgrade(
    alembic_config: Config,
) -> None:
    """T-STRUCT-06: Verify all migration scripts implement both upgrade() and downgrade()."""
    script_dir = ScriptDirectory.from_config(alembic_config)
    for rev in script_dir.walk_revisions():
        module = rev.module
        assert hasattr(module, "upgrade"), f"Migration {rev.revision} missing upgrade()"
        assert callable(module.upgrade), f"Migration {rev.revision} upgrade is not callable"
        assert hasattr(module, "downgrade"), f"Migration {rev.revision} missing downgrade()"
        assert callable(module.downgrade), f"Migration {rev.revision} downgrade is not callable"


@pytest.mark.unit
def test_t_struct_06_offline_sql_generation(alembic_config: Config) -> None:
    """T-STRUCT-06: Verify offline SQL generation succeeds without error for upgrade and downgrade."""
    # Test upgrade static SQL generation
    buf_upgrade = io.StringIO()
    cfg_upgrade = _get_alembic_config()

    with contextlib.redirect_stdout(buf_upgrade):
        command.upgrade(cfg_upgrade, "head", sql=True)

    sql_upgrade = buf_upgrade.getvalue()
    assert "CREATE TABLE campaigns" in sql_upgrade
    assert "CREATE TABLE users" in sql_upgrade
    assert "CREATE TABLE pledges" in sql_upgrade
    assert "CREATE TABLE donors" in sql_upgrade
    assert "CREATE TABLE photos" in sql_upgrade
    assert "CREATE TABLE audit_log" in sql_upgrade
    assert "CREATE TABLE phone_reveal_log" in sql_upgrade
    assert "CREATE TABLE rate_limit_events" in sql_upgrade

    # Test downgrade static SQL generation
    buf_downgrade = io.StringIO()
    cfg_downgrade = _get_alembic_config()

    with contextlib.redirect_stdout(buf_downgrade):
        command.downgrade(cfg_downgrade, "006:base", sql=True)

    sql_downgrade = buf_downgrade.getvalue()
    assert "DROP TABLE rate_limit_events" in sql_downgrade
    assert "DROP TABLE phone_reveal_log" in sql_downgrade
    assert "DROP TABLE audit_log" in sql_downgrade
    assert "DROP TABLE photos" in sql_downgrade
    assert "DROP TABLE donors" in sql_downgrade
    assert "DROP TABLE pledges" in sql_downgrade
    assert "DROP TABLE users" in sql_downgrade
    assert "DROP TABLE campaigns" in sql_downgrade


@pytest.mark.db
@pytest.mark.critical
def test_t_struct_06_live_reversible_migrations() -> None:
    """T-STRUCT-06: Live reversible migration test: up -> down -> up on PostgreSQL."""
    settings = get_settings()
    url = settings.test_database_url or settings.database_url
    test_engine = create_db_engine(url, connect_args={"connect_timeout": 1})

    if not check_database_connection(test_engine):
        pytest.skip("PostgreSQL test database is not accessible on configured port.")

    cfg = _get_alembic_config(url)

    # 1. Upgrade from scratch to head
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    inspector = inspect(test_engine)
    live_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables.keys())
    # Live tables include alembic_version plus all model tables
    assert expected_tables.issubset(live_tables), (
        f"Missing tables after upgrade head: {expected_tables - live_tables}"
    )

    # 2. Downgrade to base
    command.downgrade(cfg, "base")
    inspector = inspect(test_engine)
    tables_after_downgrade = set(inspector.get_table_names()) - {"alembic_version"}
    assert not tables_after_downgrade, (
        f"Tables remained after downgrade to base: {tables_after_downgrade}"
    )

    # 3. Upgrade back to head
    command.upgrade(cfg, "head")
    inspector = inspect(test_engine)
    tables_after_reupgrade = set(inspector.get_table_names())
    assert expected_tables.issubset(tables_after_reupgrade), (
        f"Missing tables after second upgrade head: {expected_tables - tables_after_reupgrade}"
    )

    # 4. Check model drift
    command.check(cfg)
