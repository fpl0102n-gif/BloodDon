"""Architectural test: Verify package skeleton imports and structure.

Traces to docs/05-architecture.md §4 and docs/12-implementation-plan.md P0.12.
"""

import importlib

import pytest

ALL_PACKAGES = [
    "app",
    "app.core",
    "app.models",
    "app.schemas",
    "app.repositories",
    "app.services",
    "app.policy",
    "app.api",
    "app.api.routers",
    "app.api.routers.public",
    "app.api.routers.supervisor",
    "app.api.routers.admin",
    "app.web",
    "app.web.routers",
    "app.storage",
    "app.mail",
    "app.cli",
    "tests",
    "tests.factories",
    "tests.unit",
    "tests.db",
    "tests.api",
    "tests.web",
    "tests.security",
    "tests.architecture",
]


@pytest.mark.architecture
@pytest.mark.unit
@pytest.mark.parametrize("package_name", ALL_PACKAGES)
def test_skeleton_package_imports_resolve(package_name: str) -> None:
    """Verify that all packages and subpackages in the architectural skeleton import cleanly."""
    mod = importlib.import_module(package_name)
    assert mod is not None
    assert hasattr(mod, "__file__")
