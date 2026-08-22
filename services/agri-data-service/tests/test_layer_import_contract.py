"""AST import contract test enforcing strict downward dependency lattice across all 6 layers.

See track spec in conductor/tracks/agri_sdk_layering_20260805/spec.md.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

# Dependency rules: for each layer, set of forbidden package prefixes / modules
LAYER_FORBIDDEN_IMPORTS: dict[str, set[str]] = {
    "foundation": {
        "agri_data_service",
        "sqlalchemy",
        "httpx",
        "asyncpg",
        "click",
    },
    "method": {
        "agri_data_service.warehouse",
        "agri_data_service.pipeline",
        "agri_data_service.planes",
        "agri_data_service.interface",
        "sqlalchemy",
        "httpx",
    },
    "warehouse": {
        "agri_data_service.method",
        "agri_data_service.pipeline",
        "agri_data_service.planes",
        "agri_data_service.interface",
    },
    "pipeline": {
        "agri_data_service.method",
        "agri_data_service.planes",
        "agri_data_service.interface",
    },
    "planes": {
        "agri_data_service.interface",
    },
    "interface": set(),
}

# Sub-package rules INSIDE a single layer. The lattice above is keyed by layer directory, so it
# cannot separate two packages that share a layer -- `method.monte_carlo` and `method.ml` are both
# under `method` and the lattice happily lets them import each other.
#
# They must not. Monte Carlo forecasting is per-lane statistical projection that ships with the data
# rebuild; `ml` is frozen and expected to leave for a separate Mojo service. Coupling them would tie
# the rebuild to that runtime migration. See conductor/code_styleguides/layer-lanes.md section 5 and
# conductor/RUNBOOK.md section 0.24.8, which records this rule as a wave-2 prerequisite.
SUBPACKAGE_FORBIDDEN_IMPORTS: dict[str, set[str]] = {
    "method/monte_carlo": {"agri_data_service.method.ml"},
    "method/ml": {"agri_data_service.method.monte_carlo"},
}


def _get_imports(py_path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))  # noqa: PERF401
        elif isinstance(node, ast.ImportFrom):  # noqa: SIM102
            if node.module:
                imports.append((node.lineno, node.module))
    return imports


def test_layer_import_contract() -> None:
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    violations: list[str] = []

    for layer, forbidden in LAYER_FORBIDDEN_IMPORTS.items():
        layer_dir = pkg_root / layer
        if not layer_dir.is_dir():
            continue
        for py_file in layer_dir.glob("**/*.py"):
            for line_no, imp in _get_imports(py_file):
                for forb in forbidden:
                    if imp == forb or imp.startswith(forb + "."):
                        # Special check for foundation: self-import is ok
                        if layer == "foundation" and forb == "agri_data_service":  # noqa: SIM102
                            if imp.startswith("agri_data_service.foundation"):
                                continue
                        violations.append(f"{py_file.name}:{line_no} imports '{imp}' (forbidden by layer '{layer}')")

    assert not violations, "Layer import contract violations found:\n" + "\n".join(violations)


def _module_name(py_path: Path, pkg_root: Path) -> str:
    """Map a file under ``pkg_root`` to the dotted module name Python would import it as."""
    parts = py_path.relative_to(pkg_root.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def test_layer_packages_actually_import() -> None:
    """The AST walk above only *parses* files; it never proves a layer package can be imported.

    A syntactically clean but structurally broken module (missing symbol, missing module, a
    typo'd re-export) parses fine and would sail through `test_layer_import_contract` while
    failing every real caller and `mypy`. Import every file under each of the six layers for real
    and fail loudly, naming every module that could not be imported.
    """
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    failures: list[str] = []

    for layer in LAYER_FORBIDDEN_IMPORTS:
        layer_dir = pkg_root / layer
        if not layer_dir.is_dir():
            continue
        for py_file in layer_dir.glob("**/*.py"):
            module_name = _module_name(py_file, pkg_root)
            try:
                importlib.import_module(module_name)
            except Exception as exc:  # collect every failure, not just the first
                failures.append(f"{module_name}: {type(exc).__name__}: {exc}")

    assert not failures, "Layer modules that failed to import:\n" + "\n".join(failures)


def test_subpackage_import_contract() -> None:
    """Enforce the boundaries the layer lattice cannot express, because both sides share a layer."""
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    violations: list[str] = []

    for subpackage, forbidden in SUBPACKAGE_FORBIDDEN_IMPORTS.items():
        sub_dir = pkg_root / subpackage
        if not sub_dir.is_dir():
            continue
        for py_file in sub_dir.glob("**/*.py"):
            for line_no, imp in _get_imports(py_file):
                for forb in forbidden:
                    if imp == forb or imp.startswith(forb + "."):
                        violations.append(
                            f"{py_file.relative_to(pkg_root)}:{line_no} imports '{imp}' "
                            f"(forbidden inside '{subpackage}')"
                        )

    joined = '\n'.join(violations)
    assert not violations, "Sub-package import contract violations found:" + '\n' + joined
