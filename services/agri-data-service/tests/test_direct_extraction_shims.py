"""`p4-extract`: the moved modules import no `pipeline/direct/**`, and every legacy shim binds the moved objects.

See `DEPRECATED_ALIASES.md`, "Live: the `p4-extract` shims", for the table this file holds to the tree.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Final

import pytest

SERVICE_ROOT: Final = Path(__file__).resolve().parents[1]
PACKAGE_ROOT: Final = SERVICE_ROOT / "src" / "agri_data_service"
DIRECT_PACKAGE: Final = "agri_data_service.pipeline.direct"

#: Directories the Phase-7 cleanup keeps, so nothing in them may lean on `pipeline/direct/**`.
EXTRACTED_ROOTS: Final[tuple[str, ...]] = (
    "pipeline/lanes",
    "warehouse/field_products",
)

#: Legacy module -> the module it now re-exports from. One row per `DEPRECATED_ALIASES.md` row.
SHIMS: Final[dict[str, str]] = {
    f"{DIRECT_PACKAGE}.climate.products": "agri_data_service.warehouse.field_products.climate",
    f"{DIRECT_PACKAGE}.soil.products": "agri_data_service.warehouse.field_products.soil",
    f"{DIRECT_PACKAGE}.vegetation.products": "agri_data_service.warehouse.field_products.vegetation",
    **{
        f"{DIRECT_PACKAGE}.{layer}.{leaf}": f"agri_data_service.pipeline.lanes.{layer}.{leaf}"
        for layer, leaves in {
            "burn_severity": ("source_protocol", "mtbs", "products"),
            "drought": ("source_protocol", "usdm", "products"),
            "evacuation_zones": ("watermark", "products", "rows", "source", "support"),
            "watersheds": ("watermark", "source"),
            "crop_cover": ("source_protocol", "source", "products"),
            "land_context": ("source_protocol", "source", "products"),
        }.items()
        for leaf in leaves
    },
    f"{DIRECT_PACKAGE}.sensors.adapter": "agri_data_service.pipeline.lanes.sensors.merge",
}

#: The one partial shim: `adapter.py` keeps its own adapter and re-exports only the merge unit.
PARTIAL_SHIM_NAMES: Final[dict[str, frozenset[str]]] = {
    f"{DIRECT_PACKAGE}.sensors.adapter": frozenset({"merge_sensors_day", "DirectSensorsMerge", "DirectSensorsError"}),
}


def _direct_imports(path: Path, package_root: Path = PACKAGE_ROOT) -> list[str]:
    """Every `pipeline.direct` import in one file, absolute or relative, as `file:line module`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = ("agri_data_service", *path.relative_to(package_root).parts[:-1])
    found: list[str] = []
    for node in ast.walk(tree):
        # The extend lives inside the branches so `node` is statically an Import/ImportFrom (both
        # carry `lineno`); a bare `ast.AST` does not, and the type checker is right to say so.
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join((*package_parts[: len(package_parts) - node.level + 1], *filter(None, [node.module])))
            modules = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        found.extend(
            f"{path.relative_to(package_root).as_posix()}:{node.lineno} {module}"
            for module in modules
            if module == DIRECT_PACKAGE or module.startswith(DIRECT_PACKAGE + ".")
        )
    return sorted(set(found))


def test_no_extracted_module_imports_pipeline_direct() -> None:
    """The import closure moved whole: a lane or field-product module reaching back breaks at Phase 7."""
    offenders = [
        hit
        for root in EXTRACTED_ROOTS
        for path in sorted((PACKAGE_ROOT / root).rglob("*.py"))
        for hit in _direct_imports(path)
    ]

    assert offenders == []


def test_the_direct_import_walk_sees_both_spellings(tmp_path: Path) -> None:
    """Guard the guard: an absolute import and a relative one into `pipeline/direct` are both reported."""
    lane = tmp_path / "pipeline" / "lanes" / "example"
    lane.mkdir(parents=True)
    offending = lane / "source.py"
    offending.write_text(
        "from agri_data_service.pipeline.direct.drought.usdm import USDM_DROUGHT_SOURCE\n"
        "from ...direct.soil import products\n"
        "from agri_data_service.pipeline import direct\n"
        "import agri_data_service.pipeline.direct.sensors.adapter\n",
        encoding="utf-8",
    )

    hits = _direct_imports(offending, package_root=tmp_path)

    assert any(hit.endswith("pipeline.direct.drought.usdm") for hit in hits)
    assert any(hit.endswith("pipeline.direct.soil.products") for hit in hits)
    assert any(hit.endswith(":3 agri_data_service.pipeline.direct") for hit in hits)
    assert any(hit.endswith("pipeline.direct.sensors.adapter") for hit in hits)


@pytest.mark.parametrize(("legacy", "home"), sorted(SHIMS.items()))
def test_each_shim_binds_the_moved_objects_and_is_tracked(legacy: str, home: str) -> None:
    """A copied definition would split `isinstance`/`except`/monkeypatch across two spellings."""
    legacy_module = importlib.import_module(legacy)
    home_module = importlib.import_module(home)
    names = PARTIAL_SHIM_NAMES.get(legacy, frozenset(legacy_module.__all__))

    assert names <= set(legacy_module.__all__)
    diverged = sorted(name for name in names if getattr(legacy_module, name) is not getattr(home_module, name))
    assert diverged == []
    assert f"`{legacy}`" in (SERVICE_ROOT / "DEPRECATED_ALIASES.md").read_text(encoding="utf-8")
