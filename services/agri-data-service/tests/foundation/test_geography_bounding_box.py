"""The bbox-helper extraction: `foundation/geography/bounding_box.py` stays L0-pure and every

pipeline lane that used to reach into `ingest/mtbs.py` for it now imports the new home instead.

`layer-lanes.md` §5a: "the shared half moves down; the dependents never move sideways." This guards
both halves of that sentence -- the move (module purity) and the sideways import it was meant to
retire (the six lanes below).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agri_data_service.foundation.geography.bounding_box import (
    BBOX_OPTION,
    BBOX_ORDINATE_COUNT,
    BoundingBox,
    format_bounding_box_inline,
    inline_bbox_value,
    parse_bounding_box,
)

#: `pipeline/direct/<domain>/forward.py` (or a bare `<domain>.py`) modules that imported
#: `inline_bbox_value` from `ingest/mtbs.py` before this extraction. `burn_severity/forward.py` is
#: the one lane that legitimately owned `ingest/mtbs.py` and is included anyway: the extraction moved
#: the mechanism down, so every lane reads it from its new home and `ingest/mtbs.py` keeps no
#: re-export of it at all (the warning shims were deleted 2026-09-18, STYLE-REVIEW-W4 S3).
LANES_THAT_USED_TO_IMPORT_INLINE_BBOX_VALUE_FROM_INGEST_MTBS: tuple[str, ...] = (
    "pipeline/direct/burn_severity/forward.py",
    "pipeline/direct/evacuation_zones/forward.py",
    "pipeline/direct/fire_detections.py",
    "pipeline/direct/fire_perimeters/forward.py",
    "pipeline/direct/sensors/forward.py",
    "pipeline/direct/watersheds/forward.py",
    "pipeline/direct/weather_observations/forward.py",
)


def _src_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "agri_data_service"


def _imported_modules(py_path: Path) -> set[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_bounding_box_module_imports_stdlib_only() -> None:
    """`foundation/geography/bounding_box.py` passes the L0 Admission Test with no ruled exception:

    no first-party import at all, matching `foundation/AGENTS.md` criterion 1 exactly (stricter than
    `foundation/region/`, which is a documented exception).
    """
    module_path = _src_root() / "foundation" / "geography" / "bounding_box.py"
    imported = _imported_modules(module_path)
    first_party = {name for name in imported if name.startswith("agri_data_service")}
    assert not first_party, f"foundation/geography/bounding_box.py imports first-party modules: {first_party}"


@pytest.mark.parametrize("relative_path", LANES_THAT_USED_TO_IMPORT_INLINE_BBOX_VALUE_FROM_INGEST_MTBS)
def test_the_lane_no_longer_imports_ingest_mtbs_for_the_bbox_helper(relative_path: str) -> None:
    module_path = _src_root() / relative_path
    assert module_path.is_file(), f"expected lane file at {module_path}"
    imported = _imported_modules(module_path)
    assert "agri_data_service.ingest.mtbs" not in imported, (
        f"{relative_path} still imports agri_data_service.ingest.mtbs; "
        "it should import agri_data_service.foundation.geography.bounding_box instead "
        "(the burn_severity domain excepted only for BoundingBox, a plain non-deprecated type alias)"
    )


def test_the_new_module_keeps_the_pre_extraction_name_importable_as_a_documented_alias() -> None:
    assert inline_bbox_value is format_bounding_box_inline


def test_parse_and_format_round_trip_through_the_new_module() -> None:
    argv = format_bounding_box_inline(["--bbox", "-125,42,-111,49", "--release-year", "2022"])
    assert argv == ["--bbox=-125,42,-111,49", "--release-year", "2022"]
    assert parse_bounding_box("-125,42,-111,49") == (-125.0, 42.0, -111.0, 49.0)


def test_bbox_constants_and_type_are_exported() -> None:
    assert BBOX_OPTION == "--bbox"
    assert BBOX_ORDINATE_COUNT == 4
    # `BoundingBox` is a bare 4-tuple alias, not a runtime-constructible class; this proves the
    # module exports the same name `ingest/mtbs.py` used to define locally.
    assert BoundingBox == tuple[float, float, float, float]
