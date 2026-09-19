"""Reshaping one admitted provider run into published rows: base rung, then the coarse aggregates.

Layer L3, spec FR-12. Split out of `weather_forecast_daily.py` when that module passed the size
ceiling. PURE: answers go in and row dicts come out, with no bucket, no clock of its own and no
availability vocabulary. Every published variable is INTENSIVE, so a coarse cell is a mean --
except wind, which is recomposed from averaged components because the mean of many bearings is not
a bearing. Rationale lives in `sources/AGENTS.md`.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING, Final

from plantgeo_ml_service.foundation.lattice import floor_coordinate
from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM
from plantgeo_ml_service.warehouse.weather_forecast import (
    NOT_GENERATED,
    PUBLISHED_VARIABLES,
    variable_unit_and_statistic,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.foundation.parquet_paths import ZoomTier
    from plantgeo_ml_service.pipeline.weather_forecast_daily import BatchAnswer

SECONDS_PER_HOUR: Final = 3_600

DEGREES_PER_TURN: Final = 360.0

#: What a coarse row claims about its own footprint when its members disagree. A merged cell is a
#: DERIVED field whatever its inputs were, so the weakest of the three claims is the honest one;
#: carrying the first member's support would let one sampled point speak for a 5-degree cell.
MERGED_SUPPORT: Final = "derived_field"


class WeatherForecastRowError(RuntimeError):
    """Raised when an answered run cannot be reshaped into rows the pinned schema would admit."""


def base_rows(
    answers: Sequence[BatchAnswer],
    *,
    model_init_time: datetime,
    published_at: datetime,
) -> tuple[dict[str, object], ...]:
    """Reshape every answered location into base-rung rows, filling an absent variable with a reason.

    The published `latitude`/`longitude` are the LATTICE CELL's, never the provider's snapped sample
    point: a reader joins these rows to the rest of the warehouse by cell, and the snap was already
    accounted for by the pairing check the source performed.
    """
    first = answers[0].run
    paired = [
        (cell, location)
        for answer in answers
        for cell, location in zip(answer.cells, answer.run.locations, strict=True)
    ]
    rows: list[dict[str, object]] = []
    for cell, location in paired:
        readings = {(sample.valid_time, sample.variable): sample for sample in location.samples}
        for hour in sorted({valid_time for valid_time, _ in readings}):
            hours_from_init = lead_hours(hour, model_init_time)
            for variable in PUBLISHED_VARIABLES:
                sample = readings.get((hour, variable))
                unit, statistic = variable_unit_and_statistic(variable)
                rows.append(
                    {
                        "run_id": first.run_id,
                        "model_init_time": model_init_time,
                        "provider_issue_time": first.provider_issue_time,
                        "fetched_at": first.fetched_at,
                        "admitted_at": published_at,
                        "published_at": published_at,
                        "valid_time": hour,
                        "interval_start": None if sample is None else sample.interval_start,
                        "interval_end": None if sample is None else sample.interval_end,
                        "lead_hours": hours_from_init,
                        "cell_id": cell.cell_id,
                        "latitude": cell.latitude,
                        "longitude": cell.longitude,
                        "variable": variable,
                        "value": None if sample is None else sample.value,
                        "unit": unit,
                        "statistic": statistic,
                        # A variable the provider never answered for is a governed absence at the
                        # same support as the rest of the location: the point WAS sampled, this
                        # field was not generated for it.
                        "support": "sampled_point" if sample is None else sample.support,
                        "missing_reason": NOT_GENERATED if sample is None else sample.missing_reason,
                    }
                )
    return tuple(rows)


def lead_hours(valid_time: datetime, model_init_time: datetime) -> int:
    """Return whole hours from initialization to validity, refusing a row that precedes the run.

    With the precipitation window labelled at its START (FR-12), no admitted row sits before the
    initialization; one that does is a provider or parsing fault, not a negative forecast lead.
    """
    seconds = (valid_time - model_init_time).total_seconds()
    if seconds < 0:
        raise WeatherForecastRowError(
            f"a row valid at {valid_time.isoformat()} precedes the run initialized at {model_init_time.isoformat()}"
        )
    return int(seconds // SECONDS_PER_HOUR)


def rows_for_rung(base_rows: Sequence[dict[str, object]], zoom: ZoomTier) -> tuple[dict[str, object], ...]:
    """Return the rows one rung publishes: the base rows verbatim, or their coarse aggregate."""
    if zoom == BASE_PARTITION_ZOOM:
        return tuple(base_rows)
    return aggregated_rows(base_rows, zoom)


def aggregated_rows(base_rows: Sequence[dict[str, object]], zoom: ZoomTier) -> tuple[dict[str, object], ...]:
    """Aggregate base rows onto a coarser lattice, deriving wind from the mean COMPONENTS.

    Every published variable here is intensive (a temperature, a humidity, a rainfall depth), so the
    aggregate is a mean rather than a sum. Wind is the exception that proves the rule: the mean of
    many bearings is not a bearing, so `wind_speed_10m` and `wind_direction_10m` are recomputed from
    the averaged `wind_u_10m`/`wind_v_10m` pair instead of being averaged themselves.

    `cell_id` becomes NULL: a coarse cell merges many base cells and can honestly name none of them.
    """
    grouped: dict[tuple[float, float, datetime], dict[str, list[dict[str, object]]]] = {}
    for row in base_rows:
        longitude = floor_coordinate(_as_float(row["longitude"]), zoom=zoom, axis="longitude")
        latitude = floor_coordinate(_as_float(row["latitude"]), zoom=zoom, axis="latitude")
        valid_time = _as_datetime(row["valid_time"])
        grouped.setdefault((longitude, latitude, valid_time), {}).setdefault(str(row["variable"]), []).append(row)
    aggregated: list[dict[str, object]] = []
    for key in sorted(grouped):
        members = grouped[key]
        longitude, latitude, _ = key
        means = {variable: _mean_value(rows) for variable, rows in members.items()}
        derived = _derived_wind(means)
        for variable in PUBLISHED_VARIABLES:
            rows = members.get(variable)
            if rows is None:
                continue
            value, reason = derived.get(variable, (means[variable], _absence_reason(rows)))
            aggregated.append(
                {
                    **rows[0],
                    "cell_id": None,
                    "latitude": latitude,
                    "longitude": longitude,
                    "value": value,
                    "missing_reason": None if value is not None else (reason or NOT_GENERATED),
                    "support": _merged_support(rows),
                }
            )
    return tuple(aggregated)


def _merged_support(rows: Sequence[dict[str, object]]) -> str:
    """Return the footprint a merged row may claim: the unanimous one, else the weakest.

    `rows[0]`'s support described ONE base cell. A coarse cell that merged a sampled point with a
    native-grid cell has neither footprint, and claiming the first member's would overstate what the
    coarse row rests on.
    """
    supports = {str(row["support"]) for row in rows}
    return supports.pop() if len(supports) == 1 else MERGED_SUPPORT


def _mean_value(rows: Sequence[dict[str, object]]) -> float | None:
    """Return the mean of the present readings in one group, or `None` when every one is absent."""
    present = [_as_float(row["value"]) for row in rows if row["value"] is not None]
    return sum(present) / len(present) if present else None


def _absence_reason(rows: Sequence[dict[str, object]]) -> str | None:
    """Return the reason a whole group is absent: the unanimous one, else the generic one."""
    reasons = {row["missing_reason"] for row in rows}
    if len(reasons) == 1:
        reason = next(iter(reasons))
        return None if reason is None else str(reason)
    return NOT_GENERATED


def _derived_wind(means: Mapping[str, float | None]) -> dict[str, tuple[float | None, str | None]]:
    """Return the coarse rung's wind speed and direction, recomputed from the mean u/v components."""
    east, north = means.get("wind_u_10m"), means.get("wind_v_10m")
    if east is None or north is None:
        return {"wind_speed_10m": (None, NOT_GENERATED), "wind_direction_10m": (None, NOT_GENERATED)}
    speed = math.hypot(east, north)
    if speed == 0.0:
        # Calm again at the coarse rung: a magnitude of zero still has no bearing.
        return {"wind_speed_10m": (0.0, None), "wind_direction_10m": (None, NOT_GENERATED)}
    bearing = math.degrees(math.atan2(-east, -north)) % DEGREES_PER_TURN
    return {"wind_speed_10m": (speed, None), "wind_direction_10m": (bearing, None)}


def _as_float(value: object) -> float:
    """Narrow one row field to a float, refusing a shape the schema would not have admitted."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise WeatherForecastRowError(f"expected a numeric row field, got {type(value).__name__}")
    return float(value)


def _as_datetime(value: object) -> datetime:
    """Narrow one row field to an instant, refusing a shape the schema would not have admitted."""
    if not isinstance(value, datetime):
        raise WeatherForecastRowError(f"expected a timestamp row field, got {type(value).__name__}")
    return value


__all__ = [
    "DEGREES_PER_TURN",
    "MERGED_SUPPORT",
    "SECONDS_PER_HOUR",
    "WeatherForecastRowError",
    "aggregated_rows",
    "base_rows",
    "lead_hours",
    "rows_for_rung",
]
