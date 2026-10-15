"""FastAPI application endpoints, dependencies, middleware, and error handlers."""

from app.api.error_handlers import register_error_handlers
from app.api.middleware import (
    ClientIPMiddleware,
    OriginCheckMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    register_middleware,
)

__all__ = [
    "ClientIPMiddleware",
    "OriginCheckMiddleware",
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "register_error_handlers",
    "register_middleware",
]
