"""Architectural Test T-STRUCT-04: Layer Dependency Rules and Isolation.

Per docs/05-architecture.md §5.1, §5.2, docs/07-test-cases.md §7, and docs/12-implementation-plan.md:
"T-STRUCT-04: قواعد الاعتماد: يفحص الاستيرادات بين الطبقات (05 القسم 5.2) | أي خرق"

Enforces:
1. models: Does NOT import from repositories, services, policy, api, web, cli, storage, mail, or fastapi.
2. repositories: Imports models and core ONLY. Does NOT import services, policy, api, web, cli, schemas, or fastapi.
3. services: Does NOT import from api, web, cli, fastapi, or starlette (no HTTP dependencies).
4. api/routers and web/routers: Do NOT import repositories or models directly (must call services).
5. policy: Does NOT import repositories, models, services, api, web, or fastapi (no database access).
6. core: Does NOT import any higher domain layers (models, repositories, services, policy, api, web, cli, schemas).
7. schemas: Does NOT import models, repositories, services, policy, api, web, or cli.
8. Transaction isolation: No direct .commit() or .rollback() calls inside services or repositories.
9. Circular dependency check: No cycles in internal app module dependencies.
10. Acceptance Criterion: Deliberately bad imports make tests fail.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

# ==============================================================================
# Architectural Rules Definition (docs/05-architecture.md §5.2)
# ==============================================================================

PROHIBITED_IMPORTS_BY_LAYER: dict[str, tuple[str, ...]] = {
    "models": (
        "app.repositories",
        "app.services",
        "app.policy",
        "app.api",
        "app.web",
        "app.cli",
        "app.storage",
        "app.mail",
        "fastapi",
    ),
    "repositories": (
        "app.services",
        "app.policy",
        "app.api",
        "app.web",
        "app.cli",
        "app.storage",
        "app.mail",
        "app.schemas",
        "fastapi",
    ),
    "services": (
        "app.api",
        "app.web",
        "app.cli",
        "fastapi",
        "starlette",
    ),
    "api_routers": (
        "app.repositories",
        "app.models",
    ),
    "web_routers": (
        "app.repositories",
        "app.models",
    ),
    "policy": (
        "app.repositories",
        "app.models",
        "app.services",
        "app.api",
        "app.web",
        "fastapi",
    ),
    "core": (
        "app.models",
        "app.repositories",
        "app.services",
        "app.policy",
        "app.api",
        "app.web",
        "app.cli",
        "app.schemas",
    ),
    "schemas": (
        "app.models",
        "app.repositories",
        "app.services",
        "app.policy",
        "app.api",
        "app.web",
        "app.cli",
    ),
}


@dataclass(frozen=True, slots=True)
class ArchitecturalViolation:
    """Represents a single layer violation or forbidden statement."""

    file_path: str
    layer: str
    line_number: int
    rule: str
    detail: str


def resolve_relative_import(
    file_path: Path, app_root: Path, level: int, module_name: str | None
) -> str:
    """Resolve a relative import (level > 0) to its full absolute module name."""
    rel_path = file_path.relative_to(app_root)
    parts = list(rel_path.with_suffix("").parts)
    package_parts = parts[:-1]

    # Level 1: current package; Level 2: parent package; etc.
    if level > len(package_parts):
        base_parts = ["app"]
    else:
        base_parts = ["app", *package_parts[: len(package_parts) - level + 1]]

    if module_name:
        return ".".join([*base_parts, module_name])
    return ".".join(base_parts)


def classify_layer(file_path: Path, app_root: Path) -> str | None:
    """Determine the architectural layer of a Python file under app/."""
    try:
        rel_parts = file_path.relative_to(app_root).parts
    except ValueError:
        return None

    if len(rel_parts) < 2:
        return None  # Top-level app files like app/main.py

    top_dir = rel_parts[0]

    if top_dir == "api" and len(rel_parts) >= 3 and rel_parts[1] == "routers":
        return "api_routers"
    if top_dir == "web" and len(rel_parts) >= 3 and rel_parts[1] == "routers":
        return "web_routers"
    if top_dir in PROHIBITED_IMPORTS_BY_LAYER:
        return top_dir

    return None


def extract_imports_and_violations(
    file_path: Path, app_root: Path, content: str | None = None
) -> list[ArchitecturalViolation]:
    """Parse AST of a Python source file and verify compliance with layer boundaries."""
    layer = classify_layer(file_path, app_root)
    if not layer:
        return []

    prohibited_prefixes = PROHIBITED_IMPORTS_BY_LAYER.get(layer, ())
    violations: list[ArchitecturalViolation] = []

    source_code = content if content is not None else file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source_code, filename=str(file_path))
    except SyntaxError as exc:
        violations.append(
            ArchitecturalViolation(
                file_path=str(file_path),
                layer=layer,
                line_number=exc.lineno or 0,
                rule="valid_python_syntax",
                detail=f"SyntaxError parsing file: {exc}",
            )
        )
        return violations

    for node in ast.walk(tree):
        # 1. Check direct imports: import foo.bar
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in prohibited_prefixes:
                    if alias.name == forbidden or alias.name.startswith(f"{forbidden}."):
                        violations.append(
                            ArchitecturalViolation(
                                file_path=str(file_path),
                                layer=layer,
                                line_number=node.lineno,
                                rule=f"no_{forbidden}_in_{layer}",
                                detail=f"Layer '{layer}' is prohibited from importing '{alias.name}'",
                            )
                        )

        # 2. Check from imports: from foo import bar
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                resolved_mod = resolve_relative_import(file_path, app_root, node.level, node.module)
            else:
                resolved_mod = node.module or ""

            for forbidden in prohibited_prefixes:
                if resolved_mod == forbidden or resolved_mod.startswith(f"{forbidden}."):
                    violations.append(
                        ArchitecturalViolation(
                            file_path=str(file_path),
                            layer=layer,
                            line_number=node.lineno,
                            rule=f"no_{forbidden}_in_{layer}",
                            detail=f"Layer '{layer}' is prohibited from importing from '{resolved_mod}'",
                        )
                    )

        # 3. Check forbidden .commit() / .rollback() calls in services or repositories
        if (
            layer in ("services", "repositories")
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("commit", "rollback")
        ):
            violations.append(
                ArchitecturalViolation(
                    file_path=str(file_path),
                    layer=layer,
                    line_number=node.lineno,
                    rule=f"no_transaction_{node.func.attr}_in_{layer}",
                    detail=(
                        f"Layer '{layer}' is forbidden from invoking transaction.{node.func.attr}(); "
                        "transactions must be managed at the request lifecycle boundary (CLAUDE.md §5)"
                    ),
                )
            )

    return violations


def check_entire_codebase(app_root: Path) -> list[ArchitecturalViolation]:
    """Scan all Python files under app/ and collect any architectural violations."""
    all_violations: list[ArchitecturalViolation] = []
    for py_file in sorted(app_root.rglob("*.py")):
        all_violations.extend(extract_imports_and_violations(py_file, app_root))
    return all_violations


def detect_import_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Detect cycles in an internal module dependency graph using DFS."""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    rec_stack: list[str] = []

    def dfs(node: str) -> None:
        visited.add(node)
        rec_stack.append(node)

        for neighbor in sorted(graph.get(node, set())):
            target = neighbor
            while target not in graph and "." in target:
                target = target.rsplit(".", 1)[0]

            if target in graph:
                if target in rec_stack:
                    cycle_start = rec_stack.index(target)
                    cycles.append([*rec_stack[cycle_start:], target])
                elif target not in visited:
                    dfs(target)

        rec_stack.pop()

    for mod in sorted(graph.keys()):
        if mod not in visited:
            dfs(mod)

    return cycles


def build_app_dependency_graph(app_root: Path) -> dict[str, set[str]]:
    """Build the internal dependency graph of all Python modules under app/."""
    graph: dict[str, set[str]] = {}

    for py_file in app_root.rglob("*.py"):
        rel = py_file.relative_to(app_root).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            mod_name = "app." + ".".join(parts[:-1]) if len(parts) > 1 else "app"
        else:
            mod_name = "app." + ".".join(parts)

        graph[mod_name] = set()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app" or alias.name.startswith("app."):
                        graph[mod_name].add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    resolved = resolve_relative_import(py_file, app_root, node.level, node.module)
                else:
                    resolved = node.module or ""
                if resolved == "app" or resolved.startswith("app."):
                    graph[mod_name].add(resolved)

    return graph


# ==============================================================================
# Architectural Tests (T-STRUCT-04)
# ==============================================================================


@pytest.mark.architecture
@pytest.mark.unit
def test_t_struct_04_enforce_layer_dependency_rules() -> None:
    """T-STRUCT-04: Verify that all files in app/ strictly follow layer dependency rules.

    Direction: web/routers, api/routers -> services -> repositories -> models.
    """
    app_root = Path(__file__).resolve().parents[2] / "app"
    assert app_root.is_dir(), f"Application root directory not found at {app_root}"

    violations = check_entire_codebase(app_root)
    if violations:
        error_lines = [
            f"- {v.file_path}:{v.line_number} [{v.rule}]: {v.detail}" for v in violations
        ]
        pytest.fail(
            f"T-STRUCT-04 architectural violations detected ({len(violations)}):\n"
            + "\n".join(error_lines)
        )


@pytest.mark.architecture
@pytest.mark.unit
def test_t_struct_04_no_circular_dependencies() -> None:
    """T-STRUCT-04: Verify there are no circular dependencies among internal app modules."""
    app_root = Path(__file__).resolve().parents[2] / "app"
    assert app_root.is_dir(), f"Application root directory not found at {app_root}"

    graph = build_app_dependency_graph(app_root)
    cycles = detect_import_cycles(graph)

    if cycles:
        cycle_lines = [" -> ".join(c) for c in cycles]
        pytest.fail(f"Circular dependencies detected ({len(cycles)}):\n" + "\n".join(cycle_lines))


@pytest.mark.architecture
@pytest.mark.unit
def test_t_struct_04_deliberately_bad_imports_fail() -> None:
    """T-STRUCT-04 Acceptance Criterion: Deliberately bad import makes it fail.

    Tests synthetic violations across every layer to guarantee the architectural
    linter actively catches regressions.
    """
    app_root = Path(__file__).resolve().parents[2] / "app"

    # 1. Models importing from Repositories
    bad_model_code = """
from app.repositories.donors import DonorRepository

class BadModel:
    pass
"""
    v_model = extract_imports_and_violations(
        app_root / "models" / "synthetic.py",
        app_root,
        content=bad_model_code,
    )
    assert any("app.repositories" in v.detail for v in v_model), (
        "Failed to catch model importing repository"
    )

    # 2. Repositories importing from Services
    bad_repo_code = """
from app.services.donors import DonorService

def query_donors():
    pass
"""
    v_repo = extract_imports_and_violations(
        app_root / "repositories" / "synthetic.py",
        app_root,
        content=bad_repo_code,
    )
    assert any("app.services" in v.detail for v in v_repo), (
        "Failed to catch repository importing service"
    )

    # 3. Repositories calling .rollback()
    bad_repo_rollback = """
def abort_query(session):
    session.rollback()
"""
    v_repo_rb = extract_imports_and_violations(
        app_root / "repositories" / "synthetic.py",
        app_root,
        content=bad_repo_rollback,
    )
    assert any("rollback" in v.rule for v in v_repo_rb), (
        "Failed to catch repository invoking rollback"
    )

    # 4. Services importing FastAPI / Starlette
    bad_service_code = """
from fastapi import Request

def process_donation(req: Request):
    pass
"""
    v_service = extract_imports_and_violations(
        app_root / "services" / "synthetic.py",
        app_root,
        content=bad_service_code,
    )
    assert any("fastapi" in v.detail for v in v_service), (
        "Failed to catch service importing fastapi"
    )

    # 5. Service invoking direct transaction.commit()
    bad_commit_service = """
def create_donor(session):
    session.commit()
"""
    v_commit = extract_imports_and_violations(
        app_root / "services" / "synthetic.py",
        app_root,
        content=bad_commit_service,
    )
    assert any("commit" in v.rule for v in v_commit), (
        "Failed to catch forbidden session.commit() in service"
    )

    # 6. API Routers importing Models directly
    bad_api_router_code = """
from app.models.campaign import Campaign

def get_campaign():
    pass
"""
    v_api_router = extract_imports_and_violations(
        app_root / "api" / "routers" / "synthetic.py",
        app_root,
        content=bad_api_router_code,
    )
    assert any("app.models" in v.detail for v in v_api_router), (
        "Failed to catch api router importing model directly"
    )

    # 7. Web Routers importing Repositories directly
    bad_web_router_code = """
from app.repositories.sites import SiteRepository

def render_sites():
    pass
"""
    v_web_router = extract_imports_and_violations(
        app_root / "web" / "routers" / "synthetic.py",
        app_root,
        content=bad_web_router_code,
    )
    assert any("app.repositories" in v.detail for v in v_web_router), (
        "Failed to catch web router importing repository directly"
    )

    # 8. Policy importing Repositories
    bad_policy_code = """
from app.repositories.campaigns import CampaignRepository

def check_policy():
    pass
"""
    v_policy = extract_imports_and_violations(
        app_root / "policy" / "synthetic.py",
        app_root,
        content=bad_policy_code,
    )
    assert any("app.repositories" in v.detail for v in v_policy), (
        "Failed to catch policy importing repository"
    )

    # 9. Core importing higher layer (models)
    bad_core_code = """
from app.models.base import Base

def do_core():
    pass
"""
    v_core = extract_imports_and_violations(
        app_root / "core" / "synthetic.py",
        app_root,
        content=bad_core_code,
    )
    assert any("app.models" in v.detail for v in v_core), "Failed to catch core importing model"

    # 10. Schemas importing Repositories
    bad_schema_code = """
from app.repositories.users import UserRepository

class UserSchema:
    pass
"""
    v_schema = extract_imports_and_violations(
        app_root / "schemas" / "synthetic.py",
        app_root,
        content=bad_schema_code,
    )
    assert any("app.repositories" in v.detail for v in v_schema), (
        "Failed to catch schema importing repository"
    )

    # 11. Synthetic circular dependency detection
    cyclic_graph = {
        "app.alpha": {"app.beta"},
        "app.beta": {"app.gamma"},
        "app.gamma": {"app.alpha"},
    }
    cycles = detect_import_cycles(cyclic_graph)
    assert len(cycles) > 0, "Failed to detect synthetic circular dependency cycle"
    assert cycles[0] == ["app.alpha", "app.beta", "app.gamma", "app.alpha"]
