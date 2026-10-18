"""Unified error handlers for FastAPI application.

Implements P1.4 per docs/04-api.md §3, docs/05-architecture.md §9, and docs/06-business-rules.md §2:
- Translates domain AppError exceptions to the unified JSON error envelope:
    { "error": { "code": ..., "message": ..., "details": ..., "request_id": ... } }
- Differentiates schema validation errors:
  - 422 unknown_field when extra/forbidden fields are submitted (BR-GEN-06, BR-GEN-07).
  - 422 validation_error for standard input validation failures (BR-GEN-01..05).
- Handles Starlette/FastAPI HTTPException with unified envelopes.
- Catches unhandled unexpected exceptions, logs them with request_id, and returns 500 internal_error
  without leaking stack traces, SQL syntax, or internal structures (Golden Rule 9).
"""

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, ErrorCode

logger = logging.getLogger("app.api.errors")

# Reserved fields that must trigger security log auditing per BR-GEN-07
RESERVED_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        "donated_at",
        "registered_by",
        "anonymized_at",
        "role",
        "site_id",
        "campaign_id",
    }
)


def get_request_id(request: Request) -> str:
    """Extract tracking request ID from request state, header, or generate a fresh UUID."""
    if hasattr(request, "state") and getattr(request.state, "request_id", None):
        return str(request.state.request_id)
    req_id = request.headers.get("x-request-id")
    if req_id:
        return req_id
    return str(uuid.uuid4())


def _extract_field_name(loc: tuple[int | str, ...]) -> str:
    """Extract user-friendly dot-delimited field path from Pydantic loc tuple."""
    filtered = [str(part) for part in loc if part not in ("body", "query", "path", "header")]
    return ".".join(filtered) if filtered else "non_field_error"


def _format_validation_details(errors: Any) -> list[dict[str, str]]:
    """Format Pydantic validation error dictionaries into the API contract details list."""
    details: list[dict[str, str]] = []
    for err in errors:
        loc = err.get("loc", ()) if isinstance(err, dict) else getattr(err, "loc", ())
        field_name = _extract_field_name(loc)
        issue = str(
            err.get("type", "invalid") if isinstance(err, dict) else getattr(err, "type", "invalid")
        )
        details.append({"field": field_name, "issue": issue})
    return details


def _is_emergency_path(request: Request) -> bool:
    """Return True if request path targets public emergency endpoints (BR-EMERG-09)."""
    path = request.url.path
    return path.startswith(("/public/emergency", "/api/v1/public/emergency"))


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handler for all domain AppError exceptions thrown from services or dependencies."""
    request_id = get_request_id(request)
    payload = exc.to_dict(request_id=request_id)
    headers = dict(exc.headers or {})
    if _is_emergency_path(request):
        headers["Cache-Control"] = "no-store"
    return JSONResponse(
        status_code=exc.http_status,
        content=payload,
        headers=headers or None,
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError | PydanticValidationError,
) -> JSONResponse:
    """Handler for request validation errors, differentiating unknown_field from validation_error.

    - BR-GEN-06: Extra fields are strictly forbidden and return 422 unknown_field.
    - BR-GEN-07: Injection of reserved fields emits a security.forbidden_field log.
    - Other validation failures return 422 validation_error.
    """
    request_id = get_request_id(request)
    raw_errors = list(exc.errors())

    extra_forbidden_errors = [
        err
        for err in raw_errors
        if (err.get("type") if isinstance(err, dict) else getattr(err, "type", None))
        == "extra_forbidden"
    ]

    if extra_forbidden_errors:
        details = _format_validation_details(extra_forbidden_errors)
        detected_fields = {d["field"] for d in details}

        # Check for reserved fields injection per BR-GEN-07
        forbidden_matches = detected_fields.intersection(RESERVED_FIELDS)
        if forbidden_matches:
            logger.warning(
                "security.forbidden_field: Client attempted to inject reserved fields %s [request_id=%s]",
                sorted(forbidden_matches),
                request_id,
            )

        payload = {
            "error": {
                "code": ErrorCode.UNKNOWN_FIELD.value,
                "message": "Unknown or forbidden field in request payload.",
                "details": details,
                "request_id": request_id,
            }
        }
        return JSONResponse(
            status_code=422,
            content=payload,
            headers={"Cache-Control": "no-store"} if _is_emergency_path(request) else None,
        )

    # Standard field validation failure (format, type, constraints)
    details = _format_validation_details(raw_errors)
    payload = {
        "error": {
            "code": ErrorCode.VALIDATION_ERROR.value,
            "message": "Request validation failed.",
            "details": details,
            "request_id": request_id,
        }
    }
    return JSONResponse(
        status_code=422,
        content=payload,
        headers={"Cache-Control": "no-store"} if _is_emergency_path(request) else None,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handler for Starlette and FastAPI HTTPExceptions ensuring the unified envelope."""
    request_id = get_request_id(request)

    status_to_code: dict[int, str] = {
        400: ErrorCode.INVALID_TOKEN.value,
        401: ErrorCode.NOT_AUTHENTICATED.value,
        403: ErrorCode.FORBIDDEN.value,
        404: ErrorCode.NOT_FOUND.value,
        409: ErrorCode.INVALID_STATE_TRANSITION.value,
        413: ErrorCode.FILE_TOO_LARGE.value,
        415: ErrorCode.UNSUPPORTED_MEDIA_TYPE.value,
        429: ErrorCode.RATE_LIMITED.value,
        503: ErrorCode.SERVICE_UNAVAILABLE.value,
    }

    code = status_to_code.get(exc.status_code, ErrorCode.INTERNAL_ERROR.value)
    message = str(exc.detail) if exc.detail else "An HTTP error occurred."

    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    headers = dict(exc.headers or {})
    if _is_emergency_path(request):
        headers["Cache-Control"] = "no-store"
    return JSONResponse(
        status_code=exc.status_code,
        content=payload,
        headers=headers or None,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected internal exceptions.

    Logs the error with traceback and request ID internally, but never exposes
    stack traces, SQL statements, or sensitive details to the caller (Golden Rule 9).
    """
    request_id = get_request_id(request)
    logger.exception(
        "Unhandled internal exception [request_id=%s]: %s",
        request_id,
        str(exc),
    )

    payload = {
        "error": {
            "code": ErrorCode.INTERNAL_ERROR.value,
            "message": "An unexpected internal error occurred.",
            "request_id": request_id,
        }
    }
    return JSONResponse(
        status_code=500,
        content=payload,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach all unified error handlers to the FastAPI application instance."""
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(PydanticValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
