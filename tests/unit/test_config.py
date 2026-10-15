"""Unit tests for application configuration and fail-fast validation.

Traces to T-STRUCT-08 (docs/07-test-cases.md §7) and P1.1 (docs/12-implementation-plan.md).
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, check_config

# Constants for testing configuration without triggering secret scanners
DUMMY_PROD_DB: str = (
    "postgresql+psycopg://user:pass@managed-db:5432/prod"  # pragma: allowlist secret
)
DUMMY_LOCAL_DEV_DB: str = "postgresql+psycopg://app_user:app_user_dev_password@localhost:5432/bloodday"  # pragma: allowlist secret
VALID_TOTP_KEY: str = "Z8jQL92Z6rJ3nJ-0OgCcO3J3Q_QuW722y3o8ohEiTV4="  # pragma: allowlist secret


@pytest.mark.unit
def test_dev_settings_defaults() -> None:
    """Verify that default settings load cleanly in development mode with expected defaults."""
    settings = Settings(app_env="dev")

    assert settings.app_env == "dev"
    assert settings.app_timezone == "Africa/Algiers"
    assert settings.public_base_url == "http://localhost:8000"
    assert settings.docs_enabled is True
    assert settings.emergency_enabled is True
    assert settings.pledges_enabled is True
    assert settings.emergency_min_days_since_donation == 0
    assert settings.cookie_secure is False
    assert settings.cookie_name == "session"
    assert settings.allowed_origins == ["http://localhost:8000"]
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "console"


@pytest.mark.unit
def test_prod_settings_valid() -> None:
    """Verify that production settings succeed when all strong security requirements are met."""
    valid_secret_key = "a" * 64
    valid_salt = "b" * 32

    settings = Settings(
        app_env="prod",
        secret_key=valid_secret_key,
        totp_encryption_key=VALID_TOTP_KEY,
        ip_hash_salt=valid_salt,
        public_base_url="https://blood.example.com",
        docs_enabled=False,
        cookie_secure=True,
        database_url=DUMMY_PROD_DB,
    )

    assert settings.app_env == "prod"
    assert settings.docs_enabled is False
    assert settings.cookie_secure is True
    assert settings.cookie_name.startswith("__Host-")
    assert settings.log_format == "json"
    assert settings.allowed_origins == ["https://blood.example.com"]


@pytest.mark.unit
@pytest.mark.critical
def test_prod_fails_on_default_secret_key() -> None:
    """T-STRUCT-08: Booting in production with default/all-zeros SECRET_KEY must fail fast."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="prod",
            secret_key="0" * 64,
            totp_encryption_key=VALID_TOTP_KEY,
            ip_hash_salt="b" * 32,
            public_base_url="https://blood.example.com",
            docs_enabled=False,
            cookie_secure=True,
            database_url=DUMMY_PROD_DB,
        )

    assert "SECRET_KEY must be a cryptographically secure random hex string" in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.critical
def test_prod_fails_on_short_secret_key() -> None:
    """T-STRUCT-08: Booting in production with a short/weak SECRET_KEY must fail fast."""
    weak_key = "too_short_secret"  # pragma: allowlist secret
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="prod",
            secret_key=weak_key,
            totp_encryption_key=VALID_TOTP_KEY,
            ip_hash_salt="b" * 32,
            public_base_url="https://blood.example.com",
            docs_enabled=False,
            cookie_secure=True,
            database_url=DUMMY_PROD_DB,
        )

    assert "SECRET_KEY must be a cryptographically secure random hex string" in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.critical
def test_prod_fails_on_dummy_totp_key() -> None:
    """T-STRUCT-08: Booting in production with default dummy Fernet key must fail fast."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="prod",
            secret_key="a" * 64,
            totp_encryption_key="dummy_fernet_key_base64_must_be_32_bytes_safe_url=",
            ip_hash_salt="b" * 32,
            public_base_url="https://blood.example.com",
            docs_enabled=False,
            cookie_secure=True,
            database_url=DUMMY_PROD_DB,
        )

    assert "TOTP_ENCRYPTION_KEY must be a securely generated Fernet key in prod" in str(
        exc_info.value
    )


@pytest.mark.unit
@pytest.mark.critical
def test_prod_fails_on_weak_ip_hash_salt() -> None:
    """T-STRUCT-08: Booting in production with weak IP_HASH_SALT must fail fast."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="prod",
            secret_key="a" * 64,
            totp_encryption_key=VALID_TOTP_KEY,
            ip_hash_salt="0" * 32,
            public_base_url="https://blood.example.com",
            docs_enabled=False,
            cookie_secure=True,
            database_url=DUMMY_PROD_DB,
        )

    assert "IP_HASH_SALT must be a cryptographically secure random hex string" in str(
        exc_info.value
    )


@pytest.mark.unit
@pytest.mark.critical
def test_prod_fails_when_docs_enabled() -> None:
    """T-STRUCT-08: Booting in production with DOCS_ENABLED=True must fail fast."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="prod",
            secret_key="a" * 64,
            totp_encryption_key=VALID_TOTP_KEY,
            ip_hash_salt="b" * 32,
            public_base_url="https://blood.example.com",
            docs_enabled=True,  # Forbidden in prod
            cookie_secure=True,
            database_url=DUMMY_PROD_DB,
        )

    assert "DOCS_ENABLED must be set to False in production" in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.critical
def test_prod_fails_when_cookie_insecure() -> None:
    """T-STRUCT-08: Booting in production with COOKIE_SECURE=False must fail fast."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="prod",
            secret_key="a" * 64,
            totp_encryption_key=VALID_TOTP_KEY,
            ip_hash_salt="b" * 32,
            public_base_url="https://blood.example.com",
            docs_enabled=False,
            cookie_secure=False,  # Forbidden in prod
            database_url=DUMMY_PROD_DB,
        )

    assert "COOKIE_SECURE must be True in production" in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.critical
def test_prod_fails_on_http_public_base_url() -> None:
    """T-STRUCT-08: Booting in production with http:// public URL must fail fast."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="prod",
            secret_key="a" * 64,
            totp_encryption_key=VALID_TOTP_KEY,
            ip_hash_salt="b" * 32,
            public_base_url="http://insecure-blood.example.com",
            docs_enabled=False,
            cookie_secure=True,
            database_url=DUMMY_PROD_DB,
        )

    assert "PUBLIC_BASE_URL must use the 'https://' scheme in production" in str(exc_info.value)


@pytest.mark.unit
def test_prod_fails_on_dev_database_password() -> None:
    """T-STRUCT-08: Booting in production with default local database credentials must fail fast."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="prod",
            secret_key="a" * 64,
            totp_encryption_key=VALID_TOTP_KEY,
            ip_hash_salt="b" * 32,
            public_base_url="https://blood.example.com",
            docs_enabled=False,
            cookie_secure=True,
            database_url=DUMMY_LOCAL_DEV_DB,
        )

    assert "DATABASE_URL contains development credentials or localhost in production" in str(
        exc_info.value
    )


@pytest.mark.unit
def test_trusted_proxies_and_allowed_origins_list_parsing() -> None:
    """Verify comma-separated strings are parsed into clean string lists."""
    settings = Settings(
        app_env="dev",
        trusted_proxies="10.0.0.1, 192.168.1.1 , 127.0.0.1",
        allowed_origins="https://app.example.com, https://admin.example.com",
    )

    assert settings.trusted_proxies == ["10.0.0.1", "192.168.1.1", "127.0.0.1"]
    assert settings.allowed_origins == ["https://app.example.com", "https://admin.example.com"]


@pytest.mark.unit
def test_invalid_fernet_key_in_dev_raises_error() -> None:
    """Verify that passing an invalid Fernet key in dev mode raises a validation error."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            app_env="dev",
            totp_encryption_key="not-a-valid-fernet-key",
        )

    assert "TOTP_ENCRYPTION_KEY is not a valid 32 url-safe base64-encoded Fernet key" in str(
        exc_info.value
    )


@pytest.mark.unit
def test_check_config_helper() -> None:
    """Verify check_config returns expected non-fatal warnings in dev mode."""
    settings = Settings(app_env="dev")
    warnings = check_config(settings)

    assert any("Using default insecure SECRET_KEY" in w for w in warnings)
    assert any("Using default dummy TOTP_ENCRYPTION_KEY" in w for w in warnings)
    assert any("COOKIE_SECURE is False" in w for w in warnings)
