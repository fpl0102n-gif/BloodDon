"""posts

Revision ID: 009
Revises: 008
Create Date: 2026-10-18 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create posts table, constraints, partial index, and runtime grants."""
    op.create_table(
        "posts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name=op.f("ck_posts_slug_format"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_posts_campaign_id_campaigns"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_posts")),
        sa.UniqueConstraint("slug", name=op.f("uq_posts_slug")),
    )

    op.create_index(
        "ix_posts_published",
        "posts",
        [sa.text("published_at DESC")],
        postgresql_where=sa.text("is_published = true"),
    )

    ctx = op.get_context()
    if ctx.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_user') THEN
                    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE posts TO app_user;
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    """Drop posts table and partial index."""
    op.drop_index("ix_posts_published", table_name="posts")
    op.drop_table("posts")
