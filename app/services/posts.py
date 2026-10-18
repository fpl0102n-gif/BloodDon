"""Post service orchestrating domain business rules for free posts and articles (P9.1).

Per:
- docs/06-business-rules.md (BR-POST-01..05)
- docs/04-api.md §19 (Admin Posts) & §6.10 (Public Posts)
- docs/05-architecture.md §5 (Layer boundaries: no commit/rollback, no HTTP/FastAPI imports)
- docs/07-test-cases.md §5.15 (T-POST-01..05)
"""

import uuid
from collections.abc import Sequence

from app.core.clock import Clock, get_clock
from app.core.errors import (
    NotFoundError,
    SlugTakenError,
    ValidationError,
)
from app.models.post import Post
from app.repositories.campaigns import CampaignRepository
from app.repositories.posts import PostRepository
from app.schemas.post import MAX_CONTENT_BYTES, PostCreate, PostUpdate
from app.services.audit import AuditService


class PostService:
    """Domain service managing the lifecycle and rules for free posts and awareness articles."""

    def __init__(
        self,
        repository: PostRepository,
        campaign_repository: CampaignRepository | None = None,
        audit_service: AuditService | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repository = repository
        self.campaign_repository = campaign_repository
        self.audit_service = audit_service
        self.clock = clock or get_clock()

    def create_post(
        self,
        payload: PostCreate,
        actor_user_id: uuid.UUID | None = None,
    ) -> Post:
        """Create a new free post adhering to BR-POST-01..05."""
        # 1. Verify slug uniqueness per BR-POST-01 (rejects with 409 slug_taken)
        existing = self.repository.get_by_slug(payload.slug)
        if existing is not None:
            raise SlugTakenError(f"Post with slug '{payload.slug}' already exists")

        # 2. Verify content byte length per BR-POST-04 (100 KB limit)
        if payload.content is not None:
            content_bytes = len(payload.content.encode("utf-8"))
            if content_bytes > MAX_CONTENT_BYTES:
                raise ValidationError(
                    f"Content size exceeds maximum limit of {MAX_CONTENT_BYTES} bytes"
                )

        # 3. Verify associated campaign exists if provided per BR-POST-05
        if payload.campaign_id is not None and self.campaign_repository is not None:
            campaign = self.campaign_repository.get_by_id(payload.campaign_id)
            if campaign is None:
                raise NotFoundError(f"Campaign with ID '{payload.campaign_id}' was not found")

        # 4. Handle publication timestamp per BR-POST-02
        published_at = self.clock.now() if payload.is_published else None

        # 5. Instantiate post entity
        post = Post(
            id=uuid.uuid4(),
            campaign_id=payload.campaign_id,
            slug=payload.slug,
            title=payload.title,
            excerpt=payload.excerpt,
            content=payload.content,
            cover_url=payload.cover_url,
            is_published=payload.is_published,
            published_at=published_at,
        )

        created = self.repository.create(post)

        # 6. Record audit event (zero PII per BR-AUDIT-01)
        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="post.create",
                entity_type="post",
                entity_id=created.id,
                actor_user_id=actor_user_id,
                details={"slug": created.slug, "is_published": created.is_published},
            )

        return created

    def get_admin_post(self, post_id: uuid.UUID) -> Post:
        """Retrieve full post details by ID for administrative inspection."""
        post = self.repository.get_by_id(post_id)
        if post is None:
            raise NotFoundError(f"Post with ID '{post_id}' was not found")
        return post

    def list_admin_posts(
        self,
        *,
        is_published: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Post]:
        """List posts across any publication status for administrative management."""
        return self.repository.list_all(
            is_published=is_published,
            limit=limit,
            offset=offset,
        )

    def update_post(
        self,
        post_id: uuid.UUID,
        payload: PostUpdate,
        actor_user_id: uuid.UUID | None = None,
    ) -> Post:
        """Update an existing post adhering to BR-POST-01..05."""
        post = self.repository.get_by_id(post_id)
        if post is None:
            raise NotFoundError(f"Post with ID '{post_id}' was not found")

        # 1. Slug change check per BR-POST-01
        if payload.slug is not None and payload.slug != post.slug:
            existing = self.repository.get_by_slug(payload.slug)
            if existing is not None and existing.id != post.id:
                raise SlugTakenError(f"Post with slug '{payload.slug}' already exists")
            post.slug = payload.slug

        # 2. Content size check per BR-POST-04
        if payload.content is not None:
            content_bytes = len(payload.content.encode("utf-8"))
            if content_bytes > MAX_CONTENT_BYTES:
                raise ValidationError(
                    f"Content size exceeds maximum limit of {MAX_CONTENT_BYTES} bytes"
                )
            post.content = payload.content

        # 3. Associated campaign check per BR-POST-05
        if payload.campaign_id is not None and self.campaign_repository is not None:
            campaign = self.campaign_repository.get_by_id(payload.campaign_id)
            if campaign is None:
                raise NotFoundError(f"Campaign with ID '{payload.campaign_id}' was not found")
            post.campaign_id = payload.campaign_id

        # 4. Scalar fields
        if payload.title is not None:
            post.title = payload.title
        if payload.excerpt is not None:
            post.excerpt = payload.excerpt
        if payload.cover_url is not None:
            post.cover_url = payload.cover_url

        # 5. Publication state & published_at per BR-POST-02
        # published_at is set on the FIRST publish only and never overwritten
        if payload.is_published is not None:
            if payload.is_published and post.published_at is None:
                post.published_at = self.clock.now()
            post.is_published = payload.is_published

        updated = self.repository.update(post)

        # 6. Audit event
        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="post.update",
                entity_type="post",
                entity_id=updated.id,
                actor_user_id=actor_user_id,
                details={"slug": updated.slug, "is_published": updated.is_published},
            )

        return updated

    def delete_post(
        self,
        post_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        """Delete an existing post permanently."""
        post = self.repository.get_by_id(post_id)
        if post is None:
            raise NotFoundError(f"Post with ID '{post_id}' was not found")

        slug = post.slug
        self.repository.delete(post)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="post.delete",
                entity_type="post",
                entity_id=post_id,
                actor_user_id=actor_user_id,
                details={"slug": slug},
            )

    def get_public_post(self, slug: str) -> Post:
        """Retrieve a published post by slug for visitors (BR-POST-03).

        If unpublished or non-existent, raises NotFoundError (404).
        """
        post = self.repository.get_by_slug(slug)
        if post is None or not post.is_published:
            raise NotFoundError(f"Post with slug '{slug}' was not found")
        return post

    def list_public_posts(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Post]:
        """List published posts for public blog overview ordered newest published_at first (BR-POST-03)."""
        return self.repository.list_public(
            limit=limit,
            offset=offset,
        )
