"""Application configuration and settings management.

Implements P1.1 and T-STRUCT-08 per docs/08-setup-env.md and docs/05-architecture.md.
Provides fail-fast validation in production (APP_ENV=prod) for missing or weak secrets.
"""

from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KNOWN_INSECURE_SECRETS: frozenset[str] = frozenset(
    {
        "0" * 64,
        "0" * 32,
        "change-me",
        "changeme",
        "secret",
        "password",
        "dummy_fernet_key_base64_must_be_32_bytes_safe_url=",
    }
)


def is_insecure_secret(val: str) -> bool:
    """Check if a secret matches known placeholders, empty values, or all zeros."""
    if not val:
        return True
    if val in KNOWN_INSECURE_SECRETS:
        return True
    return set(val) <= {"0"}


class Settings(BaseSettings):
    """Central application settings validated at boot time."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --------------------------------------------------------------------------
    # 1. General & Operational Flags
    # --------------------------------------------------------------------------
    app_env: Literal["dev", "test", "prod"] = Field(
        default="dev",
        description="Application environment mode",
    )
    app_timezone: str = Field(
        default="Africa/Algiers",
        description="Operational timezone (decision D-36, BR-GEN-04)",
    )
    public_base_url: str = Field(
        default="http://localhost:8000",
        description="Public base URL for invite links, Open Graph, and origin verification",
    )
    docs_enabled: bool = Field(
        default=True,
        description="Enable interactive OpenAPI documentation (/docs, /redoc). Must be False in prod.",
    )
    emergency_enabled: bool = Field(
        default=True,
        description="Kill switch for emergency donor board and reveal API (decision D-62)",
    )
    pledges_enabled: bool = Field(
        default=True,
        description="Kill switch for visitor attendance pledges (decision D-62)",
    )
    emergency_min_days_since_donation: int = Field(
        default=0,
        ge=0,
        description="Minimum days since last donation before donor appears in emergency board (D-37)",
    )
    static_dir: str = Field(
        default="./app/web/static",
        description="Directory serving local static CSS, JS, and font assets (decision D-71)",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="DEBUG",
        description="Minimum log level threshold",
    )
    log_format: Literal["console", "json"] = Field(
        default="console",
        description="Log output formatting (json required for production)",
    )
    error_reporting_dsn: str | None = Field(
        default=None,
        description="Optional error monitoring DSN (must never receive donor PII)",
    )

    # --------------------------------------------------------------------------
    # 2. Cryptographic Secrets
    # --------------------------------------------------------------------------
    secret_key: str = Field(
        default="0" * 64,
        description="Cryptographic secret key for CSRF tokens and internal signatures (64 hex chars)",
    )
    totp_encryption_key: str = Field(
        default="dummy_fernet_key_base64_must_be_32_bytes_safe_url=",
        description="Fernet 32 url-safe base64-encoded key for TOTP secrets at rest",
    )
    ip_hash_salt: str = Field(
        default="0" * 32,
        description="Cryptographic salt for anonymizing client IP addresses in rate limiters and logs",
    )

    # --------------------------------------------------------------------------
    # 3. Database Configuration
    # --------------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://app_user:app_user_dev_password@localhost:5432/bloodday",  # pragma: allowlist secret
        description="PostgreSQL connection string for application runtime (app_user role)",
    )
    migration_database_url: str | None = Field(
        default=None,
        description="PostgreSQL connection string for Alembic migrations (app_migrator role)",
    )
    test_database_url: str | None = Field(
        default="postgresql+psycopg://app_user:app_user_dev_password@localhost:5433/bloodday_test",  # pragma: allowlist secret
        description="Dedicated test database on tmpfs for isolated test runs",
    )
    db_pool_size: int = Field(default=5, ge=1, description="Database connection pool size")
    db_max_overflow: int = Field(default=5, ge=0, description="Max overflow connections")
    db_pool_timeout: int = Field(default=10, ge=1, description="Connection pool timeout in seconds")
    db_sslmode: str | None = Field(
        default=None,
        description="SSL mode for PostgreSQL (required in managed production)",
    )

    # --------------------------------------------------------------------------
    # 4. Sessions & Security Parameters
    # --------------------------------------------------------------------------
    cookie_secure: bool = Field(
        default=False,
        description="Enforce Secure flag on cookies. Must be True in prod.",
    )
    cookie_name: str = Field(
        default="session",
        description="Session cookie name (__Host-session in prod)",
    )
    session_idle_supervisor_min: int = Field(
        default=240,
        ge=1,
        description="Supervisor session idle timeout in minutes (BR-AUTH-12)",
    )
    session_absolute_supervisor_min: int = Field(
        default=840,
        ge=1,
        description="Supervisor session absolute timeout in minutes (14 hours, BR-AUTH-12)",
    )
    session_idle_admin_min: int = Field(
        default=30,
        ge=1,
        description="Admin session idle timeout in minutes (BR-AUTH-12)",
    )
    session_absolute_admin_min: int = Field(
        default=480,
        ge=1,
        description="Admin session absolute timeout in minutes (8 hours, BR-AUTH-12)",
    )
    reauth_window_min: int = Field(
        default=10,
        ge=1,
        description="Re-authentication fresh confirmation window for sensitive actions (BR-AUTH-14)",
    )
    login_max_failures: int = Field(
        default=5,
        ge=1,
        description="Failed login attempts before temporary account lockout (BR-AUTH-06)",
    )
    login_lock_base_min: int = Field(
        default=15,
        ge=1,
        description="Initial account lockout duration in minutes (BR-AUTH-06)",
    )
    login_lock_max_min: int = Field(
        default=60,
        ge=1,
        description="Maximum exponential lockout cap in minutes (BR-AUTH-06)",
    )
    invite_ttl_hours: int = Field(
        default=72,
        ge=1,
        description="Staff invitation token expiration in hours (BR-AUTH-02)",
    )
    reset_ttl_min: int = Field(
        default=60,
        ge=1,
        description="Password reset token expiration in minutes (BR-AUTH-15)",
    )
    trusted_proxies: list[str] | str = Field(
        default_factory=list,
        description="Trusted reverse proxy IP addresses/subnets (BR-RATE-10)",
    )
    allowed_origins: list[str] | str = Field(
        default_factory=list,
        description="Allowed Origin headers for write requests (no open CORS)",
    )

    # --------------------------------------------------------------------------
    # 5. Rate Limiting Limits
    # --------------------------------------------------------------------------
    rate_limit_backend: Literal["memory", "db"] = Field(
        default="memory",
        description="Rate limiter storage backend (memory for single process)",
    )
    rate_pledge_per_hour: int = Field(default=20, ge=1, description="BR-RATE-03")
    rate_emergency_list_per_hour: int = Field(default=60, ge=1, description="BR-RATE-04")
    rate_emergency_reveal_per_hour: int = Field(default=10, ge=1, description="BR-RATE-05")
    rate_forgot_per_hour: int = Field(default=5, ge=1, description="BR-RATE-06")
    rate_login_per_15min: int = Field(default=10, ge=1, description="BR-RATE-01")

    # --------------------------------------------------------------------------
    # 6. File & Photo Storage
    # --------------------------------------------------------------------------
    storage_backend: Literal["local", "s3"] = Field(
        default="local",
        description="Storage provider (local for disk, s3 for object store)",
    )
    storage_local_path: str = Field(
        default="./var/uploads",
        description="Local directory for processed photos",
    )
    storage_public_base_url: str = Field(
        default="/media",
        description="Public URL prefix for serving stored media",
    )
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str | None = None
    max_upload_bytes: int = Field(
        default=5242880,
        ge=1,
        description="Max upload size (5MB, BR-PHOTO-06)",
    )
    image_max_side_px: int = Field(
        default=1600,
        ge=1,
        description="Max image side dimension in pixels (BR-PHOTO-07)",
    )
    image_max_pixels: int = Field(
        default=40000000,
        ge=1,
        description="Max pixel count safeguard against decompression bombs",
    )

    # --------------------------------------------------------------------------
    # 7. Transactional Email
    # --------------------------------------------------------------------------
    mail_backend: Literal["console", "smtp"] = Field(
        default="console",
        description="Email delivery client (console for dev, smtp for prod)",
    )
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_tls: bool = True
    mail_from: str = Field(
        default="noreply@blood-donation.local",
        description="Sender email address for system invitations and password resets",
    )

    # --------------------------------------------------------------------------
    # 8. Data Retention & Anonymization
    # --------------------------------------------------------------------------
    retention_days: int = Field(
        default=365,
        ge=1,
        description="Donor PII retention period in days (1 year, BR-PRIV-01)",
    )
    reveal_log_retention_days: int = Field(
        default=7,
        ge=1,
        description="Phone reveal audit log retention period in days (BR-PRIV-11)",
    )

    # --------------------------------------------------------------------------
    # Validators
    # --------------------------------------------------------------------------
    @field_validator("trusted_proxies", "allowed_origins", mode="after")
    @classmethod
    def parse_string_to_list(cls, value: Any) -> list[str]:
        """Convert string or comma-separated list into a clean list of strings."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @model_validator(mode="after")
    def validate_production_and_cross_fields(self) -> "Settings":
        """Strict fail-fast validation for production and default field derivation."""
        # 1. Derive allowed_origins from public_base_url if not explicitly provided or defaulted to dev in prod
        if not self.allowed_origins or (
            self.app_env == "prod" and self.allowed_origins == ["http://localhost:8000"]
        ):
            self.allowed_origins = [self.public_base_url.rstrip("/")]

        # 2. In dev/test, validate Fernet key structure only if not using default dummy key
        if self.totp_encryption_key != "dummy_fernet_key_base64_must_be_32_bytes_safe_url=":
            try:
                Fernet(self.totp_encryption_key.encode("ascii"))
            except Exception as exc:
                raise ValueError(
                    f"TOTP_ENCRYPTION_KEY is not a valid 32 url-safe base64-encoded Fernet key: {exc}"
                ) from exc

        # 3. Production Hardening Gates (APP_ENV=prod)
        if self.app_env == "prod":
            errors: list[str] = []

            # 3a. SECRET_KEY validation
            if is_insecure_secret(self.secret_key) or len(self.secret_key) < 64:
                errors.append(
                    "SECRET_KEY must be a cryptographically secure random hex string "
                    "of at least 64 characters (32 bytes entropy). Default or weak keys are forbidden in prod."
                )

            # 3b. TOTP_ENCRYPTION_KEY validation
            if is_insecure_secret(self.totp_encryption_key):
                errors.append(
                    "TOTP_ENCRYPTION_KEY must be a securely generated Fernet key in prod. "
                    "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                )
            else:
                try:
                    Fernet(self.totp_encryption_key.encode("ascii"))
                except Exception as exc:
                    errors.append(f"TOTP_ENCRYPTION_KEY is invalid for Fernet: {exc}")

            # 3c. IP_HASH_SALT validation
            if is_insecure_secret(self.ip_hash_salt) or len(self.ip_hash_salt) < 32:
                errors.append(
                    "IP_HASH_SALT must be a cryptographically secure random hex string "
                    "of at least 32 characters (16 bytes entropy) in prod."
                )

            # 3d. DOCS_ENABLED must be False in production (T-STRUCT-08)
            if self.docs_enabled:
                errors.append(
                    "DOCS_ENABLED must be set to False in production to disable public /docs and /openapi.json."
                )

            # 3e. COOKIE_SECURE must be True in production (T-STRUCT-08)
            if not self.cookie_secure:
                errors.append(
                    "COOKIE_SECURE must be True in production to enforce HTTPS-only cookie transmission."
                )

            # 3f. COOKIE_NAME in prod should use __Host- prefix
            if not self.cookie_name.startswith("__Host-"):
                self.cookie_name = f"__Host-{self.cookie_name.lstrip('_')}"

            # 3g. PUBLIC_BASE_URL must use https://
            parsed_url = urlparse(self.public_base_url)
            if parsed_url.scheme != "https":
                errors.append(
                    f"PUBLIC_BASE_URL must use the 'https://' scheme in production (got: {self.public_base_url})."
                )

            # 3h. DATABASE_URL must not contain obvious development defaults
            if (
                "app_user_dev_password" in self.database_url
                or "localhost:5432" in self.database_url
            ):
                errors.append(
                    "DATABASE_URL contains development credentials or localhost in production. "
                    "Provide a secure managed PostgreSQL connection string."
                )

            # 3i. Log format in production defaults to json
            if self.log_format != "json":
                self.log_format = "json"

            # 3j. Storage backend validation in production
            if self.storage_backend == "s3" and (
                not self.s3_bucket or not self.s3_access_key or not self.s3_secret_key
            ):
                errors.append(
                    "S3 storage backend selected in production but S3_BUCKET, S3_ACCESS_KEY, "
                    "or S3_SECRET_KEY is missing."
                )

            # 3k. Mail backend validation in production
            if self.mail_backend == "smtp" and (not self.smtp_host or not self.smtp_port):
                errors.append(
                    "SMTP mail backend selected in production but SMTP_HOST or SMTP_PORT is missing."
                )

            if errors:
                bullet_list = "\n - ".join(errors)
                raise ValueError(f"CRITICAL CONFIGURATION ERROR (APP_ENV=prod):\n - {bullet_list}")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retrieve cached application settings instance."""
    return Settings()


def check_config(settings: Settings | None = None) -> list[str]:
    """Inspect application settings and return any operational warnings.

    Raises ValueError if critical production configuration rules fail.
    """
    cfg = settings or get_settings()
    warnings: list[str] = []

    if cfg.app_env in {"dev", "test"}:
        if is_insecure_secret(cfg.secret_key):
            warnings.append("Using default insecure SECRET_KEY (acceptable only in dev/test).")
        if is_insecure_secret(cfg.totp_encryption_key):
            warnings.append(
                "Using default dummy TOTP_ENCRYPTION_KEY (acceptable only in dev/test)."
            )
        if not cfg.cookie_secure:
            warnings.append("COOKIE_SECURE is False (acceptable only for local HTTP development).")

    if cfg.emergency_min_days_since_donation == 0:
        warnings.append(
            "EMERGENCY_MIN_DAYS_SINCE_DONATION is 0 (pending blood center medical guidance per D-37)."
        )

    return warnings
