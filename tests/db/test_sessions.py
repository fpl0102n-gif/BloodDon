"""Database and integration tests for Session Service and Repository (P3.2).

Implements tests for:
- BR-AUTH-11 & T-AUTH-11-a (★): Server-side sessions, SHA-256 hashed storage, zero raw tokens in DB.
- BR-AUTH-11 & T-AUTH-11-b (★): Session fixation defense, new session per login.
- BR-AUTH-12 & T-AUTH-12-a: Supervisor timeouts (4h idle / 14h absolute) and Admin timeouts (30m idle / 8h absolute).
- BR-AUTH-12: Activity touch throttling to minimize database write load.
- BR-AUTH-13 & T-AUTH-13-a (★): Immediate session invalidation on user deactivation or password/role changes.
- BR-AUTH-13: Revocation of single session and bulk revocation of user sessions.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.errors import NotAuthenticatedError, SessionExpiredError
from app.core.security import hash_token
from app.models.user import AuthSession, User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user


@pytest.fixture
def session_repo(db_session: Session) -> SessionRepository:
    """Provide SessionRepository bound to the isolated test transaction."""
    return SessionRepository(db_session)


@pytest.fixture
def session_service(session_repo: SessionRepository, fake_clock: FakeClock) -> SessionService:
    """Provide SessionService with test repository and fake clock."""
    return SessionService(session_repo, clock=fake_clock)


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create an active supervisor user with associated site."""
    site = create_site(db_session, name="Centre de Transfusion Test")
    return create_user(
        db_session,
        email="supervisor.sess@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create an active admin user."""
    return create_user(
        db_session,
        email="admin.sess@bloodday.local",
        role="admin",
        is_active=True,
    )


# ==============================================================================
# BR-AUTH-11: Server-side Sessions & Hashed Storage (T-AUTH-11-a ★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-11")
def test_br_auth_11_a_session_id_is_sha256_hash_and_never_raw(
    db_session: Session,
    session_service: SessionService,
    supervisor_user: User,
) -> None:
    """T-AUTH-11-a: auth_sessions.id must be a SHA-256 hash, and raw token must never be stored."""
    raw_token, session = session_service.create_session(
        supervisor_user.id,
        role=supervisor_user.role,
        ip_hash="test_ip_hash_123",
        user_agent="Mozilla/5.0 TestBrowser",
    )

    # 1. Raw token returned to client must be long and distinct from the database id
    assert raw_token is not None
    assert len(raw_token) >= 32
    assert raw_token != session.id

    # 2. Stored ID in database must be exactly 64-character SHA-256 hex digest
    assert len(session.id) == 64
    assert all(c in "0123456789abcdef" for c in session.id)
    assert session.id == hash_token(raw_token)

    # 3. Direct database lookup confirms raw_token is NOT in the DB
    stored_by_hash = db_session.get(AuthSession, session.id)
    assert stored_by_hash is not None
    assert stored_by_hash.user_id == supervisor_user.id
    assert stored_by_hash.ip_hash == "test_ip_hash_123"

    stored_by_raw = db_session.get(AuthSession, raw_token)
    assert stored_by_raw is None


# ==============================================================================
# BR-AUTH-11: Session Fixation Defense (T-AUTH-11-b ★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-11")
def test_br_auth_11_b_session_fixation_new_session_per_login(
    session_service: SessionService,
    admin_user: User,
) -> None:
    """T-AUTH-11-b: Every login produces a distinct new session; old session cannot be reused."""
    # First login session
    raw1, sess1 = session_service.create_session(admin_user.id, role=admin_user.role)
    assert sess1 is not None

    # Second login session (e.g. re-login or login from another tab)
    raw2, sess2 = session_service.create_session(admin_user.id, role=admin_user.role)
    assert sess2 is not None

    # The tokens and session IDs must be completely different
    assert raw1 != raw2
    assert sess1.id != sess2.id

    # Revoking session 1 leaves session 2 intact
    session_service.revoke_session(raw1)

    with pytest.raises(SessionExpiredError):
        session_service.validate_session(raw1)

    # Session 2 remains valid
    validated_sess, validated_user = session_service.validate_session(raw2)
    assert validated_sess.id == sess2.id
    assert validated_user.id == admin_user.id


# ==============================================================================
# BR-AUTH-12: Timeouts per Role (T-AUTH-12-a)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.br("BR-AUTH-12")
def test_br_auth_12_a_supervisor_idle_and_absolute_timeouts(
    session_service: SessionService,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-12-a: Supervisor sessions expire at 4h idle or 14h absolute timeout."""
    start_time = datetime(2026, 10, 17, 8, 0, 0, tzinfo=UTC)
    fake_clock.set_time(start_time)

    raw_token, session = session_service.create_session(
        supervisor_user.id,
        role=supervisor_user.role,
    )
    # Absolute timeout must be 14 hours (840 minutes) per BR-AUTH-12 / D-16
    assert session.expires_at == start_time + timedelta(hours=14)

    # Step 1: Active requests at 3h 50m renew idle timeout
    fake_clock.advance(timedelta(hours=3, minutes=50))
    valid_sess, _ = session_service.validate_session(raw_token)
    assert valid_sess.last_seen_at == start_time + timedelta(hours=3, minutes=50)

    # Step 2: Another active request at total elapsed 7h 40m succeeds
    fake_clock.advance(timedelta(hours=3, minutes=50))
    valid_sess, _ = session_service.validate_session(raw_token)
    assert valid_sess.last_seen_at == start_time + timedelta(hours=7, minutes=40)

    # Step 3: Advance past absolute timeout (14h + 1s from initial creation) -> must expire
    fake_clock.set_time(start_time + timedelta(hours=14, seconds=1))
    with pytest.raises(SessionExpiredError):
        session_service.validate_session(raw_token)

    # Step 4: Fresh supervisor session without activity expires after 4 hours idle
    fresh_start = datetime(2026, 10, 18, 9, 0, 0, tzinfo=UTC)
    fake_clock.set_time(fresh_start)
    raw_fresh, _ = session_service.create_session(supervisor_user.id, role=supervisor_user.role)

    # 4 hours + 1 second without activity -> idle timeout
    fake_clock.advance(timedelta(hours=4, seconds=1))
    with pytest.raises(SessionExpiredError):
        session_service.validate_session(raw_fresh)


@pytest.mark.db
@pytest.mark.security
@pytest.mark.br("BR-AUTH-12")
def test_br_auth_12_a_admin_idle_and_absolute_timeouts(
    session_service: SessionService,
    admin_user: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-12-a: Admin sessions expire at 30m idle or 8h absolute timeout."""
    start_time = datetime(2026, 10, 17, 10, 0, 0, tzinfo=UTC)
    fake_clock.set_time(start_time)

    raw_token, session = session_service.create_session(
        admin_user.id,
        role=admin_user.role,
    )
    # Absolute timeout must be 8 hours (480 minutes) per BR-AUTH-12 / D-16
    assert session.expires_at == start_time + timedelta(hours=8)

    # Step 1: Active request at 25 minutes succeeds and refreshes last_seen_at
    fake_clock.advance(timedelta(minutes=25))
    valid_sess, _ = session_service.validate_session(raw_token)
    assert valid_sess.last_seen_at == start_time + timedelta(minutes=25)

    # Step 2: Fresh admin session inactive for 30m 1s -> idle timeout
    admin_fresh_start = datetime(2026, 10, 18, 10, 0, 0, tzinfo=UTC)
    fake_clock.set_time(admin_fresh_start)
    raw_fresh, _ = session_service.create_session(admin_user.id, role=admin_user.role)

    fake_clock.advance(timedelta(minutes=30, seconds=1))
    with pytest.raises(SessionExpiredError):
        session_service.validate_session(raw_fresh)

    # Step 3: Continuously active session expiring at 8h absolute limit
    active_start = datetime(2026, 10, 19, 8, 0, 0, tzinfo=UTC)
    fake_clock.set_time(active_start)
    raw_active, _ = session_service.create_session(admin_user.id, role=admin_user.role)

    # Keep active every 20 minutes until past 8 hours
    for _ in range(23):
        fake_clock.advance(timedelta(minutes=20))
        session_service.validate_session(raw_active)

    # Now at 8 hours + 1 minute -> absolute timeout must trigger
    fake_clock.set_time(active_start + timedelta(hours=8, minutes=1))
    with pytest.raises(SessionExpiredError):
        session_service.validate_session(raw_active)


# ==============================================================================
# BR-AUTH-12: Activity Touch Throttling
# ==============================================================================


@pytest.mark.db
@pytest.mark.br("BR-AUTH-12")
def test_br_auth_12_touch_last_seen_throttled(
    session_service: SessionService,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """last_seen_at update must be throttled to avoid excessive DB write operations on every request."""
    t0 = datetime(2026, 10, 17, 8, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    raw_token, sess = session_service.create_session(supervisor_user.id, role="supervisor")
    initial_last_seen = sess.last_seen_at

    # Request 15 seconds later -> within 60s throttle window, last_seen_at not updated
    fake_clock.advance(timedelta(seconds=15))
    s1, _ = session_service.validate_session(raw_token)
    assert s1.last_seen_at == initial_last_seen

    # Request 65 seconds later -> throttle window passed, last_seen_at updated
    fake_clock.advance(timedelta(seconds=50))  # total 65s from t0
    s2, _ = session_service.validate_session(raw_token)
    assert s2.last_seen_at == t0 + timedelta(seconds=65)


# ==============================================================================
# BR-AUTH-13: Session Revocation & Immediate Deactivation (T-AUTH-13-a ★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-13")
def test_br_auth_13_a_deactivated_user_sessions_die_immediately(
    db_session: Session,
    session_service: SessionService,
    supervisor_user: User,
) -> None:
    """T-AUTH-13-a: Disabling a user account (is_active=False) drops all sessions immediately."""
    raw1, _ = session_service.create_session(supervisor_user.id, role="supervisor")
    raw2, _ = session_service.create_session(supervisor_user.id, role="supervisor")

    # Both sessions are initially valid
    assert session_service.validate_session(raw1) is not None
    assert session_service.validate_session(raw2) is not None

    # Admin deactivates supervisor account
    supervisor_user.is_active = False
    db_session.flush()

    # Both sessions are immediately rejected on the wire without needing explicit revocation
    with pytest.raises(NotAuthenticatedError):
        session_service.validate_session(raw1)

    with pytest.raises(NotAuthenticatedError):
        session_service.validate_session(raw2)


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-13")
def test_br_auth_13_a_revoke_single_session_on_logout(
    session_service: SessionService,
    supervisor_user: User,
) -> None:
    """T-AUTH-13-a: Logging out of one session revokes only that session."""
    raw1, _ = session_service.create_session(supervisor_user.id, role="supervisor")
    raw2, _ = session_service.create_session(supervisor_user.id, role="supervisor")

    session_service.revoke_session(raw1)

    # Session 1 is revoked
    with pytest.raises(SessionExpiredError):
        session_service.validate_session(raw1)

    # Session 2 remains valid
    valid_sess, _ = session_service.validate_session(raw2)
    assert valid_sess is not None


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-13")
def test_br_auth_13_a_revoke_all_user_sessions(
    session_service: SessionService,
    supervisor_user: User,
) -> None:
    """T-AUTH-13-a: Changing password, role, site, or resetting MFA revokes ALL user sessions."""
    raw1, _ = session_service.create_session(supervisor_user.id, role="supervisor")
    raw2, _ = session_service.create_session(supervisor_user.id, role="supervisor")
    raw3, _ = session_service.create_session(supervisor_user.id, role="supervisor")

    revoked_count = session_service.revoke_all_user_sessions(supervisor_user.id)
    assert revoked_count == 3

    # All sessions must raise SessionExpiredError
    for token in (raw1, raw2, raw3):
        with pytest.raises(SessionExpiredError):
            session_service.validate_session(token)


@pytest.mark.db
@pytest.mark.security
@pytest.mark.br("BR-AUTH-13")
def test_br_auth_13_a_revoke_all_sessions_except_current(
    session_service: SessionService,
    supervisor_user: User,
) -> None:
    """User changing password from an active session keeps their current session while terminating all others."""
    raw_current, _ = session_service.create_session(supervisor_user.id, role="supervisor")
    raw_other1, _ = session_service.create_session(supervisor_user.id, role="supervisor")
    raw_other2, _ = session_service.create_session(supervisor_user.id, role="supervisor")

    revoked_count = session_service.revoke_all_user_sessions(
        supervisor_user.id,
        except_raw_token=raw_current,
    )
    assert revoked_count == 2

    # Current session remains valid
    valid_sess, _ = session_service.validate_session(raw_current)
    assert valid_sess is not None

    # Other sessions are terminated
    with pytest.raises(SessionExpiredError):
        session_service.validate_session(raw_other1)

    with pytest.raises(SessionExpiredError):
        session_service.validate_session(raw_other2)


# ==============================================================================
# Token Validation Edge Cases & Cleanup
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
def test_session_validation_invalid_and_corrupt_tokens(
    session_service: SessionService,
) -> None:
    """Non-existent, empty, or garbage tokens must raise NotAuthenticatedError."""
    invalid_tokens = [
        "",
        "non_existent_token_1234567890",
        "random_garbage_string",
    ]
    for token in invalid_tokens:
        with pytest.raises(NotAuthenticatedError):
            session_service.validate_session(token)


@pytest.mark.db
def test_cleanup_expired_sessions(
    db_session: Session,
    session_service: SessionService,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """cleanup_expired_sessions deletes sessions where expires_at has passed."""
    t0 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    _, sess_old = session_service.create_session(supervisor_user.id, role="supervisor")

    # Advance clock 30 days
    t1 = t0 + timedelta(days=30)
    fake_clock.set_time(t1)

    _, sess_new = session_service.create_session(supervisor_user.id, role="supervisor")

    # Cleanup before t1
    deleted_count = session_service.cleanup_expired_sessions()
    assert deleted_count >= 1

    # Old session is removed from DB
    assert db_session.get(AuthSession, sess_old.id) is None
    # New session remains
    assert db_session.get(AuthSession, sess_new.id) is not None
