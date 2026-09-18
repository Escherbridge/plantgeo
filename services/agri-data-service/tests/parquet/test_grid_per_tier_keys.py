"""Per-rung key columns on `GridAggregation`, and what they must NOT change.

THE FIRST DUTY OF THIS FILE IS BYTE-IDENTITY. `key_columns_by_tier` defaults to `None`, and under
that default every one of the existing grid lanes must derive exactly the rows it derived before
the field existed. That is pinned two ways: a content digest computed at HEAD `ec172e88` BEFORE the
change and written here as a literal, and `_pre_change_grid_tier`, the pre-change algorithm copied
verbatim as an oracle. The digest catches drift anywhere in the path (aggregate tables, flooring,
column order, sort); the oracle says what drifted.

THE SECOND DUTY IS THE VOCABULARY LADDER: at each derived rung the grain is the tier's own tuple,
a key dropped at that rung is carried (`first`) or nulled (`null`) exactly as declared, any other
aggregate on a dropped key is refused at declaration time naming the column and the rung, and a
coarser rung's keys must SURVIVE the finer derived rung it may be chained from (z0 is derived from
the written z5 by the banded fold), so a key nulled at z9 cannot key z5.

THE THIRD DUTY IS THE LAWFULNESS OF `first`, checked against the real LANDFIRE LF2025 EVT legend.
`fixtures/lf2025-evt-hierarchy.csv` is the four hierarchy columns of
`.omc/research/runbook-20260915-vegetation-type/LF2025_EVT.csv` (sha256
5ccc130b250dbe709340afb2cc02ecdb3c4c374e2dba19cec91e2c2713e4963d, 309,948 bytes, 1,069 rows),
copied 2026-09-18. `first` on a dropped key is lawful ONLY where the finer vocabulary nests
functionally inside the coarser. Measured on that legend: `VALUE -> EVT_GP` nests; `EVT_GP ->
EVT_PHYS` does NOT (47 of 193 groups span two or more physiognomies, among them PNW groups 645
Western Red-cedar-Western Hemlock Forest, 632 Red Alder, 629 Western Oak Woodland, 609 Pacific
Coastal Scrub); `EVT_PHYS -> EVT_LF` does NOT (Riparian, Agricultural, Developed and Exotic
Tree-Shrub each span Tree/Shrub/Herb). The guard below pins the measured truth in both
directions, so a legend that changes -- whether it breaks further or is repaired -- fails the
sweep and is looked at before a schema trusts it.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.warehouse.parquet.schema import (
    ParquetStreamSchema,
    observed_stream_schema,
    register_stream_schema,
)
from agri_data_service.warehouse.parquet.tiers import (
    DERIVED_ZOOM_TIERS,
    MAX_DERIVATION_ROWS,
    TIER_RESOLUTION_DEGREES,
    Aggregation,
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    TierDerivationError,
    TierPassthrough,
    _aggregate_expression,  # private, imported on purpose: the pre-change oracle must use the same aggregate table
    derive_tier,
    floor_to_resolution,
    grid_key_columns,
    register_tier_derivation,
    tier_derivation,
    validate_derivation_against_schema,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.parquet.zoom import ZoomTier

BASE_ZOOM_TIER: Final = 13  # the base rung every ladder restates
DAY: Final = dt.date(2026, 8, 1)
NOON: Final = dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)
LATER: Final = dt.datetime(2026, 8, 1, 18, tzinfo=dt.UTC)

FIXTURE_PATH: Final = Path(__file__).resolve().parent / "fixtures" / "lf2025-evt-hierarchy.csv"
FIXTURE_SHA256: Final = "4281c1294bdaa3bd2e7c1c35580aef5e4ebf61c033d481d69d56a6c12dbc3dd5"
# The three counts INCLUDE the `-9999 Fill-NoData` sentinel row (its own group, physiognomy and
# lifeform); the real vocabularies are 192 / 19 / 9. Do not "fix" the fixture to drop it -- the
# reducer emits NoData as a class row, so the sentinel is a legitimate legend member here.
LEGEND_ROW_COUNT: Final = 1_069
LEGEND_GROUP_COUNT: Final = 193  # distinct EVT_GP codes; EVT_GP_N has 192 names, so code and name are not 1:1 either
LEGEND_PHYS_COUNT: Final = 20
LEGEND_LIFEFORM_COUNT: Final = 10

# --- Byte-identity: the golden digests, computed at HEAD ec172e88 before `key_columns_by_tier` existed ---

EVERY_AGGREGATE_STREAM: Final = "test-every-aggregate-lane"
FIRE_DETECTIONS_STREAM: Final = "fire-detections"
GOLDEN_DIGESTS: Final[Mapping[tuple[str, int], str]] = {
    (EVERY_AGGREGATE_STREAM, 9): "20d6e9cd03176b4a89c460776befd9b3e8879bee4b81db71f04c339d3a7148e8",
    (EVERY_AGGREGATE_STREAM, 5): "dad4370a1f06a6b740487a8c8dc73a88ec4fc807cd69d91c613e53f3be171d2c",
    (EVERY_AGGREGATE_STREAM, 0): "2031e389d1cd61c8a0ce238f1a94e75c00e24d8cb99b699b260f591594fcf2ff",
    (FIRE_DETECTIONS_STREAM, 9): "2dd50e916f73f4b564c12aac2f3482b9baa825a7b5bf19ff657caf3f288eb75c",
    (FIRE_DETECTIONS_STREAM, 5): "fe8cd8099e345cf1cf67f55e7cd79a0c6050e4e4d7489a57210d5ab089c654d8",
    (FIRE_DETECTIONS_STREAM, 0): "2c3c0f26663fddc9b9839f459da9b5ff6770887e735c2d563b10a422d8d57fb4",
}
GOLDEN_HEIGHTS: Final[Mapping[tuple[str, int], int]] = {
    (EVERY_AGGREGATE_STREAM, 9): 5,
    (EVERY_AGGREGATE_STREAM, 5): 4,
    (EVERY_AGGREGATE_STREAM, 0): 3,
    (FIRE_DETECTIONS_STREAM, 9): 4,
    (FIRE_DETECTIONS_STREAM, 5): 3,
    (FIRE_DETECTIONS_STREAM, 0): 2,
}

# One column per member of the closed `Aggregation` vocabulary, so the digest covers every Polars
# aggregate table entry, plus a null-coordinate row (dropped from derived rungs) and two key values.
EVERY_AGGREGATE_STRATEGY: Final = GridAggregation(
    longitude_column="cell_longitude",
    latitude_column="cell_latitude",
    key_columns=("signal_name", "observed_day"),
    aggregations=(
        ColumnAggregation("total", "sum"),
        ColumnAggregation("frp", "sum"),
        ColumnAggregation("temperature", "mean"),
        ColumnAggregation("lowest", "min"),
        ColumnAggregation("newest_at", "max"),
        ColumnAggregation("unit", "first"),
        ColumnAggregation("exposable", "all"),
        ColumnAggregation("seen", "any"),
        ColumnAggregation("external_id", "null"),
        ColumnAggregation("lineage", "sha256-lines"),
    ),
)


def _frame_digest(frame: pl.DataFrame) -> str:
    """Digest the schema and every row's Python repr: stable across Polars/Arrow serialisation changes."""
    digest = hashlib.sha256()
    digest.update(repr([(name, str(dtype)) for name, dtype in frame.schema.items()]).encode())
    for row in frame.rows():
        digest.update(repr(row).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _every_aggregate_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "cell_longitude": [-116.001, -116.004, -116.019, -116.5, -117.3, None, -116.002],
            "cell_latitude": [43.001, 43.004, 43.019, 43.5, 47.7, 43.0, 43.003],
            "signal_name": ["ndvi", "ndvi", "ndvi", "ndvi", "ndvi", "ndvi", "lst"],
            "observed_day": [DAY] * 7,
            "total": [1, 2, 3, 4, 5, 99, 7],
            "frp": [10.0, None, None, 40.0, 50.0, 1.0, 70.0],
            "temperature": [10.0, 12.0, 14.0, 16.0, 18.0, 0.0, 20.0],
            "lowest": [3, 1, 2, 9, 8, 0, 4],
            "newest_at": [NOON, LATER, NOON, LATER, NOON, NOON, LATER],
            "unit": ["index", "index", "index", "index", "index", "index", "kelvin"],
            "exposable": [True, None, True, False, True, True, True],
            "seen": [False, None, False, True, False, False, True],
            "external_id": ["a", "b", "c", "d", "e", "f", "g"],
            "lineage": ["x1", "x2", "x3", "x4", "x5", "x6", "x7"],
        },
        schema_overrides={"total": pl.Int64, "lowest": pl.Int32},
    )


def _fire_detections_frame() -> pl.DataFrame:
    """A real registered lane, on its real arrow schema: five cells, two of which share a z9 square."""
    empty = pl.from_arrow(observed_stream_schema(FIRE_DETECTIONS_STREAM).arrow_schema.empty_table())
    assert isinstance(empty, pl.DataFrame)
    rows = [
        (-116.001, 43.001, 1, 10.0, 1, 1, NOON),
        (-116.004, 43.004, 2, None, 0, 1, LATER),
        (-116.019, 43.019, 3, 30.0, 1, 0, NOON),
        (-120.5, 45.5, 4, None, 0, 0, NOON),
        (-124.9, 48.9, 5, 5.5, 1, 1, LATER),
    ]
    return pl.DataFrame(
        [
            {
                "cell_longitude": lon,
                "cell_latitude": lat,
                "observed_day": DAY,
                "detection_count": count,
                "frp_sum": frp,
                "frp_observation_count": frp_count,
                "high_confidence_detection_count": high,
                "newest_observed_at": newest,
            }
            for lon, lat, count, frp, frp_count, high, newest in rows
        ],
        schema=empty.schema,
    )


def _pre_change_grid_tier(frame: pl.DataFrame, strategy: GridAggregation, *, tier: ZoomTier) -> pl.DataFrame:
    """`_derive_grid_tier` exactly as it stood before per-tier keys: the oracle for the default path."""
    resolution = TIER_RESOLUTION_DEGREES[tier]
    coordinates = (strategy.longitude_column, strategy.latitude_column)
    grain = (*coordinates, *strategy.key_columns)
    located = frame.drop_nulls(list(coordinates))
    coarsened = located.with_columns(
        floor_to_resolution(pl.col(strategy.longitude_column), resolution).alias(strategy.longitude_column),
        floor_to_resolution(pl.col(strategy.latitude_column), resolution).alias(strategy.latitude_column),
    )
    aggregated = coarsened.group_by(grain).agg(
        *(_aggregate_expression(spec, stream="oracle") for spec in strategy.aggregations)
    )
    return aggregated.select(frame.columns).sort(grain)


@pytest.fixture(name="every_aggregate_stream")
def _every_aggregate_stream() -> str:
    register_tier_derivation(TierDerivation(stream=EVERY_AGGREGATE_STREAM, strategy=EVERY_AGGREGATE_STRATEGY))
    return EVERY_AGGREGATE_STREAM


@pytest.mark.parametrize("tier", DERIVED_ZOOM_TIERS)
def test_a_synthetic_lane_without_per_tier_keys_is_byte_identical_to_the_golden_digest(
    every_aggregate_stream: str, tier: ZoomTier
) -> None:
    derived = derive_tier(_every_aggregate_frame(), stream=every_aggregate_stream, tier=tier)

    assert derived.height == GOLDEN_HEIGHTS[every_aggregate_stream, tier]
    assert _frame_digest(derived) == GOLDEN_DIGESTS[every_aggregate_stream, tier]
    assert derived.equals(_pre_change_grid_tier(_every_aggregate_frame(), EVERY_AGGREGATE_STRATEGY, tier=tier))


@pytest.mark.parametrize("tier", DERIVED_ZOOM_TIERS)
def test_a_real_lane_without_per_tier_keys_is_byte_identical_to_the_golden_digest(tier: ZoomTier) -> None:
    derived = derive_tier(_fire_detections_frame(), stream=FIRE_DETECTIONS_STREAM, tier=tier)

    strategy = tier_derivation(FIRE_DETECTIONS_STREAM).strategy
    assert isinstance(strategy, GridAggregation)
    assert strategy.key_columns_by_tier is None, "an existing lane must not have grown a ladder"
    assert derived.height == GOLDEN_HEIGHTS[FIRE_DETECTIONS_STREAM, tier]
    assert _frame_digest(derived) == GOLDEN_DIGESTS[FIRE_DETECTIONS_STREAM, tier]
    assert derived.equals(_pre_change_grid_tier(_fire_detections_frame(), strategy, tier=tier))


def test_the_default_resolves_key_columns_at_every_rung() -> None:
    for tier in (*DERIVED_ZOOM_TIERS, 13):
        assert grid_key_columns(EVERY_AGGREGATE_STRATEGY, tier) == ("signal_name", "observed_day")


# --- The vocabulary ladder: a three-level functional hierarchy, shaped like the vegetation-type lane ---

VEGETATION_LIKE_STREAM: Final = "test-vocabulary-ladder-lane"
# THE JOINT LADDER: each coarser tuple is a subset of the finer one, so every key survives the rung
# above it without relying on `first`, and the finer codes are simply nulled. This is the ladder the
# real legend needs (see the guard tests at the bottom) and the one 1C should declare.
VEGETATION_LIKE_LADDER: Final[Mapping[ZoomTier, tuple[str, ...]]] = {
    13: ("evt_code",),
    9: ("evt_group_code", "evt_phys", "evt_lifeform"),
    5: ("evt_phys", "evt_lifeform"),
    0: ("evt_lifeform",),
}
# The single-column ladder the plan first proposed. Lawful ONLY under `first` on a nesting legend;
# used here for the refusal and carry tests.
SINGLE_COLUMN_LADDER: Final[Mapping[ZoomTier, tuple[str, ...]]] = {
    13: ("evt_code",),
    9: ("evt_group_code",),
    5: ("evt_phys",),
    0: ("evt_lifeform",),
}
# Every code has exactly one group, every group one physiognomy, every physiognomy one lifeform:
# the nesting that makes `first` lawful on the single-column ladder for THIS fixture.
LEGEND_FIXTURE: Final[Mapping[int, tuple[int, str, str]]] = {
    7001: (610, "Conifer", "Tree"),
    7002: (610, "Conifer", "Tree"),
    7003: (611, "Hardwood", "Tree"),
    7101: (620, "Shrubland", "Shrub"),
    7102: (620, "Shrubland", "Shrub"),
    7201: (630, "Grassland", "Herb"),
}
SQUARES_AT_EVERY_RUNG: Final = 2  # four base cells share one square, the fifth sits alone
DISTINCT_STRATEGIES: Final = 3


def _vegetation_like_strategy(
    *,
    ladder: Mapping[ZoomTier, tuple[str, ...]] = VEGETATION_LIKE_LADDER,
    group_how: Aggregation = "null",
    phys_how: Aggregation = "null",
    lifeform_how: Aggregation | None = None,
) -> GridAggregation:
    """The lane's strategy; `lifeform_how` defaults to absent: on the joint ladder the lifeform is never dropped."""
    lifeform = () if lifeform_how is None else (ColumnAggregation("evt_lifeform", lifeform_how),)
    return GridAggregation(
        longitude_column="cell_lon",
        latitude_column="cell_lat",
        key_columns=("evt_code",),
        key_columns_by_tier=ladder,
        aggregations=(
            ColumnAggregation("class_system", "first"),
            ColumnAggregation("evt_code", "null"),
            ColumnAggregation("evt_group_code", group_how),
            ColumnAggregation("evt_phys", phys_how),
            *lifeform,
            ColumnAggregation("pixel_count", "sum"),
            ColumnAggregation("release_day", "first"),
        ),
    )


def _vegetation_like_frame() -> pl.DataFrame:
    """Six classes spread over four 0.005-degree cells that share one z9 square and one more far away."""
    cells = [(-122.003, 47.003), (-122.006, 47.003), (-122.003, 47.006), (-122.006, 47.006), (-118.5, 44.3)]
    rows = []
    pixels = 1
    for lon, lat in cells:
        for code, (group, phys, lifeform) in LEGEND_FIXTURE.items():
            rows.append(
                {
                    "cell_lon": lon,
                    "cell_lat": lat,
                    "class_system": "LANDFIRE_EVT_LF2025",
                    "evt_code": code,
                    "evt_group_code": group,
                    "evt_phys": phys,
                    "evt_lifeform": lifeform,
                    "pixel_count": pixels,
                    "release_day": DAY,
                }
            )
            pixels += 1
    frame = pl.DataFrame(
        rows, schema_overrides={"evt_code": pl.Int32, "evt_group_code": pl.Int32, "pixel_count": pl.Int64}
    )
    assert frame["pixel_count"].sum() == sum(range(1, len(cells) * len(LEGEND_FIXTURE) + 1))
    return frame


@pytest.fixture(name="vegetation_like_stream")
def _vegetation_like_stream() -> str:
    register_tier_derivation(TierDerivation(stream=VEGETATION_LIKE_STREAM, strategy=_vegetation_like_strategy()))
    return VEGETATION_LIKE_STREAM


def test_each_rung_keys_on_its_own_tuple() -> None:
    strategy = _vegetation_like_strategy()
    assert grid_key_columns(strategy, 13) == ("evt_code",)
    assert grid_key_columns(strategy, 9) == ("evt_group_code", "evt_phys", "evt_lifeform")
    assert grid_key_columns(strategy, 5) == ("evt_phys", "evt_lifeform")
    assert grid_key_columns(strategy, 0) == ("evt_lifeform",)


def test_per_tier_keys_select_the_declared_grain_at_each_rung(vegetation_like_stream: str) -> None:
    base = _vegetation_like_frame()
    total = base["pixel_count"].sum()

    z9 = derive_tier(base, stream=vegetation_like_stream, tier=9)
    z5 = derive_tier(base, stream=vegetation_like_stream, tier=5)
    z0 = derive_tier(base, stream=vegetation_like_stream, tier=0)

    for rung, tier in ((z9, 9), (z5, 5), (z0, 0)):
        keys = grid_key_columns(_vegetation_like_strategy(), tier)
        assert rung.columns == base.columns, "the output schema is the input schema at every rung"
        assert rung.select("cell_lon", "cell_lat", *keys).is_unique().all(), f"{keys} is the grain: unique per cell"
        assert rung["pixel_count"].sum() == total, "counts are conserved along the ladder"
    # Four base cells fall in one 0.01-degree square, the fifth in another; the fixture nests, so the
    # joint tuples have exactly as many distinct values as their leading column: 3 groups + 3 groups.
    assert z9.height == SQUARES_AT_EVERY_RUNG * len({group for group, _, _ in LEGEND_FIXTURE.values()})
    assert z5.height == SQUARES_AT_EVERY_RUNG * len({phys for _, phys, _ in LEGEND_FIXTURE.values()})
    assert z0.height == SQUARES_AT_EVERY_RUNG * len({lifeform for _, _, lifeform in LEGEND_FIXTURE.values()})


def test_dropped_keys_are_carried_or_nulled_exactly_as_declared(vegetation_like_stream: str) -> None:
    base = _vegetation_like_frame()

    z9 = derive_tier(base, stream=vegetation_like_stream, tier=9)
    z5 = derive_tier(base, stream=vegetation_like_stream, tier=5)
    z0 = derive_tier(base, stream=vegetation_like_stream, tier=0)

    # `null`: a vocabulary finer than this rung's key, typed as the base column.
    assert z9["evt_code"].is_null().all()
    assert z9["evt_code"].dtype == base["evt_code"].dtype
    assert z5["evt_group_code"].is_null().all()
    assert z0["evt_phys"].is_null().all()
    # A key column is never null at any rung it keys.
    for column in ("evt_group_code", "evt_phys", "evt_lifeform"):
        assert z9[column].is_not_null().all()
    assert z5["evt_phys"].is_not_null().all()
    assert z5["evt_lifeform"].is_not_null().all()
    assert z0["evt_lifeform"].is_not_null().all()
    # Joint keys keep the real triples, not an arbitrary member of each.
    assert set(z9.select("evt_group_code", "evt_phys", "evt_lifeform").iter_rows()) == set(LEGEND_FIXTURE.values())
    assert z9["class_system"].unique().to_list() == ["LANDFIRE_EVT_LF2025"]


def test_first_carries_a_dropped_key_exactly_when_the_vocabulary_nests() -> None:
    """The single-column ladder under `first`: lawful on this nesting fixture, and the chain rule admits it."""
    stream = "test-single-column-ladder-lane"
    register_tier_derivation(
        TierDerivation(
            stream=stream,
            strategy=_vegetation_like_strategy(ladder=SINGLE_COLUMN_LADDER, phys_how="first", lifeform_how="first"),
        )
    )
    base = _vegetation_like_frame()
    z9 = derive_tier(base, stream=stream, tier=9)
    z5 = derive_tier(base, stream=stream, tier=5)

    phys_of_group = {group: phys for group, phys, _ in LEGEND_FIXTURE.values()}
    for group, phys in z9.select("evt_group_code", "evt_phys").iter_rows():
        assert phys == phys_of_group[group]
    lifeform_of_phys = {phys: lifeform for _, phys, lifeform in LEGEND_FIXTURE.values()}
    for phys, lifeform in z5.select("evt_phys", "evt_lifeform").iter_rows():
        assert lifeform == lifeform_of_phys[phys]
    assert z5["evt_group_code"].is_null().all()


def test_each_rung_derived_from_the_rung_above_equals_the_rung_derived_from_base(vegetation_like_stream: str) -> None:
    """Associativity along the ladder: what the banded fold (1B) relies on when it derives z0 from the z5 rung."""
    base = _vegetation_like_frame()

    z9 = derive_tier(base, stream=vegetation_like_stream, tier=9)
    z5 = derive_tier(base, stream=vegetation_like_stream, tier=5)
    z0 = derive_tier(base, stream=vegetation_like_stream, tier=0)

    assert derive_tier(z9, stream=vegetation_like_stream, tier=5).equals(z5)
    assert derive_tier(z5, stream=vegetation_like_stream, tier=0).equals(z0)
    assert _frame_digest(derive_tier(z5, stream=vegetation_like_stream, tier=0)) == _frame_digest(z0)


def test_flooring_to_the_cell_origin_is_unchanged_under_per_tier_keys(vegetation_like_stream: str) -> None:
    """floor(v / r) * r, written back as the ORIGIN, at every rung, on the negative longitudes this warehouse has."""
    base = _vegetation_like_frame()
    for tier in DERIVED_ZOOM_TIERS:
        resolution = TIER_RESOLUTION_DEGREES[tier]
        derived = derive_tier(base, stream=vegetation_like_stream, tier=tier)
        expected = base.select(
            floor_to_resolution(pl.col("cell_lon"), resolution).alias("cell_lon"),
            floor_to_resolution(pl.col("cell_lat"), resolution).alias("cell_lat"),
        ).unique()
        assert set(derived.select("cell_lon", "cell_lat").unique().iter_rows()) == set(expected.iter_rows())
    z0 = derive_tier(base, stream=vegetation_like_stream, tier=0)
    assert set(z0["cell_lon"].to_list()) == {-125.0, -120.0}, "floor(-122.003/5)*5 is -125, not -120"
    assert set(z0["cell_lat"].to_list()) == {45.0, 40.0}


# --- Refusals, all at declaration time ---


def test_a_dropped_key_with_an_arithmetic_aggregate_is_refused_naming_column_and_rung() -> None:
    with pytest.raises(TierDerivationError, match=r"'evt_group_code'.*z5.*'sum'") as raised:
        _vegetation_like_strategy(group_how="sum")  # group is a z9 key, dropped at z5 and z0
    assert "first" in str(raised.value)
    assert "null" in str(raised.value)


def test_a_dropped_key_with_a_mean_aggregate_is_refused_too() -> None:
    with pytest.raises(TierDerivationError, match=r"'evt_phys'.*z0.*'mean'"):
        _vegetation_like_strategy(phys_how="mean")  # phys keys z9 and z5, dropped at z0


def test_a_dropped_key_with_no_aggregate_at_all_is_refused() -> None:
    """A tier tuple naming a column no other rung mentions is caught here, before any frame is seen."""
    ladder = {**SINGLE_COLUMN_LADDER, 9: ("evt_grp_code",)}  # typo
    with pytest.raises(TierDerivationError, match=r"'evt_grp_code'.*z5.*None"):
        _vegetation_like_strategy(ladder=ladder, phys_how="first", lifeform_how="first")


def test_a_key_nulled_at_the_finer_rung_cannot_key_the_coarser_one() -> None:
    """CHAIN SAFETY. z0 from the written z5 would group on an all-null `evt_lifeform`: two rows, no exception."""
    with pytest.raises(TierDerivationError, match=r"'evt_lifeform' keys z0 but does not survive z5.*'null'"):
        _vegetation_like_strategy(ladder=SINGLE_COLUMN_LADDER, phys_how="first", lifeform_how="null")
    with pytest.raises(TierDerivationError, match=r"'evt_phys' keys z5 but does not survive z9.*'null'"):
        _vegetation_like_strategy(ladder=SINGLE_COLUMN_LADDER, phys_how="null", lifeform_how="first")


def test_the_joint_ladder_passes_the_chain_rule_without_first() -> None:
    """Each coarser tuple is a subset of the finer one, so every key survives by being a key."""
    strategy = _vegetation_like_strategy()
    for finer, coarser in pairwise(DERIVED_ZOOM_TIERS):
        assert set(grid_key_columns(strategy, coarser)) <= set(grid_key_columns(strategy, finer))


def test_a_ladder_missing_a_derived_rung_is_refused() -> None:
    incomplete = {tier: keys for tier, keys in VEGETATION_LIKE_LADDER.items() if tier != 0}
    with pytest.raises(TierDerivationError, match=r"z\[0\]"):
        _vegetation_like_strategy(ladder=incomplete)


def test_a_base_entry_must_restate_key_columns() -> None:
    contradicted = {**VEGETATION_LIKE_LADDER, 13: ("evt_group_code",)}
    with pytest.raises(TierDerivationError, match=r"key_columns_by_tier\[13\]"):
        _vegetation_like_strategy(ladder=contradicted)


def test_a_ladder_may_omit_the_base_entry() -> None:
    derived_only = {tier: keys for tier, keys in VEGETATION_LIKE_LADDER.items() if tier != BASE_ZOOM_TIER}
    assert grid_key_columns(_vegetation_like_strategy(ladder=derived_only), 13) == ("evt_code",)


def test_the_ladder_is_copied_so_a_later_mutation_of_the_callers_dict_cannot_bypass_the_checks() -> None:
    callers = dict(VEGETATION_LIKE_LADDER)
    strategy = _vegetation_like_strategy(ladder=callers)

    callers[5] = ("pixel_count",)

    assert grid_key_columns(strategy, 5) == ("evt_phys", "evt_lifeform")
    assert strategy.key_columns_by_tier is not None
    with pytest.raises(TypeError):
        strategy.key_columns_by_tier[5] = ("pixel_count",)  # type: ignore[index] - proving the copy is read-only


def test_a_laddered_strategy_is_hashable_and_equal_by_value() -> None:
    """`register_tier_derivation` compares derivations by value; a frozen dataclass must also hash."""
    one = _vegetation_like_strategy(ladder=dict(VEGETATION_LIKE_LADDER))
    same = _vegetation_like_strategy(ladder={tier: VEGETATION_LIKE_LADDER[tier] for tier in (0, 5, 9, 13)})
    other = _vegetation_like_strategy(ladder=SINGLE_COLUMN_LADDER, phys_how="first", lifeform_how="first")

    assert one == same
    assert hash(one) == hash(same)
    assert one != other
    assert len({one, same, other, EVERY_AGGREGATE_STRATEGY}) == DISTINCT_STRATEGIES
    assert TierDerivation(stream="x-lane", strategy=one) == TierDerivation(stream="x-lane", strategy=same)


def test_a_column_that_is_both_key_and_aggregated_is_refused_when_there_is_no_ladder() -> None:
    """Without a ladder a key is never dropped, so the aggregate would silently never apply."""
    with pytest.raises(TierDerivationError, match=r"\['observed_day'\] are both in `key_columns` and in `aggreg"):
        GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            key_columns=("observed_day",),
            aggregations=(ColumnAggregation("observed_day", "first"), ColumnAggregation("total", "sum")),
        )


MISSPELLED_STREAM: Final = "test-consistently-misspelled-lane"
MISSPELLED_STRATEGY: Final = GridAggregation(
    longitude_column="cell_lon",
    latitude_column="cell_lat",
    key_columns=("evt_code",),
    # The one typo declaration cannot catch: a column spelled consistently wrong at every rung.
    key_columns_by_tier={9: ("evt_grp_code",), 5: ("evt_grp_code",), 0: ("evt_grp_code",)},
    aggregations=(
        ColumnAggregation("class_system", "first"),
        ColumnAggregation("evt_code", "null"),
        ColumnAggregation("evt_group_code", "null"),
        ColumnAggregation("evt_phys", "null"),
        ColumnAggregation("evt_lifeform", "first"),
        ColumnAggregation("pixel_count", "sum"),
        ColumnAggregation("release_day", "first"),
    ),
)


def _vegetation_like_arrow_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("cell_lon", pa.float64(), nullable=False),
            pa.field("cell_lat", pa.float64(), nullable=False),
            pa.field("class_system", pa.string(), nullable=False),
            pa.field("evt_code", pa.int32(), nullable=True),
            pa.field("evt_group_code", pa.int32(), nullable=True),
            pa.field("evt_phys", pa.string(), nullable=True),
            pa.field("evt_lifeform", pa.string(), nullable=False),
            pa.field("pixel_count", pa.int64(), nullable=False),
            pa.field("release_day", pa.date32(), nullable=False),
        ]
    )


def test_a_tier_key_the_schema_lacks_is_reported_at_registration() -> None:
    """The plan's registration-time check: `validate_derivation_against_schema` runs over every lane in the sweep."""
    register_stream_schema(
        ParquetStreamSchema(
            name=MISSPELLED_STREAM, arrow_schema=_vegetation_like_arrow_schema(), sort_columns=("cell_lon", "cell_lat")
        )
    )
    register_tier_derivation(TierDerivation(stream=MISSPELLED_STREAM, strategy=MISSPELLED_STRATEGY))

    problems = validate_derivation_against_schema(MISSPELLED_STREAM)

    assert any("keys a rung on column 'evt_grp_code', which its schema lacks" in problem for problem in problems)
    assert not any("'evt_group_code'" in problem and "neither grain nor aggregated" in problem for problem in problems)


def test_a_base_key_the_schema_lacks_is_reported_at_registration_too() -> None:
    """Pre-existing gap closed alongside: `key_columns` themselves were never checked against the schema."""
    stream = "test-misspelled-base-key-lane"
    register_stream_schema(
        ParquetStreamSchema(name=stream, arrow_schema=_vegetation_like_arrow_schema(), sort_columns=("cell_lon",))
    )
    carried = ("class_system", "evt_code", "evt_group_code", "evt_phys", "evt_lifeform", "release_day")
    register_tier_derivation(
        TierDerivation(
            stream=stream,
            strategy=GridAggregation(
                longitude_column="cell_lon",
                latitude_column="cell_lat",
                key_columns=("evt_cod",),  # typo, no ladder
                aggregations=(
                    *(ColumnAggregation(column, "first") for column in carried),
                    ColumnAggregation("pixel_count", "sum"),
                ),
            ),
        )
    )

    problems = validate_derivation_against_schema(stream)

    assert any("keys a rung on column 'evt_cod', which its schema lacks" in problem for problem in problems)


def test_a_tier_key_the_base_table_lacks_is_refused_at_derivation() -> None:
    """The later, derivation-time guard for the same typo, for a lane whose schema was never validated."""
    register_tier_derivation(TierDerivation(stream=MISSPELLED_STREAM, strategy=MISSPELLED_STRATEGY))
    with pytest.raises(TierDerivationError, match=r"key column\(s\) \['evt_grp_code'\]"):
        derive_tier(_vegetation_like_frame(), stream=MISSPELLED_STREAM, tier=9)


# --- MAX_DERIVATION_ROWS: the per-call bound is unchanged ---

EXPECTED_ROW_CAP: Final = 5_000_000


def test_max_derivation_rows_still_refuses_at_the_same_threshold() -> None:
    assert MAX_DERIVATION_ROWS == EXPECTED_ROW_CAP
    stream = "test-passthrough-row-cap-lane"
    register_tier_derivation(TierDerivation(stream=stream, strategy=TierPassthrough()))

    at_the_cap = pl.DataFrame({"value": pl.zeros(MAX_DERIVATION_ROWS, dtype=pl.Int8, eager=True)})
    assert derive_tier(at_the_cap, stream=stream, tier=9).height == MAX_DERIVATION_ROWS

    over_the_cap = pl.DataFrame({"value": pl.zeros(MAX_DERIVATION_ROWS + 1, dtype=pl.Int8, eager=True)})
    with pytest.raises(TierDerivationError, match=r"5,000,001 rows exceeds MAX_DERIVATION_ROWS \(5,000,000\)"):
        derive_tier(over_the_cap, stream=stream, tier=9)


# --- The lawfulness of `first`, against the real LF2025 legend ---

# Measured 2026-09-18 on the fixture. Pinned so a changed legend is noticed, not absorbed.
GROUPS_SPANNING_SEVERAL_PHYSIOGNOMIES: Final = frozenset(
    {
        601,
        602,
        607,
        609,
        610,
        617,
        618,
        624,
        628,
        629,
        632,
        639,
        645,
        646,
        651,
        654,
        658,
        663,
        664,
        665,
        670,
        672,
        673,
        674,
        675,
        676,
        683,
        684,
        685,
        686,
        688,
        689,
        690,
        691,
        693,
        696,
        707,
        731,
        740,
        758,
        775,
        781,
        785,
        802,
        806,
        809,
        826,
    }
)
PHYSIOGNOMIES_SPANNING_SEVERAL_LIFEFORMS: Final[Mapping[str, frozenset[str]]] = {
    "Riparian": frozenset({"Herb", "Shrub", "Tree"}),
    "Agricultural": frozenset({"Agriculture", "Herb", "Shrub", "Tree"}),
    "Developed": frozenset({"Herb", "Shrub", "Tree"}),
    "Exotic Tree-Shrub": frozenset({"Shrub", "Tree"}),
}


def _legend_rows() -> list[dict[str, str]]:
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == FIXTURE_SHA256, "the fixture was edited"
    with FIXTURE_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == LEGEND_ROW_COUNT
    return rows


def _children_by_parent(rows: list[dict[str, str]], child: str, parent: str) -> dict[str, set[str]]:
    """For each `child` value, the set of `parent` values it maps to; nesting is functional when every set has one."""
    parents: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        parents[row[child]].add(row[parent])
    return dict(parents)


def _violations(mapping: dict[str, set[str]]) -> dict[str, set[str]]:
    return {child: parents for child, parents in mapping.items() if len(parents) > 1}


def test_every_evt_code_has_exactly_one_group_so_first_is_lawful_for_the_group_at_z13() -> None:
    rows = _legend_rows()
    code_to_group = _children_by_parent(rows, "VALUE", "EVT_GP")
    assert len(code_to_group) == LEGEND_ROW_COUNT, "VALUE is the legend's key"
    assert _violations(code_to_group) == {}
    assert len({row["EVT_GP"] for row in rows}) == LEGEND_GROUP_COUNT


def test_groups_do_not_nest_in_physiognomies_so_first_is_not_lawful_for_evt_phys_at_z9() -> None:
    """47 of 193 groups span two or more physiognomies -- among them core PNW groups (645, 632, 629, 609).

    The vegetation-type schema (1C) must therefore NOT declare `evt_phys` as `first` at a rung keyed
    on `evt_group_code` alone: key jointly on `("evt_group_code", "evt_phys")` or null it.
    """
    violations = _violations(_children_by_parent(_legend_rows(), "EVT_GP", "EVT_PHYS"))
    assert {int(group) for group in violations} == GROUPS_SPANNING_SEVERAL_PHYSIOGNOMIES
    assert all(len(physiognomies) > 1 for physiognomies in violations.values())


def test_physiognomies_do_not_nest_in_lifeforms_so_first_is_not_lawful_for_evt_lifeform_at_z5() -> None:
    """Riparian, Agricultural, Developed and Exotic Tree-Shrub each span Tree/Shrub/Herb.

    The vegetation-type schema (1C) must therefore NOT declare `evt_lifeform` as `first` at a rung
    keyed on `evt_phys` alone: key jointly on `("evt_phys", "evt_lifeform")` or null it -- and the
    spec's `evt_lifeform` column is non-nullable, so joint keys are the only honest choice.
    """
    rows = _legend_rows()
    violations = _violations(_children_by_parent(rows, "EVT_PHYS", "EVT_LF"))
    assert {phys: frozenset(lifeforms) for phys, lifeforms in violations.items()} == dict(
        PHYSIOGNOMIES_SPANNING_SEVERAL_LIFEFORMS
    )
    assert len({row["EVT_PHYS"] for row in rows}) == LEGEND_PHYS_COUNT
    assert len({row["EVT_LF"] for row in rows}) == LEGEND_LIFEFORM_COUNT


def test_the_platform_expresses_the_joint_key_ladder_the_legend_actually_needs() -> None:
    """The honest ladder is expressible today: the tuples widen where the vocabulary fails to nest."""
    joint = GridAggregation(
        longitude_column="cell_lon",
        latitude_column="cell_lat",
        key_columns=("evt_code",),
        key_columns_by_tier={
            13: ("evt_code",),
            9: ("evt_group_code", "evt_phys", "evt_lifeform"),
            5: ("evt_phys", "evt_lifeform"),
            0: ("evt_lifeform",),
        },
        aggregations=(
            ColumnAggregation("class_system", "first"),
            ColumnAggregation("evt_code", "null"),
            ColumnAggregation("evt_group_code", "null"),
            ColumnAggregation("evt_phys", "null"),
            ColumnAggregation("pixel_count", "sum"),
            ColumnAggregation("release_day", "first"),
        ),
    )
    assert grid_key_columns(joint, 9) == ("evt_group_code", "evt_phys", "evt_lifeform")
    assert grid_key_columns(joint, 0) == ("evt_lifeform",)
