"""Test factories for users, tokens, recovery codes, and auth sessions."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.user import AuthSession, MfaRecoveryCode, User, UserToken

DEFAULT_TEST_PASSWORD_HASH: str = "$argon2id$v=19$m=65536,t=3,p=4$fake_salt$fake_hash"
_user_counter = 0


def create_user(
    session: Session | None = None,
    *,
    id_: uuid.UUID | None = None,
    email: str | None = None,
    full_name: str = "مستخدم تجريبي",
    password_hash: str | None = DEFAULT_TEST_PASSWORD_HASH,
    role: str = "admin",
    site_id: uuid.UUID | None = None,
    is_active: bool = True,
    must_change_password: bool = False,
    totp_secret_enc: str | None = None,
    totp_enabled: bool = False,
    totp_last_used_step: int | None = None,
    failed_login_count: int = 0,
    locked_until: datetime | None = None,
    last_login_at: datetime | None = None,
    **kwargs: Any,
) -> User:
    """Create a User model instance obeying role/site constraints."""
    global _user_counter
    _user_counter += 1

    if email is None:
        email = f"user_{_user_counter}_{uuid.uuid4().hex[:6]}@example.com"

    # Supervisor requires a physical site_id per ck_users_supervisor_site
    if role == "supervisor" and site_id is None:
        site_id = uuid.uuid4()

    user = User(
        id=id_ or uuid.uuid4(),
        email=email.lower(),
        full_name=full_name,
        password_hash=password_hash,
        role=role,
        site_id=site_id,
        is_active=is_active,
        must_change_password=must_change_password,
        totp_secret_enc=totp_secret_enc,
        totp_enabled=totp_enabled,
        totp_last_used_step=totp_last_used_step,
        failed_login_count=failed_login_count,
        locked_until=locked_until,
        last_login_at=last_login_at,
        **kwargs,
    )

    if session is not None:
        session.add(user)
        session.flush()

    return user


def create_user_token(
    session: Session | None = None,
    *,
    user_id: uuid.UUID,
    id_: uuid.UUID | None = None,
    purpose: str = "invite",
    token_hash: str | None = None,
    expires_at: datetime | None = None,
    used_at: datetime | None = None,
    **kwargs: Any,
) -> UserToken:
    """Create a UserToken instance for invitation or password reset."""
    if token_hash is None:
        token_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(days=3)

    token = UserToken(
        id=id_ or uuid.uuid4(),
        user_id=user_id,
        purpose=purpose,
        token_hash=token_hash,
        expires_at=expires_at,
        used_at=used_at,
        **kwargs,
    )

    if session is not None:
        session.add(token)
        session.flush()

    return token


def create_mfa_recovery_code(
    session: Session | None = None,
    *,
    user_id: uuid.UUID,
    id_: uuid.UUID | None = None,
    code_hash: str | None = None,
    used_at: datetime | None = None,
    **kwargs: Any,
) -> MfaRecoveryCode:
    """Create a hashed MFA recovery code instance."""
    if code_hash is None:
        code_hash = hashlib.sha256(secrets.token_bytes(16)).hexdigest()

    code = MfaRecoveryCode(
        id=id_ or uuid.uuid4(),
        user_id=user_id,
        code_hash=code_hash,
        used_at=used_at,
        **kwargs,
    )

    if session is not None:
        session.add(code)
        session.flush()

    return code


def create_auth_session(
    session: Session | None = None,
    *,
    user_id: uuid.UUID,
    id_: str | None = None,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    mfa_verified: bool = False,
    ip_hash: str | None = "salt_ip_hash_test",
    user_agent: str | None = "Mozilla/5.0 TestBrowser",
    **kwargs: Any,
) -> AuthSession:
    """Create a server-side AuthSession instance."""
    if id_ is None:
        id_ = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(hours=8)

    auth_sess = AuthSession(
        id=id_,
        user_id=user_id,
        expires_at=expires_at,
        revoked_at=revoked_at,
        mfa_verified=mfa_verified,
        ip_hash=ip_hash,
        user_agent=user_agent,
        **kwargs,
    )

    if session is not None:
        session.add(auth_sess)
        session.flush()

    return auth_sess
