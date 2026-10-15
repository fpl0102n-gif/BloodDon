"""Unit tests for database engine, session lifecycle, and naming conventions.

Traces to:
- P1.2 (docs/12-implementation-plan.md §4)
- docs/02-database.md §3 (naming conventions and constraint rules)
- docs/05-architecture.md §10 (database, pool settings, session transaction lifecycle)
"""

import contextlib
from unittest.mock import MagicMock

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

from app.core.config import Settings
from app.core.database import (
    POSTGRES_NAMING_CONVENTION,
    check_database_connection,
    create_db_engine,
    get_db,
    metadata,
)
from app.models.base import Base

DUMMY_POSTGRES_URL: str = "postgresql+psycopg://app_user:app_secure_password_dev@localhost:5432/blood_test"  # pragma: allowlist secret
UNREACHABLE_DB_URL: str = "postgresql+psycopg://app_user:dummy_pass@127.0.0.1:54329/blood_unreachable"  # pragma: allowlist secret


@pytest.mark.unit
def test_postgres_naming_convention_structure() -> None:
    """Verify that POSTGRES_NAMING_CONVENTION contains all required prefixes per docs/02-database.md §3."""
    expected_conventions = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
    assert expected_conventions == POSTGRES_NAMING_CONVENTION
    assert metadata.naming_convention == POSTGRES_NAMING_CONVENTION
    assert Base.metadata.naming_convention == POSTGRES_NAMING_CONVENTION


@pytest.mark.unit
def test_naming_conventions_applied_to_constraints() -> None:
    """Verify that SQLAlchemy applies unified naming conventions to PK, UQ, FK, CK, and IX."""
    test_meta = MetaData(naming_convention=POSTGRES_NAMING_CONVENTION)

    class TestModelBase(DeclarativeBase):
        metadata = test_meta

    class TestParent(TestModelBase):
        __tablename__ = "sample_parents"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        code: Mapped[str] = mapped_column(String(20), unique=True)

    class TestChild(TestModelBase):
        __tablename__ = "sample_children"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        parent_id: Mapped[int] = mapped_column(ForeignKey("sample_parents.id"))
        score: Mapped[int] = mapped_column(Integer)
        status: Mapped[str] = mapped_column(String(10), index=True)

        __table_args__ = (CheckConstraint("score >= 0", name="score_positive"),)

    parent_table = test_meta.tables["sample_parents"]
    child_table = test_meta.tables["sample_children"]

    # Check Primary Key constraint names
    assert parent_table.primary_key.name == "pk_sample_parents"
    assert child_table.primary_key.name == "pk_sample_children"

    # Check Unique constraint name
    uq_names = [
        c.name for c in parent_table.constraints if c.__class__.__name__ == "UniqueConstraint"
    ]
    assert "uq_sample_parents_code" in uq_names

    # Check Foreign Key constraint name
    fk_names = [c.name for c in child_table.foreign_key_constraints]
    assert "fk_sample_children_parent_id_sample_parents" in fk_names

    # Check Check constraint name
    ck_names = [
        c.name for c in child_table.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    assert "ck_sample_children_score_positive" in ck_names

    # Check Index name
    ix_names = [idx.name for idx in child_table.indexes]
    assert "ix_sample_children_status" in ix_names


@pytest.mark.unit
def test_create_db_engine_postgresql_settings() -> None:
    """Verify engine configuration with PostgreSQL pool settings (QueuePool, size, overflow, timeout)."""
    settings = Settings(
        app_env="dev",
        database_url=DUMMY_POSTGRES_URL,
        db_pool_size=12,
        db_max_overflow=6,
        db_pool_timeout=45,
    )

    engine = create_db_engine(settings=settings)

    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == 12
    assert engine.pool._max_overflow == 6
    assert engine.pool._timeout == 45
    assert engine.pool._pre_ping is True


@pytest.mark.unit
def test_create_db_engine_sqlite_in_memory() -> None:
    """Verify engine configuration when using SQLite in-memory (StaticPool)."""
    engine = create_db_engine(database_url="sqlite:///:memory:")

    assert isinstance(engine.pool, StaticPool)
    # Verify pool_pre_ping is active
    assert engine.pool._pre_ping is True


@pytest.mark.unit
def test_get_db_session_commits_on_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_db() dependency commits transaction and closes the session on clean completion."""
    mock_session = MagicMock(spec=Session)
    mock_session_factory = MagicMock(return_value=mock_session)

    monkeypatch.setattr("app.core.database.SessionLocal", mock_session_factory)

    gen = get_db()
    session = next(gen)

    assert session is mock_session
    mock_session.commit.assert_not_called()
    mock_session.close.assert_not_called()

    with pytest.raises(StopIteration):
        next(gen)

    mock_session.commit.assert_called_once()
    mock_session.rollback.assert_not_called()
    mock_session.close.assert_called_once()


@pytest.mark.unit
def test_get_db_session_rolls_back_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_db() dependency rolls back transaction and closes the session when an exception occurs."""
    mock_session = MagicMock(spec=Session)
    mock_session_factory = MagicMock(return_value=mock_session)

    monkeypatch.setattr("app.core.database.SessionLocal", mock_session_factory)

    gen = get_db()
    session = next(gen)

    assert session is mock_session
    mock_session.commit.assert_not_called()
    mock_session.rollback.assert_not_called()

    with pytest.raises(ValueError, match="Domain failure"):
        gen.throw(ValueError("Domain failure"))

    mock_session.commit.assert_not_called()
    mock_session.rollback.assert_called_once()
    mock_session.close.assert_called_once()


@pytest.mark.unit
def test_get_db_transaction_behavior_with_live_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end integration test of get_db() transaction commit and rollback using SQLite."""
    test_engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    test_meta = MetaData()
    test_table = Table(
        "items",
        test_meta,
        Column("id", Integer, primary_key=True),
        Column("val", String),
    )
    test_meta.create_all(test_engine)

    test_session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr("app.core.database.SessionLocal", test_session_local)

    # 1. Clean run -> should commit
    def run_clean() -> None:
        db_gen = get_db()
        db = next(db_gen)
        try:
            db.execute(test_table.insert().values(id=1, val="first"))
        finally:
            with contextlib.suppress(StopIteration):
                next(db_gen)

    run_clean()

    # Verify committed
    with test_session_local() as verify_session:
        rows = verify_session.execute(text("SELECT val FROM items WHERE id = 1")).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "first"

    # 2. Failed run -> should rollback
    def run_failing() -> None:
        db_gen = get_db()
        db = next(db_gen)
        try:
            db.execute(test_table.insert().values(id=2, val="second"))
            raise RuntimeError("Transaction abort")
        except Exception as e:
            with contextlib.suppress(RuntimeError):
                db_gen.throw(e)

    run_failing()

    # Verify rolled back
    with test_session_local() as verify_session:
        rows = verify_session.execute(text("SELECT val FROM items WHERE id = 2")).fetchall()
        assert len(rows) == 0


@pytest.mark.unit
def test_check_database_connection_with_live_engine() -> None:
    """Verify check_database_connection returns True when engine is healthy and reachable."""
    test_engine = create_engine("sqlite:///:memory:")
    assert check_database_connection(test_engine) is True


@pytest.mark.unit
def test_check_database_connection_with_live_session() -> None:
    """Verify check_database_connection returns True when session is healthy and reachable."""
    test_engine = create_engine("sqlite:///:memory:")
    with Session(test_engine) as session:
        assert check_database_connection(session) is True


@pytest.mark.unit
def test_check_database_connection_unreachable() -> None:
    """Verify check_database_connection safely returns False without raising unhandled errors on connection failure."""
    bad_engine = create_engine(
        UNREACHABLE_DB_URL,
        connect_args={"connect_timeout": 1},
    )
    assert check_database_connection(bad_engine) is False
