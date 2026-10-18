"""FastAPI application factory and server entrypoint.

Implements P1.8 per docs/05-architecture.md §4, docs/08-setup-env.md §10,
and docs/12-implementation-plan.md:
- create_app() factory assembling logging, error handlers, middleware, and routers.
- Respects DOCS_ENABLED to disable /docs, /redoc, and /openapi.json in production (D-25, T-STRUCT-08).
- Exposes top-level 'app' instance for Uvicorn (`uvicorn app.main:app`).
"""

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.deps import verify_csrf
from app.api.error_handlers import register_error_handlers
from app.api.middleware import register_middleware
from app.api.routers.admin.audit import router as admin_audit_router
from app.api.routers.admin.campaigns import router as admin_campaigns_router
from app.api.routers.admin.donors import router as admin_donors_router
from app.api.routers.admin.file_queue import router as admin_file_queue_router
from app.api.routers.admin.photos import router as admin_photos_router
from app.api.routers.admin.pledges import router as admin_pledges_router
from app.api.routers.admin.posts import router as admin_posts_router
from app.api.routers.admin.sites import router as admin_sites_router
from app.api.routers.admin.uploads import router as admin_uploads_router
from app.api.routers.admin.users import router as admin_users_router
from app.api.routers.auth import router as auth_router
from app.api.routers.me import router as me_router
from app.api.routers.public.campaigns import router as public_campaigns_router
from app.api.routers.public.emergency import router as public_emergency_router
from app.api.routers.public.posts import router as public_posts_router
from app.api.routers.public.sites import router as public_sites_router
from app.api.routers.supervisor.donors import router as supervisor_donors_router
from app.api.routers.supervisor.photos import router as supervisor_photos_router
from app.api.routers.system import router as system_router
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.web.routers.posts import router as web_posts_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure a FastAPI application instance with complete middleware and handlers."""
    cfg = settings or get_settings()

    # 1. Initialize structured logging
    setup_logging(cfg)

    # 2. Guard documentation endpoints per decision D-25 (disabled in prod)
    docs_url = "/docs" if cfg.docs_enabled else None
    redoc_url = "/redoc" if cfg.docs_enabled else None
    openapi_url = "/openapi.json" if cfg.docs_enabled else None

    app = FastAPI(
        title="Blood Donation Platform API",
        description="Unified Arabic RTL Web Platform API for Blood Donation Days",
        version="1.0.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        dependencies=[Depends(verify_csrf)],
    )

    # 3. Register domain and unhandled error handlers
    register_error_handlers(app)

    # 4. Register security and operational middleware pipeline
    register_middleware(app, settings=cfg)

    # 5. Register core system routers (/health, /ready)
    app.include_router(system_router)

    # 6. Register authentication and personal account routers (/api/v1 and root)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(me_router, prefix="/api/v1")
    app.include_router(auth_router)
    app.include_router(me_router)

    # 7. Register admin and public resource routers
    app.include_router(admin_sites_router, prefix="/api/v1")
    app.include_router(admin_sites_router)
    app.include_router(public_sites_router, prefix="/api/v1")
    app.include_router(public_sites_router)

    app.include_router(admin_campaigns_router, prefix="/api/v1")
    app.include_router(admin_campaigns_router)
    app.include_router(public_campaigns_router, prefix="/api/v1")
    app.include_router(public_campaigns_router)
    app.include_router(public_emergency_router, prefix="/api/v1")
    app.include_router(public_emergency_router)

    app.include_router(admin_users_router, prefix="/api/v1")
    app.include_router(admin_users_router)

    app.include_router(admin_audit_router, prefix="/api/v1")
    app.include_router(admin_audit_router)

    app.include_router(admin_uploads_router, prefix="/api/v1")
    app.include_router(admin_uploads_router)

    app.include_router(supervisor_donors_router, prefix="/api/v1")
    app.include_router(supervisor_donors_router)

    app.include_router(supervisor_photos_router, prefix="/api/v1")
    app.include_router(supervisor_photos_router)

    app.include_router(admin_donors_router, prefix="/api/v1")
    app.include_router(admin_donors_router)

    app.include_router(admin_photos_router, prefix="/api/v1")
    app.include_router(admin_photos_router)

    app.include_router(admin_file_queue_router, prefix="/api/v1")
    app.include_router(admin_file_queue_router)

    app.include_router(admin_pledges_router, prefix="/api/v1")
    app.include_router(admin_pledges_router)

    app.include_router(admin_posts_router, prefix="/api/v1")
    app.include_router(admin_posts_router)

    app.include_router(public_posts_router, prefix="/api/v1")
    app.include_router(public_posts_router)

    # 8. Mount local static assets (D-71)
    app.mount("/static", StaticFiles(directory=cfg.static_dir), name="static")

    # 9. Register web page routers on root (D-73)
    app.include_router(web_posts_router)

    return app


# Application singleton entrypoint for ASGI runners (uvicorn app.main:app)
app = create_app()
