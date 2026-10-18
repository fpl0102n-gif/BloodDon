"""SQLAlchemy ORM model for Free Posts (P9.1).

Per:
- docs/02-database.md §5.11 (posts table specification)
- docs/06-business-rules.md (BR-POST-01..05)
- docs/07-test-cases.md §5.15 (T-POST-01..05)
- docs/11-decisions.md (D-01)
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign


class Post(Base):
    """Free post / article entity for blog and awareness campaigns.

    Governed by BR-POST-* rules:
    - BR-POST-01: slug must be unique and match lowercase kebab-case format.
    - BR-POST-02: published_at is set on first publish only.
    - BR-POST-03: public reads access is_published=True only.
    - BR-POST-04: content in Markdown with max 100 KB limit.
    - BR-POST-05: optional campaign_id FK with ON DELETE SET NULL.
    """

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )
    slug: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    excerpt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    cover_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationship to optional campaign
    campaign: Mapped["Campaign | None"] = relationship(
        "Campaign",
        lazy="joined",
    )

    __table_args__ = (
        CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name="slug_format",
        ),
        Index(
            "ix_posts_published",
            published_at.desc(),
            postgresql_where=text("is_published = true"),
        ),
    )
