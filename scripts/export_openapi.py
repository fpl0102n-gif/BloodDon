"""Export active FastAPI OpenAPI specification to docs/openapi.json.

Per P4.8 and docs/12-implementation-plan.md:
"P4.8: Matrix rows for admin endpoints; export OpenAPI snapshot | tests/api/ | T-STRUCT-02/07 | Snapshot committed"
"""

import json
from pathlib import Path

from app.core.config import get_settings
from app.main import create_app


def export_openapi(output_path: Path | None = None) -> Path:
    """Export formatted OpenAPI schema to target JSON file."""
    target = output_path or Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
    base_settings = get_settings()
    # Force docs_enabled=True so that OpenAPI schema metadata is generated
    schema_settings = base_settings.model_copy(update={"docs_enabled": True})
    app = create_app(schema_settings)
    schema = app.openapi()

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


if __name__ == "__main__":
    out = export_openapi()
    print(f"OpenAPI snapshot exported successfully to {out}")  # noqa: T201
