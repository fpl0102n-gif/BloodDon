"""donors

Revision ID: 004
Revises: 003
Create Date: 2026-10-16 11:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "donors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("blood_type", sa.String(length=5), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'attended'"), nullable=False
        ),
        sa.Column(
            "consent_keep_phone", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "consent_hero_name", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("consent_photo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "consent_public_phone", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("registered_by", sa.UUID(), nullable=True),
        sa.Column("donated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(first_name) BETWEEN 1 AND 40",
            name=op.f("ck_donors_first_name_len"),
        ),
        sa.CheckConstraint(
            "last_name IS NULL OR char_length(last_name) <= 40",
            name=op.f("ck_donors_last_name_len"),
        ),
        sa.CheckConstraint(
            "phone IS NULL OR phone ~ '^\\+?[0-9]{8,15}$'",
            name=op.f("ck_donors_phone_format"),
        ),
        sa.CheckConstraint(
            "blood_type IS NULL OR blood_type IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')",
            name=op.f("ck_donors_blood_type"),
        ),
        sa.CheckConstraint(
            "status IN ('attended', 'donated')",
            name=op.f("ck_donors_status"),
        ),
        sa.CheckConstraint(
            "phone IS NULL OR consent_keep_phone OR consent_public_phone",
            name=op.f("ck_donors_phone_needs_consent"),
        ),
        sa.CheckConstraint(
            "NOT consent_public_phone OR (phone IS NOT NULL AND blood_type IS NOT NULL)",
            name=op.f("ck_donors_public_phone_needs_data"),
        ),
        sa.CheckConstraint(
            "(status = 'donated') OR donated_at IS NULL",
            name=op.f("ck_donors_donated_at"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "site_id"],
            ["campaign_sites.campaign_id", "campaign_sites.site_id"],
            name="fk_donors_campaign_site",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["registered_by"],
            ["users.id"],
            name=op.f("fk_donors_registered_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_donors")),
    )
    op.create_index(
        "ix_donors_campaign_site_status",
        "donors",
        ["campaign_id", "site_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_donors_emergency",
        "donors",
        ["blood_type"],
        unique=False,
        postgresql_where=sa.text(
            "consent_public_phone AND status = 'donated' AND anonymized_at IS NULL"
        ),
    )
    op.create_index(
        "ix_donors_site_first_name_lower",
        "donors",
        ["site_id", sa.text("lower(first_name)")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_donors_site_first_name_lower", table_name="donors")
    op.drop_index(
        "ix_donors_emergency",
        table_name="donors",
        postgresql_where=sa.text(
            "consent_public_phone AND status = 'donated' AND anonymized_at IS NULL"
        ),
    )
    op.drop_index("ix_donors_campaign_site_status", table_name="donors")
    op.drop_table("donors")
