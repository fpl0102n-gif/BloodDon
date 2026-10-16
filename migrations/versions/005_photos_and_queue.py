"""photos_and_queue

Revision ID: 005
Revises: 004
Create Date: 2026-10-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. photos table
    op.create_table(
        "photos",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="general", nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("donor_id", sa.UUID(), nullable=True),
        sa.Column("mime_type", sa.String(length=50), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('general', 'individual')",
            name=op.f("ck_photos_kind"),
        ),
        sa.CheckConstraint(
            "(kind = 'individual' AND donor_id IS NOT NULL) OR (kind = 'general' AND donor_id IS NULL)",
            name=op.f("ck_photos_individual_donor"),
        ),
        sa.CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name=op.f("ck_photos_mime_type"),
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name=op.f("ck_photos_size_bytes"),
        ),
        sa.CheckConstraint(
            "caption IS NULL OR char_length(caption) <= 200",
            name=op.f("ck_photos_caption_len"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "site_id"],
            ["campaign_sites.campaign_id", "campaign_sites.site_id"],
            name="fk_photos_campaign_site",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["donor_id"],
            ["donors.id"],
            name=op.f("fk_photos_donor_id_donors"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name=op.f("fk_photos_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photos")),
    )
    op.create_index(
        "ix_photos_campaign_site",
        "photos",
        ["campaign_id", "site_id"],
        unique=False,
    )
    op.create_index(
        "ix_photos_donor_id",
        "photos",
        ["donor_id"],
        unique=False,
    )

    # 2. file_deletion_queue table
    op.create_table(
        "file_deletion_queue",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_deletion_queue")),
    )
    op.create_index(
        "ix_file_deletion_queue_deleted_at",
        "file_deletion_queue",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_file_deletion_queue_deleted_at", table_name="file_deletion_queue")
    op.drop_table("file_deletion_queue")

    op.drop_index("ix_photos_donor_id", table_name="photos")
    op.drop_index("ix_photos_campaign_site", table_name="photos")
    op.drop_table("photos")
