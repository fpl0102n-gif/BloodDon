"""Core cross-cutting infrastructure: config, database, security, logging, errors."""

from app.core.clock import Clock, FakeClock, SystemClock, get_clock, set_clock
from app.core.config import Settings, get_settings
from app.core.database import check_database_connection, create_db_engine, get_db, metadata
from app.core.errors import AppError, ErrorCode
from app.core.logging import (
    RedactionFilter,
    anonymize_ip,
    get_current_request_id,
    redact_text,
    set_request_id,
    setup_logging,
)
from app.core.ratelimit import (
    FakeRateLimiter,
    InMemoryRateLimiter,
    RateLimiter,
    RateLimitResult,
    get_rate_limiter,
    resolve_client_ip,
    resolve_client_ip_hash,
    set_rate_limiter,
)
from app.core.security import (
    COMMON_PASSWORDS,
    dummy_verify_password,
    generate_recovery_code,
    generate_secure_token,
    hash_password,
    hash_token,
    is_common_password,
    needs_rehash,
    validate_password,
    verify_password,
)

__all__ = [
    "COMMON_PASSWORDS",
    "AppError",
    "Clock",
    "ErrorCode",
    "FakeClock",
    "FakeRateLimiter",
    "InMemoryRateLimiter",
    "RateLimitResult",
    "RateLimiter",
    "RedactionFilter",
    "Settings",
    "SystemClock",
    "anonymize_ip",
    "check_database_connection",
    "create_db_engine",
    "dummy_verify_password",
    "generate_recovery_code",
    "generate_secure_token",
    "get_clock",
    "get_current_request_id",
    "get_db",
    "get_rate_limiter",
    "get_settings",
    "hash_password",
    "hash_token",
    "is_common_password",
    "metadata",
    "needs_rehash",
    "redact_text",
    "resolve_client_ip",
    "resolve_client_ip_hash",
    "set_clock",
    "set_rate_limiter",
    "set_request_id",
    "setup_logging",
    "validate_password",
    "verify_password",
]
