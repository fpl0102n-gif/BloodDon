"""Unit tests for the policy authorization module (P3.8).

Traces to:
- docs/03-auth-permissions.md §10-§13 (Conditions A, S, L, M, R, P, and full role matrix).
- docs/06-business-rules.md (BR-AUTH-14, BR-DONOR-09 site scope, BR-CAMP-16 campaign status).
- docs/07-test-cases.md §21.1 (T-STRUCT-02: Automated matrix test).
"""

import pytest

from app.policy import (
    ADMIN_PERMISSIONS,
    ALL_PERMISSIONS,
    PERSONAL_ACCOUNT_PERMISSIONS,
    PUBLIC_PERMISSIONS,
    ROLE_PERMISSIONS,
    SUPERVISOR_FIELD_PERMISSIONS,
    Condition,
    Permission,
    PolicyContext,
    can,
    get_required_conditions,
    is_permission_granted,
    requires_step_up,
)
from tests.conftest import MockUser


@pytest.mark.unit
class TestPermissionRegistry:
    """Verifies all permission tokens and groupings are properly declared."""

    def test_all_permissions_count(self) -> None:
        """Verify the exact permission catalog contains 37 distinct permissions."""
        assert len(ALL_PERMISSIONS) == 37
        assert len(PUBLIC_PERMISSIONS) == 9
        assert len(SUPERVISOR_FIELD_PERMISSIONS) == 6
        assert len(ADMIN_PERMISSIONS) == 17  # 16 admin + donor:relocate
        assert len(PERSONAL_ACCOUNT_PERMISSIONS) == 5

    def test_permission_format(self) -> None:
        """Every permission must follow 'resource:action' format."""
        for perm in ALL_PERMISSIONS:
            parts = perm.value.split(":")
            assert len(parts) == 2, f"Invalid format for {perm}"
            assert parts[0].isalnum() or "_" in parts[0]
            assert parts[1].isalnum() or "_" in parts[1]


@pytest.mark.unit
class TestPolicyMatrixStructure:
    """Verifies matrix mappings, conditions, and step-up rules (T-STRUCT-02)."""

    def test_anon_role_permissions(self) -> None:
        """Anonymous role has exactly the 9 public permissions."""
        anon_perms = ROLE_PERMISSIONS["anon"]
        assert set(anon_perms.keys()) == set(PUBLIC_PERMISSIONS)

        # Published condition check
        assert Condition.PUBLISHED_CAMPAIGN in anon_perms[Permission.CAMPAIGN_READ]
        assert Condition.PUBLISHED_CAMPAIGN in anon_perms[Permission.CAMPAIGN_SITE_READ]
        assert Condition.PUBLISHED_CAMPAIGN in anon_perms[Permission.PHOTO_READ]
        assert Condition.PUBLISHED_CAMPAIGN in anon_perms[Permission.STATS_PUBLIC]
        assert len(anon_perms[Permission.SITE_READ]) == 0
        assert len(anon_perms[Permission.PLEDGE_CREATE]) == 0
        assert len(anon_perms[Permission.EMERGENCY_LIST]) == 0
        assert len(anon_perms[Permission.EMERGENCY_REVEAL]) == 0

    def test_supervisor_role_permissions(self) -> None:
        """Supervisor role has 9 public + 6 field + 5 personal = 20 permissions."""
        sup_perms = ROLE_PERMISSIONS["supervisor"]
        assert len(sup_perms) == 20

        # Field operations require A, S, and L
        for perm in SUPERVISOR_FIELD_PERMISSIONS:
            conds = sup_perms[perm]
            assert Condition.ACTIVE_ACCOUNT in conds
            assert Condition.SITE_ISOLATION in conds
            assert Condition.LIVE_CAMPAIGN in conds

        # Personal operations require A
        for perm in PERSONAL_ACCOUNT_PERMISSIONS:
            conds = sup_perms[perm]
            assert Condition.ACTIVE_ACCOUNT in conds

        # Supervisor must NEVER have admin permissions or relocate
        assert Permission.DONOR_RELOCATE not in sup_perms
        assert Permission.CAMPAIGN_MANAGE not in sup_perms
        assert Permission.USER_INVITE not in sup_perms
        assert Permission.AUDIT_READ not in sup_perms

    def test_admin_role_permissions(self) -> None:
        """Admin role has all 37 permissions in the system."""
        admin_perms = ROLE_PERMISSIONS["admin"]
        assert len(admin_perms) == 37

        # Admin public reads have no P condition (admin can see unpublished)
        assert len(admin_perms[Permission.CAMPAIGN_READ]) == 0

        # Admin governance operations require active account (A) and MFA (M)
        for perm in ADMIN_PERMISSIONS:
            conds = admin_perms[perm]
            assert Condition.ACTIVE_ACCOUNT in conds
            assert Condition.MFA_VERIFIED in conds

    def test_step_up_sensitive_permissions(self) -> None:
        """Sensitive admin actions strictly require recent re-authentication (R)."""
        expected_r_permissions = {
            Permission.DONOR_RELOCATE,
            Permission.CAMPAIGN_END,
            Permission.PLEDGE_DELETE,
            Permission.USER_INVITE,
            Permission.USER_UPDATE,
            Permission.USER_DEACTIVATE,
            Permission.USER_UNLOCK,
            Permission.USER_RESET_MFA,
            Permission.DATA_DELETE_ON_REQUEST,
        }
        for perm in ALL_PERMISSIONS:
            if perm in expected_r_permissions:
                assert requires_step_up("admin", perm), f"{perm} should require R"
            else:
                assert not requires_step_up("admin", perm), f"{perm} should NOT require R"


@pytest.mark.unit
class TestPolicyCanEvaluation:
    """Verifies can() evaluator behavior with role fixtures and situational contexts."""

    def test_anon_can_read_published_campaign(self) -> None:
        """Anonymous visitor can read published campaigns, but not unpublished."""
        assert can(None, Permission.CAMPAIGN_READ, PolicyContext(is_published=True))
        assert not can(None, Permission.CAMPAIGN_READ, PolicyContext(is_published=False))
        assert can(None, Permission.SITE_READ)
        assert can(None, Permission.PLEDGE_CREATE)
        assert not can(None, Permission.DONOR_CREATE)

    def test_supervisor_site_isolation(
        self,
        role_sup_a: MockUser,
    ) -> None:
        """Supervisor can only act on their assigned site (Condition S) across all field operations."""
        ctx_a = PolicyContext(site_id=role_sup_a.site_id, campaign_status="live")
        field_perms = [
            Permission.DONOR_CREATE,
            Permission.DONOR_READ,
            Permission.DONOR_UPDATE,
            Permission.DONOR_DELETE,
            Permission.PHOTO_UPLOAD,
            Permission.PHOTO_DELETE,
        ]

        # Own site A with live campaign -> Allowed for all field operations
        for perm in field_perms:
            assert can(role_sup_a, perm, ctx_a), f"Supervisor should be allowed {perm} on own site"

        # Foreign site B -> Rejected (violates Condition S) for all field operations
        ctx_b = PolicyContext(site_id="site-b-002", campaign_status="live")
        for perm in field_perms:
            assert not can(role_sup_a, perm, ctx_b), (
                f"Supervisor must NOT be allowed {perm} on foreign site"
            )

        # Missing context -> Rejected
        for perm in field_perms:
            assert not can(role_sup_a, perm, None), (
                f"Supervisor must NOT be allowed {perm} with missing context"
            )

    def test_supervisor_campaign_live_status(
        self,
        role_sup_a: MockUser,
    ) -> None:
        """Supervisor cannot write/read donors when campaign is upcoming or ended (Condition L)."""
        ctx_upcoming = PolicyContext(site_id=role_sup_a.site_id, campaign_status="upcoming")
        ctx_ended = PolicyContext(site_id=role_sup_a.site_id, campaign_status="ended")
        field_perms = [
            Permission.DONOR_CREATE,
            Permission.DONOR_READ,
            Permission.DONOR_UPDATE,
            Permission.DONOR_DELETE,
            Permission.PHOTO_UPLOAD,
            Permission.PHOTO_DELETE,
        ]

        for perm in field_perms:
            assert not can(role_sup_a, perm, ctx_upcoming), (
                f"{perm} must be rejected on upcoming campaign"
            )
            assert not can(role_sup_a, perm, ctx_ended), (
                f"{perm} must be rejected on ended campaign"
            )

    def test_deactivated_supervisor_rejected(
        self,
        role_sup_disabled: MockUser,
    ) -> None:
        """Deactivated supervisor cannot perform any authenticated action (Condition A)."""
        ctx = PolicyContext(site_id=role_sup_disabled.site_id, campaign_status="live")
        assert not can(role_sup_disabled, Permission.DONOR_CREATE, ctx)
        assert not can(role_sup_disabled, Permission.ME_READ)

    def test_admin_mfa_requirement(
        self,
        role_admin_nomfa: MockUser,
        role_admin: MockUser,
    ) -> None:
        """Admin without MFA verified cannot access protected resources (Condition M)."""
        assert not can(role_admin_nomfa, Permission.CAMPAIGN_MANAGE)
        assert not can(role_admin_nomfa, Permission.DONOR_READ)
        assert not can(role_admin_nomfa, Permission.AUDIT_READ)

        # With MFA verified -> Allowed
        assert can(role_admin, Permission.CAMPAIGN_MANAGE)
        assert can(role_admin, Permission.DONOR_READ)
        assert can(role_admin, Permission.AUDIT_READ)

    def test_admin_step_up_reauth_requirement(
        self,
        role_admin: MockUser,
        role_admin_fresh: MockUser,
    ) -> None:
        """Admin without fresh confirmation cannot execute R-marked operations."""
        # role_admin has reauth_valid=False
        assert not can(role_admin, Permission.CAMPAIGN_END)
        assert not can(role_admin, Permission.USER_INVITE)
        assert not can(role_admin, Permission.USER_DEACTIVATE)
        assert not can(role_admin, Permission.DONOR_RELOCATE)

        # role_admin_fresh has reauth_valid=True -> Allowed
        assert can(role_admin_fresh, Permission.CAMPAIGN_END)
        assert can(role_admin_fresh, Permission.USER_INVITE)
        assert can(role_admin_fresh, Permission.USER_DEACTIVATE)
        assert can(role_admin_fresh, Permission.DONOR_RELOCATE)

    def test_string_permission_resolution(
        self,
        role_admin: MockUser,
    ) -> None:
        """can() accepts permission enum or valid string identifier."""
        assert can(role_admin, "campaign:manage")
        assert not can(role_admin, "nonexistent:permission")

    def test_helper_functions(self) -> None:
        """Check is_permission_granted and get_required_conditions helpers."""
        assert is_permission_granted("admin", Permission.USER_INVITE)
        assert not is_permission_granted("supervisor", Permission.USER_INVITE)
        assert not is_permission_granted("unknown_role", Permission.CAMPAIGN_READ)

        conds = get_required_conditions("supervisor", Permission.DONOR_CREATE)
        assert conds is not None
        assert Condition.SITE_ISOLATION in conds

        assert get_required_conditions("supervisor", Permission.USER_INVITE) is None
