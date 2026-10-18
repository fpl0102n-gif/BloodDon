"""Pydantic schemas for file and media upload endpoints.

Per:
- docs/04-api.md §18 (POST /admin/uploads response: { key, url })
- docs/06-business-rules.md §10 (BR-PHOTO-14)
- CLAUDE.md / GEMINI.md Rule 6.4: extra="forbid"
"""

from pydantic import ConfigDict

from app.schemas.common import AppBaseModel


class UploadOut(AppBaseModel):
    """Response model for uploaded cover or logo image."""

    key: str
    url: str

    model_config = ConfigDict(extra="forbid")
