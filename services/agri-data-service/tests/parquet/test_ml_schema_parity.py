"""Read the sibling ML service's pinned schemas and prove this service's copies are the same objects.

Three contracts are held as COPIES in two repositories' trees, on purpose: `services/agri-data-service`
and `services/plantgeo-ml-service` deploy independently and neither imports the other, so a shared
package would couple two release cadences to buy nothing but an import. The cost of that decision is
that "identical" was, until this file existed, a claim made in prose in three module headers
(`warehouse/schemas/fire_risk.py`, `warehouse/schemas/weather_forecast.py`,
`warehouse/schemas/expert_labels.py`) and checkable only by a human diffing two files.

What a drift would actually do is the reason this is a test and not a review note: the writer
encodes Parquet against ITS copy and the reader decodes against THIS one, so a single field whose
nullability or position moved produces files that are rejected at read time in the other service,
with an error that names a column rather than the commit that moved it.

A FAILURE HERE NAMES THE DECISION, NOT JUST THE DIFF: the check is bidirectional and cannot know
which copy is right, so `_divergence_message` prints both file paths, the section that owns the
field (`_CONTRACT_SECTION`) and every column position that moved. The 2026-09-19 `fire-risk` break
was a correct amendment landing on one side only; reading the section answered it, diffing the two
modules would not have.

The sibling tree is not installed -- it is a path on disk in a monorepo checkout and is absent from
the agri Docker image -- so the modules are loaded from their FILE PATHS and the whole module skips
with a named reason when that tree is not there. This mirrors `test_lane_contract.py`'s
`_require_ml_service_tree`. `importlib.util.spec_from_file_location` alone is not enough: the ML
modules import each other by absolute dotted name, so the package ROOT goes on `sys.path` (derived
from the same file path, removed again when the module's tests finish) and the import proceeds
normally from there.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.warehouse.schemas.expert_labels import (
    EXPERT_LABEL_EXPORT_SCHEMA,
    EXPERT_LABEL_SORT_COLUMNS,
)
from agri_data_service.warehouse.schemas.fire_risk import FIRE_RISK_SCHEMA
from agri_data_service.warehouse.schemas.weather_forecast import WEATHER_FORECAST_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

_MONOREPO_ROOT = Path(__file__).resolve().parents[4]
_ML_SERVICE_SRC = _MONOREPO_ROOT / "services" / "plantgeo-ml-service" / "src"
_ML_SERVICE_PACKAGE = _ML_SERVICE_SRC / "plantgeo_ml_service"

#: The three sibling modules holding a contract this service copies. Each dotted name doubles as the
#: file path under the package root, which is how the module is LOCATED on disk before it is loaded.
_FIRE_RISK_MODULE = "plantgeo_ml_service.warehouse.streams"
_WEATHER_FORECAST_MODULE = "plantgeo_ml_service.warehouse.weather_forecast"
_EXPERT_LABEL_MODULE = "plantgeo_ml_service.pipeline.expert_labels"
_ML_MODULES = (_FIRE_RISK_MODULE, _WEATHER_FORECAST_MODULE, _EXPERT_LABEL_MODULE)


@dataclass(frozen=True, slots=True)
class MlServiceSchemas:
    """The three pinned contracts read out of the sibling service's own modules."""

    # `Any`, because these objects are defined in a tree this service never imports statically:
    # the sibling is a path on disk, not a dependency, so there is no type to name here.
    fire_risk: Any
    weather_forecast: Any
    expert_labels: Any


#: The section that OWNS every field these copies share, so a failure points at the decision rather
#: than at two files. Section 3's 2026-09-19 amendment is what makes `fire-risk` carry
#: `quantile = "point"` as a string while the drawn lanes keep the numeric level.
_CONTRACT_SECTION = "conductor/code_styleguides/layer-lanes.md section 3 (forecast provenance)"

#: Both halves of each copied contract, by path, so the message names the two files to open.
_AGRI_FIRE_RISK_MODULE = "services/agri-data-service/src/agri_data_service/warehouse/schemas/fire_risk.py"
_ML_FIRE_RISK_MODULE = "services/plantgeo-ml-service/src/plantgeo_ml_service/warehouse/streams.py"


def _column_shapes(arrow_schema: Any) -> list[tuple[str, str, bool]]:
    """Render a schema as the (name, type, nullable) triples parity actually compares."""
    return [(field.name, str(field.type), field.nullable) for field in arrow_schema]


def _divergence_message(
    stream: str,
    *,
    agri_module: str,
    ml_module: str,
    agri_columns: list[tuple[str, str, bool]],
    ml_columns: list[tuple[str, str, bool]],
) -> str:
    """Name the stream, both copies, the owning contract section, and every column position that moved."""
    moved = [
        f"  position {index}: agri {agri_column!r} vs ml {ml_column!r}"
        for index, (agri_column, ml_column) in enumerate(zip_longest(agri_columns, ml_columns))
        if agri_column != ml_column
    ]
    return "\n".join(
        [
            f"the two {stream!r} schema copies diverged. The ML service WRITES these partitions and agri",
            "READS them, so the copy to change is whichever one disagrees with the contract -- not simply",
            "the older one. Read the section before editing either file:",
            f"  contract: {_CONTRACT_SECTION}",
            f"  agri (reader): {agri_module}",
            f"  ml   (writer): {ml_module}",
            *(moved or ["  (the columns agree; the divergence is in sort order, codec or name)"]),
        ]
    )


def _module_path(dotted: str) -> Path:
    """Return the file the dotted ML module name lives in, under the sibling package root."""
    return _ML_SERVICE_PACKAGE.joinpath(*dotted.split(".")[1:]).with_suffix(".py")


def _require_ml_service_tree() -> None:
    """Skip the whole check when this checkout holds no sibling service to read."""
    if not _ML_SERVICE_PACKAGE.is_dir():
        pytest.skip(
            f"no plantgeo-ml-service tree at {_ML_SERVICE_PACKAGE}; schema parity between the two "
            "services is only checkable in a monorepo checkout, never inside the agri Docker image"
        )
    missing = [str(path) for path in map(_module_path, _ML_MODULES) if not path.is_file()]
    if missing:
        pytest.skip(f"the plantgeo-ml-service tree is present but ships none of {missing}")


@pytest.fixture(scope="module")
def ml_schemas() -> Iterator[MlServiceSchemas]:
    """Load the sibling's three modules from disk, yield their contracts, and unhook the path again."""
    _require_ml_service_tree()
    source_root = str(_ML_SERVICE_SRC)
    added = source_root not in sys.path
    if added:
        sys.path.insert(0, source_root)
    try:
        try:
            loaded: dict[str, ModuleType] = {name: importlib.import_module(name) for name in _ML_MODULES}
        except ImportError as error:  # pragma: no cover - a dependency skew, not a contract failure
            pytest.skip(f"the plantgeo-ml-service tree is present but does not import here: {error}")
        yield MlServiceSchemas(
            fire_risk=loaded[_FIRE_RISK_MODULE].FIRE_RISK_SCHEMA,
            weather_forecast=loaded[_WEATHER_FORECAST_MODULE].WEATHER_FORECAST_SCHEMA,
            expert_labels=loaded[_EXPERT_LABEL_MODULE].EXPERT_LABEL_SCHEMA,
        )
    finally:
        if added and source_root in sys.path:
            sys.path.remove(source_root)


def test_the_fire_risk_schema_is_the_ml_services_own_object_field_for_field(ml_schemas: MlServiceSchemas) -> None:
    """`fire-risk` is written THERE and read HERE, so a moved field breaks the read, not the write."""
    message = _divergence_message(
        "fire-risk",
        agri_module=_AGRI_FIRE_RISK_MODULE,
        ml_module=_ML_FIRE_RISK_MODULE,
        agri_columns=_column_shapes(FIRE_RISK_SCHEMA.arrow_schema),
        ml_columns=_column_shapes(ml_schemas.fire_risk.arrow_schema),
    )
    assert FIRE_RISK_SCHEMA.arrow_schema.equals(ml_schemas.fire_risk.arrow_schema), message
    assert FIRE_RISK_SCHEMA.sort_columns == ml_schemas.fire_risk.sort_columns, message
    assert FIRE_RISK_SCHEMA.compression == ml_schemas.fire_risk.compression, message
    assert FIRE_RISK_SCHEMA.name == ml_schemas.fire_risk.name, message


def test_the_fire_risk_copies_agree_column_by_column_so_a_failure_names_the_field(
    ml_schemas: MlServiceSchemas,
) -> None:
    """`Schema.equals` answers one bool; this answers WHICH column moved, which is the actionable fact."""
    here = _column_shapes(FIRE_RISK_SCHEMA.arrow_schema)
    there = _column_shapes(ml_schemas.fire_risk.arrow_schema)

    assert here == there, _divergence_message(
        "fire-risk",
        agri_module=_AGRI_FIRE_RISK_MODULE,
        ml_module=_ML_FIRE_RISK_MODULE,
        agri_columns=here,
        ml_columns=there,
    )


def test_the_weather_forecast_schema_is_the_ml_services_own_nineteen_columns(ml_schemas: MlServiceSchemas) -> None:
    """FR-12: the provider run is written by the ML service; agri only registers the slug to serve it."""
    assert WEATHER_FORECAST_SCHEMA.arrow_schema.equals(ml_schemas.weather_forecast.arrow_schema)
    assert WEATHER_FORECAST_SCHEMA.sort_columns == ml_schemas.weather_forecast.sort_columns
    assert WEATHER_FORECAST_SCHEMA.compression == ml_schemas.weather_forecast.compression
    assert WEATHER_FORECAST_SCHEMA.name == ml_schemas.weather_forecast.name


def test_the_weather_forecast_copies_agree_column_by_column_so_a_failure_names_the_field(
    ml_schemas: MlServiceSchemas,
) -> None:
    here = [(field.name, str(field.type), field.nullable) for field in WEATHER_FORECAST_SCHEMA.arrow_schema]
    there = [(field.name, str(field.type), field.nullable) for field in ml_schemas.weather_forecast.arrow_schema]

    assert here == there


def test_the_expert_label_export_writes_the_shape_the_ml_service_pins_for_reading(
    ml_schemas: MlServiceSchemas,
) -> None:
    """The export direction is agri -> ML, and that reader refuses a file whose schema is not this one.

    It compares with `schema.remove_metadata().equals(...)`, so the comparison here is metadata-free
    for the same reason: the pandas metadata pyarrow would otherwise attach carries a library
    version and would make two identical exports differ.
    """
    assert EXPERT_LABEL_EXPORT_SCHEMA.remove_metadata().equals(ml_schemas.expert_labels.remove_metadata())


def test_the_expert_label_sort_column_exists_on_both_sides_so_the_exported_order_is_readable(
    ml_schemas: MlServiceSchemas,
) -> None:
    """The export sorts by `label_key`; a reader cannot reproduce that order without the column."""
    assert EXPERT_LABEL_SORT_COLUMNS == ("label_key",)
    for column in EXPERT_LABEL_SORT_COLUMNS:
        assert column in ml_schemas.expert_labels.names
        assert not ml_schemas.expert_labels.field(column).nullable, "a nullable sort key is not a total order"


def test_the_parity_check_runs_rather_than_skips_in_a_monorepo_checkout(ml_schemas: MlServiceSchemas) -> None:
    """A skip-shaped parity test proves nothing; reaching this line is what makes the others evidence."""
    assert _ML_SERVICE_PACKAGE.is_dir()
    assert ml_schemas.fire_risk.name == "fire-risk"
