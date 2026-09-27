"""The soil-properties registration shell: schema, derivation, pins, watermark, registration and writer contract.

Rationale lives in `pipeline/direct/soil_properties/AGENTS.md`; the numbers pinned here are CONTRACT C1/C10.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.lane_contract import resolve_static_lane
from agri_data_service.pipeline.direct import NO_SUCH_DEFECT, NOT_BBOX_BOUNDED
from agri_data_service.pipeline.direct.soil_properties import forward
from agri_data_service.pipeline.direct.soil_properties.products import (
    CELL_CENTRE_OFFSET_DEGREES,
    LATTICE_CELL_COUNT,
    LATTICE_COLUMNS,
    LATTICE_DEGREES,
    LATTICE_EAST,
    LATTICE_NORTH,
    LATTICE_ROWS,
    LATTICE_SOUTH,
    LATTICE_WEST,
    PROPERTY_UNITS,
    RELEASE_DAY,
    RELEASE_ID,
    SOURCE_FILE_PINS,
    expected_pin_keys,
)
from agri_data_service.pipeline.direct.soil_properties.watermark import read_soil_properties_source_watermark
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    DERIVED_ZOOM_TIERS,
    MAX_DERIVATION_ROWS,
    TIER_RESOLUTION_DEGREES,
    GridAggregation,
    derive_tier,
    tier_derivation,
    validate_derivation_against_schema,
)
from agri_data_service.warehouse.schemas.soil_properties import (
    SOIL_PROPERTIES_CONSTANT_COLUMNS,
    SOIL_PROPERTIES_STREAM,
    SOIL_PROPERTIES_VALUE_COLUMNS,
    SOIL_PROPERTY_CODES,
)

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.zoom import ZoomTier

_PACKAGE_ROOT: Final = (
    Path(__file__).resolve().parents[3] / "src" / "agri_data_service" / "pipeline" / "direct" / "soil_properties"
)
EXPECTED_VALUE_COLUMN_COUNT: Final = 30
EXPECTED_CELL_COUNT: Final = 3_920_000
EXPECTED_Z9_CELLS: Final = 2
LATEST_PIN_INSTANT: Final = dt.datetime(2020, 6, 2, 16, 14, 20, tzinfo=dt.UTC)
OCD_PIN_DAY: Final = dt.date(2020, 5, 26)
MAXIMUM_MAPPED_VALUE: Final = 32_767.0
TODAY: Final = dt.date(2026, 9, 27)
MANIFEST_SHA256: Final = "0" * 64
FLOAT_TOLERANCE: Final = 1e-9
REGISTRY_IMPORT_ALLOWLIST: Final = (
    "agri_data_service.foundation",
    "agri_data_service.warehouse",
    "agri_data_service.pipeline.direct.soil_properties",
)


def _base_rows(cells: list[tuple[float, float, float]]) -> pl.DataFrame:
    """Build a base-rung table in the lane's exact schema: (origin lon, origin lat, value for every column)."""
    schema = observed_stream_schema(SOIL_PROPERTIES_STREAM).arrow_schema
    rows = [
        {
            "cell_longitude": longitude,
            "cell_latitude": latitude,
            **dict.fromkeys(SOIL_PROPERTIES_VALUE_COLUMNS, value),
            "source_release": "soilgrids-v2.0",
            "source_manifest_sha256": MANIFEST_SHA256,
            "release_day": RELEASE_DAY,
        }
        for longitude, latitude, value in cells
    ]
    frame = pl.from_arrow(pa.Table.from_pylist(rows, schema=schema))
    assert isinstance(frame, pl.DataFrame)
    return frame


def _cast_back(frame: pl.DataFrame) -> pa.Table:
    """Cast a derived rung back to the storage contract, as `write_partition` does."""
    return frame.to_arrow().cast(observed_stream_schema(SOIL_PROPERTIES_STREAM).arrow_schema)


# --------------------------------------------------------------------------------------------
# Schema and derivation
# --------------------------------------------------------------------------------------------


def test_schema_is_two_origin_keys_thirty_float64_values_and_three_constants() -> None:
    schema = observed_stream_schema(SOIL_PROPERTIES_STREAM)
    names = schema.arrow_schema.names

    assert names == [
        "cell_longitude",
        "cell_latitude",
        *SOIL_PROPERTIES_VALUE_COLUMNS,
        *SOIL_PROPERTIES_CONSTANT_COLUMNS,
    ]
    assert len(SOIL_PROPERTIES_VALUE_COLUMNS) == EXPECTED_VALUE_COLUMN_COUNT
    assert "clay_15_30cm" in SOIL_PROPERTIES_VALUE_COLUMNS
    assert "geometry_wkb" not in names
    assert all(not field.nullable for field in schema.arrow_schema)
    for column in ("cell_longitude", "cell_latitude", *SOIL_PROPERTIES_VALUE_COLUMNS):
        assert schema.arrow_schema.field(column).type == pa.float64(), column
    assert schema.arrow_schema.field("release_day").type == pa.date32()
    assert schema.sort_columns == ("cell_latitude", "cell_longitude")


def test_derivation_means_every_value_and_carries_the_constants_with_no_key() -> None:
    strategy = tier_derivation(SOIL_PROPERTIES_STREAM).strategy

    assert isinstance(strategy, GridAggregation)
    assert strategy.key_columns == ()
    assert strategy.key_columns_by_tier is None
    fates = {spec.column: spec.how for spec in strategy.aggregations}
    assert fates == {
        **dict.fromkeys(SOIL_PROPERTIES_VALUE_COLUMNS, "mean"),
        **dict.fromkeys(SOIL_PROPERTIES_CONSTANT_COLUMNS, "first"),
    }
    assert validate_derivation_against_schema(SOIL_PROPERTIES_STREAM) == ()


def test_coarse_rungs_are_exact_means_of_their_base_cells_on_the_lattice() -> None:
    """P0 derivation probe: four 0.005 cells in one z9 cell, plus one neighbour across a z0 boundary."""
    base = _base_rows(
        [
            (-117.0, 46.73, 10.0),
            (-116.995, 46.73, 11.0),
            (-117.0, 46.735, 12.0),
            (-116.995, 46.735, 13.0),
            (-120.005, 44.995, 57.0),
        ],
    )
    derived = {tier: derive_tier(base, stream=SOIL_PROPERTIES_STREAM, tier=tier) for tier in DERIVED_ZOOM_TIERS}

    z9_block = derived[9].filter(
        (derived[9]["cell_longitude"] - (-117.0)).abs() < FLOAT_TOLERANCE,
    )
    assert z9_block.height == 1
    assert z9_block["cell_latitude"][0] == pytest.approx(46.73)
    assert z9_block["phh2o_0_5cm"][0] == pytest.approx(11.5)
    assert derived[9].height == EXPECTED_Z9_CELLS
    # z0 is 5 degrees: (-117, 46.73) floors to (-120, 45); (-120.005, 44.995) floors to (-125, 40).
    z0_origins = sorted(zip(derived[0]["cell_longitude"].to_list(), derived[0]["cell_latitude"].to_list(), strict=True))
    assert z0_origins == [(-125.0, 40.0), (-120.0, 45.0)]
    for frame in derived.values():
        assert frame.columns == base.columns
        _cast_back(frame)


@pytest.mark.parametrize(("tier", "resolution"), sorted(TIER_RESOLUTION_DEGREES.items()))
def test_a_lattice_origin_on_a_rung_boundary_floors_into_the_cell_that_starts_there(
    tier: ZoomTier, resolution: float
) -> None:
    """Boundary origins (multiples of the rung pitch) must not slip one cell west or south."""
    origin = (-120.0, 45.0)
    assert (origin[0] / resolution).is_integer()
    assert (origin[1] / resolution).is_integer()
    derived = derive_tier(_base_rows([(*origin, 1.0)]), stream=SOIL_PROPERTIES_STREAM, tier=tier)

    assert derived["cell_longitude"][0] == pytest.approx(origin[0])
    assert derived["cell_latitude"][0] == pytest.approx(origin[1])


def test_a_coarse_mean_of_maximum_mapped_values_casts_back_to_schema() -> None:
    """No column is summed, so the int16-range maximum survives every rung exactly."""
    corners = [(-125.0, 45.0), (-120.005, 45.0), (-125.0, 49.995), (-120.005, 49.995)]
    base = _base_rows([(longitude, latitude, MAXIMUM_MAPPED_VALUE) for longitude, latitude in corners])

    for tier in DERIVED_ZOOM_TIERS:
        derived = derive_tier(base, stream=SOIL_PROPERTIES_STREAM, tier=tier)
        cast = _cast_back(derived)
        assert all(value == MAXIMUM_MAPPED_VALUE for value in cast.column("cec_15_30cm").to_pylist())


# --------------------------------------------------------------------------------------------
# Release identity, lattice and pins
# --------------------------------------------------------------------------------------------


def test_lattice_is_the_contract_grid_and_stays_on_the_unbanded_path() -> None:
    assert round((LATTICE_EAST - LATTICE_WEST) / LATTICE_DEGREES) == LATTICE_COLUMNS
    assert round((LATTICE_NORTH - LATTICE_SOUTH) / LATTICE_DEGREES) == LATTICE_ROWS
    assert LATTICE_CELL_COUNT == EXPECTED_CELL_COUNT
    assert LATTICE_CELL_COUNT < MAX_DERIVATION_ROWS, "even a fully valid lattice must stay unbanded"
    assert CELL_CENTRE_OFFSET_DEGREES == LATTICE_DEGREES / 2
    for resolution in TIER_RESOLUTION_DEGREES.values():
        ratio = resolution / LATTICE_DEGREES
        assert abs(ratio - round(ratio)) < FLOAT_TOLERANCE, f"0.005 must nest exactly into {resolution}"


def test_every_property_depth_is_pinned_exactly_once_with_a_zoned_instant_and_an_etag() -> None:
    keys = [(pin.property_code, pin.depth_interval) for pin in SOURCE_FILE_PINS]

    assert len(keys) == len(set(keys)) == EXPECTED_VALUE_COLUMN_COUNT
    assert set(keys) == expected_pin_keys()
    assert {pin.value_column for pin in SOURCE_FILE_PINS} == set(SOIL_PROPERTIES_VALUE_COLUMNS)
    assert len({pin.etag for pin in SOURCE_FILE_PINS}) == EXPECTED_VALUE_COLUMN_COUNT
    for pin in SOURCE_FILE_PINS:
        assert pin.last_modified.utcoffset() == dt.timedelta(0), pin.file_name
        assert pin.etag.startswith('"'), pin.file_name
        assert pin.content_length > 0, pin.file_name
        assert pin.vrt_url.endswith(f"/{pin.property_code}/{pin.file_name}")
    assert {pin.last_modified.date() for pin in SOURCE_FILE_PINS if pin.property_code == "ocd"} == {OCD_PIN_DAY}


def test_every_property_has_a_unit_row() -> None:
    assert set(PROPERTY_UNITS) == set(SOIL_PROPERTY_CODES)
    assert all(unit.divisor > 0 for unit in PROPERTY_UNITS.values())


def test_the_release_id_names_the_watermark_day() -> None:
    assert RELEASE_ID == "soilgrids-v2.0/2020-06-02"
    assert LATEST_PIN_INSTANT.date() == RELEASE_DAY


# --------------------------------------------------------------------------------------------
# Watermark and registration
# --------------------------------------------------------------------------------------------


class _SessionThatRefusesEveryAttribute:
    """Stands in for the `AsyncSession` the resolver signature takes and must never touch."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the soil-properties watermark touched session.{name}")


async def test_watermark_is_the_latest_pinned_instant_and_reads_nothing() -> None:
    watermark = await read_soil_properties_source_watermark(
        _SessionThatRefusesEveryAttribute(),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        today=TODAY,
    )

    assert watermark.day == RELEASE_DAY
    assert watermark.instant == LATEST_PIN_INSTANT
    assert watermark.instant == max(pin.last_modified for pin in SOURCE_FILE_PINS)
    assert "sand_15-30cm_mean.vrt" in watermark.basis


async def test_watermark_refuses_a_day_before_the_release_exists() -> None:
    with pytest.raises(ValueError, match="cannot be available before"):
        await read_soil_properties_source_watermark(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            today=RELEASE_DAY - dt.timedelta(days=1),
        )


async def test_a_published_version_resolves_current_and_an_unwritten_one_is_owed() -> None:
    watermark = await read_soil_properties_source_watermark(None, None, today=TODAY)  # type: ignore[arg-type]
    exported = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)

    current = resolve_static_lane(
        watermark=watermark,
        newest_data_day=RELEASE_DAY,
        newest_data_instant=exported,
        newest_marker_day=None,
        today=TODAY,
    )
    owed = resolve_static_lane(
        watermark=watermark, newest_data_day=None, newest_data_instant=None, newest_marker_day=None, today=TODAY
    )

    assert current.state == "current"
    assert owed.version_day == RELEASE_DAY


def test_the_lane_is_registered_as_a_static_lookup_keyed_to_this_watermark() -> None:
    registration = LANE_REGISTRY[SOIL_PROPERTIES_STREAM]

    assert registration.nature == "static_lookup"
    assert registration.watermark is read_soil_properties_source_watermark
    assert registration.history_floor == RELEASE_DAY
    assert registration.publication_lag_days == 0
    assert registration.writer_ceiling is None
    assert registration.forecast_module is None


def test_registry_leaves_import_only_foundation_warehouse_and_their_own_package() -> None:
    """`lane_registry.py` imports `watermark.py`; a wider runtime import here closes the registry cycle.

    Module-level statements only: an `if TYPE_CHECKING:` block is an `ast.If`, so type-only imports pass.
    """
    for leaf in ("watermark.py", "products.py"):
        tree = ast.parse((_PACKAGE_ROOT / leaf).read_text(encoding="utf-8"))
        runtime_stray = [
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("agri_data_service")
            and not node.module.startswith(REGISTRY_IMPORT_ALLOWLIST)
        ]
        assert runtime_stray == [], f"{leaf} imports {runtime_stray} at runtime"


def test_the_package_init_re_exports_nothing() -> None:
    """Read the source, not the module: imported submodules become package attributes regardless."""
    body = ast.parse((_PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")).body

    assert len(body) == 1, "keep __init__ to its docstring"
    assert isinstance(body[0], ast.Expr), "keep __init__ to its docstring"
    assert isinstance(body[0].value, ast.Constant), "keep __init__ to its docstring"


# --------------------------------------------------------------------------------------------
# Writer contract and verbs
# --------------------------------------------------------------------------------------------


def _flags() -> frozenset[str]:
    return frozenset(option for action in forward.parser()._actions for option in action.option_strings)


def test_writer_contract_matches_contract_c10() -> None:
    contract = forward.WRITER_CONTRACT
    flags = _flags()

    assert contract.slug == SOIL_PROPERTIES_STREAM
    assert contract.identity_defect == NO_SUCH_DEFECT
    assert contract.geometry_defect == NO_SUCH_DEFECT
    assert contract.unconfigured_bbox == NOT_BBOX_BOUNDED
    assert "--bbox" in contract.flags_absent_on_purpose
    assert "--bbox" not in flags
    assert {"--max-days", "--run-id", "--retry-attempts", "--retry-base-seconds", "--retry-max-seconds"} <= flags


@pytest.mark.parametrize("operation", forward.OPERATIONS)
async def test_every_unbuilt_verb_refuses_by_name(operation: str) -> None:
    options = forward.parser().parse_args([operation])

    with pytest.raises(forward.SoilPropertiesOperationNotBuiltError, match=f"`{operation}` is not built yet"):
        await forward.run(options)


async def test_a_release_is_one_indivisible_day() -> None:
    options = forward.parser().parse_args(["capture", "--max-days", "2"])

    with pytest.raises(forward.SoilPropertiesConfigError, match="--max-days must be 1"):
        await forward.run(options)
