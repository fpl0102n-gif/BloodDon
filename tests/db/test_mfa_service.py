"""Database tests for Multi-Factor Authentication (MFA) service (P3.7).

Implements tests for:
- T-AUTH-08-a (★) & T-AUTH-08-b (★): Interim session handling and promotion to mfa_verified=True.
- T-AUTH-09-a (★): Valid TOTP code verification succeeds.
- T-AUTH-09-b (★): Acceptance window ±1 step accepted, ±2 steps rejected.
- T-AUTH-09-c (★): Anti-replay: code used once cannot be reused (totp_last_used_step).
- T-AUTH-09-d (★): Interim session revoked after 5 failed MFA verification attempts.
- T-AUTH-10-a: Enrollment displays 8 single-use recovery codes, stored hashed.
- T-AUTH-10-b: Backup recovery code is single-use; second use is rejected.
- BR-AUTH-17: Administrator cannot disable MFA on their account.
- BR-AUTH-13: Administrative reset of MFA revokes all user sessions and recovery codes.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pyotp
import pytest
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.config import Settings
from app.core.errors import (
    ForbiddenError,
    InvalidCredentialsError,
    SessionExpiredError,
    ValidationError,
)
from app.core.security import decrypt_totp_secret, hash_password, hash_token
from app.models.campaign import Site
from app.models.user import MfaRecoveryCode, User
from app.repositories.mfa import MfaRecoveryCodeRepository
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.services.mfa import MfaService
from app.services.sessions import SessionService

TEST_FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # pragma: allowlist secret


@dataclass
class MfaBundle:
    """Convenience bundle holding service and associated dependencies for tests."""

    service: MfaService
    sessions: SessionService
    recovery: MfaRecoveryCodeRepository


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing settings configured with a valid Fernet key."""
    return Settings(
        app_env="dev",
        totp_encryption_key=TEST_FERNET_KEY,
        secret_key="0" * 64,
        ip_hash_salt="0" * 32,
    )


@pytest.fixture
def bundle(
    db_session: Session,
    fake_clock: FakeClock,
    test_settings: Settings,
) -> MfaBundle:
    """Fixture assembling MfaService and repositories with FakeClock and test settings."""
    user_repo = UserRepository(db_session)
    session_repo = SessionRepository(db_session)
    recovery_repo = MfaRecoveryCodeRepository(db_session)
    session_service = SessionService(session_repo, clock=fake_clock, settings=test_settings)
    mfa_service = MfaService(
        user_repo=user_repo,
        recovery_repo=recovery_repo,
        session_service=session_service,
        clock=fake_clock,
        settings=test_settings,
    )
    return MfaBundle(
        service=mfa_service,
        sessions=session_service,
        recovery=recovery_repo,
    )


def _create_user(
    db_session: Session,
    *,
    role: str = "admin",
    email: str | None = None,
    site_id: uuid.UUID | None = None,
    totp_enabled: bool = False,
    totp_secret_enc: str | None = None,
    totp_last_used_step: int | None = None,
) -> User:
    """Helper to persist a test user with configurable MFA attributes."""
    if role == "supervisor" and site_id is None:
        site = Site(
            id=uuid.uuid4(),
            name=f"Site_{uuid.uuid4().hex[:6]}",
            address="123 Test St",
            is_active=True,
        )
        db_session.add(site)
        db_session.flush()
        site_id = site.id

    user = User(
        id=uuid.uuid4(),
        email=email or f"user_{uuid.uuid4().hex[:8]}@example.com",
        full_name="مستخدم تجريبي",
        role=role,
        site_id=site_id,
        password_hash=hash_password("ValidP@ssword123#"),  # pragma: allowlist secret
        is_active=True,
        totp_enabled=totp_enabled,
        totp_secret_enc=totp_secret_enc,
        totp_last_used_step=totp_last_used_step,
        failed_login_count=0,
    )
    db_session.add(user)
    db_session.flush()
    return user


# ==============================================================================
# T-AUTH-10-a & Setup Confirmation Tests
# ==============================================================================


@pytest.mark.db
@pytest.mark.br("BR-AUTH-10")
def test_t_auth_10_a_mfa_setup_and_recovery_codes_hash_storage(
    db_session: Session,
    bundle: MfaBundle,
) -> None:
    """T-AUTH-10-a: Setup generates 8 recovery codes; stored as hashes; secret is encrypted."""
    user = _create_user(db_session, role="admin", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)

    # 1. Base32 secret generated and encrypted
    assert len(setup_result.secret) == 32
    assert "otpauth://" in setup_result.provisioning_uri
    assert setup_result.secret in setup_result.provisioning_uri

    # 2. Database stores encrypted secret, not raw secret (BR-GEN-09)
    db_session.refresh(user)
    assert user.totp_secret_enc is not None
    assert user.totp_secret_enc != setup_result.secret
    decrypted = decrypt_totp_secret(user.totp_secret_enc, TEST_FERNET_KEY)
    assert decrypted == setup_result.secret

    # 3. totp_enabled remains False until confirmed
    assert user.totp_enabled is False

    # 4. Exactly 8 recovery codes generated and stored as SHA-256 hashes
    assert len(setup_result.recovery_codes) == 8
    for code in setup_result.recovery_codes:
        assert len(code) == 10
        assert code.isalnum()

    stored_codes = db_session.query(MfaRecoveryCode).filter_by(user_id=user.id).all()
    assert len(stored_codes) == 8
    for stored in stored_codes:
        assert stored.used_at is None
        # None of the stored hashes match plain recovery codes
        assert stored.code_hash not in setup_result.recovery_codes
        # Verify hashes match SHA-256 of plain codes
        assert any(hash_token(plain) == stored.code_hash for plain in setup_result.recovery_codes)


@pytest.mark.db
@pytest.mark.br("BR-AUTH-09")
def test_mfa_confirm_setup_with_code(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """Confirming enrollment with valid TOTP code enables totp_enabled and records step."""
    user = _create_user(db_session, role="supervisor", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)
    totp = pyotp.TOTP(setup_result.secret)

    # Rejects invalid confirmation code
    with pytest.raises(InvalidCredentialsError):
        bundle.service.confirm_mfa_setup(user.id, "000000")

    db_session.refresh(user)
    assert user.totp_enabled is False

    # Accepts valid confirmation code
    valid_code = totp.at(fake_clock.now())
    confirmed = bundle.service.confirm_mfa_setup(user.id, valid_code)
    assert confirmed is True

    db_session.refresh(user)
    assert user.totp_enabled is True
    assert user.totp_last_used_step is not None


# ==============================================================================
# T-AUTH-09-a, T-AUTH-09-b, T-AUTH-09-c: TOTP Verification & Anti-Replay
# ==============================================================================


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-09")
def test_t_auth_09_a_valid_totp_succeeds(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """T-AUTH-09-a ★: Valid TOTP code verification succeeds."""
    user = _create_user(db_session, role="admin", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)
    totp = pyotp.TOTP(setup_result.secret)
    valid_code = totp.at(fake_clock.now())
    bundle.service.confirm_mfa_setup(user.id, valid_code)

    # Advance clock by 30 seconds to the next step
    fake_clock.advance(30)
    next_code = totp.at(fake_clock.now())

    verified = bundle.service.verify_totp(user.id, next_code)
    assert verified is True


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-09")
def test_t_auth_09_b_window_plus_minus_one_accepted_two_rejected(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """T-AUTH-09-b ★: Accept ±1 step (current ±30s); strictly reject ±2 steps (±60s)."""
    user = _create_user(db_session, role="admin", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)
    totp = pyotp.TOTP(setup_result.secret)

    # Set clock to a steady step boundary
    t0 = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    # Confirm setup at t0 - 5 minutes
    t_confirm = datetime(2026, 10, 16, 11, 55, 0, tzinfo=UTC)
    fake_clock.set_time(t_confirm)
    confirm_code = totp.at(t_confirm)
    bundle.service.confirm_mfa_setup(user.id, confirm_code)

    # Advance to t0
    fake_clock.set_time(t0)
    cur_step = totp.timecode(t0)

    code_step_minus_2 = totp.generate_otp(cur_step - 2)
    code_step_minus_1 = totp.generate_otp(cur_step - 1)
    code_step_plus_1 = totp.generate_otp(cur_step + 1)
    code_step_plus_2 = totp.generate_otp(cur_step + 2)

    # Step -2 must be REJECTED
    with pytest.raises(InvalidCredentialsError):
        bundle.service.verify_totp(user.id, code_step_minus_2)

    # Step +2 must be REJECTED
    with pytest.raises(InvalidCredentialsError):
        bundle.service.verify_totp(user.id, code_step_plus_2)

    # Step -1 must be ACCEPTED
    assert bundle.service.verify_totp(user.id, code_step_minus_1) is True

    # Step +1 must be ACCEPTED
    assert bundle.service.verify_totp(user.id, code_step_plus_1) is True


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-09")
def test_t_auth_09_c_anti_replay_prevents_code_reuse(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """T-AUTH-09-c ★: Code that just succeeded cannot be reused (enforced via totp_last_used_step)."""
    user = _create_user(db_session, role="admin", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)
    totp = pyotp.TOTP(setup_result.secret)

    t0 = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)

    # Confirm setup at earlier time
    fake_clock.advance(-120)
    bundle.service.confirm_mfa_setup(user.id, totp.at(fake_clock.now()))

    # Return to t0 and verify
    fake_clock.set_time(t0)
    current_code = totp.at(fake_clock.now())

    # 1. First verification: SUCCEEDS
    assert bundle.service.verify_totp(user.id, current_code) is True

    # 2. Second verification with same code within the same 30s window: REJECTED
    with pytest.raises(InvalidCredentialsError, match="already been used"):
        bundle.service.verify_totp(user.id, current_code)

    # 3. Code from earlier step: also REJECTED
    cur_step = totp.timecode(fake_clock.now())
    earlier_code = totp.generate_otp(cur_step - 1)
    with pytest.raises(InvalidCredentialsError, match="already been used"):
        bundle.service.verify_totp(user.id, earlier_code)


# ==============================================================================
# T-AUTH-08-a, T-AUTH-08-b & T-AUTH-09-d: Interim Sessions and Failure Limits
# ==============================================================================


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-08")
def test_t_auth_08_b_interim_session_promoted_on_valid_mfa(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """T-AUTH-08-b ★: Valid MFA verification promotes interim session to mfa_verified=True."""
    user = _create_user(db_session, role="admin", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)
    totp = pyotp.TOTP(setup_result.secret)
    bundle.service.confirm_mfa_setup(user.id, totp.at(fake_clock.now()))

    # Create interim session (mfa_verified=False)
    fake_clock.advance(30)
    raw_token, session = bundle.sessions.create_session(
        user.id,
        role="admin",
        mfa_verified=False,
    )
    assert session.mfa_verified is False

    # Verify TOTP code with interim session attached
    valid_code = totp.at(fake_clock.now())
    result = bundle.service.verify_totp(user.id, valid_code, session=session)
    assert result is True

    # Session is now promoted
    assert session.mfa_verified is True
    assert session.revoked_at is None

    # validate_session confirms session is valid and active
    active_session, active_user = bundle.sessions.validate_session(raw_token)
    assert active_session.mfa_verified is True
    assert active_user.id == user.id


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-09")
def test_t_auth_09_d_interim_session_revoked_after_five_failed_attempts(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """T-AUTH-09-d ★: Interim session is revoked after 5 failed MFA verification attempts."""
    user = _create_user(db_session, role="admin", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)
    totp = pyotp.TOTP(setup_result.secret)
    bundle.service.confirm_mfa_setup(user.id, totp.at(fake_clock.now()))

    # Create interim session
    raw_token, session = bundle.sessions.create_session(
        user.id,
        role="admin",
        mfa_verified=False,
    )

    # 4 invalid attempts: session remains active
    for _ in range(4):
        with pytest.raises(InvalidCredentialsError):
            bundle.service.verify_totp(user.id, "000000", session=session)
        assert session.revoked_at is None

    # 5th invalid attempt: interim session is REVOKED
    with pytest.raises(InvalidCredentialsError):
        bundle.service.verify_totp(user.id, "000000", session=session)

    assert session.revoked_at is not None

    # Subsequent access using this session fails with SessionExpiredError
    with pytest.raises(SessionExpiredError):
        bundle.sessions.validate_session(raw_token)


# ==============================================================================
# T-AUTH-10-b: Recovery Code Single-Use Verification
# ==============================================================================


@pytest.mark.db
@pytest.mark.br("BR-AUTH-10")
def test_t_auth_10_b_recovery_code_single_use(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """T-AUTH-10-b: Recovery code first use succeeds, second use is rejected."""
    user = _create_user(db_session, role="admin", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)
    totp = pyotp.TOTP(setup_result.secret)
    bundle.service.confirm_mfa_setup(user.id, totp.at(fake_clock.now()))

    recovery_code = setup_result.recovery_codes[0]

    # Create interim session
    _, session = bundle.sessions.create_session(
        user.id,
        role="admin",
        mfa_verified=False,
    )

    # 1. First use of recovery code: SUCCEEDS
    verified = bundle.service.verify_recovery_code(user.id, recovery_code, session=session)
    assert verified is True
    assert session.mfa_verified is True

    # Recovery code is marked used in DB
    unused_count = bundle.recovery.count_unused(user.id)
    assert unused_count == 7

    # 2. Second use of same recovery code: REJECTED
    with pytest.raises(InvalidCredentialsError, match="already used"):
        bundle.service.verify_recovery_code(user.id, recovery_code)


@pytest.mark.db
@pytest.mark.br("BR-AUTH-09")
def test_unified_verify_mfa_gateway(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """Unified verify_mfa gateway routes correctly and enforces mutually exclusive arguments."""
    user = _create_user(db_session, role="supervisor", totp_enabled=False)

    setup_result = bundle.service.initiate_mfa_setup(user.id)
    totp = pyotp.TOTP(setup_result.secret)
    bundle.service.confirm_mfa_setup(user.id, totp.at(fake_clock.now()))

    # Mutually exclusive validation
    with pytest.raises(ValidationError):
        bundle.service.verify_mfa(user.id)

    with pytest.raises(ValidationError):
        bad_rec_code = "ABCDEF1234"  # pragma: allowlist secret
        bundle.service.verify_mfa(user.id, code="123456", recovery_code=bad_rec_code)

    # Route to TOTP
    fake_clock.advance(30)
    valid_code = totp.at(fake_clock.now())
    assert bundle.service.verify_mfa(user.id, code=valid_code) is True

    # Route to recovery code
    rec_code = setup_result.recovery_codes[1]
    assert bundle.service.verify_mfa(user.id, recovery_code=rec_code) is True


# ==============================================================================
# BR-AUTH-17 & BR-AUTH-13: Disabling & Administrative Reset
# ==============================================================================


@pytest.mark.db
@pytest.mark.br("BR-AUTH-17")
def test_br_auth_17_admin_cannot_disable_mfa(
    db_session: Session,
    bundle: MfaBundle,
) -> None:
    """BR-AUTH-17: Administrator cannot disable MFA on their own account."""
    admin_user = _create_user(db_session, role="admin", totp_enabled=True)

    with pytest.raises(ForbiddenError, match="Administrators cannot disable"):
        bundle.service.disable_mfa(admin_user, admin_user)


@pytest.mark.db
@pytest.mark.br("BR-AUTH-17")
def test_supervisor_can_disable_mfa(
    db_session: Session,
    fake_clock: FakeClock,
    bundle: MfaBundle,
) -> None:
    """Supervisor can disable MFA on their own account; recovery codes cleared."""
    sup_user = _create_user(db_session, role="supervisor", totp_enabled=False)

    # Enroll
    setup_result = bundle.service.initiate_mfa_setup(sup_user.id)
    totp = pyotp.TOTP(setup_result.secret)
    bundle.service.confirm_mfa_setup(sup_user.id, totp.at(fake_clock.now()))

    assert sup_user.totp_enabled is True
    assert bundle.recovery.count_unused(sup_user.id) == 8

    # Disable
    bundle.service.disable_mfa(sup_user, sup_user)

    db_session.refresh(sup_user)
    assert sup_user.totp_enabled is False
    assert sup_user.totp_secret_enc is None
    assert sup_user.totp_last_used_step is None
    assert bundle.recovery.count_unused(sup_user.id) == 0


@pytest.mark.db
@pytest.mark.br("BR-AUTH-13")
def test_br_auth_13_admin_reset_mfa_revokes_all_user_sessions(
    db_session: Session,
    bundle: MfaBundle,
) -> None:
    """BR-AUTH-13 & BR-USER-08: Admin resetting MFA clears MFA and revokes all active sessions."""
    admin_actor = _create_user(db_session, role="admin", email="admin1@example.com")
    target_user = _create_user(
        db_session,
        role="supervisor",
        email="sup1@example.com",
        totp_enabled=True,
    )

    # Target user has two active sessions
    raw_token1, _ = bundle.sessions.create_session(target_user.id, role="supervisor")
    raw_token2, _ = bundle.sessions.create_session(target_user.id, role="supervisor")

    # Target user has recovery codes
    code_hashes = [hash_token(f"RECCODE{i}") for i in range(8)]  # pragma: allowlist secret
    bundle.recovery.replace_recovery_codes(target_user.id, code_hashes)
    assert bundle.recovery.count_unused(target_user.id) == 8

    # Admin resets MFA
    bundle.service.reset_mfa_by_admin(admin_actor, target_user)

    # 1. MFA fields cleared
    db_session.refresh(target_user)
    assert target_user.totp_enabled is False
    assert target_user.totp_secret_enc is None
    assert target_user.totp_last_used_step is None
    assert bundle.recovery.count_unused(target_user.id) == 0

    # 2. Both sessions are revoked immediately (BR-AUTH-13)
    with pytest.raises(SessionExpiredError):
        bundle.sessions.validate_session(raw_token1)

    with pytest.raises(SessionExpiredError):
        bundle.sessions.validate_session(raw_token2)
