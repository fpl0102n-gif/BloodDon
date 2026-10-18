"""Unit tests for AuditService details validation and PII rejection (BR-AUDIT-01 ★).

Per:
- docs/06-business-rules.md §13 (BR-AUDIT-01: no PII in details).
- docs/07-test-cases.md §5.13 (T-AUDIT-01-a: sentinel leak check).
"""

import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.core.clock import FakeClock
from app.repositories.audit import AuditRepository
from app.services.audit import (
    APPROVED_AUDIT_ACTIONS,
    APPROVED_AUTH_ACTIONS,
    FORBIDDEN_DETAIL_KEYS,
    AuditService,
    validate_audit_details,
)


@pytest.mark.unit
def test_validate_audit_details_none() -> None:
    """Validate that None details returns None safely."""
    assert validate_audit_details(None) is None


@pytest.mark.unit
def test_validate_audit_details_valid() -> None:
    """Validate that non-PII diagnostic and operational details are preserved."""
    details = {
        "reason": "invalid_password",
        "ip_hash": "abcdef1234567890",  # pragma: allowlist secret
        "lock_minutes": 15,
        "failed_attempts": 5,
        "method": "totp",
        "count": 3,
    }
    result = validate_audit_details(details)
    assert result == details


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.parametrize(
    "forbidden_key",
    list(FORBIDDEN_DETAIL_KEYS),
)
def test_validate_audit_details_rejects_forbidden_keys(forbidden_key: str) -> None:
    """Verify BR-AUDIT-01 rejects any details dictionary with prohibited PII keys."""
    details = {forbidden_key: "some_value"}
    with pytest.raises(ValueError, match="BR-AUDIT-01 Violation: Prohibited PII key"):
        validate_audit_details(details)


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.parametrize(
    "sentinel",
    ["ZZFIRST", "ZZLAST", "0550123456"],
)
def test_validate_audit_details_rejects_sentinel_values(sentinel: str) -> None:
    """Verify BR-AUDIT-01 rejects sentinel values defined in docs/07 §3.3."""
    details = {"diagnostic_info": sentinel}
    with pytest.raises(ValueError, match="BR-AUDIT-01 Violation: Sentinel leak value"):
        validate_audit_details(details)


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.parametrize(
    "phone_val",
    ["+213550123456", "0550112233", "0770998877", "+12025550199"],
)
def test_validate_audit_details_rejects_phone_numbers(phone_val: str) -> None:
    """Verify BR-AUDIT-01 rejects string values matching telephone regex."""
    details = {"meta": phone_val}
    with pytest.raises(ValueError, match="BR-AUDIT-01 Violation: Phone number pattern detected"):
        validate_audit_details(details)


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.parametrize(
    "email_val",
    ["user@bloodday.local", "donor@domain.dz", "admin@hospital.org"],
)
def test_validate_audit_details_rejects_emails(email_val: str) -> None:
    """Verify BR-AUDIT-01 rejects string values containing email addresses."""
    details = {"context": email_val}
    with pytest.raises(ValueError, match="BR-AUDIT-01 Violation: Email address detected"):
        validate_audit_details(details)


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.parametrize(
    "blood_type",
    ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
)
def test_validate_audit_details_rejects_blood_types(blood_type: str) -> None:
    """Verify BR-AUDIT-01 rejects string values matching blood types."""
    details = {"category": blood_type}
    with pytest.raises(ValueError, match="BR-AUDIT-01 Violation: Blood type"):
        validate_audit_details(details)


@pytest.mark.unit
def test_audit_service_log_event_unit() -> None:
    """Verify AuditService constructs AuditLog entry with Clock and calls repository create."""
    repo_mock = MagicMock(spec=AuditRepository)
    repo_mock.create.side_effect = lambda entry: entry
    clock = FakeClock(datetime(2026, 11, 20, 10, 0, 0))

    service = AuditService(repo_mock, clock=clock)
    user_id = uuid.uuid4()

    entry = service.log_login_success(user_id, ip_hash="test_ip_hash")

    assert entry.action == "auth.login_success"
    assert entry.entity_type == "user"
    assert entry.entity_id == user_id
    assert entry.actor_user_id == user_id
    assert entry.details == {"ip_hash": "test_ip_hash"}
    assert entry.at == clock.now()
    repo_mock.create.assert_called_once_with(entry)


@pytest.mark.unit
@pytest.mark.critical
@pytest.mark.parametrize(
    "forbidden_item",
    ["ZZFIRST", "ZZLAST", "0550123456", "O+", "user@hospital.dz", "+213550123456"],
)
def test_validate_audit_details_rejects_forbidden_values_inside_sequences(
    forbidden_item: str,
) -> None:
    """Verify BR-AUDIT-01 rejects PII/sentinel values even when nested within lists or sets."""
    details = {"changed_items": ["valid_field", forbidden_item, "another_field"]}
    with pytest.raises(ValueError, match="BR-AUDIT-01 Violation:"):
        validate_audit_details(details)


@pytest.mark.unit
def test_approved_auth_actions_catalog() -> None:
    """Verify that all 7 mandatory Auth audit actions from docs/03 §17 and BR-AUDIT-05 are approved."""
    expected = {
        "auth.login_success",
        "auth.login_failure",
        "auth.lockout",
        "auth.mfa_enabled",
        "auth.password_changed",
        "auth.password_reset",
        "auth.session_revoked",
    }
    assert expected.issubset(APPROVED_AUTH_ACTIONS)


@pytest.mark.unit
@pytest.mark.br("BR-AUDIT-05")
def test_approved_audit_actions_catalog() -> None:
    """Verify completeness of approved audit events catalog per BR-AUDIT-05."""
    expected_categories = {
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
        # File Deletion Queue
        "file_queue.mark_deleted",
        "file_queue.batch_process",
        # Campaign
        "campaign.create",
        "campaign.publish",
        "campaign.unpublish",
        "campaign.set_current",
        "campaign.open",
        "campaign.revert",
        "campaign.end",
        "campaign.delete",
        # Users
        "user.invited",
        "user.updated",
        "user.deactivated",
        "user.activated",
        "user.unlocked",
        "user.mfa_reset",
        "user.bulk_deactivated",
        # Auth
        "auth.login_success",
        "auth.login_failure",
        "auth.lockout",
        "auth.mfa_enabled",
        "auth.password_changed",
        "auth.password_reset",
        "auth.session_revoked",
        # Emergency
        "emergency.reveal",
        # Privacy
        "data.deleted_on_request",
        "retention.purge",
        # Security
        "security.forbidden_field",
        "security.reveal_spike",
        # Posts (P9)
        "post.create",
        "post.update",
        "post.delete",
    }
    assert expected_categories.issubset(APPROVED_AUDIT_ACTIONS)


@pytest.mark.unit
def test_audit_service_list_events_unit() -> None:
    """Verify AuditService.list_events passes all query parameters to repository."""
    repo_mock = MagicMock(spec=AuditRepository)
    repo_mock.list_events.return_value = []
    clock = FakeClock(datetime(2026, 11, 20, 10, 0, 0))

    service = AuditService(repo_mock, clock=clock)
    actor_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    from_dt = datetime(2026, 11, 1, 0, 0, 0)
    to_dt = datetime(2026, 11, 30, 23, 59, 59)

    result = service.list_events(
        action="campaign.publish",
        entity_type="campaign",
        entity_id=entity_id,
        actor_user_id=actor_id,
        from_date=from_dt,
        to_date=to_dt,
        limit=50,
        offset=10,
    )

    assert result == []
    repo_mock.list_events.assert_called_once_with(
        action="campaign.publish",
        entity_type="campaign",
        entity_id=entity_id,
        actor_user_id=actor_id,
        from_date=from_dt,
        to_date=to_dt,
        limit=50,
        offset=10,
    )
