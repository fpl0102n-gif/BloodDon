"""Authentication schemas for login, invitation acceptance, and password reset.

Conforms to:
- docs/04-api.md §9 (Authentication endpoints contracts).
- docs/06-business-rules.md §11 (BR-AUTH-02, BR-AUTH-03, BR-AUTH-15, BR-USER-01).
- CLAUDE.md §6 Golden Rule 4: all schemas enforce extra='forbid' via AppBaseModel.
"""

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.common import AppBaseModel


class AcceptInviteIn(AppBaseModel):
    """Payload for accepting an invitation and setting account password."""

    token: str = Field(
        ...,
        min_length=1,
        description="One-time invitation token received via email",
    )
    password: str = Field(
        ...,
        min_length=10,
        max_length=128,
        description="Account password conforming to BR-AUTH-03 policy",
    )


class ForgotPasswordIn(AppBaseModel):
    """Payload for requesting a password reset link."""

    email: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Account email address",
    )


class ResetPasswordIn(AppBaseModel):
    """Payload for completing password reset using a one-time token."""

    token: str = Field(
        ...,
        min_length=1,
        description="One-time password reset token received via email",
    )
    new_password: str = Field(
        ...,
        min_length=10,
        max_length=128,
        description="New password conforming to BR-AUTH-03 policy",
    )


class InviteUserIn(AppBaseModel):
    """Payload for inviting a new staff member (admin or supervisor)."""

    email: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Invitee email address",
    )
    role: Literal["admin", "supervisor"] = Field(
        ...,
        description="Role assigned to the invited user",
    )
    site_id: uuid.UUID | None = Field(
        default=None,
        description="Site ID required for supervisors per BR-USER-01",
    )
    full_name: str = Field(
        default="مستخدم جديد",
        min_length=1,
        max_length=100,
        description="Display name of the user",
    )


class InviteUserOut(AppBaseModel):
    """Response returned upon creating a user invitation."""

    user_id: uuid.UUID
    email: str
    role: str
    site_id: uuid.UUID | None = None
    invite_url: str | None = None
    email_sent: bool


class MfaSetupOut(AppBaseModel):
    """Response returned when initiating MFA enrollment."""

    secret: str = Field(..., description="Base32 TOTP secret for manual entry")
    provisioning_uri: str = Field(..., description="otpauth:// URI for QR code generation")
    recovery_codes: list[str] = Field(..., description="8 backup recovery codes displayed once")


class MfaConfirmIn(AppBaseModel):
    """Payload to confirm TOTP setup with a first 6-digit code."""

    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit verification code from authenticator app",
    )


class MfaVerifyIn(AppBaseModel):
    """Payload for verifying second factor during login."""

    code: str | None = Field(
        default=None,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit TOTP code",
    )
    recovery_code: str | None = Field(
        default=None,
        min_length=8,
        max_length=16,
        description="Backup recovery code if authenticator app is unavailable",
    )


class MfaStatusOut(AppBaseModel):
    """User MFA status details."""

    totp_enabled: bool = Field(..., description="Whether MFA is enabled on the account")
    recovery_codes_remaining: int = Field(..., ge=0, description="Number of unused backup codes")
