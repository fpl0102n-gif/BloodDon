"""Database schema, constraint, and relationship tests for the Post model (P9.1).

Per:
- docs/02-database.md §5.11 (posts table specification)
- docs/06-business-rules.md (BR-POST-01, BR-POST-05)
- docs/07-test-cases.md §5.15 (T-POST-01-a, T-POST-05-a)
- docs/11-decisions.md (D-01)
"""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.post import Post


@pytest.mark.unit
@pytest.mark.db
def test_post_model_metadata_and_columns() -> None:
    """Verify Post table metadata, column types, and constraints per docs/02-database.md §5.11."""
    table = Post.__table__
    assert isinstance(table, Table)
    assert table.name == "posts"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "campaign_id",
        "slug",
        "title",
        "excerpt",
        "content",
        "cover_url",
        "is_published",
        "published_at",
        "created_at",
        "updated_at",
    }
    assert expected_cols.issubset(col_names)

    # Column type and nullability assertions
    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)
    assert table.c.slug.nullable is False
    assert table.c.slug.unique is True
    assert table.c.title.nullable is False
    assert table.c.excerpt.nullable is True
    assert table.c.content.nullable is True
    assert table.c.cover_url.nullable is True
    assert table.c.is_published.nullable is False
    assert table.c.published_at.nullable is True
    assert table.c.campaign_id.nullable is True


@pytest.mark.db
@pytest.mark.br("BR-POST-01")
def test_t_post_01_a_invalid_slug_constraint(db_session: Session) -> None:
    """T-POST-01-a: DB CheckConstraint rejects invalid slug formats (BR-POST-01)."""
    invalid_slugs = [
        "Upper-Case-Slug",
        "spaces in slug",
        "-leading-hyphen",
        "trailing-hyphen-",
        "double--hyphen",
        "slug_with_underscore",
        "slug.with.dots",
        "slug@symbol",
    ]

    for bad_slug in invalid_slugs:
        post = Post(
            id=uuid.uuid4(),
            slug=bad_slug,
            title="Test Post",
            content="Valid content",
        )
        db_session.add(post)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()


@pytest.mark.db
@pytest.mark.br("BR-POST-01")
def test_t_post_01_a_duplicate_slug_unique_constraint(db_session: Session) -> None:
    """T-POST-01-a: Unique constraint rejects duplicate slug (BR-POST-01)."""
    slug = f"unique-post-{uuid.uuid4().hex[:8]}"

    post1 = Post(
        id=uuid.uuid4(),
        slug=slug,
        title="Post 1",
        content="Content 1",
    )
    db_session.add(post1)
    db_session.flush()

    post2 = Post(
        id=uuid.uuid4(),
        slug=slug,
        title="Post 2",
        content="Content 2",
    )
    db_session.add(post2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


@pytest.mark.db
@pytest.mark.br("BR-POST-05")
def test_t_post_05_a_campaign_deletion_sets_campaign_id_null(db_session: Session) -> None:
    """T-POST-05-a: Deleting a campaign sets post.campaign_id = NULL without deleting post."""
    camp = Campaign(
        id=uuid.uuid4(),
        title="Awareness Campaign",
        slug=f"awareness-{uuid.uuid4().hex[:8]}",
        campaign_date=date(2026, 11, 1),
        status="upcoming",
    )
    db_session.add(camp)
    db_session.flush()

    post = Post(
        id=uuid.uuid4(),
        campaign_id=camp.id,
        slug=f"article-{uuid.uuid4().hex[:8]}",
        title="Awareness Article",
        content="Article content",
        is_published=True,
        published_at=datetime.now(UTC),
    )
    db_session.add(post)
    db_session.flush()

    assert post.campaign_id == camp.id

    # Delete campaign
    db_session.delete(camp)
    db_session.flush()
    db_session.expire_all()

    # Verify post still exists and campaign_id is now NULL
    reloaded_post = db_session.get(Post, post.id)
    assert reloaded_post is not None
    assert reloaded_post.campaign_id is None
    assert reloaded_post.title == "Awareness Article"
