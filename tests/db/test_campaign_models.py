"""Database constraint and schema tests for Campaign, Site, and CampaignSite models.

Implements P2.1 per docs/02-database.md §3, §4, §5.1, §5.2, §5.3,
docs/06-business-rules.md §3 & §4, and docs/07-test-cases.md:
- T-CAMP-02-c: CheckConstraint rejection on invalid slug format (BR-CAMP-02).
- T-CAMP-04-a: Partial unique index rejection on multiple current campaigns (BR-CAMP-04).
- T-CSITE-01-a: Composite PK rejection on duplicate campaign-site participation (BR-CSITE-01).
- T-CSITE-03-a: CheckConstraint rejection when end_time <= start_time (BR-CSITE-03).
"""

from datetime import date, time

import pytest
from sqlalchemy import Table, inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.campaign import Campaign, CampaignSite, Site

# ==============================================================================
# 1. Structural Schema and Metadata Tests (docs/02-database.md)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.db
def test_campaign_model_metadata_and_columns() -> None:
    """Verify Campaign table metadata, column types, and constraints."""
    table = Campaign.__table__
    assert isinstance(table, Table)
    assert table.name == "campaigns"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "slug",
        "title",
        "summary",
        "content",
        "cover_url",
        "campaign_date",
        "status",
        "is_published",
        "is_current",
        "final_stats",
        "ended_at",
        "personal_data_purged_at",
        "created_at",
        "updated_at",
    }
    assert expected_cols.issubset(col_names)

    # Column type and nullability assertions
    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)
    assert table.c.slug.nullable is False
    assert table.c.slug.unique is True
    assert table.c.title.nullable is False
    assert table.c.campaign_date.nullable is False
    assert table.c.status.nullable is False
    assert table.c.is_published.nullable is False
    assert table.c.is_current.nullable is False
    assert isinstance(table.c.final_stats.type, JSONB)

    # Check constraints
    ck_names = {ck.name for ck in table.constraints if hasattr(ck, "name")}
    assert "ck_campaigns_slug_format" in ck_names
    assert "ck_campaigns_status" in ck_names

    # Partial unique index
    indexes = [idx for idx in table.indexes if idx.name == "uq_campaigns_single_current"]
    assert len(indexes) == 1
    idx = indexes[0]
    assert idx.unique is True
    assert "is_current" in [col.name for col in idx.columns]
    assert idx.dialect_options["postgresql"]["where"] is not None


@pytest.mark.unit
@pytest.mark.db
def test_site_model_metadata_and_columns() -> None:
    """Verify Site table metadata, column types, and constraints."""
    table = Site.__table__
    assert isinstance(table, Table)
    assert table.name == "sites"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "name",
        "logo_key",
        "address",
        "map_url",
        "latitude",
        "longitude",
        "description",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert expected_cols.issubset(col_names)

    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)
    assert table.c.name.nullable is False
    assert table.c.address.nullable is False
    assert table.c.is_active.nullable is False


@pytest.mark.unit
@pytest.mark.db
def test_campaign_site_composite_pk_and_fk_metadata() -> None:
    """Verify CampaignSite composite primary key, foreign keys, and check constraint."""
    table = CampaignSite.__table__
    assert isinstance(table, Table)
    assert table.name == "campaign_sites"

    # Composite primary key: (campaign_id, site_id)
    pk_cols = [col.name for col in table.primary_key]
    assert sorted(pk_cols) == ["campaign_id", "site_id"]

    # Foreign keys
    fk_targets = {(fk.parent.name, fk.target_fullname, fk.ondelete) for fk in table.foreign_keys}
    assert ("campaign_id", "campaigns.id", "CASCADE") in fk_targets
    assert ("site_id", "sites.id", "RESTRICT") in fk_targets

    # Time order check constraint
    ck_names = {ck.name for ck in table.constraints if hasattr(ck, "name")}
    assert "ck_campaign_sites_time_order" in ck_names


@pytest.mark.unit
@pytest.mark.db
def test_orm_relationships_configured() -> None:
    """Verify SQLAlchemy ORM relationship wiring between Campaign, Site, and CampaignSite."""
    camp_mapper = inspect(Campaign)
    site_mapper = inspect(Site)
    csite_mapper = inspect(CampaignSite)

    assert "campaign_sites" in camp_mapper.relationships
    assert "campaign_sites" in site_mapper.relationships
    assert "campaign" in csite_mapper.relationships
    assert "site" in csite_mapper.relationships


# ==============================================================================
# 2. Database Constraint Rejection Tests (docs/07-test-cases.md)
# ==============================================================================


@pytest.fixture
def isolated_db(db_session: Session) -> Session:
    """Ensure database schema is created inside the active test transaction."""
    Base.metadata.create_all(bind=db_session.connection())
    return db_session


@pytest.mark.db
@pytest.mark.br("BR-CAMP-02")
@pytest.mark.parametrize(
    "invalid_slug",
    [
        "Abc",  # Uppercase not allowed
        "a b",  # Spaces not allowed
        "عربي",  # Non-ASCII not allowed
        "a--b",  # Consecutive hyphens not allowed
        "-a",  # Leading hyphen not allowed
        "a-",  # Trailing hyphen not allowed
        "a_b",  # Underscores not allowed
    ],
)
def test_t_camp_02_c_slug_check_constraint_rejects_invalid(
    isolated_db: Session, invalid_slug: str
) -> None:
    """T-CAMP-02-c: Direct insertion of invalid slug is rejected by check constraint."""
    campaign = Campaign(
        slug=invalid_slug,
        title="Invalid Slug Campaign",
        campaign_date=date(2026, 11, 1),
    )
    isolated_db.add(campaign)

    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    assert (
        "ck_campaigns_slug_format" in str(exc_info.value)
        or "check constraint" in str(exc_info.value).lower()
    )


@pytest.mark.db
@pytest.mark.br("BR-CAMP-04")
def test_t_camp_04_a_single_current_campaign_partial_unique_index(
    isolated_db: Session,
) -> None:
    """T-CAMP-04-a: Partial unique index rejects multiple campaigns with is_current=True."""
    # First current campaign succeeds
    c1 = Campaign(
        slug="current-campaign-one",
        title="Current Campaign 1",
        campaign_date=date(2026, 11, 1),
        is_current=True,
        is_published=True,
    )
    isolated_db.add(c1)
    isolated_db.flush()

    # Second campaign with is_current=True must be rejected by partial unique index
    c2 = Campaign(
        slug="current-campaign-two",
        title="Current Campaign 2",
        campaign_date=date(2026, 11, 15),
        is_current=True,
        is_published=True,
    )
    isolated_db.add(c2)

    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    assert (
        "uq_campaigns_single_current" in str(exc_info.value)
        or "unique constraint" in str(exc_info.value).lower()
    )


@pytest.mark.db
@pytest.mark.br("BR-CAMP-04")
def test_multiple_non_current_campaigns_allowed(isolated_db: Session) -> None:
    """Verify that multiple campaigns with is_current=False are permitted."""
    c1 = Campaign(
        slug="past-campaign-one",
        title="Past Campaign 1",
        campaign_date=date(2026, 9, 1),
        is_current=False,
    )
    c2 = Campaign(
        slug="past-campaign-two",
        title="Past Campaign 2",
        campaign_date=date(2026, 10, 1),
        is_current=False,
    )
    isolated_db.add_all([c1, c2])
    isolated_db.flush()

    assert c1.id is not None
    assert c2.id is not None


@pytest.mark.db
@pytest.mark.br("BR-CSITE-01")
def test_t_csite_01_a_composite_pk_rejects_duplicate_participation(
    isolated_db: Session,
) -> None:
    """T-CSITE-01-a: Rejects inserting a duplicate (campaign_id, site_id) participation."""
    campaign = Campaign(
        slug="autumn-drive-2026",
        title="Autumn Drive",
        campaign_date=date(2026, 10, 25),
    )
    site = Site(
        name="Medical Club Center",
        address="University Hospital, Algiers",
    )
    isolated_db.add_all([campaign, site])
    isolated_db.flush()

    # First participation record succeeds
    cs1 = CampaignSite(
        campaign_id=campaign.id,
        site_id=site.id,
        start_time=time(9, 0),
        end_time=time(17, 0),
    )
    isolated_db.add(cs1)
    isolated_db.flush()

    # Duplicate participation record with identical PK must be rejected
    cs2 = CampaignSite(
        campaign_id=campaign.id,
        site_id=site.id,
        start_time=time(10, 0),
        end_time=time(18, 0),
    )
    isolated_db.add(cs2)

    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    assert (
        "pk_campaign_sites" in str(exc_info.value)
        or "unique constraint" in str(exc_info.value).lower()
        or "duplicate key" in str(exc_info.value).lower()
    )


@pytest.mark.db
@pytest.mark.br("BR-CSITE-03")
@pytest.mark.parametrize(
    ("start_t", "end_t"),
    [
        (time(14, 0), time(8, 0)),  # end before start
        (time(10, 0), time(10, 0)),  # equal start and end
    ],
)
def test_t_csite_03_a_time_order_check_constraint_rejects_invalid(
    isolated_db: Session, start_t: time, end_t: time
) -> None:
    """T-CSITE-03-a: Rejects saving when end_time <= start_time."""
    campaign = Campaign(
        slug="winter-drive-2026",
        title="Winter Drive",
        campaign_date=date(2026, 12, 1),
    )
    site = Site(
        name="Central Site",
        address="Main Plaza, Oran",
    )
    isolated_db.add_all([campaign, site])
    isolated_db.flush()

    cs = CampaignSite(
        campaign_id=campaign.id,
        site_id=site.id,
        start_time=start_t,
        end_time=end_t,
    )
    isolated_db.add(cs)

    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    assert (
        "ck_campaign_sites_time_order" in str(exc_info.value)
        or "check constraint" in str(exc_info.value).lower()
    )


@pytest.mark.db
@pytest.mark.br("BR-CSITE-03")
def test_csite_valid_times_and_nullable_times_allowed(
    isolated_db: Session,
) -> None:
    """Verify that end_time > start_time and NULL time values are permitted."""
    campaign = Campaign(
        slug="spring-drive-2026",
        title="Spring Drive",
        campaign_date=date(2026, 4, 1),
    )
    site1 = Site(name="Site Alpha", address="Alpha Street")
    site2 = Site(name="Site Beta", address="Beta Street")
    isolated_db.add_all([campaign, site1, site2])
    isolated_db.flush()

    # Valid time range
    cs1 = CampaignSite(
        campaign_id=campaign.id,
        site_id=site1.id,
        start_time=time(8, 30),
        end_time=time(16, 30),
    )
    # Nullable time values
    cs2 = CampaignSite(
        campaign_id=campaign.id,
        site_id=site2.id,
        start_time=None,
        end_time=None,
    )
    isolated_db.add_all([cs1, cs2])
    isolated_db.flush()

    assert cs1.campaign_id == campaign.id
    assert cs2.campaign_id == campaign.id
