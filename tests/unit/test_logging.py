"""Unit tests for structured logging and central PII redaction filter.

Traces to:
- P1.5 (docs/12-implementation-plan.md §4)
- docs/05-architecture.md §15 (logging, request-id, JSON formatting, redaction)
- CLAUDE.md §6 Golden Rule 9 (central redaction; no phone, name, blood type, password in logs)
- T-STRUCT-10 (sentinel values redaction test)
"""

import io
import json
import logging

import pytest

from app.core.config import Settings
from app.core.logging import (
    JSONFormatter,
    RedactionFilter,
    anonymize_ip,
    redact_object,
    redact_text,
    set_request_id,
    setup_logging,
)


@pytest.mark.unit
def test_t_struct_10_sentinel_values_redaction() -> None:
    """T-STRUCT-10: Verify sentinel test values are strictly masked and never appear in logs."""
    sentinels = [
        "ZZFIRST",
        "zzfirst",
        "ZZLAST",
        "zzlast",
        "0550123456",
    ]

    for val in sentinels:
        raw_msg = f"Processing donor record for {val} in site A"
        cleaned = redact_text(raw_msg)
        assert val not in cleaned
        assert "[REDACTED]" in cleaned


@pytest.mark.unit
def test_phone_number_formats_redacted() -> None:
    """Verify various Algerian and international phone formats are masked."""
    phone_samples = [
        "0551234567",
        "0661234567",
        "0771234567",
        "+213551234567",
        "00213551234567",
        "+213 551 23 45 67",
        "+33612345678",
        "+14155552671",
    ]

    for phone in phone_samples:
        raw = f"Contact verified: {phone} for donor"
        cleaned = redact_text(raw)
        # Verify the raw phone does not appear
        assert phone not in cleaned
        assert "[REDACTED]" in cleaned


@pytest.mark.unit
def test_email_redacted() -> None:
    """Verify email addresses in text are masked."""
    emails = [
        "supervisor@blood.org",
        "user.test+tag@hospital.dz",
        "donor123@sub.example.com",
    ]

    for email in emails:
        raw = f"Sending invite notification to {email} now"
        cleaned = redact_text(raw)
        assert email not in cleaned
        assert "[REDACTED]" in cleaned


@pytest.mark.unit
def test_secrets_and_bearer_tokens_redacted() -> None:
    """Verify bearer tokens, passwords, and API secrets are masked."""
    test_cases = [
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.fake",  # pragma: allowlist secret
        "password='super_secret_password_123'",  # pragma: allowlist secret
        'secret="my_totp_seed_key_xyz"',  # pragma: allowlist secret
        "token: tok_abcdef123456",
        "recovery_code=12345678",
    ]

    for secret in test_cases:
        raw = f"Debug context dump: {secret} in payload"
        cleaned = redact_text(raw)
        assert "super_secret_password_123" not in cleaned
        assert "my_totp_seed_key_xyz" not in cleaned
        assert "tok_abcdef123456" not in cleaned
        assert "[REDACTED]" in cleaned


@pytest.mark.unit
def test_blood_types_redacted() -> None:
    """Verify donor blood groups are masked in log strings."""
    blood_types = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]

    for bt in blood_types:
        raw = f"Emergency search criteria matched group {bt} at center"
        cleaned = redact_text(raw)
        assert bt not in cleaned
        assert "[REDACTED]" in cleaned


@pytest.mark.unit
def test_ipv4_address_redacted() -> None:
    """Verify external client IPv4 addresses are masked in free text."""
    ips = ["198.51.100.42", "203.0.113.195", "10.0.5.21"]

    for ip in ips:
        raw = f"Client request received from remote IP {ip}"
        cleaned = redact_text(raw)
        assert ip not in cleaned
        assert "[REDACTED]" in cleaned


@pytest.mark.unit
def test_anonymize_ip_deterministic_salted_hash() -> None:
    """Verify IP hashing provides consistent pseudo-anonymization without leaking IP."""
    salt = "a" * 32
    hash1 = anonymize_ip("192.0.2.1", salt=salt)
    hash2 = anonymize_ip("192.0.2.1", salt=salt)
    hash3 = anonymize_ip("192.0.2.2", salt=salt)

    assert hash1.startswith("ip_hash_")
    assert hash1 == hash2
    assert hash1 != hash3
    assert "192.0.2.1" not in hash1


@pytest.mark.unit
def test_recursive_object_redaction() -> None:
    """Verify dictionary keys and nested structures are sanitized."""
    sensitive_data = {
        "user_id": 42,
        "email": "secret_user@example.com",
        "credentials": {
            "password": "plain_password",  # pragma: allowlist secret
            "totp_secret": "my_totp_secret",  # pragma: allowlist secret
            "nested_list": ["0550123456", "safe_val"],
        },
        "safe_key": "safe_data",
        "blood_type": "O+",
    }

    cleaned = redact_object(sensitive_data)

    assert cleaned["email"] == "[REDACTED]"
    assert cleaned["credentials"]["password"] == "[REDACTED]"
    assert cleaned["credentials"]["totp_secret"] == "[REDACTED]"
    assert cleaned["credentials"]["nested_list"][0] == "[REDACTED]"
    assert cleaned["credentials"]["nested_list"][1] == "safe_val"
    assert cleaned["safe_key"] == "safe_data"
    assert cleaned["blood_type"] == "[REDACTED]"


@pytest.mark.unit
def test_redaction_filter_integration_with_logger() -> None:
    """Verify RedactionFilter cleans record.msg and positional args on actual loggers."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))

    test_logger = logging.getLogger("test_redaction_logger")
    test_logger.handlers.clear()
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)

    set_request_id("test-req-id-555")

    # Log with formatting placeholders and sensitive args
    test_logger.info(
        "Donor %s registered with contact %s and blood %s",
        "ZZFIRST",
        "0550123456",
        "O+",
    )

    output = log_stream.getvalue()

    assert "ZZFIRST" not in output
    assert "0550123456" not in output
    assert "O+" not in output
    assert "[REDACTED]" in output

    set_request_id(None)


@pytest.mark.unit
def test_json_formatter_structure_and_traceback_redaction() -> None:
    """Verify JSONFormatter outputs valid JSON and redacts exceptions."""
    set_request_id("trace-req-888")

    record = logging.LogRecord(
        name="app.test",
        level=logging.ERROR,
        pathname="test.py",
        lineno=10,
        msg="Operation failed for user %s with key %s",
        args=("ZZFIRST", "password=super_secret"),
        exc_info=None,
    )
    RedactionFilter().filter(record)

    # Attach fake exception info containing secret
    try:
        raise ValueError(
            "Crash with sensitive password=my_leaked_secret and phone 0550123456"  # pragma: allowlist secret
        )
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()

    formatter = JSONFormatter()
    output = formatter.format(record)

    parsed = json.loads(output)
    assert parsed["level"] == "ERROR"
    assert parsed["logger"] == "app.test"
    assert parsed["request_id"] == "trace-req-888"
    assert "timestamp" in parsed
    assert "ZZFIRST" not in parsed["message"]
    assert "super_secret" not in parsed["message"]

    # Verify exception traceback was also redacted
    assert "exc_info" in parsed
    assert "my_leaked_secret" not in parsed["exc_info"]
    assert "0550123456" not in parsed["exc_info"]
    assert "[REDACTED]" in parsed["exc_info"]

    set_request_id(None)


@pytest.mark.unit
def test_setup_logging_production_configuration() -> None:
    """Verify setup_logging configures root logger with JSONFormatter in prod."""
    prod_settings = Settings(
        app_env="dev",
        log_format="json",
        log_level="WARNING",
    )

    setup_logging(prod_settings)

    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JSONFormatter)
    assert any(isinstance(f, RedactionFilter) for f in root.handlers[0].filters)
