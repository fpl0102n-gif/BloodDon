"""Database constraint and schema tests for Pledge model.

Implements P2.3 per docs/02-database.md §5.8, docs/06-business-rules.md §5,
and docs/07-test-cases.md §5.4:
- T-PLEDGE-06-b: Partial unique index rejects duplicate phone per campaign site.
- T-CSITE-06-a: Composite FK rejects pledges for non-participating campaign sites.
- CheckConstraint on first_name length (2 to 40 characters).
- CheckConstraint on phone format.
- Foreign key cascade deletion upon CampaignSite removal.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import Table, inspect
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignSite, Site
from app.models.pledge import Pledge

# ==============================================================================
# 1. Structural Schema and Metadata Tests (docs/02-database.md §5.8)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.db
def test_pledge_model_metadata_and_columns() -> None:
    """Verify Pledge table metadata, column types, and constraints."""
    table = Pledge.__table__
    assert isinstance(table, Table)
    assert table.name == "pledges"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "campaign_id",
        "site_id",
        "first_name",
        "phone",
        "created_at",
    }
    assert expected_cols.issubset(col_names)

    # Check primary key
    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)

    # Check non-nullability
    assert table.c.campaign_id.nullable is False
    assert table.c.site_id.nullable is False
    assert table.c.first_name.nullable is False
    assert table.c.phone.nullable is True
    assert table.c.created_at.nullable is False


@pytest.mark.unit
@pytest.mark.db
def test_pledge_composite_foreign_key_and_constraints() -> None:
    """Verify Pledge composite foreign key to campaign_sites and check constraints."""
    table = Pledge.__table__
    assert isinstance(table, Table)

    # Check composite FK constraint
    fk_constraints = list(table.foreign_key_constraints)
    assert len(fk_constraints) >= 1
    composite_fk = next(
        (
            fk
            for fk in fk_constraints
            if {col.name for col in fk.columns} == {"campaign_id", "site_id"}
        ),
        None,
    )
    assert composite_fk is not None
    assert composite_fk.ondelete == "CASCADE"
    assert {elem.column.table.name for elem in composite_fk.elements} == {"campaign_sites"}

    # Check constraints
    ck_names = {ck.name for ck in table.constraints if hasattr(ck, "name") and ck.name}
    assert "ck_pledges_first_name_len" in ck_names or any(
        "first_name_len" in str(ck.name) for ck in table.constraints
    )
    assert "ck_pledges_phone_format" in ck_names or any(
        "phone_format" in str(ck.name) for ck in table.constraints
    )


@pytest.mark.unit
@pytest.mark.db
def test_pledge_indexes_declared() -> None:
    """Verify partial unique index on phone and campaign_site index."""
    table = Pledge.__table__
    assert isinstance(table, Table)

    index_names = {idx.name for idx in table.indexes}
    assert "uq_pledges_campaign_site_phone" in index_names
    assert "ix_pledges_campaign_site" in index_names

    partial_uq = next(idx for idx in table.indexes if idx.name == "uq_pledges_campaign_site_phone")
    assert partial_uq.unique is True
    assert {col.name for col in partial_uq.columns} == {"campaign_id", "site_id", "phone"}
    assert partial_uq.dialect_options["postgresql"]["where"] is not None


@pytest.mark.unit
@pytest.mark.db
def test_pledge_relationships_configured() -> None:
    """Verify ORM relationships on Pledge entity."""
    mapper = inspect(Pledge)
    relationships = {rel.key: rel for rel in mapper.relationships}
    assert "campaign_site" in relationships


# ==============================================================================
# 2. Database Constraint and Integrity Tests (docs/07-test-cases.md §5.4)
# ==============================================================================


def _setup_campaign_and_site(
    db: Session,
) -> tuple[Campaign, Site, CampaignSite]:
    """Helper to create campaign, site, and campaign_site records."""
    suffix = uuid.uuid4().hex[:8]
    campaign = Campaign(
        slug=f"pledge-test-camp-{suffix}",
        title=f"Pledge Test Campaign {suffix}",
        campaign_date=date(2026, 11, 20),
        status="upcoming",
    )
    site = Site(
        name=f"Pledge Test Site {suffix}",
        address="University Campus Hall B",
        is_active=True,
    )
    db.add_all([campaign, site])
    db.flush()

    csite = CampaignSite(
        campaign_id=campaign.id,
        site_id=site.id,
    )
    db.add(csite)
    db.flush()
    return campaign, site, csite


@pytest.mark.db
@pytest.mark.br("BR-PLEDGE-04")
def test_pledge_valid_insert(isolated_db: Session) -> None:
    """Verify inserting valid pledges with and without phone numbers."""
    campaign, site, _ = _setup_campaign_and_site(isolated_db)

    # Pledge with phone
    p1 = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Ahmed",
        phone="+213550123456",
    )
    # Pledge without phone
    p2 = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Sara",
        phone=None,
    )
    isolated_db.add_all([p1, p2])
    isolated_db.flush()

    assert p1.id is not None
    assert p1.created_at is not None
    assert p2.id is not None
    assert p2.phone is None


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-PLEDGE-06")
def test_t_pledge_06_b_duplicate_phone_rejected_by_partial_unique_index(
    isolated_db: Session,
) -> None:
    """T-PLEDGE-06-b: Same phone in the same campaign and site is rejected by partial unique index."""
    campaign, site, _ = _setup_campaign_and_site(isolated_db)
    phone = "+213550999999"

    p1 = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Original",
        phone=phone,
    )
    isolated_db.add(p1)
    isolated_db.flush()

    p2 = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Duplicate",
        phone=phone,
    )
    isolated_db.add(p2)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "uq_pledges_campaign_site_phone" in err_msg or "unique constraint" in err_msg


@pytest.mark.db
@pytest.mark.br("BR-PLEDGE-06")
def test_pledge_same_phone_different_site_or_campaign_accepted(
    isolated_db: Session,
) -> None:
    """Verify that same phone is permitted across different sites or campaigns."""
    campaign1, site1, _ = _setup_campaign_and_site(isolated_db)
    campaign2, site2, _ = _setup_campaign_and_site(isolated_db)
    phone = "+213550888888"

    p1 = Pledge(
        campaign_id=campaign1.id,
        site_id=site1.id,
        first_name="Donor One",
        phone=phone,
    )
    # Same campaign, different site
    csite1_2 = CampaignSite(campaign_id=campaign1.id, site_id=site2.id)
    isolated_db.add(csite1_2)
    isolated_db.flush()

    p2 = Pledge(
        campaign_id=campaign1.id,
        site_id=site2.id,
        first_name="Donor One at Site 2",
        phone=phone,
    )
    # Different campaign
    p3 = Pledge(
        campaign_id=campaign2.id,
        site_id=site2.id,
        first_name="Donor One at Camp 2",
        phone=phone,
    )
    isolated_db.add_all([p1, p2, p3])
    isolated_db.flush()

    assert p1.id is not None
    assert p2.id is not None
    assert p3.id is not None


@pytest.mark.db
@pytest.mark.br("BR-PLEDGE-07")
def test_pledge_multiple_null_phones_accepted(isolated_db: Session) -> None:
    """BR-PLEDGE-07: Multiple pledges with phone=None for the same site and campaign are accepted."""
    campaign, site, _ = _setup_campaign_and_site(isolated_db)

    p1 = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Anonymous 1",
        phone=None,
    )
    p2 = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Anonymous 2",
        phone=None,
    )
    isolated_db.add_all([p1, p2])
    isolated_db.flush()

    assert p1.id != p2.id


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-CSITE-06")
def test_t_csite_06_a_pledge_composite_fk_rejection(isolated_db: Session) -> None:
    """T-CSITE-06-a: Inserting pledge for site not participating in campaign fails composite FK."""
    campaign1, _, _ = _setup_campaign_and_site(isolated_db)
    _, site2, _ = _setup_campaign_and_site(isolated_db)
    # site2 does not participate in campaign1

    invalid_pledge = Pledge(
        campaign_id=campaign1.id,
        site_id=site2.id,
        first_name="Invalid Site",
        phone=None,
    )
    isolated_db.add(invalid_pledge)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert (
        "fk_pledges_campaign_site" in err_msg
        or "foreign key constraint" in err_msg
        or "campaign_sites" in err_msg
    )


@pytest.mark.db
@pytest.mark.parametrize(
    "invalid_name",
    [
        "A",  # 1 char (< 2)
        "x" * 41,  # 41 chars (> 40)
    ],
)
@pytest.mark.br("BR-PLEDGE-04")
def test_pledge_first_name_length_constraint(isolated_db: Session, invalid_name: str) -> None:
    """Verify rejection of first_name outside 2-40 characters range."""
    campaign, site, _ = _setup_campaign_and_site(isolated_db)

    p = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name=invalid_name,
        phone=None,
    )
    isolated_db.add(p)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "first_name_len" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.parametrize(
    "invalid_phone",
    [
        "123",  # too short
        "abcdefgh",  # non-digits
        "++213550123456",  # double plus
        "05501234567890123",  # too long (> 15 digits)
    ],
)
@pytest.mark.br("BR-GEN-01")
def test_pledge_phone_format_constraint(isolated_db: Session, invalid_phone: str) -> None:
    """Verify rejection of non-conforming phone numbers."""
    campaign, site, _ = _setup_campaign_and_site(isolated_db)

    p = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Test Phone",
        phone=invalid_phone,
    )
    isolated_db.add(p)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "phone_format" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.br("BR-CSITE-04")
def test_pledge_cascade_deleted_with_campaign_site(isolated_db: Session) -> None:
    """Verify that deleting CampaignSite cascades to its pledges."""
    campaign, site, csite = _setup_campaign_and_site(isolated_db)

    p = Pledge(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Cascade Test",
        phone=None,
    )
    isolated_db.add(p)
    isolated_db.flush()
    pledge_id = p.id

    # Delete CampaignSite
    isolated_db.delete(csite)
    isolated_db.flush()

    assert isolated_db.get(Pledge, pledge_id) is None
