"""Tests for cookie handling and CSRF protection (P3.4).

Traces to:
- BR-AUTH-18 & T-AUTH-18-a (★): State-changing write requests with session cookie require X-CSRF-Token -> 403 csrf_failed.
- BR-AUTH-18 & T-AUTH-18-b (★): Invalid X-CSRF-Token -> 403 csrf_failed.
- BR-AUTH-18 & T-AUTH-18-c: Authenticated GET requests bypass X-CSRF-Token.
- BR-AUTH-18 & T-AUTH-18-d: Anonymous public requests (no session cookie) bypass X-CSRF-Token.
- BR-AUTH-19 & T-AUTH-19-a: Production cookie attributes (__Host-, HttpOnly, Secure, SameSite=Lax, no Domain).
- BR-AUTH-19 & T-AUTH-19-b: Development/Test cookie attributes and clear_session_cookie verification.
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_cookie,
    generate_csrf_token,
    get_current_user,
    set_session_cookie,
    verify_csrf,
    verify_csrf_token,
)
from app.api.error_handlers import register_error_handlers
from app.core.config import Settings
from app.core.database import get_db
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user


@pytest.fixture
def auth_test_app(db_session: Session) -> FastAPI:
    """Create test FastAPI application with registered dependencies and routes."""
    app = FastAPI(dependencies=[Depends(verify_csrf)])
    register_error_handlers(app)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Test routes
    @app.get("/api/v1/test/read")
    def read_endpoint(user: Annotated[User, Depends(get_current_user)]):
        return {"status": "read_ok", "user": user.email}

    @app.post("/api/v1/test/write")
    def write_endpoint(user: Annotated[User, Depends(get_current_user)]):
        return {"status": "write_ok", "user": user.email}

    @app.post("/api/v1/public/pledges")
    def public_pledge_endpoint():
        return {"status": "pledge_ok"}

    return app


@pytest.fixture
def auth_client(auth_test_app: FastAPI) -> TestClient:
    """Provide TestClient for auth_test_app."""
    return TestClient(auth_test_app)


@pytest.fixture
def sample_user_and_session(db_session: Session) -> tuple[User, str]:
    """Create an active supervisor and an active session, returning (user, raw_token)."""
    site = create_site(db_session, name="Centre Test CSRF")
    user = create_user(
        db_session,
        email="supervisor.csrf@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )
    repo = SessionRepository(db_session)
    service = SessionService(repo)
    raw_token, _ = service.create_session(user.id, role=user.role, mfa_verified=True)
    return user, raw_token


# ==============================================================================
# BR-AUTH-18: CSRF Protection (T-AUTH-18-a/b/c/d/e ★)
# ==============================================================================


@pytest.mark.api
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-18")
def test_br_auth_18_a_authenticated_write_without_csrf_token_fails(
    auth_client: TestClient,
    sample_user_and_session: tuple[User, str],
) -> None:
    """T-AUTH-18-a: Authenticated write request with cookie but missing X-CSRF-Token must return 403 csrf_failed."""
    _, raw_token = sample_user_and_session
    auth_client.cookies.set("session", raw_token)

    response = auth_client.post("/api/v1/test/write", json={"some": "payload"})
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["code"] == "csrf_failed"


@pytest.mark.api
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-18")
def test_br_auth_18_b_authenticated_write_with_invalid_csrf_token_fails(
    auth_client: TestClient,
    sample_user_and_session: tuple[User, str],
) -> None:
    """T-AUTH-18-b: Authenticated write request with wrong/tampered X-CSRF-Token must return 403 csrf_failed."""
    _, raw_token = sample_user_and_session
    auth_client.cookies.set("session", raw_token)

    response = auth_client.post(
        "/api/v1/test/write",
        json={"some": "payload"},
        headers={"X-CSRF-Token": "tampered_csrf_token_value_123"},
    )
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["code"] == "csrf_failed"


@pytest.mark.api
@pytest.mark.security
@pytest.mark.br("BR-AUTH-18")
def test_br_auth_18_c_authenticated_get_bypasses_csrf(
    auth_client: TestClient,
    sample_user_and_session: tuple[User, str],
) -> None:
    """T-AUTH-18-c: Authenticated safe read request (GET) without X-CSRF-Token succeeds."""
    user, raw_token = sample_user_and_session
    auth_client.cookies.set("session", raw_token)

    response = auth_client.get("/api/v1/test/read")
    assert response.status_code == 200
    assert response.json() == {"status": "read_ok", "user": user.email}


@pytest.mark.api
@pytest.mark.security
@pytest.mark.br("BR-AUTH-18")
def test_br_auth_18_d_anonymous_public_request_bypasses_csrf(
    auth_client: TestClient,
) -> None:
    """T-AUTH-18-d: Public write requests without session cookie do NOT require X-CSRF-Token."""
    # Ensure no session cookie is attached
    auth_client.cookies.clear()

    response = auth_client.post("/api/v1/public/pledges", json={"first_name": "Karim"})
    assert response.status_code == 200
    assert response.json() == {"status": "pledge_ok"}


@pytest.mark.api
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-18")
def test_br_auth_18_e_authenticated_write_with_valid_csrf_token_succeeds(
    auth_client: TestClient,
    sample_user_and_session: tuple[User, str],
) -> None:
    """T-AUTH-18-e: Authenticated write request with matching X-CSRF-Token succeeds."""
    user, raw_token = sample_user_and_session
    auth_client.cookies.set("session", raw_token)

    valid_csrf = generate_csrf_token(raw_token)

    response = auth_client.post(
        "/api/v1/test/write",
        json={"some": "payload"},
        headers={"X-CSRF-Token": valid_csrf},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "write_ok", "user": user.email}


# ==============================================================================
# BR-AUTH-19: Cookie Attributes in Production and Development (T-AUTH-19-a)
# ==============================================================================


@pytest.mark.api
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-19")
def test_br_auth_19_a_production_cookie_attributes() -> None:
    """T-AUTH-19-a: In prod environment, Set-Cookie must have __Host-, HttpOnly, Secure, SameSite=Lax, and no Domain."""
    prod_settings = Settings(
        app_env="prod",
        secret_key="a" * 64,  # pragma: allowlist secret
        totp_encryption_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",  # pragma: allowlist secret
        ip_hash_salt="b" * 32,  # pragma: allowlist secret
        docs_enabled=False,
        cookie_secure=True,
        public_base_url="https://blooddonation.dz",
        database_url="postgresql+psycopg://migrator:strongpass@remote.db.internal:5432/bloodday_prod",  # pragma: allowlist secret
    )

    response = Response()
    raw_token = "secure_raw_session_token_xyz123"
    set_session_cookie(response, raw_token, settings=prod_settings)

    # Inspect Set-Cookie header string
    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None

    # Must start with __Host- prefix per BR-AUTH-19 / docs/03 §8.2
    assert cookie_header.startswith("__Host-session=")

    # Must enforce HttpOnly, Secure, SameSite=lax, and Path=/
    cookie_lower = cookie_header.lower()
    assert "httponly" in cookie_lower
    assert "secure" in cookie_lower
    assert "samesite=lax" in cookie_lower
    assert "path=/" in cookie_lower

    # Domain MUST be absent for __Host- cookies per RFC 6265bis
    assert "domain=" not in cookie_lower


@pytest.mark.api
@pytest.mark.br("BR-AUTH-19")
def test_br_auth_19_b_dev_cookie_attributes_and_clear() -> None:
    """T-AUTH-19-b: In dev/test, cookie uses standard name; clear_session_cookie deletes it."""
    dev_settings = Settings(app_env="dev", cookie_name="session", cookie_secure=False)

    response = Response()
    raw_token = "dev_session_token_123"
    set_session_cookie(response, raw_token, settings=dev_settings)

    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None
    assert cookie_header.startswith("session=")
    assert "httponly" in cookie_header.lower()
    assert "samesite=lax" in cookie_header.lower()

    # Clear cookie
    clear_response = Response()
    clear_session_cookie(clear_response, settings=dev_settings)
    clear_header = clear_response.headers.get("set-cookie")
    assert clear_header is not None
    assert (
        'session=""' in clear_header
        or "session=;" in clear_header
        or "max-age=0" in clear_header.lower()
    )


# ==============================================================================
# CSRF Helper Unit Verification
# ==============================================================================


@pytest.mark.unit
def test_csrf_token_generation_and_verification() -> None:
    """CSRF tokens must be deterministic for the same session token and reject mismatches."""
    raw_token_1 = "session_token_sample_one"
    raw_token_2 = "session_token_sample_two"

    csrf_1 = generate_csrf_token(raw_token_1)
    csrf_2 = generate_csrf_token(raw_token_2)

    assert csrf_1 != csrf_2
    assert len(csrf_1) == 64  # SHA-256 hex length

    # Verification passes with matching token
    assert verify_csrf_token(raw_token_1, csrf_1) is True
    assert verify_csrf_token(raw_token_2, csrf_2) is True

    # Verification fails on mismatched session token
    assert verify_csrf_token(raw_token_1, csrf_2) is False
    assert verify_csrf_token(raw_token_2, csrf_1) is False

    # Empty or None rejected safely
    assert verify_csrf_token(raw_token_1, "") is False
    assert verify_csrf_token(raw_token_1, None) is False
