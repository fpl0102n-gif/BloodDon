"""Database constraint and schema tests for Photo and FileDeletionQueue models.

Implements P2.4 per docs/02-database.md §5.10 & §5.14, docs/06-business-rules.md §8,
and docs/07-test-cases.md §5.7:
- T-PHOTO-02-a: CheckConstraint rejects individual photo without donor_id and general photo with donor_id.
- T-CSITE-06-a: Composite FK rejects photos for non-participating campaign sites.
- CheckConstraint on kind ('general', 'individual').
- CheckConstraint on mime_type ('image/jpeg', 'image/png', 'image/webp').
- CheckConstraint on size_bytes (> 0).
- CheckConstraint on caption length (<= 200 characters).
- Foreign key cascade: deleting Donor cascades to individual photo.
- Foreign key set null: deleting uploader sets uploaded_by to NULL.
- Foreign key restrict: deleting CampaignSite with photos is RESTRICTED.
- FileDeletionQueue staging queue functionality.
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
from app.models.photo import FileDeletionQueue, Photo
from app.models.user import User

# ==============================================================================
# 1. Structural Schema and Metadata Tests (docs/02-database.md §5.10 & §5.14)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.db
def test_photo_model_metadata_and_columns() -> None:
    """Verify Photo table metadata, column types, and constraints."""
    table = Photo.__table__
    assert isinstance(table, Table)
    assert table.name == "photos"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "campaign_id",
        "site_id",
        "storage_key",
        "kind",
        "caption",
        "donor_id",
        "mime_type",
        "size_bytes",
        "uploaded_by",
        "created_at",
    }
    assert expected_cols.issubset(col_names)

    # Primary key checks
    assert table.c.id.primary_key is True
    assert isinstance(table.c.id.type, UUID)

    # Nullability checks
    assert table.c.campaign_id.nullable is False
    assert table.c.site_id.nullable is False
    assert table.c.storage_key.nullable is False
    assert table.c.kind.nullable is False
    assert table.c.caption.nullable is True
    assert table.c.donor_id.nullable is True
    assert table.c.mime_type.nullable is False
    assert table.c.size_bytes.nullable is False
    assert table.c.uploaded_by.nullable is True
    assert table.c.created_at.nullable is False


@pytest.mark.unit
@pytest.mark.db
def test_file_deletion_queue_model_metadata_and_columns() -> None:
    """Verify FileDeletionQueue table metadata, column types, and index."""
    table = FileDeletionQueue.__table__
    assert isinstance(table, Table)
    assert table.name == "file_deletion_queue"

    col_names = {col.name for col in table.columns}
    expected_cols = {
        "id",
        "storage_key",
        "created_at",
        "deleted_at",
    }
    assert expected_cols.issubset(col_names)
    assert table.c.id.primary_key is True
    assert table.c.storage_key.nullable is False
    assert table.c.created_at.nullable is False
    assert table.c.deleted_at.nullable is True

    index_names = {idx.name for idx in table.indexes}
    assert "ix_file_deletion_queue_deleted_at" in index_names


@pytest.mark.unit
@pytest.mark.db
def test_photo_foreign_keys_and_constraints() -> None:
    """Verify Photo foreign key constraints and CHECK constraints."""
    table = Photo.__table__
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

    # Check donor_id FK
    donor_fk = next(
        (fk for fk in fk_constraints if {col.name for col in fk.columns} == {"donor_id"}),
        None,
    )
    assert donor_fk is not None
    assert donor_fk.ondelete == "CASCADE"

    # Check uploaded_by FK
    user_fk = next(
        (fk for fk in fk_constraints if {col.name for col in fk.columns} == {"uploaded_by"}),
        None,
    )
    assert user_fk is not None
    assert user_fk.ondelete == "SET NULL"

    # Check constraints
    ck_names = {ck.name for ck in table.constraints if hasattr(ck, "name") and ck.name}
    assert "ck_photos_kind" in ck_names or any("kind" in str(c) for c in ck_names)
    assert "ck_photos_individual_donor" in ck_names or any(
        "individual_donor" in str(c) for c in ck_names
    )
    assert "ck_photos_mime_type" in ck_names or any("mime_type" in str(c) for c in ck_names)
    assert "ck_photos_size_bytes" in ck_names or any("size_bytes" in str(c) for c in ck_names)
    assert "ck_photos_caption_len" in ck_names or any("caption_len" in str(c) for c in ck_names)


@pytest.mark.unit
@pytest.mark.db
def test_photo_indexes_and_relationships() -> None:
    """Verify Photo indexes and ORM relationships."""
    table = Photo.__table__
    assert isinstance(table, Table)

    index_names = {idx.name for idx in table.indexes}
    assert "ix_photos_campaign_site" in index_names
    assert "ix_photos_donor_id" in index_names

    mapper = inspect(Photo)
    relationships = {rel.key: rel for rel in mapper.relationships}
    assert "campaign_site" in relationships
    assert "donor" in relationships
    assert "uploader" in relationships


# ==============================================================================
# 2. Database Constraint and Integrity Tests (docs/07-test-cases.md §5.7)
# ==============================================================================


def _setup_photo_context(
    db: Session,
) -> tuple[Campaign, Site, CampaignSite, User, Donor]:
    """Helper to create campaign, site, campaign_site, supervisor, and donor."""
    suffix = uuid.uuid4().hex[:8]
    campaign = Campaign(
        slug=f"photo-camp-{suffix}",
        title=f"Photo Campaign {suffix}",
        campaign_date=date(2026, 11, 20),
        status="live",
    )
    site = Site(
        name=f"Photo Site {suffix}",
        address="West Campus Blood Center",
        is_active=True,
    )
    db.add_all([campaign, site])
    db.flush()

    csite = CampaignSite(
        campaign_id=campaign.id,
        site_id=site.id,
    )
    supervisor = User(
        email=f"photo.sup.{suffix}@domain.com",
        full_name="Photo Supervisor",
        role="supervisor",
        site_id=site.id,
        is_active=True,
    )
    db.add_all([csite, supervisor])
    db.flush()

    donor = Donor(
        campaign_id=campaign.id,
        site_id=site.id,
        first_name="Photogenic Donor",
        consent_photo=True,
        registered_by=supervisor.id,
    )
    db.add(donor)
    db.flush()
    return campaign, site, csite, supervisor, donor


@pytest.mark.db
@pytest.mark.br("BR-PHOTO-01")
def test_photo_valid_general_and_individual_inserts(isolated_db: Session) -> None:
    """Verify inserting valid general and individual photos."""
    campaign, site, _, supervisor, donor = _setup_photo_context(isolated_db)

    # General photo
    general = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.jpg",
        kind="general",
        caption="Team atmosphere",
        donor_id=None,
        mime_type="image/jpeg",
        size_bytes=1024 * 500,
        uploaded_by=supervisor.id,
    )
    # Individual photo
    individual = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.webp",
        kind="individual",
        caption="Donor at station",
        donor_id=donor.id,
        mime_type="image/webp",
        size_bytes=1024 * 300,
        uploaded_by=supervisor.id,
    )
    isolated_db.add_all([general, individual])
    isolated_db.flush()

    assert general.id is not None
    assert individual.id is not None
    assert general.donor_id is None
    assert individual.donor_id == donor.id


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-02")
def test_t_photo_02_a_individual_without_donor_rejected(isolated_db: Session) -> None:
    """T-PHOTO-02-a: Individual photo without donor_id is rejected by CHECK constraint."""
    campaign, site, _, supervisor, _ = _setup_photo_context(isolated_db)

    invalid_photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.jpg",
        kind="individual",
        donor_id=None,  # Missing donor_id for individual photo
        mime_type="image/jpeg",
        size_bytes=1024,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(invalid_photo)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "individual_donor" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-02")
def test_t_photo_02_a_general_with_donor_rejected(isolated_db: Session) -> None:
    """T-PHOTO-02-a: General photo carrying a donor_id is rejected by CHECK constraint."""
    campaign, site, _, supervisor, donor = _setup_photo_context(isolated_db)

    invalid_photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.jpg",
        kind="general",
        donor_id=donor.id,  # General photo must not have donor_id
        mime_type="image/jpeg",
        size_bytes=1024,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(invalid_photo)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "individual_donor" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.parametrize(
    "valid_mime",
    ["image/jpeg", "image/png", "image/webp"],
)
@pytest.mark.br("BR-PHOTO-05")
def test_photo_valid_mime_types_accepted(isolated_db: Session, valid_mime: str) -> None:
    """BR-PHOTO-05: Only JPEG, PNG, and WebP are allowed."""
    campaign, site, _, supervisor, _ = _setup_photo_context(isolated_db)

    photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}",
        kind="general",
        mime_type=valid_mime,
        size_bytes=2048,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(photo)
    isolated_db.flush()
    assert photo.mime_type == valid_mime


@pytest.mark.db
@pytest.mark.parametrize(
    "invalid_mime",
    ["image/gif", "image/svg+xml", "application/pdf", "text/plain"],
)
@pytest.mark.br("BR-PHOTO-05")
def test_photo_invalid_mime_types_rejected(isolated_db: Session, invalid_mime: str) -> None:
    """BR-PHOTO-05: Forbidden mime types rejected by CHECK constraint."""
    campaign, site, _, supervisor, _ = _setup_photo_context(isolated_db)

    photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}",
        kind="general",
        mime_type=invalid_mime,
        size_bytes=2048,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(photo)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "mime_type" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.parametrize("invalid_size", [0, -1, -500])
@pytest.mark.br("BR-PHOTO-06")
def test_photo_size_bytes_must_be_positive(isolated_db: Session, invalid_size: int) -> None:
    """BR-PHOTO-06: size_bytes must be strictly positive."""
    campaign, site, _, supervisor, _ = _setup_photo_context(isolated_db)

    photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.jpg",
        kind="general",
        mime_type="image/jpeg",
        size_bytes=invalid_size,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(photo)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "size_bytes" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.br("BR-PHOTO-12")
def test_photo_caption_length_constraint(isolated_db: Session) -> None:
    """BR-PHOTO-12: caption length must be <= 200 characters."""
    campaign, site, _, supervisor, _ = _setup_photo_context(isolated_db)

    # 201 characters
    long_caption = "x" * 201
    photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.jpg",
        kind="general",
        caption=long_caption,
        mime_type="image/jpeg",
        size_bytes=1024,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(photo)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert "caption_len" in err_msg or "check constraint" in err_msg


@pytest.mark.db
@pytest.mark.critical
@pytest.mark.br("BR-CSITE-06")
def test_t_csite_06_a_photo_composite_fk_rejection(isolated_db: Session) -> None:
    """T-CSITE-06-a: Inserting photo for site not participating in campaign fails composite FK."""
    campaign1, _, _, supervisor, _ = _setup_photo_context(isolated_db)
    _, site2, _, _, _ = _setup_photo_context(isolated_db)
    # site2 does not participate in campaign1

    invalid_photo = Photo(
        campaign_id=campaign1.id,
        site_id=site2.id,
        storage_key=f"photos/{uuid.uuid4().hex}.jpg",
        kind="general",
        mime_type="image/jpeg",
        size_bytes=1024,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(invalid_photo)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert (
        "fk_photos_campaign_site" in err_msg
        or "foreign key constraint" in err_msg
        or "campaign_sites" in err_msg
    )


@pytest.mark.db
@pytest.mark.br("BR-DONOR-11")
def test_photo_cascade_deleted_when_donor_deleted(isolated_db: Session) -> None:
    """BR-DONOR-11: Deleting a donor cascades to delete their individual photo records."""
    campaign, site, _, supervisor, donor = _setup_photo_context(isolated_db)

    photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.webp",
        kind="individual",
        donor_id=donor.id,
        mime_type="image/webp",
        size_bytes=2048,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(photo)
    isolated_db.flush()
    photo_id = photo.id

    isolated_db.delete(donor)
    isolated_db.flush()

    assert isolated_db.get(Photo, photo_id) is None


@pytest.mark.db
@pytest.mark.br("BR-CSITE-04")
def test_campaign_site_deletion_restricted_when_photos_exist(
    isolated_db: Session,
) -> None:
    """BR-CSITE-04: Composite FK has ondelete='RESTRICT' preventing deletion of CampaignSite with photos."""
    campaign, site, csite, supervisor, _ = _setup_photo_context(isolated_db)

    photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.jpg",
        kind="general",
        mime_type="image/jpeg",
        size_bytes=1024,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(photo)
    isolated_db.flush()

    isolated_db.delete(csite)
    with pytest.raises(IntegrityError) as exc_info:
        isolated_db.flush()

    err_msg = str(exc_info.value).lower()
    assert (
        "foreign key" in err_msg
        or "campaign_sites" in err_msg
        or "fk_photos_campaign_site" in err_msg
    )


@pytest.mark.db
def test_photo_uploader_deletion_sets_null(isolated_db: Session) -> None:
    """Verify that deleting the uploader user sets photo.uploaded_by to NULL."""
    campaign, site, _, supervisor, _ = _setup_photo_context(isolated_db)

    photo = Photo(
        campaign_id=campaign.id,
        site_id=site.id,
        storage_key=f"photos/{uuid.uuid4().hex}.jpg",
        kind="general",
        mime_type="image/jpeg",
        size_bytes=1024,
        uploaded_by=supervisor.id,
    )
    isolated_db.add(photo)
    isolated_db.flush()
    photo_id = photo.id

    isolated_db.delete(supervisor)
    isolated_db.flush()

    reloaded = isolated_db.get(Photo, photo_id)
    assert reloaded is not None
    assert reloaded.uploaded_by is None


@pytest.mark.db
def test_file_deletion_queue_crud(isolated_db: Session) -> None:
    """Verify FileDeletionQueue creation, indexing, and soft deletion timestamp."""
    entry = FileDeletionQueue(
        storage_key="photos/to_delete_001.jpg",
    )
    isolated_db.add(entry)
    isolated_db.flush()

    assert entry.id is not None
    assert entry.created_at is not None
    assert entry.deleted_at is None

    # Mark deleted
    entry.deleted_at = datetime.now(UTC)
    isolated_db.flush()

    reloaded = isolated_db.get(FileDeletionQueue, entry.id)
    assert reloaded is not None
    assert reloaded.deleted_at is not None
