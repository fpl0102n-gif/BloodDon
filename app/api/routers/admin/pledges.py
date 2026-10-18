"""Admin API router for pledge management.

Per:
- docs/04-api.md §16 (DELETE /admin/pledges/{id})
- docs/06-business-rules.md §5 (BR-PLEDGE-10)
- docs/06-business-rules.md §12 (BR-PRIV-10)
- docs/07-test-cases.md §5.12 (T-PRIV-10-a)
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.api.deps import get_pledge_service, require
from app.core.errors import ForbiddenError
from app.policy.permissions import Permission
from app.services.pledges import PledgeService

router = APIRouter(prefix="/admin/pledges", tags=["admin-pledges"])


@router.delete(
    "/{pledge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a pledge record immediately on owner request",
)
def delete_admin_pledge(
    pledge_id: uuid.UUID,
    current_user: Annotated[
        Any,
        Depends(require(Permission.PLEDGE_DELETE, require_reauth=True)),
    ],
    pledge_service: Annotated[PledgeService, Depends(get_pledge_service)],
) -> None:
    """Hard delete a pledge upon request of its owner.

    Per docs/04-api.md §16, BR-PLEDGE-10, BR-PRIV-10, and T-PRIV-10-a:
    - Requires admin role with active session, verified MFA, and fresh step-up reauthentication (Condition R).
    - Emits data.deleted_on_request audit event (zero PII).
    """
    if current_user.role != "admin":
        raise ForbiddenError("This operation is restricted to system administrators.")
    pledge_service.delete_pledge(current_user, pledge_id)
