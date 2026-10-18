"""Admin Audit API router (P4.6).

Per:
- docs/04-api.md §20 (Audit log inspection: GET /admin/audit)
- docs/06-business-rules.md §13 (BR-AUDIT-01..05)
- docs/03-auth-permissions.md §11.3 (AUDIT_READ permission)
- docs/05-architecture.md §12 (Routers layer rules: no direct repo/model imports)
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_audit_service, require
from app.policy import Permission
from app.schemas.audit import AuditLogOut
from app.services.audit import AuditService

router = APIRouter(prefix="/admin/audit", tags=["Admin Audit"])


@router.get(
    "",
    response_model=list[AuditLogOut],
    status_code=status.HTTP_200_OK,
    summary="List audit logs",
)
def list_audit_logs(
    audit_service: Annotated[AuditService, Depends(get_audit_service)],
    _user: Annotated[Any, Depends(require(Permission.AUDIT_READ))],
    action: Annotated[
        str | None,
        Query(description="Filter by action name"),
    ] = None,
    actor_user_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter by actor user ID"),
    ] = None,
    from_date: Annotated[
        datetime | None,
        Query(alias="from", description="Filter events on or after timestamp"),
    ] = None,
    to_date: Annotated[
        datetime | None,
        Query(alias="to", description="Filter events on or before timestamp"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Page limit (max 100)"),
    ] = 100,
    offset: Annotated[
        int,
        Query(ge=0, description="Page offset"),
    ] = 0,
) -> list[AuditLogOut]:
    """Retrieve audit log entries matching criteria, ordered newest first."""
    entries = audit_service.list_events(
        action=action,
        actor_user_id=actor_user_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    return [AuditLogOut.model_validate(e) for e in entries]
