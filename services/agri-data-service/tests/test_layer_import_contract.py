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

# Domain isolation inside `ingest/` and `execution/`, which the six-layer lattice above does not
# police at all -- neither directory is one of its layers, and never has been. RUNBOOK sections
# 0.25.1 (decisions 1 and 2) and 0.25.2: producers move under `<parent>/<domain>/` while shared
# primitives stay at `<parent>/` root, and no domain package may import a sibling domain package.
#
# Deliberately DEFAULT-DENY: every subpackage of a parent below counts as a domain unless it is
# named in DOMAIN_PARENT_SHARED_SUBPACKAGES. A domain added later is therefore policed the day it
# lands, with nothing to remember to register -- the opposite bias from an allow-list, where the
# forgotten entry is silently unenforced.
DOMAIN_PARENTS: tuple[str, ...] = ("ingest", "execution")

DOMAIN_PARENT_SHARED_SUBPACKAGES: dict[str, set[str]] = {
    # Organised per source over shared internals (`_shared`, `_results`, `_release_sets`) that
    # CAMS, GloFAS and USDM all use; splitting it would export private modules across packages.
    # See execution/weather_observations/AGENTS.md.
    "execution": {"historical_writer"},
    # Cross-source ingest validation models, not one source's producer.
    "ingest": {"validation"},
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


def _resolved_imports(py_path: Path, pkg_root: Path) -> list[tuple[int, str]]:
    """Return imports as absolute `agri_data_service.*` names, resolving relative ones.

    `_get_imports` reports `from .sibling import x` as bare `sibling`, which no absolute-prefix
    check can ever match -- a relative import would slip past domain isolation silently.
    """
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    package_parts = ("agri_data_service", *py_path.relative_to(pkg_root).parts[:-1])
    resolved: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            resolved.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                if node.module:
                    resolved.append((node.lineno, node.module))
                continue
            base = package_parts[: len(package_parts) - node.level + 1]
            resolved.append((node.lineno, ".".join((*base, node.module) if node.module else base)))
    return resolved


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
                violations.extend(
                    f"{py_file.relative_to(pkg_root)}:{line_no} imports '{imp}' "
                    f"(forbidden inside '{subpackage}')"
                    for forb in forbidden
                    if imp == forb or imp.startswith(forb + ".")
                )

    joined = '\n'.join(violations)
    assert not violations, "Sub-package import contract violations found:" + '\n' + joined


def _domain_packages(pkg_root: Path, parent: str) -> list[str]:
    """Return every subpackage of `parent` that counts as a domain, newest-safe by default-deny."""
    parent_dir = pkg_root / parent
    if not parent_dir.is_dir():
        return []
    shared = DOMAIN_PARENT_SHARED_SUBPACKAGES.get(parent, set())
    return sorted(
        child.name
        for child in parent_dir.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file() and child.name not in shared
    )


def _domain_isolation_violations(pkg_root: Path, parent: str) -> list[str]:
    """Return every import of one domain package by a sibling domain package under `parent`."""
    domains = _domain_packages(pkg_root, parent)
    violations: list[str] = []
    for domain in domains:
        siblings = {f"agri_data_service.{parent}.{other}" for other in domains if other != domain}
        for py_file in (pkg_root / parent / domain).glob("**/*.py"):
            for line_no, imp in _resolved_imports(py_file, pkg_root):
                violations.extend(
                    f"{py_file.relative_to(pkg_root)}:{line_no} imports '{imp}' "
                    f"-- a domain package may not import sibling domain '{sibling}'"
                    for sibling in siblings
                    if imp == sibling or imp.startswith(sibling + ".")
                )
    return violations


def test_domain_packages_do_not_import_each_other() -> None:
    """No `ingest.<domain>` or `execution.<domain>` may import a sibling domain (RUNBOOK §0.25.2).

    This is the cross-lane coupling the whole wave-2 boundary exists to prevent, and until this
    test existed it was convention only: the six-layer lattice does not cover `ingest/` or
    `execution/` at all.
    """
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    violations = [v for parent in DOMAIN_PARENTS for v in _domain_isolation_violations(pkg_root, parent)]

    assert not violations, "Domain isolation violations found:\n" + "\n".join(violations)


def test_domain_isolation_catches_an_absolute_and_a_relative_sibling_import(tmp_path: Path) -> None:
    """Prove the rule fires. With one real domain the check above can only ever pass vacuously."""
    parent_dir = tmp_path / "execution"
    for domain in ("weather_observations", "fire_detections"):
        (parent_dir / domain).mkdir(parents=True)
        (parent_dir / domain / "__init__.py").write_text("", encoding="utf-8")
    (parent_dir / "weather_observations" / "nasa_power.py").write_text(
        "from agri_data_service.execution.fire_detections.firms import fetch\n", encoding="utf-8"
    )
    (parent_dir / "fire_detections" / "firms.py").write_text(
        "from ..weather_observations import nasa_power\n", encoding="utf-8"
    )

    violations = _domain_isolation_violations(tmp_path, "execution")

    expected_violation_count = 2  # one absolute import, one relative import
    assert len(violations) == expected_violation_count, violations
    assert any("nasa_power.py:1" in v and "fire_detections.firms" in v for v in violations)
    assert any("firms.py:1" in v and "weather_observations" in v for v in violations)


def test_domain_isolation_actually_has_domains_to_police() -> None:
    """A default-deny rule that silently matches nothing is indistinguishable from no rule at all."""
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    discovered = {parent: _domain_packages(pkg_root, parent) for parent in DOMAIN_PARENTS}

    assert "weather_observations" in discovered["execution"], (
        f"the first domain package is missing; discovered {discovered}"
    )
