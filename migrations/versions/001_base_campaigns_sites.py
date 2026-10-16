"""base_campaigns_sites

Revision ID: 001
Revises:
Create Date: 2026-10-16 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. campaigns table
    op.create_table(
        "campaigns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("campaign_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="upcoming", nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "final_stats",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("personal_data_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name=op.f("ck_campaigns_slug_format"),
        ),
        sa.CheckConstraint(
            "status IN ('upcoming', 'live', 'ended')",
            name=op.f("ck_campaigns_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
        sa.UniqueConstraint("slug", name=op.f("uq_campaigns_slug")),
    )
    op.create_index(
        "ix_campaigns_status",
        "campaigns",
        ["status"],
        unique=False,
    )
    op.create_index(
        "uq_campaigns_single_current",
        "campaigns",
        ["is_current"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    # 2. sites table
    op.create_table(
        "sites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("logo_key", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("map_url", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sites")),
    )

    # 3. campaign_sites table
    op.create_table(
        "campaign_sites",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("supervisors_text", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "end_time IS NULL OR start_time IS NULL OR end_time > start_time",
            name=op.f("ck_campaign_sites_time_order"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_campaign_sites_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["sites.id"],
            name=op.f("fk_campaign_sites_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("campaign_id", "site_id", name=op.f("pk_campaign_sites")),
    )


def downgrade() -> None:
    op.drop_table("campaign_sites")
    op.drop_table("sites")
    op.drop_index(
        "uq_campaigns_single_current",
        table_name="campaigns",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index("ix_campaigns_status", table_name="campaigns")
    op.drop_table("campaigns")
