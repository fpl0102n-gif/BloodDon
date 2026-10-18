"""Role-permission authorization matrix and condition mappings.

Per docs/03-auth-permissions.md §10-§11, docs/05-architecture.md §14, and docs/06-business-rules.md:
- Implements the complete role-permission matrix defining what roles can access which permissions.
- Maps roles ('anon', 'supervisor', 'admin') to discrete permissions and the conditions required
  to exercise them:
    - A: Active account and valid authenticated session.
    - S: Site isolation - resource belongs to user's assigned site.
    - L: Live campaign - campaign status must be 'live'.
    - M: MFA verified - session must be authenticated with two-factor authentication.
    - R: Step-up reauthentication - fresh password confirmation within last 10 minutes (600s).
    - P: Published campaign - campaign must be marked as published.
- Architectural boundary: This module contains NO database or HTTP dependencies.
"""

from collections.abc import Mapping
from enum import StrEnum

from app.policy.permissions import Permission


class Condition(StrEnum):
    """Conditions governing permission execution per docs/03-auth-permissions.md §10."""

    ACTIVE_ACCOUNT = "A"  # Active account and valid session (is_active = True)
    SITE_ISOLATION = "S"  # Resource belongs to user's assigned site
    LIVE_CAMPAIGN = "L"  # Campaign status is 'live'
    MFA_VERIFIED = "M"  # Session has mfa_verified = True (mandatory for admin)
    RECENT_REAUTH = "R"  # Recent password step-up re-authentication (within 10 minutes)
    PUBLISHED_CAMPAIGN = "P"  # Campaign is published (for public consumption)


# Role-Permission Mapping Matrix (docs/03-auth-permissions.md §11)
# Mapping: role -> dict of {Permission: frozenset[Condition]}
ROLE_PERMISSIONS: Mapping[str, Mapping[Permission, frozenset[Condition]]] = {
    # --------------------------------------------------------------------------
    # 1. Anonymous Visitor (anon)
    # --------------------------------------------------------------------------
    "anon": {
        Permission.CAMPAIGN_READ: frozenset({Condition.PUBLISHED_CAMPAIGN}),
        Permission.SITE_READ: frozenset(),
        Permission.CAMPAIGN_SITE_READ: frozenset({Condition.PUBLISHED_CAMPAIGN}),
        Permission.PHOTO_READ: frozenset({Condition.PUBLISHED_CAMPAIGN}),
        Permission.POST_READ: frozenset(),
        Permission.STATS_PUBLIC: frozenset({Condition.PUBLISHED_CAMPAIGN}),
        Permission.PLEDGE_CREATE: frozenset(),
        Permission.EMERGENCY_LIST: frozenset(),
        Permission.EMERGENCY_REVEAL: frozenset(),
    },
    # --------------------------------------------------------------------------
    # 2. Site Supervisor (supervisor)
    # --------------------------------------------------------------------------
    "supervisor": {
        # Public resources (same access as public with P condition on campaign data)
        Permission.CAMPAIGN_READ: frozenset({Condition.PUBLISHED_CAMPAIGN}),
        Permission.SITE_READ: frozenset(),
        Permission.CAMPAIGN_SITE_READ: frozenset({Condition.PUBLISHED_CAMPAIGN}),
        Permission.PHOTO_READ: frozenset({Condition.PUBLISHED_CAMPAIGN}),
        Permission.POST_READ: frozenset(),
        Permission.STATS_PUBLIC: frozenset({Condition.PUBLISHED_CAMPAIGN}),
        Permission.PLEDGE_CREATE: frozenset(),
        Permission.EMERGENCY_LIST: frozenset(),
        Permission.EMERGENCY_REVEAL: frozenset(),
        # Donor and photo field operations (bound to assigned site and live campaign)
        Permission.DONOR_CREATE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.SITE_ISOLATION, Condition.LIVE_CAMPAIGN}
        ),
        Permission.DONOR_READ: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.SITE_ISOLATION, Condition.LIVE_CAMPAIGN}
        ),
        Permission.DONOR_UPDATE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.SITE_ISOLATION, Condition.LIVE_CAMPAIGN}
        ),
        Permission.DONOR_DELETE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.SITE_ISOLATION, Condition.LIVE_CAMPAIGN}
        ),
        Permission.PHOTO_UPLOAD: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.SITE_ISOLATION, Condition.LIVE_CAMPAIGN}
        ),
        Permission.PHOTO_DELETE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.SITE_ISOLATION, Condition.LIVE_CAMPAIGN}
        ),
        # Personal account management
        Permission.ME_READ: frozenset({Condition.ACTIVE_ACCOUNT}),
        Permission.ME_CHANGE_PASSWORD: frozenset({Condition.ACTIVE_ACCOUNT}),
        Permission.ME_SETUP_MFA: frozenset({Condition.ACTIVE_ACCOUNT}),
        Permission.ME_LOGOUT: frozenset({Condition.ACTIVE_ACCOUNT}),
        Permission.ME_LOGOUT_ALL: frozenset({Condition.ACTIVE_ACCOUNT}),
    },
    # --------------------------------------------------------------------------
    # 3. System Administrator (admin)
    # --------------------------------------------------------------------------
    "admin": {
        # Public resources (admin can inspect campaigns in any status, no P required)
        Permission.CAMPAIGN_READ: frozenset(),
        Permission.SITE_READ: frozenset(),
        Permission.CAMPAIGN_SITE_READ: frozenset(),
        Permission.PHOTO_READ: frozenset(),
        Permission.POST_READ: frozenset(),
        Permission.STATS_PUBLIC: frozenset(),
        Permission.PLEDGE_CREATE: frozenset(),
        Permission.EMERGENCY_LIST: frozenset(),
        Permission.EMERGENCY_REVEAL: frozenset(),
        # Donor and photo operations across all sites (MFA verified)
        Permission.DONOR_CREATE: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.DONOR_READ: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.DONOR_UPDATE: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.DONOR_DELETE: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.DONOR_RELOCATE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.PHOTO_UPLOAD: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.PHOTO_DELETE: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        # Administrative governance (MFA required, sensitive ops require step-up R)
        Permission.CAMPAIGN_MANAGE: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.CAMPAIGN_END: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.SITE_MANAGE: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.CAMPAIGN_SITE_MANAGE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}
        ),
        Permission.POST_MANAGE: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.PLEDGE_READ: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.PLEDGE_DELETE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.USER_INVITE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.USER_UPDATE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.USER_DEACTIVATE: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.USER_UNLOCK: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.USER_RESET_MFA: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.AUDIT_READ: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.STATS_ADMIN: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.DATA_DELETE_ON_REQUEST: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED, Condition.RECENT_REAUTH}
        ),
        Permission.FILE_QUEUE_MANAGE: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        # Personal account management
        Permission.ME_READ: frozenset({Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}),
        Permission.ME_CHANGE_PASSWORD: frozenset(
            {Condition.ACTIVE_ACCOUNT, Condition.MFA_VERIFIED}
        ),
        Permission.ME_SETUP_MFA: frozenset({Condition.ACTIVE_ACCOUNT}),
        Permission.ME_LOGOUT: frozenset({Condition.ACTIVE_ACCOUNT}),
        Permission.ME_LOGOUT_ALL: frozenset({Condition.ACTIVE_ACCOUNT}),
    },
}


def get_required_conditions(role: str, permission: Permission) -> frozenset[Condition] | None:
    """Retrieve the set of required conditions for a role and permission.

    Returns:
        frozenset of required Condition enums if the role is granted the permission;
        None if the role does not have this permission.
    """
    role_perms = ROLE_PERMISSIONS.get(role)
    if role_perms is None:
        return None
    return role_perms.get(permission)


def is_permission_granted(role: str, permission: Permission) -> bool:
    """Check if a role is granted a permission under any condition."""
    role_perms = ROLE_PERMISSIONS.get(role)
    if role_perms is None:
        return False
    return permission in role_perms


def requires_step_up(role: str, permission: Permission) -> bool:
    """Check if the role's execution of permission demands recent re-authentication (R)."""
    conditions = get_required_conditions(role, permission)
    if conditions is None:
        return False
    return Condition.RECENT_REAUTH in conditions
