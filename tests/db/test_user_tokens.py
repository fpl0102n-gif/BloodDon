"""Database and domain tests for user invitation tokens and password resets (P3.5).

Traces to:
- BR-AUTH-02 & T-AUTH-02-a (★): Invitation tokens stored as SHA-256 hash only.
- BR-AUTH-02 & T-AUTH-02-b (★): Invitation expires after 72 hours -> 400 invalid_token.
- BR-AUTH-02 & T-AUTH-02-c (★): Invitation single-use; second usage -> 400 invalid_token.
- BR-AUTH-02 & T-AUTH-02-d (★): Resending invitation invalidates old token -> 400 invalid_token.
- BR-AUTH-03 & T-AUTH-03-a/b: Password policy validation on invitation acceptance and reset.
- BR-AUTH-13: Password reset invalidates all existing user sessions.
- BR-AUTH-15 & T-AUTH-15-a (★): Password reset request uniform response for existing and non-existing email.
- BR-AUTH-15 & T-AUTH-15-b (★): Unknown email triggers zero emails in Mailer.
- BR-AUTH-15 & T-AUTH-15-c (★): Reset token expires after 1 hour / single-use -> 400 invalid_token.
- BR-AUTH-15 & T-AUTH-15-d (★): Password reset preserves MFA requirement (MFA is not cleared).
- BR-USER-09 & T-USER-09-a: Email delivery failure returns invitation link once to admin.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.config import Settings
from app.core.errors import InvalidTokenError, ValidationError
from app.core.security import hash_token
from app.mail.base import FakeMailer
from app.models.user import UserToken
from app.repositories.sessions import SessionRepository
from app.repositories.user_tokens import UserTokenRepository
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user


@pytest.fixture
def auth_components(db_session: Session) -> tuple[AuthService, FakeClock, FakeMailer]:
    """Provide isolated AuthService with FakeClock and FakeMailer bound to db_session."""
    clock = FakeClock()
    mailer = FakeMailer()
    settings = Settings(app_env="test", public_base_url="https://bloodday.dz")

    user_repo = UserRepository(db_session)
    session_repo = SessionRepository(db_session)
    token_repo = UserTokenRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    service = AuthService(
        user_repo=user_repo,
        session_service=session_service,
        token_repo=token_repo,
        mailer=mailer,
        clock=clock,
        settings=settings,
    )
    return service, clock, mailer


# ==============================================================================
# BR-AUTH-02 & T-AUTH-02: Invitation Tokens (★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-02")
def test_br_auth_02_a_token_stored_as_hash_only(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-02-a: In DB, user_tokens contains SHA-256 hash only; raw token is never stored."""
    service, _, mailer = auth_components
    site = create_site(db_session, name="Site Token A")
    user = create_user(
        db_session,
        email="invitee.hash@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=False,
        password_hash=None,
    )

    raw_token, invite_url, email_sent = service.create_invitation(user.id, send_email=True)
    assert email_sent is True
    assert raw_token in invite_url

    # Check database record directly
    stmt = select(UserToken).where(UserToken.user_id == user.id)
    tokens = list(db_session.execute(stmt).scalars().all())
    assert len(tokens) == 1
    stored_token = tokens[0]

    # Stored token_hash must match hash_token(raw_token) and NOT equal raw_token
    expected_hash = hash_token(raw_token)
    assert stored_token.token_hash == expected_hash
    assert stored_token.token_hash != raw_token
    assert len(stored_token.token_hash) == 64  # SHA-256 hex string
    assert stored_token.purpose == "invite"
    assert stored_token.used_at is None

    # Check outgoing mail
    assert len(mailer.outbox) == 1
    assert mailer.outbox[0].to == "invitee.hash@bloodday.local"
    assert invite_url in mailer.outbox[0].body_text


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-02")
def test_br_auth_02_b_invitation_expires_after_72_hours(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-02-b: Invitation token used after 72 hours and 1 second raises 400 invalid_token."""
    service, clock, _ = auth_components
    site = create_site(db_session, name="Site Token B")
    user = create_user(
        db_session,
        email="invitee.expiry@bloodday.local",
        full_name="كريم بن سالم",
        role="supervisor",
        site_id=site.id,
        is_active=False,
        password_hash=None,
    )

    raw_token, _, _ = service.create_invitation(user.id)

    # Fast forward clock by 72 hours and 1 second
    clock.advance(timedelta(hours=72, seconds=1))

    # Acceptance after expiration must fail
    with pytest.raises(InvalidTokenError) as exc_info:
        service.accept_invitation(raw_token, "ValidPassphrase2026!")  # pragma: allowlist secret

    assert exc_info.value.code == "invalid_token"
    assert exc_info.value.http_status == 400

    # User remains inactive
    db_session.refresh(user)
    assert user.is_active is False
    assert user.password_hash is None


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-02")
def test_br_auth_02_c_invitation_single_use(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-02-c: Invitation token is single-use; second usage attempt raises 400 invalid_token."""
    service, _, _ = auth_components
    site = create_site(db_session, name="Site Token C")
    user = create_user(
        db_session,
        email="invitee.single@bloodday.local",
        full_name="فاطمة الزهراء",
        role="supervisor",
        site_id=site.id,
        is_active=False,
        password_hash=None,
    )

    raw_token, _, _ = service.create_invitation(user.id)

    # First acceptance succeeds
    activated_user = service.accept_invitation(
        raw_token, "SecurePassphrase123!"
    )  # pragma: allowlist secret
    assert activated_user.is_active is True
    assert activated_user.password_hash is not None

    # Second acceptance with same token must fail
    with pytest.raises(InvalidTokenError) as exc_info:
        service.accept_invitation(
            raw_token, "AnotherValidPassphrase123!"
        )  # pragma: allowlist secret

    assert exc_info.value.code == "invalid_token"
    assert exc_info.value.http_status == 400


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-02")
def test_br_auth_02_d_invitation_resend_invalidates_old_token(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-02-d: Re-issuing an invitation invalidates prior active token; old token returns invalid_token."""
    service, clock, _ = auth_components
    site = create_site(db_session, name="Site Token D")
    user = create_user(
        db_session,
        email="invitee.resend@bloodday.local",
        full_name="يوسف بن أحمد",
        role="supervisor",
        site_id=site.id,
        is_active=False,
        password_hash=None,
    )

    old_token, _, _ = service.create_invitation(user.id)
    clock.advance(timedelta(minutes=10))

    # Resend invitation produces new token and invalidates old token
    new_token, _, _ = service.create_invitation(user.id)
    assert old_token != new_token

    # Attempting to accept with old token must fail
    with pytest.raises(InvalidTokenError) as exc_info:
        service.accept_invitation(old_token, "ValidPassphrase123!")  # pragma: allowlist secret

    assert exc_info.value.code == "invalid_token"

    # Accepting with new token succeeds
    activated_user = service.accept_invitation(
        new_token, "ValidPassphrase123!"
    )  # pragma: allowlist secret
    assert activated_user.is_active is True


# ==============================================================================
# BR-AUTH-03 & T-AUTH-03: Password Policy on Invitation Acceptance
# ==============================================================================


@pytest.mark.db
@pytest.mark.br("BR-AUTH-03")
def test_br_auth_03_password_policy_on_invite_acceptance(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-03-a/b: Password policy enforced during invitation acceptance."""
    service, _, _ = auth_components
    site = create_site(db_session, name="Site Policy")
    user = create_user(
        db_session,
        email="user.policy@bloodday.local",
        full_name="أحمد بن علي",
        role="supervisor",
        site_id=site.id,
        is_active=False,
        password_hash=None,
    )

    raw_token, _, _ = service.create_invitation(user.id)

    # 1. Short password (<10 chars)
    with pytest.raises(ValidationError, match="at least 10 characters"):
        service.accept_invitation(raw_token, "short123")  # pragma: allowlist secret

    # 2. Common password
    with pytest.raises(ValidationError, match="too common"):
        service.accept_invitation(raw_token, "password123")  # pragma: allowlist secret

    # 3. Password containing email prefix
    with pytest.raises(ValidationError, match="email"):
        service.accept_invitation(raw_token, "Secret_user.policy_Pass!")  # pragma: allowlist secret

    # 4. Password without special symbols (passphrase) must SUCCEED (T-AUTH-03-b)
    activated = service.accept_invitation(
        raw_token, "correct horse battery"
    )  # pragma: allowlist secret
    assert activated.is_active is True


# ==============================================================================
# BR-AUTH-15 & T-AUTH-15: Password Reset Flows (★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-15")
def test_br_auth_15_a_uniform_response_for_existing_and_non_existing_email(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-15-a: Requesting password reset returns identical uniform response whether email exists or not."""
    service, _, mailer = auth_components
    site = create_site(db_session, name="Site Reset A")
    create_user(
        db_session,
        email="existing.user@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )

    # 1. Existing user -> uniform completion (for 204), email sent
    service.request_password_reset("existing.user@bloodday.local")
    assert len(mailer.outbox) == 1
    assert mailer.outbox[0].to == "existing.user@bloodday.local"

    # 2. Non-existing user -> uniform completion (for 204)
    service.request_password_reset("non_existing@bloodday.local")
    # No extra email dispatched for non-existent user
    assert len(mailer.outbox) == 1


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-15")
def test_br_auth_15_b_unknown_email_triggers_no_mailer(
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-15-b: Non-existing email produces zero messages in Mailer."""
    service, _, mailer = auth_components
    assert len(mailer.outbox) == 0

    service.request_password_reset("nobody.ever@bloodday.local")
    assert len(mailer.outbox) == 0


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-15")
def test_br_auth_15_c_reset_token_expires_after_1_hour_and_single_use(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-15-c: Reset token expires after 1 hour and cannot be reused."""
    service, clock, mailer = auth_components
    site = create_site(db_session, name="Site Reset C")
    user = create_user(
        db_session,
        email="reset.expire@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )

    service.request_password_reset(user.email)
    assert len(mailer.outbox) == 1
    reset_email_body = mailer.outbox[0].body_text
    # Extract token from URL
    raw_token = reset_email_body.split("token=")[1].split()[0].strip()

    # Fast forward clock past 1 hour (1 hour + 5 seconds)
    clock.advance(timedelta(hours=1, seconds=5))

    with pytest.raises(InvalidTokenError) as exc_info:
        service.reset_password(raw_token, "BrandNewPassword123!")  # pragma: allowlist secret

    assert exc_info.value.code == "invalid_token"

    # Rewind clock back to within 1-hour window for single-use test with fresh request
    clock.advance(timedelta(seconds=1))
    service.request_password_reset(user.email)
    raw_token_2 = mailer.outbox[1].body_text.split("token=")[1].split()[0].strip()

    # First reset succeeds
    reset_user = service.reset_password(
        raw_token_2, "BrandNewPassword123!"
    )  # pragma: allowlist secret
    assert reset_user.id == user.id

    # Second reset with same token fails (single use)
    with pytest.raises(InvalidTokenError) as exc_info2:
        service.reset_password(raw_token_2, "YetAnotherPassword123!")  # pragma: allowlist secret

    assert exc_info2.value.code == "invalid_token"


@pytest.mark.db
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-15")
def test_br_auth_15_d_password_reset_preserves_mfa_requirement(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-AUTH-15-d: Password reset invalidates sessions, accepts new password, and PRESERVES MFA."""
    service, _, mailer = auth_components
    site = create_site(db_session, name="Site Reset D")
    user = create_user(
        db_session,
        email="sup_mfa.reset@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
        totp_enabled=True,
        totp_secret_enc="ENCRYPTED_MFA_SECRET_DATA",  # pragma: allowlist secret
    )

    # 1. Establish an active session prior to password reset
    _old_raw_token, old_session = service.session_service.create_session(
        user.id,
        role=user.role,
        mfa_verified=True,
    )
    assert old_session.revoked_at is None

    # 2. Request and complete password reset
    service.request_password_reset(user.email)
    raw_token = mailer.outbox[0].body_text.split("token=")[1].split()[0].strip()
    new_password = "MyNewStrongPassword2026!"  # pragma: allowlist secret
    service.reset_password(raw_token, new_password)

    # 3. Old session must be revoked per BR-AUTH-13
    db_session.refresh(old_session)
    assert old_session.revoked_at is not None

    # 4. MFA must NOT be cleared per BR-AUTH-15 (totp_enabled remains True)
    db_session.refresh(user)
    assert user.totp_enabled is True
    assert user.totp_secret_enc == "ENCRYPTED_MFA_SECRET_DATA"  # pragma: allowlist secret

    # 5. Login with new password succeeds and requires MFA
    login_result = service.authenticate(user.email, new_password)
    assert login_result.user.id == user.id
    assert login_result.mfa_required is True
    assert login_result.session.mfa_verified is False


# ==============================================================================
# BR-USER-09 & T-USER-09-a: Fallback Invitation Link on Mailer Failure
# ==============================================================================


@pytest.mark.db
@pytest.mark.br("BR-USER-09")
def test_br_user_09_a_invitation_mailer_failure_returns_link(
    db_session: Session,
    auth_components: tuple[AuthService, FakeClock, FakeMailer],
) -> None:
    """T-USER-09-a: If Mailer fails during invitation, raw token and link are still returned."""
    service, _, mailer = auth_components
    site = create_site(db_session, name="Site Fallback")
    user = create_user(
        db_session,
        email="fallback.user@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=False,
        password_hash=None,
    )

    # Inject failure into mailer
    mailer.inject_failure = True

    raw_token, invite_url, email_sent = service.create_invitation(user.id, send_email=True)
    assert email_sent is False
    assert raw_token is not None
    assert f"token={raw_token}" in invite_url

    # The invitation token was persisted in DB and can still be accepted
    activated_user = service.accept_invitation(
        raw_token, "ValidPassphrase123!"
    )  # pragma: allowlist secret
    assert activated_user.is_active is True
