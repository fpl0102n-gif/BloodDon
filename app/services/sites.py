"""Domain service for physical donation site (club) management (P4.1).

Per:
- docs/04-api.md §14 (Admin Sites API: GET, POST, PATCH, DELETE)
- docs/04-api.md §6.9 (Public Sites API: GET /public/sites)
- docs/06-business-rules.md §4 (BR-SITE-01..04)
- docs/05-architecture.md §12 (Services layer rules: no direct commit, no HTTP concepts)
"""

import logging
import uuid
from collections.abc import Sequence

from app.core.clock import Clock, get_clock
from app.core.errors import InUseError, NotFoundError
from app.models.campaign import Site
from app.repositories.sites import SiteRepository
from app.schemas.site import SiteCreate, SiteUpdate
from app.services.audit import AuditService

logger = logging.getLogger("app.services.sites")


class SiteService:
    """Service governing donation sites lifecycle, deactivation, and conditional deletion."""

    def __init__(
        self,
        repo: SiteRepository,
        *,
        audit_service: AuditService | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repo = repo
        self.audit_service = audit_service
        self.clock = clock or get_clock()

    def list_sites(self, *, include_inactive: bool = True) -> Sequence[Site]:
        """List donation sites, optionally including deactivated sites."""
        return self.repo.list_all(include_inactive=include_inactive)

    def get_site(self, site_id: uuid.UUID) -> Site:
        """Retrieve a site by ID or raise NotFoundError."""
        site = self.repo.get_by_id(site_id)
        if site is None:
            raise NotFoundError(f"Site with ID '{site_id}' not found.")
        return site

    def create_site(
        self,
        data: SiteCreate,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> Site:
        """Create a new donation site."""
        site = Site(
            name=data.name,
            address=data.address,
            map_url=str(data.map_url) if data.map_url else None,
            latitude=data.latitude,
            longitude=data.longitude,
            description=data.description,
            logo_key=data.logo_key,
            is_active=True,
        )

        created = self.repo.create(site)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="site.create",
                entity_type="site",
                entity_id=created.id,
                actor_user_id=actor_user_id,
            )

        return created

    def update_site(
        self,
        site_id: uuid.UUID,
        data: SiteUpdate,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> Site:
        """Update an existing site's details or deactivation state (BR-SITE-02)."""
        site = self.get_site(site_id)

        if data.name is not None:
            site.name = data.name
        if data.address is not None:
            site.address = data.address
        if data.map_url is not None:
            site.map_url = str(data.map_url) if data.map_url else None
        if data.latitude is not None:
            site.latitude = data.latitude
        if data.longitude is not None:
            site.longitude = data.longitude
        if data.description is not None:
            site.description = data.description
        if data.logo_key is not None:
            site.logo_key = data.logo_key
        if data.is_active is not None:
            site.is_active = data.is_active

        updated = self.repo.update(site)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="site.update",
                entity_type="site",
                entity_id=updated.id,
                actor_user_id=actor_user_id,
                details={"is_active": updated.is_active} if data.is_active is not None else None,
            )

        return updated

    def delete_site(
        self,
        site_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        """Conditionally delete an unused site or raise 409 in_use per BR-SITE-03."""
        site = self.get_site(site_id)

        # BR-SITE-03: Deletion only allowed if never used in any campaign
        if self.repo.is_site_in_use(site_id):
            raise InUseError(
                "Site cannot be deleted because it has been used in campaigns. Deactivate it instead."
            )

        self.repo.delete(site)

        if self.audit_service and actor_user_id:
            self.audit_service.log_event(
                action="site.delete",
                entity_type="site",
                entity_id=site_id,
                actor_user_id=actor_user_id,
            )
