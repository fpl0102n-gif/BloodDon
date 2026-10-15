"""Structured logging and central PII redaction engine.

Implements P1.5 per docs/05-architecture.md §15 and CLAUDE.md §6 Golden Rule 9:
- Central RedactionFilter masks sensitive donor and security data from all log messages,
  arguments, and formatted tracebacks (phones, emails, passwords, tokens, blood types, IPs,
  and sentinel test values).
- Request ID context propagation binds request correlation IDs to every emitted log entry.
- Environment-aware formatters:
  - JSON structured logging in production.
  - Human-readable console formatting in development.
- IP anonymization hashing helper using HMAC/SHA-256 with salt.
"""

import hashlib
import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings

# Context variable for current request tracking ID
request_id_ctx: ContextVar[str | None] = ContextVar("request_id_ctx", default=None)


def set_request_id(req_id: str | None) -> None:
    """Set the correlation request ID for the current async task / thread context."""
    request_id_ctx.set(req_id)


def get_current_request_id() -> str | None:
    """Retrieve the correlation request ID from context."""
    return request_id_ctx.get()


def anonymize_ip(ip: str, salt: str | None = None) -> str:
    """Hash client IP address with salt for logging and rate limiting per docs/05-architecture.md §15."""
    if not ip or ip in ("-", "unknown"):
        return "unknown"
    effective_salt = salt if salt is not None else get_settings().ip_hash_salt
    digest = hashlib.sha256(f"{effective_salt}:{ip}".encode()).hexdigest()
    return f"ip_hash_{digest[:16]}"


# ==============================================================================
# Sensitive Keys & Redaction Patterns
# ==============================================================================

SENSITIVE_KEY_NAMES: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "password_hash",
        "secret",
        "secret_key",
        "totp_secret",
        "totp_secret_enc",
        "totp_code",
        "recovery_code",
        "recovery_codes",
        "token",
        "user_token",
        "token_hash",
        "access_token",
        "refresh_token",
        "auth_token",
        "authorization",
        "cookie",
        "set_cookie",
        "phone",
        "phone_number",
        "email",
        "blood_type",
        "national_id",
        "client_ip",
        "ip_address",
        "remote_addr",
    }
)

# Compiled redaction regex patterns applied to all string text
REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 1. Sentinel test values (CLAUDE.md §8)
    (re.compile(r"\bZZFIRST\b", re.IGNORECASE), "[REDACTED]"),
    (re.compile(r"\bZZLAST\b", re.IGNORECASE), "[REDACTED]"),
    (re.compile(r"\b0550123456\b"), "[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9_]*sentinel[A-Za-z0-9_]*\b", re.IGNORECASE), "[REDACTED]"),
    # 2. Email addresses
    (
        re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            re.IGNORECASE,
        ),
        "[REDACTED]",
    ),
    # 3. Phone numbers: Algerian formats (05/06/07XXXXXXXX, +213...) and standard international numbers
    (
        re.compile(r"(?:\+213|00213|0)\s*[567](?:[\s.-]*\d){8}\b"),
        "[REDACTED]",
    ),
    (
        re.compile(r"\+?[0-9]{9,15}\b"),
        "[REDACTED]",
    ),
    # 4. Bearer tokens and Authorization headers
    (
        re.compile(r"(?i)\bbearer\s+[a-z0-9\-._~+/]+=*"),
        "Bearer [REDACTED]",
    ),
    # 5. Key-value secrets in text (e.g. password=..., secret=...)
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|totp|recovery_code|salt)\s*[:=]\s*['\"]?[^\s,'\"]+['\"]?"
        ),
        r"\1=[REDACTED]",
    ),
    # 6. IPv4 addresses (excluding standard 0.0.0.0 and 127.0.0.1 for local services)
    (
        re.compile(
            r"\b(?!127\.0\.0\.1|0\.0\.0\.0)(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
        ),
        "[REDACTED]",
    ),
    # 7. Blood types when formatted with sign (A+, B-, AB+, O-, etc.)
    (
        re.compile(r"\b(A|B|AB|O)[+-](?!\w)"),
        "[REDACTED]",
    ),
]


def redact_text(text: str) -> str:
    """Apply all central redaction regex patterns to input text."""
    if not text:
        return text
    result = text
    for pattern, replacement in REDACTION_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_object(obj: Any) -> Any:
    """Recursively traverse and redact sensitive keys and values from structures."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]" if str(k).lower() in SENSITIVE_KEY_NAMES else redact_object(v)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple, set)):
        items = [redact_object(item) for item in obj]
        if isinstance(obj, tuple):
            return tuple(items)
        if isinstance(obj, set):
            return set(items)
        return items
    return obj


# ==============================================================================
# Logging Filter & Formatters
# ==============================================================================


class RedactionFilter(logging.Filter):
    """Central logging filter intercepting and redacting all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Attach request_id if not present
        if not hasattr(record, "request_id") or not record.request_id:
            record.request_id = get_current_request_id() or "-"

        # Redact main message string
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)

        # Redact positional arguments (record.args)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_object(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(redact_object(arg) for arg in record.args)

        return True


class JSONFormatter(logging.Formatter):
    """Structured JSON formatter for production log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        # Format exception if present and redact its traceback
        exc_text: str | None = None
        if record.exc_info:
            exc_raw = self.formatException(record.exc_info)
            exc_text = redact_text(exc_raw)

        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }

        if exc_text:
            log_data["exc_info"] = exc_text

        # Include custom extra fields if attached to record
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "request_id",
            "message",
        }
        extras = {
            k: redact_object(v)
            for k, v in record.__dict__.items()
            if k not in standard_attrs and not k.startswith("_")
        }
        if extras:
            log_data["extra"] = extras

        return json.dumps(log_data, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Readable log formatter for local development and testing."""

    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        req_id = getattr(record, "request_id", "-")
        formatted = (
            f"{dt} [{record.levelname:<7}] [{record.name}] [req_id={req_id}] {record.getMessage()}"
        )
        if record.exc_info:
            exc_raw = self.formatException(record.exc_info)
            formatted = f"{formatted}\n{redact_text(exc_raw)}"
        return formatted


def setup_logging(settings: Settings | None = None) -> None:
    """Configure root and application logging handlers, level, and redaction filters."""
    cfg = settings or get_settings()

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))

    # Remove existing handlers to prevent duplicate output
    root_logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter())

    if cfg.log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(ConsoleFormatter())

    root_logger.addHandler(handler)
