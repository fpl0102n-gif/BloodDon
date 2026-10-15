"""Security and structural test T-STRUCT-10: Central Log Redaction.

Per docs/07-test-cases.md §7:
"T-STRUCT-10: الحجب في السجلات — تشغيل تدفقات كاملة بقيم خَفر — ظهور قيمة حساسة في السجل"

Per CLAUDE.md §6 Golden Rule 9:
"لا بيانات شخصية في السجلات ولا في التدقيق ولا في الأخطاء: لا اسم، لا هاتف، لا فصيلة،
لا كلمة مرور، لا رمز. المرشّح المركزي يحجب، والاختبار يكشف بقيم خفر."
"""

import io
import json
import logging

import pytest

from app.core.config import Settings
from app.core.logging import setup_logging


@pytest.mark.security
@pytest.mark.architecture
@pytest.mark.unit
def test_t_struct_10_sentinel_leakage_prevented_in_json_and_console() -> None:
    """T-STRUCT-10: Verify that sentinel values and PII never appear in logs across formats."""
    sentinel_payload = {
        "donor_first_name": "ZZFIRST",
        "donor_last_name": "ZZLAST",
        "phone": "0550123456",
        "email": "donor_sentinel@hospital.dz",
        "password": "sentinel_password_12345",  # pragma: allowlist secret
        "totp_token": "998877",
        "blood_type": "AB+",
        "ip_address": "198.51.100.99",
    }

    for log_format in ("console", "json"):
        settings = Settings(
            app_env="dev",
            log_format=log_format,
            log_level="DEBUG",
        )
        setup_logging(settings)

        # Intercept output from root logger
        root = logging.getLogger()
        log_capture = io.StringIO()
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        handler.stream = log_capture

        test_logger = logging.getLogger("security_audit_test")

        # Emit various log styles: text, args, exception, extras
        test_logger.info("Registration attempt for donor ZZFIRST ZZLAST with phone 0550123456")
        test_logger.warning(
            "Auth failed for %s with email %s and blood %s",
            "ZZFIRST",
            sentinel_payload["email"],
            sentinel_payload["blood_type"],
        )
        test_logger.error(
            "Payload dump: %s",
            sentinel_payload,
        )

        try:
            raise ValueError(
                f"Exception containing {sentinel_payload['password']} and {sentinel_payload['phone']}"
            )
        except ValueError:
            test_logger.exception("Handled unexpected error in donor flow")

        captured = log_capture.getvalue()

        # Assert NONE of the sensitive sentinel values leaked into captured log
        assert "ZZFIRST" not in captured
        assert "ZZLAST" not in captured
        assert "0550123456" not in captured
        assert "donor_sentinel@hospital.dz" not in captured
        assert "sentinel_password_12345" not in captured
        assert "198.51.100.99" not in captured
        assert "AB+" not in captured

        # For JSON format, assert every emitted line parses as valid JSON
        if log_format == "json":
            for line in captured.strip().splitlines():
                if line.strip():
                    parsed = json.loads(line)
                    assert "level" in parsed
                    assert "message" in parsed
                    assert "request_id" in parsed
                    assert "[REDACTED]" in line
