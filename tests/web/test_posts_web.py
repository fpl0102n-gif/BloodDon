"""Tests for Free Posts Web Layer (Phase 9.2).

Per:
- docs/04-api.md §27.1 (HTML pages on root, SSR Markdown)
- docs/05-architecture.md §4, §5.2
- docs/11-decisions.md (D-34, D-70, D-71, D-73, D-74)
- docs/07-test-cases.md §5.16 (T-WEB-01, T-WEB-03, T-WEB-04, T-WEB-06)
- docs/07-test-cases.md §5.15 (T-POST-03-a, T-POST-04-b ★)
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from tests.factories.post import create_post


@pytest.fixture
def client(test_client: TestClient) -> TestClient:
    """Use standard test client wired to isolated database transaction."""
    return test_client


# ==============================================================================
# Web Posts List Tests
# ==============================================================================


def test_posts_list_renders_published_only(client: TestClient, db_session: Session) -> None:
    """GET /posts renders published posts and excludes unpublished drafts."""
    # 1. Arrange: create one published post and one draft post
    published_post = create_post(
        db_session,
        slug="published-awareness-guide",
        title="دليل التبرع بالدم المنشور",
        excerpt="نبذة عن فوائد التبرع الصحية",
        is_published=True,
    )
    draft_post = create_post(
        db_session,
        slug="internal-draft-article",
        title="مسودة داخلية سرية",
        excerpt="هذه مسودة لا ينبغي أن تظهر للعامة",
        is_published=False,
    )

    # 2. Act
    response = client.get("/posts")

    # 3. Assert
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # Published post is visible with link
    assert published_post.title in html
    assert published_post.excerpt in html
    assert f"/posts/{published_post.slug}" in html

    # Draft post is strictly excluded
    assert draft_post.title not in html
    assert draft_post.slug not in html


def test_posts_list_empty_state_when_no_published_posts(
    client: TestClient, db_session: Session
) -> None:
    """GET /posts displays friendly Arabic empty state when no posts exist."""
    response = client.get("/posts")
    assert response.status_code == 200
    html = response.text
    assert "لا توجد مقالات منشورة حاليًا" in html or "لا توجد منشورات" in html


# ==============================================================================
# T-WEB-01-a ★: Open Graph Meta Tags & Canonical Link
# ==============================================================================


@pytest.mark.critical
def test_t_web_01_posts_open_graph_meta_tags(client: TestClient, db_session: Session) -> None:
    """T-WEB-01-a ★: GET /posts/{slug} generates complete Open Graph tags and canonical link in server."""
    settings = get_settings()
    public_base = settings.public_base_url.rstrip("/")

    post = create_post(
        db_session,
        slug="og-test-article",
        title="أهمية التبرع بالدم لإنقاذ الأرواح",
        excerpt="مقال تفصيلي حول الأثر الإيجابي للتبرع بالدم على صحة القلب.",
        content="## محتوى المقال\n\nنص تفصيلي عن التبرع.",
        cover_url="/static/images/og-default.svg",
        is_published=True,
    )

    response = client.get(f"/posts/{post.slug}")
    assert response.status_code == 200
    html = response.text

    expected_canonical = f"{public_base}/posts/{post.slug}"

    # 1. og:title matches post title
    assert f'<meta property="og:title" content="{post.title}">' in html

    # 2. og:description matches excerpt
    assert f'<meta property="og:description" content="{post.excerpt}">' in html

    # 3. og:url matches canonical URL
    assert f'<meta property="og:url" content="{expected_canonical}">' in html

    # 4. canonical link present
    assert f'<link rel="canonical" href="{expected_canonical}">' in html

    # 5. og:image is an absolute URL starting with public_base_url
    assert f'<meta property="og:image" content="{public_base}{post.cover_url}">' in html

    # 6. og:type is article
    assert '<meta property="og:type" content="article">' in html


# ==============================================================================
# T-WEB-03-a: Arabic RTL Language and Direction Attributes
# ==============================================================================


def test_t_web_03_posts_rtl_and_arabic_lang(client: TestClient, db_session: Session) -> None:
    """T-WEB-03-a: Root <html> element contains lang="ar" and dir="rtl" on posts pages."""
    post = create_post(
        db_session,
        slug="rtl-test-article",
        title="مقال لاختبار الاتجاه واللغة",
        is_published=True,
    )

    # 1. Verify list page
    resp_list = client.get("/posts")
    assert resp_list.status_code == 200
    assert '<html lang="ar" dir="rtl">' in resp_list.text

    # 2. Verify detail page
    resp_detail = client.get(f"/posts/{post.slug}")
    assert resp_detail.status_code == 200
    assert '<html lang="ar" dir="rtl">' in resp_detail.text


# ==============================================================================
# T-WEB-04-a ★: Zero Inline Scripts and Handlers for Strict CSP Compliance
# ==============================================================================


@pytest.mark.critical
def test_t_web_04_posts_zero_inline_scripts_and_handlers(
    client: TestClient, db_session: Session
) -> None:
    """T-WEB-04-a ★: HTML must contain ZERO inline scripts and ZERO inline event handlers."""
    post = create_post(
        db_session,
        slug="csp-test-article",
        title="مقال لاختبار سياسة أمان المحتوى",
        content="## عنوان\n\nنص تجريبي.",
        is_published=True,
    )

    pages = [
        client.get("/posts"),
        client.get(f"/posts/{post.slug}"),
    ]

    for resp in pages:
        assert resp.status_code == 200
        html = resp.text

        # 1. Zero inline script tags (<script>...</script> containing code)
        # Note: <script src="..."> is allowed, but <script>code</script> is forbidden
        inline_scripts = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.DOTALL)
        assert len(inline_scripts) == 0, f"Found inline scripts in page: {inline_scripts}"

        # 2. Zero inline event handlers (onclick, onload, onerror, onmouseover, etc.)
        inline_handlers = re.findall(r"\s(on[a-z]+)\s*=\s*[\"'][^\"']*[\"']", html, re.IGNORECASE)
        assert len(inline_handlers) == 0, f"Found inline event handlers: {inline_handlers}"

        # 3. No external remote CSS/JS/fonts calls (CDN / google fonts)
        external_resources = re.findall(
            r'(?:src|href)=["\']https?://(?!(?:localhost|127\.0\.0\.1))[^"\']+["\']',
            html,
            re.IGNORECASE,
        )
        # Filter out og:url or canonical metadata links (which must be absolute)
        resource_links = [
            link
            for link in external_resources
            if any(ext in link for ext in (".css", ".js", ".woff", ".ttf"))
        ]
        assert len(resource_links) == 0, f"Found external resource links: {resource_links}"


# ==============================================================================
# T-WEB-06-a ★ & T-POST-04-b ★: Markdown XSS Sanitization & autoescape
# ==============================================================================


@pytest.mark.critical
def test_t_web_06_post_markdown_xss_sanitization(client: TestClient, db_session: Session) -> None:
    """T-WEB-06-a ★ / T-POST-04-b ★: Markdown with malicious script/iframe/onerror is sanitized on server."""
    malicious_content = (
        "## عنوان سليم\n\n"
        "فقرة عادية تحتوي على معلومات طبية موثوقة.\n\n"
        "<script>alert('XSS Attack!');</script>\n\n"
        '<iframe src="https://attacker.evil.com/phish"></iframe>\n\n'
        '<img src="https://example.com/logo.png" onerror="alert(\'pwned\')">\n\n'
        "<a href=\"javascript:alert('steal-cookie')\">اضغط هنا</a>\n\n"
        '<object data="malware.swf"></object>\n\n'
        "* قائمة آمنة\n"
        "* عنصر آخر"
    )

    post = create_post(
        db_session,
        slug="xss-sanitization-article",
        title="مقال تجربة تعقيم المحتوى الخبيث",
        content=malicious_content,
        is_published=True,
    )

    response = client.get(f"/posts/{post.slug}")
    assert response.status_code == 200
    html = response.text

    # 1. Dangerous elements are completely stripped
    assert "<script>" not in html
    assert "alert('XSS Attack!');" not in html
    assert "<iframe" not in html
    assert "attacker.evil.com" not in html
    assert "onerror=" not in html
    assert "javascript:" not in html
    assert "<object" not in html

    # 2. Legitimate semantic elements are preserved
    assert "<h2>عنوان سليم</h2>" in html
    assert "فقرة عادية تحتوي على معلومات طبية موثوقة." in html
    assert "<li>قائمة آمنة</li>" in html


# ==============================================================================
# T-POST-03-a & T-WEB-11-a: Drafts & Non-Existent Slugs Return 404
# ==============================================================================


def test_t_post_03_web_draft_returns_404(client: TestClient, db_session: Session) -> None:
    """T-POST-03-a / T-WEB-11-a: Accessing an unpublished draft via web route returns clean 404 page."""
    draft_post = create_post(
        db_session,
        slug="confidential-draft-post",
        title="مسودة غير منشورة",
        is_published=False,
    )

    response = client.get(f"/posts/{draft_post.slug}")

    # 1. HTTP 404 status code
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # 2. Clean Arabic 404 message without technical stack trace
    assert "404" in html
    assert "الصفحة المطلوبة غير موجودة" in html
    assert "traceback" not in html.lower()
    assert "exception" not in html.lower()


def test_web_non_existent_post_returns_404(client: TestClient, db_session: Session) -> None:
    """Requesting an unknown slug returns clean 404 page."""
    response = client.get("/posts/totally-unknown-slug-12345")
    assert response.status_code == 404
    html = response.text
    assert "404" in html
    assert "الصفحة المطلوبة غير موجودة" in html
