"""Contract and structural tests for the OpenAPI schema snapshot (P4.8).

Per:
- docs/07-test-cases.md §7 (T-STRUCT-07: OpenAPI snapshot matching)
- docs/07-test-cases.md §2 (T-GEN-09-a: No secret fields in schemas)
- docs/04-api.md §24 (Contract test requirements)
- docs/12-implementation-plan.md §7 (P4.8 task completion criteria)
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.config import get_settings
from app.main import create_app

# Forbidden sensitive property names that MUST NOT appear anywhere in OpenAPI schemas (T-GEN-09)
FORBIDDEN_SCHEMA_FIELDS: frozenset[str] = frozenset(
    {
        "password_hash",
        "totp_secret",
        "totp_secret_enc",
        "token_hash",
        "ip_hash",
        "code_hash",
        "raw_token",
    }
)


def _collect_all_keys(data: Any) -> set[str]:
    """Recursively collect all property and dictionary keys in a JSON-like structure."""
    keys: set[str] = set()
    if isinstance(data, dict):
        for k, v in data.items():
            keys.add(k)
            keys.update(_collect_all_keys(v))
    elif isinstance(data, list):
        for item in data:
            keys.update(_collect_all_keys(item))
    return keys


# ==============================================================================
# T-STRUCT-07 & T-GEN-09 Test Cases
# ==============================================================================


@pytest.mark.critical
def test_t_struct_07_openapi_schema_matches_snapshot() -> None:
    """T-STRUCT-07: Active OpenAPI specification must exactly match the committed snapshot.

    If an intentional API change was made, re-export the snapshot via:
        uv run python -m scripts.export_openapi
    """
    snapshot_path = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    assert snapshot_path.is_file(), (
        f"Missing OpenAPI snapshot file at {snapshot_path}. Run scripts.export_openapi to generate it."
    )

    snapshot_data = json.loads(snapshot_path.read_text(encoding="utf-8"))

    base_settings = get_settings()
    schema_settings = base_settings.model_copy(update={"docs_enabled": True})
    app = create_app(schema_settings)
    current_schema = app.openapi()

    # Compare title, version, and OpenAPI version
    assert current_schema["openapi"] == snapshot_data["openapi"]
    assert current_schema["info"] == snapshot_data["info"]

    # Compare paths (endpoints)
    current_paths = set(current_schema.get("paths", {}).keys())
    snapshot_paths = set(snapshot_data.get("paths", {}).keys())

    missing_in_snapshot = current_paths - snapshot_paths
    removed_from_snapshot = snapshot_paths - current_paths

    assert not missing_in_snapshot, (
        f"New endpoints missing in docs/openapi.json snapshot: {missing_in_snapshot}. "
        "Run `uv run python -m scripts.export_openapi` to update the snapshot."
    )
    assert not removed_from_snapshot, (
        f"Endpoints removed compared to docs/openapi.json snapshot: {removed_from_snapshot}. "
        "Run `uv run python -m scripts.export_openapi` to update the snapshot."
    )

    # Full structural equality check
    assert current_schema == snapshot_data, (
        "OpenAPI schema drift detected between active code and docs/openapi.json. "
        "Run `uv run python -m scripts.export_openapi` to update the snapshot."
    )


@pytest.mark.critical
def test_t_gen_09_a_no_secret_fields_in_openapi_schemas() -> None:
    """T-GEN-09-a (★): OpenAPI components schemas must never expose password_hash, secrets, or internal hashes."""
    base_settings = get_settings()
    schema_settings = base_settings.model_copy(update={"docs_enabled": True})
    app = create_app(schema_settings)
    schema = app.openapi()

    components_schemas = schema.get("components", {}).get("schemas", {})
    assert components_schemas, "OpenAPI schema must contain components/schemas definitions."

    # Inspect all property names defined in all schemas
    leaked_fields: list[str] = []
    for schema_name, schema_def in components_schemas.items():
        properties = schema_def.get("properties", {})
        for prop_name in properties:
            if prop_name in FORBIDDEN_SCHEMA_FIELDS:
                leaked_fields.append(f"{schema_name}.{prop_name}")

    assert not leaked_fields, f"Forbidden secret fields exposed in OpenAPI schemas: {leaked_fields}"


def test_openapi_contains_all_phase4_admin_endpoints() -> None:
    """Verify that all Phase 4 administrative endpoints are documented in OpenAPI."""
    snapshot_path = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    snapshot_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    paths = snapshot_data.get("paths", {})

    expected_admin_prefixes = [
        "/admin/sites",
        "/admin/campaigns",
        "/admin/users",
        "/admin/audit",
        "/admin/uploads",
        "/public/sites",
        "/public/campaigns",
    ]

    for prefix in expected_admin_prefixes:
        matching = [p for p in paths if p.startswith(prefix) or p.startswith(f"/api/v1{prefix}")]
        assert matching, (
            f"Expected endpoint path matching prefix '{prefix}' in OpenAPI specification."
        )
