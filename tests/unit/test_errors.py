"""Unit tests for domain errors, error code registry, and error serialization.

Traces to:
- P1.3 (docs/12-implementation-plan.md §4)
- docs/04-api.md §3 (unified error envelope and code catalog)
- docs/05-architecture.md §9 (error structure and domain exceptions)
"""

import pytest

from app.core.errors import (
    ERROR_SPEC_REGISTRY,
    AppError,
    ErrorCode,
    InternalError,
    NotFoundError,
    PhoneRequiresConsentError,
    RateLimitedError,
    ValidationError,
    get_arabic_message,
    get_error_spec,
)


@pytest.mark.unit
def test_app_error_base_serialization_minimal() -> None:
    """Verify minimal serialization into unified envelope without details or request_id."""
    err = NotFoundError()
    payload = err.to_dict()

    assert payload == {
        "error": {
            "code": "not_found",
            "message": "Resource not found.",
        }
    }
    assert err.http_status == 404
    assert err.code == ErrorCode.NOT_FOUND


@pytest.mark.unit
def test_app_error_base_serialization_full() -> None:
    """Verify serialization into unified envelope with details, request_id, and custom message."""
    details = [{"field": "phone", "issue": "requires_consent"}]
    err = PhoneRequiresConsentError(
        message="Custom technical explanation",
        details=details,
    )
    payload = err.to_dict(request_id="req-123-abc")

    assert payload == {
        "error": {
            "code": "phone_requires_consent",
            "message": "Custom technical explanation",
            "details": details,
            "request_id": "req-123-abc",
        }
    }
    assert err.http_status == 422


@pytest.mark.unit
def test_validation_error_with_details() -> None:
    """Verify ValidationError properly retains field details."""
    field_errors = [
        {"field": "email", "issue": "invalid_email_format"},
        {"field": "password", "issue": "too_short"},
    ]
    err = ValidationError(details=field_errors)
    payload = err.to_dict(request_id="test-req-001")

    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["details"] == field_errors
    assert payload["error"]["request_id"] == "test-req-001"
    assert err.http_status == 422


@pytest.mark.unit
def test_rate_limited_error_headers() -> None:
    """Verify RateLimitedError attaches Retry-After header when supplied."""
    err_with_retry = RateLimitedError(retry_after=60)
    assert err_with_retry.http_status == 429
    assert err_with_retry.headers == {"Retry-After": "60"}
    assert err_with_retry.retry_after == 60

    err_no_retry = RateLimitedError()
    assert err_no_retry.headers is None
    assert err_no_retry.retry_after is None


@pytest.mark.unit
def test_all_registry_error_codes_have_subclasses() -> None:
    """Verify that every ErrorCode has a complete entry in ERROR_SPEC_REGISTRY."""
    assert len(ErrorCode) == 32
    assert len(ERROR_SPEC_REGISTRY) == 32

    for code in ErrorCode:
        assert code in ERROR_SPEC_REGISTRY
        spec = ERROR_SPEC_REGISTRY[code]
        assert spec.code == code
        assert issubclass(spec.exception_class, AppError)
        assert spec.exception_class.code == code
        assert spec.exception_class.http_status == spec.http_status
        assert spec.ar_message != ""
        assert spec.default_message != ""

        # Test instantiation of the exception class
        instance = spec.exception_class()
        assert instance.code == code
        assert instance.http_status == spec.http_status
        assert instance.message == spec.default_message


@pytest.mark.unit
def test_arabic_messages_lookup() -> None:
    """Verify Arabic message retrieval for UI client localization."""
    assert get_arabic_message(ErrorCode.INVALID_CREDENTIALS) == "بيانات الدخول غير صحيحة"
    assert get_arabic_message("session_expired") == "انتهت الجلسة، يرجى الدخول مجددًا"
    assert get_arabic_message("rate_limited") == "حاول مرة أخرى بعد قليل"
    assert get_arabic_message("last_admin") == "لا يمكن تعطيل آخر أدمن"
    assert get_arabic_message("internal_error") == "حدث خطأ غير متوقع، حاول لاحقًا"


@pytest.mark.unit
def test_get_error_spec_lookup() -> None:
    """Verify error spec lookup helper works by enum and string."""
    spec_enum = get_error_spec(ErrorCode.NOT_FOUND)
    spec_str = get_error_spec("not_found")

    assert spec_enum == spec_str
    assert spec_enum.http_status == 404
    assert spec_enum.code == ErrorCode.NOT_FOUND


@pytest.mark.unit
def test_polymorphic_exception_handling() -> None:
    """Verify domain exceptions can be caught polymorphically as AppError."""
    caught = False
    try:
        raise InternalError("Something broke")
    except AppError as e:
        caught = True
        assert e.code == ErrorCode.INTERNAL_ERROR
        assert e.http_status == 500

    assert caught is True
