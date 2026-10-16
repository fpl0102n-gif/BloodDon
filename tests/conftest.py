"""Root pytest configuration, shared test doubles, lifecycle fixtures, and role skeletons.

Implements P1.10 per docs/05-architecture.md §13/§14, docs/07-test-cases.md §2/§3,
and docs/12-implementation-plan.md:
- Transaction-per-test database isolation fixture (db_session).
- Test doubles for cross-cutting services (fake_clock, fake_storage, fake_mailer, fake_rate_limiter).
- Role fixture skeletons (anon, sup_a, sup_a2, sup_b, sup_disabled, sup_mfa, admin_nomfa, admin, admin_fresh, admin2).
- Leak-detection sentinel constants (ZZFIRST, ZZLAST, 0550123456).
- Test client and application factory fixtures.
"""

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.clock import FakeClock, set_clock
from app.core.config import get_settings
from app.core.database import check_database_connection, create_db_engine, get_db
from app.core.ratelimit import FakeRateLimiter, InMemoryRateLimiter, set_rate_limiter
from app.mail import FakeMailer, set_mailer
from app.main import create_app
from app.storage import FakeStorage, set_storage

# ==============================================================================
# 1. Leak Detection Sentinel Constants (docs/07-test-cases.md §3.3)
# ==============================================================================

SENTINEL_FIRST_NAME: str = "ZZFIRST"
SENTINEL_LAST_NAME: str = "ZZLAST"
SENTINEL_PHONE: str = "0550123456"


# ==============================================================================
# 2. Cross-Cutting Service Fakes & Test Doubles (docs/05-architecture.md §13)
# ==============================================================================


@pytest.fixture
def fake_clock() -> Generator[FakeClock, None, None]:
    """Provide an injectable FakeClock for deterministic time manipulation without sleep."""
    clock = FakeClock()
    set_clock(clock)
    try:
        yield clock
    finally:
        set_clock(None)


@pytest.fixture
def fake_storage() -> Generator[FakeStorage, None, None]:
    """Provide an in-memory FakeStorage instance with failure injection support."""
    storage = FakeStorage()
    set_storage(storage)
    try:
        yield storage
    finally:
        storage.clear()
        set_storage(None)


@pytest.fixture
def fake_mailer() -> Generator[FakeMailer, None, None]:
    """Provide an in-memory FakeMailer capturing sent transactional emails."""
    mailer = FakeMailer()
    set_mailer(mailer)
    try:
        yield mailer
    finally:
        mailer.clear()
        set_mailer(None)


@pytest.fixture
def fake_rate_limiter() -> Generator[FakeRateLimiter, None, None]:
    """Provide a controllable FakeRateLimiter double."""
    limiter = FakeRateLimiter(allow_all=True)
    set_rate_limiter(limiter)
    try:
        yield limiter
    finally:
        set_rate_limiter(InMemoryRateLimiter())


# ==============================================================================
# 3. Database Isolation Fixture (Transaction-per-Test)
# ==============================================================================

_test_engine: Engine | None = None
_db_connection_accessible: bool | None = None


def _get_test_engine() -> Engine:
    """Retrieve or create dedicated test database engine."""
    global _test_engine
    if _test_engine is None:
        settings = get_settings()
        url = settings.test_database_url or settings.database_url
        _test_engine = create_db_engine(
            url,
            pool_size=5,
            max_overflow=5,
            connect_args={"connect_timeout": 1},
        )
    return _test_engine


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated database session wrapped in an outer transaction that rolls back on exit."""
    global _db_connection_accessible
    engine = _get_test_engine()
    if _db_connection_accessible is None:
        _db_connection_accessible = check_database_connection(engine)

    if not _db_connection_accessible:
        pytest.skip("PostgreSQL test database is not accessible on configured port.")

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def isolated_db(db_session: Session) -> Session:
    """Ensure database schema is created inside the active test transaction."""
    from app.models.base import Base

    Base.metadata.create_all(bind=db_session.connection())
    return db_session


@pytest.fixture
def test_app(db_session: Session) -> Generator[FastAPI, None, None]:
    """Provide FastAPI application with get_db overridden to the test transaction."""
    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def test_client(test_app: FastAPI) -> TestClient:
    """Provide TestClient wired to the transaction-isolated application instance."""
    return TestClient(test_app)


# ==============================================================================
# 4. Role Fixtures (Skeleton per docs/07-test-cases.md §3.1)
# ==============================================================================


@dataclass(frozen=True, slots=True)
class MockUser:
    """Skeleton representation of an authenticated user context for testing role matrix."""

    id: str
    email: str
    role: str
    site_id: str | None = None
    is_active: bool = True
    mfa_enabled: bool = False
    mfa_verified: bool = False
    reauth_valid: bool = False


@pytest.fixture
def role_anon() -> None:
    """Visitor role without authenticated session."""
    return


@pytest.fixture
def role_sup_a() -> MockUser:
    """Active supervisor assigned to Site A."""
    return MockUser(
        id="usr-sup-a-001",
        email="supervisor.a@bloodday.local",
        role="supervisor",
        site_id="site-a-001",
        is_active=True,
        mfa_enabled=False,
        mfa_verified=False,
        reauth_valid=False,
    )


@pytest.fixture
def role_sup_a2() -> MockUser:
    """Second active supervisor assigned to Site A."""
    return MockUser(
        id="usr-sup-a2-002",
        email="supervisor.a2@bloodday.local",
        role="supervisor",
        site_id="site-a-001",
        is_active=True,
    )


@pytest.fixture
def role_sup_b() -> MockUser:
    """Active supervisor assigned to Site B."""
    return MockUser(
        id="usr-sup-b-001",
        email="supervisor.b@bloodday.local",
        role="supervisor",
        site_id="site-b-002",
        is_active=True,
    )


@pytest.fixture
def role_sup_disabled() -> MockUser:
    """Deactivated supervisor."""
    return MockUser(
        id="usr-sup-dis-001",
        email="supervisor.disabled@bloodday.local",
        role="supervisor",
        site_id="site-a-001",
        is_active=False,
    )


@pytest.fixture
def role_sup_mfa() -> MockUser:
    """Active supervisor with verified MFA."""
    return MockUser(
        id="usr-sup-mfa-001",
        email="supervisor.mfa@bloodday.local",
        role="supervisor",
        site_id="site-a-001",
        is_active=True,
        mfa_enabled=True,
        mfa_verified=True,
    )


@pytest.fixture
def role_admin_nomfa() -> MockUser:
    """Admin in staging session prior to completing MFA."""
    return MockUser(
        id="usr-adm-nomfa-001",
        email="admin.nomfa@bloodday.local",
        role="admin",
        site_id=None,
        is_active=True,
        mfa_enabled=True,
        mfa_verified=False,
        reauth_valid=False,
    )


@pytest.fixture
def role_admin() -> MockUser:
    """Fully authenticated admin (MFA verified, no fresh confirmation)."""
    return MockUser(
        id="usr-adm-001",
        email="admin@bloodday.local",
        role="admin",
        site_id=None,
        is_active=True,
        mfa_enabled=True,
        mfa_verified=True,
        reauth_valid=False,
    )


@pytest.fixture
def role_admin_fresh() -> MockUser:
    """Fully authenticated admin with fresh password re-authentication (R valid)."""
    return MockUser(
        id="usr-adm-fresh-001",
        email="admin.fresh@bloodday.local",
        role="admin",
        site_id=None,
        is_active=True,
        mfa_enabled=True,
        mfa_verified=True,
        reauth_valid=True,
    )


@pytest.fixture
def role_admin2() -> MockUser:
    """Second active admin."""
    return MockUser(
        id="usr-adm2-002",
        email="admin2@bloodday.local",
        role="admin",
        site_id=None,
        is_active=True,
        mfa_enabled=True,
        mfa_verified=True,
        reauth_valid=False,
    )
