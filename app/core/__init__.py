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
    InMemoryRateLimiter,
    RateLimiter,
    RateLimitResult,
    get_rate_limiter,
    resolve_client_ip,
    resolve_client_ip_hash,
    set_rate_limiter,
)

__all__ = [
    "AppError",
    "Clock",
    "ErrorCode",
    "FakeClock",
    "InMemoryRateLimiter",
    "RateLimitResult",
    "RateLimiter",
    "RedactionFilter",
    "Settings",
    "SystemClock",
    "anonymize_ip",
    "check_database_connection",
    "create_db_engine",
    "get_clock",
    "get_current_request_id",
    "get_db",
    "get_rate_limiter",
    "get_settings",
    "metadata",
    "redact_text",
    "resolve_client_ip",
    "resolve_client_ip_hash",
    "set_clock",
    "set_rate_limiter",
    "set_request_id",
    "setup_logging",
]
