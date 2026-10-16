"""Permission code registry defining all system authorization tokens.

Per docs/03-auth-permissions.md §11, docs/04-api.md §22, and docs/05-architecture.md §14:
- All system operations declare a discrete permission code in 'resource:action' format.
- Permission codes are strictly typed using StrEnum to eliminate string typos and enable
  compile-time static verification.
- Exactly 37 permissions cover public access, supervisor field operations,
  administrative governance, and self-service account management.
"""

from enum import StrEnum


class Permission(StrEnum):
    """System permission codes following resource:action convention."""

    # --------------------------------------------------------------------------
    # 1. Public Resources (docs/03-auth-permissions.md §11.1)
    # --------------------------------------------------------------------------
    CAMPAIGN_READ = "campaign:read"
    SITE_READ = "site:read"
    CAMPAIGN_SITE_READ = "campaign_site:read"
    PHOTO_READ = "photo:read"
    POST_READ = "post:read"
    STATS_PUBLIC = "stats:public"
    PLEDGE_CREATE = "pledge:create"
    EMERGENCY_LIST = "emergency:list"
    EMERGENCY_REVEAL = "emergency:reveal"

    # --------------------------------------------------------------------------
    # 2. Donors and Photos - Field Operations (§11.2)
    # --------------------------------------------------------------------------
    DONOR_CREATE = "donor:create"
    DONOR_READ = "donor:read"
    DONOR_UPDATE = "donor:update"
    DONOR_DELETE = "donor:delete"
    DONOR_RELOCATE = "donor:relocate"
    PHOTO_UPLOAD = "photo:upload"
    PHOTO_DELETE = "photo:delete"

    # --------------------------------------------------------------------------
    # 3. Administration and Governance (§11.3)
    # --------------------------------------------------------------------------
    CAMPAIGN_MANAGE = "campaign:manage"
    CAMPAIGN_END = "campaign:end"
    SITE_MANAGE = "site:manage"
    CAMPAIGN_SITE_MANAGE = "campaign_site:manage"
    POST_MANAGE = "post:manage"
    PLEDGE_READ = "pledge:read"
    PLEDGE_DELETE = "pledge:delete"
    USER_INVITE = "user:invite"
    USER_UPDATE = "user:update"
    USER_DEACTIVATE = "user:deactivate"
    USER_UNLOCK = "user:unlock"
    USER_RESET_MFA = "user:reset_mfa"
    AUDIT_READ = "audit:read"
    STATS_ADMIN = "stats:admin"
    DATA_DELETE_ON_REQUEST = "data:delete_on_request"
    FILE_QUEUE_MANAGE = "file_queue:manage"

    # --------------------------------------------------------------------------
    # 4. Personal Account Management (§11.4)
    # --------------------------------------------------------------------------
    ME_READ = "me:read"
    ME_CHANGE_PASSWORD = "me:change_password"  # noqa: S105 # pragma: allowlist secret
    ME_SETUP_MFA = "me:setup_mfa"
    ME_LOGOUT = "me:logout"
    ME_LOGOUT_ALL = "me:logout_all"


# Categorized permission groupings for policy validation and inspection
PUBLIC_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.CAMPAIGN_READ,
        Permission.SITE_READ,
        Permission.CAMPAIGN_SITE_READ,
        Permission.PHOTO_READ,
        Permission.POST_READ,
        Permission.STATS_PUBLIC,
        Permission.PLEDGE_CREATE,
        Permission.EMERGENCY_LIST,
        Permission.EMERGENCY_REVEAL,
    }
)

SUPERVISOR_FIELD_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.DONOR_CREATE,
        Permission.DONOR_READ,
        Permission.DONOR_UPDATE,
        Permission.DONOR_DELETE,
        Permission.PHOTO_UPLOAD,
        Permission.PHOTO_DELETE,
    }
)

ADMIN_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.CAMPAIGN_MANAGE,
        Permission.CAMPAIGN_END,
        Permission.SITE_MANAGE,
        Permission.CAMPAIGN_SITE_MANAGE,
        Permission.POST_MANAGE,
        Permission.PLEDGE_READ,
        Permission.PLEDGE_DELETE,
        Permission.USER_INVITE,
        Permission.USER_UPDATE,
        Permission.USER_DEACTIVATE,
        Permission.USER_UNLOCK,
        Permission.USER_RESET_MFA,
        Permission.AUDIT_READ,
        Permission.STATS_ADMIN,
        Permission.DATA_DELETE_ON_REQUEST,
        Permission.FILE_QUEUE_MANAGE,
        Permission.DONOR_RELOCATE,
    }
)

PERSONAL_ACCOUNT_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.ME_READ,
        Permission.ME_CHANGE_PASSWORD,
        Permission.ME_SETUP_MFA,
        Permission.ME_LOGOUT,
        Permission.ME_LOGOUT_ALL,
    }
)

ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)
