"""Architectural Test T-STRUCT-03: Error Code and Domain Exception Parity.

Per docs/07-test-cases.md §7:
"T-STRUCT-03: تطابق الأخطاء: كل رمز في 04 (3.2) له استثناء نطاقي، وكل استثناء له رمز | عدم تطابق"

Verifies:
1. Every error code in docs/04-api.md §3.2 has a distinct AppError subclass in app.core.errors.
2. Every AppError subclass in app.core.errors maps to an approved code in docs/04-api.md §3.2.
3. Every exception's HTTP status matches the exact HTTP status documented in docs/04-api.md §3.2.
"""

import re
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorCode


def parse_doc_error_catalog() -> dict[str, int]:
    """Parse docs/04-api.md Section 3.2 to extract authoritative error codes and HTTP statuses."""
    doc_path = Path(__file__).resolve().parents[2] / "docs" / "04-api.md"
    assert doc_path.exists(), f"API specification not found at {doc_path}"

    content = doc_path.read_text(encoding="utf-8")

    # Locate Section 3.2 table
    start_marker = "### 3.2 فهرس الرموز"
    end_marker = "### 3.3"
    start_pos = content.find(start_marker)
    assert start_pos != -1, f"Section '{start_marker}' not found in docs/04-api.md"

    end_pos = content.find(end_marker, start_pos)
    section_text = content[start_pos:end_pos] if end_pos != -1 else content[start_pos:]

    # Parse markdown table lines: | `code` | HTTP | Description |
    pattern = re.compile(r"\|\s*`([a-z0-9_]+)`\s*\|\s*(\d{3})\s*\|")
    doc_codes: dict[str, int] = {}

    for line in section_text.splitlines():
        match = pattern.search(line)
        if match:
            code = match.group(1)
            http_status = int(match.group(2))
            doc_codes[code] = http_status

    return doc_codes


@pytest.mark.architecture
@pytest.mark.unit
def test_t_struct_03_error_parity_with_spec() -> None:
    """T-STRUCT-03: Verify 1-to-1 parity between docs/04-api.md §3.2 and app.core.errors."""
    doc_codes = parse_doc_error_catalog()

    # 1. Verify ErrorCode enum matches docs/04-api.md exactly
    enum_codes = {str(code.value) for code in ErrorCode}
    doc_code_set = set(doc_codes.keys())

    missing_in_enum = doc_code_set - enum_codes
    extra_in_enum = enum_codes - doc_code_set

    assert not missing_in_enum, (
        f"Codes in docs/04-api.md missing from ErrorCode enum: {missing_in_enum}"
    )
    assert not extra_in_enum, (
        f"Codes in ErrorCode enum not documented in docs/04-api.md: {extra_in_enum}"
    )

    # 2. Collect all concrete AppError subclasses
    subclasses = list(AppError.__subclasses__())
    subclass_code_map = {cls.code.value: cls for cls in subclasses if hasattr(cls, "code")}

    missing_subclasses = doc_code_set - set(subclass_code_map.keys())
    assert not missing_subclasses, (
        f"Documented error codes without a corresponding AppError subclass: {missing_subclasses}"
    )

    # 3. Verify HTTP status parity
    status_mismatches: list[str] = []
    for code, expected_status in doc_codes.items():
        exc_cls = subclass_code_map[code]
        if exc_cls.http_status != expected_status:
            status_mismatches.append(
                f"{code}: doc says {expected_status}, but {exc_cls.__name__}.http_status is {exc_cls.http_status}"
            )

    assert not status_mismatches, (
        f"HTTP status code mismatches between doc and code: {status_mismatches}"
    )


@pytest.mark.architecture
@pytest.mark.unit
def test_t_struct_03_no_orphan_exceptions() -> None:
    """T-STRUCT-03: Verify no AppError subclass exists with an unapproved or orphan code."""
    doc_codes = parse_doc_error_catalog()

    for cls in AppError.__subclasses__():
        assert hasattr(cls, "code"), f"Exception {cls.__name__} does not define a 'code' attribute"
        assert hasattr(cls, "http_status"), (
            f"Exception {cls.__name__} does not define an 'http_status' attribute"
        )
        assert cls.code.value in doc_codes, (
            f"Exception {cls.__name__} has code '{cls.code.value}' which is not documented in docs/04-api.md §3.2"
        )
