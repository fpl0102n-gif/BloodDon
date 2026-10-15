"""Database engine, session management, and naming conventions.

Implements P1.2 per docs/05-architecture.md §10 and docs/02-database.md §3 & §13.
Configures synchronous SQLAlchemy 2.0 with connection pooling, explicit transaction
commit-on-success / rollback-on-error, and unified constraint naming conventions.
"""

from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, MetaData, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

from app.core.config import Settings, get_settings

# Unified naming convention for database constraints (docs/02-database.md §3)
POSTGRES_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=POSTGRES_NAMING_CONVENTION)


def create_db_engine(
    database_url: str | None = None,
    settings: Settings | None = None,
    **kwargs: Any,
) -> Engine:
    """Create a configured synchronous SQLAlchemy Engine instance with connection pooling."""
    cfg = settings or get_settings()
    url = database_url or cfg.database_url

    engine_kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        **kwargs,
    }

    # Special handling for in-memory SQLite (used in fast isolated unit tests if requested)
    if url.startswith("sqlite"):
        engine_kwargs.setdefault("connect_args", {"check_same_thread": False})
        if ":memory:" in url or "mode=memory" in url:
            engine_kwargs.setdefault("poolclass", StaticPool)
    else:
        # PostgreSQL with psycopg 3 connection pooling
        engine_kwargs.setdefault("poolclass", QueuePool)
        engine_kwargs.setdefault("pool_size", cfg.db_pool_size)
        engine_kwargs.setdefault("max_overflow", cfg.db_max_overflow)
        engine_kwargs.setdefault("pool_timeout", cfg.db_pool_timeout)

    return create_engine(url, **engine_kwargs)


# Application default engine and session factory
engine: Engine = create_db_engine()
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI request dependency providing a single database Session.

    - Commits transaction atomically when the request finishes successfully.
    - Rolls back transaction if an unhandled exception or domain error is raised.
    - Always closes the session connection in finally block.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_database_connection(
    engine_or_session: Engine | Session | None = None,
) -> bool:
    """Verify that the database is reachable by executing a lightweight SELECT 1.

    Returns True if the database responds successfully, False otherwise.
    Used by /ready endpoint and operational check-config CLI commands.
    """
    target = engine_or_session or engine
    try:
        if isinstance(target, Session):
            target.execute(text("SELECT 1"))
        elif isinstance(target, Engine):
            with target.connect() as connection:
                connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, Exception):
        return False
