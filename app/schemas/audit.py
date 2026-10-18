"""Pydantic schemas for Audit Log inspection.

Per:
- docs/04-api.md §20 (Audit endpoint: GET /admin/audit)
- docs/06-business-rules.md §13 (BR-AUDIT-01..05)
- CLAUDE.md / GEMINI.md Rule 6.4: extra="forbid", zero PII in details, no ORM leakage.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import ConfigDict

from app.schemas.common import AppBaseModel


class AuditLogOut(AppBaseModel):
    """Audit log entry response model."""

    id: int
    at: datetime
    actor_user_id: uuid.UUID | None = None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None = None
    details: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")
