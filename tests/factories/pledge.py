"""Test factories for declarations of intent (pledges)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.pledge import Pledge

_pledge_counter = 0


def create_pledge(
    session: Session | None = None,
    *,
    campaign_id: uuid.UUID,
    site_id: uuid.UUID,
    id_: uuid.UUID | None = None,
    first_name: str | None = None,
    phone: str | None = None,
    created_at: datetime | None = None,
    **kwargs: Any,
) -> Pledge:
    """Create a Pledge model instance obeying character and phone constraints."""
    global _pledge_counter
    _pledge_counter += 1

    if first_name is None:
        first_name = f"متعهد {_pledge_counter}"

    pledge = Pledge(
        id=id_ or uuid.uuid4(),
        campaign_id=campaign_id,
        site_id=site_id,
        first_name=first_name,
        phone=phone,
        created_at=created_at or datetime.now(UTC),
        **kwargs,
    )

    if session is not None:
        session.add(pledge)
        session.flush()

    return pledge
