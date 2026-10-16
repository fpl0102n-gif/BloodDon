"""Cryptographic security utilities, password hashing, and token generation.

Implements P3.1 per:
- docs/03-auth-permissions.md §5 & §6 (argon2id password hashing, SHA-256 token hashing).
- docs/06-business-rules.md §11 (BR-AUTH-03 password policy, BR-AUTH-04 argon2id, BR-AUTH-05 dummy verification).
- docs/07-test-cases.md §5.10 (T-AUTH-03, T-AUTH-04, T-AUTH-05-b).
"""

import contextlib
import hashlib
import re
import secrets
from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from app.core.errors import ValidationError

# ==============================================================================
# Common Passwords Blacklist (BR-AUTH-03 / T-AUTH-03-a)
# ==============================================================================

COMMON_PASSWORDS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "password123",
        "password1234",
        "1234567890",
        "123456789012",
        "admin123456",
        "administrator",
        "qwerty12345",
        "letmein1234",
        "iloveyou123",
        "welcome1234",
        "blooddonation",
        "bloodday2026",
        "supervisor123",
        "medicalclub123",
        "hospital1234",
        "change_me_123",
        "changeme1234",
        "secretpassword",
        "passphrase123",
    }
)

# ==============================================================================
# Argon2id Hasher Configuration (RFC 9106 / OWASP recommended parameters)
# ==============================================================================

# Time cost: 3 iterations
# Memory cost: 65536 KiB (64 MiB)
# Parallelism: 4 threads
# Output hash length: 32 bytes
# Salt length: 16 bytes
_HASHER: Final[PasswordHasher] = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# Pre-computed dummy argon2id hash for constant-time dummy verification (BR-AUTH-05)
# Prevents user enumeration attacks through response timing differences
_DUMMY_HASH: Final[str] = (
    "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQAAAAAAAAAAA$4WvQ9W8Wz2oJ7n9R/P4A9B1C2D3E4F5G6H7I8J9K0L"  # pragma: allowlist secret
)


# ==============================================================================
# Password Policy Validation (BR-AUTH-03 / T-AUTH-03)
# ==============================================================================


def is_common_password(password: str) -> bool:
    """Check if password matches common leaked/guessable passwords (case-insensitive)."""
    return password.strip().lower() in COMMON_PASSWORDS


def validate_password(
    password: str,
    *,
    user_email: str | None = None,
    user_name: str | None = None,
) -> None:
    """Validate password according to BR-AUTH-03 policy.

    Rules:
    - Minimum length: 10 characters.
    - Maximum length: 128 characters.
    - Rejects common passwords.
    - Rejects passwords containing user's email, local part, or name tokens.
    - Does NOT require special characters or arbitrary complexity.

    Raises:
        ValidationError: If password fails any policy rule.
    """
    # 1. Length bounds
    if len(password) < 10:
        raise ValidationError("Password must be at least 10 characters long.")
    if len(password) > 128:
        raise ValidationError("Password must not exceed 128 characters.")

    pwd_lower = password.lower()

    # 2. Common password rejection
    if is_common_password(password):
        raise ValidationError("Password is too common and easily guessable.")

    # 3. User email matching check
    if user_email:
        clean_email = user_email.strip().lower()
        if clean_email in pwd_lower:
            raise ValidationError("Password must not contain your email address.")

        # Check local part (e.g., 'karim' in 'karim@hospital.dz')
        local_part = clean_email.split("@")[0]
        if len(local_part) >= 3 and local_part in pwd_lower:
            raise ValidationError("Password must not contain your username or email prefix.")

    # 4. User name matching check
    if user_name:
        clean_name = user_name.strip().lower()
        if len(clean_name) >= 3 and clean_name in pwd_lower:
            raise ValidationError("Password must not contain your full name.")

        # Check individual name tokens (words with >= 3 characters)
        tokens = re.findall(r"\w+", clean_name)
        for token in tokens:
            if len(token) >= 3 and token in pwd_lower:
                raise ValidationError("Password must not contain parts of your name.")


# ==============================================================================
# Argon2id Hashing & Verification (BR-AUTH-04 / T-AUTH-04)
# ==============================================================================


def hash_password(password: str) -> str:
    """Hash plaintext password using argon2id with configured salt and cost parameters."""
    return _HASHER.hash(password)


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    """Verify plaintext password against an argon2id hash.

    Returns False safely without raising if hash is None, corrupted, or does not match.
    """
    if not hashed_password or not plain_password:
        return False
    try:
        return _HASHER.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Check whether stored hash was created with outdated argon2 parameters."""
    try:
        return _HASHER.check_needs_rehash(hashed_password)
    except Exception:
        return True


def dummy_verify_password(password: str = "dummy_password_input") -> bool:  # noqa: S107
    """Execute dummy argon2id verification for constant-time defense against user enumeration (BR-AUTH-05).

    Always returns False.
    """
    with contextlib.suppress(Exception):
        _HASHER.verify(_DUMMY_HASH, password)
    return False


# ==============================================================================
# Token & Recovery Code Cryptographic Primitives (D-07 / BR-AUTH-02 / BR-AUTH-10)
# ==============================================================================


def hash_token(raw_token: str) -> str:
    """Compute standard SHA-256 hex digest for storage of one-time tokens and session IDs."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_secure_token(nbytes: int = 32) -> str:
    """Generate cryptographically secure URL-safe random token."""
    return secrets.token_urlsafe(nbytes)


def generate_recovery_code(length: int = 10) -> str:
    """Generate unambiguous 10-character alphanumeric MFA backup recovery code (BR-AUTH-10)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # pragma: allowlist secret
    return "".join(secrets.choice(alphabet) for _ in range(length))
