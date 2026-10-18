"""Public Web router for awareness posts and articles (Phase 9.2).

Per:
- docs/04-api.md §27.1 (HTML pages on root)
- docs/05-architecture.md §4 (web/routers)
- docs/11-decisions.md (D-34, D-70, D-71, D-73, D-74)
- docs/07-test-cases.md §5.16 (T-WEB-01, T-WEB-03, T-WEB-04, T-WEB-06)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.api.deps import get_post_service, require
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.core.markdown import render_safe_markdown
from app.policy import Permission
from app.services.posts import PostService
from app.web.templates import templates

router = APIRouter(tags=["Web Posts"])


@router.get(
    "/posts",
    response_class=HTMLResponse,
    dependencies=[Depends(require(Permission.POST_READ))],
    summary="Public articles list page",
)
def render_posts_list(
    request: Request,
    post_service: Annotated[PostService, Depends(get_post_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HTMLResponse:
    """Render public catalog of published awareness posts and articles."""
    posts = post_service.list_public_posts(limit=limit, offset=offset)

    public_base = settings.public_base_url.rstrip("/")
    canonical_url = f"{public_base}/posts"
    og_image_url = f"{public_base}/static/images/og-default.png"

    return templates.TemplateResponse(
        request=request,
        name="public/posts_list.html",
        context={
            "posts": posts,
            "canonical_url": canonical_url,
            "og_image_url": og_image_url,
        },
    )


@router.get(
    "/posts/{slug}",
    response_class=HTMLResponse,
    dependencies=[Depends(require(Permission.POST_READ))],
    summary="Public article detail page",
)
def render_post_detail(
    request: Request,
    slug: str,
    post_service: Annotated[PostService, Depends(get_post_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    """Render single published article with server-side sanitized Markdown and Open Graph tags."""
    public_base = settings.public_base_url.rstrip("/")
    canonical_url = f"{public_base}/posts/{slug}"

    try:
        post = post_service.get_public_post(slug)
    except NotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="errors/404.html",
            context={
                "canonical_url": canonical_url,
                "message": "عذرًا، المنشور المطلوب غير موجود أو لم يعد منشورًا.",
            },
            status_code=404,
        )

    # Convert Markdown to sanitized HTML (nh3 + autoescape)
    content_html = render_safe_markdown(post.content)

    # Resolve absolute Open Graph image URL
    if post.cover_url:
        if post.cover_url.startswith(("http://", "https://")):
            og_image_url = post.cover_url
        else:
            og_image_url = f"{public_base}{post.cover_url}"
    else:
        og_image_url = f"{public_base}/static/images/og-default.png"

    return templates.TemplateResponse(
        request=request,
        name="public/post_detail.html",
        context={
            "post": post,
            "content_html": content_html,
            "canonical_url": canonical_url,
            "og_image_url": og_image_url,
        },
    )
