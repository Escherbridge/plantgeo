"""AST import contract test enforcing the dependency lattice across core and interface layers.

See track spec in conductor/tracks/agri_sdk_layering_20260805/spec.md.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

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
    "parquet_ops": {
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

# The same "a lane never imports another lane" rule, for the directories the domain-package walk
# above cannot see: it is keyed on `ingest`/`execution` and looks only for subdirectories, while
# `layer-lanes.md` section 1 puts one FILE per layer in each lattice directory. `pipeline/direct`
# holds both shapes at once -- `fire_detections.py` and `water_gauges.py` are modules, `climate/` is
# a package -- so the walk below treats a subpackage as one lane exactly like a module.
SIBLING_MODULE_DIRECTORIES: tuple[str, ...] = (
    "pipeline/lanes",
    "warehouse/schemas",
    "method/monte_carlo",
    "pipeline/validation",
    "pipeline/direct",
)

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

    Two forms would otherwise slip past an absolute-prefix check:
      * `from .sibling import x` -- `_get_imports` reports it as bare `sibling`.
      * `from . import sibling` -- the module resolves to the PACKAGE, and the thing actually
        imported is a name in `node.names`. Discarding it hid a real cross-lane import.

    So each `from X import a, b` also yields `X.a` and `X.b`. That over-reports for plain symbol
    imports (`X.SomeClass`), which is harmless here: every caller matches against known
    module/package prefixes, and no symbol shares a prefix with a sibling module.
    """
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    package_parts = ("agri_data_service", *py_path.relative_to(pkg_root).parts[:-1])
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
    failing every real caller and `mypy`. Import every file under each registered layer for real
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
                    f"{py_file.relative_to(pkg_root)}:{line_no} imports '{imp}' (forbidden inside '{subpackage}')"
                    for forb in forbidden
                    if imp == forb or imp.startswith(forb + ".")
                )

    joined = "\n".join(violations)
    assert not violations, "Sub-package import contract violations found:" + "\n" + joined


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
    # Keyed by (file, line) for the same reason as `_sibling_module_violations`.
    violations: dict[tuple[str, int], str] = {}
    for domain in domains:
        siblings = {f"agri_data_service.{parent}.{other}" for other in domains if other != domain}
        for py_file in (pkg_root / parent / domain).glob("**/*.py"):
            for line_no, imp in _resolved_imports(py_file, pkg_root):
                for sibling in siblings:
                    if imp == sibling or imp.startswith(sibling + "."):
                        violations.setdefault(
                            (str(py_file.relative_to(pkg_root)), line_no),
                            f"{py_file.relative_to(pkg_root)}:{line_no} imports '{imp}' "
                            f"-- a domain package may not import sibling domain '{sibling}'",
                        )
    return [violations[key] for key in sorted(violations)]


def test_domain_packages_do_not_import_each_other() -> None:
    """No `ingest.<domain>` or `execution.<domain>` may import a sibling domain (RUNBOOK §0.25.2).

    This is the cross-lane coupling the whole wave-2 boundary exists to prevent, and until this
    test existed it was convention only: the six-layer lattice does not cover `ingest/` or
    `execution/` at all.
    """
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    violations = [v for parent in DOMAIN_PARENTS for v in _domain_isolation_violations(pkg_root, parent)]

    assert not violations, "Domain isolation violations found:\n" + "\n".join(violations)


def _lane_names(lane_dir: Path) -> set[str]:
    """Name every lane in one directory: each flat module, and each subpackage as a single lane."""
    modules = {path.stem for path in lane_dir.glob("*.py") if path.stem != "__init__"}
    packages = {path.name for path in lane_dir.iterdir() if path.is_dir() and (path / "__init__.py").is_file()}
    return modules | packages


def _lane_of(path: Path, lane_dir: Path) -> str:
    """Return which lane one file belongs to: its own stem, or the subpackage that contains it."""
    relative = path.relative_to(lane_dir)
    return relative.parts[0] if len(relative.parts) > 1 else path.stem


def _sibling_module_violations(pkg_root: Path, directory: str) -> list[str]:
    """Return every import of one lane by a sibling lane in the same directory."""
    lane_dir = pkg_root / directory
    if not lane_dir.is_dir():
        return []
    package = f"agri_data_service.{directory.replace('/', '.')}"
    lanes = _lane_names(lane_dir)
    # Keyed by (file, line): `_resolved_imports` reports one `from X import a` twice on purpose,
    # and a reader wants the offending LINE named once, not once per matching form.
    violations: dict[tuple[str, int], str] = {}
    for path in sorted(lane_dir.rglob("*.py")):
        if path.name == "__init__.py" and path.parent == lane_dir:
            continue
        own = _lane_of(path, lane_dir)
        siblings = {f"{package}.{other}" for other in lanes if other != own}
        for line_no, imp in _resolved_imports(path, pkg_root):
            for sibling in siblings:
                if imp == sibling or imp.startswith(sibling + "."):
                    violations.setdefault(
                        (str(path.relative_to(pkg_root)), line_no),
                        f"{path.relative_to(pkg_root)}:{line_no} imports '{imp}' "
                        f"-- a lane may not import sibling lane '{sibling}'",
                    )
    return [violations[key] for key in sorted(violations)]


def test_lanes_do_not_import_each_other() -> None:
    """`layer-lanes.md` §1: a lane never imports another lane. Shared needs move DOWN the lattice.

    A cross-lane import is what quietly re-couples streams the wave plan exists to separate, and it
    is invisible to the layer lattice because both sides sit in the same layer directory.
    """
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    violations = [v for d in SIBLING_MODULE_DIRECTORIES for v in _sibling_module_violations(pkg_root, d)]

    assert not violations, "Cross-lane import violations found:\n" + "\n".join(violations)


def test_the_cross_lane_rule_sees_a_subpackage_lane(tmp_path: Path) -> None:
    """`pipeline/direct` holds two flat lanes and one package lane, and the package must be policed.

    The domain walk cannot see it (it is keyed on `ingest`/`execution`) and the module walk used to
    look only at `*.py` directly in the directory, so a `climate/` importing `water_gauges` -- or the
    reverse -- crossed two lanes with nothing to catch it.
    """
    lane_dir = tmp_path / "pipeline" / "direct"
    climate_dir = lane_dir / "climate"
    climate_dir.mkdir(parents=True)
    (lane_dir / "__init__.py").write_text("", encoding="utf-8")
    (lane_dir / "water_gauges.py").write_text("", encoding="utf-8")
    (lane_dir / "fire_detections.py").write_text(
        "from agri_data_service.pipeline.direct.climate.source import fetch_climate_day\n", encoding="utf-8"
    )
    (climate_dir / "__init__.py").write_text("", encoding="utf-8")
    (climate_dir / "forward.py").write_text(
        "from agri_data_service.pipeline.direct import water_gauges\n", encoding="utf-8"
    )

    violations = _sibling_module_violations(tmp_path, "pipeline/direct")

    expected_violation_count = 2  # one flat lane reaching into the package, one package lane reaching out
    assert len(violations) == expected_violation_count, violations
    assert any("climate" in violation for violation in violations)
    assert any("water_gauges" in violation for violation in violations)


def test_the_cross_lane_rule_actually_fires(tmp_path: Path) -> None:
    """Eleven real lanes currently cross nothing, so without this the rule proves nothing."""
    lane_dir = tmp_path / "pipeline" / "lanes"
    lane_dir.mkdir(parents=True)
    (lane_dir / "__init__.py").write_text("", encoding="utf-8")
    (lane_dir / "signal.py").write_text(
        "from agri_data_service.pipeline.lanes.vegetation import read_vegetation_day\n", encoding="utf-8"
    )
    (lane_dir / "vegetation.py").write_text("from . import signal\n", encoding="utf-8")

    violations = _sibling_module_violations(tmp_path, "pipeline/lanes")

    expected_violation_count = 2  # one absolute import, one relative import
    assert len(violations) == expected_violation_count, violations


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


# --- interface/cli is an adapter, not an execution layer ------------------------------------------
#
# The lattice above lets `interface` import anything, which is right for a Click adapter and useless
# as a check on what the adapter DEFINES. Track spec `repository_conformity_hardening_20260901`
# acceptance gate 3: "`interface/cli` contains adapters only; reusable lane/framework/orchestration
# logic has a domain owner". Two bounded AST rules, chosen because each names a specific ownership
# decision rather than a style preference:
#
# 1. **Transaction ownership.** A `with`/`async with` item calling `.begin()` -- `session.begin()`,
#    `engine.begin()` -- is the adapter deciding a commit boundary. That is a durability decision
#    about domain work, and it belongs with the domain that knows what a partial write means. It is
#    also why these are hard to test: the boundary is only reachable through a Click invocation.
# 2. **Framework definition.** A class named for a lane, runner, framework, orchestrator or executor
#    is reusable machinery. Defined here, it can only ever be reused by another CLI command.
#
# Deliberately NOT flagged: `async with some_session() as session` without `.begin()`. Opening a
# read scope to render output is exactly what an adapter does.

CLI_ADAPTER_DIRECTORY = "interface/cli"

#: A class named for execution machinery rather than for Click wiring.
CLI_FRAMEWORK_CLASS_SUFFIXES: tuple[str, ...] = ("Framework", "Runner", "Lane", "Orchestrator", "Executor")

#: The context-manager method that opens a transaction, whatever it is called on.
CLI_TRANSACTION_METHOD = "begin"

#: What `c2` still owes, counted at HEAD `ad4e015`: 24 transaction boundaries and 2 framework
#: classes, all in `commands.py`. The exact sites are pinned rather than only how many there are,
#: so a PARTIAL extraction and a one-for-one SWAP -- one site extracted, a new one grown elsewhere
#: -- are both loud; a count alone silently accepts either. `test_cli_is_a_thin_click_adapter` is
#: `xfail(strict=True)`, so it fails as XPASS the moment the last one lands and the whole block
#: flips to an enforced rule. The line numbers move whenever `commands.py` does: regenerate this
#: list from the assertion message below, never by editing entries until the test passes again.
CLI_ADAPTER_VIOLATIONS: tuple[str, ...] = (
    "interface/cli/commands.py:620 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:734 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:805 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:980 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:1063 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:1174 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:1580 owns a transaction boundary 'combined_local_engine().begin()'",
    "interface/cli/commands.py:1856 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:1950 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:1959 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2047 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2165 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2169 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2228 defines execution machinery 'class LaneChunkRunner'",
    "interface/cli/commands.py:2241 defines execution machinery 'class ChunkedLane'",
    "interface/cli/commands.py:2500 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2540 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2626 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2638 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2740 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2753 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2937 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2939 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:2994 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:3001 owns a transaction boundary 'session.begin()'",
    "interface/cli/commands.py:3045 owns a transaction boundary 'session.begin()'",
)

#: Quoted by the xfail reason below; `CLI_ADAPTER_VIOLATIONS` is the source of truth.
CLI_ADAPTER_VIOLATION_COUNT = len(CLI_ADAPTER_VIOLATIONS)


def _cli_adapter_violations(pkg_root: Path) -> list[str]:
    """Return every transaction boundary and framework class defined under `interface/cli`."""
    adapter_dir = pkg_root / CLI_ADAPTER_DIRECTORY
    if not adapter_dir.is_dir():
        return []
    violations: list[tuple[str, int, str]] = []
    for py_file in sorted(adapter_dir.rglob("*.py")):
        relative = py_file.relative_to(pkg_root).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith(CLI_FRAMEWORK_CLASS_SUFFIXES):
                violations.append((relative, node.lineno, f"defines execution machinery 'class {node.name}'"))
            elif isinstance(node, ast.With | ast.AsyncWith):
                violations.extend(
                    (relative, node.lineno, f"owns a transaction boundary '{ast.unparse(item.context_expr)}'")
                    for item in node.items
                    if isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Attribute)
                    and item.context_expr.func.attr == CLI_TRANSACTION_METHOD
                )
    return [f"{path}:{line} {detail}" for path, line, detail in sorted(violations)]


@pytest.mark.xfail(
    strict=True,
    reason=(
        f"{CLI_ADAPTER_VIOLATION_COUNT} known violations await the wave-C2 extraction; "
        "the count is pinned by test_cli_adapter_violations_stay_pinned"
    ),
)
def test_cli_is_a_thin_click_adapter() -> None:
    """`interface/cli` may wire Click to a domain; it may not own transactions or lane frameworks."""
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    violations = _cli_adapter_violations(pkg_root)

    assert not violations, "CLI thin-adapter violations found:\n" + "\n".join(violations)


def test_cli_adapter_violations_stay_pinned() -> None:
    """The xfail above only proves 'more than zero'. This pins WHICH sites, in both directions."""
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service"
    violations = _cli_adapter_violations(pkg_root)

    assert violations == list(CLI_ADAPTER_VIOLATIONS), (
        f"the CLI adapter debt moved from {CLI_ADAPTER_VIOLATION_COUNT} sites to {len(violations)}, "
        "or moved sideways. If c2 extracted work, delete the extracted entries from "
        "CLI_ADAPTER_VIOLATIONS; if a command grew a new transaction or framework class, put it in a "
        "domain package instead. A one-for-one swap keeps the count and still fails here, which is "
        "the point.\nCurrent:\n" + "\n".join(violations)
    )


def test_the_cli_adapter_rule_catches_both_shapes(tmp_path: Path) -> None:
    """Prove the rule fires, and that a plain read scope is deliberately left alone."""
    adapter_dir = tmp_path / "interface" / "cli"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "__init__.py").write_text("", encoding="utf-8")
    (adapter_dir / "commands.py").write_text(
        "async def persist(loader_session, database_url):\n"
        "    async with loader_session() as session, session.begin():\n"
        "        await session.execute('select 1')\n"
        "    async with loader_session() as read_only:\n"
        "        await read_only.execute('select 1')\n"
        "\n"
        "class ChunkedLane:\n"
        "    pass\n",
        encoding="utf-8",
    )

    violations = _cli_adapter_violations(tmp_path)

    expected_violation_count = 2  # one transaction boundary, one framework class
    assert len(violations) == expected_violation_count, violations
    assert any("session.begin()" in violation for violation in violations)
    assert any("class ChunkedLane" in violation for violation in violations)
