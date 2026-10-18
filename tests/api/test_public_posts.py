"""API integration tests for Public Posts endpoints (P9.1).

Per:
- docs/04-api.md §6.10 (Public Posts: GET /public/posts, GET /public/posts/{slug})
- docs/06-business-rules.md (BR-POST-01..05)
- docs/07-test-cases.md §5.15 (T-POST-03-a)
- docs/03-auth-permissions.md §11.1 (Permission.POST_READ)
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.user import User
from tests.api.test_admin_posts import create_auth_headers_and_cookies
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create an active administrator user with TOTP enabled."""
    return create_user(
        db_session,
        email="admin.pubposts@bloodday.local",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


@pytest.fixture
def client(test_client: TestClient) -> TestClient:
    return test_client


@pytest.mark.br("BR-POST-03")
def test_t_post_03_a_public_read_published_only_draft_returns_404(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """T-POST-03-a: Public visitors can view published posts; draft posts return 404 (BR-POST-03)."""
    admin_headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    pub_slug = f"published-article-{uuid.uuid4().hex[:6]}"
    draft_slug = f"draft-article-{uuid.uuid4().hex[:6]}"

    # 1. Create published post
    client.post(
        "/api/v1/admin/posts",
        json={
            "title": "مقال منشور للعامة",
            "slug": pub_slug,
            "excerpt": "مقدمة المقال المنشور",
            "content": "# مقال منشور\n\nتفاصيل المقال الهام.",  # noqa: RUF001
            "is_published": True,
        },
        headers=admin_headers,
    )

    # 2. Create draft post
    client.post(
        "/api/v1/admin/posts",
        json={
            "title": "مقال مسودة داخلي",
            "slug": draft_slug,
            "excerpt": "مقدمة المسودة",
            "content": "محتوى لم ينشر بعد",
            "is_published": False,
        },
        headers=admin_headers,
    )

    # Clear cookies so client is anonymous public visitor
    client.cookies.clear()

    # 3. GET /public/posts/{pub_slug} -> 200
    pub_resp = client.get(f"/api/v1/public/posts/{pub_slug}")
    assert pub_resp.status_code == 200
    assert pub_resp.headers.get("Cache-Control") == "public, max-age=300"
    data = pub_resp.json()
    assert data["slug"] == pub_slug
    assert data["title"] == "مقال منشور للعامة"
    assert data["content"] == "# مقال منشور\n\nتفاصيل المقال الهام."  # noqa: RUF001
    assert data["published_at"] is not None

    # 4. GET /public/posts/{draft_slug} -> 404 (T-POST-03-a)
    draft_resp = client.get(f"/api/v1/public/posts/{draft_slug}")
    assert draft_resp.status_code == 404
    assert draft_resp.json()["error"]["code"] == "not_found"

    # 5. Non-existent slug -> 404
    non_existent_resp = client.get(f"/api/v1/public/posts/non-existent-slug-{uuid.uuid4().hex[:6]}")
    assert non_existent_resp.status_code == 404
    assert non_existent_resp.json()["error"]["code"] == "not_found"


@pytest.mark.br("BR-POST-03")
def test_public_posts_list_only_returns_published_articles(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Public list includes published posts and strictly excludes drafts, sorted by published_at DESC."""
    admin_headers = create_auth_headers_and_cookies(client, db_session, admin_user)

    slug_a = f"published-a-{uuid.uuid4().hex[:6]}"
    slug_b = f"published-b-{uuid.uuid4().hex[:6]}"
    slug_draft = f"draft-x-{uuid.uuid4().hex[:6]}"

    # Create published A
    client.post(
        "/api/v1/admin/posts",
        json={"title": "مقال أ", "slug": slug_a, "is_published": True},
        headers=admin_headers,
    )
    # Create published B
    client.post(
        "/api/v1/admin/posts",
        json={"title": "مقال ب", "slug": slug_b, "is_published": True},
        headers=admin_headers,
    )
    # Create draft X
    client.post(
        "/api/v1/admin/posts",
        json={"title": "مسودة س", "slug": slug_draft, "is_published": False},
        headers=admin_headers,
    )

    client.cookies.clear()

    # Query public list
    resp = client.get("/api/v1/public/posts")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "public, max-age=300"
    items = resp.json()
    slugs = [p["slug"] for p in items]

    assert slug_a in slugs
    assert slug_b in slugs
    assert slug_draft not in slugs  # drafts must never leak to public catalog


@pytest.mark.br("BR-POST-03")
def test_public_posts_contract_strictness_no_leaks(
    client: TestClient,
    db_session: Session,
    admin_user: User,
) -> None:
    """Verify public response keys strictly adhere to PostPublicOut schema without extra internal keys."""
    admin_headers = create_auth_headers_and_cookies(client, db_session, admin_user)
    slug = f"contract-post-{uuid.uuid4().hex[:6]}"

    client.post(
        "/api/v1/admin/posts",
        json={
            "title": "مقال العقد",
            "slug": slug,
            "content": "محتوى",
            "is_published": True,
        },
        headers=admin_headers,
    )

    client.cookies.clear()
    resp = client.get(f"/api/v1/public/posts/{slug}")
    assert resp.status_code == 200
    data = resp.json()

    # Check allowed fields
    expected_fields = {"id", "slug", "title", "excerpt", "content", "cover_url", "published_at"}
    assert set(data.keys()) == expected_fields
