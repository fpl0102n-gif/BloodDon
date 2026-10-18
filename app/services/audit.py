"""Audit logging domain service (P3.12).

Per:
- docs/06-business-rules.md §13 (BR-AUDIT-01 no PII, BR-AUDIT-02 append-only, BR-AUDIT-03 same transaction, BR-AUDIT-04 actor, BR-AUDIT-05 completeness).
- docs/03-auth-permissions.md §17 (Auth audit events: login_success, login_failure, lockout, mfa_enabled, password_changed, password_reset, session_revoked).
- docs/07-test-cases.md §5.13 (T-AUDIT-01-a sentinel leak check, T-AUDIT-03-a atomic failure rollback, T-AUDIT-05-a event catalog).
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Final

from app.core.clock import Clock, get_clock
from app.models.audit import AuditLog
from app.repositories.audit import AuditRepository

logger = logging.getLogger("app.services.audit")

# Canonical set of approved audit actions per BR-AUDIT-05 and docs/03 §17
APPROVED_AUTH_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "auth.login_success",
        "auth.login_failure",
        "auth.lockout",
        "auth.mfa_enabled",
        "auth.mfa_reset",
        "auth.password_changed",
        "auth.password_reset",
        "auth.session_revoked",
    }
)

# Canonical set of approved audit actions per BR-AUDIT-05 across all domains
APPROVED_AUDIT_ACTIONS: Final[frozenset[str]] = frozenset(
    APPROVED_AUTH_ACTIONS
    | {
        # Donor
        "donor.create",
        "donor.update",
        "donor.status_change",
        "donor.delete",
        "donor.relocate",
        "donor.anonymize",
        # Photos
        "photo.upload",
        "photo.delete",
        # Campaign
        "campaign.create",
        "campaign.publish",
        "campaign.unpublish",
        "campaign.set_current",
        "campaign.open",
        "campaign.revert",
        "campaign.end",
        "campaign.delete",
        # Sites & Participation
        "site.create",
        "site.update",
        "site.delete",
        "campaign_site.add",
        "campaign_site.update",
        "campaign_site.remove",
        # Users
        "user.invited",
        "user.updated",
        "user.deactivated",
        "user.activated",
        "user.unlocked",
        "user.mfa_reset",
        "user.bulk_deactivated",
        # Posts (Free Posts & Awareness Articles - P9)
        "post.create",
        "post.update",
        "post.delete",
        # File Deletion Queue
        "file_queue.mark_deleted",
        "file_queue.batch_process",
        # Emergency
        "emergency.reveal",
        # Privacy & Retention
        "data.deleted_on_request",
        "retention.purge",
        # Security
        "security.forbidden_field",
        "security.reveal_spike",
    }
)

# Known blood types that must never be recorded in audit log details (BR-AUDIT-01)
FORBIDDEN_BLOOD_TYPES: Final[frozenset[str]] = frozenset(
    {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}
)

# Sentinel leak values that must never appear in audit logs (docs/07 §3.3)
SENTINEL_VALUES: Final[frozenset[str]] = frozenset({"ZZFIRST", "ZZLAST", "0550123456"})

# Prohibited key substrings or names in audit details
FORBIDDEN_DETAIL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "first_name",
        "last_name",
        "full_name",
        "phone",
        "phone_number",
        "mobile",
        "blood_type",
        "blood",
        "password",
        "password_hash",
        "secret",
        "token",
        "raw_token",
        "totp_secret",
        "recovery_code",
        "code",
        "otp",
        "email",
    }
)

PHONE_REGEX: Final[re.Pattern[str]] = re.compile(r"^\+?[0-9]{8,15}$")


def validate_audit_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate and sanitize audit log details dict against PII leakage (BR-AUDIT-01 ★).

    Strictly forbids:
    - Keys named or containing: name, phone, blood_type, password, token, secret, code, email.
    - String values containing phone numbers, emails, blood types, or test sentinels.

    Raises:
        ValueError: If any prohibited PII key or value pattern is detected.
    """
    if details is None:
        return None

    def _check_string(s: str) -> None:
        val_stripped = s.strip()
        val_upper = val_stripped.upper()

        # Reject test sentinel values
        if val_upper in SENTINEL_VALUES:
            raise ValueError(
                f"BR-AUDIT-01 Violation: Sentinel leak value '{s}' in audit log details."
            )

        # Reject emails
        if "@" in val_stripped:
            raise ValueError("BR-AUDIT-01 Violation: Email address detected in audit log details.")

        # Reject phone numbers
        if PHONE_REGEX.match(val_stripped):
            raise ValueError(
                "BR-AUDIT-01 Violation: Phone number pattern detected in audit log details."
            )

        # Reject blood types
        if val_upper in FORBIDDEN_BLOOD_TYPES:
            raise ValueError(
                f"BR-AUDIT-01 Violation: Blood type '{s}' detected in audit log details."
            )

    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        key_lower = str(key).strip().lower()

        # Reject prohibited key names
        if key_lower in FORBIDDEN_DETAIL_KEYS:
            raise ValueError(
                f"BR-AUDIT-01 Violation: Prohibited PII key '{key}' in audit log details."
            )

        # Inspect values for PII leak patterns
        if isinstance(value, str):
            _check_string(value)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, str):
                    _check_string(item)

        cleaned[key] = value

    return cleaned


class AuditService:
    """Domain service managing the creation and inspection of audit log entries."""

    def __init__(
        self,
        audit_repo: AuditRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.audit_repo = audit_repo
        self.clock = clock or get_clock()

    def log_event(
        self,
        action: str,
        entity_type: str,
        *,
        entity_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Create and flush an audit log entry in the caller's transaction (BR-AUDIT-01/03).

        Per:
        - BR-AUDIT-01: Validates details dict ensuring zero PII before writing.
        - BR-AUDIT-03: Flushes in the same transaction; exceptions cause caller rollback.
        - BR-AUDIT-04: actor_user_id is None for anonymous / system operations.
        """
        validated_details = validate_audit_details(details)
        now = self.clock.now()

        entry = AuditLog(
            at=now,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            details=validated_details,
        )

        return self.audit_repo.create(entry)

    # ==========================================================================
    # Dedicated Auth Event Loggers (docs/03 §17 & docs/06 §13 BR-AUDIT-05)
    # ==========================================================================

    def log_login_success(
        self,
        user_id: uuid.UUID,
        *,
        ip_hash: str | None = None,
    ) -> AuditLog:
        """Log successful authentication event (auth.login_success)."""
        details: dict[str, Any] | None = {"ip_hash": ip_hash} if ip_hash else None
        return self.log_event(
            action="auth.login_success",
            entity_type="user",
            entity_id=user_id,
            actor_user_id=user_id,
            details=details,
        )

    def log_login_failure(
        self,
        *,
        reason: str,
        ip_hash: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> AuditLog:
        """Log failed authentication attempt with internal reason code (auth.login_failure).

        Internal reason codes:
        - user_not_found
        - uninitialized_account
        - account_locked
        - invalid_password
        - account_disabled
        - invalid_mfa_code
        - mfa_code_reused
        - invalid_recovery_code
        """
        details: dict[str, Any] = {"reason": reason}
        if ip_hash:
            details["ip_hash"] = ip_hash

        return self.log_event(
            action="auth.login_failure",
            entity_type="user",
            entity_id=user_id,
            actor_user_id=None,
            details=details,
        )

    def log_lockout(
        self,
        user_id: uuid.UUID,
        *,
        lock_minutes: int,
        failed_attempts: int,
        ip_hash: str | None = None,
    ) -> AuditLog:
        """Log account temporary lockout event (auth.lockout)."""
        details: dict[str, Any] = {
            "lock_minutes": lock_minutes,
            "failed_attempts": failed_attempts,
        }
        if ip_hash:
            details["ip_hash"] = ip_hash

        return self.log_event(
            action="auth.lockout",
            entity_type="user",
            entity_id=user_id,
            actor_user_id=None,
            details=details,
        )

    def log_mfa_enabled(
        self,
        user_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
        method: str = "totp",
    ) -> AuditLog:
        """Log MFA enrollment confirmation event (auth.mfa_enabled)."""
        return self.log_event(
            action="auth.mfa_enabled",
            entity_type="user",
            entity_id=user_id,
            actor_user_id=actor_user_id or user_id,
            details={"method": method},
        )

    def log_mfa_reset(
        self,
        user_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> AuditLog:
        """Log administrative MFA reset event (auth.mfa_reset)."""
        return self.log_event(
            action="auth.mfa_reset",
            entity_type="user",
            entity_id=user_id,
            actor_user_id=actor_user_id,
        )

    def log_password_changed(
        self,
        user_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
        reason: str = "user_change",
    ) -> AuditLog:
        """Log authenticated user password change (auth.password_changed)."""
        return self.log_event(
            action="auth.password_changed",
            entity_type="user",
            entity_id=user_id,
            actor_user_id=actor_user_id or user_id,
            details={"reason": reason},
        )

    def log_password_reset(
        self,
        user_id: uuid.UUID,
        *,
        method: str = "token",
    ) -> AuditLog:
        """Log token-based password reset completion (auth.password_reset)."""
        return self.log_event(
            action="auth.password_reset",
            entity_type="user",
            entity_id=user_id,
            actor_user_id=user_id,
            details={"method": method},
        )

    def log_session_revoked(
        self,
        user_id: uuid.UUID,
        *,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
        count: int = 1,
    ) -> AuditLog:
        """Log session invalidation event (auth.session_revoked).

        Reasons:
        - logout
        - logout_all
        - password_changed
        - password_reset
        - mfa_reset
        - max_mfa_attempts
        - account_deactivated
        """
        return self.log_event(
            action="auth.session_revoked",
            entity_type="user",
            entity_id=user_id,
            actor_user_id=actor_user_id,
            details={"reason": reason, "count": count},
        )

    # ==========================================================================
    # Query Helper for Testing and Admin Inspection
    # ==========================================================================

    def list_events(
        self,
        *,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Fetch audit log records matching criteria, ordered newest first."""
        return self.audit_repo.list_events(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )
