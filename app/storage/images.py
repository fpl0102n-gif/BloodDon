"""Shared image validation, sanitization, and processing pipeline.

Per:
- docs/05-architecture.md §13.1 (Image processing pipeline)
- docs/06-business-rules.md §10:
  - BR-PHOTO-05 (★): Verify actual content (JPEG, PNG, WebP only).
  - BR-PHOTO-06: Max 5MB, reject decompression bombs / excessive dimensions.
  - BR-PHOTO-07 (★): Re-encode, completely strip all EXIF (including GPS), resize longest side <= 1600px.
  - BR-PHOTO-08: Server-generated UUID filename.
  - BR-PHOTO-14: Admin covers/logos processed with identical rules under admin/ prefix.
- docs/07-test-cases.md §5.7:
  - T-PHOTO-05-a (★): Real content inspection (rejects disguised text files with 422).
  - T-PHOTO-05-b (★): GIF, SVG, PDF rejected with 415; JPEG, PNG, WebP accepted with 201.
  - T-PHOTO-06-a: 5MB size limit enforced with 413.
  - T-PHOTO-06-b: Decompression bomb rejected with 422.
  - T-PHOTO-07-a (★): Complete EXIF stripping verified.
  - T-PHOTO-07-b: Downscaling to <= 1600px verified.
"""

import contextlib
import io
from dataclasses import dataclass
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import Settings, get_settings
from app.core.errors import FileTooLargeError, InvalidImageError, UnsupportedMediaTypeError

# Authoritative set of allowed image formats per BR-PHOTO-05
ACCEPTED_IMAGE_FORMATS: Final[frozenset[str]] = frozenset({"JPEG", "PNG", "WEBP"})

# Common recognized but explicitly unsupported formats rejected with 415 per T-PHOTO-05-b
UNSUPPORTED_MEDIA_FORMATS: Final[frozenset[str]] = frozenset(
    {"GIF", "PDF", "TIFF", "BMP", "EPS", "ICO", "PPM", "TGA"}
)

FORMAT_TO_MIME: Final[dict[str, str]] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

FORMAT_TO_EXT: Final[dict[str, str]] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """Represents a validated, sanitized, EXIF-stripped, and resized image payload."""

    content: bytes
    content_type: str
    format: str
    width: int
    height: int
    extension: str


def is_svg_payload(content: bytes) -> bool:
    """Detect SVG XML/text content without XML execution."""
    snippet = content[:2048].strip().lower()
    return b"<svg" in snippet or (b"<?xml" in snippet and b"<svg" in content[:4096].lower())


def process_uploaded_image(
    raw_bytes: bytes,
    *,
    settings: Settings | None = None,
    max_upload_bytes: int | None = None,
    max_side_px: int | None = None,
    max_pixels: int | None = None,
) -> ProcessedImage:
    """Validate, sanitize, strip EXIF, and downscale image bytes.

    Raises:
        FileTooLargeError (413): If raw content size exceeds max_upload_bytes (5MB).
        UnsupportedMediaTypeError (415): If image format is GIF, SVG, PDF, or non-accepted format.
        InvalidImageError (422): If content is empty, corrupted, disguised text, or decompression bomb.
    """
    cfg = settings or get_settings()
    limit_bytes = max_upload_bytes or cfg.max_upload_bytes
    limit_side = max_side_px or cfg.image_max_side_px
    limit_pixels = max_pixels or cfg.image_max_pixels

    # 1. Size bounds check (BR-PHOTO-06, T-PHOTO-06-a)
    if not raw_bytes:
        raise InvalidImageError("Uploaded image file is empty.")

    if len(raw_bytes) > limit_bytes:
        raise FileTooLargeError(
            f"Image file size ({len(raw_bytes)} bytes) exceeds maximum permitted limit ({limit_bytes} bytes)."
        )

    # 2. Early rejection of SVG and PDF formats (T-PHOTO-05-b)
    if is_svg_payload(raw_bytes):
        raise UnsupportedMediaTypeError(
            "Unsupported media type: SVG. Only JPEG, PNG, and WebP are allowed."
        )

    if raw_bytes.startswith(b"%PDF"):
        raise UnsupportedMediaTypeError(
            "Unsupported media type: PDF. Only JPEG, PNG, and WebP are allowed."
        )

    # 3. Configure Pillow decompression bomb guard (T-PHOTO-06-b)
    original_max_pixels = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = limit_pixels

    # 4. Content inspection via Pillow magic header parsing (BR-PHOTO-05, T-PHOTO-05-a)
    try:
        try:
            img = Image.open(io.BytesIO(raw_bytes))
        except Image.DecompressionBombError as exc:
            raise InvalidImageError("Image dimensions exceed safe operating limits.") from exc
        except UnidentifiedImageError as exc:
            raise InvalidImageError("The uploaded file is not a valid or supported image.") from exc
        except Exception as exc:
            raise InvalidImageError("The uploaded file is not a valid image.") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = original_max_pixels

    raw_format = (img.format or "").upper()

    # 5. Format validation (BR-PHOTO-05, T-PHOTO-05-b)
    if raw_format in UNSUPPORTED_MEDIA_FORMATS:
        raise UnsupportedMediaTypeError(
            f"Unsupported image format: '{raw_format}'. Only JPEG, PNG, and WebP are allowed."
        )

    if raw_format not in ACCEPTED_IMAGE_FORMATS:
        raise UnsupportedMediaTypeError(
            f"Unsupported image format: '{raw_format}'. Only JPEG, PNG, and WebP are allowed."
        )

    # Check total pixel count safety (T-PHOTO-06-b)
    if img.width * img.height > limit_pixels:
        raise InvalidImageError("Image dimensions exceed permitted safety limits.")

    # 6. Apply EXIF orientation rotation if present
    with contextlib.suppress(Exception):
        img = ImageOps.exif_transpose(img)

    # 7. Aspect-ratio preserving downscaling (BR-PHOTO-07, T-PHOTO-07-b)
    if max(img.width, img.height) > limit_side:
        img.thumbnail((limit_side, limit_side), Image.Resampling.LANCZOS)

    # 8. Complete EXIF and metadata stripping via clean buffer reconstruction (BR-PHOTO-07, T-PHOTO-07-a)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)

    out_buf = io.BytesIO()

    if raw_format == "PNG":
        target_mode = "RGBA" if has_alpha else "RGB"
        clean = Image.frombytes(target_mode, img.size, img.convert(target_mode).tobytes())
        clean.save(out_buf, format="PNG", optimize=True)
        content_type = FORMAT_TO_MIME["PNG"]
        extension = FORMAT_TO_EXT["PNG"]
    elif raw_format == "WEBP":
        target_mode = "RGBA" if has_alpha else "RGB"
        clean = Image.frombytes(target_mode, img.size, img.convert(target_mode).tobytes())
        clean.save(out_buf, format="WEBP", quality=85)
        content_type = FORMAT_TO_MIME["WEBP"]
        extension = FORMAT_TO_EXT["WEBP"]
    else:  # JPEG
        # Convert any transparent or paletted source to clean RGB with white background
        clean_rgb = Image.new("RGB", img.size, (255, 255, 255))
        if has_alpha:
            clean_rgb.paste(img, mask=img.convert("RGBA").split()[3])
        else:
            clean_rgb.paste(img.convert("RGB"))
        clean = Image.frombytes("RGB", clean_rgb.size, clean_rgb.tobytes())
        clean.save(out_buf, format="JPEG", quality=85, optimize=True)
        content_type = FORMAT_TO_MIME["JPEG"]
        extension = FORMAT_TO_EXT["JPEG"]

    return ProcessedImage(
        content=out_buf.getvalue(),
        content_type=content_type,
        format=raw_format,
        width=clean.width,
        height=clean.height,
        extension=extension,
    )
