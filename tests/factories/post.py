"""Test factory for Post models.

Per docs/02-database.md §5.11, BR-POST-01..05.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.post import Post


def create_post(
    session: Session,
    *,
    post_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    slug: str | None = None,
    title: str = "مقال توعوي عن أهمية التبرع بالدم",
    excerpt: str | None = "موجز المقال التوعوي حول شروط التبرع بالدم.",
    content: str | None = "## أهمية التبرع\n\nالتبرع بالدم عمل إنساني نبيل ينقذ الأرواح.",  # noqa: RUF001
    cover_url: str | None = "/static/images/og-default.svg",
    is_published: bool = True,
    published_at: datetime | None = None,
) -> Post:
    """Create and persist a Post instance in the database session."""
    now = datetime.now(UTC)
    unique_suffix = uuid.uuid4().hex[:6]
    actual_slug = slug or f"awareness-article-{unique_suffix}"
    actual_published_at = (
        published_at if published_at is not None else (now if is_published else None)
    )

    post = Post(
        id=post_id or uuid.uuid4(),
        campaign_id=campaign_id,
        slug=actual_slug,
        title=title,
        excerpt=excerpt,
        content=content,
        cover_url=cover_url,
        is_published=is_published,
        published_at=actual_published_at,
        created_at=now,
        updated_at=now,
    )
    session.add(post)
    session.flush()
    return post
