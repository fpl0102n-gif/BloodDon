"""Structural Test T-STRUCT-01: Route Permission Coverage.

Per docs/07-test-cases.md §7 (T-STRUCT-01), docs/03-auth-permissions.md §13,
docs/04-api.md §22 & §24, and CLAUDE.md / GEMINI.md Rule 6.3:
- Every registered route must either explicitly declare its required permission
  via require(...) dependency or be present in the authorized public allowlist.
- An unannotated or unprotected route MUST fail the test and break the build.
- Gated documentation endpoints (/docs, /redoc, /openapi.json) must respect
  configuration and be omitted when docs_enabled is False.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, _IncludedRouter

from app.api.deps import get_route_permission
from app.core.config import get_settings
from app.main import create_app
from app.policy import Permission

# ==============================================================================
# Authorized Public Route Allowlist (docs/04 §22 & §24)
# ==============================================================================

AUTHORIZED_PUBLIC_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # 1. Operational & System Probes (docs/04 §21)
        ("GET", "/health"),
        ("GET", "/ready"),
        # 2. OpenAPI & Swagger Documentation (D-25, T-STRUCT-08; when enabled)
        ("GET", "/docs"),
        ("GET", "/redoc"),
        ("GET", "/openapi.json"),
        ("GET", "/docs/oauth2-redirect"),
        # 3. Local Static Assets (D-71)
        ("GET", "/static"),
        # 3. Authentication Handshake & Sessions (/auth/* and /api/v1/auth/* per docs/04 §9)
        ("GET", "/auth/csrf"),
        ("GET", "/api/v1/auth/csrf"),
        ("POST", "/auth/login"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/auth/mfa/verify"),
        ("POST", "/api/v1/auth/mfa/verify"),
        ("POST", "/auth/reauth"),
        ("POST", "/api/v1/auth/reauth"),
        ("POST", "/auth/logout"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/auth/logout-all"),
        ("POST", "/api/v1/auth/logout-all"),
        ("POST", "/auth/password/forgot"),
        ("POST", "/api/v1/auth/password/forgot"),
        ("POST", "/auth/password/reset"),
        ("POST", "/api/v1/auth/password/reset"),
        ("POST", "/auth/invite/accept"),
        ("POST", "/api/v1/auth/invite/accept"),
    }
)


def extract_all_routes(router: Any, prefix: str = "") -> list[tuple[str, set[str], Any]]:
    """Recursively extract all routes with their normalized paths, HTTP methods, and route objects."""
    extracted: list[tuple[str, set[str], Any]] = []

    for route in router.routes:
        if isinstance(route, _IncludedRouter):
            inc_prefix = route.include_context.prefix
            extracted.extend(
                extract_all_routes(
                    route.include_context.included_router,
                    prefix=prefix + inc_prefix,
                )
            )
        elif isinstance(route, APIRoute):
            full_path = prefix + route.path
            extracted.append((full_path, route.methods, route))
        else:
            # Starlette Route (e.g. openapi/docs)
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", {"GET"})
            extracted.append((prefix + path, set(methods), route))

    return extracted


def validate_application_route_coverage(app: FastAPI) -> list[str]:
    """Inspect all routes in the given FastAPI app and return a list of violations."""
    routes = extract_all_routes(app.router)
    violations: list[str] = []

    for path, methods, route_obj in routes:
        # Normalize methods (filter out auto-generated HEAD/OPTIONS for check)
        http_methods = {m for m in methods if m not in {"HEAD", "OPTIONS"}}
        if not http_methods:
            http_methods = {"GET"}

        perm, _reauth = get_route_permission(route_obj)

        for method in http_methods:
            is_allowlisted = (method, path) in AUTHORIZED_PUBLIC_ALLOWLIST
            has_permission = perm is not None

            if not is_allowlisted and not has_permission:
                violations.append(
                    f"UNPROTECTED ROUTE: {method} {path} - declares neither require(...) "
                    f"nor exists in AUTHORIZED_PUBLIC_ALLOWLIST."
                )

    return violations


# ==============================================================================
# T-STRUCT-01 Test Cases
# ==============================================================================


@pytest.mark.critical
def test_t_struct_01_all_registered_routes_declare_permission_or_are_allowlisted() -> None:
    """T-STRUCT-01: Every route in create_app() must declare permission or be in public allowlist."""
    app = create_app()
    violations = validate_application_route_coverage(app)

    assert not violations, (
        f"Build broken: found {len(violations)} unprotected route(s):\n" + "\n".join(violations)
    )


@pytest.mark.critical
def test_t_struct_01_unprotected_route_breaks_the_build() -> None:
    """T-STRUCT-01: Adding an unannotated, unallowlisted route MUST cause the check to fail."""
    test_app = FastAPI()

    # Define a rogue unprotected endpoint without require(...) and not in allowlist
    @test_app.get("/api/v1/rogue/unprotected-endpoint")
    def rogue_endpoint() -> dict[str, str]:
        return {"data": "unprotected"}

    violations = validate_application_route_coverage(test_app)
    assert len(violations) == 1
    assert "UNPROTECTED ROUTE: GET /api/v1/rogue/unprotected-endpoint" in violations[0]


def test_t_struct_01_declared_permissions_are_valid_enum_members() -> None:
    """T-STRUCT-01: All route permissions must be valid members of Permission enum."""
    app = create_app()
    routes = extract_all_routes(app.router)

    for path, _methods, route_obj in routes:
        perm, _reauth = get_route_permission(route_obj)
        if perm is not None:
            assert isinstance(perm, Permission), (
                f"Route {path} permission {perm!r} is not a valid Permission enum instance."
            )
            assert perm in Permission, (
                f"Route {path} declared permission {perm} not registered in Permission enum."
            )


def test_t_struct_01_sensitive_step_up_operations_have_reauth_flag() -> None:
    """T-STRUCT-01: Sensitive account operations like /me/mfa/disable must declare require_reauth=True."""
    app = create_app()
    routes = extract_all_routes(app.router)

    disable_routes = [r for path, _methods, r in routes if path.endswith("/me/mfa/disable")]
    assert len(disable_routes) > 0, "Expected /me/mfa/disable route to be registered."

    for route_obj in disable_routes:
        perm, require_reauth = get_route_permission(route_obj)
        assert perm == Permission.ME_SETUP_MFA
        assert require_reauth is True, (
            f"Route {route_obj.path} must declare require_reauth=True for step-up enforcement."
        )


def test_t_struct_01_docs_disabled_in_production() -> None:
    """T-STRUCT-01 & T-STRUCT-08: In production mode (docs_enabled=False), doc routes are omitted."""
    base_settings = get_settings()
    prod_settings = base_settings.model_copy(update={"docs_enabled": False})
    prod_app = create_app(prod_settings)

    routes = extract_all_routes(prod_app.router)
    doc_paths = {
        path for path, _methods, _r in routes if path in {"/docs", "/redoc", "/openapi.json"}
    }
    assert not doc_paths, (
        f"Docs endpoints must not be registered when docs_enabled=False: {doc_paths}"
    )
