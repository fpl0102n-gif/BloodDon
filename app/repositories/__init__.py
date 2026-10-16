"""Data access layer encapsulating pure SQLAlchemy queries without business rules."""

from app.repositories.sessions import SessionRepository

__all__ = ["SessionRepository"]
