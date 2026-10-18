"""Emergency public schemas (P7.1 / P7.2).

Per:
- docs/04-api.md §8.1 (GET /public/emergency/donors)
- docs/04-api.md §8.2 (POST /public/emergency/donors/{donor_id}/reveal)
- docs/06-business-rules.md §10 (BR-EMERG-01..14)
- docs/07-test-cases.md §5.9 (T-EMERG-01..14)
- docs/05-architecture.md §8 (Schemas layer: strict validation, extra='forbid')
"""

import uuid
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from app.schemas.common import AppBaseModel, BloodType


def normalize_blood_type_query(v: Any) -> Any:
    """Normalize blood type query parameter handling unencoded '+' signs parsed as spaces."""
    if isinstance(v, str):
        cleaned = v.strip()
        if v.endswith(" ") and cleaned in {"A", "B", "AB", "O"}:
            cleaned = cleaned + "+"
        return cleaned
    return v


BloodTypeQuery = Annotated[BloodType, BeforeValidator(normalize_blood_type_query)]


class EmergencyDonorItemOut(AppBaseModel):
    """Public representation of an eligible donor on the emergency board (BR-EMERG-02 ★).

    STRICT PRIVACY GUARD:
    Per BR-EMERG-02 ★ & T-EMERG-02-a ★, the emergency list NEVER contains a phone number!
    Exactly and only donor_id, first_name, blood_type, site_name, and days_since_donation are returned.
    No last_name, phone, status, or internal identifiers.
    """

    donor_id: uuid.UUID = Field(description="Anonymous public donor identifier for reveal action")
    first_name: str = Field(description="First name of the eligible donor")
    blood_type: BloodType = Field(description="ABO/Rh blood group of the donor")
    site_name: str = Field(description="Name of the donation center/site where donation occurred")
    days_since_donation: int = Field(
        ge=0,
        description="Elapsed days since donation calculated in operational timezone (BR-EMERG-11)",
    )


class EmergencyRevealOut(AppBaseModel):
    """Result of emergency phone reveal (BR-EMERG-05 ★, docs/04-api.md §8.2).

    STRICT PRIVACY GUARD:
    Contains ONLY the revealed phone number. Strictly forbids extra fields.
    """

    phone: str = Field(description="Donor phone number for medical emergency contact")
