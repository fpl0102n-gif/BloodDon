"""Pydantic schemas for request input validation and response output serialization."""

from app.schemas.common import (
    AppBaseModel,
    BloodType,
    HeroesPaginationParams,
    NormalizedEmail,
    NormalizedName,
    PaginatedResponse,
    PaginationParams,
    PhoneNumber,
    normalize_email,
    normalize_name,
    normalize_phone_number,
)

__all__ = [
    "AppBaseModel",
    "BloodType",
    "HeroesPaginationParams",
    "NormalizedEmail",
    "NormalizedName",
    "PaginatedResponse",
    "PaginationParams",
    "PhoneNumber",
    "normalize_email",
    "normalize_name",
    "normalize_phone_number",
]
