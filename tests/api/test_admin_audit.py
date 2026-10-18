"""Integration tests for Admin Audit API endpoints (P4.6).

Per:
- docs/04-api.md §20 (Audit log inspection: GET /admin/audit)
- docs/06-business-rules.md §13 (BR-AUDIT-01..05)
- docs/07-test-cases.md §5.13:
  - T-AUDIT-01-a (★): Sentinel leak test across all recorded audit entries.
  - T-AUDIT-04-a: System events have actor_user_id as null.
  - T-AUDIT-05-a: All approved action names verified.
- docs/03-auth-permissions.md §11.3 (AUDIT_READ permission matrix).
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import generate_csrf_token, get_clock, get_settings
from app.models.audit import AuditLog
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.repositories.sessions import SessionRepository
from app.services.audit import APPROVED_AUDIT_ACTIONS, AuditService
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user

# Sentinel test values per docs/07 §3.3
SENTINEL_FIRST_NAME = "ZZFIRST"
SENTINEL_LAST_NAME = "ZZLAST"
SENTINEL_PHONE = "0550123456"


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user."""
    return create_user(
        db_session,
        email="admin.audit@bloodday.local",
        full_name="مسؤول التدقيق الأول",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def second_admin_user(db_session: Session) -> User:
    """Create a second verified administrator user."""
    return create_user(
        db_session,
        email="admin.audit2@bloodday.local",
        full_name="مسؤول التدقيق الثاني",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create a supervisor user assigned to a test site."""
    site = create_site(db_session, name="موقع التدقيق التجريبي")
    return create_user(
        db_session,
        email="supervisor.audit@bloodday.local",
        full_name="مشرف التدقيق التجريبي",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


def create_auth_headers_and_cookies(
    test_client: TestClient,
    db_session: Session,
    user: User,
    *,
    mfa_verified: bool = True,
) -> dict[str, str]:
    """Helper to attach session cookie and CSRF token for a given user."""
    settings = get_settings()
    clock = get_clock()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    raw_token, _session = session_service.create_session(
        user.id,
        user.role,
        mfa_verified=mfa_verified,
    )

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    test_client.cookies.set(settings.cookie_name, raw_token)

    return {"X-CSRF-Token": csrf_token}


# ==============================================================================
# 1. Authorization & Role Matrix Tests (docs/03 §11.3)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
def test_anonymous_caller_rejected(test_client: TestClient) -> None:
    """Anonymous visitors receive 401 Not Authenticated on GET /admin/audit."""
    resp = test_client.get("/admin/audit")
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] == "not_authenticated"

    resp_v1 = test_client.get("/api/v1/admin/audit")
    assert resp_v1.status_code == 401
    assert resp_v1.json()["error"]["code"] == "not_authenticated"


@pytest.mark.api
def test_supervisor_forbidden(
    test_client: TestClient,
    db_session: Session,
    supervisor_user: User,
) -> None:
    """Site supervisors lack AUDIT_READ permission and receive 403 Forbidden."""
    create_auth_headers_and_cookies(test_client, db_session, supervisor_user)

    resp = test_client.get("/api/v1/admin/audit")
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "forbidden"


@pytest.mark.api
def test_admin_without_mfa_rejected(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Admin sessions without MFA verification receive 403 mfa_required."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user, mfa_verified=False)

    resp = test_client.get("/api/v1/admin/audit")
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "mfa_required"


@pytest.mark.api
def test_admin_with_mfa_succeeds(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Admin sessions with MFA verification succeed with 200 OK and AuditLogOut list."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user, mfa_verified=True)

    resp = test_client.get("/api/v1/admin/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.api
def test_get_does_not_require_csrf_header(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Safe HTTP GET requests to /admin/audit do not fail CSRF validation."""
    settings = get_settings()
    clock = get_clock()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    raw_token, _session = session_service.create_session(
        admin_user.id,
        admin_user.role,
        mfa_verified=True,
    )
    # Set cookie ONLY, do not send X-CSRF-Token header
    test_client.cookies.set(settings.cookie_name, raw_token)

    resp = test_client.get("/api/v1/admin/audit")
    assert resp.status_code == 200


# ==============================================================================
# 2. Filtering & Pagination Tests (docs/04 §20)
# ==============================================================================


@pytest.mark.api
def test_audit_filter_by_action(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Filtering by action returns only events with the specified action."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user)
    audit_service = AuditService(AuditRepository(db_session))

    audit_service.log_event(
        action="campaign.create",
        entity_type="campaign",
        actor_user_id=admin_user.id,
        details={"slug": "campaign-test"},
    )
    audit_service.log_event(
        action="user.invited",
        entity_type="user",
        actor_user_id=admin_user.id,
        details={"role": "supervisor"},
    )
    db_session.flush()

    resp = test_client.get("/api/v1/admin/audit?action=campaign.create")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    for item in items:
        assert item["action"] == "campaign.create"


@pytest.mark.api
def test_audit_filter_by_actor_user_id(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    second_admin_user: User,
) -> None:
    """Filtering by actor_user_id returns only events performed by that actor."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user)
    audit_service = AuditService(AuditRepository(db_session))

    audit_service.log_event(
        action="site.create",
        entity_type="site",
        actor_user_id=admin_user.id,
    )
    audit_service.log_event(
        action="site.update",
        entity_type="site",
        actor_user_id=second_admin_user.id,
    )
    db_session.flush()

    resp = test_client.get(f"/api/v1/admin/audit?actor_user_id={second_admin_user.id}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    for item in items:
        assert item["actor_user_id"] == str(second_admin_user.id)


@pytest.mark.api
def test_audit_filter_by_date_range(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Filtering by 'from' and 'to' restricts results to the timestamp window."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user)
    audit_repo = AuditRepository(db_session)

    t1 = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)
    t3 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)

    e1 = AuditLog(at=t1, action="site.create", entity_type="site", actor_user_id=admin_user.id)
    e2 = AuditLog(
        at=t2, action="campaign.create", entity_type="campaign", actor_user_id=admin_user.id
    )
    e3 = AuditLog(
        at=t3, action="campaign.publish", entity_type="campaign", actor_user_id=admin_user.id
    )
    audit_repo.create(e1)
    audit_repo.create(e2)
    audit_repo.create(e3)
    db_session.flush()

    # Filter from=t2: should return t2 and t3 (not t1)
    from_iso = t2.isoformat()
    resp_from = test_client.get("/api/v1/admin/audit", params={"from": from_iso})
    assert resp_from.status_code == 200
    items_from = resp_from.json()
    returned_actions_from = [x["action"] for x in items_from]
    assert "campaign.create" in returned_actions_from
    assert "campaign.publish" in returned_actions_from
    assert "site.create" not in returned_actions_from

    # Filter to=t2: should return t1 and t2 (not t3)
    to_iso = t2.isoformat()
    resp_to = test_client.get("/api/v1/admin/audit", params={"to": to_iso})
    assert resp_to.status_code == 200
    items_to = resp_to.json()
    returned_actions_to = [x["action"] for x in items_to]
    assert "site.create" in returned_actions_to
    assert "campaign.create" in returned_actions_to
    assert "campaign.publish" not in returned_actions_to

    # Filter window t2 <= at <= t2
    resp_exact = test_client.get("/api/v1/admin/audit", params={"from": from_iso, "to": to_iso})
    assert resp_exact.status_code == 200
    items_exact = resp_exact.json()
    assert len(items_exact) == 1
    assert items_exact[0]["action"] == "campaign.create"


@pytest.mark.api
def test_audit_pagination_and_ordering(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Audit entries are returned newest first, respecting limit and offset."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user)
    audit_repo = AuditRepository(db_session)

    base_time = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
    for i in range(5):
        entry = AuditLog(
            at=base_time + timedelta(minutes=i),
            action="site.update",
            entity_type="site",
            actor_user_id=admin_user.id,
            details={"index": i},
        )
        audit_repo.create(entry)
    db_session.flush()

    # Query newest entries with limit=2, offset=0
    resp = test_client.get("/api/v1/admin/audit?action=site.update&limit=2&offset=0")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    # Verify ordering: newest first (index 4 then 3)
    assert items[0]["details"]["index"] == 4
    assert items[1]["details"]["index"] == 3

    # Query offset=2
    resp_offset = test_client.get("/api/v1/admin/audit?action=site.update&limit=2&offset=2")
    assert resp_offset.status_code == 200
    items_offset = resp_offset.json()
    assert len(items_offset) == 2
    assert items_offset[0]["details"]["index"] == 2
    assert items_offset[1]["details"]["index"] == 1


@pytest.mark.api
def test_audit_pagination_validation_bounds(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Invalid limit or offset parameters are rejected with 422 Unprocessable Entity."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user)

    # limit > 100 rejected (hard cap per BR-GEN-05)
    resp = test_client.get("/api/v1/admin/audit?limit=101")
    assert resp.status_code == 422

    # limit < 1 rejected
    resp = test_client.get("/api/v1/admin/audit?limit=0")
    assert resp.status_code == 422

    resp = test_client.get("/api/v1/admin/audit?limit=-5")
    assert resp.status_code == 422

    # offset < 0 rejected
    resp = test_client.get("/api/v1/admin/audit?offset=-1")
    assert resp.status_code == 422


# ==============================================================================
# 3. Security, Privacy & Sentinel Leak Checks (BR-AUDIT-01 ★ / T-AUDIT-01-a ★)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
@pytest.mark.br("BR-AUDIT-01")
def test_t_audit_01_a_zero_pii_in_response(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Verify that audit endpoint responses never contain PII or sentinel leak values.

    Per BR-AUDIT-01 ★ & docs/07 §3.3:
    - No donor/user name (ZZFIRST, ZZLAST).
    - No telephone numbers (0550123456).
    - No blood types (A+, O-, etc.).
    - No password hashes, tokens, or recovery secrets.
    """
    create_auth_headers_and_cookies(test_client, db_session, admin_user)
    audit_service = AuditService(AuditRepository(db_session))

    audit_service.log_event(
        action="user.invited",
        entity_type="user",
        actor_user_id=admin_user.id,
        details={"role": "supervisor", "site_name": "Centre Test"},
    )
    audit_service.log_event(
        action="auth.login_failure",
        entity_type="user",
        details={"reason": "invalid_password", "ip_hash": "abcdef123456"},
    )
    db_session.flush()

    resp = test_client.get("/api/v1/admin/audit?limit=50")
    assert resp.status_code == 200

    raw_response = resp.text
    # Check sentinels
    assert SENTINEL_FIRST_NAME not in raw_response
    assert SENTINEL_LAST_NAME not in raw_response
    assert SENTINEL_PHONE not in raw_response

    # Check blood types
    for bt in ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]:
        assert f'"{bt}"' not in raw_response

    # Check secret keys
    for secret_key in ["password_hash", "totp_secret", "raw_token", "recovery_code"]:
        assert f'"{secret_key}"' not in raw_response


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-04")
def test_t_audit_04_a_system_events_null_actor(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """System / CLI operations record actor_user_id as null (BR-AUDIT-04 / T-AUDIT-04-a)."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user)
    audit_service = AuditService(AuditRepository(db_session))

    audit_service.log_event(
        action="retention.purge",
        entity_type="campaign",
        actor_user_id=None,
        details={"purged_count": 12},
    )
    db_session.flush()

    resp = test_client.get("/api/v1/admin/audit?action=retention.purge")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert items[0]["actor_user_id"] is None
    assert items[0]["action"] == "retention.purge"


@pytest.mark.api
@pytest.mark.br("BR-AUDIT-05")
def test_t_audit_05_a_approved_actions_in_audit_feed(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Audit endpoint accurately reflects canonical approved actions per BR-AUDIT-05."""
    create_auth_headers_and_cookies(test_client, db_session, admin_user)
    audit_service = AuditService(AuditRepository(db_session))

    test_actions = [
        "campaign.create",
        "campaign.publish",
        "user.invited",
        "user.deactivated",
        "auth.login_success",
        "emergency.reveal",
        "data.deleted_on_request",
        "post.create",
    ]

    for action in test_actions:
        assert action in APPROVED_AUDIT_ACTIONS
        audit_service.log_event(
            action=action,
            entity_type="test",
            actor_user_id=admin_user.id,
        )
    db_session.flush()

    resp = test_client.get("/api/v1/admin/audit?limit=20")
    assert resp.status_code == 200
    returned_actions = {entry["action"] for entry in resp.json()}
    for action in test_actions:
        assert action in returned_actions
