"""Admin Posts API router (P9.1).

Per:
- docs/04-api.md §19 (Admin Posts: GET, POST, PATCH, DELETE)
- docs/06-business-rules.md (BR-POST-01..05)
- docs/03-auth-permissions.md §11.3 (Permission.POST_MANAGE, require_reauth for DELETE)
- docs/07-test-cases.md §5.15 (T-POST-01..05)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_post_service, require
from app.policy import Permission
from app.schemas.post import (
    PostAdminOut,
    PostAdminSummaryOut,
    PostCreate,
    PostUpdate,
)
from app.services.posts import PostService

router = APIRouter(prefix="/admin/posts", tags=["Admin Posts"])


@router.get(
    "",
    response_model=list[PostAdminSummaryOut],
    status_code=status.HTTP_200_OK,
    summary="List all posts (admin overview)",
)
def list_posts(
    post_service: Annotated[PostService, Depends(get_post_service)],
    _user: Annotated[Any, Depends(require(Permission.POST_MANAGE))],
    is_published: Annotated[
        bool | None,
        Query(description="Filter by publication flag"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PostAdminSummaryOut]:
    """Retrieve all articles and awareness posts across any publication status."""
    posts = post_service.list_admin_posts(
        is_published=is_published,
        limit=limit,
        offset=offset,
    )
    return [PostAdminSummaryOut.model_validate(p) for p in posts]


@router.post(
    "",
    response_model=PostAdminOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new free post or awareness article",
)
def create_post(
    payload: PostCreate,
    post_service: Annotated[PostService, Depends(get_post_service)],
    current_user: Annotated[Any, Depends(require(Permission.POST_MANAGE))],
) -> PostAdminOut:
    """Create a new post adhering to BR-POST-01..05."""
    created = post_service.create_post(payload, actor_user_id=current_user.id)
    return PostAdminOut.model_validate(created)


@router.get(
    "/{post_id}",
    response_model=PostAdminOut,
    status_code=status.HTTP_200_OK,
    summary="Get post details (admin view)",
)
def get_post(
    post_id: uuid.UUID,
    post_service: Annotated[PostService, Depends(get_post_service)],
    _user: Annotated[Any, Depends(require(Permission.POST_MANAGE))],
) -> PostAdminOut:
    """Get full details of a specific post."""
    post = post_service.get_admin_post(post_id)
    return PostAdminOut.model_validate(post)


@router.patch(
    "/{post_id}",
    response_model=PostAdminOut,
    status_code=status.HTTP_200_OK,
    summary="Update post content, metadata, or publication state",
)
def update_post(
    post_id: uuid.UUID,
    payload: PostUpdate,
    post_service: Annotated[PostService, Depends(get_post_service)],
    current_user: Annotated[Any, Depends(require(Permission.POST_MANAGE))],
) -> PostAdminOut:
    """Update post attributes per BR-POST-01..05."""
    updated = post_service.update_post(post_id, payload, actor_user_id=current_user.id)
    return PostAdminOut.model_validate(updated)


@router.delete(
    "/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete post permanently (requires step-up R)",
)
def delete_post(
    post_id: uuid.UUID,
    post_service: Annotated[PostService, Depends(get_post_service)],
    current_user: Annotated[
        Any,
        Depends(require(Permission.POST_MANAGE, require_reauth=True)),
    ],
) -> None:
    """Hard delete a post."""
    post_service.delete_post(post_id, actor_user_id=current_user.id)
