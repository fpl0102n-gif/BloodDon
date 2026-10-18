"""Pydantic schemas for User entity administration.

Per:
- docs/04-api.md §15 (Users Administration: GET, POST, PATCH, sub-actions)
- docs/06-business-rules.md §11.2 (BR-USER-01..10, BR-AUTH-02, BR-AUTH-13)
- CLAUDE.md / GEMINI.md Rule 6.4: extra="forbid", never expose password_hash,
  totp_secret_enc, recovery codes, or raw tokens.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.common import AppBaseModel


class UserOut(AppBaseModel):
    """User response model exposing public account attributes without sensitive credentials."""

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    site_id: uuid.UUID | None = None
    is_active: bool
    totp_enabled: bool
    locked_until: datetime | None = None
    failed_login_count: int = 0
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class UserInviteIn(AppBaseModel):
    """Request payload for inviting a new staff member (BR-USER-01)."""

    email: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Invitee email address",
    )
    full_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Full display name of the staff member",
    )
    role: Literal["admin", "supervisor"] = Field(
        ...,
        description="Role assigned to the invited user",
    )
    site_id: uuid.UUID | None = Field(
        default=None,
        description="Required for supervisors; forbidden for admins per BR-USER-01",
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_supervisor_site(self) -> "UserInviteIn":
        """Enforce BR-USER-01: Supervisor requires site_id; Admin forbids site_id."""
        if self.role == "supervisor" and self.site_id is None:
            raise ValueError("Supervisor role requires a site_id.")
        if self.role == "admin" and self.site_id is not None:
            raise ValueError("Admin role must not have a site_id.")
        return self


class UserAdminUpdateIn(AppBaseModel):
    """Payload for updating an existing user's details administratively."""

    full_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Updated display name",
    )
    role: Literal["admin", "supervisor"] | None = Field(
        default=None,
        description="Updated role assignment",
    )
    site_id: uuid.UUID | None = Field(
        default=None,
        description="Site ID for supervisors",
    )

    model_config = ConfigDict(extra="forbid")


class UserInviteOut(AppBaseModel):
    """Response model returned upon successful user invitation."""

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    site_id: uuid.UUID | None = None
    is_active: bool
    created_at: datetime
    invite_url: str | None = Field(
        default=None,
        description="One-time fallback invitation URL populated only if email delivery fails (BR-USER-09)",
    )

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class BulkDeactivateOut(AppBaseModel):
    """Response returned upon bulk supervisor deactivation."""

    deactivated_count: int = Field(
        ...,
        ge=0,
        description="Total number of supervisors deactivated in this operation",
    )

    model_config = ConfigDict(extra="forbid")
