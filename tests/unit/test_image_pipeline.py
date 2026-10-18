"""Unit tests for the image processing pipeline and storage backends (P4.7).

Per:
- docs/05-architecture.md §13 (Storage and image pipeline)
- docs/06-business-rules.md §10:
  - BR-PHOTO-05 (★): Verified image content (JPEG, PNG, WebP only).
  - BR-PHOTO-06: 5MB size limit, decompression bomb protection.
  - BR-PHOTO-07 (★): Re-encoded, EXIF completely stripped, resized longest side <= 1600px.
  - BR-PHOTO-08: Server-generated UUID key, no client filename used.
  - BR-PHOTO-14: Admin covers/logos processed with identical rules.
- docs/07-test-cases.md §5.7:
  - T-PHOTO-05-a (★): Real content inspection (rejects disguised text files with 422).
  - T-PHOTO-05-b (★): GIF, SVG, PDF rejected with 415; JPEG, PNG, WebP accepted with 201.
  - T-PHOTO-06-a: 5MB size limit enforced with 413.
  - T-PHOTO-06-b: Decompression bomb rejected with 422.
  - T-PHOTO-07-a (★): Complete EXIF stripping verified.
  - T-PHOTO-07-b: Downscaling to <= 1600px verified.
"""

import io
from pathlib import Path

import pytest
from PIL import Image

from app.core.errors import FileTooLargeError, InvalidImageError, UnsupportedMediaTypeError
from app.services.uploads import read_upload_file_safely
from app.storage.images import (
    is_svg_payload,
    process_uploaded_image,
)
from app.storage.local import LocalStorage

# ==============================================================================
# Helper functions to construct test image buffers
# ==============================================================================


def make_image_bytes(
    fmt: str = "JPEG",
    size: tuple[int, int] = (100, 100),
    color: tuple[int, int, int] = (255, 0, 0),
    mode: str = "RGB",
    exif_data: dict[int, str] | None = None,
) -> bytes:
    """Generate in-memory image bytes with optional EXIF metadata."""
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    if exif_data and fmt.upper() == "JPEG":
        exif = img.getexif()
        for tag, val in exif_data.items():
            exif[tag] = val
        img.save(buf, format=fmt, exif=exif)
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


# ==============================================================================
# 1. Real Content Inspection & Format Acceptance (BR-PHOTO-05, T-PHOTO-05)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-05")
def test_t_photo_05_a_rejects_disguised_text_file() -> None:
    """T-PHOTO-05-a (★): Non-image content with image extension/name is rejected with 422 invalid_image."""
    fake_payload = b"This is not a real image, just plain ASCII text content."
    with pytest.raises(InvalidImageError, match="not a valid or supported image"):
        process_uploaded_image(fake_payload)


@pytest.mark.br("BR-PHOTO-05")
def test_rejects_empty_payload() -> None:
    """Empty payload raises InvalidImageError (422)."""
    with pytest.raises(InvalidImageError, match="empty"):
        process_uploaded_image(b"")


@pytest.mark.br("BR-PHOTO-05")
def test_rejects_corrupted_binary() -> None:
    """Corrupted binary payload raises InvalidImageError (422)."""
    corrupt = b"\xff\xd8\xff\xe0" + b"\x00" * 30  # Broken JPEG header
    with pytest.raises(InvalidImageError):
        process_uploaded_image(corrupt)


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-05")
def test_t_photo_05_b_rejects_svg() -> None:
    """T-PHOTO-05-b (★): SVG images are rejected with 415 unsupported_media_type."""
    svg_data = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="40"/></svg>'
    assert is_svg_payload(svg_data)
    with pytest.raises(UnsupportedMediaTypeError, match="SVG"):
        process_uploaded_image(svg_data)

    xml_svg = b'<?xml version="1.0" encoding="utf-8"?>\n<svg viewBox="0 0 10 10"></svg>'
    assert is_svg_payload(xml_svg)
    with pytest.raises(UnsupportedMediaTypeError, match="SVG"):
        process_uploaded_image(xml_svg)


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-05")
def test_t_photo_05_b_rejects_pdf() -> None:
    """T-PHOTO-05-b (★): PDF files are rejected with 415 unsupported_media_type."""
    pdf_data = b"%PDF-1.4\n%...\n%%EOF"
    with pytest.raises(UnsupportedMediaTypeError, match="PDF"):
        process_uploaded_image(pdf_data)


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-05")
def test_t_photo_05_b_rejects_gif() -> None:
    """T-PHOTO-05-b (★): GIF images are recognized but rejected with 415 unsupported_media_type."""
    gif_bytes = make_image_bytes(fmt="GIF", size=(50, 50))
    with pytest.raises(UnsupportedMediaTypeError, match="GIF"):
        process_uploaded_image(gif_bytes)


@pytest.mark.br("BR-PHOTO-05")
def test_t_photo_05_b_accepts_jpeg_png_webp() -> None:
    """T-PHOTO-05-b (★): JPEG, PNG, and WebP are accepted and successfully processed."""
    # JPEG
    jpeg_bytes = make_image_bytes(fmt="JPEG", size=(120, 80))
    res_jpeg = process_uploaded_image(jpeg_bytes)
    assert res_jpeg.format == "JPEG"
    assert res_jpeg.content_type == "image/jpeg"
    assert res_jpeg.extension == ".jpg"
    assert res_jpeg.width == 120
    assert res_jpeg.height == 80

    # PNG
    png_bytes = make_image_bytes(fmt="PNG", size=(64, 64))
    res_png = process_uploaded_image(png_bytes)
    assert res_png.format == "PNG"
    assert res_png.content_type == "image/png"
    assert res_png.extension == ".png"
    assert res_png.width == 64
    assert res_png.height == 64

    # WebP
    webp_bytes = make_image_bytes(fmt="WEBP", size=(90, 70))
    res_webp = process_uploaded_image(webp_bytes)
    assert res_webp.format == "WEBP"
    assert res_webp.content_type == "image/webp"
    assert res_webp.extension == ".webp"
    assert res_webp.width == 90
    assert res_webp.height == 70


# ==============================================================================
# 2. File Size & Decompression Bomb Protection (BR-PHOTO-06, T-PHOTO-06)
# ==============================================================================


@pytest.mark.br("BR-PHOTO-06")
def test_t_photo_06_a_enforces_max_upload_bytes() -> None:
    """T-PHOTO-06-a: Payloads exceeding max_upload_bytes raise 413 FileTooLargeError."""
    limit = 500
    oversized = b"\xff\xd8\xff\xe0" + b"\x00" * 600
    with pytest.raises(FileTooLargeError):
        process_uploaded_image(oversized, max_upload_bytes=limit)


@pytest.mark.br("BR-PHOTO-06")
def test_t_photo_06_b_rejects_decompression_bomb() -> None:
    """T-PHOTO-06-b: Image dimensions exceeding safe pixel limits raise 422 InvalidImageError."""
    # A tiny compressed file with massive dimensions
    img_bytes = make_image_bytes(fmt="JPEG", size=(200, 200))
    with pytest.raises(InvalidImageError, match="limits"):
        # Set max_pixels artificially low to trigger decompression guard
        process_uploaded_image(img_bytes, max_pixels=100)


# ==============================================================================
# 3. EXIF Stripping & Downscaling (BR-PHOTO-07, T-PHOTO-07)
# ==============================================================================


@pytest.mark.critical
@pytest.mark.br("BR-PHOTO-07")
def test_t_photo_07_a_strips_all_exif_metadata() -> None:
    """T-PHOTO-07-a (★): EXIF tags (camera, software, GPS) are completely stripped from output."""
    # 0x010F: Make, 0x0110: Model, 0x0131: Software
    exif_tags = {
        0x010F: "SecretCameraBrand",
        0x0110: "SecretCameraModel",
        0x0131: "SecretEditingSoftware",
    }
    raw_with_exif = make_image_bytes(fmt="JPEG", size=(150, 150), exif_data=exif_tags)

    # Verify that raw input has EXIF metadata
    inp_img = Image.open(io.BytesIO(raw_with_exif))
    assert len(inp_img.getexif()) > 0
    assert inp_img.getexif().get(0x010F) == "SecretCameraBrand"

    # Process through pipeline
    processed = process_uploaded_image(raw_with_exif)

    # Inspect processed output
    out_img = Image.open(io.BytesIO(processed.content))
    out_exif = out_img.getexif()
    assert len(out_exif) == 0, f"Expected 0 EXIF tags in processed image, found: {dict(out_exif)}"


@pytest.mark.br("BR-PHOTO-07")
def test_t_photo_07_b_downscales_large_image_preserving_aspect_ratio() -> None:
    """T-PHOTO-07-b: Images with longest side > 1600px are downscaled to 1600px maintaining ratio."""
    # 2400 x 1200 -> ratio 2:1 -> downscaled to 1600 x 800
    large_img = make_image_bytes(fmt="JPEG", size=(2400, 1200))
    processed = process_uploaded_image(large_img, max_side_px=1600)

    assert processed.width == 1600
    assert processed.height == 800

    out_img = Image.open(io.BytesIO(processed.content))
    assert out_img.size == (1600, 800)


@pytest.mark.br("BR-PHOTO-07")
def test_small_image_is_not_upscaled() -> None:
    """Images with dimensions within limit are preserved without upscaling."""
    small_img = make_image_bytes(fmt="PNG", size=(500, 300))
    processed = process_uploaded_image(small_img, max_side_px=1600)

    assert processed.width == 500
    assert processed.height == 300


# ==============================================================================
# 4. Stream Safety Reader & LocalStorage Provider
# ==============================================================================


def test_read_upload_file_safely_chunked() -> None:
    """Stream reader accurately reads bytes and raises 413 when exceeding budget."""
    payload = b"A" * 1024
    stream = io.BytesIO(payload)

    # Reads correctly when within budget
    result = read_upload_file_safely(stream, max_bytes=2048)
    assert result == payload

    # Raises when exceeding budget
    stream_large = io.BytesIO(payload)
    with pytest.raises(FileTooLargeError):
        read_upload_file_safely(stream_large, max_bytes=512)


def test_local_storage_lifecycle(tmp_path: Path) -> None:
    """LocalStorage properly writes, reads, checks, generates URLs, and deletes files."""
    storage = LocalStorage(root_path=tmp_path, public_base_url="/media")

    key = "admin/cover.jpg"
    data = b"image-payload-bytes"

    # Save
    saved_key = storage.save(key, data, content_type="image/jpeg")
    assert saved_key == key
    assert storage.exists(key) is True
    assert storage.get(key) == data
    assert storage.get_url(key) == "/media/admin/cover.jpg"

    # Delete
    deleted = storage.delete(key)
    assert deleted is True
    assert storage.exists(key) is False
    assert storage.get(key) is None

    # Deleting non-existent returns False
    assert storage.delete("nonexistent.jpg") is False


def test_local_storage_path_traversal_prevention(tmp_path: Path) -> None:
    """LocalStorage strictly prevents directory traversal attacks via path escaping."""
    storage = LocalStorage(root_path=tmp_path, public_base_url="/media")

    with pytest.raises(ValueError, match="Path traversal"):
        storage.save("../../etc/passwd", b"malicious")

    with pytest.raises(ValueError, match="Path traversal"):
        storage.get("../../../secret.key")
