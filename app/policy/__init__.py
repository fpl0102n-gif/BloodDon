"""Authorization, permissions matrix, and role evaluation policy engine.

Per docs/03-auth-permissions.md §10-§13 and docs/05-architecture.md §14:
- Centralizes all access control decisions into an isolated, database-agnostic domain layer.
- Exports typed permission tokens, condition rules, role-permission matrix, and can() evaluator.
"""

from app.policy.matrix import (
    ROLE_PERMISSIONS,
    Condition,
    get_required_conditions,
    is_permission_granted,
    requires_step_up,
)
from app.policy.permissions import (
    ADMIN_PERMISSIONS,
    ALL_PERMISSIONS,
    PERSONAL_ACCOUNT_PERMISSIONS,
    PUBLIC_PERMISSIONS,
    SUPERVISOR_FIELD_PERMISSIONS,
    Permission,
)
from app.policy.rules import PolicyContext, can

__all__ = [
    "ADMIN_PERMISSIONS",
    "ALL_PERMISSIONS",
    "PERSONAL_ACCOUNT_PERMISSIONS",
    "PUBLIC_PERMISSIONS",
    "ROLE_PERMISSIONS",
    "SUPERVISOR_FIELD_PERMISSIONS",
    "Condition",
    "Permission",
    "PolicyContext",
    "can",
    "get_required_conditions",
    "is_permission_granted",
    "requires_step_up",
]
