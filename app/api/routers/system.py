"""System monitoring and operational health endpoints.

Implements /health and /ready per docs/04-api.md §21, docs/08-setup-env.md §10,
and docs/12-implementation-plan.md (P1.8):
- GET /health: Liveness probe. Always returns 200 if the process is responsive. Does not touch DB.
- GET /ready: Readiness probe. Performs lightweight DB connectivity test (SELECT 1).
  Returns 200 if connected, 503 if disconnected.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.database import check_database_connection

router = APIRouter(tags=["System"])


@router.get("/health", summary="Liveness Probe")
async def health_check() -> dict[str, str]:
    """Check if application process is alive and responsive without touching external services."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness Probe")
async def readiness_check() -> JSONResponse:
    """Check if application dependencies (PostgreSQL database) are ready to serve requests."""
    db_ready = check_database_connection()
    if db_ready:
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "database": "connected"},
        )
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "database": "disconnected"},
    )
