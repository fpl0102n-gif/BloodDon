"""Unit tests for common shared schemas, data types, and normalization rules.

Traces to:
- P1.9 (docs/12-implementation-plan.md §4)
- docs/06-business-rules.md §2 (BR-GEN-01..06)
- docs/07-test-cases.md (T-GEN-01, T-GEN-02, T-GEN-03, T-GEN-04-a, T-GEN-05, T-GEN-06)
"""

import pytest
from pydantic import ValidationError

from app.schemas.common import (
    AppBaseModel,
    BloodType,
    HeroesPaginationParams,
    NormalizedEmail,
    NormalizedName,
    PaginatedResponse,
    PaginationParams,
    PhoneNumber,
    normalize_email,
    normalize_name,
    normalize_phone_number,
)


class DummyModel(AppBaseModel):
    """Test model exercising all shared field types and extra='forbid' validation."""

    phone: PhoneNumber
    name: NormalizedName
    email: NormalizedEmail | None = None
    blood_type: BloodType | None = None


# ==============================================================================
# 1. Phone Normalization Tests (BR-GEN-01, T-GEN-01)
# ==============================================================================


@pytest.mark.unit
def test_phone_number_normalization_arabic_indic_t_gen_01_a() -> None:
    """T-GEN-01-a: Phone number with Arabic-Indic digits and punctuation is normalized to ASCII."""
    # 10-digit Algerian mobile format (0550 00 00 00)
    raw_phone_10 = "٠٥٥٠ ٠٠-٠٠٠٠"
    assert normalize_phone_number(raw_phone_10) == "0550000000"

    # 12-digit variant per spec table
    raw_phone_12 = "٠٥٥٠ ٠٠-٠٠٠٠٠٠"
    assert normalize_phone_number(raw_phone_12) == "055000000000"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_input", "expected"),
    [
        ("+213 550-000-000", "+213550000000"),
        ("(0550) 00.00.00", "0550000000"),
        ("+213 (0) 550 12 34 56", "+2130550123456"),
        ("0550.12.34.56", "0550123456"),
        ("  0770-11-22-33  ", "0770112233"),
        ("۰۵۵۰ ۱۲۳ ۴۵۶", "0550123456"),  # Eastern Arabic/Persian digits
    ],
)
def test_phone_number_normalization_international_and_spaces_t_gen_01_b(
    raw_input: str,
    expected: str,
) -> None:
    """T-GEN-01-b: International formats, dots, dashes, and parentheses normalize accurately."""
    assert normalize_phone_number(raw_input) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_phone",
    [
        "123",  # Too short (< 8 digits)
        "+",  # No digits
        "1234567890123456",  # Too long (16 digits > 15)
        "12+3456789",  # Plus sign not at start
        "abc",  # Letters
        "+213+550000000",  # Multiple plus signs
        "++213550000000",  # Double leading plus
        "0550-abc-0000",  # Mixed letters
        "",  # Empty string
        "   ",  # Whitespace only
    ],
)
def test_phone_number_invalid_rejected_t_gen_01_c(invalid_phone: str) -> None:
    """T-GEN-01-c: Malformed or out-of-range phone numbers raise ValueError."""
    with pytest.raises(ValueError):
        normalize_phone_number(invalid_phone)


# ==============================================================================
# 2. Name Normalization Tests (BR-GEN-02, T-GEN-02)
# ==============================================================================


@pytest.mark.unit
def test_name_normalization_t_gen_02_a() -> None:
    """T-GEN-02-a: Trims outer whitespace and collapses multiple inner spaces to one."""
    assert normalize_name("  أحمد   علي ") == "أحمد علي"
    assert normalize_name("  Mohamed    Amine  Benali  ") == "Mohamed Amine Benali"


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_name",
    [
        "Ahmed\x00Ali",  # Null byte (0x00)
        "Ahmed\x07Ali",  # Bell character (0x07)
        "Ahmed\x1fAli",  # Control char (0x1F)
        "Ahmed\x7fAli",  # Delete char (0x7F)
        "",  # Empty string
        "   ",  # Whitespace only
    ],
)
def test_name_control_characters_rejected_t_gen_02_b(invalid_name: str) -> None:
    """T-GEN-02-b: Names containing control characters are rejected with ValueError."""
    with pytest.raises(ValueError):
        normalize_name(invalid_name)


# ==============================================================================
# 3. Email Normalization Tests (BR-GEN-03, T-GEN-03)
# ==============================================================================


@pytest.mark.unit
def test_email_normalization_t_gen_03_a() -> None:
    """T-GEN-03-a: Email is trimmed and converted to lowercase."""
    assert normalize_email(" Ali@Example.COM ") == "ali@example.com"
    assert normalize_email("ADMIN@DOMAIN.DZ") == "admin@domain.dz"


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_email",
    [
        "invalid-email",
        "",
        "   ",
    ],
)
def test_email_invalid_rejected(invalid_email: str) -> None:
    """Verify malformed email strings raise ValueError."""
    with pytest.raises(ValueError):
        normalize_email(invalid_email)


# ==============================================================================
# 4. Blood Type Enumeration Tests (BR-DONOR-04, T-GEN-04-a)
# ==============================================================================


@pytest.mark.unit
def test_blood_type_authorized_values() -> None:
    """Verify that exactly the 8 authorized blood types exist in the enum."""
    expected = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}
    actual = {bt.value for bt in BloodType}
    assert actual == expected


@pytest.mark.unit
def test_blood_type_validation_in_pydantic() -> None:
    """Verify Pydantic model parses valid blood types and rejects invalid values (T-DONOR-04-a)."""
    valid_instance = DummyModel(
        phone="0550000000",
        name="Sarah",
        blood_type=BloodType.O_POS,
    )
    assert valid_instance.blood_type == "O+"

    with pytest.raises(ValidationError):
        DummyModel.model_validate(
            {
                "phone": "0550000000",
                "name": "Sarah",
                "blood_type": "C+",  # Invalid blood type (T-DONOR-04-a)
            }
        )


# ==============================================================================
# 5. Pagination Bounds Tests (BR-GEN-05, T-GEN-05)
# ==============================================================================


@pytest.mark.unit
def test_pagination_params_allowed_t_gen_05_a() -> None:
    """T-GEN-05-a: Standard pagination accepts defaults and up to limit=100."""
    default_params = PaginationParams()
    assert default_params.limit == 20
    assert default_params.offset == 0

    max_params = PaginationParams(limit=100, offset=50)
    assert max_params.limit == 100
    assert max_params.offset == 50


@pytest.mark.unit
@pytest.mark.parametrize(
    ("limit_val", "offset_val"),
    [
        (101, 0),  # Limit exceeds hard cap 100
        (0, 0),  # Limit < 1
        (-1, 0),  # Negative limit
        (20, -1),  # Negative offset
    ],
)
def test_pagination_params_rejected_t_gen_05_b(limit_val: int, offset_val: int) -> None:
    """T-GEN-05-b: Limit > 100, limit <= 0, or negative offset raise ValidationError."""
    with pytest.raises(ValidationError):
        PaginationParams(limit=limit_val, offset=offset_val)


@pytest.mark.unit
def test_heroes_pagination_params_t_gen_05_c() -> None:
    """T-GEN-05-c: Heroes pagination accepts limit up to 200, rejects limit=201."""
    allowed = HeroesPaginationParams(limit=200, offset=0)
    assert allowed.limit == 200

    with pytest.raises(ValidationError):
        HeroesPaginationParams(limit=201, offset=0)


# ==============================================================================
# 6. Extra Fields Rejection Tests (BR-GEN-06, T-GEN-06)
# ==============================================================================


@pytest.mark.unit
def test_app_base_model_forbids_extra_fields_t_gen_06_a() -> None:
    """T-GEN-06-a: Extra fields on any model inheriting AppBaseModel are strictly rejected."""
    # Valid model instantiation
    model = DummyModel(phone="0550000000", name="Test User")
    assert model.name == "Test User"
    assert model.phone == "0550000000"

    # With unexpected extra field
    with pytest.raises(ValidationError) as exc_info:
        DummyModel.model_validate(
            {
                "phone": "0550000000",
                "name": "Test User",
                "zzz_extra": "unexpected_payload",  # Extra field
            }
        )

    errors = exc_info.value.errors()
    assert any(err["type"] == "extra_forbidden" for err in errors)


# ==============================================================================
# 7. Paginated Response Envelope Tests
# ==============================================================================


@pytest.mark.unit
def test_paginated_response_envelope() -> None:
    """Verify generic PaginatedResponse serializes properly with items and metadata."""
    payload = PaginatedResponse[str](
        items=["item1", "item2"],
        total=2,
        limit=20,
        offset=0,
    )
    dumped = payload.model_dump()
    assert dumped == {
        "items": ["item1", "item2"],
        "total": 2,
        "limit": 20,
        "offset": 0,
    }
