"""The coarse-rung vocabulary of a `kind=forecast` lane: the lattice, the strategies, the derivation.

Layer L3, spec FR-4. Split out of `forecast_lane_bootstrap.py` when that module passed the size
ceiling: the two halves answer different questions. This one is PURE — a frame goes in and a frame
comes out, nothing here opens a bucket or reads a clock — which is what lets the derivation be
tested against a literal table. Rationale lives in `AGENTS-forecast-lanes.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import polars as pl

from plantgeo_ml_service.foundation.lattice import (
    MICRO_DEGREES_PER_DEGREE,
    TIER_PITCH_MICRO,
    LatticeAxis,
    axis_origin_micro,
    tier_pitch_micro,
)
from plantgeo_ml_service.warehouse.lanes import forecast_root_kind
from plantgeo_ml_service.warehouse.streams import (
    FIRE_DETECTIONS_STREAM,
    FIRE_RISK_STREAM,
    FORECAST_PROVENANCE_COLUMNS,
    SIGNAL_STREAM,
    VEGETATION_STREAM,
    stream_schema,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from plantgeo_ml_service.foundation.parquet_paths import ZoomTier


TIER_PITCH_MICRO_DEGREES: Final[Mapping[ZoomTier, int]] = TIER_PITCH_MICRO

ColumnMerge = Literal["sum", "mean", "max", "first", "all", "null"]


class ForecastLaneError(RuntimeError):
    """Raised when a forecast lane cannot be derived, bootstrapped or indexed as asked."""


@dataclass(frozen=True, slots=True)
class ColumnAggregation:
    """How one observed column merges when several base cells fall in one coarse cell."""

    column: str
    merge: ColumnMerge


@dataclass(frozen=True, slots=True)
class RowSelect:
    """Pick one WHOLE base row per coarse cell, ordered by these columns, rather than merging fields.

    The strategy a lane declares when its published value is a decision about a place rather than a
    quantity of it: a fire-risk cell's coarse summary is its worst constituent cell, and every
    column of that row — the score, the stratum, the refusal reason — must come from the SAME base
    row, or the coarse rung would report a stratum that never carried that score.
    """

    order_by: tuple[str, ...]
    descending: tuple[bool, ...]

    def __post_init__(self) -> None:
        if not self.order_by or len(self.order_by) != len(self.descending):
            raise ForecastLaneError("a row select declares one direction per ordering column, and at least one column")


@dataclass(frozen=True, slots=True)
class GridAggregation:
    """One lane's coarse-rung rule: the spatial columns, the grain that stays distinct, the merges.

    A copy of the sibling's registered `TierDerivation` for this stream, restated over the FORECAST
    schema: the six provenance columns join `key_columns`, so no merge ever crosses two runs, two
    horizons or two quantiles.

    `row_select`, when declared, REPLACES `aggregations`: the merge rule is a property of the field,
    and a probability surface has no honest mean. See `RowSelect`.
    """

    longitude_column: str
    latitude_column: str
    key_columns: tuple[str, ...]
    aggregations: tuple[ColumnAggregation, ...] = ()
    row_select: RowSelect | None = None

    def __post_init__(self) -> None:
        if bool(self.aggregations) == (self.row_select is not None):
            raise ForecastLaneError(
                "a coarse-rung derivation declares EITHER per-column merges or one row select, never both and "
                "never neither; a lane with no declared rule would silently keep whichever row Polars saw first"
            )


FORECAST_TIER_DERIVATIONS: Final[Mapping[str, GridAggregation]] = {
    SIGNAL_STREAM: GridAggregation(
        longitude_column="cell_longitude",
        latitude_column="cell_latitude",
        key_columns=("support_key", "signal_name", "normalized_unit", "observed_day"),
        aggregations=(
            ColumnAggregation("cell_id", "null"),
            ColumnAggregation("normalized_value", "mean"),
            ColumnAggregation("observation_count", "sum"),
            ColumnAggregation("newest_observed_at", "max"),
            ColumnAggregation("coverage_fraction", "mean"),
            ColumnAggregation("allowed_client_exposure", "all"),
        ),
    ),
    FIRE_DETECTIONS_STREAM: GridAggregation(
        longitude_column="cell_longitude",
        latitude_column="cell_latitude",
        key_columns=("observed_day",),
        aggregations=(
            ColumnAggregation("detection_count", "sum"),
            ColumnAggregation("frp_sum", "sum"),
            ColumnAggregation("frp_observation_count", "sum"),
            ColumnAggregation("high_confidence_detection_count", "sum"),
            ColumnAggregation("newest_observed_at", "max"),
        ),
    ),
    #: A probability surface: the coarse cell reports its WORST base cell, whole. Ordered by score
    #: descending, then by refusal reason so a tie between two unscored cells is still deterministic.
    FIRE_RISK_STREAM: GridAggregation(
        longitude_column="cell_longitude",
        latitude_column="cell_latitude",
        key_columns=("valid_day",),
        row_select=RowSelect(
            order_by=("risk_score", "probability", "refused_reason"),
            descending=(True, True, False),
        ),
    ),
    VEGETATION_STREAM: GridAggregation(
        longitude_column="cell_longitude",
        latitude_column="cell_latitude",
        key_columns=("observed_day",),
        aggregations=(
            ColumnAggregation("cell_id", "null"),
            ColumnAggregation("grid_name", "first"),
            ColumnAggregation("metric_name", "first"),
            ColumnAggregation("metric_unit", "first"),
            ColumnAggregation("metric_value", "mean"),
            ColumnAggregation("observation_checksum", "null"),
            ColumnAggregation("data_available_at", "max"),
            ColumnAggregation("release_count", "sum"),
            ColumnAggregation("allowed_client_exposure", "all"),
        ),
    ),
}


def floor_to_tier(values: pl.Expr, *, zoom: ZoomTier, axis: LatticeAxis) -> pl.Expr:
    """Floor a coordinate column onto one rung's lattice, returning the cell ORIGIN.

    INTEGER micro-degrees throughout, which is the whole point: `value / pitch` is evaluated by
    different paths per frame length and platform, so a coordinate that IS a multiple of the pitch
    binned one way in a one-row frame and another way in a bulk frame (memory note "Polars division
    is frame-length dependent", and the 46-degree envelope edges it moved). Polars' `//` on Int64
    floors toward negative infinity, which is what every longitude in this envelope needs.
    """
    pitch = tier_pitch_micro(zoom)
    origin = axis_origin_micro(axis)
    micro = (values * float(MICRO_DEGREES_PER_DEGREE)).round().cast(pl.Int64)
    floored = ((micro - origin) // pitch) * pitch + origin
    return floored.cast(pl.Float64) / float(MICRO_DEGREES_PER_DEGREE)


def derive_coarse_rung(frame: pl.DataFrame, *, layer: str, zoom: ZoomTier) -> pl.DataFrame:
    """Coarsen one lane-day's base-rung forecast rows onto a published rung's lattice."""
    derivation = _derivation_for(layer)
    binned = frame.with_columns(
        floor_to_tier(pl.col(derivation.longitude_column), zoom=zoom, axis="longitude").alias(
            derivation.longitude_column
        ),
        floor_to_tier(pl.col(derivation.latitude_column), zoom=zoom, axis="latitude").alias(derivation.latitude_column),
    )
    # The six provenance columns join the grain in BOTH strategies, so neither a merge nor a select
    # can ever cross two runs, two horizons or two quantiles.
    keys = (
        derivation.longitude_column,
        derivation.latitude_column,
        *derivation.key_columns,
        *FORECAST_PROVENANCE_COLUMNS,
    )
    coarse = (
        _selected_rows(binned, derivation.row_select, keys=keys)
        if derivation.row_select is not None
        else _merged_rows(binned, derivation, keys=keys, dtypes=frame.schema)
    )
    return coarse.select(list(stream_schema(layer, forecast_root_kind(layer)).column_names))


def _derivation_for(layer: str) -> GridAggregation:
    """Return the coarse-rung rule of one lane, or refuse by naming the lanes that declare one."""
    derivation = FORECAST_TIER_DERIVATIONS.get(layer)
    if derivation is None:
        raise ForecastLaneError(
            f"lane {layer!r} declares no coarse-rung derivation in this service; it knows "
            f"{tuple(sorted(FORECAST_TIER_DERIVATIONS))}"
        )
    return derivation


def _merged_rows(
    binned: pl.DataFrame,
    derivation: GridAggregation,
    *,
    keys: tuple[str, ...],
    dtypes: Mapping[str, pl.DataType],
) -> pl.DataFrame:
    """Collapse the binned rows by their declared per-column merges, one coarse row per key."""
    merged = tuple(entry for entry in derivation.aggregations if entry.merge != "null")
    nulled = tuple(entry.column for entry in derivation.aggregations if entry.merge == "null")
    grouped = binned.group_by(list(keys)).agg([_merge_expression(aggregation) for aggregation in merged])
    # Nulled AFTER the group, keeping each column's declared dtype: a column unique to one base cell
    # has no honest merged value, and a literal null typed by inference would change the schema.
    return grouped.with_columns([pl.lit(None).cast(dtypes[column]).alias(column) for column in nulled])


def _selected_rows(binned: pl.DataFrame, select: RowSelect, *, keys: tuple[str, ...]) -> pl.DataFrame:
    """Keep ONE whole base row per coarse key, the first under the lane's declared ordering."""
    ordered = binned.sort(
        by=[*keys, *select.order_by],
        descending=[*([False] * len(keys)), *select.descending],
        nulls_last=True,
    )
    return ordered.unique(subset=list(keys), keep="first", maintain_order=True)


def _merge_expression(aggregation: ColumnAggregation) -> pl.Expr:
    """Return the Polars expression one column's declared merge is spelled as."""
    column = pl.col(aggregation.column)
    if aggregation.merge == "sum":
        return column.sum().alias(aggregation.column)
    if aggregation.merge == "mean":
        return column.mean().alias(aggregation.column)
    if aggregation.merge == "max":
        return column.max().alias(aggregation.column)
    if aggregation.merge == "all":
        return column.all().alias(aggregation.column)
    # `first`, for a column that is constant across the whole lane and so honestly picks its one value.
    return column.first().alias(aggregation.column)


__all__ = [
    "FORECAST_TIER_DERIVATIONS",
    "TIER_PITCH_MICRO_DEGREES",
    "ColumnAggregation",
    "ColumnMerge",
    "ForecastLaneError",
    "GridAggregation",
    "RowSelect",
    "derive_coarse_rung",
    "floor_to_tier",
]
