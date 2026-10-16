"""Tests for require(...) authorization dependency and step-up (R) enforcement (P3.8).

Traces to:
- docs/03-auth-permissions.md §11-§13 & docs/06-business-rules.md (BR-AUTH-14).
- T-AUTH-14-a (★): Admin with recent reauth (< 10 min) succeeds on R-requiring operations.
- T-AUTH-14-b (★): Admin with expired reauth (> 10 min / 600s) receives 403 reauth_required.
- T-AUTH-14-c (★): Unauthenticated caller receives 401; supervisor receives 403 forbidden.
- T-AUTH-08-a (★): Admin without MFA verification receives 403 mfa_required.
- Public route access: Anonymous visitor allowed without session cookie; returns user=None.
"""

from datetime import datetime, timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    get_session_service,
    record_session_reauth,
    require,
)
from app.api.error_handlers import register_error_handlers
from app.core.clock import FakeClock, get_clock
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.policy import Permission
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user


@pytest.fixture
def fake_clock() -> FakeClock:
    """Deterministic fake clock starting at reference timestamp."""
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0))


@pytest.fixture
def permission_test_app(db_session: Session, fake_clock: FakeClock) -> FastAPI:
    """Create test FastAPI application with routes exercising require() dependency."""
    app = FastAPI()
    register_error_handlers(app)

    # Dependency overrides
    def override_get_db():
        yield db_session

    def override_get_clock():
        return fake_clock

    def override_get_session_service():
        repo = SessionRepository(db_session)
        return SessionService(repo, clock=fake_clock, settings=get_settings())

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_clock] = override_get_clock
    app.dependency_overrides[get_session_service] = override_get_session_service

    # Route 1: Public endpoint (campaign:read)
    @app.get("/api/v1/test/public")
    def public_endpoint(
        caller: Annotated[User | None, Depends(require(Permission.CAMPAIGN_READ))],
    ):
        return {
            "status": "public_ok",
            "caller": caller.email if caller else "anonymous",
        }

    # Route 2: Protected supervisor field endpoint (donor:create)
    @app.post("/api/v1/test/donor-create")
    def supervisor_endpoint(
        user: Annotated[User, Depends(require(Permission.DONOR_CREATE))],
    ):
        return {"status": "donor_create_ok", "user": user.email}

    # Route 3: Protected admin governance endpoint (campaign:manage)
    @app.post("/api/v1/test/campaign-manage")
    def admin_endpoint(
        user: Annotated[User, Depends(require(Permission.CAMPAIGN_MANAGE))],
    ):
        return {"status": "campaign_manage_ok", "user": user.email}

    # Route 4: Step-up sensitive admin endpoint (campaign:end) - requires Condition.RECENT_REAUTH
    @app.post("/api/v1/test/campaign-end")
    def sensitive_end_endpoint(
        user: Annotated[User, Depends(require(Permission.CAMPAIGN_END))],
    ):
        return {"status": "campaign_end_ok", "user": user.email}

    # Route 5: Route with explicit require_reauth=True override
    @app.post("/api/v1/test/revert-override")
    def revert_endpoint(
        user: Annotated[
            User,
            Depends(require(Permission.CAMPAIGN_MANAGE, require_reauth=True)),
        ],
    ):
        return {"status": "revert_ok", "user": user.email}

    return app


@pytest.fixture
def api_client(permission_test_app: FastAPI) -> TestClient:
    """Test client for permission test application."""
    return TestClient(permission_test_app)


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create active supervisor assigned to Site A."""
    site = create_site(db_session, name="Centre Test Supervisor")
    return create_user(
        db_session,
        email="supervisor.perm@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create active admin user."""
    return create_user(
        db_session,
        email="admin.perm@bloodday.local",
        role="admin",
        site_id=None,
        is_active=True,
    )


def create_session_for_user(
    db_session: Session,
    user: User,
    fake_clock: FakeClock,
    settings: Settings,
    *,
    mfa_verified: bool = False,
) -> str:
    """Helper to create session in test DB and return raw token."""
    repo = SessionRepository(db_session)
    svc = SessionService(repo, clock=fake_clock, settings=settings)
    raw_token, _ = svc.create_session(
        user.id,
        user.role,
        mfa_verified=mfa_verified,
    )
    return raw_token


# ==============================================================================
# Public Access Tests
# ==============================================================================


@pytest.mark.api
def test_public_route_anonymous_access(api_client: TestClient) -> None:
    """Anonymous visitors without session cookie access public routes and receive caller=anonymous."""
    resp = api_client.get("/api/v1/test/public")
    assert resp.status_code == 200
    assert resp.json() == {"status": "public_ok", "caller": "anonymous"}


@pytest.mark.api
def test_public_route_authenticated_access(
    api_client: TestClient,
    db_session: Session,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """Authenticated user calling public route is identified by user entity."""
    token = create_session_for_user(db_session, supervisor_user, fake_clock, get_settings())
    api_client.cookies.set(get_settings().cookie_name, token)

    resp = api_client.get("/api/v1/test/public")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "public_ok",
        "caller": supervisor_user.email,
    }


# ==============================================================================
# Protected Field Operations & Role Enforcement Tests
# ==============================================================================


@pytest.mark.api
def test_protected_route_unauthenticated_rejected(
    api_client: TestClient,
) -> None:
    """Protected routes reject unauthenticated caller with 401 not_authenticated."""
    resp = api_client.post("/api/v1/test/donor-create")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.api
def test_supervisor_can_access_supervisor_action(
    api_client: TestClient,
    db_session: Session,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """Active supervisor accesses donor-create successfully with 200."""
    token = create_session_for_user(db_session, supervisor_user, fake_clock, get_settings())
    api_client.cookies.set(get_settings().cookie_name, token)

    resp = api_client.post("/api/v1/test/donor-create")
    assert resp.status_code == 200
    assert resp.json()["status"] == "donor_create_ok"


@pytest.mark.api
def test_supervisor_forbidden_from_admin_action(
    api_client: TestClient,
    db_session: Session,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """Supervisor attempting administrative action receives 403 forbidden."""
    token = create_session_for_user(db_session, supervisor_user, fake_clock, get_settings())
    api_client.cookies.set(get_settings().cookie_name, token)

    resp = api_client.post("/api/v1/test/campaign-manage")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# ==============================================================================
# MFA Requirement Tests (T-AUTH-08-a ★)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
def test_admin_without_mfa_receives_mfa_required(
    api_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-08-a (★): Admin with interim session (mfa_verified=False) receives 403 mfa_required."""
    token = create_session_for_user(
        db_session,
        admin_user,
        fake_clock,
        get_settings(),
        mfa_verified=False,
    )
    api_client.cookies.set(get_settings().cookie_name, token)

    resp = api_client.post("/api/v1/test/campaign-manage")
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "mfa_required"


@pytest.mark.api
def test_admin_with_mfa_verified_succeeds(
    api_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_clock: FakeClock,
) -> None:
    """Admin with verified MFA session (mfa_verified=True) accesses campaign-manage successfully."""
    token = create_session_for_user(
        db_session,
        admin_user,
        fake_clock,
        get_settings(),
        mfa_verified=True,
    )
    api_client.cookies.set(get_settings().cookie_name, token)

    resp = api_client.post("/api/v1/test/campaign-manage")
    assert resp.status_code == 200
    assert resp.json()["status"] == "campaign_manage_ok"


# ==============================================================================
# Step-Up Re-Authentication Tests (T-AUTH-14 ★)
# ==============================================================================


@pytest.mark.api
@pytest.mark.critical
def test_t_auth_14_a_admin_recent_reauth_succeeds(
    api_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-14-a (★): Admin with fresh re-auth (< 10 min) succeeds on sensitive R operations."""
    # Session created at T=10:00:00, fake_clock at 10:05:00 (5 minutes elapsed <= 10 min window)
    token = create_session_for_user(
        db_session,
        admin_user,
        fake_clock,
        get_settings(),
        mfa_verified=True,
    )
    api_client.cookies.set(get_settings().cookie_name, token)

    fake_clock.advance(timedelta(minutes=5))

    resp = api_client.post("/api/v1/test/campaign-end")
    assert resp.status_code == 200
    assert resp.json()["status"] == "campaign_end_ok"


@pytest.mark.api
@pytest.mark.critical
def test_t_auth_14_b_admin_expired_reauth_rejected(
    api_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-14-b (★): Admin with stale re-auth (> 10 min / 600s) receives 403 reauth_required."""
    # Session created at T=10:00:00, fake_clock advances by 10 minutes and 1 second (601s > 600s)
    token = create_session_for_user(
        db_session,
        admin_user,
        fake_clock,
        get_settings(),
        mfa_verified=True,
    )
    api_client.cookies.set(get_settings().cookie_name, token)

    fake_clock.advance(timedelta(minutes=10, seconds=1))

    resp = api_client.post("/api/v1/test/campaign-end")
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "reauth_required"


@pytest.mark.api
@pytest.mark.critical
def test_t_auth_14_c_step_up_unauthenticated_and_forbidden(
    api_client: TestClient,
    db_session: Session,
    supervisor_user: User,
    fake_clock: FakeClock,
) -> None:
    """T-AUTH-14-c (★): Unauthenticated caller receives 401; supervisor receives 403 forbidden."""
    # Unauthenticated caller -> 401
    resp_unauth = api_client.post("/api/v1/test/campaign-end")
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error"]["code"] == "not_authenticated"

    # Supervisor caller -> 403 forbidden
    token = create_session_for_user(db_session, supervisor_user, fake_clock, get_settings())
    api_client.cookies.set(get_settings().cookie_name, token)

    resp_sup = api_client.post("/api/v1/test/campaign-end")
    assert resp_sup.status_code == 403
    assert resp_sup.json()["error"]["code"] == "forbidden"


@pytest.mark.api
def test_step_up_renewal_after_confirmation(
    api_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_clock: FakeClock,
) -> None:
    """Registering fresh confirmation resets step-up window and permits action."""
    repo = SessionRepository(db_session)
    svc = SessionService(repo, clock=fake_clock, settings=get_settings())
    raw_token, session_entity = svc.create_session(
        admin_user.id,
        admin_user.role,
        mfa_verified=True,
    )
    api_client.cookies.set(get_settings().cookie_name, raw_token)

    # 1. Advance beyond 10 minutes -> 403 reauth_required
    fake_clock.advance(timedelta(minutes=15))
    resp1 = api_client.post("/api/v1/test/campaign-end")
    assert resp1.status_code == 403
    assert resp1.json()["error"]["code"] == "reauth_required"

    # 2. Re-authenticate / confirm password -> update reauth timestamp
    record_session_reauth(session_entity.id, fake_clock.now())

    # 3. Request succeeds immediately
    resp2 = api_client.post("/api/v1/test/campaign-end")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "campaign_end_ok"

    clear_session_reauth(session_entity.id)


@pytest.mark.api
def test_route_level_reauth_override(
    api_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_clock: FakeClock,
) -> None:
    """Endpoint with require_reauth=True enforces step-up even on permissions without R by default."""
    token = create_session_for_user(
        db_session,
        admin_user,
        fake_clock,
        get_settings(),
        mfa_verified=True,
    )
    api_client.cookies.set(get_settings().cookie_name, token)

    # Advance 11 minutes
    fake_clock.advance(timedelta(minutes=11))

    resp = api_client.post("/api/v1/test/revert-override")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"
