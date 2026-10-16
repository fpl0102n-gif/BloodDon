"""Tests for database role privileges and append-only audit_log enforcement.

Implements T-AUDIT-02-a and T-STRUCT-11 per docs/07-test-cases.md §5.13 & §7,
docs/02-database.md §7, and docs/06-business-rules.md BR-AUDIT-02:
- T-AUDIT-02-a: app_user is rejected when attempting UPDATE, DELETE, or TRUNCATE on audit_log.
- T-STRUCT-11: app_user does not possess DDL privileges (CREATE/ALTER/DROP TABLE) and cannot alter audit_log.
- Unit checks: migration 008 script metadata and SQL correctness.
"""

import importlib
from collections.abc import Generator
from urllib.parse import urlparse, urlunparse

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.database import check_database_connection, create_db_engine

mig_008 = importlib.import_module("migrations.versions.008_db_roles_and_grants")


@pytest.mark.unit
def test_migration_008_contains_required_privilege_statements() -> None:
    """T-STRUCT-11 (unit): Verify migration 008 script defines exact role grants and audit_log restrictions."""
    # Check that upgrade and downgrade are defined
    assert hasattr(mig_008, "upgrade")
    assert hasattr(mig_008, "downgrade")

    # Inspect module source or inspect upgrade code
    import inspect as py_inspect

    upgrade_source = py_inspect.getsource(mig_008.upgrade)
    assert "GRANT USAGE ON SCHEMA public TO app_user" in upgrade_source
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user"
        in upgrade_source
    )
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM app_user" in upgrade_source

    downgrade_source = py_inspect.getsource(mig_008.downgrade)
    assert "GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log TO app_user" in downgrade_source


def _get_app_user_engine() -> Engine | None:
    """Construct an Engine connecting as app_user based on settings."""
    settings = get_settings()
    base_url = settings.test_database_url or settings.database_url
    parsed = urlparse(base_url)

    hostname = parsed.hostname or "localhost"
    port_str = f":{parsed.port}" if parsed.port else ""
    netloc = f"app_user:app_user_dev_password@{hostname}{port_str}"
    app_user_url = urlunparse(parsed._replace(netloc=netloc))

    engine = create_db_engine(app_user_url, connect_args={"connect_timeout": 1})
    if not check_database_connection(engine):
        return None
    return engine


@pytest.fixture
def app_user_engine() -> Generator[Engine, None, None]:
    """Fixture providing database engine connected as app_user."""
    engine = _get_app_user_engine()
    if engine is None:
        pytest.skip("PostgreSQL test database with role app_user is not accessible.")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.db
@pytest.mark.critical
def test_t_audit_02_a_app_user_cannot_update_or_delete_audit_log(app_user_engine: Engine) -> None:
    """T-AUDIT-02-a: app_user is rejected when attempting UPDATE, DELETE, or TRUNCATE on audit_log."""
    with app_user_engine.connect() as conn:
        # 1. SELECT on audit_log should succeed
        conn.execute(text("SELECT count(*) FROM audit_log"))

        # 2. INSERT on audit_log should succeed
        conn.execute(
            text(
                "INSERT INTO audit_log (action, entity_type, details) "
                "VALUES ('test.insert', 'test', '{\"status\": \"ok\"}'::jsonb)"
            )
        )
        conn.commit()

        # 3. UPDATE on audit_log must fail with permission denied (42501)
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("UPDATE audit_log SET action = 'tampered'"))
        assert "permission denied" in str(exc_info.value).lower()
        conn.rollback()

        # 4. DELETE on audit_log must fail with permission denied (42501)
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("DELETE FROM audit_log"))
        assert "permission denied" in str(exc_info.value).lower()
        conn.rollback()

        # 5. TRUNCATE on audit_log must fail with permission denied (42501)
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("TRUNCATE TABLE audit_log"))
        assert "permission denied" in str(exc_info.value).lower()
        conn.rollback()


@pytest.mark.db
@pytest.mark.critical
def test_t_struct_11_app_user_cannot_execute_ddl(app_user_engine: Engine) -> None:
    """T-STRUCT-11: app_user does not possess DDL privileges and cannot create or alter tables."""
    with app_user_engine.connect() as conn:
        # 1. CREATE TABLE must fail (no CREATE on public schema)
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("CREATE TABLE evil_table (id int)"))
        assert "permission denied" in str(exc_info.value).lower()
        conn.rollback()

        # 2. DROP TABLE must fail
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("DROP TABLE audit_log"))
        assert "permission denied" in str(exc_info.value).lower()
        conn.rollback()

        # 3. ALTER TABLE must fail
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(text("ALTER TABLE audit_log ADD COLUMN evil_col text"))
        assert "permission denied" in str(exc_info.value).lower()
        conn.rollback()
