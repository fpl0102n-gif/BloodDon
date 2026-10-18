"""Server-side safe Markdown rendering and sanitization.

Per:
- docs/06-business-rules.md §3.1 (BR-CAMP-15)
- docs/11-decisions.md §3 (D-70)
- docs/07-test-cases.md §5.2 (T-CAMP-15-b)
- docs/05-architecture.md §19

Converts raw Markdown to sanitized, safe HTML. Drops malicious scripts,
dangerous tags, inline event handlers, and unknown URI schemes.
"""

import markdown
import nh3

# Strictly allowed HTML tags for article/campaign content
ALLOWED_TAGS: set[str] = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "dd",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

# Strictly allowed attributes per tag
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "abbr": {"title"},
    "th": {"align", "colspan", "rowspan"},
    "td": {"align", "colspan", "rowspan"},
}


def render_safe_markdown(raw_content: str | None) -> str:
    """Convert raw Markdown to sanitized HTML, completely stripping dangerous code.

    Raw HTML embedded in Markdown is processed and strictly sanitized by nh3 (ammonia).
    Disallows <script>, <style>, <iframe vốn>, event handlers (onerror, onload), and
    javascript: URI schemes.
    """
    if not raw_content or not raw_content.strip():
        return ""

    # 1. Render Markdown to HTML using standard Python-Markdown
    rendered_html = markdown.markdown(
        raw_content,
        extensions=["extra", "sane_lists"],
        output_format="html",
    )

    # 2. Sanitize resulting HTML using nh3 (Rust ammonia library)
    return nh3.clean(
        rendered_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
    )
