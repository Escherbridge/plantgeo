"""Leakage-gated fire-risk features: one row per (cell_longitude, cell_latitude, valid_day).

Layer L3. Every lane day this module reads is bounded by that lane's own `settled_through`, never by
today. The lattice arithmetic, the stratum definition, the two geometry decisions and the biased cell
universe live in `AGENTS-fire-risk.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import polars as pl

from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM, ZoomTier
from plantgeo_ml_service.pipeline.fire_risk_geometry import (
    MAX_GEOMETRY_BYTES,
    FireRiskGeometryError,
    wkb_envelope,
)

# Re-exported rather than re-declared: the frame assembly moved out when this module passed the
# size ceiling, and every existing importer names these here.
from plantgeo_ml_service.pipeline.fire_risk_plane import (
    FEATURE_SET_VERSION,
    FIRE_RISK_FEATURE_NAMES,
    GRAIN_COLUMNS,
    NDVI_FEATURE_COLUMN,
    crossed_with_horizons,
    cyclical_day_of_year,
    empty_feature_frame,
    photoperiod_seconds,
    with_checksums,
    with_seasonality,
)
from plantgeo_ml_service.warehouse.lanes import lane_contract, settled_through

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from plantgeo_ml_service.pipeline.observed_reader import ObservedReader

#: Bumped whenever a feature is added, removed, or its arithmetic changes. An artifact trained under
#: one version may not score a frame built under another, and `fire_risk_model` refuses the mismatch.

# --- The lattice ------------------------------------------------------------------------------------
# Every lattice operation is integer arithmetic on micro-degrees, shifted into a non-negative space
# first. Two reasons, both measured: polars division is frame-length dependent (a 1-row frame and a
# bulk frame disagree at cell edges), and integer division of a NEGATIVE longitude truncates towards
# zero in some engines and floors in others. Shifting removes the sign and multiplication removes the
# division. See `AGENTS-fire-risk.md`.

MICRO_DEGREES_PER_DEGREE: Final = 1_000_000
LONGITUDE_SHIFT_MICRO: Final = 180 * MICRO_DEGREES_PER_DEGREE
LATITUDE_SHIFT_MICRO: Final = 90 * MICRO_DEGREES_PER_DEGREE

#: The fire-detections grain, and therefore the fire-risk grain: 0.005 degrees.
FIRE_CELL_PITCH_MICRO: Final = 5_000

#: The vegetation and signal lattice: 0.25 degrees, centres at odd multiples of 0.125 degrees.
ANALYSIS_CELL_PITCH_MICRO: Final = 250_000
ANALYSIS_CELL_HALF_PITCH_MICRO: Final = ANALYSIS_CELL_PITCH_MICRO // 2

# --- The lanes, their windows and their vocabulary ---------------------------------------------------

FIRE_DETECTIONS_LANE: Final = "fire-detections"
VEGETATION_LANE: Final = "vegetation"
SIGNAL_LANE: Final = "signal"
DROUGHT_LANE: Final = "drought"
BURN_SEVERITY_LANE: Final = "burn-severity"

#: Every lane a fire-risk row rests on. The binding frontier is the MINIMUM of their settled days,
#: so adding a lane here can only move the issue day earlier, never later.
FIRE_RISK_INPUT_LANES: Final[tuple[str, ...]] = (
    FIRE_DETECTIONS_LANE,
    VEGETATION_LANE,
    SIGNAL_LANE,
    DROUGHT_LANE,
    BURN_SEVERITY_LANE,
)

#: Trailing ignition pressure. Four weeks covers a fire's active detection life without letting a
#: previous season's burn act as this season's ignition history.
IGNITION_HISTORY_DAYS: Final = 28

#: NDVI publishes every few days at best, so a fuel-state window shorter than this is regularly empty.
FUEL_STATE_DAYS: Final = 40

#: Weather drivers are the fast variables; a fortnight of means is the antecedent dryness the index
#: was measured on, without averaging away the spell that matters.
WEATHER_DRIVER_DAYS: Final = 14

#: USDM publishes weekly with a four-day lag, so three weeks always contains at least one release.
DROUGHT_WINDOW_DAYS: Final = 21

#: Prior burn suppresses reburn while the fuel it consumed is still absent. Three seasons.
PRIOR_BURN_DAYS: Final = 1_095

SURFACE_MOISTURE_SIGNAL: Final = "soil_water_content_layer_1"
ROOT_ZONE_MOISTURE_SIGNAL: Final = "soil_water_content_layer_3"
VAPOR_PRESSURE_DEFICIT_SIGNAL: Final = "vapor_pressure_deficit"
SOIL_TEMPERATURE_SIGNAL: Final = "soil_temperature_level_1"
AIR_TEMPERATURE_SIGNAL: Final = "air_temperature_mean"
RELATIVE_HUMIDITY_SIGNAL: Final = "relative_humidity"
WIND_SPEED_SIGNAL: Final = "wind_speed"
PRECIPITATION_SIGNAL: Final = "precipitation"

#: Signal name to feature column. `soil_wetness_*` is a different upstream on a different grid
#: measuring degree of saturation, and is deliberately absent: blending it with soil water content
#: would average two different physical quantities into one column.
SIGNAL_FEATURE_COLUMNS: Final[dict[str, str]] = {
    VAPOR_PRESSURE_DEFICIT_SIGNAL: "vapor_pressure_deficit",
    AIR_TEMPERATURE_SIGNAL: "air_temperature_mean",
    RELATIVE_HUMIDITY_SIGNAL: "relative_humidity",
    WIND_SPEED_SIGNAL: "wind_speed",
    PRECIPITATION_SIGNAL: "precipitation",
    SURFACE_MOISTURE_SIGNAL: "surface_soil_water_content",
    ROOT_ZONE_MOISTURE_SIGNAL: "root_zone_soil_water_content",
    SOIL_TEMPERATURE_SIGNAL: "soil_temperature",
}

VEGETATION_METRIC_NAME: Final = "ndvi"

# --- The stratum ---------------------------------------------------------------------------------
# The measured VPD discrimination runs 0.693 / 0.746 / 0.667 / 0.586 across greenness quartiles: the
# index works in steppe and transition and fails in closed forest, so a closed-forest cell is refused
# rather than scored. The split is a DECLARED NDVI threshold and not the analysis's own quartile,
# because a quantile computed on the scoring day's population is a different definition every day and
# no artifact could be reproduced against it.

CLOSED_FOREST_STRATUM: Final = "closed_forest"
OPEN_CANOPY_STRATUM: Final = "open_canopy"
UNKNOWN_STRATUM: Final = "unknown"

#: The greenness above which a cell is treated as closed forest. Sited on the 2026 analysis plane's
#: upper-quartile boundary; moving it is a model decision and bumps `FEATURE_SET_VERSION`.
CLOSED_FOREST_NDVI_FLOOR: Final = 0.6

FIRE_RISK_STRATA: Final[tuple[str, ...]] = (CLOSED_FOREST_STRATUM, OPEN_CANOPY_STRATUM, UNKNOWN_STRATUM)

# --- Bounds ----------------------------------------------------------------------------------------

#: One issue day's whole feature plane. A per-row checksum is computed in Python, so this is also the
#: bound on how long that pass may run.
MAX_FEATURE_ROWS: Final = 500_000

#: How many fire-cells the prior-burn envelopes may cover before the join is refused rather than
#: materialised. A perimeter bounding box is coarse; an unbounded one is a cross join.
MAX_PRIOR_BURN_CELLS: Final = 2_000_000

DEFAULT_HORIZON_DAYS: Final[tuple[int, ...]] = tuple(range(1, 15))


class FireRiskFeatureError(RuntimeError):
    """Raised when a feature frame cannot be built honestly: a bad horizon, a bound, or a geometry."""


@dataclass(frozen=True, slots=True)
class LaneWindow:
    """What one lane was actually read for, and the frontier that bounded it."""

    layer: str
    first_day: date
    last_day: date
    settled_through: date
    row_count: int

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection carried into the prediction receipt."""
        return {
            "layer": self.layer,
            "first_day": self.first_day.isoformat(),
            "last_day": self.last_day.isoformat(),
            "settled_through": self.settled_through.isoformat(),
            "row_count": self.row_count,
        }


@dataclass(frozen=True, slots=True)
class FireRiskFeatureFrame:
    """One issue day's feature plane, plus the lane windows that prove what it was built from."""

    #: The BINDING FRONTIER horizons are counted from: the newest day every input lane has settled
    #: through. This is what every row's `issued_on` column carries.
    issued_on: date
    #: The calendar day the run happened on, which bounded each lane's own read. Recorded separately
    #: because it is NOT the day the forecast was issued from, and one name for both hid a full
    #: publication lag inside "horizon 1".
    run_date: date
    frame: pl.DataFrame
    lane_windows: tuple[LaneWindow, ...]
    feature_set_version: str = FEATURE_SET_VERSION

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection carried into the prediction receipt."""
        return {
            "feature_set_version": self.feature_set_version,
            "issued_on": self.issued_on.isoformat(),
            "row_count": self.frame.height,
            "run_date": self.run_date.isoformat(),
            "lane_windows": [window.to_wire() for window in self.lane_windows],
        }


def binding_frontier(run_date: date) -> date:
    """Return the newest day EVERY fire-risk input lane has settled through by `run_date`.

    A fire-risk row is a joint claim over five lanes, so it can only be issued from the day the
    SLOWEST of them has published — `signal`'s nine-day lag today. Counting horizons from the
    calendar run date instead would label a row "one day out" when its binding evidence stopped ten
    days earlier (`plan.md` tripwire: staleness is measured from the provider frontier, never today).
    """
    return min(settled_through(lane, run_date) for lane in FIRE_RISK_INPUT_LANES)


def build_fire_risk_features(
    reader: ObservedReader,
    *,
    run_date: date,
    issued_on: date | None = None,
    horizons: Sequence[int] = DEFAULT_HORIZON_DAYS,
    zoom: ZoomTier = BASE_PARTITION_ZOOM,
) -> FireRiskFeatureFrame:
    """Build the leakage-gated feature plane for one run date, labelled from its binding frontier.

    `run_date` bounds every lane read at that lane's own `settled_through`; `issued_on` is the day
    horizons are counted from and defaults to `binding_frontier(run_date)`. They are two different
    dates and this lane previously used one name for both.
    """
    horizon_days = _validated_horizons(horizons)
    issue_day = issued_on if issued_on is not None else binding_frontier(run_date)
    windows: list[LaneWindow] = []
    ignition = _ignition_history(reader, run_date=run_date, zoom=zoom, windows=windows)
    if ignition.height == 0:
        return FireRiskFeatureFrame(
            issued_on=issue_day,
            run_date=run_date,
            frame=empty_feature_frame(),
            lane_windows=tuple(windows),
        )
    fuel = _fuel_state(reader, run_date=run_date, zoom=zoom, windows=windows)
    drivers = _weather_drivers(reader, run_date=run_date, zoom=zoom, windows=windows)
    drought_category = _regional_drought_category(reader, run_date=run_date, zoom=zoom, windows=windows)
    prior_burn = _prior_burn_envelopes(reader, run_date=run_date, zoom=zoom, windows=windows)
    cells = (
        ignition.join(fuel, on=("longitude_index", "latitude_index"), how="left")
        .join(drivers, on=("longitude_index", "latitude_index"), how="left")
        .join(prior_burn, on=("fire_longitude_index", "fire_latitude_index"), how="left")
        .with_columns(
            pl.col("prior_burn_envelope_overlap").fill_null(0.0),
            pl.lit(drought_category, dtype=pl.Float64).alias("regional_drought_category"),
            pl.col("stratum").fill_null(UNKNOWN_STRATUM),
        )
    )
    frame = with_seasonality(crossed_with_horizons(cells, issued_on=issue_day, horizons=horizon_days))
    _refuse_oversized_plane(frame.height)
    ordered = frame.select(*GRAIN_COLUMNS, "issued_on", "horizon_days", "stratum", *FIRE_RISK_FEATURE_NAMES).sort(
        by=["cell_longitude", "cell_latitude", "valid_day"]
    )
    return FireRiskFeatureFrame(
        issued_on=issue_day,
        run_date=run_date,
        frame=with_checksums(ordered),
        lane_windows=tuple(windows),
    )


def analysis_cell_index(coordinate: pl.Expr, *, shift_micro: int) -> pl.Expr:
    """Return the 0.25-degree lattice index nearest one coordinate, by integer micro-degree division."""
    return (_micro_degrees(coordinate) + shift_micro) // ANALYSIS_CELL_PITCH_MICRO


def fire_cell_index(coordinate: pl.Expr, *, shift_micro: int) -> pl.Expr:
    """Return the 0.005-degree base-grain index of one coordinate."""
    return (_micro_degrees(coordinate) + shift_micro) // FIRE_CELL_PITCH_MICRO


# --- Lane readers ------------------------------------------------------------------------------------


def _ignition_history(
    reader: ObservedReader, *, run_date: date, zoom: ZoomTier, windows: list[LaneWindow]
) -> pl.DataFrame:
    """Read trailing detections and aggregate them per base cell, which is also the cell universe."""
    rows = _read_window(reader, FIRE_DETECTIONS_LANE, run_date=run_date, zoom=zoom, span=IGNITION_HISTORY_DAYS)
    windows.append(rows.window)
    return (
        rows.frame.group_by("cell_longitude", "cell_latitude")
        .agg(
            pl.col("detection_count").sum().cast(pl.Float64).alias("detection_count_history"),
            pl.col("frp_sum").sum().cast(pl.Float64).alias("radiative_power_history"),
            pl.col("high_confidence_detection_count").sum().cast(pl.Float64).alias("high_confidence_detection_history"),
        )
        .with_columns(
            analysis_cell_index(pl.col("cell_longitude"), shift_micro=LONGITUDE_SHIFT_MICRO).alias("longitude_index"),
            analysis_cell_index(pl.col("cell_latitude"), shift_micro=LATITUDE_SHIFT_MICRO).alias("latitude_index"),
            fire_cell_index(pl.col("cell_longitude"), shift_micro=LONGITUDE_SHIFT_MICRO).alias("fire_longitude_index"),
            fire_cell_index(pl.col("cell_latitude"), shift_micro=LATITUDE_SHIFT_MICRO).alias("fire_latitude_index"),
        )
    )


def _fuel_state(reader: ObservedReader, *, run_date: date, zoom: ZoomTier, windows: list[LaneWindow]) -> pl.DataFrame:
    """Read NDVI, average it per analysis cell, and assign the stratum that average implies."""
    rows = _read_window(reader, VEGETATION_LANE, run_date=run_date, zoom=zoom, span=FUEL_STATE_DAYS)
    windows.append(rows.window)
    greenness = (
        rows.frame.filter(
            (pl.col("metric_name") == VEGETATION_METRIC_NAME) & pl.col("metric_value").is_between(-1.0, 1.0)
        )
        .with_columns(
            analysis_cell_index(pl.col("cell_longitude"), shift_micro=LONGITUDE_SHIFT_MICRO).alias("longitude_index"),
            analysis_cell_index(pl.col("cell_latitude"), shift_micro=LATITUDE_SHIFT_MICRO).alias("latitude_index"),
        )
        .group_by("longitude_index", "latitude_index")
        .agg(pl.col("metric_value").mean().alias(NDVI_FEATURE_COLUMN))
    )
    return greenness.with_columns(
        pl.when(pl.col(NDVI_FEATURE_COLUMN) >= CLOSED_FOREST_NDVI_FLOOR)
        .then(pl.lit(CLOSED_FOREST_STRATUM))
        .otherwise(pl.lit(OPEN_CANOPY_STRATUM))
        .alias("stratum")
    )


def _weather_drivers(
    reader: ObservedReader, *, run_date: date, zoom: ZoomTier, windows: list[LaneWindow]
) -> pl.DataFrame:
    """Read the signal lane and mean each named driver per analysis cell, one column per signal."""
    rows = _read_window(reader, SIGNAL_LANE, run_date=run_date, zoom=zoom, span=WEATHER_DRIVER_DAYS)
    windows.append(rows.window)
    named = rows.frame.filter(pl.col("signal_name").is_in(list(SIGNAL_FEATURE_COLUMNS))).with_columns(
        analysis_cell_index(pl.col("cell_longitude"), shift_micro=LONGITUDE_SHIFT_MICRO).alias("longitude_index"),
        analysis_cell_index(pl.col("cell_latitude"), shift_micro=LATITUDE_SHIFT_MICRO).alias("latitude_index"),
    )
    aggregates = [
        pl.col("normalized_value").filter(pl.col("signal_name") == signal_name).mean().alias(column)
        for signal_name, column in SIGNAL_FEATURE_COLUMNS.items()
    ]
    return named.group_by("longitude_index", "latitude_index").agg(*aggregates)


def _regional_drought_category(
    reader: ObservedReader, *, run_date: date, zoom: ZoomTier, windows: list[LaneWindow]
) -> float | None:
    """Return the worst drought class in the newest settled release, as a REGIONAL scalar.

    Deliberately not cell-resolved: a USDM release is a CONUS-wide multipolygon, so the bounding-box
    trick that is honest for a fire perimeter would assign every cell the same class anyway. See
    `AGENTS-fire-risk.md`, "Why drought is a scalar and burn history is not".
    """
    rows = _read_window(reader, DROUGHT_LANE, run_date=run_date, zoom=zoom, span=DROUGHT_WINDOW_DAYS)
    windows.append(rows.window)
    if rows.frame.height == 0:
        return None
    newest = rows.frame.select(pl.col("valid_date").max()).item()
    return float(rows.frame.filter(pl.col("valid_date") == newest).select(pl.col("dm_category").max()).item())


def _prior_burn_envelopes(
    reader: ObservedReader, *, run_date: date, zoom: ZoomTier, windows: list[LaneWindow]
) -> pl.DataFrame:
    """Return the base cells a prior burn perimeter's bounding box covers, one row per covered cell."""
    rows = _read_window(reader, BURN_SEVERITY_LANE, run_date=run_date, zoom=zoom, span=PRIOR_BURN_DAYS)
    windows.append(rows.window)
    envelopes = tuple(
        envelope
        for envelope in (wkb_envelope(payload) for payload in rows.frame.get_column("geom").to_list() if payload)
        if envelope is not None
    )
    covered = _covered_fire_cells(envelopes)
    if not covered:
        return pl.DataFrame(
            schema={
                "fire_longitude_index": pl.Int64,
                "fire_latitude_index": pl.Int64,
                "prior_burn_envelope_overlap": pl.Float64,
            }
        )
    longitude_indices, latitude_indices = zip(*sorted(covered), strict=True)
    return pl.DataFrame(
        {
            "fire_longitude_index": list(longitude_indices),
            "fire_latitude_index": list(latitude_indices),
            "prior_burn_envelope_overlap": [1.0] * len(covered),
        },
        schema={
            "fire_longitude_index": pl.Int64,
            "fire_latitude_index": pl.Int64,
            "prior_burn_envelope_overlap": pl.Float64,
        },
    )


@dataclass(frozen=True, slots=True)
class _LaneRows:
    """One lane read: the rows and the receipt of the window they came from."""

    frame: pl.DataFrame
    window: LaneWindow


def _read_window(reader: ObservedReader, layer: str, *, run_date: date, zoom: ZoomTier, span: int) -> _LaneRows:
    """Read one lane's trailing window, ending at the lane's own settled frontier, never at today."""
    contract = lane_contract(layer)
    last_day = contract.settled_through(run_date)
    first_day = max(contract.history_floor, last_day - timedelta(days=span - 1))
    frame = reader.read_lane_window(layer, zoom, first_day, last_day, as_of=run_date)
    window = LaneWindow(
        layer=layer,
        first_day=first_day,
        last_day=last_day,
        settled_through=last_day,
        row_count=frame.height,
    )
    return _LaneRows(frame=frame, window=window)


# --- Guards and small arithmetic ---------------------------------------------------------------------


def _validated_horizons(horizons: Sequence[int]) -> tuple[int, ...]:
    """Return the horizons in order, refusing a duplicate, a non-positive day or an empty request."""
    ordered = tuple(sorted(horizons))
    if not ordered:
        raise FireRiskFeatureError("a feature plane needs at least one horizon")
    if len(set(ordered)) != len(ordered):
        raise FireRiskFeatureError(f"horizons {horizons!r} repeat a day; one valid day may carry one row")
    if ordered[0] < 1:
        raise FireRiskFeatureError(f"horizons {horizons!r} include a day at or before the issue day")
    if ordered[-1] > max(DEFAULT_HORIZON_DAYS):
        raise FireRiskFeatureError(
            f"horizon {ordered[-1]} exceeds the declared fire-risk range of 1..{max(DEFAULT_HORIZON_DAYS)} days"
        )
    return ordered


def _refuse_oversized_plane(row_count: int) -> None:
    """Refuse a plane past the row budget rather than spending an unbounded checksum pass on it."""
    if row_count > MAX_FEATURE_ROWS:
        raise FireRiskFeatureError(
            f"a feature plane of {row_count} rows exceeds the {MAX_FEATURE_ROWS}-row budget for one issue day"
        )


def _micro_degrees(coordinate: pl.Expr) -> pl.Expr:
    """Return one coordinate as integer micro-degrees, by multiplication, never by division."""
    return (coordinate * float(MICRO_DEGREES_PER_DEGREE)).round().cast(pl.Int64)


def _covered_fire_cells(envelopes: Iterable[tuple[float, float, float, float]]) -> set[tuple[int, int]]:
    """Return every base-cell index pair a perimeter envelope covers, refusing past the cell budget."""
    covered: set[tuple[int, int]] = set()
    for west, south, east, north in envelopes:
        longitude_indices = _index_range(west, east, shift_micro=LONGITUDE_SHIFT_MICRO)
        latitude_indices = _index_range(south, north, shift_micro=LATITUDE_SHIFT_MICRO)
        if len(covered) + len(longitude_indices) * len(latitude_indices) > MAX_PRIOR_BURN_CELLS:
            raise FireRiskFeatureError(
                f"prior-burn envelopes cover more than {MAX_PRIOR_BURN_CELLS} base cells; the bounding-box "
                "join is bounded on purpose and a wider one is a refusal, not a slower run"
            )
        covered.update((longitude, latitude) for longitude in longitude_indices for latitude in latitude_indices)
    return covered


def _index_range(low: float, high: float, *, shift_micro: int) -> range:
    """Return the inclusive base-cell index range one envelope side spans."""
    first = (round(low * MICRO_DEGREES_PER_DEGREE) + shift_micro) // FIRE_CELL_PITCH_MICRO
    last = (round(high * MICRO_DEGREES_PER_DEGREE) + shift_micro) // FIRE_CELL_PITCH_MICRO
    return range(first, last + 1)


__all__ = [
    "DEFAULT_HORIZON_DAYS",
    "FEATURE_SET_VERSION",
    "FIRE_RISK_FEATURE_NAMES",
    "FIRE_RISK_INPUT_LANES",
    "FIRE_RISK_STRATA",
    "MAX_GEOMETRY_BYTES",
    "FireRiskFeatureError",
    "FireRiskFeatureFrame",
    "FireRiskGeometryError",
    "LaneWindow",
    "binding_frontier",
    "build_fire_risk_features",
    "cyclical_day_of_year",
    "photoperiod_seconds",
    "wkb_envelope",
]
