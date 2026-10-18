"""Database integration tests for AuditRepository and AuditService (P3.12).

Per:
- docs/06-business-rules.md §13:
  - BR-AUDIT-01 (★): No PII in details.
  - BR-AUDIT-03 (★): Same transaction; failure aborts operation.
  - BR-AUDIT-05 (★): Completeness of audit events.
- docs/07-test-cases.md §5.13:
  - T-AUDIT-01-a (★): Sentinel leak test across all recorded audit entries.
  - T-AUDIT-03-a (★): Failure injection in audit insertion rolls back the entire operation.
  - T-AUDIT-05-a: All approved auth action names verified in database.
"""

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.security import hash_password, verify_password
from app.models.audit import AuditLog
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user

TEST_PASSWORD = "OldPassword123!"  # pragma: allowlist secret
NEW_PASSWORD = "NewPassword123!"  # pragma: allowlist secret


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0))


@pytest.fixture
def audit_repo(db_session: Session) -> AuditRepository:
    return AuditRepository(db_session)


@pytest.fixture
def audit_service(audit_repo: AuditRepository, fake_clock: FakeClock) -> AuditService:
    return AuditService(audit_repo, clock=fake_clock)


@pytest.fixture
def test_user(db_session: Session) -> User:
    site = create_site(db_session, name="Centre Audit DB")
    return create_user(
        db_session,
        email="user.audit@bloodday.local",
        password_hash=hash_password(TEST_PASSWORD),
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


# ==============================================================================
# 1. AuditRepository Database Tests
# ==============================================================================


@pytest.mark.db
def test_audit_repository_create_and_query(
    audit_repo: AuditRepository,
    test_user: User,
    fake_clock: FakeClock,
) -> None:
    """Verify AuditRepository creates an AuditLog row and can retrieve by ID and filters."""
    entry = AuditLog(
        at=fake_clock.now(),
        action="auth.login_success",
        entity_type="user",
        entity_id=test_user.id,
        actor_user_id=test_user.id,
        details={"ip_hash": "test_hash_123"},
    )
    created = audit_repo.create(entry)
    assert created.id is not None

    fetched = audit_repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.action == "auth.login_success"
    assert fetched.entity_id == test_user.id
    assert fetched.actor_user_id == test_user.id
    assert fetched.details == {"ip_hash": "test_hash_123"}

    # List events with filters
    results = audit_repo.list_events(action="auth.login_success", entity_id=test_user.id)
    assert len(results) >= 1
    assert any(r.id == created.id for r in results)


# ==============================================================================
# 2. AuditService Auth Events Persistence (BR-AUDIT-05 / T-AUDIT-05-a)
# ==============================================================================


@pytest.mark.db
@pytest.mark.br("BR-AUDIT-05")
def test_t_audit_05_a_all_auth_events_recorded_in_db(
    audit_service: AuditService,
    audit_repo: AuditRepository,
    test_user: User,
) -> None:
    """Verify each of the 7 standard auth events can be logged and persisted with correct schemas."""
    # 1. auth.login_success
    e1 = audit_service.log_login_success(test_user.id, ip_hash="hash_login_ok")
    assert e1.id is not None
    assert e1.action == "auth.login_success"
    assert e1.actor_user_id == test_user.id
    assert e1.details == {"ip_hash": "hash_login_ok"}

    # 2. auth.login_failure
    e2 = audit_service.log_login_failure(
        reason="invalid_password",
        ip_hash="hash_fail",
        user_id=test_user.id,
    )
    assert e2.id is not None
    assert e2.action == "auth.login_failure"
    assert e2.actor_user_id is None
    assert e2.details == {"reason": "invalid_password", "ip_hash": "hash_fail"}

    # 3. auth.lockout
    e3 = audit_service.log_lockout(
        test_user.id,
        lock_minutes=15,
        failed_attempts=5,
        ip_hash="hash_fail",
    )
    assert e3.id is not None
    assert e3.action == "auth.lockout"
    assert e3.details == {
        "lock_minutes": 15,
        "failed_attempts": 5,
        "ip_hash": "hash_fail",
    }

    # 4. auth.mfa_enabled
    e4 = audit_service.log_mfa_enabled(test_user.id, method="totp")
    assert e4.id is not None
    assert e4.action == "auth.mfa_enabled"
    assert e4.actor_user_id == test_user.id
    assert e4.details == {"method": "totp"}

    # 5. auth.password_changed
    e5 = audit_service.log_password_changed(
        test_user.id,
        actor_user_id=test_user.id,
        reason="user_change",
    )
    assert e5.id is not None
    assert e5.action == "auth.password_changed"
    assert e5.actor_user_id == test_user.id
    assert e5.details == {"reason": "user_change"}

    # 6. auth.password_reset
    e6 = audit_service.log_password_reset(test_user.id, method="token")
    assert e6.id is not None
    assert e6.action == "auth.password_reset"
    assert e6.details == {"method": "token"}

    # 7. auth.session_revoked
    e7 = audit_service.log_session_revoked(
        test_user.id,
        reason="logout",
        actor_user_id=test_user.id,
        count=1,
    )
    assert e7.id is not None
    assert e7.action == "auth.session_revoked"
    assert e7.details == {"reason": "logout", "count": 1}

    # Verify all 7 events exist in DB
    logged_actions = {
        row.action for row in audit_repo.list_events(entity_id=test_user.id, limit=50)
    }
    expected_actions = {
        "auth.login_success",
        "auth.login_failure",
        "auth.lockout",
        "auth.mfa_enabled",
        "auth.password_changed",
        "auth.password_reset",
        "auth.session_revoked",
    }
    assert expected_actions.issubset(logged_actions)


# ==============================================================================
# 3. Atomic Failure Rollback (BR-AUDIT-03 ★ / T-AUDIT-03-a ★)
# ==============================================================================


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUDIT-03")
def test_t_audit_03_a_failure_injection_rolls_back_operation(
    db_session: Session,
    test_user: User,
    fake_clock: FakeClock,
) -> None:
    """Verify that if audit log persistence fails, the calling domain operation fails and state is rolled back.

    Per BR-AUDIT-03 ★:
    "تُكتب السجلات في معاملة العملية نفسها، فإن فشل التدقيق فشلت العملية (لا فعل حساس بلا أثر)"
    """
    user_repo = UserRepository(db_session)
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=fake_clock)
    audit_service = AuditService(AuditRepository(db_session), clock=fake_clock)

    auth_service = AuthService(
        user_repo=user_repo,
        session_service=session_service,
        audit_service=audit_service,
        clock=fake_clock,
    )

    original_password_hash = test_user.password_hash

    # Inject failure into audit_repo.create during change_password within a savepoint
    savepoint = db_session.begin_nested()
    with (
        patch.object(
            audit_service.audit_repo,
            "create",
            side_effect=RuntimeError("Simulated database audit disk write error"),
        ),
        pytest.raises(RuntimeError, match="Simulated database audit disk write error"),
    ):
        auth_service.change_password(
            test_user.id,
            current_password=TEST_PASSWORD,
            new_password=NEW_PASSWORD,
        )

    # Roll back savepoint to reflect request boundary transaction rollback
    savepoint.rollback()

    # Re-fetch user: password must NOT have changed!
    db_session.refresh(test_user)
    assert test_user.password_hash == original_password_hash
    assert verify_password(TEST_PASSWORD, test_user.password_hash)
    assert not verify_password(NEW_PASSWORD, test_user.password_hash)
