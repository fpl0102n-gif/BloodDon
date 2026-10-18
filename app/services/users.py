"""User administration domain service (P4.5).

Per:
- docs/04-api.md §15 (Users Administration: GET, POST, PATCH, sub-actions)
- docs/06-business-rules.md §11.2 (BR-USER-01..10, BR-AUTH-02, BR-AUTH-13)
- docs/03-auth-permissions.md §11.3 (Administrative governance)
- docs/05-architecture.md §12 (Services layer rules: no direct commit, no HTTP concepts)
"""

import logging
import uuid

from app.core.clock import Clock, get_clock
from app.core.errors import (
    EmailTakenError,
    ForbiddenError,
    LastAdminError,
    NotFoundError,
    ValidationError,
)
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.repositories.sites import SiteRepository
from app.repositories.users import UserRepository
from app.schemas.user import UserAdminUpdateIn, UserInviteIn
from app.services.audit import AuditService
from app.services.auth import AuthService

logger = logging.getLogger("app.services.users")


class UserService:
    """Domain service managing user account administration, invitation lifecycle, and access governance."""

    def __init__(
        self,
        user_repo: UserRepository,
        session_repo: SessionRepository,
        site_repo: SiteRepository,
        auth_service: AuthService,
        audit_service: AuditService,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.user_repo = user_repo
        self.session_repo = session_repo
        self.site_repo = site_repo
        self.auth_service = auth_service
        self.audit_service = audit_service
        self.clock = clock or get_clock()

    def list_users(
        self,
        *,
        role: str | None = None,
        site_id: uuid.UUID | None = None,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[User]:
        """List user accounts matching optional filters with pagination bounded to 100 max."""
        bounded_limit = min(max(1, limit), 100)
        bounded_offset = max(0, offset)
        return self.user_repo.list_users(
            role=role,
            site_id=site_id,
            is_active=is_active,
            limit=bounded_limit,
            offset=bounded_offset,
        )

    def get_user(self, user_id: uuid.UUID) -> User:
        """Retrieve user by ID or raise NotFoundError."""
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"User with ID '{user_id}' not found.")
        return user

    def invite_user(
        self,
        payload: UserInviteIn,
        *,
        actor_user_id: uuid.UUID,
    ) -> tuple[User, str | None]:
        """Invite a new staff user with 72-hour one-time token (BR-USER-01, BR-USER-02, BR-USER-09).

        Returns:
            tuple[User, invite_url | None]
            invite_url is returned once ONLY if email delivery failed (BR-USER-09).
        """
        # BR-USER-02: Email uniqueness (normalized lowercase)
        clean_email = payload.email.strip().lower()
        existing = self.user_repo.get_by_email(clean_email)
        if existing is not None:
            raise EmailTakenError(f"A user with email '{clean_email}' already exists.")

        # BR-USER-01: Supervisor requires site_id; Admin forbids site_id
        if payload.role == "supervisor":
            if payload.site_id is None:
                raise ValidationError("Supervisor role requires a site_id.")
            site = self.site_repo.get_by_id(payload.site_id)
            if site is None:
                raise NotFoundError(f"Site with ID '{payload.site_id}' not found.")
        elif payload.role == "admin" and payload.site_id is not None:
            raise ValidationError("Admin role must not have a site_id.")

        # Create user record (password_hash=None indicates pending invite)
        user = User(
            email=clean_email,
            full_name=payload.full_name.strip(),
            role=payload.role,
            site_id=payload.site_id if payload.role == "supervisor" else None,
            password_hash=None,
            is_active=True,
            must_change_password=False,
            totp_enabled=False,
        )
        user = self.user_repo.create(user)

        # Generate single-use invitation token with 72h validity (BR-AUTH-02)
        _raw_token, invite_url, email_sent = self.auth_service.create_invitation(
            user.id,
            send_email=True,
        )

        # Log audit event (zero PII: no email, no name)
        self.audit_service.log_event(
            action="user.invite",
            entity_type="user",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            details={
                "role": user.role,
                "site_id": str(user.site_id) if user.site_id else None,
                "email_sent": email_sent,
            },
        )

        # BR-USER-09: Fallback invite_url returned once only if email delivery failed
        fallback_invite_url = invite_url if not email_sent else None
        return user, fallback_invite_url

    def resend_invite(
        self,
        user_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> tuple[User, str | None]:
        """Resend invitation for an unactivated user, invalidating previous active tokens (BR-AUTH-02, BR-USER-09)."""
        user = self.get_user(user_id)

        # If user has already set password and activated, invite resend is disallowed
        if user.password_hash is not None:
            raise ValidationError("Cannot resend invitation: account is already activated.")

        _raw_token, invite_url, email_sent = self.auth_service.create_invitation(
            user.id,
            send_email=True,
        )

        self.audit_service.log_event(
            action="user.resend_invite",
            entity_type="user",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            details={
                "role": user.role,
                "site_id": str(user.site_id) if user.site_id else None,
                "email_sent": email_sent,
            },
        )

        fallback_invite_url = invite_url if not email_sent else None
        return user, fallback_invite_url

    def update_user(
        self,
        user_id: uuid.UUID,
        payload: UserAdminUpdateIn,
        *,
        actor_user_id: uuid.UUID,
    ) -> User:
        """Update user role, site, or full name (BR-USER-01, BR-USER-03, BR-USER-04, BR-AUTH-13)."""
        user = self.get_user(user_id)

        # BR-USER-03 ★: Self-modification forbidden
        if actor_user_id == user.id:
            raise ForbiddenError("Self-modification of account role or settings is forbidden.")

        # Determine target final role and site
        new_role = payload.role if payload.role is not None else user.role
        new_site_id = payload.site_id if payload.site_id is not None else user.site_id
        if payload.role == "admin":
            new_site_id = None

        # BR-USER-01: Validate role-site constraints
        if new_role == "supervisor":
            if new_site_id is None:
                raise ValidationError("Supervisor role requires a site_id.")
            site = self.site_repo.get_by_id(new_site_id)
            if site is None:
                raise NotFoundError(f"Site with ID '{new_site_id}' not found.")
        elif new_role == "admin" and new_site_id is not None:
            raise ValidationError("Admin role must not have a site_id.")

        # BR-USER-04 ★: Last active admin protection when demoting admin
        if user.role == "admin" and new_role == "supervisor":
            self.user_repo.acquire_admin_lock()
            if user.is_active and self.user_repo.count_active_admins() <= 1:
                raise LastAdminError("Cannot demote the last active administrator.")

        role_or_site_changed = (new_role != user.role) or (new_site_id != user.site_id)

        if payload.full_name is not None:
            user.full_name = payload.full_name.strip()
        user.role = new_role
        user.site_id = new_site_id

        # BR-AUTH-13 ★: Invalidate all active sessions if role or site changed
        if role_or_site_changed:
            now = self.clock.now()
            revoked = self.session_repo.revoke_all_for_user(user.id, now)
            self.audit_service.log_session_revoked(
                user.id,
                reason="role_or_site_changed",
                actor_user_id=actor_user_id,
                count=revoked,
            )

        self.audit_service.log_event(
            action="user.update",
            entity_type="user",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            details={
                "role": user.role,
                "site_id": str(user.site_id) if user.site_id else None,
            },
        )

        return user

    def deactivate_user(
        self,
        user_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> User:
        """Deactivate account, dropping sessions immediately (BR-USER-03, BR-USER-04, BR-AUTH-13)."""
        user = self.get_user(user_id)

        # BR-USER-03 ★: Self-deactivation forbidden
        if actor_user_id == user.id:
            raise ForbiddenError("Self-deactivation is strictly forbidden.")

        # BR-USER-04 ★: Last active admin protection with advisory lock
        if user.role == "admin" and user.is_active:
            self.user_repo.acquire_admin_lock()
            if self.user_repo.count_active_admins() <= 1:
                raise LastAdminError("Cannot deactivate the last active administrator.")

        user.is_active = False

        # BR-AUTH-13 ★: Invalidate all active sessions immediately
        now = self.clock.now()
        revoked = self.session_repo.revoke_all_for_user(user.id, now)
        self.audit_service.log_session_revoked(
            user.id,
            reason="account_deactivated",
            actor_user_id=actor_user_id,
            count=revoked,
        )

        self.audit_service.log_event(
            action="user.deactivate",
            entity_type="user",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            details={
                "role": user.role,
                "site_id": str(user.site_id) if user.site_id else None,
            },
        )

        return user

    def activate_user(
        self,
        user_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> User:
        """Reactivate a previously deactivated user account."""
        user = self.get_user(user_id)
        user.is_active = True

        self.audit_service.log_event(
            action="user.activate",
            entity_type="user",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            details={
                "role": user.role,
                "site_id": str(user.site_id) if user.site_id else None,
            },
        )

        return user

    def unlock_user(
        self,
        user_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> User:
        """Administratively clear account lockout and failed attempt counters."""
        user = self.get_user(user_id)
        self.user_repo.reset_failed_logins(user)

        self.audit_service.log_event(
            action="user.unlock",
            entity_type="user",
            entity_id=user.id,
            actor_user_id=actor_user_id,
        )

        return user

    def reset_mfa(
        self,
        user_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> User:
        """Reset two-factor authentication, deleting recovery codes and dropping sessions (BR-AUTH-13)."""
        user = self.get_user(user_id)

        user.totp_secret_enc = None
        user.totp_enabled = False
        user.totp_last_used_step = None
        self.user_repo.delete_mfa_recovery_codes(user.id)

        # BR-AUTH-13 ★: Invalidate all active sessions immediately
        now = self.clock.now()
        revoked = self.session_repo.revoke_all_for_user(user.id, now)
        self.audit_service.log_mfa_reset(user.id, actor_user_id=actor_user_id)
        self.audit_service.log_session_revoked(
            user.id,
            reason="mfa_reset",
            actor_user_id=actor_user_id,
            count=revoked,
        )

        return user

    def bulk_deactivate_supervisors(
        self,
        *,
        actor_user_id: uuid.UUID,
    ) -> int:
        """Deactivate all supervisors in bulk after campaign completion (BR-USER-05, BR-AUTH-13)."""
        count = self.user_repo.bulk_deactivate_supervisors()

        # Invalidate all active sessions for supervisors immediately
        now = self.clock.now()
        self.session_repo.revoke_all_for_supervisors(now)

        self.audit_service.log_event(
            action="user.bulk_deactivate_supervisors",
            entity_type="user",
            entity_id=actor_user_id,
            actor_user_id=actor_user_id,
            details={"count": count},
        )

        return count

    def get_admin_warning(self) -> str | None:
        """Check BR-USER-07 advisory condition: warns if only one active admin exists."""
        if self.user_repo.count_active_admins() <= 1:
            return "single_active_admin"
        return None
