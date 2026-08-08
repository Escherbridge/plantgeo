"""Read-only, bounded warehouse tools the agent graph exposes to the model.

Every tool here is a SELECT against a least-privilege reader session, with caps on radius,
time window and row count baked in rather than left to the model. See agent/AGENTS.md,
"Tool contract", for why the bounds are enforced here and not in the prompt.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from anthropic import beta_async_tool
from sqlalchemy import ARRAY, Text, bindparam, text

from agri_data_service.db.engine import published_reader_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.firms import resolve_firms_layer_name
from agri_data_service.ingest.mtbs import resolve_burn_severity_layer_name

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

# --- Bounds ------------------------------------------------------------------------
#
# Caps, not defaults: a model asking for more gets silently clamped, and the clamped value
# is reported back in the payload so the model can see what it actually received.

DEFAULT_RADIUS_METERS: Final = 10_000.0
MAX_RADIUS_METERS: Final = 50_000.0
MIN_RADIUS_METERS: Final = 100.0

DEFAULT_SUMMARY_ROWS: Final = 25
MAX_SUMMARY_ROWS: Final = 60

DEFAULT_DAYS_BACK: Final = 90
MAX_DAYS_BACK: Final = 3_650

DEFAULT_WEEKS_BACK: Final = 52
MAX_WEEKS_BACK: Final = 520

DEFAULT_FIRE_YEARS_BACK: Final = 5
MAX_FIRE_YEARS_BACK: Final = 45

DEFAULT_FORECAST_ROWS: Final = 40
MAX_FORECAST_ROWS: Final = 120

# Pre-aggregation caps: how much the database may gather before it collapses to a summary.
MAX_CELL_FANOUT: Final = 250
MAX_FIRE_FEATURE_FANOUT: Final = 2_000
MAX_NAME_FILTER_ENTRIES: Final = 12
MAX_NAME_LENGTH: Final = 150

_DAYS_PER_WEEK: Final = 7
_DAYS_PER_YEAR: Final = 365

_MIN_LONGITUDE: Final = -180.0
_MAX_LONGITUDE: Final = 180.0
_MIN_LATITUDE: Final = -90.0
_MAX_LATITUDE: Final = 90.0

_SIGNALS_SQL: Final = text(load_query_sql("agent/signals_near_point.sql")).bindparams(
    bindparam("signal_names", type_=ARRAY(Text))
)
_DROUGHT_SQL: Final = text(load_query_sql("agent/drought_history_at_point.sql"))
_FIRE_SQL: Final = text(load_query_sql("agent/fire_history_near_point.sql")).bindparams(
    bindparam("layer_names", type_=ARRAY(Text))
)
_FORECAST_SQL: Final = text(load_query_sql("agent/forecast_summary_for_cell.sql")).bindparams(
    bindparam("metric_names", type_=ARRAY(Text))
)


# --- Ambient run state -------------------------------------------------------------
#
# Tool functions are module-level and their signatures are the model-facing schema, so the
# session factory and the run ledger cannot be parameters. They travel in context variables
# the graph sets for the duration of one run, which also makes them trivial to stub.

_session_provider: ContextVar[Callable[[], AbstractAsyncContextManager[AsyncSession]]] = ContextVar(
    "agri_agent_session_provider", default=published_reader_session
)
_tool_ledger: ContextVar[list[dict[str, Any]] | None] = ContextVar("agri_agent_tool_ledger", default=None)


@asynccontextmanager
async def run_context(
    *,
    session_provider: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Bind one run's session provider and yield the ledger the tools append to."""
    ledger: list[dict[str, Any]] = []
    provider_token = _session_provider.set(session_provider or published_reader_session)
    ledger_token = _tool_ledger.set(ledger)
    try:
        yield ledger
    finally:
        _tool_ledger.reset(ledger_token)
        _session_provider.reset(provider_token)


def _record(tool_name: str, row_count: int, detail: dict[str, Any]) -> None:
    """Append one tool outcome to the run ledger, so sufficiency is judged on facts."""
    ledger = _tool_ledger.get()
    if ledger is not None:
        ledger.append({"tool": tool_name, "row_count": row_count, **detail})


# --- Bounding helpers --------------------------------------------------------------


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _clean_names(names: list[str] | None) -> list[str]:
    """Normalize an optional name filter into a bounded, deduplicated list."""
    if not names:
        return []
    seen: list[str] = []
    for raw in names:
        candidate = raw.strip()[:MAX_NAME_LENGTH]
        if candidate and candidate not in seen:
            seen.append(candidate)
        if len(seen) >= MAX_NAME_FILTER_ENTRIES:
            break
    return seen


def _valid_coordinate(longitude: float, latitude: float) -> bool:
    return _MIN_LONGITUDE <= longitude <= _MAX_LONGITUDE and _MIN_LATITUDE <= latitude <= _MAX_LATITUDE


def _json_safe(value: Any) -> Any:
    """Convert database scalars that json.dumps cannot serialize."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _payload(body: dict[str, Any]) -> str:
    """Render a tool result as compact JSON text."""
    return json.dumps(_json_safe(body), sort_keys=True, separators=(",", ":"))


def _coordinate_error(tool_name: str) -> str:
    _record(tool_name, 0, {"error": "invalid_coordinate"})
    return _payload({"error": "longitude must be within -180..180 and latitude within -90..90"})


async def _fetch(statement: Any, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one bounded read on a fresh least-privilege reader session."""
    async with _session_provider.get()() as session:
        result = await session.execute(statement, parameters)
        return [dict(row) for row in result.mappings().all()]


# --- Query implementations ---------------------------------------------------------
#
# Each is a plain async function so tests can call it directly; the model-facing tool
# below is a thin decorated wrapper whose signature is the published schema.


async def query_signals_near_point(  # noqa: PLR0913 - the parameter list is the published tool schema.
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    days_back: int = DEFAULT_DAYS_BACK,
    signal_names: list[str] | None = None,
    as_of: datetime | None = None,
) -> str:
    """Summarise governed signal observations near a point."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("signals_near_point")
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    window_days = _clamp_int(days_back, 1, MAX_DAYS_BACK)
    names = _clean_names(signal_names)
    window_end = as_of or datetime.now(UTC)
    window_start = window_end - timedelta(days=window_days)
    rows = await _fetch(
        _SIGNALS_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius,
            "cell_limit": MAX_CELL_FANOUT,
            "window_start": window_start,
            "window_end": window_end,
            "signal_names": names,
            "row_limit": DEFAULT_SUMMARY_ROWS,
        },
    )
    _record("signals_near_point", len(rows), {"radius_meters": radius, "days_back": window_days})
    return _payload(
        {
            "applied_bounds": {
                "radius_meters": radius,
                "days_back": window_days,
                "window_start": window_start,
                "window_end": window_end,
                "signal_names": names,
                "max_summary_rows": DEFAULT_SUMMARY_ROWS,
            },
            "signal_summaries": rows,
            "note": (
                "Empty means the warehouse holds no accepted observations for this radius and "
                "window. It does not mean the condition is absent."
            ),
        }
    )


async def query_drought_history_at_point(
    longitude: float,
    latitude: float,
    weeks_back: int = DEFAULT_WEEKS_BACK,
    as_of: datetime | None = None,
) -> str:
    """Return the weekly U.S. Drought Monitor severity covering a point."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("drought_history_at_point")
    window_weeks = _clamp_int(weeks_back, 1, MAX_WEEKS_BACK)
    reference = (as_of or datetime.now(UTC)).date()
    issue_date_from = reference - timedelta(days=window_weeks * _DAYS_PER_WEEK)
    rows = await _fetch(
        _DROUGHT_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "issue_date_from": issue_date_from,
            "row_limit": MAX_WEEKS_BACK,
        },
    )
    _record("drought_history_at_point", len(rows), {"weeks_back": window_weeks})
    return _payload(
        {
            "applied_bounds": {"weeks_back": window_weeks, "issue_date_from": issue_date_from},
            "severity_scale": (
                "0 = D0 abnormally dry, 1 = D1 moderate, 2 = D2 severe, 3 = D3 extreme, 4 = D4 exceptional"
            ),
            "weekly_severity": rows,
            "note": (
                "One row per published week, carrying the highest severity class whose polygon "
                "covered the point. A week with no row was not covered by any drought class."
            ),
        }
    )


async def query_fire_history_near_point(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    years_back: int = DEFAULT_FIRE_YEARS_BACK,
    as_of: datetime | None = None,
) -> str:
    """Summarise served fire detections and burn perimeters near a point."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("fire_history_near_point")
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    window_years = _clamp_int(years_back, 1, MAX_FIRE_YEARS_BACK)
    reference = (as_of or datetime.now(UTC)).date()
    observed_day_from = (reference - timedelta(days=window_years * _DAYS_PER_YEAR)).isoformat()
    layer_names = [resolve_firms_layer_name(), resolve_burn_severity_layer_name()]
    rows = await _fetch(
        _FIRE_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius,
            "layer_names": layer_names,
            "observed_day_from": observed_day_from,
            "feature_limit": MAX_FIRE_FEATURE_FANOUT,
        },
    )
    _record("fire_history_near_point", len(rows), {"radius_meters": radius, "years_back": window_years})
    return _payload(
        {
            "applied_bounds": {
                "radius_meters": radius,
                "years_back": window_years,
                "observed_day_from": observed_day_from,
                "layer_names": layer_names,
                "max_features_scanned": MAX_FIRE_FEATURE_FANOUT,
            },
            "layer_summaries": rows,
            "note": (
                "Counts are of served features within the radius, capped at max_features_scanned "
                "and nearest-first. A satellite detection is a thermal anomaly, not a confirmed "
                "fire perimeter."
            ),
        }
    )


async def query_forecast_summary_for_cell(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    metric_names: list[str] | None = None,
    as_of: datetime | None = None,
) -> str:
    """Return published forecast values for the analysis cell nearest a point."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("forecast_summary_for_cell")
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    names = _clean_names(metric_names)
    valid_from = as_of or datetime.now(UTC)
    rows = await _fetch(
        _FORECAST_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius,
            "valid_from": valid_from,
            "metric_names": names,
            "row_limit": DEFAULT_FORECAST_ROWS,
        },
    )
    _record("forecast_summary_for_cell", len(rows), {"radius_meters": radius})
    return _payload(
        {
            "applied_bounds": {
                "radius_meters": radius,
                "valid_from": valid_from,
                "metric_names": names,
                "max_rows": DEFAULT_FORECAST_ROWS,
            },
            "forecast_values": rows,
            "note": (
                "Only published, finalized, validated forecasts are visible here. Empty means no "
                "analysis cell within the radius has a published forecast."
            ),
        }
    )


# --- Model-facing tools ------------------------------------------------------------


@beta_async_tool
async def signals_near_point(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    days_back: int = DEFAULT_DAYS_BACK,
    signal_names: list[str] | None = None,
) -> str:
    """Summarise PlantGeo's governed environmental signal observations near a coordinate.

    Returns one summary row per signal, source parameter and unit: how many accepted
    readings there were, over what window, their value range, and how far the nearest
    contributing analysis cell was. Covers whatever the warehouse has ingested for the
    area (weather, streamflow, air quality, vegetation indices and similar). Radius, time
    window and row count are capped by the service; values beyond the cap are clamped and
    the applied bounds are reported back to you.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        radius_meters: Search radius around the point in metres; capped at 50000.
        days_back: How far back to look in days; capped at 3650.
        signal_names: Optional exact signal names to restrict to. Omit for every signal.
    """
    return await query_signals_near_point(
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius_meters,
        days_back=days_back,
        signal_names=signal_names,
    )


@beta_async_tool
async def drought_history_at_point(
    longitude: float,
    latitude: float,
    weeks_back: int = DEFAULT_WEEKS_BACK,
) -> str:
    """Return the U.S. Drought Monitor severity that covered a coordinate, week by week.

    One row per published week, carrying the highest severity class whose polygon covered
    the point (0 = D0 abnormally dry through 4 = D4 exceptional drought). A week absent
    from the result was not covered by any drought class at that point. The lookback is
    capped at 520 weeks.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        weeks_back: How many weeks of drought history to return; capped at 520.
    """
    return await query_drought_history_at_point(
        longitude=longitude,
        latitude=latitude,
        weeks_back=weeks_back,
    )


@beta_async_tool
async def fire_history_near_point(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    years_back: int = DEFAULT_FIRE_YEARS_BACK,
) -> str:
    """Summarise served satellite fire detections and mapped burn perimeters near a coordinate.

    Returns one row per served fire layer: how many features fell within the radius, how
    close the nearest was, and the calendar day range they span. Satellite detections are
    thermal anomalies rather than confirmed fires; burn perimeters are post-fire mapped
    boundaries. Radius is capped at 50000 metres and the lookback at 45 years.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        radius_meters: Search radius around the point in metres; capped at 50000.
        years_back: How many years of fire history to include; capped at 45.
    """
    return await query_fire_history_near_point(
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius_meters,
        years_back=years_back,
    )


@beta_async_tool
async def forecast_summary_for_cell(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    metric_names: list[str] | None = None,
) -> str:
    """Return PlantGeo's published metric forecasts for the analysis cell nearest a coordinate.

    Resolves the point to the single nearest analysis cell within the radius, then returns
    that cell's published forecast values -- metric, unit, issue time, valid time, horizon
    step, point value and the p10/p90 uncertainty band. Only published, finalized and
    validated forecasts are visible. Row count is capped at 120.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        radius_meters: How far to look for an analysis cell, in metres; capped at 50000.
        metric_names: Optional exact metric names to restrict to. Omit for every metric.
    """
    return await query_forecast_summary_for_cell(
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius_meters,
        metric_names=metric_names,
    )


WAREHOUSE_TOOLS: Final = (
    signals_near_point,
    drought_history_at_point,
    fire_history_near_point,
    forecast_summary_for_cell,
)
