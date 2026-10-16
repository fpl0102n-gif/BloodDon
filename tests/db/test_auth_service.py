"""Database and integration tests for AuthService login flow and lockout (P3.3).

Implements tests for:
- BR-AUTH-05 & T-AUTH-05-a (★): Uniform error response across all authentication failure modes.
- BR-AUTH-05 & T-AUTH-05-b (★): Constant-time dummy verification invocation when user is missing or uninitialized.
- BR-AUTH-06 & T-AUTH-06-a (★): Lockout after 5 consecutive failures for 15 minutes.
- BR-AUTH-06 & T-AUTH-06-b (★): Correct password rejected during active lockout.
- BR-AUTH-06 & T-AUTH-06-c: Login succeeds and resets failure counters once lockout duration expires.
- BR-AUTH-06 & T-AUTH-06-d: Lockout doubling (15m -> 30m -> 60m capped).
- BR-AUTH-06 & T-AUTH-06-e (★): Admin unlock allows immediate login.
- BR-AUTH-04: Automatic Argon2id rehash on successful login if hash parameters are outdated.
- BR-AUTH-08: MFA requirement detection and interim session issuance.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher, Type
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.errors import InvalidCredentialsError
from app.core.security import hash_password
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user

TEST_PASSWORD: str = "valid_secure_passphrase_2026"  # pragma: allowlist secret


@pytest.fixture
def user_repo(db_session: Session) -> UserRepository:
    """Provide UserRepository bound to test session."""
    return UserRepository(db_session)


@pytest.fixture
def session_repo(db_session: Session) -> SessionRepository:
    """Provide SessionRepository bound to test session."""
    return SessionRepository(db_session)


@pytest.fixture
def session_service(session_repo: SessionRepository, fake_clock: FakeClock) -> SessionService:
    """Provide SessionService with test repository and fake clock."""
    return SessionService(session_repo, clock=fake_clock)


@pytest.fixture
def auth_service(
    user_repo: UserRepository,
    session_service: SessionService,
    fake_clock: FakeClock,
) -> AuthService:
    """Provide AuthService with test dependencies and fake clock."""
    return AuthService(user_repo, session_service, clock=fake_clock)


@pytest.fixture
def active_supervisor(db_session: Session) -> User:
    """Create an active supervisor with known password hash."""
    site = create_site(db_session, name="Centre Mobile Alger")
    return create_user(
        db_session,
        email="supervisor.auth@bloodday.local",
        password_hash=hash_password(TEST_PASSWORD),
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def active_admin(db_session: Session) -> User:
    """Create an active admin with known password hash."""
    return create_user(
        db_session,
        email="admin.auth@bloodday.local",
        password_hash=hash_password(TEST_PASSWORD),
        role="admin",
        is_active=True,
    )


# ==============================================================================
# BR-AUTH-05: Uniform Error Across All Failure Modes (T-AUTH-05-a ★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-05")
def test_br_auth_05_a_uniform_error_for_all_login_failures(
    db_session: Session,
    auth_service: AuthService,
    active_supervisor: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-05-a: All failure causes must yield identical 401 invalid_credentials exceptions."""
    # 1. Non-existent email
    with pytest.raises(InvalidCredentialsError) as exc_1:
        auth_service.authenticate("nonexistent@bloodday.local", TEST_PASSWORD)
    assert exc_1.value.code == "invalid_credentials"
    assert exc_1.value.http_status == 401

    # 2. Wrong password for existing user
    with pytest.raises(InvalidCredentialsError) as exc_2:
        auth_service.authenticate(active_supervisor.email, "wrong_password_attempt")
    assert exc_2.value.code == "invalid_credentials"
    assert exc_2.value.http_status == 401

    # 3. Deactivated user (is_active = False)
    active_supervisor.is_active = False
    db_session.flush()
    with pytest.raises(InvalidCredentialsError) as exc_3:
        auth_service.authenticate(active_supervisor.email, TEST_PASSWORD)
    assert exc_3.value.code == "invalid_credentials"
    assert exc_3.value.http_status == 401
    active_supervisor.is_active = True
    db_session.flush()

    # 4. Incomplete invitation (password_hash is None)
    site = create_site(db_session, name="Centre Pending")
    pending_user = create_user(
        db_session,
        email="pending@bloodday.local",
        password_hash=None,
        role="supervisor",
        site_id=site.id,
    )
    with pytest.raises(InvalidCredentialsError) as exc_4:
        auth_service.authenticate(pending_user.email, TEST_PASSWORD)
    assert exc_4.value.code == "invalid_credentials"
    assert exc_4.value.http_status == 401

    # 5. Locked account
    now = fake_clock.now()
    active_supervisor.locked_until = now + timedelta(minutes=15)
    db_session.flush()
    with pytest.raises(InvalidCredentialsError) as exc_5:
        auth_service.authenticate(active_supervisor.email, TEST_PASSWORD)
    assert exc_5.value.code == "invalid_credentials"
    assert exc_5.value.http_status == 401

    # Confirm all 5 error messages and representations are completely identical
    messages = [e.value.message for e in (exc_1, exc_2, exc_3, exc_4, exc_5)]
    assert len(set(messages)) == 1


# ==============================================================================
# BR-AUTH-05: Dummy Verification on Missing / Uninitialized User (T-AUTH-05-b ★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-05")
def test_br_auth_05_b_dummy_verify_called_for_missing_user(
    auth_service: AuthService,
) -> None:
    """T-AUTH-05-b: dummy_verify_password must be called when user email does not exist."""
    with patch("app.services.auth.dummy_verify_password") as mock_dummy:
        mock_dummy.return_value = False
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate("ghost.user@bloodday.local", "any_password_attempt")

        mock_dummy.assert_called_once()


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-05")
def test_br_auth_05_b_dummy_verify_called_for_uninitialized_password(
    db_session: Session,
    auth_service: AuthService,
) -> None:
    """T-AUTH-05-b: dummy_verify_password must be called when user has no password_hash set."""
    site = create_site(db_session, name="Centre Dummy")
    user = create_user(
        db_session,
        email="uninitialized@bloodday.local",
        password_hash=None,
        role="supervisor",
        site_id=site.id,
    )
    with patch("app.services.auth.dummy_verify_password") as mock_dummy:
        mock_dummy.return_value = False
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(user.email, "any_password_attempt")

        mock_dummy.assert_called_once()


# ==============================================================================
# BR-AUTH-06: Account Lockout Logic (T-AUTH-06-a/b/c/d/e ★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-06")
def test_br_auth_06_a_lockout_after_five_failed_attempts(
    db_session: Session,
    auth_service: AuthService,
    active_supervisor: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-06-a: 5 consecutive failed logins lock account for 15 minutes."""
    t0 = datetime(2026, 10, 17, 9, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    # 4 consecutive failures: account not locked yet
    for i in range(1, 5):
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(active_supervisor.email, "wrong_attempt")
        db_session.refresh(active_supervisor)
        assert active_supervisor.failed_login_count == i
        assert active_supervisor.locked_until is None

    # 5th failure triggers 15-minute lockout
    with pytest.raises(InvalidCredentialsError):
        auth_service.authenticate(active_supervisor.email, "wrong_attempt")
    db_session.refresh(active_supervisor)
    assert active_supervisor.failed_login_count == 5
    assert active_supervisor.locked_until == t0 + timedelta(minutes=15)


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-06")
def test_br_auth_06_b_correct_password_rejected_during_lockout(
    db_session: Session,
    auth_service: AuthService,
    active_supervisor: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-06-b: Even correct password must be rejected with 401 while locked_until > now."""
    t0 = datetime(2026, 10, 17, 9, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    # Trigger lockout
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(active_supervisor.email, "bad_attempt")

    # Fast forward 5 minutes (still locked for 10 more minutes)
    fake_clock.advance(timedelta(minutes=5))

    # Attempt login with the CORRECT password -> must fail uniformly
    with pytest.raises(InvalidCredentialsError):
        auth_service.authenticate(active_supervisor.email, TEST_PASSWORD)


@pytest.mark.db
@pytest.mark.security
@pytest.mark.br("BR-AUTH-06")
def test_br_auth_06_c_login_succeeds_and_resets_counters_after_lockout_expires(
    db_session: Session,
    auth_service: AuthService,
    active_supervisor: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-06-c: After 15 minutes have passed, correct login succeeds and resets failure count."""
    t0 = datetime(2026, 10, 17, 9, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    # Lock account
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(active_supervisor.email, "bad_attempt")

    # Advance 15 minutes + 1 second
    fake_clock.advance(timedelta(minutes=15, seconds=1))

    # Correct login succeeds
    result = auth_service.authenticate(active_supervisor.email, TEST_PASSWORD)
    assert result.user.id == active_supervisor.id
    assert result.raw_session_token is not None

    db_session.refresh(active_supervisor)
    assert active_supervisor.failed_login_count == 0
    assert active_supervisor.locked_until is None


@pytest.mark.db
@pytest.mark.security
@pytest.mark.br("BR-AUTH-06")
def test_br_auth_06_d_lockout_duration_doubling_and_capping(
    db_session: Session,
    auth_service: AuthService,
    active_supervisor: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-06-d: Lockout duration doubles on repeat lockouts (15m -> 30m -> 60m cap)."""
    t0 = datetime(2026, 10, 17, 9, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    # Lockout 1: 5 failures -> 15 min
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(active_supervisor.email, "bad")
    db_session.refresh(active_supervisor)
    assert active_supervisor.locked_until == t0 + timedelta(minutes=15)

    # Advance past lockout 1
    t1 = t0 + timedelta(minutes=16)
    fake_clock.set_time(t1)

    # Lockout 2: 5 more failures (total 10) -> 30 min
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(active_supervisor.email, "bad")
    db_session.refresh(active_supervisor)
    assert active_supervisor.locked_until == t1 + timedelta(minutes=30)

    # Advance past lockout 2
    t2 = t1 + timedelta(minutes=31)
    fake_clock.set_time(t2)

    # Lockout 3: 5 more failures (total 15) -> 60 min (capped)
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(active_supervisor.email, "bad")
    db_session.refresh(active_supervisor)
    assert active_supervisor.locked_until == t2 + timedelta(minutes=60)

    # Advance past lockout 3
    t3 = t2 + timedelta(minutes=61)
    fake_clock.set_time(t3)

    # Lockout 4: 5 more failures (total 20) -> still 60 min cap
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(active_supervisor.email, "bad")
    db_session.refresh(active_supervisor)
    assert active_supervisor.locked_until == t3 + timedelta(minutes=60)


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-06")
def test_br_auth_06_e_admin_unlocks_user_instantly(
    db_session: Session,
    auth_service: AuthService,
    active_supervisor: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-06-e: Admin unlock clears lockout immediately without waiting."""
    t0 = datetime(2026, 10, 17, 9, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    # Lock account
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth_service.authenticate(active_supervisor.email, "bad")
    db_session.refresh(active_supervisor)
    assert active_supervisor.locked_until is not None

    # Admin unlocks user
    auth_service.unlock_user(active_supervisor.id)

    db_session.refresh(active_supervisor)
    assert active_supervisor.locked_until is None
    assert active_supervisor.failed_login_count == 0

    # User can log in immediately
    result = auth_service.authenticate(active_supervisor.email, TEST_PASSWORD)
    assert result.user.id == active_supervisor.id


# ==============================================================================
# BR-AUTH-04 & BR-AUTH-08: Argon2id Rehash & MFA Session Issuance
# ==============================================================================


@pytest.mark.db
@pytest.mark.br("BR-AUTH-04")
def test_br_auth_04_automatic_rehash_on_login(
    db_session: Session,
    auth_service: AuthService,
    active_supervisor: User,
) -> None:
    """BR-AUTH-04: Outdated hash parameters are automatically rehashed on successful login."""
    # Create outdated weak hash
    old_hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        hash_len=16,
        salt_len=8,
        type=Type.ID,
    )
    old_hash = old_hasher.hash(TEST_PASSWORD)
    active_supervisor.password_hash = old_hash
    db_session.flush()

    # Login succeeds
    auth_service.authenticate(active_supervisor.email, TEST_PASSWORD)
    db_session.refresh(active_supervisor)

    # Stored password_hash must now have been upgraded to configured RFC 9106 parameters (64 MiB)
    assert active_supervisor.password_hash != old_hash
    assert "$m=65536" in active_supervisor.password_hash


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-08")
def test_br_auth_08_mfa_requirement_for_admin_and_supervisor(
    db_session: Session,
    auth_service: AuthService,
    active_admin: User,
    active_supervisor: User,
) -> None:
    """BR-AUTH-08: Admin logins require MFA; supervisors require MFA only if totp_enabled."""
    # 1. Admin login -> requires MFA, session is interim (mfa_verified=False)
    admin_result = auth_service.authenticate(active_admin.email, TEST_PASSWORD)
    assert admin_result.mfa_required is True
    assert admin_result.session.mfa_verified is False
    assert admin_result.user.last_login_at is None  # Not updated until MFA verified

    # 2. Supervisor without MFA -> does NOT require MFA, session is verified
    sup_result = auth_service.authenticate(active_supervisor.email, TEST_PASSWORD)
    assert sup_result.mfa_required is False
    assert sup_result.session.mfa_verified is True
    assert sup_result.user.last_login_at is not None

    # 3. Supervisor with MFA enabled -> requires MFA
    active_supervisor.totp_enabled = True
    db_session.flush()

    sup_mfa_result = auth_service.authenticate(active_supervisor.email, TEST_PASSWORD)
    assert sup_mfa_result.mfa_required is True
    assert sup_mfa_result.session.mfa_verified is False
