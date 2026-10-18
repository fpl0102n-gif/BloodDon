"""Database constraint and schema tests for Donor model.

Implements P2.3 per docs/02-database.md §5.9, docs/06-business-rules.md §6-§7,
and docs/07-test-cases.md §5.5-§5.6:
- T-DONOR-04-b: Direct insert with invalid blood type rejected by CHECK constraint.
- T-DONOR-07-c: Direct insert with status='attended' and donated_at filled rejected by CHECK.
- T-CONSENT-02-a (DB): Truth-table for phone vs. consent combinations.
- T-CONSENT-03-a (DB): public_phone=True without phone or blood_type rejected by CHECK.
- T-CSITE-06-a: Composite FK rejects donors for non-participating campaign sites.
- Composite FK ondelete="RESTRICT" prevents deleting CampaignSite with donors.
- registered_by FK ondelete="SET NULL" cleans up user reference on user deletion.
"""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Table, inspect
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.user import User

# ==============================================================================
# 1. Structural Schema and Metadata Tests (docs/02-database.md §5.9)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.db
def test_donor_model_metadata_and_columns() -> None:
    """Verify Donor table metadata, column types, and constraints."""
    table = Donor.__table__
    assert isinstance(table, Table)
    assert table.name == "donors"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "campaign_id",
        "site_id",
        "first_name",
        "last_name",
        "phone",
        "blood_type",
        "status",
        "consent_keep_phone",
        "consent_hero_name",
        "consent_photo",
        "consent_public_phone",
        "registered_by",
        "donated_at",
        "anonymized_at",
        "created_at",
        "updated_at",
    }
    assert expected_cols.issubset(col_names)

    # Primary key checks
    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)

    # Nullability checks
    assert table.c.campaign_id.nullable is False
    assert table.c.site_id.nullable is False
    assert table.c.first_name.nullable is False
    assert table.c.last_name.nullable is True
    assert table.c.phone.nullable is True
    assert table.c.blood_type.nullable is True
    assert table.c.status.nullable is False
    assert table.c.consent_keep_phone.nullable is False
    assert table.c.consent_hero_name.nullable is False
    assert table.c.consent_photo.nullable is False
    assert table.c.consent_public_phone.nullable is False
    assert table.c.registered_by.nullable is True
    assert table.c.donated_at.nullable is True
    assert table.c.anonymized_at.nullable is True


@pytest.mark.unit
@pytest.mark.db
def test_donor_foreign_keys_and_constraints() -> None:
    """Verify Donor foreign keys and CHECK constraint declarations."""
    table = Donor.__table__
    assert isinstance(table, Table)

    # Check composite FK to campaign_sites
    fk_constraints = list(table.foreign_key_constraints)
    composite_fk = next(
        (
            fk
            for fk in fk_constraints
            if {col.name for col in fk.columns} == {"campaign_id", "site_id"}
        ),
        None,
    )
    assert composite_fk is not None
    assert composite_fk.ondelete == "RESTRICT"
    assert {elem.column.table.name for elem in composite_fk.elements} == {"campaign_sites"}

    # Check registered_by FK to users
    user_fk = next(
        (fk for fk in fk_constraints if {col.name for col in fk.columns} == {"registered_by"}),
        None,
    )
    assert user_fk is not None
    assert user_fk.ondelete == "SET NULL"

    # Check constraints
    ck_names = {ck.name for ck in table.constraints if hasattr(ck, "name") and ck.name}
    assert "ck_donors_first_name_len" in ck_names or any(
        "first_name_len" in str(c) for c in ck_names
    )
    assert "ck_donors_last_name_len" in ck_names or any("last_name_len" in str(c) for c in ck_names)
    assert "ck_donors_phone_format" in ck_names or any("phone_format" in str(c) for c in ck_names)
    assert "ck_donors_blood_type" in ck_names or any("blood_type" in str(c) for c in ck_names)
    assert "ck_donors_status" in ck_names or any("status" in str(c) for c in ck_names)
    assert "ck_donors_phone_needs_consent" in ck_names or any(
        "phone_needs_consent" in str(c) for c in ck_names
    )
    assert "ck_donors_public_phone_needs_data" in ck_names or any(
        "public_phone_needs_data" in str(c) for c in ck_names
    )
    assert "ck_donors_donated_at" in ck_names or any("donated_at" in str(c) for c in ck_names)


@pytest.mark.unit
@pytest.mark.db
def test_donor_indexes_declared() -> None:
    """Verify Donor index declarations for campaign_site_status and emergency list."""
    table = Donor.__table__
    assert isinstance(table, Table)

    index_names = {idx.name for idx in table.indexes}
    assert "ix_donors_campaign_site_status" in index_names
    assert "ix_donors_emergency" in index_names

    emergency_idx = next(idx for idx in table.indexes if idx.name == "ix_donors_emergency")
    assert emergency_idx.dialect_options["postgresql"]["where"] is not None


@pytest.mark.unit
@pytest.mark.db
def test_donor_relationships_configured() -> None:
    """Verify ORM relationships on Donor entity."""
    mapper = inspect(Donor)
    relationships = {rel.key: rel for rel in mapper.relationships}
    assert "campaign_site" in relationships
    assert "registered_by_user" in relationships


# ==============================================================================
# 2. Database Constraint and Integrity Tests (docs/07-test-cases.md §5.5-§5.6)
# ==============================================================================


def _setup_context(
    db: Session,
) -> tuple[Campaign, Site, CampaignSite, User]:
    """Helper to create campaign, site, campaign_site, and supervisor user."""
    suffix = uuid.uuid4().hex[:8]
    campaign = Campaign(
        slug=f"donor-camp-{suffix}",
        title=f"Donor Campaign {suffix}",
        campaign_date=date(2026, 11, 20),
        status="live",
    )
    site = Site(
        name=f"Donor Site {suffix}",
        address="Central Clinic",
        is_active=True,
    )
    db.add_all([campaign, site])
    db.flush()

    csite = CampaignSite(
        campaign_id=campaign.id,
        site_id=site.id,
    )
    supervisor = User(
        email=f"sup.{suffix}@domain.com",
        full_name="Supervisor User",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )
    db.add_all([csite, supervisor])
    db.flush()
    return campaign, site, csite, supervisor


@pytest.mark.db
@pytest.mark.br("BR-DONOR-03")
def test_donor_valid_insert_default_status(isolated_db: Session) -> None:
    """Verify that a valid donor defaults to attended status with client-generated UUID."""
    campaign, site, _, supervisor = _setup_context(isolated_db)
    client_id = uuid.uuid4()

    donor = Donor(
        id=client_id,
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Karim",
        last_name="Bouzid",
        registered_by=supervisor.id,
    )
    isolated_db.add(donor)
    isolated_db.flush()

    assert donor.id == client_id
    assert donor.status == "attended"
    assert donor.consent_keep_phone is False
    assert donor.consent_hero_name is False
    assert donor.consent_photo is False
    assert donor.consent_public_phone is False
    assert donor.donated_at is None
    assert donor.created_at is not None


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-04")
def test_t_donor_04_b_invalid_blood_type_rejected(isolated_db: Session) -> None:
    """T-DONOR-04-b: Direct insert with invalid blood type (e.g. 'C+') rejected by CHECK constraint."""
    campaign, site, _, _ = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Invalid Blood",
        blood_type="C+",
    )
    isolated_db.add(donor)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert (
        "ck_donors_blood_type" in err_msg
        or "blood_type" in err_msg
        or "check constraint" in err_msg
    )


@pytest.mark.db
@pytest.mark.parametrize(
    "valid_blood",
    ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", None],
)
@pytest.mark.br("BR-DONOR-04")
def test_donor_valid_blood_types_accepted(isolated_db: Session, valid_blood: str | None) -> None:
    """Verify that all eight standard blood types and None are accepted."""
    campaign, site, _, _ = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name=f"Blood {valid_blood}",
        blood_type=valid_blood,
    )
    isolated_db.add(donor)
    isolated_db.flush()
    assert donor.blood_type == valid_blood


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-DONOR-07")
def test_t_donor_07_c_attended_with_donated_at_rejected(isolated_db: Session) -> None:
    """T-DONOR-07-c: Direct insert with status='attended' and donated_at filled is rejected by CHECK."""
    campaign, site, _, _ = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Premature Timestamp",
        status="attended",
        donated_at=datetime.now(UTC),
    )
    isolated_db.add(donor)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert (
        "ck_donors_donated_at" in err_msg
        or "donated_at" in err_msg
        or "check constraint" in err_msg
    )


@pytest.mark.db
@pytest.mark.br("BR-DONOR-07")
def test_donor_donated_with_timestamp_accepted(isolated_db: Session) -> None:
    """Verify that status='donated' with donated_at filled is accepted."""
    campaign, site, _, _ = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Donated Hero",
        status="donated",
        donated_at=datetime.now(UTC),
    )
    isolated_db.add(donor)
    isolated_db.flush()
    assert donor.status == "donated"
    assert donor.donated_at is not None


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.parametrize(
    ("keep", "public", "phone", "blood", "should_succeed"),
    [
        (False, False, None, None, True),  # Case 1: (✗, ✗, null) -> Accepted
        (
            False,
            False,
            "+213550123456",
            None,
            False,
        ),  # Case 2: (✗, ✗, phone) -> Rejected (phone_needs_consent)
        (True, False, "+213550123456", None, True),  # Case 3: (✓, ✗, phone) -> Accepted
        (True, False, None, None, True),  # Case 4: (✓, ✗, null) -> Accepted
        (False, True, "+213550123456", "O+", True),  # Case 5: (✗, ✓, phone + blood) -> Accepted
        (True, True, "+213550123456", "O+", True),  # Case 6: (✓, ✓, phone + blood) -> Accepted
    ],
)
@pytest.mark.br("BR-CONSENT-02")
def test_t_consent_02_a_phone_consent_truth_table_at_db_level(
    isolated_db: Session,
    keep: bool,
    public: bool,
    phone: str | None,
    blood: str | None,
    should_succeed: bool,
) -> None:
    """T-CONSENT-02-a (DB): Truth-table for phone vs. consent combinations per docs/06-business-rules.md §7.2."""
    campaign, site, _, _ = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name=f"Consent Test k={keep} p={public}",
        phone=phone,
        blood_type=blood,
        consent_keep_phone=keep,
        consent_public_phone=public,
    )
    isolated_db.add(donor)
    if should_succeed:
        isolated_db.flush()
        assert donor.id is not None
    else:
        with pytest.raises(IntegrityError) as exc_info:
            isolated_db.flush()
        err_msg = str(exc_info.value).lower()
        assert "phone_needs_consent" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.parametrize(
    ("phone", "blood"),
    [
        (None, "O+"),  # public=True without phone
        ("+213550123456", None),  # public=True without blood_type
        (None, None),  # public=True without either
    ],
)
@pytest.mark.br("BR-CONSENT-03")
def test_t_consent_03_a_public_phone_requires_data_at_db_level(
    isolated_db: Session,
    phone: str | None,
    blood: str | None,
) -> None:
    """T-CONSENT-03-a (DB): public_phone=True without phone or blood_type rejected by CHECK."""
    campaign, site, _, _ = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Public Phone Deficient",
        phone=phone,
        blood_type=blood,
        consent_public_phone=True,
        consent_keep_phone=bool(phone),
    )
    isolated_db.add(donor)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert (
        "public_phone_needs_data" in err_msg
        or "phone_needs_consent" in err_msg
        or "check constraint" in err_msg
    )


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-CSITE-06")
def test_t_csite_06_a_donor_composite_fk_rejection(isolated_db: Session) -> None:
    """T-CSITE-06-a: Inserting donor for site not participating in campaign fails composite FK."""
    campaign1, _, _, _ = _setup_context(isolated_db)
    _, site2, _, _ = _setup_context(isolated_db)
    # site2 does not participate in campaign1

    invalid_donor = Donor(
        campaign_id=campaign1.id,
        site_id=site2.id,
        first_name="Alien Site Donor",
    )
    isolated_db.add(invalid_donor)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert (
        "fk_donors_campaign_site" in err_msg
        or "foreign key constraint" in err_msg
        or "campaign_sites" in err_msg
    )


@pytest.mark.db
@pytest.mark.br("BR-DONOR-08")
@pytest.mark.parametrize("invalid_status", ["rejected", "deferred", "cancelled"])
def test_donor_status_constraint_rejects_medical_rejection_states(
    isolated_db: Session,
    invalid_status: str,
) -> None:
    """BR-DONOR-08: No 'rejected' or 'deferred' status exists; only 'attended' or 'donated' allowed."""
    campaign, site, _, _ = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Test Donor",
        status=invalid_status,
    )
    isolated_db.add(donor)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "ck_donors_status" in err_msg or "status" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.br("BR-CSITE-04")
def test_campaign_site_deletion_restricted_when_donors_exist(
    isolated_db: Session,
) -> None:
    """BR-CSITE-04: Composite FK has ondelete='RESTRICT' preventing deletion of CampaignSite with donors."""
    campaign, site, csite, _ = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Site Donor",
    )
    isolated_db.add(donor)
    isolated_db.flush()

    isolated_db.delete(csite)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert (
        "foreign key" in err_msg
        or "campaign_sites" in err_msg
        or "fk_donors_campaign_site" in err_msg
    )


@pytest.mark.db
@pytest.mark.br("BR-DONOR-02")
def test_donor_registered_by_user_deletion_sets_null(isolated_db: Session) -> None:
    """Verify that deleting the supervisor user sets donor.registered_by to NULL."""
    campaign, site, _, supervisor = _setup_context(isolated_db)

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Registered Donor",
        registered_by=supervisor.id,
    )
    isolated_db.add(donor)
    isolated_db.flush()
    donor_id = donor.id

    isolated_db.delete(supervisor)
    isolated_db.flush()
    isolated_db.expire_all()

    reloaded = isolated_db.get(Donor, donor_id)
    assert reloaded is not None
    assert reloaded.registered_by is None


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-COUNT-01")
@pytest.mark.br("BR-COUNT-02")
def test_donor_repository_counter_invariants_db(isolated_db: Session) -> None:
    """T-COUNT-02-a (DB): Verify counter queries on PostgreSQL satisfy 0 <= heroes <= donated <= present."""
    from app.repositories.donors import DonorRepository

    campaign, site, _, _ = _setup_context(isolated_db)
    repo = DonorRepository(isolated_db)

    # 1. Initially empty
    present, donated, heroes = repo.count_site_counters(campaign.id, site.id)
    assert (present, donated, heroes) == (0, 0, 0)

    # 2. Add attended donor with hero_consent=True
    # BR-COUNT-01: An attended donor with hero_consent=True MUST NOT be counted in heroes!
    d1 = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="متبرع 1",
        status="attended",
        consent_hero_name=True,
    )
    repo.create(d1)

    present, donated, heroes = repo.count_site_counters(campaign.id, site.id)
    assert present == 1
    assert donated == 0
    assert heroes == 0
    assert 0 <= heroes <= donated <= present

    # 3. Add attended donor without hero consent
    d2 = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="متبرع 2",
        status="attended",
        consent_hero_name=False,
    )
    repo.create(d2)

    present, donated, heroes = repo.count_site_counters(campaign.id, site.id)
    assert present == 2
    assert donated == 0
    assert heroes == 0
    assert 0 <= heroes <= donated <= present

    # 4. d1 donates -> status='donated', hero_consent=True -> donated=1, heroes=1
    d1.status = "donated"
    d1.donated_at = datetime.now(UTC)
    repo.update(d1)

    present, donated, heroes = repo.count_site_counters(campaign.id, site.id)
    assert present == 2
    assert donated == 1
    assert heroes == 1
    assert 0 <= heroes <= donated <= present

    # 5. d2 donates -> status='donated', hero_consent=False -> donated=2, heroes=1
    d2.status = "donated"
    d2.donated_at = datetime.now(UTC)
    repo.update(d2)

    present, donated, heroes = repo.count_site_counters(campaign.id, site.id)
    assert present == 2
    assert donated == 2
    assert heroes == 1
    assert 0 <= heroes <= donated <= present

    # Campaign-level query matches site aggregations
    camp_present, camp_donated, camp_heroes = repo.count_campaign_counters(campaign.id)
    assert (camp_present, camp_donated, camp_heroes) == (2, 2, 1)

    # 6. Delete d1 -> present=1, donated=1, heroes=0
    repo.delete_donor(d1)

    present, donated, heroes = repo.count_site_counters(campaign.id, site.id)
    assert present == 1
    assert donated == 1
    assert heroes == 0
    assert 0 <= heroes <= donated <= present
