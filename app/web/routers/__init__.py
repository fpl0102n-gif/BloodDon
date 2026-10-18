"""HTML page routers serving views at the application root."""

from app.web.routers.posts import router as posts_web_router

__all__ = ["posts_web_router"]
