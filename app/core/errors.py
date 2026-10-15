"""Domain errors base, error code registry, and unified error specification.

Per docs/04-api.md §3, docs/05-architecture.md §9, and docs/06-business-rules.md:
- Services throw domain exceptions defined in this module.
- Every documented error code in docs/04-api.md §3.2 corresponds 1-to-1 with an AppError subclass.
- Error representations map to the unified error envelope:
    { "error": { "code": ..., "message": ..., "details": ..., "request_id": ... } }
- Technical messages are in English for developer diagnosis and logs; Arabic messages are provided
  for UI client rendering.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Authoritative catalog of API error codes per docs/04-api.md §3.2."""

    # 422 Unprocessable Content / Validation
    VALIDATION_ERROR = "validation_error"
    UNKNOWN_FIELD = "unknown_field"
    PHONE_REQUIRES_CONSENT = "phone_requires_consent"
    PUBLIC_PHONE_REQUIRES_DATA = "public_phone_requires_data"
    INVALID_IMAGE = "invalid_image"
    PHOTO_CONSENT_MISSING = "photo_consent_missing"

    # 401 Unauthorized / Authentication
    NOT_AUTHENTICATED = "not_authenticated"
    SESSION_EXPIRED = "session_expired"
    INVALID_CREDENTIALS = "invalid_credentials"

    # 403 Forbidden / Authorization
    FORBIDDEN = "forbidden"
    MFA_REQUIRED = "mfa_required"
    REAUTH_REQUIRED = "reauth_required"
    CSRF_FAILED = "csrf_failed"

    # 404 Not Found / Out of Scope
    NOT_FOUND = "not_found"

    # 409 Conflict / Domain state violations
    CAMPAIGN_CLOSED = "campaign_closed"
    CAMPAIGN_NOT_LIVE = "campaign_not_live"
    NO_LIVE_CAMPAIGN = "no_live_campaign"
    CAMPAIGN_ALREADY_ENDED = "campaign_already_ended"
    CAMPAIGN_PURGED = "campaign_purged"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    SITE_NOT_IN_CAMPAIGN = "site_not_in_campaign"
    DUPLICATE_ID_CONFLICT = "duplicate_id_conflict"
    SLUG_TAKEN = "slug_taken"
    EMAIL_TAKEN = "email_taken"
    IN_USE = "in_use"
    LAST_ADMIN = "last_admin"

    # 400 Bad Request
    INVALID_TOKEN = "invalid_token"  # noqa: S105

    # 413 Payload Too Large
    FILE_TOO_LARGE = "file_too_large"

    # 415 Unsupported Media Type
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"

    # 429 Too Many Requests
    RATE_LIMITED = "rate_limited"

    # 503 Service Unavailable (Kill Switch)
    SERVICE_UNAVAILABLE = "service_unavailable"

    # 500 Internal Server Error
    INTERNAL_ERROR = "internal_error"


class AppError(Exception):
    """Base domain exception for all expected application errors.

    Every subclass corresponds to an approved ErrorCode in docs/04-api.md §3.2.
    """

    code: ErrorCode
    http_status: int
    message: str

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        self.details: list[dict[str, Any]] | None = details
        self.headers: dict[str, str] | None = headers

    def to_dict(self, request_id: str | None = None) -> dict[str, Any]:
        """Format domain exception into the unified contract envelope."""
        error_payload: dict[str, Any] = {
            "code": str(self.code),
            "message": self.message,
        }
        if self.details is not None:
            error_payload["details"] = self.details
        if request_id is not None:
            error_payload["request_id"] = request_id
        return {"error": error_payload}


# ==============================================================================
# Specific Domain Exception Subclasses (32 Total)
# ==============================================================================


# --- 422 Validation & Consent ---


class ValidationError(AppError):
    """Raised when request payload fails schema validation rules."""

    code = ErrorCode.VALIDATION_ERROR
    http_status = 422
    message = "Request validation failed."


class UnknownFieldError(AppError):
    """Raised when an unapproved or client-injected reserved field is present."""

    code = ErrorCode.UNKNOWN_FIELD
    http_status = 422
    message = "Unknown or forbidden field in request payload."


class PhoneRequiresConsentError(AppError):
    """Raised when phone is provided without consent_keep_phone or consent_public_phone."""

    code = ErrorCode.PHONE_REQUIRES_CONSENT
    http_status = 422
    message = "Phone number cannot be stored without explicit donor consent."


class PublicPhoneRequiresDataError(AppError):
    """Raised when public phone consent is given without valid phone and blood type."""

    code = ErrorCode.PUBLIC_PHONE_REQUIRES_DATA
    http_status = 422
    message = "Public phone consent requires both a valid phone number and blood type."


class InvalidImageError(AppError):
    """Raised when uploaded file is corrupted or not a valid image format."""

    code = ErrorCode.INVALID_IMAGE
    http_status = 422
    message = "The uploaded file is not a valid image."


class PhotoConsentMissingError(AppError):
    """Raised when an individual photo is uploaded for a donor without photo consent."""

    code = ErrorCode.PHOTO_CONSENT_MISSING
    http_status = 422
    message = "Individual photo upload requires explicit donor photo consent."


# --- 401 Authentication ---


class NotAuthenticatedError(AppError):
    """Raised when an unauthenticated request attempts to access protected endpoints."""

    code = ErrorCode.NOT_AUTHENTICATED
    http_status = 401
    message = "Authentication required."


class SessionExpiredError(AppError):
    """Raised when an active session has expired (idle or absolute timeout)."""

    code = ErrorCode.SESSION_EXPIRED
    http_status = 401
    message = "Session has expired. Please log in again."


class InvalidCredentialsError(AppError):
    """Raised when login credentials fail verification (uniform wire response)."""

    code = ErrorCode.INVALID_CREDENTIALS
    http_status = 401
    message = "Invalid email or password."


# --- 403 Authorization & Step-Up ---


class ForbiddenError(AppError):
    """Raised when the authenticated user lacks permission for the requested action."""

    code = ErrorCode.FORBIDDEN
    http_status = 403
    message = "You do not have permission to perform this action."


class MfaRequiredError(AppError):
    """Raised when a partially authenticated session must complete MFA verification."""

    code = ErrorCode.MFA_REQUIRED
    http_status = 403
    message = "Two-factor authentication code required."


class ReauthRequiredError(AppError):
    """Raised when a sensitive action requires recent password confirmation (step-up)."""

    code = ErrorCode.REAUTH_REQUIRED
    http_status = 403
    message = "Recent re-authentication required for this sensitive action."


class CsrfFailedError(AppError):
    """Raised when CSRF token or Origin validation fails on state-changing requests."""

    code = ErrorCode.CSRF_FAILED
    http_status = 403
    message = "CSRF verification failed."


# --- 404 Not Found & Scope ---


class NotFoundError(AppError):
    """Raised when a requested resource does not exist or is out of supervisor scope."""

    code = ErrorCode.NOT_FOUND
    http_status = 404
    message = "Resource not found."


# --- 409 Domain State Conflicts ---


class CampaignClosedError(AppError):
    """Raised when a campaign cannot accept operations because it is unpublished or ended."""

    code = ErrorCode.CAMPAIGN_CLOSED
    http_status = 409
    message = "Campaign is closed and does not accept this operation."


class CampaignNotLiveError(AppError):
    """Raised when field operations require an active live campaign."""

    code = ErrorCode.CAMPAIGN_NOT_LIVE
    http_status = 409
    message = "Campaign is not currently live."


class NoLiveCampaignError(AppError):
    """Raised when an operation requires a currently live campaign but none exists."""

    code = ErrorCode.NO_LIVE_CAMPAIGN
    http_status = 409
    message = "There is currently no live campaign."


class CampaignAlreadyEndedError(AppError):
    """Raised when attempting to end an already ended campaign."""

    code = ErrorCode.CAMPAIGN_ALREADY_ENDED
    http_status = 409
    message = "Campaign has already ended."


class CampaignPurgedError(AppError):
    """Raised when attempting modifications on a campaign whose personal data was purged."""

    code = ErrorCode.CAMPAIGN_PURGED
    http_status = 409
    message = "Campaign personal data has been purged and cannot be modified."


class InvalidStateTransitionError(AppError):
    """Raised when an invalid lifecycle transition is requested."""

    code = ErrorCode.INVALID_STATE_TRANSITION
    http_status = 409
    message = "Invalid state transition for this entity."


class SiteNotInCampaignError(AppError):
    """Raised when a site is not participating in the target campaign."""

    code = ErrorCode.SITE_NOT_IN_CAMPAIGN
    http_status = 409
    message = "The specified site is not participating in this campaign."


class DuplicateIdConflictError(AppError):
    """Raised when a donor client UUID matches an existing record with conflicting payload."""

    code = ErrorCode.DUPLICATE_ID_CONFLICT
    http_status = 409
    message = "ID conflict: record with this identifier already exists with different data."


class SlugTakenError(AppError):
    """Raised when attempting to use a campaign slug that already exists."""

    code = ErrorCode.SLUG_TAKEN
    http_status = 409
    message = "Campaign slug is already taken."


class EmailTakenError(AppError):
    """Raised when creating an account with an email that is already registered."""

    code = ErrorCode.EMAIL_TAKEN
    http_status = 409
    message = "Email address is already in use."


class InUseError(AppError):
    """Raised when attempting to delete an entity that has active dependent references."""

    code = ErrorCode.IN_USE
    http_status = 409
    message = "Resource cannot be deleted because dependent data exists."


class LastAdminError(AppError):
    """Raised when attempting to deactivate or remove the last active administrator or self."""

    code = ErrorCode.LAST_ADMIN
    http_status = 409
    message = "Cannot deactivate or delete the last active administrator or self."


# --- 400 Bad Request ---


class InvalidTokenError(AppError):
    """Raised when an invite or password reset token is invalid, expired, or already used."""

    code = ErrorCode.INVALID_TOKEN
    http_status = 400
    message = "Invalid, expired, or already used token."


# --- 413 & 415 Media Files ---


class FileTooLargeError(AppError):
    """Raised when an uploaded file exceeds the maximum permitted size limit."""

    code = ErrorCode.FILE_TOO_LARGE
    http_status = 413
    message = "File size exceeds the permitted limit."


class UnsupportedMediaTypeError(AppError):
    """Raised when uploaded media MIME type is unsupported."""

    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    http_status = 415
    message = "Unsupported media type."


# --- 429 Rate Limiting ---


class RateLimitedError(AppError):
    """Raised when request frequency exceeds rate limiting thresholds."""

    code = ErrorCode.RATE_LIMITED
    http_status = 429
    message = "Too many requests. Please try again later."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        super().__init__(message, details=details, headers=headers)
        self.retry_after = retry_after


# --- 503 & 500 Infrastructure ---


class ServiceUnavailableError(AppError):
    """Raised when a feature or service is disabled via operational kill switch."""

    code = ErrorCode.SERVICE_UNAVAILABLE
    http_status = 503
    message = "This service is temporarily unavailable."


class InternalError(AppError):
    """Raised when an unexpected internal failure occurs."""

    code = ErrorCode.INTERNAL_ERROR
    http_status = 500
    message = "An unexpected internal error occurred."


# ==============================================================================
# Error Specification & Registry Metadata
# ==============================================================================


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """Specification record for an API error code."""

    code: ErrorCode
    http_status: int
    default_message: str
    ar_message: str
    exception_class: type[AppError]


ERROR_SPEC_REGISTRY: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.VALIDATION_ERROR: ErrorSpec(
        code=ErrorCode.VALIDATION_ERROR,
        http_status=422,
        default_message="Request validation failed.",
        ar_message="يرجى مراجعة الحقول المحددة",
        exception_class=ValidationError,
    ),
    ErrorCode.UNKNOWN_FIELD: ErrorSpec(
        code=ErrorCode.UNKNOWN_FIELD,
        http_status=422,
        default_message="Unknown or forbidden field in request payload.",
        ar_message="حقل غير معرّف أو محجوز",
        exception_class=UnknownFieldError,
    ),
    ErrorCode.PHONE_REQUIRES_CONSENT: ErrorSpec(
        code=ErrorCode.PHONE_REQUIRES_CONSENT,
        http_status=422,
        default_message="Phone number cannot be stored without explicit donor consent.",
        ar_message="لا يمكن حفظ الرقم دون موافقة المتبرع",
        exception_class=PhoneRequiresConsentError,
    ),
    ErrorCode.PUBLIC_PHONE_REQUIRES_DATA: ErrorSpec(
        code=ErrorCode.PUBLIC_PHONE_REQUIRES_DATA,
        http_status=422,
        default_message="Public phone consent requires both a valid phone number and blood type.",
        ar_message="نشر الرقم يتطلب رقم هاتف وفصيلة دم",
        exception_class=PublicPhoneRequiresDataError,
    ),
    ErrorCode.INVALID_IMAGE: ErrorSpec(
        code=ErrorCode.INVALID_IMAGE,
        http_status=422,
        default_message="The uploaded file is not a valid image.",
        ar_message="الملف ليس صورة مدعومة",
        exception_class=InvalidImageError,
    ),
    ErrorCode.PHOTO_CONSENT_MISSING: ErrorSpec(
        code=ErrorCode.PHOTO_CONSENT_MISSING,
        http_status=422,
        default_message="Individual photo upload requires explicit donor photo consent.",
        ar_message="لا يمكن رفع صورة هذا المتبرع دون موافقته",
        exception_class=PhotoConsentMissingError,
    ),
    ErrorCode.NOT_AUTHENTICATED: ErrorSpec(
        code=ErrorCode.NOT_AUTHENTICATED,
        http_status=401,
        default_message="Authentication required.",
        ar_message="يرجى تسجيل الدخول أولاً",
        exception_class=NotAuthenticatedError,
    ),
    ErrorCode.SESSION_EXPIRED: ErrorSpec(
        code=ErrorCode.SESSION_EXPIRED,
        http_status=401,
        default_message="Session has expired. Please log in again.",
        ar_message="انتهت الجلسة، يرجى الدخول مجددًا",
        exception_class=SessionExpiredError,
    ),
    ErrorCode.INVALID_CREDENTIALS: ErrorSpec(
        code=ErrorCode.INVALID_CREDENTIALS,
        http_status=401,
        default_message="Invalid email or password.",
        ar_message="بيانات الدخول غير صحيحة",
        exception_class=InvalidCredentialsError,
    ),
    ErrorCode.FORBIDDEN: ErrorSpec(
        code=ErrorCode.FORBIDDEN,
        http_status=403,
        default_message="You do not have permission to perform this action.",
        ar_message="ليست لديك صلاحية لهذا الإجراء",
        exception_class=ForbiddenError,
    ),
    ErrorCode.MFA_REQUIRED: ErrorSpec(
        code=ErrorCode.MFA_REQUIRED,
        http_status=403,
        default_message="Two-factor authentication code required.",
        ar_message="يرجى إدخال رمز المصادقة الثنائية",
        exception_class=MfaRequiredError,
    ),
    ErrorCode.REAUTH_REQUIRED: ErrorSpec(
        code=ErrorCode.REAUTH_REQUIRED,
        http_status=403,
        default_message="Recent re-authentication required for this sensitive action.",
        ar_message="للتأكيد، أدخل كلمة المرور",
        exception_class=ReauthRequiredError,
    ),
    ErrorCode.CSRF_FAILED: ErrorSpec(
        code=ErrorCode.CSRF_FAILED,
        http_status=403,
        default_message="CSRF verification failed.",
        ar_message="فشل التحقق من أمان الطلب",
        exception_class=CsrfFailedError,
    ),
    ErrorCode.NOT_FOUND: ErrorSpec(
        code=ErrorCode.NOT_FOUND,
        http_status=404,
        default_message="Resource not found.",
        ar_message="المورد المطلوب غير موجود",
        exception_class=NotFoundError,
    ),
    ErrorCode.CAMPAIGN_CLOSED: ErrorSpec(
        code=ErrorCode.CAMPAIGN_CLOSED,
        http_status=409,
        default_message="Campaign is closed and does not accept this operation.",
        ar_message="عذرًا، هذه العملية غير متاحة الآن",
        exception_class=CampaignClosedError,
    ),
    ErrorCode.CAMPAIGN_NOT_LIVE: ErrorSpec(
        code=ErrorCode.CAMPAIGN_NOT_LIVE,
        http_status=409,
        default_message="Campaign is not currently live.",
        ar_message="عذرًا، هذه العملية غير متاحة الآن",
        exception_class=CampaignNotLiveError,
    ),
    ErrorCode.NO_LIVE_CAMPAIGN: ErrorSpec(
        code=ErrorCode.NO_LIVE_CAMPAIGN,
        http_status=409,
        default_message="There is currently no live campaign.",
        ar_message="عذرًا، هذه العملية غير متاحة الآن",
        exception_class=NoLiveCampaignError,
    ),
    ErrorCode.CAMPAIGN_ALREADY_ENDED: ErrorSpec(
        code=ErrorCode.CAMPAIGN_ALREADY_ENDED,
        http_status=409,
        default_message="Campaign has already ended.",
        ar_message="الحملة منتهية بالفعل",
        exception_class=CampaignAlreadyEndedError,
    ),
    ErrorCode.CAMPAIGN_PURGED: ErrorSpec(
        code=ErrorCode.CAMPAIGN_PURGED,
        http_status=409,
        default_message="Campaign personal data has been purged and cannot be modified.",
        ar_message="تم إخفاء بيانات الحملة مسبقًا",
        exception_class=CampaignPurgedError,
    ),
    ErrorCode.INVALID_STATE_TRANSITION: ErrorSpec(
        code=ErrorCode.INVALID_STATE_TRANSITION,
        http_status=409,
        default_message="Invalid state transition for this entity.",
        ar_message="لا يمكن تنفيذ هذا الإجراء في الحالة الحالية للحملة",
        exception_class=InvalidStateTransitionError,
    ),
    ErrorCode.SITE_NOT_IN_CAMPAIGN: ErrorSpec(
        code=ErrorCode.SITE_NOT_IN_CAMPAIGN,
        http_status=409,
        default_message="The specified site is not participating in this campaign.",
        ar_message="الموقع غير مشارك في الحملة",
        exception_class=SiteNotInCampaignError,
    ),
    ErrorCode.DUPLICATE_ID_CONFLICT: ErrorSpec(
        code=ErrorCode.DUPLICATE_ID_CONFLICT,
        http_status=409,
        default_message="ID conflict: record with this identifier already exists with different data.",
        ar_message="المعرّف مستعمل بالفعل بسجل مختلف",
        exception_class=DuplicateIdConflictError,
    ),
    ErrorCode.SLUG_TAKEN: ErrorSpec(
        code=ErrorCode.SLUG_TAKEN,
        http_status=409,
        default_message="Campaign slug is already taken.",
        ar_message="هذا الرابط مستعمل بالفعل",
        exception_class=SlugTakenError,
    ),
    ErrorCode.EMAIL_TAKEN: ErrorSpec(
        code=ErrorCode.EMAIL_TAKEN,
        http_status=409,
        default_message="Email address is already in use.",
        ar_message="البريد الإلكتروني مستعمل بالفعل",
        exception_class=EmailTakenError,
    ),
    ErrorCode.IN_USE: ErrorSpec(
        code=ErrorCode.IN_USE,
        http_status=409,
        default_message="Resource cannot be deleted because dependent data exists.",
        ar_message="لا يمكن الحذف لوجود بيانات مرتبطة",
        exception_class=InUseError,
    ),
    ErrorCode.LAST_ADMIN: ErrorSpec(
        code=ErrorCode.LAST_ADMIN,
        http_status=409,
        default_message="Cannot deactivate or delete the last active administrator or self.",
        ar_message="لا يمكن تعطيل آخر أدمن",
        exception_class=LastAdminError,
    ),
    ErrorCode.INVALID_TOKEN: ErrorSpec(
        code=ErrorCode.INVALID_TOKEN,
        http_status=400,
        default_message="Invalid, expired, or already used token.",
        ar_message="الرابط غير صالح أو منتهي الصلاحية",
        exception_class=InvalidTokenError,
    ),
    ErrorCode.FILE_TOO_LARGE: ErrorSpec(
        code=ErrorCode.FILE_TOO_LARGE,
        http_status=413,
        default_message="File size exceeds the permitted limit.",
        ar_message="حجم الصورة كبير جدًا",
        exception_class=FileTooLargeError,
    ),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: ErrorSpec(
        code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        http_status=415,
        default_message="Unsupported media type.",
        ar_message="الملف ليس صورة مدعومة",
        exception_class=UnsupportedMediaTypeError,
    ),
    ErrorCode.RATE_LIMITED: ErrorSpec(
        code=ErrorCode.RATE_LIMITED,
        http_status=429,
        default_message="Too many requests. Please try again later.",
        ar_message="حاول مرة أخرى بعد قليل",
        exception_class=RateLimitedError,
    ),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorSpec(
        code=ErrorCode.SERVICE_UNAVAILABLE,
        http_status=503,
        default_message="This service is temporarily unavailable.",
        ar_message="هذه الخدمة غير متاحة حاليًا",
        exception_class=ServiceUnavailableError,
    ),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(
        code=ErrorCode.INTERNAL_ERROR,
        http_status=500,
        default_message="An unexpected internal error occurred.",
        ar_message="حدث خطأ غير متوقع، حاول لاحقًا",
        exception_class=InternalError,
    ),
}

# Derived bidirectional maps
CODE_TO_EXCEPTION: dict[str, type[AppError]] = {
    spec.code.value: spec.exception_class for spec in ERROR_SPEC_REGISTRY.values()
}

EXCEPTION_TO_CODE: dict[type[AppError], ErrorCode] = {
    spec.exception_class: spec.code for spec in ERROR_SPEC_REGISTRY.values()
}

ARABIC_ERROR_MESSAGES: dict[str, str] = {
    spec.code.value: spec.ar_message for spec in ERROR_SPEC_REGISTRY.values()
}


def get_error_spec(code: str | ErrorCode) -> ErrorSpec:
    """Retrieve the error specification by code."""
    key = code if isinstance(code, ErrorCode) else ErrorCode(code)
    return ERROR_SPEC_REGISTRY[key]


def get_arabic_message(code: str | ErrorCode) -> str:
    """Retrieve Arabic user-facing message corresponding to an error code."""
    spec = get_error_spec(code)
    return spec.ar_message
