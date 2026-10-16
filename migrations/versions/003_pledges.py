"""pledges

Revision ID: 003
Revises: 002
Create Date: 2026-10-16 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pledges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(first_name) BETWEEN 2 AND 40",
            name=op.f("ck_pledges_first_name_len"),
        ),
        sa.CheckConstraint(
            "phone IS NULL OR phone ~ '^\\+?[0-9]{8,15}$'",
            name=op.f("ck_pledges_phone_format"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "site_id"],
            ["campaign_sites.campaign_id", "campaign_sites.site_id"],
            name="fk_pledges_campaign_site",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pledges")),
    )
    op.create_index(
        "ix_pledges_campaign_site",
        "pledges",
        ["campaign_id", "site_id"],
        unique=False,
    )
    op.create_index(
        "uq_pledges_campaign_site_phone",
        "pledges",
        ["campaign_id", "site_id", "phone"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_pledges_campaign_site_phone",
        table_name="pledges",
        postgresql_where=sa.text("phone IS NOT NULL"),
    )
    op.drop_index("ix_pledges_campaign_site", table_name="pledges")
    op.drop_table("pledges")
