"""Test factories for donors with valid consent and status constraints."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.donor import Donor

_donor_counter = 0


def create_donor(
    session: Session | None = None,
    *,
    campaign_id: uuid.UUID,
    site_id: uuid.UUID,
    id_: uuid.UUID | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
    blood_type: str | None = None,
    status: str = "attended",
    consent_keep_phone: bool = False,
    consent_hero_name: bool = False,
    consent_photo: bool = False,
    consent_public_phone: bool = False,
    registered_by: uuid.UUID | None = None,
    donated_at: datetime | None = None,
    anonymized_at: datetime | None = None,
    **kwargs: Any,
) -> Donor:
    """Create a Donor model instance obeying all 8 DB CHECK constraints.

    Rules handled:
    - If phone is set: must have consent_keep_phone or consent_public_phone (ck_donors_phone_needs_consent).
    - If consent_public_phone is True: phone and blood_type must not be None (ck_donors_public_phone_needs_data).
    - If status is 'donated' and donated_at is None: automatically set donated_at.
    - If status is 'attended': donated_at must be None (ck_donors_donated_at).
    """
    global _donor_counter
    _donor_counter += 1

    if first_name is None:
        first_name = f"متبرع {_donor_counter}"

    # Reconcile status and donated_at per ck_donors_donated_at
    if status == "donated" and donated_at is None:
        donated_at = datetime.now(UTC)
    elif status == "attended":
        donated_at = None

    # Reconcile phone and consents per ck_donors_phone_needs_consent
    if phone is not None and not (consent_keep_phone or consent_public_phone):
        consent_keep_phone = True

    # Reconcile public phone and data per ck_donors_public_phone_needs_data
    if consent_public_phone:
        if phone is None:
            phone = f"+213550{_donor_counter:06d}"
        if blood_type is None:
            blood_type = "O+"

    donor = Donor(
        id=id_ or uuid.uuid4(),
        campaign_id=campaign_id,
        site_id=site_id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        blood_type=blood_type,
        status=status,
        consent_keep_phone=consent_keep_phone,
        consent_hero_name=consent_hero_name,
        consent_photo=consent_photo,
        consent_public_phone=consent_public_phone,
        registered_by=registered_by,
        donated_at=donated_at,
        anonymized_at=anonymized_at,
        **kwargs,
    )

    if session is not None:
        session.add(donor)
        session.flush()

    return donor
