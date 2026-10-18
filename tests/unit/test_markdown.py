"""Unit tests for server-side Markdown rendering and sanitization (T-CAMP-15-b ★).

Per:
- docs/06-business-rules.md §3.1 (BR-CAMP-15)
- docs/07-test-cases.md §5.2 (T-CAMP-15-b ★)
- docs/11-decisions.md (D-70)
"""

import pytest

from app.core.markdown import render_safe_markdown


@pytest.mark.br("BR-CAMP-15")
@pytest.mark.critical
def test_t_camp_15_b_renders_safe_markdown_properly() -> None:
    """T-CAMP-15-b: Valid Markdown constructs convert to valid semantic HTML."""
    raw_md = (
        "# حملة التبرع بالدم\n\n"
        "مرحبًا بكم في **اليوم الوطني للتبرع بالدم**.\n\n"
        "- الموقع الأول\n"
        "- الموقع الثاني\n\n"
        "[رابط المنصة](https://bloodday.dz)"
    )

    result = render_safe_markdown(raw_md)

    assert "<h1>حملة التبرع بالدم</h1>" in result
    assert "<strong>اليوم الوطني للتبرع بالدم</strong>" in result
    assert "<ul>" in result
    assert "<li>الموقع الأول</li>" in result
    assert '<a href="https://bloodday.dz"' in result


@pytest.mark.br("BR-CAMP-15")
@pytest.mark.critical
def test_t_camp_15_b_strips_malicious_scripts_and_events() -> None:
    """T-CAMP-15-b ★: Malicious script tags and event handlers are stripped completely."""
    malicious_inputs = [
        "<script>alert(1)</script>",
        "# Title\n<script src='https://evil.local/xss.js'></script>",
        "<img src='x' onerror='alert(document.cookie)'>",
        "[Click me](javascript:alert(1))",
        "<iframe src='https://malicious.com'></iframe>",
        "<a href='#' onclick='malicious()'>Test</a>",
        "<style>body { background: red; }</style>",
    ]

    for payload in malicious_inputs:
        cleaned = render_safe_markdown(payload)
        assert "<script" not in cleaned.lower()
        assert "alert(" not in cleaned
        assert "onerror" not in cleaned.lower()
        assert "javascript:" not in cleaned.lower()
        assert "<iframe" not in cleaned.lower()
        assert "onclick" not in cleaned.lower()
        assert "<style" not in cleaned.lower()


@pytest.mark.br("BR-CAMP-15")
def test_render_safe_markdown_empty_or_whitespace() -> None:
    """Empty or whitespace-only markdown returns empty string."""
    assert render_safe_markdown("") == ""
    assert render_safe_markdown("   \n\t  ") == ""
    assert render_safe_markdown(None) == ""
