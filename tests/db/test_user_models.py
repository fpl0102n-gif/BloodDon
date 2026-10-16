"""Database constraint and schema tests for User, UserToken, MfaRecoveryCode, and AuthSession.

Implements P2.2 per docs/02-database.md §3, §4, §5.4-§5.7, docs/06-business-rules.md §5,
and docs/07-test-cases.md:
- T-USER-01-a: Rejection of supervisor without site_id (BR-USER-01).
- T-USER-01-b: Admin without site_id is accepted.
- T-USER-02-a: Functional unique index rejects case-insensitive duplicate email (BR-USER-02).
- CheckConstraint on user roles ('supervisor', 'admin').
- CheckConstraint on token purposes ('invite', 'password_reset').
- Foreign key cascade deletions on dependent auth entities.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Table, inspect
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.campaign import Site
from app.models.user import AuthSession, MfaRecoveryCode, User, UserToken

# ==============================================================================
# 1. Structural Schema and Metadata Tests (docs/02-database.md §5.4-§5.7)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.db
def test_user_model_metadata_and_columns() -> None:
    """Verify User table metadata, column types, and constraints."""
    table = User.__table__
    assert isinstance(table, Table)
    assert table.name == "users"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "email",
        "full_name",
        "password_hash",
        "role",
        "site_id",
        "is_active",
        "must_change_password",
        "totp_secret_enc",
        "totp_enabled",
        "totp_last_used_step",
        "failed_login_count",
        "locked_until",
        "last_login_at",
        "created_at",
        "updated_at",
    }
    assert expected_cols.issubset(col_names)

    # Column properties
    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)
    assert table.c.email.nullable is False
    assert table.c.full_name.nullable is False
    assert table.c.role.nullable is False
    assert table.c.site_id.nullable is True
    assert table.c.is_active.nullable is False
    assert table.c.must_change_password.nullable is False
    assert table.c.totp_enabled.nullable is False
    assert table.c.failed_login_count.nullable is False

    # Check constraints
    ck_names = {ck.name for ck in table.constraints if hasattr(ck, "name")}
    assert "ck_users_role" in ck_names
    assert "ck_users_supervisor_site" in ck_names

    # Functional index on lower(email)
    indexes = [idx for idx in table.indexes if idx.name == "uq_users_email_lower"]
    assert len(indexes) == 1
    assert indexes[0].unique is True

    # Foreign key to sites
    fk_targets = {(fk.parent.name, fk.target_fullname, fk.ondelete) for fk in table.foreign_keys}
    assert ("site_id", "sites.id", "SET NULL") in fk_targets


@pytest.mark.unit
@pytest.mark.db
def test_user_token_model_metadata() -> None:
    """Verify UserToken table metadata, unique token_hash, and foreign key."""
    table = UserToken.__table__
    assert isinstance(table, Table)
    assert table.name == "user_tokens"

    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)
    assert table.c.user_id.nullable is False
    assert table.c.purpose.nullable is False
    assert table.c.token_hash.nullable is False
    assert table.c.token_hash.unique is True
    assert table.c.expires_at.nullable is False
    assert table.c.used_at.nullable is True

    # Purpose check constraint
    ck_names = {ck.name for ck in table.constraints if hasattr(ck, "name")}
    assert "ck_user_tokens_purpose" in ck_names

    # Foreign key
    fk_targets = {(fk.parent.name, fk.target_fullname, fk.ondelete) for fk in table.foreign_keys}
    assert ("user_id", "users.id", "CASCADE") in fk_targets


@pytest.mark.unit
@pytest.mark.db
def test_mfa_recovery_code_model_metadata() -> None:
    """Verify MfaRecoveryCode table metadata and foreign key."""
    table = MfaRecoveryCode.__table__
    assert isinstance(table, Table)
    assert table.name == "mfa_recovery_codes"

    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)
    assert table.c.user_id.nullable is False
    assert table.c.code_hash.nullable is False
    assert table.c.used_at.nullable is True

    fk_targets = {(fk.parent.name, fk.target_fullname, fk.ondelete) for fk in table.foreign_keys}
    assert ("user_id", "users.id", "CASCADE") in fk_targets


@pytest.mark.unit
@pytest.mark.db
def test_auth_session_model_metadata() -> None:
    """Verify AuthSession table metadata, primary key, and foreign key."""
    table = AuthSession.__table__
    assert isinstance(table, Table)
    assert table.name == "auth_sessions"

    assert table.c.id.primary_key is True
    assert table.c.user_id.nullable is False
    assert table.c.created_at.nullable is False
    assert table.c.last_seen_at.nullable is False
    assert table.c.expires_at.nullable is False
    assert table.c.mfa_verified.nullable is False

    fk_targets = {(fk.parent.name, fk.target_fullname, fk.ondelete) for fk in table.foreign_keys}
    assert ("user_id", "users.id", "CASCADE") in fk_targets


@pytest.mark.unit
@pytest.mark.db
def test_user_orm_relationships() -> None:
    """Verify SQLAlchemy ORM relationships on User model."""
    user_mapper = inspect(User)
    token_mapper = inspect(UserToken)
    mfa_mapper = inspect(MfaRecoveryCode)
    session_mapper = inspect(AuthSession)

    assert "tokens" in user_mapper.relationships
    assert "mfa_recovery_codes" in user_mapper.relationships
    assert "sessions" in user_mapper.relationships
    assert "site" in user_mapper.relationships

    assert "user" in token_mapper.relationships
    assert "user" in mfa_mapper.relationships
    assert "user" in session_mapper.relationships


# ==============================================================================
# 2. Database Constraint Rejection Tests (docs/07-test-cases.md)
# ==============================================================================


@pytest.fixture
def isolated_db(db_session: Session) -> Session:
    """Ensure database schema is created inside the active test transaction."""
    Base.metadata.create_all(bind=db_session.connection())
    return db_session


@pytest.mark.db
@pytest.mark.br("BR-USER-01")
def test_t_user_01_a_supervisor_without_site_rejected(isolated_db: Session) -> None:
    """T-USER-01-a: Supervisor creation without site_id is rejected by check constraint."""
    supervisor = User(
        email="supervisor.nosite@bloodday.local",
        full_name="Site-less Supervisor",
        role="supervisor",
        site_id=None,
    )
    isolated_db.add(supervisor)

    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    assert (
        "ck_users_supervisor_site" in str(exc_info.value)
        or "check constraint" in str(exc_info.value).lower()
    )


@pytest.mark.db
@pytest.mark.br("BR-USER-01")
def test_t_user_01_b_admin_without_site_allowed(isolated_db: Session) -> None:
    """T-USER-01-b: Administrator without site_id is successfully created."""
    admin = User(
        email="admin.nosite@bloodday.local",
        full_name="Platform Administrator",
        role="admin",
        site_id=None,
    )
    isolated_db.add(admin)
    isolated_db.flush()

    assert admin.id is not None
    assert admin.site_id is None
    assert admin.role == "admin"


@pytest.mark.db
@pytest.mark.br("BR-USER-01")
def test_supervisor_with_valid_site_allowed(isolated_db: Session) -> None:
    """Verify that a supervisor with an associated site_id is accepted."""
    site = Site(
        name="Medical Faculty Center",
        address="1st May Plaza, Algiers",
    )
    isolated_db.add(site)
    isolated_db.flush()

    supervisor = User(
        email="supervisor.site@bloodday.local",
        full_name="Site Supervisor",
        role="supervisor",
        site_id=site.id,
    )
    isolated_db.add(supervisor)
    isolated_db.flush()

    assert supervisor.id is not None
    assert supervisor.site_id == site.id


@pytest.mark.db
@pytest.mark.br("BR-USER-02")
def test_t_user_02_a_email_case_insensitive_uniqueness(isolated_db: Session) -> None:
    """T-USER-02-a: Functional index on lower(email) rejects case-variant duplicate emails."""
    admin1 = User(
        email="doctor.ali@bloodday.local",
        full_name="Dr. Ali First",
        role="admin",
    )
    isolated_db.add(admin1)
    isolated_db.flush()

    # Second user with uppercase variant of the same email
    admin2 = User(
        email="DOCTOR.ALI@bloodday.local",
        full_name="Dr. Ali Duplicate",
        role="admin",
    )
    isolated_db.add(admin2)

    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    assert (
        "uq_users_email_lower" in str(exc_info.value)
        or "unique constraint" in str(exc_info.value).lower()
        or "duplicate key" in str(exc_info.value).lower()
    )


@pytest.mark.db
@pytest.mark.br("BR-AUTH-01")
@pytest.mark.parametrize("invalid_role", ["guest", "superadmin", "operator", "user"])
def test_user_role_check_constraint_rejects_invalid(
    isolated_db: Session, invalid_role: str
) -> None:
    """Verify that only 'supervisor' and 'admin' roles are accepted by the database."""
    user = User(
        email=f"invalid.role.{invalid_role}@bloodday.local",
        full_name="Invalid Role User",
        role=invalid_role,
    )
    isolated_db.add(user)

    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    assert (
        "ck_users_role" in str(exc_info.value) or "check constraint" in str(exc_info.value).lower()
    )


@pytest.mark.db
@pytest.mark.br("BR-AUTH-01")
@pytest.mark.parametrize("valid_purpose", ["invite", "password_reset"])
def test_user_token_valid_purposes_accepted(isolated_db: Session, valid_purpose: str) -> None:
    """Verify that 'invite' and 'password_reset' are valid token purposes."""
    admin = User(
        email=f"token.test.{valid_purpose}@bloodday.local",
        full_name="Token Tester",
        role="admin",
    )
    isolated_db.add(admin)
    isolated_db.flush()

    token = UserToken(
        user_id=admin.id,
        purpose=valid_purpose,
        token_hash=f"hash_{valid_purpose}_{uuid.uuid4().hex}",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    isolated_db.add(token)
    isolated_db.flush()

    assert token.id is not None
    assert token.purpose == valid_purpose


@pytest.mark.db
@pytest.mark.br("BR-AUTH-01")
def test_user_token_invalid_purpose_rejected(isolated_db: Session) -> None:
    """Verify that unapproved token purposes are rejected by check constraint."""
    admin = User(
        email="invalid.token.purpose@bloodday.local",
        full_name="Token Tester Invalid",
        role="admin",
    )
    isolated_db.add(admin)
    isolated_db.flush()

    token = UserToken(
        user_id=admin.id,
        purpose="session_refresh",
        token_hash=f"hash_invalid_{uuid.uuid4().hex}",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    isolated_db.add(token)

    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    assert (
        "ck_user_tokens_purpose" in str(exc_info.value)
        or "check constraint" in str(exc_info.value).lower()
    )


@pytest.mark.db
def test_user_cascade_deletion(isolated_db: Session) -> None:
    """Verify that deleting a User cascades to their tokens, recovery codes, and sessions."""
    admin = User(
        email="cascade.admin@bloodday.local",
        full_name="Cascade Admin",
        role="admin",
    )
    isolated_db.add(admin)
    isolated_db.flush()

    token = UserToken(
        user_id=admin.id,
        purpose="invite",
        token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=72),
    )
    recovery_code = MfaRecoveryCode(
        user_id=admin.id,
        code_hash=uuid.uuid4().hex,
    )
    session = AuthSession(
        id=uuid.uuid4().hex,
        user_id=admin.id,
        expires_at=datetime.now(UTC) + timedelta(hours=8),
    )
    isolated_db.add_all([token, recovery_code, session])
    isolated_db.flush()
    token_id = token.id
    recovery_code_id = recovery_code.id
    session_id = session.id

    # Delete the user
    isolated_db.delete(admin)
    isolated_db.flush()
    isolated_db.expire_all()

    # Assert children are gone
    assert isolated_db.get(UserToken, token_id) is None
    assert isolated_db.get(MfaRecoveryCode, recovery_code_id) is None
    assert isolated_db.get(AuthSession, session_id) is None
