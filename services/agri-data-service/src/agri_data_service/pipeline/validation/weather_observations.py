"""Reconcile the weather-observations current-conditions side lane's written Parquet, honestly.

Layer L3 pipeline: may import `foundation`, `warehouse`, and `ingest` (not policed by the six-layer
lattice, see `tests/test_layer_import_contract.py`); may NOT import `method`, `planes`, or
`interface`.

SCOPE, CONFIRMED AGAINST THE CODE THIS VALIDATES: this lane is `ingest/open_meteo.py`'s
`WEATHER_LAYER`, a rolling *current-conditions* poll of `https://api.open-meteo.com/v1/forecast`
landing in `geo.features` -- NOT the governed NASA POWER / ERA5-Land archive behind
`agri.signal_observation` that `docs/lanes/weather-observations.md` actually describes (that plane
is `signal`, validated separately). Because its producer holds no historical depth
(`MAX_OBSERVATION_AGE = 3h`, `ingest/open_meteo.py`), it gets no Monte Carlo forecaster: the
forecastable weather series lives in the `signal` stream, and standing up a second one here would
ship two contradictory weather forecasts from one service.

WHY THIS MODULE CANNOT DO WHAT `pipeline/validation/vegetation.py` DOES. That sibling reconciles the
written Parquet against a SOURCE SYSTEM query (`agri.forecast_observation`) that still holds the full
history it needs to answer a past day. Open-Meteo's *current-conditions* endpoint retains no such
history -- it answers "what is it right now", never "what did you report on 2026-08-01" -- so a day
already exported cannot be re-fetched and its values compared against what the source now holds.
Re-querying `geo.features` instead would only prove the export agrees with itself
(`pipeline/validation/__init__.py`'s own warning), not that it agrees with Open-Meteo.

WHAT IS GENUINELY CHECKED INSTEAD, and it is real verification, not a shrug: internal consistency of
the written partition against invariants the SOURCE's OWN contract already specifies
(`CURRENT_VALUE_BOUNDS`, `MAX_OBSERVATION_AGE`, `MAX_FUTURE_SKEW` -- all read from
`ingest/open_meteo.py` and `ingest/policy.py`, never re-stated as a second copy of the numbers), plus
point coverage against the ingest job's currently-configured sample lattice
(`ingest/open_meteo.py::bounded_sample_points`). No check here makes a network call.
`WeatherObservationsValidationReport.source_reconciliation_note` says this in the report itself, so a
caller reading `is_structurally_clean=True` cannot mistake it for "reconciled against Open-Meteo".

THE LANE CONTRACT HAS NO DECLARED CONTENT for this producer -- no cadence, horizon, historical depth,
or known-gaps list (`pipeline/parquet/lane_registry.py`'s `WEATHER_OBSERVATIONS_STREAM` registration,
`floor_basis="FALLBACK"`). Nothing here fabricates one; the lattice-coverage check is explicitly
scoped to "only trustworthy for the newest settled day" for exactly this reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.ingest.open_meteo import (
    CURRENT_VALUE_BOUNDS,
    MAX_OBSERVATION_AGE,
    OPEN_METEO_PROPERTY_SOURCE,
    bounded_sample_points,
)
from agri_data_service.ingest.policy import MAX_FUTURE_SKEW, UNCONFIGURED_BBOX_REASON
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_GRAIN,
    WEATHER_OBSERVATIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

# `CURRENT_VALUE_BOUNDS` keys by the upstream/stored field names `ingest/open_meteo.py` uses
# internally ("temperature", "windSpeed", ...); this lane's Parquet columns are named for the
# warehouse (`temperature_c`, `wind_speed_ms`, ...). This is the one place that translates between
# them, cited so the mapping cannot silently drift out of sync with either side.
_STORED_FIELD_TO_PARQUET_COLUMN: Final[Mapping[str, str]] = MappingProxyType(
    {
        "temperature": "temperature_c",
        "humidity": "relative_humidity_pct",
        "windSpeed": "wind_speed_ms",
        "windDirection": "wind_direction_deg",
        "precipitation": "precipitation_mm",
    }
)

# Derived from `CURRENT_VALUE_BOUNDS`, never a second copy of the numbers: the writer already
# enforced these bounds before a row could reach `geo.features`, so re-checking them here proves the
# written partition still agrees with that gate rather than restating it.
PARQUET_VALUE_BOUNDS: Final[tuple[tuple[str, float, float], ...]] = tuple(
    (_STORED_FIELD_TO_PARQUET_COLUMN[stored_field], minimum, maximum)
    for _, stored_field, minimum, maximum in CURRENT_VALUE_BOUNDS
)

# Six decimal degrees is ~11 cm at the equator -- far tighter than any real coordinate drift, so this
# only absorbs floating-point noise between two evaluations of the same deterministic grid formula
# (`bounded_sample_points`), never a genuinely different point.
_LATTICE_COORDINATE_PRECISION: Final = 6

LATTICE_COVERAGE_CHECK: Final = "lattice_coverage"

SOURCE_RECONCILIATION_NOTE: Final = (
    "Value-level reconciliation against Open-Meteo was NOT performed for {day}: the current-"
    "conditions forecast endpoint (ingest/open_meteo.py's WEATHER_LAYER) retains no history, so an "
    "already-exported day cannot be re-fetched and its temperature/humidity/wind/precipitation values "
    "compared against what the source reports now. The checks below instead re-verify the written "
    "partition against invariants the source's own contract already specifies, and the ingest job's "
    "currently-configured sample lattice; none of them contact Open-Meteo over the network."
)


class WeatherObservationsValidationError(ValueError):
    """Raised when a validation request itself is malformed, not when a check finds a disagreement."""


@dataclass(frozen=True, slots=True)
class WeatherObservationsValidityCheck:
    """One structural check on the written partition, or the stated reason it could not run."""

    name: str
    breaks: str
    evaluated: bool
    count: int = 0
    skipped_reason: str | None = None

    def __post_init__(self) -> None:
        """An unevaluated check must say why; a non-zero count on one is a contradiction, not data."""
        if not self.evaluated and not (self.skipped_reason or "").strip():
            raise WeatherObservationsValidationError(f"{self.name}: an unevaluated check must carry a skip reason")
        if self.evaluated and self.skipped_reason is not None:
            raise WeatherObservationsValidationError(f"{self.name}: an evaluated check must not carry a skip reason")
        if not self.evaluated and self.count:
            raise WeatherObservationsValidationError(f"{self.name}: an unevaluated check cannot report a count")

    @property
    def is_failing(self) -> bool:
        """True only when the check ran and found something; a skipped check never fails silently."""
        return self.evaluated and self.count > 0


@dataclass(frozen=True, slots=True)
class WeatherObservationsValidationReport:
    """Every structural check this producer's shape allows, plus the mandatory reconciliation caveat."""

    stream: str
    day: date
    row_count: int
    checks: tuple[WeatherObservationsValidityCheck, ...]
    source_reconciliation_note: str

    @property
    def failing_checks(self) -> tuple[WeatherObservationsValidityCheck, ...]:
        """The checks that ran and found something, in declared order."""
        return tuple(check for check in self.checks if check.is_failing)

    @property
    def is_structurally_clean(self) -> bool:
        """True when every EVALUATED check found nothing. Says nothing about source reconciliation."""
        return not self.failing_checks


def validate_weather_observations_partition(
    table: pa.Table,
    *,
    day: date,
    configured_bbox: str | None = None,
    configured_sample_spacing_degrees: float | None = None,
) -> WeatherObservationsValidationReport:
    """Run every check this lane's shape supports against one day's exported table.

    `table` is the same conformed rows `pipeline/lanes/weather_observations.py::export_weather_observations_day`
    hands to `store.write_partition` -- that call only selects, casts, and sorts columns, so validating
    it here is equivalent to validating the bytes that land in the partition; the Parquet round-trip
    itself (compression, on-disk encoding) is not re-verified by this module. Pass `configured_bbox`
    and `configured_sample_spacing_degrees` (`ingest.policy.resolve_bounded_bbox()` /
    `resolve_weather_sample_spacing_degrees()`, resolved by the caller at call time) to also check
    point coverage; that check is only trustworthy for the newest settled day, since a past day's true
    lattice depended on that day's own configuration, which nothing here has a historical record of.
    """
    if table.num_rows == 0:
        raise WeatherObservationsValidationError(
            f"refusing to validate a zero-row {WEATHER_OBSERVATIONS_STREAM!r} partition for {day}: an "
            "empty report reads as 'nothing wrong' and hides that nothing was actually checked"
        )
    frame = pl.from_arrow(table)
    # `pl.from_arrow` is typed to return `DataFrame | Series`; a `pa.Table` only ever yields the
    # former, and the assertion makes that a checked fact rather than an unstated assumption.
    assert isinstance(frame, pl.DataFrame)
    checks: list[WeatherObservationsValidityCheck] = [
        _day_scope_check(frame, day),
        _observed_day_consistency_check(frame),
        _duplicate_grain_check(frame),
        _unexpected_source_check(frame),
        *_value_bound_checks(frame),
        _stale_observation_check(frame),
        _future_skewed_observation_check(frame),
        _lattice_coverage_check(frame, configured_bbox, configured_sample_spacing_degrees),
    ]
    return WeatherObservationsValidationReport(
        stream=WEATHER_OBSERVATIONS_STREAM,
        day=day,
        row_count=table.num_rows,
        checks=tuple(checks),
        source_reconciliation_note=SOURCE_RECONCILIATION_NOTE.format(day=day.isoformat()),
    )


def _day_scope_check(frame: pl.DataFrame, day: date) -> WeatherObservationsValidityCheck:
    """A row outside the requested day means the exporter's own day filter stopped scoping correctly."""
    count = frame.filter(pl.col("observed_day") != day).height
    return WeatherObservationsValidityCheck(
        name="day_scope",
        breaks=(
            "a row's observed_day differs from the requested export day, meaning "
            "weather_observations_day_export.sql's day filter no longer scopes the query it runs"
        ),
        evaluated=True,
        count=count,
    )


def _observed_day_consistency_check(frame: pl.DataFrame) -> WeatherObservationsValidityCheck:
    """`observed_day` must reproduce the UTC calendar date of `observed_at`; the schema asserts they never disagree."""
    count = frame.filter(pl.col("observed_day") != pl.col("observed_at").dt.date()).height
    return WeatherObservationsValidityCheck(
        name="observed_day_consistency",
        breaks=(
            "observed_day does not match the UTC calendar date of observed_at, contradicting "
            "warehouse/schemas/weather_observations.py's stated invariant that the two never disagree"
        ),
        evaluated=True,
        count=count,
    )


def _duplicate_grain_check(frame: pl.DataFrame) -> WeatherObservationsValidityCheck:
    """More than one row per (latitude, longitude, observed_at) breaks the exporter's own primary key."""
    distinct_rows = frame.unique(subset=list(WEATHER_OBSERVATIONS_GRAIN)).height
    return WeatherObservationsValidityCheck(
        name="duplicate_grain",
        breaks=(
            "more than one row shares one (latitude, longitude, observed_at) triple, the grain this "
            "stream is sorted and clustered on; a reader relying on the grain would drop or double an instant"
        ),
        evaluated=True,
        count=frame.height - distinct_rows,
    )


def _unexpected_source_check(frame: pl.DataFrame) -> WeatherObservationsValidityCheck:
    """A row whose `source` is not `OPEN_METEO_PROPERTY_SOURCE` carries mixed, unaccounted-for provenance."""
    count = frame.filter(pl.col("source") != OPEN_METEO_PROPERTY_SOURCE).height
    return WeatherObservationsValidityCheck(
        name="unexpected_source",
        breaks=(
            "a row's source is not the literal Open-Meteo constant every value-bound and history-"
            "capability claim in this lane is scoped to; a second, unaccounted-for producer would misattribute them"
        ),
        evaluated=True,
        count=count,
    )


def _value_bound_checks(frame: pl.DataFrame) -> tuple[WeatherObservationsValidityCheck, ...]:
    """Re-verify each column against the bound the writer already enforced before the row reached `geo.features`."""
    return tuple(
        WeatherObservationsValidityCheck(
            name=f"value_bounds_{column}",
            breaks=(
                f"{column} falls outside the range ingest/open_meteo.py's CURRENT_VALUE_BOUNDS already "
                "enforced at fetch time; a violation here means that gate regressed, not that Open-Meteo changed"
            ),
            evaluated=True,
            count=frame.filter(~pl.col(column).is_between(minimum, maximum)).height,
        )
        for column, minimum, maximum in PARQUET_VALUE_BOUNDS
    )


def _stale_observation_check(frame: pl.DataFrame) -> WeatherObservationsValidityCheck:
    """`ingested_at - observed_at` must stay within `MAX_OBSERVATION_AGE`, the same bound the fetch-time gate used."""
    age = pl.col("ingested_at") - pl.col("observed_at")
    return WeatherObservationsValidityCheck(
        name="stale_observation",
        breaks=(
            "observed_at is older than MAX_OBSERVATION_AGE relative to ingested_at, the same freshness "
            "bound ingest/open_meteo.py's is_fresh_observation() enforced before the row could be written"
        ),
        evaluated=True,
        count=frame.filter(age > MAX_OBSERVATION_AGE).height,
    )


def _future_skewed_observation_check(frame: pl.DataFrame) -> WeatherObservationsValidityCheck:
    """`observed_at - ingested_at` must stay within `MAX_FUTURE_SKEW`, the other half of the same fetch-time gate."""
    skew = pl.col("observed_at") - pl.col("ingested_at")
    return WeatherObservationsValidityCheck(
        name="future_skewed_observation",
        breaks=(
            "observed_at sits more than MAX_FUTURE_SKEW ahead of ingested_at, the same clock-skew guard "
            "ingest/open_meteo.py's is_fresh_observation() enforced before the row could be written"
        ),
        evaluated=True,
        count=frame.filter(skew > MAX_FUTURE_SKEW).height,
    )


def _lattice_coverage_check(
    frame: pl.DataFrame,
    configured_bbox: str | None,
    configured_sample_spacing_degrees: float | None,
) -> WeatherObservationsValidityCheck:
    """Compare distinct sampled points against the currently-configured grid; skipped when unconfigured.

    Only trustworthy for the newest settled day: a past day's true lattice depended on that day's own
    `INGEST_BBOX` / `WEATHER_SAMPLE_SPACING_DEGREES`, which nothing here has a historical record of, so
    running this against an older partition can report a spurious gap after a deliberate reconfiguration.
    """
    if configured_bbox is None or configured_sample_spacing_degrees is None:
        return WeatherObservationsValidityCheck(
            name=LATTICE_COVERAGE_CHECK,
            breaks=(
                "fewer currently-configured sample points have a reading in this partition than the "
                "grid expects, which can mean the poller's fan-out is failing for part of the region"
            ),
            evaluated=False,
            skipped_reason=UNCONFIGURED_BBOX_REASON,
        )
    expected_points = bounded_sample_points(configured_bbox, configured_sample_spacing_degrees)
    expected = pl.DataFrame(
        {
            "latitude": [latitude for latitude, _ in expected_points],
            "longitude": [longitude for _, longitude in expected_points],
        }
    ).with_columns(
        pl.col("latitude").round(_LATTICE_COORDINATE_PRECISION),
        pl.col("longitude").round(_LATTICE_COORDINATE_PRECISION),
    )
    observed_points = frame.select(
        pl.col("latitude").round(_LATTICE_COORDINATE_PRECISION),
        pl.col("longitude").round(_LATTICE_COORDINATE_PRECISION),
    ).unique()
    unmatched = expected.join(observed_points, on=["latitude", "longitude"], how="anti")
    return WeatherObservationsValidityCheck(
        name=LATTICE_COVERAGE_CHECK,
        breaks=(
            "fewer currently-configured sample points have a reading in this partition than the grid "
            "expects, which can mean the poller's fan-out is failing for part of the region"
        ),
        evaluated=True,
        count=unmatched.height,
    )
