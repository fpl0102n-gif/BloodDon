"""Common shared Pydantic schemas, normalization validators, and base types.

Implements P1.9 per docs/05-architecture.md §7, docs/06-business-rules.md §2 (BR-GEN-01..06),
and CLAUDE.md §6 Golden Rule 4:
- AppBaseModel: Base model enforcing extra='forbid' across all entry schemas.
- PhoneNumber: Strict Algerian and international phone format normalization (BR-GEN-01, T-GEN-01).
- NormalizedName: Whitespace collapsing and control-character rejection for donor/user names (BR-GEN-02, T-GEN-02).
- BloodType: Strict enumeration of the 8 valid human blood types (BR-DONOR-04, T-GEN-04-a).
- PaginationParams & HeroesPaginationParams: Strict pagination bounds (limit <= 100 / <= 200, BR-GEN-05, T-GEN-05).
- PaginatedResponse: Generic envelope for paginated collections.
"""

import re
import unicodedata
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, functional_validators

# Translation table converting Arabic-Indic and Eastern Arabic-Indic digits to ASCII (BR-GEN-01)
ARABIC_INDIC_DIGITS = str.maketrans(
    {
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
    }
)

# Final pattern: optional leading plus sign followed by 8 to 15 digits (BR-GEN-01)
PHONE_REGEX = re.compile(r"^\+?[0-9]{8,15}$")


# ==============================================================================
# 1. Base Pydantic Models (extra="forbid")
# ==============================================================================


class AppBaseModel(BaseModel):
    """Base model for all application schemas enforcing strict unknown field rejection."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# ==============================================================================
# 2. Blood Type Enumeration
# ==============================================================================


class BloodType(StrEnum):
    """Authoritative enumeration of the 8 valid human blood types (BR-DONOR-04)."""

    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    AB_POS = "AB+"
    AB_NEG = "AB-"
    O_POS = "O+"
    O_NEG = "O-"


# ==============================================================================
# 3. Normalization Functions & Annotated Types
# ==============================================================================


def normalize_phone_number(value: Any) -> str:
    """Normalize and validate phone numbers per BR-GEN-01 and T-GEN-01.

    Rules:
    - Converts Arabic-Indic and Eastern Arabic digits to Latin ASCII.
    - Strips whitespace, hyphens, dots, parentheses, brackets, and slashes.
    - Allows a leading '+' sign only at index 0. Rejects internal or multiple plus signs.
    - Enforces length constraint: exactly 8 to 15 digits.
    - Rejects letters and non-numeric characters.
    """
    if not isinstance(value, str):
        raise ValueError("Phone number must be a string.")

    cleaned = value.strip().translate(ARABIC_INDIC_DIGITS)
    if not cleaned:
        raise ValueError("Phone number cannot be empty.")

    has_plus = cleaned.startswith("+")
    remainder = cleaned[1:] if has_plus else cleaned

    if "+" in remainder:
        raise ValueError("Plus sign '+' is only permitted at the beginning of the phone number.")

    # Remove standard formatting punctuation: spaces, dashes, dots, parentheses, brackets, slashes
    digits_only = re.sub(r"[\s\-\.\(\)\[\]/\\_]+", "", remainder)

    if not digits_only:
        raise ValueError("Phone number contains no digits.")

    if not digits_only.isdigit():
        raise ValueError("Phone number contains illegal non-numeric characters.")

    digit_count = len(digits_only)
    if digit_count < 8 or digit_count > 15:
        raise ValueError(f"Phone number must contain between 8 and 15 digits (got {digit_count}).")

    return f"+{digits_only}" if has_plus else digits_only


def normalize_name(value: Any) -> str:
    """Normalize donor and user names per BR-GEN-02 and T-GEN-02.

    Rules:
    - Strips outer whitespace.
    - Rejects illegal control characters (e.g. \\x00, \\x07, or Unicode category 'Cc').
    - Collapses multiple consecutive whitespace characters into a single space.
    """
    if not isinstance(value, str):
        raise ValueError("Name must be a string.")

    val = value.strip()
    if not val:
        raise ValueError("Name cannot be empty.")

    # Reject control characters (0-31, 127-159, or category Cc)
    for ch in val:
        if ord(ch) < 32 or (127 <= ord(ch) <= 159) or unicodedata.category(ch) == "Cc":
            raise ValueError(f"Name contains illegal control character (code {ord(ch)}).")

    # Collapse internal whitespace
    return re.sub(r"\s+", " ", val)


def normalize_email(value: Any) -> str:
    """Normalize email address per BR-GEN-03: lowercase and trimmed."""
    if not isinstance(value, str):
        raise ValueError("Email must be a string.")

    val = value.strip().lower()
    if not val or "@" not in val:
        raise ValueError("Invalid email format.")

    return val


PhoneNumber = Annotated[str, functional_validators.BeforeValidator(normalize_phone_number)]
NormalizedName = Annotated[str, functional_validators.BeforeValidator(normalize_name)]
NormalizedEmail = Annotated[str, functional_validators.BeforeValidator(normalize_email)]


# ==============================================================================
# 4. Pagination Schemas (BR-GEN-05, T-GEN-05)
# ==============================================================================


class PaginationParams(AppBaseModel):
    """Standard pagination parameters capped at 100 items (BR-GEN-05)."""

    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum items per page (hard cap 100)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip",
    )


class HeroesPaginationParams(AppBaseModel):
    """Specialized pagination parameters for Heroes wall capped at 200 items (BR-GEN-05)."""

    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum hero cards per page (hard cap 200)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip",
    )


class PaginatedResponse[T](AppBaseModel):
    """Generic envelope for paginated collection responses."""

    items: list[T]
    total: int = Field(ge=0, description="Total count of available matching items")
    limit: int = Field(ge=1, description="Applied page size")
    offset: int = Field(ge=0, description="Applied page offset")
