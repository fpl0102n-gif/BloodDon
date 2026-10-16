"""SQLAlchemy models for Users, Tokens, MFA Recovery Codes, and Sessions.

Implements P2.2 per docs/02-database.md §3, §4, §5.4, §5.5, §5.6, §5.7,
docs/03-auth-permissions.md §2-§6, and docs/06-business-rules.md §5:
- User: Administrator and supervisor identity, role constraint, lower-case email
  functional unique index, argon2 password hash, encrypted TOTP secret, and
  supervisor-needs-site CHECK constraint.
- UserToken: Hashed one-time invitation and password reset tokens.
- MfaRecoveryCode: Hashed backup recovery codes for two-factor authentication.
- AuthSession: Server-side stateful session store indexed by session ID hash,
  with idle/absolute timeouts, IP hash, and immediate revocation support.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.campaign import Site


class User(Base):
    """User account entity for platform administrators and field site supervisors.

    Governed by BR-AUTH-* and BR-USER-* rules:
    - Email is stored in lowercase with a functional unique index: UNIQUE (lower(email)).
    - Supervisor role requires an associated physical site_id.
    - Admin role does not require a site_id.
    - Password hash stores argon2id hash; empty before invite acceptance.
    - totp_last_used_step prevents TOTP token replay attacks (BR-AUTH-09).
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    password_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    totp_secret_enc: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    totp_last_used_step: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    site: Mapped["Site | None"] = relationship(
        "Site",
        lazy="joined",
    )
    tokens: Mapped[list["UserToken"]] = relationship(
        "UserToken",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    mfa_recovery_codes: Mapped[list["MfaRecoveryCode"]] = relationship(
        "MfaRecoveryCode",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sessions: Mapped[list["AuthSession"]] = relationship(
        "AuthSession",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('supervisor', 'admin')",
            name="role",
        ),
        CheckConstraint(
            "role = 'admin' OR site_id IS NOT NULL",
            name="supervisor_site",
        ),
        Index(
            "uq_users_email_lower",
            func.lower(email),
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<User(id={self.id}, email='{self.email}', role='{self.role}', "
            f"site_id={self.site_id}, is_active={self.is_active})>"
        )


class UserToken(Base):
    """Hashed one-time credential token for invitations and password resets.

    Per docs/02-database.md §5.5 and docs/03-auth-permissions.md §6:
    - token_hash stores SHA-256 hash; raw plaintext token is never stored in DB.
    - Used tokens are marked with used_at timestamp rather than deleted, preserving audit history.
    """

    __tablename__ = "user_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="tokens",
    )

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('invite', 'password_reset')",
            name="purpose",
        ),
        Index(
            "ix_user_tokens_user_id_purpose",
            "user_id",
            "purpose",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<UserToken(id={self.id}, user_id={self.user_id}, purpose='{self.purpose}', "
            f"used={self.used_at is not None})>"
        )


class MfaRecoveryCode(Base):
    """Hashed backup MFA recovery code.

    Per docs/02-database.md §5.6 and docs/03-auth-permissions.md §5:
    - code_hash stores one-way cryptographic hash of 10-character alphanumeric recovery codes.
    - Each code is single-use; marked with used_at upon consumption.
    """

    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="mfa_recovery_codes",
    )

    def __repr__(self) -> str:
        return f"<MfaRecoveryCode(id={self.id}, user_id={self.user_id}, used={self.used_at is not None})>"


class AuthSession(Base):
    """Server-side authenticated user session store.

    Per docs/02-database.md §5.7, docs/03-auth-permissions.md §7, and decision D-07:
    - id is the SHA-256 hash of the raw session token stored in client cookie.
    - Supports immediate server-side revocation on logout, password change, or user deactivation.
    - Tracks MFA verification status and salted client IP hash.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    mfa_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    ip_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="sessions",
    )

    def __repr__(self) -> str:
        return (
            f"<AuthSession(id='{self.id[:10]}...', user_id={self.user_id}, "
            f"mfa_verified={self.mfa_verified}, revoked={self.revoked_at is not None})>"
        )
