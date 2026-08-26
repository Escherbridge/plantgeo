"""The coarse-rung derivation: the transform, its guards, and its agreement with every lane's schema.

These tests are deliberately BLUNT about the two failure modes that actually happened while this
module was written, both of which are silent in production:

  * an all-null group summing to 0 rather than staying null (`frp_sum` -- a fabricated 0 MW reads
    as a measured absence of fire), and
  * a column carrying the `null` aggregate while its arrow field is `nullable=False`, which is
    invisible until a derived table is cast back to the storage contract inside the drain.

`test_every_lane_derivation_matches_its_own_schema` is the one that must never be deleted: it is
the only thing standing between a schema edit and a drain that fails thousands of lane-days in.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Final

import duckdb
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.parquet.lane_registry import registered_lane_slugs
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    DERIVED_ZOOM_TIERS,
    TIER_RESOLUTION_DEGREES,
    ColumnAggregation,
    GeometrySimplification,
    GridAggregation,
    HierarchicalDissolve,
    TierDerivation,
    TierDerivationError,
    TierPassthrough,
    derive_tier,
    register_tier_derivation,
    tier_derivation,
    validate_derivation_against_schema,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

DAY: Final = dt.date(2026, 8, 1)

# The four base cells `_grid_frame` holds, and what they must become. Named rather than inlined
# because ruff rejects a bare literal in a comparison, and because a reader checking the arithmetic
# wants the expected merge stated once rather than rediscovered at each assertion.
Z9_CELL_COUNT: Final = 3
MERGED_Z9_LONGITUDE: Final = -116.01
LONELY_Z9_LONGITUDE: Final = -116.02
MERGED_DETECTIONS: Final = 3
MERGED_FRP: Final = 30.0
ALL_DETECTIONS: Final = 10
Z0_LONGITUDE: Final = -120.0
Z0_LATITUDE: Final = 40.0
HUC10_GROUPS: Final = 2
HUC10_AREAS: Final = [30.0, 70.0]
HUC8_AREA: Final = 100.0
SYNTHETIC_ROWS: Final = 3


def _coordinate_sample(name: str, row: int) -> float:
    """Spread synthetic rows across real longitudes/latitudes so a grid lane has something to merge."""
    if "longi" in name:
        return -116.0 + row
    return 43.0 + row if "lati" in name else 1.0 + row


@pytest.fixture(name="spatial")
def _spatial() -> Iterator[duckdb.DuckDBPyConnection]:
    """One DuckDB session with the spatial extension, shared across a test's tiers."""
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("LOAD spatial")
    except duckdb.Error:  # pragma: no cover - only the first run on a machine pays this
        connection.execute("INSTALL spatial")
        connection.execute("LOAD spatial")
    yield connection
    connection.close()


def _grid_frame() -> pl.DataFrame:
    """Four cells: two that merge at 0.01 degrees, one that does not, and one far away."""
    return pl.DataFrame(
        {
            "cell_longitude": [-116.001, -116.004, -116.019, -115.5],
            "cell_latitude": [43.001, 43.004, 43.019, 43.5],
            "observed_day": [DAY] * 4,
            "detection_count": [1, 2, 3, 4],
            "frp_sum": [10.0, 20.0, None, 40.0],
        }
    )


@pytest.fixture(name="grid_stream")
def _grid_stream() -> str:
    """Register a throwaway grid lane; the slug is not a real lane, so no registry is disturbed."""
    stream = "test-grid-lane"
    register_tier_derivation(
        TierDerivation(
            stream=stream,
            strategy=GridAggregation(
                longitude_column="cell_longitude",
                latitude_column="cell_latitude",
                key_columns=("observed_day",),
                aggregations=(ColumnAggregation("detection_count", "sum"), ColumnAggregation("frp_sum", "sum")),
            ),
        )
    )
    return stream


def test_grid_tier_merges_only_cells_that_share_a_coarse_square(grid_stream: str) -> None:
    derived = derive_tier(_grid_frame(), stream=grid_stream, tier=9)
    assert derived.height == Z9_CELL_COUNT
    merged = derived.filter(pl.col("cell_longitude") == MERGED_Z9_LONGITUDE)
    assert merged["detection_count"][0] == MERGED_DETECTIONS  # 1 + 2, the two cells in one 0.01-degree square


def test_negative_longitudes_floor_away_from_zero(grid_stream: str) -> None:
    """floor(-116/5)*5 is -120, not -115. Every longitude in this warehouse's universe is negative."""
    derived = derive_tier(_grid_frame(), stream=grid_stream, tier=0)
    assert derived.height == 1
    assert derived["cell_longitude"][0] == Z0_LONGITUDE
    assert derived["cell_latitude"][0] == Z0_LATITUDE


def test_an_all_null_group_sums_to_null_and_never_to_zero(grid_stream: str) -> None:
    """Polars folds `sum()` over an all-null group to 0; SQL returns NULL. This lane needs NULL.

    `warehouse/schemas/fire_detections.py` requires `frp_sum` to be "NULL, never 0, when none did",
    because a fabricated 0 MW of fire-radiative power reads as a measured absence of fire rather
    than as an absent measurement.
    """
    derived = derive_tier(_grid_frame(), stream=grid_stream, tier=9)
    lonely = derived.filter(pl.col("cell_longitude") == LONELY_Z9_LONGITUDE)
    assert lonely.height == 1
    assert lonely["frp_sum"][0] is None
    # ...while a group that DOES hold values still sums them.
    assert derived.filter(pl.col("cell_longitude") == MERGED_Z9_LONGITUDE)["frp_sum"][0] == MERGED_FRP


def test_rows_without_coordinates_are_dropped_from_derived_tiers(grid_stream: str) -> None:
    """`water-gauges` carries nullable coordinates; a row with no position has no rung."""
    frame = _grid_frame().vstack(
        pl.DataFrame(
            {
                "cell_longitude": [None],
                "cell_latitude": [None],
                "observed_day": [DAY],
                "detection_count": [99],
                "frp_sum": [1.0],
            },
            schema=_grid_frame().schema,
        )
    )
    derived = derive_tier(frame, stream=grid_stream, tier=0)
    assert derived["detection_count"][0] == ALL_DETECTIONS  # 1+2+3+4, and NOT the unlocated 99


def test_an_unmentioned_column_is_refused_rather_than_dropped() -> None:
    """The safety property: a schema gaining a column must not silently vanish from coarse rungs."""
    stream = "test-incomplete-lane"
    register_tier_derivation(
        TierDerivation(
            stream=stream,
            strategy=GridAggregation(
                longitude_column="cell_longitude",
                latitude_column="cell_latitude",
                key_columns=("observed_day",),
                aggregations=(ColumnAggregation("detection_count", "sum"),),  # frp_sum unmentioned
            ),
        )
    )
    with pytest.raises(TierDerivationError, match="frp_sum"):
        derive_tier(_grid_frame(), stream=stream, tier=9)


def test_the_base_rung_is_refused_because_its_exporter_writes_it(grid_stream: str) -> None:
    with pytest.raises(TierDerivationError, match="base rung"):
        derive_tier(_grid_frame(), stream=grid_stream, tier=13)


def test_passthrough_returns_the_same_rows_at_every_rung() -> None:
    stream = "test-flat-lane"
    register_tier_derivation(TierDerivation(stream=stream, strategy=TierPassthrough()))
    frame = _grid_frame()
    for tier in DERIVED_ZOOM_TIERS:
        assert derive_tier(frame, stream=stream, tier=tier).equals(frame)


def _huc_frame(spatial: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Four HUC12 basins: two per HUC10, all four inside one HUC8."""

    def square(x: float, y: float) -> str:
        ring = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1), (x, y)]
        return "POLYGON((" + ",".join(f"{a} {b}" for a, b in ring) + "))"

    codes = ["170501010101", "170501010102", "170501010201", "170501010202"]
    boxes = [square(-117, 43), square(-116, 43), square(-117, 44), square(-116, 44)]
    wkb = [spatial.execute("SELECT ST_AsWKB(ST_GeomFromText(?))", [box]).fetchone()[0] for box in boxes]  # type: ignore[index]
    return pl.DataFrame(
        {"huc12": codes, "areasqkm": [10.0, 20.0, 30.0, 40.0], "feature_id": ["f1", "f2", "f3", "f4"], "geom": wkb}
    )


@pytest.fixture(name="huc_stream")
def _huc_stream() -> str:
    stream = "test-huc-lane"
    register_tier_derivation(
        TierDerivation(
            stream=stream,
            strategy=GeometrySimplification(
                geometry_column="geom",
                dissolve=HierarchicalDissolve(code_column="huc12", code_length_by_tier={9: 10, 5: 8, 0: 6}),
                aggregations=(ColumnAggregation("areasqkm", "sum"), ColumnAggregation("feature_id", "null")),
            ),
        )
    )
    return stream


def test_a_dissolve_rolls_children_into_the_parent_its_code_names(
    spatial: duckdb.DuckDBPyConnection, huc_stream: str
) -> None:
    frame = _huc_frame(spatial)
    z9 = derive_tier(frame, stream=huc_stream, tier=9, connection=spatial)
    assert z9.height == HUC10_GROUPS
    assert sorted(z9["huc12"].to_list()) == ["1705010101", "1705010102"]
    assert sorted(z9["areasqkm"].to_list()) == HUC10_AREAS

    z5 = derive_tier(frame, stream=huc_stream, tier=5, connection=spatial)
    assert z5.height == 1
    assert z5["huc12"][0] == "17050101"
    assert z5["areasqkm"][0] == HUC8_AREA  # every child's area, and none lost


def test_a_dissolve_nulls_the_identity_no_merged_row_can_carry(
    spatial: duckdb.DuckDBPyConnection, huc_stream: str
) -> None:
    """There is no single `feature_id` for a basin that is the union of others."""
    z5 = derive_tier(_huc_frame(spatial), stream=huc_stream, tier=5, connection=spatial)
    assert z5["feature_id"][0] is None
    assert z5.schema["feature_id"] == pl.String  # typed null, not an untyped one


def test_simplification_removes_vertices_and_preserves_the_column_order(
    spatial: duckdb.DuckDBPyConnection,
) -> None:
    stream = "test-simplify-lane"
    register_tier_derivation(TierDerivation(stream=stream, strategy=GeometrySimplification(geometry_column="geom")))
    dense = "POLYGON((" + ",".join(f"{-117 + i / 40} 43" for i in range(40)) + ",-117 44,-117 43))"
    wkb = spatial.execute("SELECT ST_AsWKB(ST_GeomFromText(?))", [dense]).fetchone()[0]  # type: ignore[index]
    frame = pl.DataFrame({"huc12": ["x"], "areasqkm": [1.0], "feature_id": ["f"], "geom": [wkb]})
    derived = derive_tier(frame, stream=stream, tier=5, connection=spatial)
    assert derived.columns == frame.columns
    before = spatial.execute("SELECT ST_NPoints(ST_GeomFromWKB(?))", [frame["geom"][0]]).fetchone()[0]  # type: ignore[index]
    after = spatial.execute("SELECT ST_NPoints(ST_GeomFromWKB(?))", [derived["geom"][0]]).fetchone()[0]  # type: ignore[index]
    assert after < before


def test_an_empty_day_derives_to_an_empty_frame_and_keeps_its_schema(
    spatial: duckdb.DuckDBPyConnection,
) -> None:
    """A zero-batch arrow result loses its schema; the derivation must not hand that on."""
    stream = "test-empty-lane"
    register_tier_derivation(TierDerivation(stream=stream, strategy=GeometrySimplification(geometry_column="geom")))
    frame = pl.DataFrame(
        {"huc12": [], "areasqkm": [], "feature_id": [], "geom": []},
        schema={"huc12": pl.String, "areasqkm": pl.Float64, "feature_id": pl.String, "geom": pl.Binary},
    )
    derived = derive_tier(frame, stream=stream, tier=0, connection=spatial)
    assert derived.height == 0
    assert derived.columns == frame.columns


def test_the_resolution_ladder_is_strictly_coarser_downward() -> None:
    """z9 finer than z5 finer than z0, or the rungs are not a ladder."""
    assert TIER_RESOLUTION_DEGREES[9] < TIER_RESOLUTION_DEGREES[5] < TIER_RESOLUTION_DEGREES[0]
    assert set(TIER_RESOLUTION_DEGREES) == set(DERIVED_ZOOM_TIERS)


@pytest.mark.parametrize("stream", registered_lane_slugs())
def test_every_lane_declares_a_derivation(stream: str) -> None:
    """A lane with a schema and no derivation would publish rungs nobody decided the contents of."""
    assert tier_derivation(stream).stream == stream


@pytest.mark.parametrize("stream", registered_lane_slugs())
def test_every_lane_derivation_matches_its_own_schema(stream: str) -> None:
    """DO NOT DELETE. The derivation and the schema are coupled and nothing else enforces it.

    Four real defects were found by hand on 2026-08-23 that this catches: columns carrying the
    `null` aggregate whose arrow field was `nullable=False`, which the drain would only have
    discovered thousands of lane-days in.
    """
    assert validate_derivation_against_schema(stream) == ()


@pytest.mark.parametrize("stream", registered_lane_slugs())
def test_every_lane_derives_a_real_row_at_every_rung(stream: str, spatial: duckdb.DuckDBPyConnection) -> None:
    """End to end per lane: derive a synthetic day, then cast the result back to the storage contract.

    The cast is the point. `write_partition` conforms every table to its stream's schema, so a rung
    that derives cleanly but no longer satisfies that schema fails at upload, not here.
    """
    schema = observed_stream_schema(stream)
    wkb = spatial.execute(
        "SELECT ST_AsWKB(ST_GeomFromText('POLYGON((-117 43,-117 44,-116 44,-116 43,-117 43))'))"
    ).fetchone()[0]  # type: ignore[index]

    def sample(field: pa.Field, row: int) -> object:
        """A type-appropriate value for one column, so every lane gets a derivable synthetic day."""
        # A predicate table rather than a return chain: arrow has no switchable type tag, and this
        # keeps the mapping readable as the one-line-per-type list it actually is.
        by_type = (
            (pa.types.is_binary, lambda: wkb),
            # A HUC code must stay twelve digits or a dissolve has no prefix to cut.
            (pa.types.is_string, lambda: "170501010101" if field.name == "huc12" else f"value-{row}"),
            (pa.types.is_floating, lambda: _coordinate_sample(field.name, row)),
            (pa.types.is_integer, lambda: row + 1),
            (pa.types.is_boolean, lambda: True),
            (pa.types.is_date, lambda: DAY),
            (pa.types.is_timestamp, lambda: dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)),
        )
        for matches, produce in by_type:
            if matches(field.type):
                return produce()
        return None

    rows = [{field.name: sample(field, row) for field in schema.arrow_schema} for row in range(SYNTHETIC_ROWS)]
    frame = pl.from_arrow(pa.Table.from_pylist(rows, schema=schema.arrow_schema))
    assert isinstance(frame, pl.DataFrame)
    for tier in DERIVED_ZOOM_TIERS:
        derived = derive_tier(frame, stream=stream, tier=tier, connection=spatial)
        assert derived.columns == frame.columns, f"{stream} z{tier} changed the column set"
        derived.to_arrow().cast(schema.arrow_schema)
