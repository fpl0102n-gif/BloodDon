"""Public Posts API router (P9.1).

Per:
- docs/04-api.md §6.10 (Public Posts: GET /public/posts, GET /public/posts/{slug})
- docs/06-business-rules.md (BR-POST-01..05)
- docs/03-auth-permissions.md §11.1 (Permission.POST_READ)
- docs/07-test-cases.md §5.15 (T-POST-03-a)
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import get_post_service, require
from app.policy import Permission
from app.schemas.post import PostPublicCardOut, PostPublicOut
from app.services.posts import PostService

router = APIRouter(prefix="/public/posts", tags=["Public Posts"])


@router.get(
    "",
    response_model=list[PostPublicCardOut],
    status_code=status.HTTP_200_OK,
    summary="List published posts (catalog view)",
)
def list_public_posts(
    response: Response,
    post_service: Annotated[PostService, Depends(get_post_service)],
    _user: Annotated[Any, Depends(require(Permission.POST_READ))],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PostPublicCardOut]:
    """Retrieve published articles ordered by published_at DESC (BR-POST-03)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    posts = post_service.list_public_posts(
        limit=limit,
        offset=offset,
    )
    return [PostPublicCardOut.model_validate(p) for p in posts]


@router.get(
    "/{slug}",
    response_model=PostPublicOut,
    status_code=status.HTTP_200_OK,
    summary="Get published post detail by slug",
)
def get_public_post(
    slug: str,
    response: Response,
    post_service: Annotated[PostService, Depends(get_post_service)],
    _user: Annotated[Any, Depends(require(Permission.POST_READ))],
) -> PostPublicOut:
    """Retrieve a published post by slug (BR-POST-03). 404 if draft or non-existent."""
    response.headers["Cache-Control"] = "public, max-age=300"
    post = post_service.get_public_post(slug)
    return PostPublicOut.model_validate(post)
