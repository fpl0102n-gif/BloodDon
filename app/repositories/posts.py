"""Post database repository (P9.1).

Per:
- docs/05-architecture.md §4 & §5 (Layer boundaries: queries only, flush only, no commit/rollback)
- docs/02-database.md §5.11 (posts table schema and indexes)
- docs/06-business-rules.md (BR-POST-01..05)
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.post import Post


class PostRepository:
    """Repository handling SQL persistence and queries for Post entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, post_id: uuid.UUID) -> Post | None:
        """Fetch a post by its primary UUID."""
        stmt = select(Post).where(Post.id == post_id)
        return self.db.scalars(stmt).first()

    def get_by_slug(self, slug: str) -> Post | None:
        """Fetch a post by its unique slug."""
        stmt = select(Post).where(Post.slug == slug)
        return self.db.scalars(stmt).first()

    def create(self, post: Post) -> Post:
        """Persist a new post entity into the database session."""
        self.db.add(post)
        self.db.flush()
        return post

    def update(self, post: Post) -> Post:
        """Flush modifications of an existing post entity to the database."""
        self.db.flush()
        return post

    def delete(self, post: Post) -> None:
        """Delete a post entity from the database."""
        self.db.delete(post)
        self.db.flush()

    def list_all(
        self,
        *,
        is_published: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Post]:
        """List posts for administrative overview, optionally filtered by publication flag."""
        stmt = select(Post)
        if is_published is not None:
            stmt = stmt.where(Post.is_published.is_(is_published))
        stmt = stmt.order_by(Post.created_at.desc()).limit(limit).offset(offset)
        return self.db.scalars(stmt).all()

    def count_all(self, *, is_published: bool | None = None) -> int:
        """Return total count of posts, optionally filtered by publication flag."""
        stmt = select(func.count(Post.id))
        if is_published is not None:
            stmt = stmt.where(Post.is_published.is_(is_published))
        return self.db.scalar(stmt) or 0

    def list_public(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Post]:
        """List published posts for public catalog view ordered by published_at DESC."""
        stmt = (
            select(Post)
            .where(Post.is_published.is_(True))
            .order_by(Post.published_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return self.db.scalars(stmt).all()

    def count_public(self) -> int:
        """Return total count of published posts."""
        stmt = select(func.count(Post.id)).where(Post.is_published.is_(True))
        return self.db.scalar(stmt) or 0
