"""Unit tests for password hashing, policy verification, and security primitives (P3.1).

Implements test cases for:
- BR-AUTH-03 & T-AUTH-03: Password policy validation (10-128 chars, common passwords, email/name check).
- BR-AUTH-04 & T-AUTH-04 (★): Argon2id hashing, format verification, rehash detection, and zero password leakage.
- BR-AUTH-05: Dummy verification for constant-time timing attack defense.
"""

import logging

import pytest

from app.core.errors import ValidationError
from app.core.security import (
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

# ==============================================================================
# BR-AUTH-03: Password Policy Validation (T-AUTH-03)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.br("BR-AUTH-03")
def test_br_auth_03_a_password_too_short() -> None:
    """T-AUTH-03-a: Passwords shorter than 10 characters must be rejected with ValidationError."""
    short_passwords = [
        "",
        "1",
        "123456789",  # 9 chars
        "short",
        "abcdefghi",  # 9 chars
    ]
    for pwd in short_passwords:
        with pytest.raises(ValidationError) as exc_info:
            validate_password(pwd)
        assert exc_info.value.code == "validation_error"
        assert exc_info.value.http_status == 422


@pytest.mark.unit
@pytest.mark.br("BR-AUTH-03")
def test_br_auth_03_a_password_too_long() -> None:
    """T-AUTH-03-a: Passwords longer than 128 characters must be rejected with ValidationError."""
    long_pwd = "a" * 129
    with pytest.raises(ValidationError) as exc_info:
        validate_password(long_pwd)
    assert exc_info.value.code == "validation_error"
    assert exc_info.value.http_status == 422


@pytest.mark.unit
@pytest.mark.br("BR-AUTH-03")
def test_br_auth_03_a_common_passwords_rejected() -> None:
    """T-AUTH-03-a: Common and guessable passwords must be rejected."""
    common_samples = [
        "password123",
        "1234567890",
        "admin123456",
        "qwerty12345",
        "letmein1234",
        "iloveyou123",
    ]
    for pwd in common_samples:
        assert is_common_password(pwd) is True
        with pytest.raises(ValidationError) as exc_info:
            validate_password(pwd)
        assert exc_info.value.code == "validation_error"
        assert exc_info.value.http_status == 422


@pytest.mark.unit
@pytest.mark.br("BR-AUTH-03")
def test_br_auth_03_a_password_matching_email_rejected() -> None:
    """T-AUTH-03-a: Password matching user email or its local part must be rejected."""
    email = "supervisor.algiers@bloodday.local"

    # Exact email as password
    with pytest.raises(ValidationError) as exc_info:
        validate_password("supervisor.algiers@bloodday.local", user_email=email)
    assert exc_info.value.code == "validation_error"

    # Case-insensitive email
    with pytest.raises(ValidationError) as exc_info:
        validate_password("Supervisor.Algiers@BloodDay.local", user_email=email)
    assert exc_info.value.code == "validation_error"

    # Containing local part
    with pytest.raises(ValidationError) as exc_info:
        validate_password("supervisor.algiers_2026", user_email=email)
    assert exc_info.value.code == "validation_error"


@pytest.mark.unit
@pytest.mark.br("BR-AUTH-03")
def test_br_auth_03_a_password_matching_user_name_rejected() -> None:
    """T-AUTH-03-a: Password matching user's full name or name tokens must be rejected."""
    name = "Karim Benali"

    # Matching full name
    with pytest.raises(ValidationError) as exc_info:
        validate_password("Karim Benali 123", user_name=name)
    assert exc_info.value.code == "validation_error"

    # Matching name token
    with pytest.raises(ValidationError) as exc_info:
        validate_password("benali_secret_pass", user_name=name)
    assert exc_info.value.code == "validation_error"


@pytest.mark.unit
@pytest.mark.br("BR-AUTH-03")
def test_br_auth_03_b_passphrase_without_special_chars_accepted() -> None:
    """T-AUTH-03-b: Passphrases with letters/spaces only (no special characters) must be accepted."""
    valid_passphrases = [
        "correct horse battery",
        "un mot de passe tres securise",
        "كلمة مرور سرية وقوية جدا",
        "SimplePlainPassphrase123",
    ]
    for pwd in valid_passphrases:
        # Must not raise
        validate_password(pwd, user_email="other@hospital.dz", user_name="Samir")


# ==============================================================================
# BR-AUTH-04: Argon2id Password Hashing & Rehash (T-AUTH-04 ★)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-04")
def test_br_auth_04_a_argon2id_format_and_hashing() -> None:
    """T-AUTH-04-a: Passwords must be hashed using argon2id format ($argon2id$)."""
    raw_password = "correct horse battery"  # pragma: allowlist secret
    hashed = hash_password(raw_password)

    # Assert argon2id prefix per RFC 9106
    assert hashed.startswith("$argon2id$")
    assert "$v=19$" in hashed
    assert "$m=" in hashed
    assert ",t=" in hashed
    assert ",p=" in hashed

    # Verify matching password
    assert verify_password(raw_password, hashed) is True

    # Verify mismatched password
    assert verify_password("wrong horse battery", hashed) is False

    # Verify None or corrupted hash gracefully returns False without raising
    assert verify_password(raw_password, None) is False
    assert verify_password(raw_password, "not_an_argon_hash") is False
    assert verify_password(raw_password, "") is False


@pytest.mark.unit
@pytest.mark.br("BR-AUTH-04")
def test_br_auth_04_b_rehash_detection() -> None:
    """T-AUTH-04-b: Outdated hash parameters must trigger needs_rehash=True."""
    from argon2 import PasswordHasher, Type

    # Current hash should not need rehash
    current_hash = hash_password("valid_test_password_123")  # pragma: allowlist secret
    assert needs_rehash(current_hash) is False

    # Create hash with outdated/weak parameters
    old_hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        hash_len=16,
        salt_len=8,
        type=Type.ID,
    )
    old_hash = old_hasher.hash("valid_test_password_123")  # pragma: allowlist secret

    # Old hash must verify successfully
    assert verify_password("valid_test_password_123", old_hash) is True

    # But needs_rehash must return True so service can upgrade it on login
    assert needs_rehash(old_hash) is True


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-04")
def test_br_auth_04_c_no_password_in_logs_during_hashing(caplog: pytest.LogCaptureFixture) -> None:
    """T-AUTH-04-c: Passwords must never appear in log records during hashing or verification."""
    caplog.set_level(logging.DEBUG)
    sentinel_pwd = "ZZ_SECRET_PASSWORD_12345"  # pragma: allowlist secret

    hashed = hash_password(sentinel_pwd)
    verify_password(sentinel_pwd, hashed)
    verify_password("wrong_attempt", hashed)

    # Ensure sentinel plaintext password does not appear in captured log output
    for record in caplog.records:
        assert sentinel_pwd not in record.getMessage()


# ==============================================================================
# BR-AUTH-05: Constant-Time Dummy Verification (T-AUTH-05-b ★)
# ==============================================================================


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.br("BR-AUTH-05")
def test_br_auth_05_b_dummy_verify_constant_time() -> None:
    """T-AUTH-05-b: dummy_verify_password must run argon2id verification safely and return False."""
    result = dummy_verify_password("nonexistent_user_attempt")
    assert result is False


# ==============================================================================
# Token & Recovery Code Hashing Primitives
# ==============================================================================


@pytest.mark.unit
def test_hash_token_sha256() -> None:
    """Verify SHA-256 token hashing for sessions, invitations, and password reset tokens."""
    token = "raw_sample_token_abc123"
    hashed = hash_token(token)
    assert len(hashed) == 64
    assert all(c in "0123456789abcdef" for c in hashed)

    # Deterministic hashing
    assert hash_token(token) == hashed
    assert hash_token("different_token") != hashed


@pytest.mark.unit
def test_generate_secure_token_and_recovery_code() -> None:
    """Verify secure token generation and 10-character recovery code formatting."""
    token1 = generate_secure_token(32)
    token2 = generate_secure_token(32)
    assert len(token1) >= 40
    assert token1 != token2

    code1 = generate_recovery_code()
    code2 = generate_recovery_code()
    assert len(code1) == 10
    assert len(code2) == 10
    assert code1.isalnum()
    assert code1 != code2
