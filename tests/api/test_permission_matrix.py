"""Structural Test T-STRUCT-02: Role-Permission Matrix Test Harness.

Per docs/07-test-cases.md §4 (Matrix Table), §7 (T-STRUCT-02),
docs/03-auth-permissions.md §11, and docs/06-business-rules.md (BR-AUTH-14):
- Executes matrix rows against existing API endpoints for each of the six canonical roles:
    1. anon: Unauthenticated visitor without session cookie.
    2. sup_a: Active supervisor assigned to site A.
    3. sup_disabled: Deactivated supervisor with existing session in DB.
    4. admin_nomfa: Admin with interim session (mfa_verified = False).
    5. admin: Admin with verified session (mfa_verified = True), without recent reauth (> 10m).
    6. admin_fresh: Admin with verified session and valid recent reauth (< 10m).
- Verifies exact HTTP status codes and error code envelopes.
- Verifies underlying policy matrix definitions across all 37 typed permissions.
- Any cell mismatch MUST fail the test suite and break the build.
"""

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_db,
    get_session_service,
    record_session_reauth,
)
from app.core.clock import FakeClock
from app.core.config import get_settings
from app.main import create_app
from app.models.user import User
from app.policy import (
    ADMIN_PERMISSIONS,
    PUBLIC_PERMISSIONS,
    SUPERVISOR_FIELD_PERMISSIONS,
    Condition,
    Permission,
    get_required_conditions,
    is_permission_granted,
)
from app.policy.matrix import ROLE_PERMISSIONS
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user


@dataclass(frozen=True, slots=True)
class PersonaContext:
    """Canonical test persona definition for matrix execution."""

    name: str
    user: User | None
    raw_token: str | None


@dataclass(frozen=True, slots=True)
class MatrixRow:
    """Expected outcome definition for a single route across all 6 personas."""

    method: str
    path: str
    json_body: dict[str, Any] | None
    expected_status: dict[str, int]
    expected_error_codes: dict[str, str | None]


@pytest.fixture
def matrix_clock() -> FakeClock:
    """Deterministic fake clock starting at reference timestamp."""
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC))


@pytest.fixture
def matrix_personas(
    db_session: Session,
    matrix_clock: FakeClock,
) -> dict[str, PersonaContext]:
    """Provision the 6 canonical personas in database per docs/07 §3.1 and §4."""
    site_a = create_site(db_session, name="Site A")
    settings = get_settings()
    repo = SessionRepository(db_session)
    session_service = SessionService(repo, clock=matrix_clock, settings=settings)

    # 1. anon: unauthenticated visitor
    p_anon = PersonaContext(name="anon", user=None, raw_token=None)

    # 2. sup_a: active supervisor at Site A (without MFA per docs/07 §3.1)
    user_sup_a = create_user(
        db_session,
        email="sup_a_matrix@example.com",
        role="supervisor",
        site_id=site_a.id,
        is_active=True,
        totp_enabled=False,
    )
    tok_sup_a, sess_sup_a = session_service.create_session(user_sup_a.id, "supervisor")
    sess_sup_a.created_at = matrix_clock.now() - timedelta(minutes=15)
    clear_session_reauth(sess_sup_a.id)
    p_sup_a = PersonaContext(name="sup_a", user=user_sup_a, raw_token=tok_sup_a)

    # 3. sup_disabled: deactivated supervisor
    user_sup_dis = create_user(
        db_session,
        email="sup_dis_matrix@example.com",
        role="supervisor",
        site_id=site_a.id,
        is_active=False,
    )
    tok_sup_dis, _sess_sup_dis = session_service.create_session(user_sup_dis.id, "supervisor")
    p_sup_disabled = PersonaContext(
        name="sup_disabled",
        user=user_sup_dis,
        raw_token=tok_sup_dis,
    )

    # 4. admin_nomfa: admin in interim session prior to MFA completion
    user_admin_nomfa = create_user(
        db_session,
        email="admin_nomfa_matrix@example.com",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )
    tok_admin_nomfa, sess_admin_nomfa = session_service.create_session(user_admin_nomfa.id, "admin")
    sess_admin_nomfa.mfa_verified = False
    p_admin_nomfa = PersonaContext(
        name="admin_nomfa",
        user=user_admin_nomfa,
        raw_token=tok_admin_nomfa,
    )

    # 5. admin: admin with verified MFA session, but expired reauth (> 10m, created 15m ago)
    user_admin = create_user(
        db_session,
        email="admin_matrix@example.com",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )
    tok_admin, sess_admin = session_service.create_session(user_admin.id, "admin")
    sess_admin.mfa_verified = True
    sess_admin.created_at = matrix_clock.now() - timedelta(minutes=15)
    clear_session_reauth(sess_admin.id)
    p_admin = PersonaContext(name="admin", user=user_admin, raw_token=tok_admin)

    # 6. admin_fresh: admin with verified session and valid fresh reauth (< 10m)
    user_admin_fresh = create_user(
        db_session,
        email="admin_fresh_matrix@example.com",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )
    tok_admin_fresh, sess_admin_fresh = session_service.create_session(user_admin_fresh.id, "admin")
    sess_admin_fresh.mfa_verified = True
    record_session_reauth(sess_admin_fresh.id, matrix_clock.now())
    p_admin_fresh = PersonaContext(
        name="admin_fresh",
        user=user_admin_fresh,
        raw_token=tok_admin_fresh,
    )

    db_session.flush()

    return {
        "anon": p_anon,
        "sup_a": p_sup_a,
        "sup_disabled": p_sup_disabled,
        "admin_nomfa": p_admin_nomfa,
        "admin": p_admin,
        "admin_fresh": p_admin_fresh,
    }


@pytest.fixture
def matrix_test_client(
    db_session: Session,
    matrix_clock: FakeClock,
) -> TestClient:
    """FastAPI TestClient wired to transactional DB session and test clock."""
    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def override_get_clock() -> FakeClock:
        return matrix_clock

    def override_get_session_service() -> SessionService:
        repo = SessionRepository(db_session)
        return SessionService(repo, clock=matrix_clock, settings=get_settings())

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_clock] = override_get_clock
    app.dependency_overrides[get_session_service] = override_get_session_service

    return TestClient(app)


def execute_persona_request(
    client: TestClient,
    persona: PersonaContext,
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> Any:
    """Execute HTTP request impersonating the specified canonical persona with valid CSRF."""
    client.cookies.clear()
    headers: dict[str, str] = {}
    cookie_name = get_settings().cookie_name

    if persona.raw_token:
        client.cookies.set(cookie_name, persona.raw_token)
        csrf_token = generate_csrf_token(persona.raw_token, secret_key=get_settings().secret_key)
        headers["X-CSRF-Token"] = csrf_token

    if method == "GET":
        return client.get(path, headers=headers)
    if method == "POST":
        return client.post(path, json=json_body or {}, headers=headers)
    if method == "PUT":
        return client.put(path, json=json_body or {}, headers=headers)
    if method == "PATCH":
        return client.patch(path, json=json_body or {}, headers=headers)
    if method == "DELETE":
        return client.delete(path, headers=headers)

    raise ValueError(f"Unsupported HTTP method in matrix test: {method}")


# ==============================================================================
# Matrix Execution Rows for Existing API Routes (docs/07 §4)
# ==============================================================================

EXISTING_ROUTE_MATRIX_ROWS: list[MatrixRow] = [
    # --------------------------------------------------------------------------
    # 1. System Health Probes
    # --------------------------------------------------------------------------
    MatrixRow(
        method="GET",
        path="/health",
        json_body=None,
        expected_status={
            "anon": 200,
            "sup_a": 200,
            "sup_disabled": 200,
            "admin_nomfa": 200,
            "admin": 200,
            "admin_fresh": 200,
        },
        expected_error_codes={
            "anon": None,
            "sup_a": None,
            "sup_disabled": None,
            "admin_nomfa": None,
            "admin": None,
            "admin_fresh": None,
        },
    ),
    MatrixRow(
        method="GET",
        path="/ready",
        json_body=None,
        expected_status={
            "anon": 200,
            "sup_a": 200,
            "sup_disabled": 200,
            "admin_nomfa": 200,
            "admin": 200,
            "admin_fresh": 200,
        },
        expected_error_codes={
            "anon": None,
            "sup_a": None,
            "sup_disabled": None,
            "admin_nomfa": None,
            "admin": None,
            "admin_fresh": None,
        },
    ),
    # --------------------------------------------------------------------------
    # 2. Public CSRF Token Endpoint
    # --------------------------------------------------------------------------
    MatrixRow(
        method="GET",
        path="/api/v1/auth/csrf",
        json_body=None,
        expected_status={
            "anon": 200,
            "sup_a": 200,
            "sup_disabled": 200,
            "admin_nomfa": 200,
            "admin": 200,
            "admin_fresh": 200,
        },
        expected_error_codes={
            "anon": None,
            "sup_a": None,
            "sup_disabled": None,
            "admin_nomfa": None,
            "admin": None,
            "admin_fresh": None,
        },
    ),
    # --------------------------------------------------------------------------
    # 3. Authenticated Logout (docs/07 §4 Row 5)
    # --------------------------------------------------------------------------
    MatrixRow(
        method="POST",
        path="/api/v1/auth/logout",
        json_body=None,
        expected_status={
            "anon": 401,
            "sup_a": 204,
            "sup_disabled": 401,
            "admin_nomfa": 204,
            "admin": 204,
            "admin_fresh": 204,
        },
        expected_error_codes={
            "anon": "not_authenticated",
            "sup_a": None,
            "sup_disabled": "not_authenticated",
            "admin_nomfa": None,
            "admin": None,
            "admin_fresh": None,
        },
    ),
    # --------------------------------------------------------------------------
    # 4. Authenticated Logout-All
    # --------------------------------------------------------------------------
    MatrixRow(
        method="POST",
        path="/api/v1/auth/logout-all",
        json_body=None,
        expected_status={
            "anon": 401,
            "sup_a": 204,
            "sup_disabled": 401,
            "admin_nomfa": 204,
            "admin": 204,
            "admin_fresh": 204,
        },
        expected_error_codes={
            "anon": "not_authenticated",
            "sup_a": None,
            "sup_disabled": "not_authenticated",
            "admin_nomfa": None,
            "admin": None,
            "admin_fresh": None,
        },
    ),
    # --------------------------------------------------------------------------
    # 5. Personal Profile Inspection (docs/07 §4 Row 4)
    # --------------------------------------------------------------------------
    MatrixRow(
        method="GET",
        path="/api/v1/me",
        json_body=None,
        expected_status={
            "anon": 401,
            "sup_a": 200,
            "sup_disabled": 401,
            "admin_nomfa": 403,
            "admin": 200,
            "admin_fresh": 200,
        },
        expected_error_codes={
            "anon": "not_authenticated",
            "sup_a": None,
            "sup_disabled": "not_authenticated",
            "admin_nomfa": "mfa_required",
            "admin": None,
            "admin_fresh": None,
        },
    ),
    # --------------------------------------------------------------------------
    # 6. Personal MFA Setup Initiation
    # --------------------------------------------------------------------------
    MatrixRow(
        method="POST",
        path="/api/v1/me/mfa/setup",
        json_body=None,
        expected_status={
            "anon": 401,
            "sup_a": 200,
            "sup_disabled": 401,
            "admin_nomfa": 403,
            "admin": 200,
            "admin_fresh": 200,
        },
        expected_error_codes={
            "anon": "not_authenticated",
            "sup_a": None,
            "sup_disabled": "not_authenticated",
            "admin_nomfa": "mfa_required",
            "admin": None,
            "admin_fresh": None,
        },
    ),
]


# ==============================================================================
# T-STRUCT-02 Test Cases
# ==============================================================================


@pytest.mark.critical
@pytest.mark.parametrize(
    "matrix_row",
    EXISTING_ROUTE_MATRIX_ROWS,
    ids=lambda r: f"{r.method}_{r.path}",
)
def test_t_struct_02_matrix_route_execution_against_personas(
    matrix_test_client: TestClient,
    matrix_personas: dict[str, PersonaContext],
    matrix_row: MatrixRow,
) -> None:
    """T-STRUCT-02: Execute each route against all 6 canonical personas and verify responses."""
    for role_name, persona in matrix_personas.items():
        expected_status = matrix_row.expected_status[role_name]
        expected_error_code = matrix_row.expected_error_codes.get(role_name)

        resp = execute_persona_request(
            matrix_test_client,
            persona,
            method=matrix_row.method,
            path=matrix_row.path,
            json_body=matrix_row.json_body,
        )

        assert resp.status_code == expected_status, (
            f"Matrix mismatch for role '{role_name}' on {matrix_row.method} {matrix_row.path}: "
            f"expected HTTP {expected_status}, got {resp.status_code}. Response: {resp.text}"
        )

        if expected_error_code is not None:
            data = resp.json()
            assert "error" in data and data["error"]["code"] == expected_error_code, (
                f"Error code mismatch for role '{role_name}' on {matrix_row.method} {matrix_row.path}: "
                f"expected '{expected_error_code}', got '{data.get('error', {}).get('code')}'. "
                f"Full body: {data}"
            )


@pytest.mark.critical
def test_t_struct_02_step_up_sensitive_mfa_disable_matrix(
    matrix_test_client: TestClient,
    matrix_personas: dict[str, PersonaContext],
    matrix_clock: FakeClock,
    db_session: Session,
) -> None:
    """T-STRUCT-02: Verify step-up (R) condition matrix specifically on /me/mfa/disable."""
    path = "/api/v1/me/mfa/disable"

    # 1. anon -> 401
    resp = execute_persona_request(matrix_test_client, matrix_personas["anon"], "POST", path)
    assert resp.status_code == 401

    # 2. sup_disabled -> 401
    resp = execute_persona_request(
        matrix_test_client, matrix_personas["sup_disabled"], "POST", path
    )
    assert resp.status_code == 401

    # 3. admin_nomfa -> 403 mfa_required
    resp = execute_persona_request(matrix_test_client, matrix_personas["admin_nomfa"], "POST", path)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "mfa_required"

    # 4. admin (without fresh reauth) -> 403 reauth_required
    resp = execute_persona_request(matrix_test_client, matrix_personas["admin"], "POST", path)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"

    # 5. admin_fresh (with fresh reauth) -> 403 forbidden (admins cannot disable MFA per BR-AUTH-10)
    resp = execute_persona_request(matrix_test_client, matrix_personas["admin_fresh"], "POST", path)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"

    # 6. sup_mfa without fresh reauth -> 403 reauth_required
    session_service = SessionService(
        SessionRepository(db_session),
        clock=matrix_clock,
        settings=get_settings(),
    )
    user_sup_mfa = create_user(
        db_session,
        email="sup_mfa_stepup@example.com",
        role="supervisor",
        site_id=matrix_personas["sup_a"].user.site_id,
        is_active=True,
        totp_enabled=True,
    )
    tok_sup_mfa, sess_sup_mfa = session_service.create_session(user_sup_mfa.id, "supervisor")
    sess_sup_mfa.mfa_verified = True
    sess_sup_mfa.created_at = matrix_clock.now() - timedelta(minutes=15)
    clear_session_reauth(sess_sup_mfa.id)
    p_sup_mfa = PersonaContext(name="sup_mfa", user=user_sup_mfa, raw_token=tok_sup_mfa)

    resp = execute_persona_request(matrix_test_client, p_sup_mfa, "POST", path)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "reauth_required"

    # 7. sup_mfa with fresh reauth -> succeeds (204)
    record_session_reauth(sess_sup_mfa.id, matrix_clock.now())
    resp = execute_persona_request(matrix_test_client, p_sup_mfa, "POST", path)
    assert resp.status_code == 204


@pytest.mark.critical
def test_t_struct_02_policy_matrix_exhaustive_permission_contract() -> None:
    """T-STRUCT-02: Unit-level verification that all 37 permissions in matrix match specifications."""
    all_perms = set(Permission)
    assert len(all_perms) == 37, f"Expected 37 total permissions, found {len(all_perms)}"

    # Check anon role
    anon_perms = ROLE_PERMISSIONS["anon"]
    assert set(anon_perms.keys()) == PUBLIC_PERMISSIONS, (
        "Anonymous role must possess only the 9 public permissions."
    )
    for perm in all_perms:
        if perm in PUBLIC_PERMISSIONS:
            assert is_permission_granted("anon", perm), (
                f"Public perm {perm} should be granted to anon"
            )
        else:
            assert not is_permission_granted("anon", perm), f"{perm} must NOT be granted to anon"

    # Check supervisor role
    sup_perms = ROLE_PERMISSIONS["supervisor"]
    # Supervisor must NOT have administrative governance permissions
    for admin_perm in ADMIN_PERMISSIONS:
        assert admin_perm not in sup_perms, (
            f"Administrative permission {admin_perm} must not exist in supervisor role matrix."
        )

    # Supervisor field permissions must demand ACTIVE_ACCOUNT, SITE_ISOLATION, and LIVE_CAMPAIGN
    for field_perm in SUPERVISOR_FIELD_PERMISSIONS:
        conds = get_required_conditions("supervisor", field_perm)
        assert conds is not None, f"Supervisor must possess {field_perm}"
        assert Condition.ACTIVE_ACCOUNT in conds
        assert Condition.SITE_ISOLATION in conds
        assert Condition.LIVE_CAMPAIGN in conds

    # Check admin role
    admin_perms = ROLE_PERMISSIONS["admin"]
    assert len(admin_perms) > 0
    for admin_perm in ADMIN_PERMISSIONS:
        conds = get_required_conditions("admin", admin_perm)
        assert conds is not None, f"Admin must possess {admin_perm}"
        assert Condition.ACTIVE_ACCOUNT in conds
        assert Condition.MFA_VERIFIED in conds

    # Verify sensitive admin operations require Condition.RECENT_REAUTH (R)
    sensitive_r_permissions = {
        Permission.CAMPAIGN_END,
        Permission.PLEDGE_DELETE,
        Permission.USER_INVITE,
        Permission.USER_UPDATE,
        Permission.USER_DEACTIVATE,
        Permission.USER_UNLOCK,
        Permission.USER_RESET_MFA,
        Permission.DATA_DELETE_ON_REQUEST,
        Permission.DONOR_RELOCATE,
    }
    for perm in sensitive_r_permissions:
        conds = get_required_conditions("admin", perm)
        assert conds is not None
        assert Condition.RECENT_REAUTH in conds, (
            f"Admin permission {perm} must demand Condition.RECENT_REAUTH (R)."
        )
