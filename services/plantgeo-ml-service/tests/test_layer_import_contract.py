"""AST import contract enforcing the six-layer lattice.

The lattice is `foundation -> method -> warehouse -> pipeline -> planes -> interface`.
Ported from agri-data-service's contract so one vocabulary covers both services. The ML service adds
two rules of its own: `method` may not touch a storage client (spec FR-2), and `method/kernels` may
import `foundation` only, because a kernel has to be callable across the Mojo boundary.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Final

PACKAGE_ROOT: Final = Path(__file__).resolve().parents[1] / "src" / "plantgeo_ml_service"

#: For each layer directory, the import prefixes nothing inside it may name.
LAYER_FORBIDDEN_IMPORTS: Final[dict[str, frozenset[str]]] = {
    "foundation": frozenset(
        {
            "plantgeo_ml_service",
            "sqlalchemy",
            "httpx",
            "asyncpg",
            "click",
            "numpy",
            "polars",
            "pyarrow",
            "duckdb",
            "boto3",
            "sanic",
        }
    ),
    # A storage or web dependency in `method` is what would make a pure estimator untestable without
    # a bucket, and is the coupling the Mojo port has to be free of. Spec FR-2.
    "method": frozenset(
        {
            "plantgeo_ml_service.warehouse",
            "plantgeo_ml_service.pipeline",
            "plantgeo_ml_service.planes",
            "plantgeo_ml_service.interface",
            "plantgeo_ml_service.app",
            "plantgeo_ml_service.config",
            "polars",
            "pyarrow",
            "duckdb",
            "boto3",
            "sanic",
            "sqlalchemy",
            "httpx",
            "asyncpg",
        }
    ),
    "warehouse": frozenset(
        {
            "plantgeo_ml_service.pipeline",
            "plantgeo_ml_service.planes",
            "plantgeo_ml_service.interface",
        }
    ),
    "pipeline": frozenset(
        {
            "plantgeo_ml_service.planes",
            "plantgeo_ml_service.interface",
        }
    ),
    "planes": frozenset({"plantgeo_ml_service.interface"}),
    "interface": frozenset(),
}

#: The package ROOT is a pseudo-layer: `app.py`, `config.py` and `__init__.py` sit in no layer
#: directory, so the lattice above never reached them and the one file that decides whether this
#: service can talk to Postgres was ungoverned. Decision D5 is zero-Postgres, and `alembic`/`httpx`
#: are here for the same reason: the root is where an inherited sibling dependency would land first.
ROOT_FORBIDDEN_IMPORTS: Final[frozenset[str]] = frozenset(
    {
        "sqlalchemy",
        "asyncpg",
        "psycopg",
        "psycopg2",
        "alembic",
        "httpx",
    }
)

#: Boundaries INSIDE one layer, which a directory-keyed lattice cannot express because both sides
#: share a layer directory. `method/ml` and `method/monte_carlo` are siblings that never import each
#: other; `method/kernels` is stricter than either.
SUBPACKAGE_FORBIDDEN_IMPORTS: Final[dict[str, frozenset[str]]] = {
    # `method/kernels` is BELOW both siblings, not beside them: it is the one place the Mojo
    # dispatch lives, and both estimator families call it (phase 3, spec FR-9). The rule that
    # matters is the reverse edge, which `method/kernels` below still forbids, so the lattice
    # stays a lattice and `kernels` can never reach back into a lane.
    "method/monte_carlo": frozenset({"plantgeo_ml_service.method.ml"}),
    "method/ml": frozenset({"plantgeo_ml_service.method.monte_carlo"}),
    "method/kernels": frozenset(
        {
            "plantgeo_ml_service.method.ml",
            "plantgeo_ml_service.method.monte_carlo",
            "plantgeo_ml_service.warehouse",
            "plantgeo_ml_service.pipeline",
            "plantgeo_ml_service.planes",
            "plantgeo_ml_service.interface",
        }
    ),
}

#: `layer-lanes.md` section 1: a lane never imports another lane. Shared needs move DOWN the lattice.
SIBLING_MODULE_DIRECTORIES: Final[tuple[str, ...]] = ("method/monte_carlo",)


def _resolved_imports(python_path: Path, package_root: Path) -> list[tuple[int, str]]:
    """Return imports as absolute `plantgeo_ml_service.*` names, resolving relative ones.

    Each `from X import a, b` also yields `X.a` and `X.b`, because `from . import sibling` resolves
    to the PACKAGE and the thing actually imported is a name in `node.names`. That over-reports for
    plain symbol imports, which is harmless: every caller matches known module prefixes only.
    """
    tree = ast.parse(python_path.read_text(encoding="utf-8"), filename=str(python_path))
    package_parts = ("plantgeo_ml_service", *python_path.relative_to(package_root).parts[:-1])
    resolved: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            resolved.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                module = ".".join((*base, node.module) if node.module else base)
            elif node.module:
                module = node.module
            else:
                continue
            resolved.append((node.lineno, module))
            resolved.extend((node.lineno, f"{module}.{alias.name}") for alias in node.names)
    return resolved


def _names_a_forbidden_module(imported: str, forbidden: str) -> bool:
    """Return whether one import names a forbidden module or something inside it."""
    return imported == forbidden or imported.startswith(forbidden + ".")


def _layer_violations(layer: str, forbidden: frozenset[str]) -> list[str]:
    """Return every forbidden import made by one layer directory."""
    layer_directory = PACKAGE_ROOT / layer
    if not layer_directory.is_dir():
        return []
    violations: list[str] = []
    for python_path in sorted(layer_directory.glob("**/*.py")):
        relative = python_path.relative_to(PACKAGE_ROOT).as_posix()
        for line_number, imported in _resolved_imports(python_path, PACKAGE_ROOT):
            # `foundation` forbids the whole package but must still reach its own siblings.
            if layer == "foundation" and imported.startswith("plantgeo_ml_service.foundation"):
                continue
            violations.extend(
                f"{relative}:{line_number} imports '{imported}' (forbidden by layer '{layer}')"
                for entry in sorted(forbidden)
                if _names_a_forbidden_module(imported, entry)
            )
    return violations


def _root_violations(package_root: Path, forbidden: frozenset[str]) -> list[str]:
    """Return every forbidden import made by a module sitting directly at the package root."""
    violations: list[str] = []
    for python_path in sorted(package_root.glob("*.py")):
        relative = python_path.relative_to(package_root).as_posix()
        for line_number, imported in _resolved_imports(python_path, package_root):
            violations.extend(
                f"{relative}:{line_number} imports '{imported}' (forbidden at the package root)"
                for entry in sorted(forbidden)
                if _names_a_forbidden_module(imported, entry)
            )
    return violations


def test_the_package_root_is_governed_too() -> None:
    """`app.py` and `config.py` are the zero-Postgres decision's last line; nothing else sees them."""
    root_modules = {path.name for path in PACKAGE_ROOT.glob("*.py")}
    assert {"app.py", "config.py", "__init__.py"} <= root_modules, root_modules

    violations = _root_violations(PACKAGE_ROOT, ROOT_FORBIDDEN_IMPORTS)

    assert not violations, "Package-root import contract violations found:\n" + "\n".join(violations)


def test_the_root_rule_actually_fires(tmp_path: Path) -> None:
    """Three clean root modules prove nothing on their own, so drive the walker over a dirty one."""
    (tmp_path / "config.py").write_text("import sqlalchemy\nimport psycopg2\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import structlog\n", encoding="utf-8")

    violations = _root_violations(tmp_path, ROOT_FORBIDDEN_IMPORTS)

    expected_violation_count = 2  # `sqlalchemy` and `psycopg2`; `structlog` is allowed
    assert len(violations) == expected_violation_count, violations


def test_layer_import_contract() -> None:
    violations = [
        violation
        for layer, forbidden in LAYER_FORBIDDEN_IMPORTS.items()
        for violation in _layer_violations(layer, forbidden)
    ]

    assert not violations, "Layer import contract violations found:\n" + "\n".join(violations)


def test_subpackage_import_contract() -> None:
    """Enforce the boundaries the layer lattice cannot express, because both sides share a layer."""
    violations: list[str] = []
    for subpackage, forbidden in SUBPACKAGE_FORBIDDEN_IMPORTS.items():
        subpackage_directory = PACKAGE_ROOT / subpackage
        if not subpackage_directory.is_dir():
            continue
        for python_path in sorted(subpackage_directory.glob("**/*.py")):
            relative = python_path.relative_to(PACKAGE_ROOT).as_posix()
            violations.extend(
                f"{relative}:{line_number} imports '{imported}' (forbidden inside '{subpackage}')"
                for line_number, imported in _resolved_imports(python_path, PACKAGE_ROOT)
                for entry in sorted(forbidden)
                if _names_a_forbidden_module(imported, entry)
            )

    assert not violations, "Sub-package import contract violations found:\n" + "\n".join(violations)


def _lane_names(lane_directory: Path) -> set[str]:
    """Name every lane in one directory: each flat module, and each subpackage as a single lane."""
    modules = {path.stem for path in lane_directory.glob("*.py") if path.stem != "__init__"}
    packages = {path.name for path in lane_directory.iterdir() if path.is_dir() and (path / "__init__.py").is_file()}
    return modules | packages


def _lane_of(path: Path, lane_directory: Path) -> str:
    """Return which lane one file belongs to: its own stem, or the subpackage that contains it."""
    relative = path.relative_to(lane_directory)
    return relative.parts[0] if len(relative.parts) > 1 else path.stem


def _sibling_module_violations(package_root: Path, directory: str) -> list[str]:
    """Return every import of one lane by a sibling lane in the same directory."""
    lane_directory = package_root / directory
    if not lane_directory.is_dir():
        return []
    package = f"plantgeo_ml_service.{directory.replace('/', '.')}"
    lanes = _lane_names(lane_directory)
    # Keyed by (file, line): `_resolved_imports` reports one `from X import a` twice on purpose, and
    # a reader wants the offending LINE named once, not once per matching form.
    violations: dict[tuple[str, int], str] = {}
    for path in sorted(lane_directory.rglob("*.py")):
        if path.name == "__init__.py" and path.parent == lane_directory:
            continue
        own = _lane_of(path, lane_directory)
        siblings = {f"{package}.{other}" for other in lanes if other != own}
        for line_number, imported in _resolved_imports(path, package_root):
            for sibling in sorted(siblings):
                if _names_a_forbidden_module(imported, sibling):
                    violations.setdefault(
                        (path.relative_to(package_root).as_posix(), line_number),
                        f"{path.relative_to(package_root).as_posix()}:{line_number} imports '{imported}' "
                        f"-- a lane may not import sibling lane '{sibling}'",
                    )
    return [violations[key] for key in sorted(violations)]


def test_lanes_do_not_import_each_other() -> None:
    """A cross-lane import re-couples streams the lattice exists to separate, and it cannot see it."""
    violations = [
        violation
        for directory in SIBLING_MODULE_DIRECTORIES
        for violation in _sibling_module_violations(PACKAGE_ROOT, directory)
    ]

    assert not violations, "Cross-lane import violations found:\n" + "\n".join(violations)


def test_the_cross_lane_rule_actually_fires(tmp_path: Path) -> None:
    """Five real forecasters currently cross nothing, so without this the rule proves nothing."""
    lane_directory = tmp_path / "method" / "monte_carlo"
    lane_directory.mkdir(parents=True)
    (lane_directory / "__init__.py").write_text("", encoding="utf-8")
    (lane_directory / "signal.py").write_text(
        "from plantgeo_ml_service.method.monte_carlo.sensors import simulate\n", encoding="utf-8"
    )
    (lane_directory / "sensors.py").write_text("from . import signal\n", encoding="utf-8")

    violations = _sibling_module_violations(tmp_path, "method/monte_carlo")

    expected_violation_count = 2  # one absolute import, one relative import
    assert len(violations) == expected_violation_count, violations


def _module_name(python_path: Path, package_root: Path) -> str:
    """Map a file under `package_root` to the dotted module name Python would import it as."""
    parts = python_path.relative_to(package_root.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def test_layer_packages_actually_import() -> None:
    """The AST walk only PARSES files; it never proves a layer package can be imported.

    A syntactically clean but structurally broken module -- a missing symbol, a typo'd re-export --
    parses fine and would sail through the contract above while failing every real caller and mypy.
    """
    failures: list[str] = []
    for layer in LAYER_FORBIDDEN_IMPORTS:
        layer_directory = PACKAGE_ROOT / layer
        if not layer_directory.is_dir():
            continue
        for python_path in sorted(layer_directory.glob("**/*.py")):
            module_name = _module_name(python_path, PACKAGE_ROOT)
            try:
                importlib.import_module(module_name)
            except Exception as error:  # collect every failure, not just the first
                failures.append(f"{module_name}: {type(error).__name__}: {error}")

    assert not failures, "Layer modules that failed to import:\n" + "\n".join(failures)
