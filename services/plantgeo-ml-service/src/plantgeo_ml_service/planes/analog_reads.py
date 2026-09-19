"""`analogs`: one signal cell's Analog Ensemble forecast, as issued from one origin day.

Layer L4. Why the window is walked day by day rather than globbed, and why a day the run absented is
skipped instead of refusing the whole answer, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM
from plantgeo_ml_service.pipeline.analog_ensemble_daily import (
    ANALOG_ENSEMBLE_HORIZON_DAYS,
    HIGH_QUANTILE,
    LOW_QUANTILE,
    MEDIAN_QUANTILE,
)
from plantgeo_ml_service.pipeline.monte_carlo_daily import analog_ensemble_artifact_sha256
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.availability_reads import read_lane_availability
from plantgeo_ml_service.planes.partition_reads import (
    MAX_DAY_KEYS,
    MAX_SERIES_ROWS,
    day_part_keys,
    rows_for_cell_identifier,
)
from plantgeo_ml_service.planes.wire import ARTIFACT_ABSENT_NO_ARTIFACT, ClaimProvenance, render_day
from plantgeo_ml_service.warehouse.streams import SIGNAL_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.foundation.parquet_paths import PartitionKind
    from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
    from plantgeo_ml_service.pipeline.object_store import ReadOnlyObjectStore

FORECAST_KIND: Final[PartitionKind] = "forecast"

#: How many object keys one analog read may name across its whole window. Thirty days of one lane,
#: each written by one run, sit far under this; a window this wide is a fault, not a busy lane.
MAX_WINDOW_KEYS: Final = MAX_DAY_KEYS

#: The three quantiles the AnEn lane publishes, in the order a reader draws them.
PUBLISHED_QUANTILES: Final[tuple[float, float, float]] = (LOW_QUANTILE, MEDIAN_QUANTILE, HIGH_QUANTILE)


@dataclass(frozen=True, slots=True)
class AnalogStep:
    """One horizon step of one series: the three published quantiles and the ensemble behind them."""

    horizon_days: int
    valid_day: date
    p10: float | None
    p50: float | None
    p90: float | None
    ensemble_size: int

    def to_wire(self) -> dict[str, object]:
        """Render one horizon step."""
        return {
            "horizon_days": self.horizon_days,
            "valid_day": render_day(self.valid_day),
            "p10": self.p10,
            "p50": self.p50,
            "p90": self.p90,
            "ensemble_size": self.ensemble_size,
        }


@dataclass(frozen=True, slots=True)
class AnalogSeries:
    """One cell-signal's forecast from one origin: its steps, ascending by horizon."""

    signal_name: str
    support_key: str
    normalized_unit: str
    forecast_run_id: str
    random_seed: int
    steps: tuple[AnalogStep, ...]

    def to_wire(self) -> dict[str, object]:
        """Render one series and its steps."""
        return {
            "signal_name": self.signal_name,
            "support_key": self.support_key,
            "normalized_unit": self.normalized_unit,
            "forecast_run_id": self.forecast_run_id,
            "random_seed": self.random_seed,
            "steps": [step.to_wire() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class AnalogAnswer:
    """Every series one cell carries for one origin, with the days the window could not read."""

    cell_id: str
    origin: date
    series: tuple[AnalogSeries, ...]
    unreadable_days: tuple[tuple[date, str], ...]
    truncated: bool
    claim: ClaimProvenance

    def to_wire(self) -> dict[str, object]:
        """Render the analogs payload; the claim block is added by the route."""
        return {
            "layer": SIGNAL_STREAM,
            "cell_id": self.cell_id,
            "origin": render_day(self.origin),
            "horizon_days": ANALOG_ENSEMBLE_HORIZON_DAYS,
            "quantiles": list(PUBLISHED_QUANTILES),
            "series": [entry.to_wire() for entry in self.series],
            "unreadable_days": [{"day": render_day(day), "reason": reason} for day, reason in self.unreadable_days],
            "truncated": self.truncated,
        }


def read_analogs(
    store: ReadOnlyObjectStore,
    session: DuckDbSession,
    *,
    cell_id: str,
    origin: date,
) -> AnalogAnswer:
    """Return one cell's AnEn forecast issued from `origin`, p10/p50/p90 by horizon, or refuse.

    Each day of the window is judged against the availability POINTER before its prefix is listed:
    writing a partition does not publish it (FR-4a), so a horizon outside the current generation is
    reported as unreadable rather than answered from objects the lane never advanced onto.
    """
    availability = read_lane_availability(store, layer=SIGNAL_STREAM, kind=FORECAST_KIND)
    window = tuple(origin + timedelta(days=step) for step in range(1, ANALOG_ENSEMBLE_HORIZON_DAYS + 1))
    keys: list[str] = []
    unreadable: list[tuple[date, str]] = []
    for day in window:
        if not availability.covers(day):
            unreadable.append((day, refusals.AVAILABILITY_DAY_NOT_COVERED))
            continue
        try:
            keys.extend(
                day_part_keys(store, layer=SIGNAL_STREAM, kind=FORECAST_KIND, zoom=BASE_PARTITION_ZOOM, day=day)
            )
        except refusals.MachineLearningRefusalError as error:
            # A day the run absented or never reached is skipped and REPORTED. Refusing the whole
            # window would hide the horizons that were forecast behind the ones that were not.
            unreadable.append((day, error.code))
    if len(keys) > MAX_WINDOW_KEYS:
        raise refusals.read_over_budget(
            operation="analogs",
            detail=f"the {ANALOG_ENSEMBLE_HORIZON_DAYS}-day window names {len(keys)} objects, over its key budget",
        )
    if not keys:
        raise refusals.forecast_window_unwritten(
            layer=SIGNAL_STREAM, first_day=window[0].isoformat(), last_day=window[-1].isoformat()
        )
    answer = rows_for_cell_identifier(session, keys, cell_id=cell_id, issued_on=origin, row_limit=MAX_SERIES_ROWS)
    if not answer.rows:
        raise refusals.cell_series_absent(layer=SIGNAL_STREAM, cell_id=cell_id, origin=origin.isoformat())
    return AnalogAnswer(
        cell_id=cell_id,
        origin=origin,
        series=_series_from(answer.rows),
        unreadable_days=tuple(unreadable),
        truncated=answer.truncated,
        claim=_claim(store, origin=origin),
    )


def _series_from(rows: Sequence[Mapping[str, object]]) -> tuple[AnalogSeries, ...]:
    """Fold quantile rows into one series per signal, each ascending by horizon."""
    grouped: dict[tuple[str, str, str], list[Mapping[str, object]]] = {}
    for row in rows:
        identity = (str(row["signal_name"]), str(row["support_key"]), str(row["normalized_unit"]))
        grouped.setdefault(identity, []).append(row)
    return tuple(
        AnalogSeries(
            signal_name=signal_name,
            support_key=support_key,
            normalized_unit=normalized_unit,
            forecast_run_id=str(entries[0]["forecast_run_id"]),
            random_seed=int(entries[0]["random_seed"]),  # type: ignore[call-overload]  # the schema pins this int64
            steps=_steps_from(entries),
        )
        for (signal_name, support_key, normalized_unit), entries in sorted(grouped.items())
    )


def _steps_from(rows: Sequence[Mapping[str, object]]) -> tuple[AnalogStep, ...]:
    """Fold one series' rows into one step per horizon, with the three quantiles side by side."""
    by_horizon: dict[int, dict[float, Mapping[str, object]]] = {}
    for row in rows:
        horizon = int(row["horizon_days"])  # type: ignore[call-overload]  # the schema pins this int16 non-null
        by_horizon.setdefault(horizon, {})[float(row["quantile"])] = row  # type: ignore[arg-type]  # pinned float64
    steps: list[AnalogStep] = []
    for horizon in sorted(by_horizon):
        quantiles = by_horizon[horizon]
        anchor = quantiles[min(quantiles)]
        steps.append(
            AnalogStep(
                horizon_days=horizon,
                valid_day=_as_day(anchor["observed_day"]),
                p10=_value(quantiles.get(LOW_QUANTILE)),
                p50=_value(quantiles.get(MEDIAN_QUANTILE)),
                p90=_value(quantiles.get(HIGH_QUANTILE)),
                ensemble_size=int(anchor["ensemble_size"]),  # type: ignore[call-overload]  # pinned int32 non-null
            )
        )
    return tuple(steps)


def _value(row: Mapping[str, object] | None) -> float | None:
    """Return one quantile's value, or `None` when the run published no row for that quantile."""
    if row is None:
        return None
    value = row["normalized_value"]
    if not isinstance(value, (int, float)):
        raise refusals.serving_fault(operation="analogs", fault=f"{type(value).__name__} in a float column")
    return float(value)


def _as_day(value: object) -> date:
    """Narrow one date32 cell, refusing a shape the pinned schema says cannot occur."""
    if not isinstance(value, date):
        raise refusals.serving_fault(operation="analogs", fault=f"{type(value).__name__} in a date column")
    return value


def _claim(store: ReadOnlyObjectStore, *, origin: date) -> ClaimProvenance:
    """Return the claim block: the AnEn artifact the bucket holds, or the declared absence of one."""
    digest = analog_ensemble_artifact_sha256(store)
    if digest is None:
        return ClaimProvenance(
            artifact_sha256=None, artifact_absent_reason=ARTIFACT_ABSENT_NO_ARTIFACT, issued_on=origin
        )
    return ClaimProvenance(artifact_sha256=digest, artifact_absent_reason=None, issued_on=origin)


__all__ = [
    "MAX_WINDOW_KEYS",
    "PUBLISHED_QUANTILES",
    "AnalogAnswer",
    "AnalogSeries",
    "AnalogStep",
    "read_analogs",
]
