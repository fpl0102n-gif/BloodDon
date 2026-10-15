"""FastAPI application factory and server entrypoint.

Implements P1.8 per docs/05-architecture.md §4, docs/08-setup-env.md §10,
and docs/12-implementation-plan.md:
- create_app() factory assembling logging, error handlers, middleware, and routers.
- Respects DOCS_ENABLED to disable /docs, /redoc, and /openapi.json in production (D-25, T-STRUCT-08).
- Exposes top-level 'app' instance for Uvicorn (`uvicorn app.main:app`).
"""

from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.middleware import register_middleware
from app.api.routers.system import router as system_router
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging


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
    )

    # 3. Register domain and unhandled error handlers
    register_error_handlers(app)

    # 4. Register security and operational middleware pipeline
    register_middleware(app, settings=cfg)

    # 5. Register core system routers (/health, /ready)
    app.include_router(system_router)

    return app


# Application singleton entrypoint for ASGI runners (uvicorn app.main:app)
app = create_app()
