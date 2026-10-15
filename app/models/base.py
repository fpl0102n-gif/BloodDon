"""Declarative base class for all SQLAlchemy ORM models.

Per docs/02-database.md §3 and docs/05-architecture.md §4:
Centralizes model metadata and ensures all models share the unified naming
convention (ix_, uq_, ck_, fk_, pk_) for consistent Alembic migrations.
"""

from sqlalchemy.orm import DeclarativeBase

from app.core.database import metadata


class Base(DeclarativeBase):
    """Base declarative class bound to central metadata with naming conventions."""

    metadata = metadata
