"""Supervisor field API routes (donors, attendance, photos)."""

from app.api.routers.supervisor.donors import router as supervisor_donors_router
from app.api.routers.supervisor.photos import router as supervisor_photos_router

__all__ = ["supervisor_donors_router", "supervisor_photos_router"]
