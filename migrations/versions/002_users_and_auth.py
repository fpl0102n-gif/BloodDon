"""users_and_auth

Revision ID: 002
Revises: 001
Create Date: 2026-10-16 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. users table
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("totp_secret_enc", sa.Text(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("totp_last_used_step", sa.Integer(), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('supervisor', 'admin')",
            name=op.f("ck_users_role"),
        ),
        sa.CheckConstraint(
            "role = 'admin' OR site_id IS NOT NULL",
            name=op.f("ck_users_supervisor_site"),
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["sites.id"],
            name=op.f("fk_users_site_id_sites"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )
    op.create_index(
        "ix_users_site_id",
        "users",
        ["site_id"],
        unique=False,
    )

    # 2. user_tokens table
    op.create_table(
        "user_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "purpose IN ('invite', 'password_reset')",
            name=op.f("ck_user_tokens_purpose"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_tokens_token_hash")),
    )
    op.create_index(
        "ix_user_tokens_user_id",
        "user_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_tokens_user_id_purpose",
        "user_tokens",
        ["user_id", "purpose"],
        unique=False,
    )

    # 3. mfa_recovery_codes table
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mfa_recovery_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mfa_recovery_codes")),
    )
    op.create_index(
        "ix_mfa_recovery_codes_user_id",
        "mfa_recovery_codes",
        ["user_id"],
        unique=False,
    )

    # 4. auth_sessions table
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index(
        "ix_auth_sessions_expires_at",
        "auth_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_sessions_user_id",
        "auth_sessions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_index("ix_mfa_recovery_codes_user_id", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")

    op.drop_index("ix_user_tokens_user_id_purpose", table_name="user_tokens")
    op.drop_index("ix_user_tokens_user_id", table_name="user_tokens")
    op.drop_table("user_tokens")

    op.drop_index("ix_users_site_id", table_name="users")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
