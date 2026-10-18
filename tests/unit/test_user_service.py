"""Unit tests for UserService (P4.5).

Per:
- docs/06-business-rules.md §11.2 (BR-USER-01..10, BR-AUTH-02, BR-AUTH-13)
- docs/07-test-cases.md §5.11 (T-USER-01..09, T-CONC-05)
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.clock import FakeClock
from app.core.errors import (
    EmailTakenError,
    ForbiddenError,
    LastAdminError,
)
from app.models.campaign import Site
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.repositories.sites import SiteRepository
from app.repositories.users import UserRepository
from app.schemas.user import UserAdminUpdateIn, UserInviteIn
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.users import UserService


@pytest.fixture
def mock_user_repo() -> MagicMock:
    return MagicMock(spec=UserRepository)


@pytest.fixture
def mock_session_repo() -> MagicMock:
    return MagicMock(spec=SessionRepository)


@pytest.fixture
def mock_site_repo() -> MagicMock:
    return MagicMock(spec=SiteRepository)


@pytest.fixture
def mock_auth_service() -> MagicMock:
    return MagicMock(spec=AuthService)


@pytest.fixture
def mock_audit_service() -> MagicMock:
    return MagicMock(spec=AuditService)


@pytest.fixture
def user_service(
    mock_user_repo: MagicMock,
    mock_session_repo: MagicMock,
    mock_site_repo: MagicMock,
    mock_auth_service: MagicMock,
    mock_audit_service: MagicMock,
) -> UserService:
    clock = FakeClock()
    return UserService(
        user_repo=mock_user_repo,
        session_repo=mock_session_repo,
        site_repo=mock_site_repo,
        auth_service=mock_auth_service,
        audit_service=mock_audit_service,
        clock=clock,
    )


@pytest.mark.br("BR-USER-01")
@pytest.mark.critical
def test_br_user_01_a_supervisor_requires_site(user_service: UserService) -> None:
    """T-USER-01-a: Supervisor creation requires site_id."""
    with pytest.raises(ValueError, match="Supervisor role requires a site_id"):
        UserInviteIn(
            email="sup@bloodday.local",
            full_name="مشرف ميداني",
            role="supervisor",
            site_id=None,
        )


@pytest.mark.br("BR-USER-01")
@pytest.mark.critical
def test_br_user_01_b_admin_forbids_site(user_service: UserService) -> None:
    """T-USER-01-a: Admin creation must not have a site_id."""
    with pytest.raises(ValueError, match="Admin role must not have a site_id"):
        UserInviteIn(
            email="admin@bloodday.local",
            full_name="مسؤول النظام",
            role="admin",
            site_id=uuid.uuid4(),
        )


@pytest.mark.br("BR-USER-01")
@pytest.mark.critical
def test_br_user_01_c_admin_without_site_succeeds(
    user_service: UserService,
    mock_user_repo: MagicMock,
    mock_auth_service: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-USER-01-b: Admin creation without site_id succeeds."""
    mock_user_repo.get_by_email.return_value = None

    def _create_side_effect(user: User) -> User:
        user.id = uuid.uuid4()
        return user

    mock_user_repo.create.side_effect = _create_side_effect
    mock_auth_service.create_invitation.return_value = (
        "token123",
        "https://example.com/invite",
        True,
    )

    payload = UserInviteIn(
        email="newadmin@bloodday.local",
        full_name="أدمن جديد",
        role="admin",
        site_id=None,
    )
    actor_id = uuid.uuid4()

    user, invite_url = user_service.invite_user(payload, actor_user_id=actor_id)

    assert user.role == "admin"
    assert user.site_id is None
    assert user.email == "newadmin@bloodday.local"
    assert invite_url is None  # email succeeded
    mock_user_repo.create.assert_called_once()
    mock_audit_service.log_event.assert_called_once()


@pytest.mark.br("BR-USER-02")
def test_br_user_02_a_email_case_insensitive_unique(
    user_service: UserService,
    mock_user_repo: MagicMock,
) -> None:
    """T-USER-02-a: Duplicate email (case-insensitive) raises 409 email_taken."""
    existing_user = User(
        id=uuid.uuid4(),
        email="existing@bloodday.local",
        full_name="مستخدم سابق",
        role="admin",
    )
    mock_user_repo.get_by_email.return_value = existing_user

    payload = UserInviteIn(
        email="EXISTING@bloodday.local",
        full_name="مستخدم مكرر",
        role="admin",
        site_id=None,
    )
    actor_id = uuid.uuid4()

    with pytest.raises(EmailTakenError, match="already exists"):
        user_service.invite_user(payload, actor_user_id=actor_id)


@pytest.mark.br("BR-USER-03")
@pytest.mark.critical
def test_br_user_03_a_self_modification_forbidden(
    user_service: UserService,
    mock_user_repo: MagicMock,
) -> None:
    """T-USER-03-a: Self-modification of account role/site is forbidden."""
    same_id = uuid.uuid4()
    admin_user = User(
        id=same_id,
        email="self@bloodday.local",
        full_name="الأدمن نفسه",
        role="admin",
        is_active=True,
    )
    mock_user_repo.get_by_id.return_value = admin_user

    payload = UserAdminUpdateIn(full_name="تعديل اسمي")

    with pytest.raises(ForbiddenError, match="Self-modification"):
        user_service.update_user(same_id, payload, actor_user_id=same_id)


@pytest.mark.br("BR-USER-03")
@pytest.mark.critical
def test_br_user_03_b_self_deactivation_forbidden(
    user_service: UserService,
    mock_user_repo: MagicMock,
) -> None:
    """T-USER-03-a: Self-deactivation returns 403 forbidden."""
    same_id = uuid.uuid4()
    admin_user = User(
        id=same_id,
        email="self@bloodday.local",
        full_name="الأدمن نفسه",
        role="admin",
        is_active=True,
    )
    mock_user_repo.get_by_id.return_value = admin_user

    with pytest.raises(ForbiddenError, match="Self-deactivation is strictly forbidden"):
        user_service.deactivate_user(same_id, actor_user_id=same_id)


@pytest.mark.br("BR-USER-04")
@pytest.mark.critical
def test_br_user_04_a_last_admin_deactivation_protection(
    user_service: UserService,
    mock_user_repo: MagicMock,
) -> None:
    """T-USER-04-a: Deactivating last active admin raises 409 last_admin."""
    target_admin_id = uuid.uuid4()
    actor_admin_id = uuid.uuid4()

    target_admin = User(
        id=target_admin_id,
        email="target@bloodday.local",
        full_name="الأدمن الهدف",
        role="admin",
        is_active=True,
    )
    mock_user_repo.get_by_id.return_value = target_admin
    mock_user_repo.count_active_admins.return_value = 1

    with pytest.raises(LastAdminError, match="last active administrator"):
        user_service.deactivate_user(target_admin_id, actor_user_id=actor_admin_id)

    mock_user_repo.acquire_admin_lock.assert_called_once()


@pytest.mark.br("BR-USER-04")
@pytest.mark.critical
def test_br_user_04_b_last_admin_demotion_protection(
    user_service: UserService,
    mock_user_repo: MagicMock,
    mock_site_repo: MagicMock,
) -> None:
    """T-USER-04-a: Demoting last active admin to supervisor raises 409 last_admin."""
    target_admin_id = uuid.uuid4()
    actor_admin_id = uuid.uuid4()
    site_id = uuid.uuid4()

    target_admin = User(
        id=target_admin_id,
        email="target@bloodday.local",
        full_name="الأدمن الهدف",
        role="admin",
        is_active=True,
    )
    mock_user_repo.get_by_id.return_value = target_admin
    mock_user_repo.count_active_admins.return_value = 1
    mock_site_repo.get_by_id.return_value = Site(id=site_id, name="موقع")

    payload = UserAdminUpdateIn(role="supervisor", site_id=site_id)

    with pytest.raises(LastAdminError, match="last active administrator"):
        user_service.update_user(target_admin_id, payload, actor_user_id=actor_admin_id)


@pytest.mark.br("BR-USER-05")
def test_br_user_05_a_bulk_deactivate_supervisors(
    user_service: UserService,
    mock_user_repo: MagicMock,
    mock_session_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """T-USER-05-a: Bulk deactivate supervisors marks them inactive and drops sessions."""
    mock_user_repo.bulk_deactivate_supervisors.return_value = 5
    actor_id = uuid.uuid4()

    count = user_service.bulk_deactivate_supervisors(actor_user_id=actor_id)

    assert count == 5
    mock_user_repo.bulk_deactivate_supervisors.assert_called_once()
    mock_session_repo.revoke_all_for_supervisors.assert_called_once()
    mock_audit_service.log_event.assert_called_once_with(
        action="user.bulk_deactivate_supervisors",
        entity_type="user",
        entity_id=actor_id,
        actor_user_id=actor_id,
        details={"count": 5},
    )


@pytest.mark.br("BR-USER-09")
def test_br_user_09_a_mailer_failure_returns_fallback_url(
    user_service: UserService,
    mock_user_repo: MagicMock,
    mock_auth_service: MagicMock,
) -> None:
    """T-USER-09-a: If mailer fails, invite_url is returned once to the caller."""
    mock_user_repo.get_by_email.return_value = None

    def _create_side_effect(user: User) -> User:
        user.id = uuid.uuid4()
        return user

    mock_user_repo.create.side_effect = _create_side_effect
    fallback_url = "https://example.com/auth/accept-invite?token=secret123"
    mock_auth_service.create_invitation.return_value = ("secret123", fallback_url, False)

    payload = UserInviteIn(
        email="failedmail@bloodday.local",
        full_name="مستخدم بلا بريد",
        role="admin",
        site_id=None,
    )
    actor_id = uuid.uuid4()

    _user, invite_url = user_service.invite_user(payload, actor_user_id=actor_id)

    assert invite_url == fallback_url


@pytest.mark.br("BR-AUTH-13")
@pytest.mark.critical
def test_br_auth_13_sessions_revoked_on_deactivate(
    user_service: UserService,
    mock_user_repo: MagicMock,
    mock_session_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """BR-AUTH-13: Invalidate all active sessions upon deactivation."""
    target_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    user = User(
        id=target_id,
        email="sup@bloodday.local",
        full_name="مشرف",
        role="supervisor",
        is_active=True,
    )
    mock_user_repo.get_by_id.return_value = user
    mock_session_repo.revoke_all_for_user.return_value = 3

    deactivated = user_service.deactivate_user(target_id, actor_user_id=actor_id)

    assert deactivated.is_active is False
    mock_session_repo.revoke_all_for_user.assert_called_once()
    mock_audit_service.log_session_revoked.assert_called_once_with(
        target_id,
        reason="account_deactivated",
        actor_user_id=actor_id,
        count=3,
    )


@pytest.mark.br("BR-AUTH-13")
@pytest.mark.critical
def test_br_auth_13_sessions_revoked_on_mfa_reset(
    user_service: UserService,
    mock_user_repo: MagicMock,
    mock_session_repo: MagicMock,
    mock_audit_service: MagicMock,
) -> None:
    """BR-AUTH-13: Invalidate all active sessions and clear codes on MFA reset."""
    target_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    user = User(
        id=target_id,
        email="admin@bloodday.local",
        full_name="أدمن",
        role="admin",
        is_active=True,
        totp_enabled=True,
        totp_secret_enc="encrypted_secret",
    )
    mock_user_repo.get_by_id.return_value = user
    mock_session_repo.revoke_all_for_user.return_value = 2

    resetted = user_service.reset_mfa(target_id, actor_user_id=actor_id)

    assert resetted.totp_enabled is False
    assert resetted.totp_secret_enc is None
    mock_user_repo.delete_mfa_recovery_codes.assert_called_once_with(target_id)
    mock_session_repo.revoke_all_for_user.assert_called_once()
    mock_audit_service.log_mfa_reset.assert_called_once_with(target_id, actor_user_id=actor_id)


@pytest.mark.br("BR-USER-07")
def test_br_user_07_admin_warning_single_admin(
    user_service: UserService,
    mock_user_repo: MagicMock,
) -> None:
    """T-USER-07-a: Warning produced when only one active admin exists."""
    mock_user_repo.count_active_admins.return_value = 1
    assert user_service.get_admin_warning() == "single_active_admin"

    mock_user_repo.count_active_admins.return_value = 2
    assert user_service.get_admin_warning() is None
