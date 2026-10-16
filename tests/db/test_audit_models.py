"""Database schema and constraint tests for AuditLog, PhoneRevealLog, and RateLimitEvent models.

Implements P2.4 per docs/02-database.md §5.12, §5.13, §5.15,
docs/06-business-rules.md §13 (AUDIT), and docs/07-test-cases.md §5.12:
- T-AUDIT-02-a: AuditLog append-only structure, actor relationship, and JSONB details.
- Deleting user sets AuditLog.actor_user_id to NULL (audit preservation).
- PhoneRevealLog tracks emergency reveals with NO foreign key on donor_id (docs/02-database.md §5.13).
- RateLimitEvent tracks window events with composite index (key_hash, action, created_at).
"""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Table, inspect
from sqlalchemy.orm import Session

from app.models.audit import AuditLog, PhoneRevealLog, RateLimitEvent
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.user import User

# ==============================================================================
# 1. Structural Schema and Metadata Tests (docs/02-database.md §5.12, §5.13, §5.15)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.db
def test_audit_log_model_metadata_and_columns() -> None:
    """Verify AuditLog table metadata, column types, and indexes."""
    table = AuditLog.__table__
    assert isinstance(table, Table)
    assert table.name == "audit_log"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "at",
        "actor_user_id",
        "action",
        "entity_type",
        "entity_id",
        "details",
    }
    assert expected_cols.issubset(col_names)

    assert table.c.id.primary_key is True
    assert table.c.at.nullable is False
    assert table.c.actor_user_id.nullable is True
    assert table.c.action.nullable is False
    assert table.c.entity_type.nullable is False
    assert table.c.entity_id.nullable is True
    assert table.c.details.nullable is True

    index_names = {idx.name for idx in table.indexes}
    assert "ix_audit_log_at" in index_names
    assert "ix_audit_log_entity" in index_names
    assert "ix_audit_log_actor" in index_names

    # Check actor FK
    fk_constraints = list(table.foreign_key_constraints)
    user_fk = next(
        (fk for fk in fk_constraints if {col.name for col in fk.columns} == {"actor_user_id"}),
        None,
    )
    assert user_fk is not None
    assert user_fk.ondelete == "SET NULL"


@pytest.mark.unit
@pytest.mark.db
def test_phone_reveal_log_model_metadata_and_no_fk_on_donor() -> None:
    """Verify PhoneRevealLog metadata and ensure donor_id has NO foreign key per §5.13."""
    table = PhoneRevealLog.__table__
    assert isinstance(table, Table)
    assert table.name == "phone_reveal_log"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "ip_hash",
        "donor_id",
        "created_at",
    }
    assert expected_cols.issubset(col_names)

    assert table.c.id.primary_key is True
    assert table.c.ip_hash.nullable is False
    assert table.c.donor_id.nullable is False
    assert table.c.created_at.nullable is False

    # CRITICAL: donor_id must NOT have a foreign key (docs/02-database.md §5.13)
    assert len(list(table.foreign_key_constraints)) == 0

    index_names = {idx.name for idx in table.indexes}
    assert "ix_phone_reveal_log_ip_created_at" in index_names


@pytest.mark.unit
@pytest.mark.db
def test_rate_limit_event_model_metadata_and_indexes() -> None:
    """Verify RateLimitEvent table metadata, columns, and sliding window index."""
    table = RateLimitEvent.__table__
    assert isinstance(table, Table)
    assert table.name == "rate_limit_events"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "key_hash",
        "action",
        "created_at",
    }
    assert expected_cols.issubset(col_names)

    assert table.c.id.primary_key is True
    assert table.c.key_hash.nullable is False
    assert table.c.action.nullable is False
    assert table.c.created_at.nullable is False

    index_names = {idx.name for idx in table.indexes}
    assert "ix_rate_limit_events_window" in index_names


@pytest.mark.unit
@pytest.mark.db
def test_audit_log_relationships() -> None:
    """Verify AuditLog ORM relationships."""
    mapper = inspect(AuditLog)
    relationships = {rel.key: rel for rel in mapper.relationships}
    assert "actor" in relationships


# ==============================================================================
# 2. Database Constraint and Integrity Tests (docs/07-test-cases.md §5.12)
# ==============================================================================


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-AUDIT-01")
def test_t_audit_02_a_audit_log_insert_and_retrieval(isolated_db: Session) -> None:
    """T-AUDIT-02-a: Verify AuditLog entry creation with actor, target entity, and non-PII details."""
    admin = User(
        email=f"audit.admin.{uuid.uuid4().hex[:6]}@bloodday.local",
        full_name="Audit Administrator",
        role="admin",
        is_active=True,
    )
    isolated_db.add(admin)
    isolated_db.flush()

    entity_uuid = uuid.uuid4()
    log_entry = AuditLog(
        actor_user_id=admin.id,
        action="donor.status_change",
        entity_type="donor",
        entity_id=entity_uuid,
        details={
            "old_status": "attended",
            "new_status": "donated",
            "changed_fields": ["status", "donated_at"],
        },
    )
    isolated_db.add(log_entry)
    isolated_db.flush()

    assert log_entry.id is not None
    assert log_entry.at is not None
    assert log_entry.actor_user_id == admin.id
    assert log_entry.details == {
        "old_status": "attended",
        "new_status": "donated",
        "changed_fields": ["status", "donated_at"],
    }


@pytest.mark.db
@pytest.mark.br("BR-AUDIT-01")
def test_audit_log_system_action_without_actor(isolated_db: Session) -> None:
    """Verify system-triggered audit entries with actor_user_id=None."""
    log_entry = AuditLog(
        actor_user_id=None,  # System action
        action="campaign.purge",
        entity_type="campaign",
        entity_id=uuid.uuid4(),
        details={"purged_records": 42},
    )
    isolated_db.add(log_entry)
    isolated_db.flush()

    assert log_entry.id is not None
    assert log_entry.actor_user_id is None


@pytest.mark.db
def test_audit_log_actor_deletion_sets_null(isolated_db: Session) -> None:
    """Verify that deleting the actor user sets AuditLog.actor_user_id to NULL to preserve history."""
    admin = User(
        email=f"temp.admin.{uuid.uuid4().hex[:6]}@bloodday.local",
        full_name="Temporary Admin",
        role="admin",
        is_active=True,
    )
    isolated_db.add(admin)
    isolated_db.flush()
    admin_id = admin.id

    log_entry = AuditLog(
        actor_user_id=admin_id,
        action="user.invite",
        entity_type="user",
        entity_id=uuid.uuid4(),
    )
    isolated_db.add(log_entry)
    isolated_db.flush()
    log_id = log_entry.id

    # Delete actor user
    isolated_db.delete(admin)
    isolated_db.flush()
    isolated_db.expire_all()

    reloaded = isolated_db.get(AuditLog, log_id)
    assert reloaded is not None
    assert reloaded.actor_user_id is None


@pytest.mark.db
@pytest.mark.br("BR-RATE-04")
def test_phone_reveal_log_persists_after_donor_deletion(isolated_db: Session) -> None:
    """BR-RATE-04 & docs/02 §5.13: PhoneRevealLog has no FK, persists even if donor is deleted."""
    suffix = uuid.uuid4().hex[:8]
    campaign = Campaign(
        slug=f"reveal-camp-{suffix}",
        title=f"Reveal Campaign {suffix}",
        campaign_date=date(2026, 11, 20),
        status="live",
    )
    site = Site(
        name=f"Reveal Site {suffix}",
        address="Clinic Hall",
        is_active=True,
    )
    isolated_db.add_all([campaign, site])
    isolated_db.flush()

    csite = CampaignSite(campaign_id=campaign.id, site_id=site.id)
    isolated_db.add(csite)
    isolated_db.flush()

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Reveal Test Donor",
    )
    isolated_db.add(donor)
    isolated_db.flush()
    donor_id = donor.id

    # Create reveal log entry
    reveal = PhoneRevealLog(
        ip_hash=f"salted_hash_{uuid.uuid4().hex}",
        donor_id=donor_id,
    )
    isolated_db.add(reveal)
    isolated_db.flush()
    reveal_id = reveal.id

    # Delete donor
    isolated_db.delete(donor)
    isolated_db.flush()
    isolated_db.expire_all()

    # Reveal log persists because there is intentionally no FK
    reloaded_reveal = isolated_db.get(PhoneRevealLog, reveal_id)
    assert reloaded_reveal is not None
    assert reloaded_reveal.donor_id == donor_id


@pytest.mark.db
def test_rate_limit_event_lifecycle(isolated_db: Session) -> None:
    """Verify RateLimitEvent insertion and retrieval across sliding windows."""
    key_hash = f"ratelimit_key_{uuid.uuid4().hex}"
    event = RateLimitEvent(
        key_hash=key_hash,
        action="login",
        created_at=datetime.now(UTC),
    )
    isolated_db.add(event)
    isolated_db.flush()

    assert event.id is not None
    assert event.action == "login"
    assert event.key_hash == key_hash
