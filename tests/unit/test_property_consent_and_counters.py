"""Property-based tests for Consent Truth Table and Counter Invariants (D-43).

Per:
- docs/11-decisions.md §4.6 (Decision D-43: Hypothesis restricted strictly to COUNT-02 invariants
  and Consent Truth Table)
- docs/06-business-rules.md §7.2 (Consent Truth Table: BR-CONSENT-01..04, BR-CONSENT-06)
- docs/06-business-rules.md §9.3 (Counter Invariants: BR-COUNT-01..02)
- docs/07-test-cases.md §5.8 (T-COUNT-02-a: Property tests for randomized lifecycle sequences)
- docs/07-test-cases.md line 675 (D-43: hypothesis for COUNT-02 and T-CONSENT-02-a)
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, consumes, invariant, rule

from app.core.errors import (
    PhoneRequiresConsentError,
    PublicPhoneRequiresDataError,
)
from app.models.campaign import Campaign, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.user import User
from app.repositories.campaigns import CampaignRepository
from app.repositories.sites import SiteRepository
from app.schemas.common import BloodType
from app.schemas.donor import DonorCreateIn
from app.services.donors import DonorService

# ==============================================================================
# Helper / In-Memory Simulator for Fast Property Verification
# ==============================================================================


@dataclass
class SimulatedDonor:
    """Lightweight in-memory donor representation for high-throughput property testing."""

    id: uuid.UUID
    campaign_id: uuid.UUID
    site_id: uuid.UUID
    first_name: str
    last_name: str | None
    phone: str | None
    blood_type: str | None
    status: Literal["attended", "donated"]
    consent_keep_phone: bool
    consent_hero_name: bool
    consent_photo: bool
    consent_public_phone: bool
    registered_by: uuid.UUID
    donated_at: datetime | None = None
    anonymized_at: datetime | None = None


class InMemoryDonorRepository:
    """In-memory repository replicating DonorRepository query and aggregation logic."""

    def __init__(self) -> None:
        self.donors: dict[uuid.UUID, SimulatedDonor] = {}
        self.photos: dict[uuid.UUID, list[Photo]] = {}
        self.enqueued_deletion_keys: list[str] = []
        self.db = MagicMock()
        self.db.begin_nested.return_value.__enter__.return_value = MagicMock()
        self.db.begin_nested.return_value.__exit__.return_value = None

    def get_by_id(self, donor_id: uuid.UUID) -> SimulatedDonor | None:
        return self.donors.get(donor_id)

    def create(self, donor: SimulatedDonor | Donor) -> SimulatedDonor | Donor:
        if isinstance(donor, Donor):
            sim = SimulatedDonor(
                id=donor.id,
                campaign_id=donor.campaign_id,
                site_id=donor.site_id,
                first_name=donor.first_name,
                last_name=donor.last_name,
                phone=donor.phone,
                blood_type=donor.blood_type,
                status=donor.status,  # type: ignore[arg-type]
                consent_keep_phone=donor.consent_keep_phone,
                consent_hero_name=donor.consent_hero_name,
                consent_photo=donor.consent_photo,
                consent_public_phone=donor.consent_public_phone,
                registered_by=donor.registered_by or uuid.uuid4(),
                donated_at=donor.donated_at,
                anonymized_at=donor.anonymized_at,
            )
            self.donors[sim.id] = sim
            return donor
        self.donors[donor.id] = donor
        return donor

    def update(self, donor: SimulatedDonor | Donor) -> SimulatedDonor | Donor:
        if isinstance(donor, Donor):
            sim = self.donors.get(donor.id)
            if sim:
                sim.first_name = donor.first_name
                sim.last_name = donor.last_name
                sim.phone = donor.phone
                sim.blood_type = donor.blood_type
                sim.status = donor.status  # type: ignore[assignment]
                sim.consent_keep_phone = donor.consent_keep_phone
                sim.consent_hero_name = donor.consent_hero_name
                sim.consent_photo = donor.consent_photo
                sim.consent_public_phone = donor.consent_public_phone
                sim.donated_at = donor.donated_at
                sim.anonymized_at = donor.anonymized_at
            return donor
        self.donors[donor.id] = donor
        return donor

    def delete_donor(self, donor: SimulatedDonor | Donor) -> None:
        self.donors.pop(donor.id, None)

    def count_summary(self, campaign_id: uuid.UUID, site_id: uuid.UUID) -> tuple[int, int]:
        present = sum(
            1 for d in self.donors.values() if d.campaign_id == campaign_id and d.site_id == site_id
        )
        donated = sum(
            1
            for d in self.donors.values()
            if d.campaign_id == campaign_id and d.site_id == site_id and d.status == "donated"
        )
        return present, donated

    def count_site_counters(
        self, campaign_id: uuid.UUID, site_id: uuid.UUID
    ) -> tuple[int, int, int]:
        present = sum(
            1 for d in self.donors.values() if d.campaign_id == campaign_id and d.site_id == site_id
        )
        donated = sum(
            1
            for d in self.donors.values()
            if d.campaign_id == campaign_id and d.site_id == site_id and d.status == "donated"
        )
        heroes = sum(
            1
            for d in self.donors.values()
            if d.campaign_id == campaign_id
            and d.site_id == site_id
            and d.status == "donated"
            and d.consent_hero_name is True
        )
        return present, donated, heroes

    def count_campaign_counters(self, campaign_id: uuid.UUID) -> tuple[int, int, int]:
        present = sum(1 for d in self.donors.values() if d.campaign_id == campaign_id)
        donated = sum(
            1
            for d in self.donors.values()
            if d.campaign_id == campaign_id and d.status == "donated"
        )
        heroes = sum(
            1
            for d in self.donors.values()
            if d.campaign_id == campaign_id
            and d.status == "donated"
            and d.consent_hero_name is True
        )
        return present, donated, heroes

    def get_photos_by_donor(self, donor_id: uuid.UUID) -> Sequence[Photo]:
        return self.photos.get(donor_id, [])

    def delete_photo_and_enqueue(self, photo: Photo) -> None:
        self.enqueued_deletion_keys.append(photo.storage_key)


def evaluate_truth_table_oracle(
    phone: str | None,
    blood_type: BloodType | str | None,
    keep_phone: bool,
    public_phone: bool,
) -> tuple[bool, type[Exception] | None]:
    """Canonical specification oracle for the Consent Truth Table (docs/06 §7.2).

    Rules:
    1. Phone cannot be stored without explicit keep or public consent (BR-CONSENT-02).
       If phone is present and not (keep_phone or public_phone) -> PhoneRequiresConsentError.
    2. Public phone display requires both phone and blood_type (BR-CONSENT-03).
       If public_phone is True and (phone is None or blood_type is None) -> PublicPhoneRequiresDataError.
    """
    if phone is not None and not (keep_phone or public_phone):
        return False, PhoneRequiresConsentError
    if public_phone and not (phone is not None and blood_type is not None):
        return False, PublicPhoneRequiresDataError
    return True, None


# ==============================================================================
# Part A: Consent Truth Table Property Tests (Decision D-43, T-CONSENT-02-a)
# ==============================================================================

# Strategies for arbitrary demographic and consent inputs
valid_phones = st.sampled_from(["+966501234567", "+213550123456", "+12025550199", "0550123456"])
optional_phone = st.one_of(st.none(), valid_phones)
optional_blood = st.one_of(st.none(), st.sampled_from(list(BloodType)))
booleans = st.booleans()


@pytest.mark.unit
@pytest.mark.property
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-02")
@pytest.mark.br("BR-CONSENT-03")
@given(
    phone=optional_phone,
    blood_type=optional_blood,
    keep_phone=booleans,
    public_phone=booleans,
    hero_name=booleans,
    photo=booleans,
)
@settings(max_examples=150)
def test_property_consent_truth_table_schema(
    phone: str | None,
    blood_type: BloodType | None,
    keep_phone: bool,
    public_phone: bool,
    hero_name: bool,
    photo: bool,
) -> None:
    """T-CONSENT-02-a (Property): Comprehensive evaluation of DonorCreateIn schema across all states.

    Tests that any combination of (phone, blood_type, keep, public, hero, photo)
    strictly complies with the specification oracle:
    - Raises PhoneRequiresConsentError when phone is supplied without keep or public consent.
    - Raises PublicPhoneRequiresDataError when public consent is active without phone or blood type.
    - Successfully validates when consent rules are satisfied.
    """
    expected_valid, expected_error = evaluate_truth_table_oracle(
        phone, blood_type, keep_phone, public_phone
    )

    payload = {
        "first_name": "سارة",
        "last_name": "أحمد",
        "phone": phone,
        "blood_type": blood_type,
        "consent_keep_phone": keep_phone,
        "consent_public_phone": public_phone,
        "consent_hero_name": hero_name,
        "consent_photo": photo,
    }

    if not expected_valid:
        assert expected_error is not None
        with pytest.raises(expected_error):
            DonorCreateIn.model_validate(payload)
    else:
        schema = DonorCreateIn.model_validate(payload)
        assert schema.consent_keep_phone == keep_phone
        assert schema.consent_public_phone == public_phone
        assert schema.consent_hero_name == hero_name
        assert schema.consent_photo == photo
        if phone is not None:
            assert schema.phone is not None
        else:
            assert schema.phone is None
        assert schema.blood_type == blood_type


@pytest.mark.unit
@pytest.mark.property
@pytest.mark.critical
@pytest.mark.br("BR-CONSENT-04")
@pytest.mark.br("BR-CONSENT-06")
@given(
    # Initial valid donor state
    init_phone=valid_phones,
    init_blood=st.sampled_from(list(BloodType)),
    init_keep=st.booleans(),
    init_public=st.booleans(),
    # Arbitrary update deltas
    update_phone=st.one_of(st.just("OMIT"), st.none(), valid_phones),
    update_blood=st.one_of(st.just("OMIT"), st.none(), st.sampled_from(list(BloodType))),
    update_keep=st.one_of(st.just("OMIT"), st.booleans()),
    update_public=st.one_of(st.just("OMIT"), st.booleans()),
    update_hero=st.one_of(st.just("OMIT"), st.booleans()),
    update_photo=st.one_of(st.just("OMIT"), st.booleans()),
)
@settings(max_examples=200)
def test_property_consent_update_final_state_evaluation(
    init_phone: str,
    init_blood: BloodType,
    init_keep: bool,
    init_public: bool,
    update_phone: str | Literal["OMIT"] | None,
    update_blood: BloodType | Literal["OMIT"] | None,
    update_keep: bool | Literal["OMIT"],
    update_public: bool | Literal["OMIT"],
    update_hero: bool | Literal["OMIT"],
    update_photo: bool | Literal["OMIT"],
) -> None:
    """T-CONSENT-02-a / T-CONSENT-04-a (Property): Final-state evaluation and BR-CONSENT-06 enforcement.

    Ensures that for ANY valid initial donor and ANY arbitrary update delta:
    1. The candidate final state is computed from initial state + update deltas (BR-CONSENT-04).
    2. Withdrawing keep_phone when public_phone is False without explicit phone: null
       is correctly rejected (BR-CONSENT-06).
    3. The oracle exactly predicts service acceptance or failure.
    """
    # Ensure initial state is valid
    if not (init_keep or init_public):
        init_keep = True

    campaign_id = uuid.uuid4()
    site_id = uuid.uuid4()
    donor_id = uuid.uuid4()

    mock_campaign = Campaign(
        id=campaign_id,
        title="حملة نوفمبر",
        slug="nov",
        campaign_date=datetime(2026, 11, 20, 9, 0, 0, tzinfo=UTC),
        status="live",
        is_published=True,
        is_current=True,
    )
    mock_site = Site(id=site_id, name="موقع أ", is_active=True)
    supervisor = User(
        id=uuid.uuid4(),
        email="sup@domain.local",
        full_name="المشرف",
        role="supervisor",
        site_id=site_id,
        is_active=True,
    )

    in_memory_repo = InMemoryDonorRepository()
    initial_donor = Donor(
        id=donor_id,
        campaign_id=campaign_id,
        site_id=site_id,
        first_name="أحمد",
        last_name="علي",
        phone=init_phone,
        blood_type=init_blood.value,
        status="attended",
        consent_keep_phone=init_keep,
        consent_hero_name=False,
        consent_photo=False,
        consent_public_phone=init_public,
        registered_by=supervisor.id,
    )
    in_memory_repo.donors[donor_id] = initial_donor  # type: ignore[assignment]

    campaign_repo = MagicMock(spec=CampaignRepository)
    campaign_repo.get_live_campaign.return_value = mock_campaign
    site_repo = MagicMock(spec=SiteRepository)
    site_repo.get_by_id.return_value = mock_site

    service = DonorService(
        donor_repo=in_memory_repo,  # type: ignore[arg-type]
        campaign_repo=campaign_repo,
        site_repo=site_repo,
    )

    # Build update payload dict
    update_data: dict[str, Any] = {}
    if update_phone != "OMIT":
        update_data["phone"] = update_phone
    if update_blood != "OMIT":
        update_data["blood_type"] = update_blood
    if update_keep != "OMIT":
        update_data["consent_keep_phone"] = update_keep
    if update_public != "OMIT":
        update_data["consent_public_phone"] = update_public
    if update_hero != "OMIT":
        update_data["consent_hero_name"] = update_hero
    if update_photo != "OMIT":
        update_data["consent_photo"] = update_photo

    # Compute expected final state
    expected_final_phone = update_phone if update_phone != "OMIT" else init_phone
    expected_final_blood = (
        update_blood.value
        if (update_blood != "OMIT" and update_blood is not None)
        else (None if update_blood is None else init_blood.value)
    )
    expected_final_keep = update_keep if update_keep != "OMIT" else init_keep
    expected_final_public = update_public if update_public != "OMIT" else init_public

    expected_valid, expected_error = evaluate_truth_table_oracle(
        expected_final_phone,
        expected_final_blood,
        expected_final_keep,
        expected_final_public,
    )

    if not expected_valid:
        assert expected_error is not None
        with pytest.raises(expected_error):
            service.update_donor(supervisor, donor_id, update_data)
    else:
        updated = service.update_donor(supervisor, donor_id, update_data)
        assert updated.phone == expected_final_phone
        assert updated.blood_type == expected_final_blood
        assert updated.consent_keep_phone == expected_final_keep
        assert updated.consent_public_phone == expected_final_public


# ==============================================================================
# Part B: Counter Invariants Property Tests (Decision D-43, T-COUNT-02-a)
# ==============================================================================


@dataclass
class CreateAction:
    donor_id: uuid.UUID
    first_name: str
    consent_hero_name: bool


@dataclass
class ToggleStatusAction:
    donor_id: uuid.UUID
    target_status: Literal["attended", "donated"]


@dataclass
class ToggleHeroAction:
    donor_id: uuid.UUID
    consent_hero_name: bool


@dataclass
class DeleteAction:
    donor_id: uuid.UUID


# Action generators for sequential property testing
create_action_st = st.builds(
    CreateAction,
    donor_id=st.uuids(),
    first_name=st.sampled_from(["علي", "سارة", "محمد", "فاطمة", "خالد"]),
    consent_hero_name=st.booleans(),
)

action_types = st.sampled_from(["create", "toggle_status", "toggle_hero", "delete"])


@pytest.mark.unit
@pytest.mark.property
@pytest.mark.critical
@pytest.mark.br("BR-COUNT-01")
@pytest.mark.br("BR-COUNT-02")
@given(
    actions=st.lists(
        st.tuples(
            action_types,
            st.uuids(),
            st.sampled_from(["attended", "donated"]),
            st.booleans(),
        ),
        min_size=1,
        max_size=60,
    )
)
@settings(max_examples=100)
def test_property_counter_invariants_action_sequence(
    actions: list[tuple[str, uuid.UUID, Literal["attended", "donated"], bool]],
) -> None:
    """T-COUNT-02-a (Property): Invariants hold after EVERY single step in arbitrary lifecycle sequences.

    Lifecycle actions: (create, toggle status, toggle hero consent, delete donor).
    Invariant: 0 <= heroes_count <= donated_count <= present_count.
    """
    repo = InMemoryDonorRepository()
    campaign_id = uuid.uuid4()
    site_id = uuid.uuid4()
    active_ids: list[uuid.UUID] = []

    for action_kind, target_uuid, status_val, hero_val in actions:
        if action_kind == "create" or not active_ids:
            # Create a new donor record
            new_id = target_uuid
            if new_id not in repo.donors:
                donor = SimulatedDonor(
                    id=new_id,
                    campaign_id=campaign_id,
                    site_id=site_id,
                    first_name="متبرع",
                    last_name=None,
                    phone=None,
                    blood_type=None,
                    status="attended",  # Initial status is attended per BR-DONOR-03
                    consent_keep_phone=False,
                    consent_hero_name=hero_val,
                    consent_photo=False,
                    consent_public_phone=False,
                    registered_by=uuid.uuid4(),
                )
                repo.create(donor)
                active_ids.append(new_id)

        elif action_kind == "toggle_status":
            # Change status of an existing donor
            chosen_id = active_ids[hash(target_uuid) % len(active_ids)]
            donor = repo.get_by_id(chosen_id)
            if donor:
                donor.status = status_val
                repo.update(donor)

        elif action_kind == "toggle_hero":
            # Toggle hero consent of an existing donor
            chosen_id = active_ids[hash(target_uuid) % len(active_ids)]
            donor = repo.get_by_id(chosen_id)
            if donor:
                donor.consent_hero_name = hero_val
                repo.update(donor)

        elif action_kind == "delete":
            # Delete an existing donor
            chosen_id = active_ids[hash(target_uuid) % len(active_ids)]
            donor = repo.get_by_id(chosen_id)
            if donor:
                repo.delete_donor(donor)
                active_ids.remove(chosen_id)

        # ----------------------------------------------------------------------
        # Assert Invariants after EVERY step (BR-COUNT-02, T-COUNT-02-a)
        # ----------------------------------------------------------------------
        present, donated, heroes = repo.count_site_counters(campaign_id, site_id)

        # 1. Non-negativity
        assert heroes >= 0, f"Negative heroes_count: {heroes}"
        assert donated >= 0, f"Negative donated_count: {donated}"
        assert present >= 0, f"Negative present_count: {present}"

        # 2. Main Invariant: heroes <= donated <= present
        assert heroes <= donated, f"Invariant broken: heroes ({heroes}) > donated ({donated})"
        assert donated <= present, f"Invariant broken: donated ({donated}) > present ({present})"

        # 3. Model validation
        assert present == len(active_ids)
        assert donated == sum(1 for d_id in active_ids if repo.donors[d_id].status == "donated")
        assert heroes == sum(
            1
            for d_id in active_ids
            if repo.donors[d_id].status == "donated" and repo.donors[d_id].consent_hero_name is True
        )


# ==============================================================================
# Part C: Hypothesis RuleBasedStateMachine (Stateful Invariant Testing)
# ==============================================================================


class DonorCounterStateMachine(RuleBasedStateMachine):
    """Hypothesis state machine verifying counter invariants across complex interleaved actions."""

    def __init__(self) -> None:
        super().__init__()
        self.campaign_id = uuid.uuid4()
        self.site_id = uuid.uuid4()
        self.repo = InMemoryDonorRepository()
        self.active_donors: dict[uuid.UUID, SimulatedDonor] = {}

    donors = Bundle("donors")

    @rule(target=donors, hero_consent=st.booleans())
    def create_attended_donor(self, hero_consent: bool) -> uuid.UUID:
        """Create a new attended donor with arbitrary hero consent."""
        donor_id = uuid.uuid4()
        donor = SimulatedDonor(
            id=donor_id,
            campaign_id=self.campaign_id,
            site_id=self.site_id,
            first_name="متبرع",
            last_name=None,
            phone=None,
            blood_type=None,
            status="attended",
            consent_keep_phone=False,
            consent_hero_name=hero_consent,
            consent_photo=False,
            consent_public_phone=False,
            registered_by=uuid.uuid4(),
        )
        self.repo.create(donor)
        self.active_donors[donor_id] = donor
        return donor_id

    @rule(donor_id=donors)
    def mark_donated(self, donor_id: uuid.UUID) -> None:
        """Advance donor status from attended to donated (BR-DONOR-06)."""
        donor = self.repo.get_by_id(donor_id)
        if donor is not None:
            donor.status = "donated"
            donor.donated_at = datetime.now(UTC)
            self.repo.update(donor)

    @rule(donor_id=donors)
    def revert_to_attended(self, donor_id: uuid.UUID) -> None:
        """Revert donor status from donated back to attended (BR-DONOR-06)."""
        donor = self.repo.get_by_id(donor_id)
        if donor is not None:
            donor.status = "attended"
            donor.donated_at = None
            self.repo.update(donor)

    @rule(donor_id=donors, new_consent=st.booleans())
    def toggle_hero_consent(self, donor_id: uuid.UUID, new_consent: bool) -> None:
        """Toggle hero consent for an existing donor."""
        donor = self.repo.get_by_id(donor_id)
        if donor is not None:
            donor.consent_hero_name = new_consent
            self.repo.update(donor)

    @rule(donor_id=consumes(donors))
    def delete_donor_rule(self, donor_id: uuid.UUID) -> None:
        """Delete a donor from active pool (BR-DONOR-11)."""
        donor = self.repo.get_by_id(donor_id)
        if donor is not None:
            self.repo.delete_donor(donor)
            self.active_donors.pop(donor_id, None)

    @invariant()
    def verify_counter_invariants(self) -> None:
        """CRITICAL INVARIANT (BR-COUNT-02, T-COUNT-02-a):

        0 <= heroes <= donated <= present
        """
        present, donated, heroes = self.repo.count_site_counters(self.campaign_id, self.site_id)

        # Invariant inequality chain
        assert heroes >= 0, f"heroes count is negative: {heroes}"
        assert heroes <= donated, f"heroes ({heroes}) > donated ({donated})"
        assert donated <= present, f"donated ({donated}) > present ({present})"

        # Structural model checks
        assert present == len(self.active_donors)
        assert donated == sum(1 for d in self.active_donors.values() if d.status == "donated")
        assert heroes == sum(
            1
            for d in self.active_donors.values()
            if d.status == "donated" and d.consent_hero_name is True
        )


TestDonorCounterStateMachine = DonorCounterStateMachine.TestCase
