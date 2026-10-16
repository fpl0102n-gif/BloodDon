"""Central authorization policy evaluation engine.

Per docs/03-auth-permissions.md §13.2, docs/05-architecture.md §14, and docs/06-business-rules.md:
- Implements single authoritative entry point can(user, permission, context=None) -> bool.
- Evaluates role permissions against the matrix and verifies all required conditions:
    - Condition.ACTIVE_ACCOUNT: rejects inactive users or missing sessions.
    - Condition.SITE_ISOLATION: ensures supervisor only accesses their assigned site.
    - Condition.LIVE_CAMPAIGN: ensures supervisor only acts when campaign is live.
    - Condition.MFA_VERIFIED: ensures admin (or MFA-enforced session) has completed 2FA.
    - Condition.RECENT_REAUTH: ensures sensitive action has fresh confirmation within 10 minutes.
    - Condition.PUBLISHED_CAMPAIGN: ensures public reads only access published campaigns.
- Architectural boundary: NO database queries or HTTP dependencies are permitted in this layer.
"""

from dataclasses import dataclass
from typing import Any

from app.policy.matrix import Condition, get_required_conditions
from app.policy.permissions import Permission


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Contextual metadata passed to policy evaluation for situational condition enforcement."""

    site_id: str | None = None
    campaign_status: str | None = None
    is_published: bool = True


def _extract_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Helper to safely extract attribute from dict or object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def can(
    user: Any,
    permission: Permission | str,
    context: PolicyContext | dict[str, Any] | None = None,
) -> bool:
    """Evaluate whether caller has permission to perform action under current context.

    Args:
        user: Authenticated User entity, MockUser fixture, dict, or None (anonymous visitor).
        permission: Permission enum or exact string value (e.g. 'donor:create').
        context: PolicyContext or dict supplying resource site_id, campaign_status, etc.

    Returns:
        True if the user's role possesses the permission and satisfies all associated conditions;
        False otherwise.
    """
    # 1. Normalize permission token
    if isinstance(permission, Permission):
        perm = permission
    else:
        try:
            perm = Permission(str(permission))
        except ValueError:
            return False

    # 2. Extract user role and state
    if user is None:
        role = "anon"
        is_active = True
        mfa_verified = False
        reauth_valid = False
        user_site_id = None
    else:
        role = str(_extract_attr(user, "role", "anon"))
        is_active = bool(_extract_attr(user, "is_active", True))
        mfa_verified = bool(_extract_attr(user, "mfa_verified", False))
        reauth_valid = bool(_extract_attr(user, "reauth_valid", False))
        user_site_id = _extract_attr(user, "site_id", None)
        if user_site_id is not None:
            user_site_id = str(user_site_id)

    # 3. Lookup conditions for (role, permission)
    conditions = get_required_conditions(role, perm)
    if conditions is None:
        return False

    # 4. If no conditions required (e.g. site:read for anon/supervisor/admin), allow immediately
    if not conditions:
        return True

    # 5. Evaluate required conditions
    # (A) Active account check
    if Condition.ACTIVE_ACCOUNT in conditions and (user is None or not is_active):
        return False

    # (M) MFA verified check
    if Condition.MFA_VERIFIED in conditions and not mfa_verified:
        return False

    # (R) Recent step-up re-authentication check
    if Condition.RECENT_REAUTH in conditions and not reauth_valid:
        return False

    # (S) Site isolation check (supervisor must operate on their assigned site)
    if Condition.SITE_ISOLATION in conditions:
        if user_site_id is None or context is None:
            return False
        ctx_site_id = _extract_attr(context, "site_id", None)
        if ctx_site_id is None or str(ctx_site_id) != user_site_id:
            return False

    # (L) Live campaign check (supervisor operates only when campaign status is 'live')
    if Condition.LIVE_CAMPAIGN in conditions:
        if context is None:
            return False
        camp_status = _extract_attr(context, "campaign_status", None)
        if camp_status != "live":
            return False

    # (P) Published campaign check (public visitors only see published campaigns)
    if Condition.PUBLISHED_CAMPAIGN in conditions and context is not None:
        is_pub = _extract_attr(context, "is_published", True)
        if not is_pub:
            return False

    return True
