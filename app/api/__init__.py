"""FastAPI application endpoints, dependencies, middleware, and error handlers."""

from app.api.error_handlers import register_error_handlers

__all__ = ["register_error_handlers"]
