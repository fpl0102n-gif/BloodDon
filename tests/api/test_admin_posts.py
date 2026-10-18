"""API integration tests for Admin Posts endpoints (P9.1).

Per:
- docs/04-api.md §19 (Admin Posts API: GET, POST, PATCH, DELETE)
- docs/06-business-rules.md (BR-POST-01..05)
- docs/07-test-cases.md §5.15 (T-POST-01-a, T-POST-02-a, T-POST-04-a)
- docs/03-auth-permissions.md §11.3 (Permission.POST_MANAGE, step-up R)
"""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_settings,
    record_session_reauth,
)
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from tests.factories.campaign import create_site
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create an active administrator user with TOTP enabled."""
    return create_user(
        db_session,
        email="admin.posts@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def supervisor_user(db_session: Session) -> User:
    """Create an active supervisor user."""
    site = create_site(db_session, name="موقع إشراف المنشورات")
    return create_user(
        db_session,
        email="supervisor.posts@bloodday.local",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )


@pytest.fixture
def client(test_client: TestClient) -> TestClient:
    return test_client


def create_auth_headers_and_cookies(
    test_client: TestClient,
    db_session: Session,
    user: User,
    *,
    mfa_verified: bool = True,
    fresh_reauth: bool = False,
) -> dict[str, str]:
    """Helper to attach session cookie and CSRF token for a given user."""
    settings = get_settings()
    clock = get_clock()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    raw_token, session = session_service.create_session(
        user.id,
        user.role,
        mfa_verified=mfa_verified,
    )

    if fresh_reauth:
        record_session_reauth(session.id, clock.now())
    else:
        clear_session_reauth(session.id)
        session.created_at = clock.now() - timedelta(minutes=15)
        db_session.flush()

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    test_client.cookies.set(settings.cookie_name, raw_token)

    return {"X-CSRF-Token": csrf_token}


# ==============================================================================
# 1. Authorization & Role Access
# ==============================================================================


@pytest.mark.br("BR-POST-01")
def test_admin_posts_endpoints_require_admin_role_and_mfa(
    client: TestClient,
    db_session: Session,
    admin_user: User,
    supervisor_user: User,
) -> None:
    """Anonymous visitors, supervisors, and admins without MFA are denied access."""
    path = "/api/v1/admin/posts"

    # 1. Anonymous visitor -> 401
    client.cookies.clear()
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"

    # 2. Supervisor -> 403 Forbidden
    sup_headers = create_auth_headers_and_cookies(client, db_session, supervisor_user)
    resp = client.get(path, headers=sup_headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"

    # 3. Admin without MFA -> 403 MFA Required
    nomfa_headers = create_auth_headers_and_cookies(
        client, db_session, admin_user, mfa_verified=False
    )
    resp = client.get(path, headers=nomfa_headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "mfa_required"


# ==============================================================================
# 2. CRUD Operations & Business Rules
# ==============================================================================


@pytest.mark.br("BR-POST-01")
def test_t_post_01_a_create_post_and_slug_validations(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-POST-01-a: Create post succeeds, duplicate slug returns 409, invalid slug returns 422."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    unique_slug = f"blood-donation-guide-{uuid.uuid4().hex[:6]}"
    payload = {
        "title": "دليل التبرع بالدم الشامل",
        "slug": unique_slug,
        "excerpt": "كل ما تحتاج معرفته عن التبرع بالدم",
        "content": "# دليل التبرع\n\nمعلومات هامة للمتبرعين الجدد.",  # noqa: RUF001
        "is_published": False,
    }

    # 1. Valid creation -> 201
    resp = client.post("/api/v1/admin/posts", json=payload, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == unique_slug
    assert data["title"] == "دليل التبرع بالدم الشامل"
    assert data["is_published"] is False
    assert data["published_at"] is None

    # 2. Duplicate slug -> 409 slug_taken
    resp_dup = client.post("/api/v1/admin/posts", json=payload, headers=headers)
    assert resp_dup.status_code == 409
    assert resp_dup.json()["error"]["code"] == "slug_taken"

    # 3. Invalid slug format -> 422 validation_error
    bad_payload = dict(payload, slug="Invalid_Slug_Capital")
    resp_bad = client.post("/api/v1/admin/posts", json=bad_payload, headers=headers)
    assert resp_bad.status_code == 422
    assert resp_bad.json()["error"]["code"] == "validation_error"


@pytest.mark.br("BR-POST-02")
def test_t_post_02_a_first_publish_populates_published_at_and_persists_on_republish(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-POST-02-a: published_at is set on first publish only, preserved on unpublish then republish."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    slug = f"post-lifecycle-{uuid.uuid4().hex[:6]}"
    create_resp = client.post(
        "/api/v1/admin/posts",
        json={
            "title": "دورة حياة المنشور",
            "slug": slug,
            "content": "محتوى المقال",
            "is_published": False,
        },
        headers=headers,
    )
    assert create_resp.status_code == 201
    post_id = create_resp.json()["id"]
    assert create_resp.json()["published_at"] is None

    # 1. Publish for the first time -> published_at populated
    pub_resp = client.patch(
        f"/api/v1/admin/posts/{post_id}",
        json={"is_published": True},
        headers=headers,
    )
    assert pub_resp.status_code == 200
    first_published_at = pub_resp.json()["published_at"]
    assert first_published_at is not None

    # 2. Unpublish -> is_published is False, published_at preserved
    unpub_resp = client.patch(
        f"/api/v1/admin/posts/{post_id}",
        json={"is_published": False},
        headers=headers,
    )
    assert unpub_resp.status_code == 200
    assert unpub_resp.json()["is_published"] is False
    assert datetime.fromisoformat(unpub_resp.json()["published_at"]) == datetime.fromisoformat(
        first_published_at
    )

    # 3. Republish -> is_published is True, published_at is STILL first_published_at (NOT reset)
    repub_resp = client.patch(
        f"/api/v1/admin/posts/{post_id}",
        json={"is_published": True},
        headers=headers,
    )
    assert repub_resp.status_code == 200
    assert repub_resp.json()["is_published"] is True
    assert datetime.fromisoformat(repub_resp.json()["published_at"]) == datetime.fromisoformat(
        first_published_at
    )


@pytest.mark.br("BR-POST-04")
def test_t_post_04_a_content_size_boundary_100kb(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-POST-04-a: Content size <= 100 KB succeeds, > 100 KB returns 422 (BR-POST-04)."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    # 1. 100 KB exactly (102,400 bytes) -> 201
    content_100kb = "x" * 102400
    slug_ok = f"ok-size-{uuid.uuid4().hex[:6]}"
    resp_ok = client.post(
        "/api/v1/admin/posts",
        json={
            "title": "مقال بحجم مسموح",
            "slug": slug_ok,
            "content": content_100kb,
        },
        headers=headers,
    )
    assert resp_ok.status_code == 201

    # 2. 100 KB + 1 byte (102,401 bytes) -> 422
    content_excess = "x" * 102401
    slug_fail = f"fail-size-{uuid.uuid4().hex[:6]}"
    resp_fail = client.post(
        "/api/v1/admin/posts",
        json={
            "title": "مقال بحجم زائد",
            "slug": slug_fail,
            "content": content_excess,
        },
        headers=headers,
    )
    assert resp_fail.status_code == 422
    assert resp_fail.json()["error"]["code"] == "validation_error"


@pytest.mark.br("BR-POST-01")
def test_admin_list_and_get_post_by_id(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Admin can list all posts and fetch a single post by ID."""
    headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    slug = f"list-test-{uuid.uuid4().hex[:6]}"
    create_resp = client.post(
        "/api/v1/admin/posts",
        json={"title": "مقال للفهرسة", "slug": slug, "is_published": True},
        headers=headers,
    )
    assert create_resp.status_code == 201
    post_id = create_resp.json()["id"]

    # List
    list_resp = client.get("/api/v1/admin/posts", headers=headers)
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert any(p["id"] == post_id for p in items)

    # Get by ID
    get_resp = client.get(f"/api/v1/admin/posts/{post_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == post_id
    assert get_resp.json()["slug"] == slug

    # Non-existent ID -> 404
    missing_resp = client.get(f"/api/v1/admin/posts/{uuid.uuid4()}", headers=headers)
    assert missing_resp.status_code == 404
    assert missing_resp.json()["error"]["code"] == "not_found"


@pytest.mark.br("BR-POST-01")
def test_admin_delete_post_requires_fresh_reauth(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """DELETE /admin/posts/{id} requires step-up reauthentication (R)."""
    # 1. Create post with admin session
    admin_headers = create_auth_headers_and_cookies(client, db_session, admin_user)
    slug = f"delete-target-{uuid.uuid4().hex[:6]}"
    create_resp = client.post(
        "/api/v1/admin/posts",
        json={"title": "مقال للحذف", "slug": slug},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201
    post_id = create_resp.json()["id"]

    # 2. Attempt delete WITHOUT fresh reauth -> 403 reauth_required
    delete_resp_no_reauth = client.delete(f"/api/v1/admin/posts/{post_id}", headers=admin_headers)
    assert delete_resp_no_reauth.status_code == 403
    assert delete_resp_no_reauth.json()["error"]["code"] == "reauth_required"

    # 3. Attempt delete WITH fresh reauth -> 204 No Content
    reauth_headers = create_auth_headers_and_cookies(
        client, db_session, admin_user, fresh_reauth=True
    )
    delete_resp = client.delete(f"/api/v1/admin/posts/{post_id}", headers=reauth_headers)
    assert delete_resp.status_code == 204

    # 4. Confirm deleted -> 404
    get_resp = client.get(f"/api/v1/admin/posts/{post_id}", headers=admin_headers)
    assert get_resp.status_code == 404
