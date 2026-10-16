"""db_roles_and_grants

Revision ID: 008
Revises: 006
Create Date: 2026-10-16 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Grant table privileges to app_user and enforce append-only audit_log (BR-AUDIT-02)."""
    ctx = op.get_context()
    if ctx.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_user') THEN
                    -- Grant runtime schema access
                    GRANT USAGE ON SCHEMA public TO app_user;

                    -- Grant DML operations on all existing tables and sequences
                    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
                    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;

                    -- Enforce append-only on audit_log per BR-AUDIT-02 & docs/02-database.md §7:
                    -- app_user can only SELECT and INSERT on audit_log; NO UPDATE, DELETE, or TRUNCATE.
                    REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM app_user;
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    """Restore default table privileges on audit_log for app_user."""
    ctx = op.get_context()
    if ctx.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_user') THEN
                    GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log TO app_user;
                END IF;
            END
            $$;
            """
        )
