"""Read-only, bounded warehouse tools the agent graph exposes to the model.

Every environmental answer here is a bounded read of the day-partitioned Parquet warehouse, through
`agent/warehouse.py`, with caps on radius, time window and row count baked in rather than left to
the model. Two questions are still PostgreSQL reads and say why: the ingest lane's absence ledger
and the governed ML forecast plane, neither of which is environmental data. See agent/AGENTS.md,
"Tool contract", for why the bounds are enforced here and not in the prompt, and "Reading the
Parquet warehouse" for what a four-state answer means.
"""

from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import wraps
from math import cos, radians
from typing import TYPE_CHECKING, Any, Final

from anthropic import beta_async_tool
from sqlalchemy import ARRAY, Float, Text, bindparam, text

from agri_data_service.agent import parquet_reads, warehouse
from agri_data_service.agent.surfaces import (
    AGENT_SURFACE_NAMES,
    FEATURE_SURFACE_NAMES,
    FIRE_LANE_NAMES,
    POSTGRESQL_ONLY_SURFACE_NAMES,
    SIGNAL_PLANE_LANE,
    STREAM_SURFACE_NAMES,
    surface_lanes,
)
from agri_data_service.db.engine import published_reader_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.warehouse_reader import (
    GeometrySupport,
    NoSpatialSupport,
    PointSupport,
    spatial_support,
)
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.agent.warehouse import AgentWarehouseSource, LaneEvidence, LaneWindow

__all__ = [
    "AGENT_SURFACE_NAMES",
    "FEATURE_SURFACE_NAMES",
    "STREAM_SURFACE_NAMES",
    "WAREHOUSE_TOOLS",
    "run_context",
]

# --- Bounds ------------------------------------------------------------------------
#
# Caps, not defaults: a model asking for more gets silently clamped, and the clamped value
# is reported back in the payload so the model can see what it actually received.

DEFAULT_RADIUS_METERS: Final = 10_000.0
MAX_RADIUS_METERS: Final = 50_000.0
MIN_RADIUS_METERS: Final = 100.0

DEFAULT_SUMMARY_ROWS: Final = 25
MAX_SUMMARY_ROWS: Final = 60

# --- Window caps, and why every one of them fell -----------------------------------
#
# THESE ARE SCAN BUDGETS, NOT THE DEPTH OF THE RECORD, and every tool that clamps one says so in
# its note. Against the dropped `geo.mv_signal_cell_daily` a decade-deep window was a longer
# contiguous index range; against Parquet it is one object-store GET per written day per lane. A
# 3,650-day window is therefore thousands of GETs on a request path, not a longer scan.
#
# `MAX_DAYS_BACK` and `MAX_WEEKS_BACK` are set TO `warehouse.MAX_SCANNED_DAY_PARTITIONS` (120), the
# partition budget that already bounds one read's object-store GETs via `narrow_to_budget`. A
# smaller depth cap bought nothing here: at the old 92 days / 52 weeks the requested span never held
# 120 written days, so `narrow_to_budget` never fired and the shallower window was pure lost depth at
# an unchanged worst-case GET cost. Raising the cap to the budget lets `narrow_to_budget` do the
# bounding it already exists to do -- it reports itself back (`window_narrowed_by_scan_budget`)
# whenever a fully-published window is wide enough to trip it, same as it always has for the fire
# lanes below.
#
# `MAX_FIRE_YEARS_BACK` stays at 2 and is NOT the same budget: fire's window is walked a year at a
# time (`warehouse.lane_years`), so its cost is object-store LIST calls, not GETs, and 45 years
# across two lanes would be roughly 1,080 LISTs. Raising it properly needs a `lane_years`-style
# listing the way drought already has; that is an owner product decision, not made here.
#
# The depth question -- how far back does this lane go at all -- was meant to move to
# `observation_coverage_on_day`, which is SUPPOSED to answer it from the availability index at the
# cost of two small GETs. IT CANNOT TODAY: `parquet_coverage_authority` defaults to
# `census_until_bootstrap` (`config.py:177`) and no lane has a published availability receipt, so
# every lane comes back `availability_unpublished` and the tool answers
# `parquet_availability_withheld` instead of a day count. Every note below that points a caller
# there is naming an intended escape hatch, not a working one.

DEFAULT_DAYS_BACK: Final = 90
MAX_DAYS_BACK: Final = 120

DEFAULT_WEEKS_BACK: Final = 52
MAX_WEEKS_BACK: Final = 120

DEFAULT_FIRE_YEARS_BACK: Final = 1
MAX_FIRE_YEARS_BACK: Final = 2

DEFAULT_FORECAST_ROWS: Final = 40
MAX_FORECAST_ROWS: Final = 120

# --- Selected-day bounds -----------------------------------------------------------
#
# Bounds for the tools that answer at the day the UI has selected. See agent/AGENTS.md,
# "Answering at the selected day", for what each one is measured against and why.

# 19 signal names are under contract across the three lanes (execution/coverage_contract.py,
# verified against agri.data_source and agri.signal_observation 2026-08-11). Rows group by
# signal x support key x unit -- the Parquet signal plane carries no source_parameter column -- so
# 40 leaves a lane room to publish a second unit spelling without an answer being truncated.
MAX_DAY_SUMMARY_ROWS: Final = 40
MAX_COVERAGE_AUDIT_ROWS: Final = 40
# At most two rows -- one before, one after -- per group the day summary admits.
MAX_TEMPORAL_NEIGHBOR_ROWS: Final = MAX_DAY_SUMMARY_ROWS * 2

# Measured against production 2026-08-11: NASA POWER is gapless daily over 397 cells from
# 2022-08-06 to 2026-08-06 and ERA5-Land runs to 2026-08-02, so a neighbour is normally one day
# out and the widest routine gap is ERA5-Land's four-day publication lag. 180 days sits far
# above that, which is what makes "no neighbour inside the window" a claim about the data.
DEFAULT_NEIGHBOR_DAYS: Final = 30
MAX_NEIGHBOR_DAYS: Final = 180

# agri.spatial_cell 2026-08-11: nasa-power-0.5-degree holds 397 cells at roughly 55 km spacing
# and sentinel2-ndvi-0p25deg 1,568 at roughly 27.8 km east-west by 20.1 km north-south at 43.6N,
# so a 50 km radius admits about fourteen centroids of the denser grid. 25 covers both grids at
# once with headroom.
DEFAULT_NEAREST_CELLS: Final = 8
MAX_NEAREST_CELLS: Final = 25

# How far back `nearest_signal_cells` and `forecast_summary_for_cell` look to learn WHICH cells
# exist near a point.
#
# `agri.spatial_cell` was the registry of cells and is gone -- the retirement inventory classes it
# "drop now" and production no longer has it. The Parquet signal plane carries each row's own cell
# centroid, so the cell set is recoverable, but only as "cells that reported inside a window"
# rather than as "cells this grid declares". 30 days is far wider than any contracted lane's
# cadence, so a cell absent from it has not reported for a month; the tools say that outright
# rather than presenting an observed set as a registry.
CELL_UNIVERSE_DAYS: Final = 30

# --- Generic-surface bounds --------------------------------------------------------
#
# Bounds for the three tools that answer for ANY catalogue surface rather than for the signal
# plane alone. See agent/AGENTS.md, "The generic surface triad".

# The availability index answers a whole lane's history from two small GETs, so this window bounds
# the ANSWER and not the work. 180 days matches MAX_NEIGHBOR_DAYS so both temporal tools make the
# same claim about how far "no neighbour" was searched.
DEFAULT_SURFACE_NEIGHBOR_DAYS: Final = 30
MAX_SURFACE_NEIGHBOR_DAYS: Final = 180

DEFAULT_SURFACE_FEATURE_ROWS: Final = 12
MAX_SURFACE_FEATURE_ROWS: Final = 50

# Pre-aggregation caps: how much one read may gather before it collapses to a summary.
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

# --- What a published object must actually carry before a question can be asked of it ------
#
# A REGISTERED SCHEMA IS A PROMISE, NOT AN OBSERVATION, and the difference is measurable today.
# `warehouse/parquet/schema.py` declares `cell_longitude` and `cell_latitude` non-nullable on the
# signal plane, and the newest published z13 part -- `year=2026/month=08/day=06/part-0.parquet`,
# read 2026-09-04 -- carries eleven columns and neither of them: the lane was exported before the
# positions were added and the `postgres-*` lanes are stopped, so no re-export has followed.
#
# These tuples are the columns each read genuinely needs. `warehouse.scan` probes for them once per
# read and refuses by name when they are missing, so a lane that owes a re-export says so instead of
# raising a binder error the model can only report as "the tool broke".
SIGNAL_PLANE_COLUMNS: Final = (
    "support_key",
    "signal_name",
    "normalized_unit",
    "cell_id",
    "observed_day",
    "normalized_value",
    "observation_count",
    "newest_observed_at",
    "coverage_fraction",
    "allowed_client_exposure",
    "cell_longitude",
    "cell_latitude",
)

DROUGHT_LANE_COLUMNS: Final = ("valid_date", "dm_category", "ingested_at", "geom")

# Which column carries "how many features is this row worth" on a fire lane.
#
# HAND-SPELLED because the two lanes have genuinely different grains and guessing from a column
# name would be a rule nobody wrote down. `fire-detections` publishes one row per CELL-DAY carrying
# `detection_count`, so its feature count is that column summed; `burn-severity` publishes one row
# per mapped perimeter, so its feature count is the row count itself.
FIRE_LANE_FEATURE_COUNT_COLUMN: Final[dict[str, str | None]] = {
    "fire-detections": "detection_count",
    "burn-severity": None,
}

# --- The one pre-aggregated relation still probed ----------------------------------
#
# agri.mv_forecast_ml_daily_serving is a MATERIALIZED VIEW, which can exist while holding nothing:
# PostgreSQL creates it WITH NO DATA and raises rather than returning zero rows until a REFRESH has
# run. It shipped in exactly that state. It is also the last relation any agent tool probes: every
# environmental plane moved to Parquet, where "never built" is `lane_never_written`, one of the four
# states the warehouse itself reports rather than a catalog fact to be inferred.

FORECAST_DAILY_RELATION: Final = "agri.mv_forecast_ml_daily_serving"

# --- Bounding-box prefilter arithmetic ---------------------------------------------
#
# A metre radius has to become a degree box before it can be a range predicate DuckDB pushes into a
# Parquet row group. A degree of latitude is a fixed 110,574 m (WGS84 mean); a degree of longitude
# is 111,320 m only at the equator and shrinks by cos(latitude). Sizing the box on the latitude
# figure alone would clip its eastern and western edges at any distance from the equator and
# silently drop real rows, which is the exact failure this prefilter must not introduce -- so each
# axis is sized on its own figure, with a margin for the ellipsoid error the spherical figures
# leave behind. The box is a strict superset of the circle: it changes how many rows are measured,
# never which rows survive the exact test.
_METERS_PER_DEGREE_LATITUDE: Final = 110_574.0
_METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR: Final = 111_320.0
_BBOX_SAFETY_MARGIN: Final = 1.05
# Floors the cosine so the divide cannot explode within ~0.6 degrees of a pole. At that latitude
# the box degenerates to most of the meridian anyway and the exact test does the real work.
_MIN_LATITUDE_COSINE: Final = 0.01

# --- The PostgreSQL statements that remain -----------------------------------------
#
# Four, and each one names why it is not a Parquet read: two governance relations the retirement
# inventory classes "keep", one community layer RUNBOOK section 0.26.1 keeps in PostgreSQL, and the
# catalog probe that guards the first of them.

_PLANE_POPULATED_SQL: Final = text(load_query_sql("agent/materialized_plane_populated.sql")).bindparams(
    bindparam("relation_names", type_=ARRAY(Text))
)
_FORECAST_SQL: Final = text(load_query_sql("agent/forecast_summary_for_cell.sql")).bindparams(
    bindparam("metric_names", type_=ARRAY(Text))
)
_COVERAGE_ON_DAY_SQL: Final = text(load_query_sql("agent/signal_coverage_on_day.sql")).bindparams(
    bindparam("cell_ids", type_=ARRAY(Text)),
    bindparam("cell_distances", type_=ARRAY(Float)),
    bindparam("signal_names", type_=ARRAY(Text)),
)
_FEATURE_NEAR_POINT_SQL: Final = text(load_query_sql("agent/feature_value_near_point.sql")).bindparams(
    bindparam("property_keys", type_=ARRAY(Text))
)

# The property keys the ONE remaining PostgreSQL feature layer may carry, hand-spelled and capped.
#
# `SELECT properties` on geo.features is how a bounded row count becomes an unbounded byte count --
# the column carries roughly 1,467 MB of TOAST across 4.97 million rows (measured 2026-08-15), so a
# fifty-row answer can be tens of megabytes. The Parquet lanes have no such column: they publish
# typed columns their registered schema declares, so this allow-list guards `interventions` alone.
FEATURE_PROPERTY_KEYS: Final = (
    "acres",
    "county",
    "description",
    "id",
    "ignitionDate",
    "name",
    "observedAt",
    "priority",
    "source",
    "status",
    "updatedAt",
)


# --- Ambient run state -------------------------------------------------------------
#
# Tool functions are module-level and their signatures are the model-facing schema, so the
# session factory, the warehouse source and the run ledger cannot be parameters. They travel in
# context variables the graph sets for the duration of one run, which also makes them trivial to
# stub.

_session_provider: ContextVar[Callable[[], AbstractAsyncContextManager[AsyncSession]]] = ContextVar(
    "agri_agent_session_provider", default=published_reader_session
)
_tool_ledger: ContextVar[list[dict[str, Any]] | None] = ContextVar("agri_agent_tool_ledger", default=None)
# One run's answers to "is this plane built". A matview never becomes unpopulated again once it
# has been refreshed, so the answer cannot go stale inside a run.
_plane_state: ContextVar[dict[str, bool] | None] = ContextVar("agri_agent_plane_state", default=None)


@asynccontextmanager
async def run_context(
    *,
    session_provider: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
    warehouse_source: AgentWarehouseSource | None = None,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Bind one run's PostgreSQL session provider and Parquet source, and yield the tool ledger."""
    ledger: list[dict[str, Any]] = []
    provider_token = _session_provider.set(session_provider or published_reader_session)
    ledger_token = _tool_ledger.set(ledger)
    plane_token = _plane_state.set({})
    source_token = warehouse.set_source(warehouse_source)
    try:
        yield ledger
    finally:
        warehouse.reset_source(source_token)
        _plane_state.reset(plane_token)
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


def _bbox_degrees(radius_meters: float, latitude: float) -> float:
    """Half-width in degrees of a square that certainly contains everything within the radius."""
    cosine = max(cos(radians(latitude)), _MIN_LATITUDE_COSINE)
    longitude_degrees = radius_meters / (_METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * cosine)
    latitude_degrees = radius_meters / _METERS_PER_DEGREE_LATITUDE
    return max(longitude_degrees, latitude_degrees) * _BBOX_SAFETY_MARGIN


def _bbox_bounds(longitude: float, latitude: float, radius_meters: float) -> tuple[float, float, float, float]:
    """The `west, south, east, north` box containing the radius, sized per axis rather than square."""
    cosine = max(cos(radians(latitude)), _MIN_LATITUDE_COSINE)
    longitude_degrees = radius_meters / (_METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * cosine) * _BBOX_SAFETY_MARGIN
    latitude_degrees = radius_meters / _METERS_PER_DEGREE_LATITUDE * _BBOX_SAFETY_MARGIN
    return (
        longitude - longitude_degrees,
        latitude - latitude_degrees,
        longitude + longitude_degrees,
        latitude + latitude_degrees,
    )


def _signal_scope_parameters(longitude: float, latitude: float, radius_meters: float) -> list[object]:
    """The eight parameters every signal statement's shared scope binds, in DuckDB's own order.

    THE PROBE IS LATITUDE FIRST. DuckDB's geodesic distance functions take the ordinates the
    opposite way round from every geometry function, and `parquet_reads` carries the measurement
    that proves it. Bound the ordinary way, `ST_Distance_Spheroid` answers NaN.
    """
    west, south, east, north = _bbox_bounds(longitude, latitude, radius_meters)
    return [west, east, south, north, latitude, longitude, radius_meters, MAX_CELL_FANOUT]


def _json_safe(value: Any) -> Any:  # noqa: PLR0911 - one return per JSON-incompatible scalar/collection type
    """Convert warehouse scalars that json.dumps cannot serialize.

    The rules mirror `parquet_ops.wire.render_scalar`, which is the frozen renderer the map's own
    client reads through: a day stays day-shaped, an instant carries its zone, bytes go hex, and a
    non-finite float becomes null because `0.0` would fabricate a reading where a NaN says there is
    none.
    """
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def _payload(body: dict[str, Any]) -> str:
    """Render a tool result as compact JSON text."""
    return json.dumps(_json_safe(body), sort_keys=True, separators=(",", ":"))


def _coordinate_error(tool_name: str) -> str:
    _record(tool_name, 0, {"error": "invalid_coordinate"})
    return _payload({"error": "longitude must be within -180..180 and latitude within -90..90"})


def _parse_day(raw_day: str) -> date | None:
    """Parse an ISO calendar day, answering None rather than guessing at an unparseable one."""
    try:
        return date.fromisoformat(raw_day.strip())
    except ValueError:
        return None


def _day_error(tool_name: str, raw_day: str) -> str:
    """Refuse an unparseable day outright; a substituted day would answer a different question."""
    _record(tool_name, 0, {"error": "invalid_day"})
    return _payload(
        {
            "error": "day must be an ISO calendar day such as 2026-03-14",
            "received_day": raw_day,
        }
    )


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """The half-open pair of UTC midnights bounding one calendar day."""
    opening = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return opening, opening + timedelta(days=1)


def _day_span(first_day: date, last_day: date) -> list[date]:
    """Every calendar day of a closed range, ascending; a day is never derived from an instant."""
    return [first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1)]


def _surface_error(tool_name: str, raw_surface: str) -> str:
    """Refuse a surface outside the catalogue, naming what the catalogue holds."""
    _record(tool_name, 0, {"error": "unknown_surface"})
    return _payload(
        {
            "error": "surface_name must be one of the map's published surfaces",
            "received_surface_name": raw_surface,
            "known_surface_names": list(AGENT_SURFACE_NAMES),
            "note": (
                "This is a refusal, not an absence. The name given is not a surface the map "
                "publishes, so nothing was queried and nothing can be concluded about it."
            ),
        }
    )


def _feature_surface_error(raw_surface: str) -> str:
    """Refuse a non-feature surface by name, listing the layers this tool can actually answer for."""
    _record("feature_value_near_point", 0, {"error": "unsupported_surface"})
    return _payload(
        {
            "error": "surface_name must be one of the feature-backed map layers",
            "received_surface_name": raw_surface,
            "supported_surface_names": list(FEATURE_SURFACE_NAMES),
            "note": (
                "This is a refusal, not an absence. The surfaces this tool cannot answer for are "
                "the cell-grid signal streams and the drought release set, which have no "
                "individual features to return -- use signal_value_on_day or "
                "drought_history_at_point for those. Nothing was queried, so nothing follows "
                "about the surface that was named."
            ),
        }
    )


async def _fetch(statement: Any, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one bounded PostgreSQL read on a fresh least-privilege reader session."""
    async with _session_provider.get()() as session:
        result = await session.execute(statement, parameters)
        return [dict(row) for row in result.mappings().all()]


# --- Refusing rather than answering nothing ----------------------------------------
#
# The refusal discipline is the point of this module and it did not soften in the move to Parquet;
# it gained states. A PostgreSQL matview could only be built or unbuilt. A Parquet lane-day is in
# one of four states, and three of them are things a model must never read as "nothing is here":
#
#   published            rows exist and were served
#   governed_absence     the lane looked and the SOURCE had nothing; the marker says why
#   day_not_written      nobody has ever written this day -- a real gap, and no claim follows
#   lane_never_written   the lane has never written anything at this rung -- the old "unbuilt plane"
#
# On top of those sit the SERVING refusals (`parquet_ops.faults`), which are statements about this
# process rather than about the warehouse: a half-written day, a read over its memory budget, every
# serving slot busy. Each one is reported by its own code so a model cannot fold it into an absence.


def _plane_refusal(tool_name: str, unbuilt: list[dict[str, Any]]) -> str:
    """State that a pre-aggregated PostgreSQL plane has never been built, rather than answering nothing."""
    names = [entry["relation"] for entry in unbuilt]
    _record(tool_name, 0, {"error": "plane_unbuilt", "relations": names})
    return _payload(
        {
            "error": "pre_aggregated_plane_unbuilt",
            "unbuilt_relations": names,
            "note": (
                "This is a REFUSAL, not an absence. The pre-aggregated relation this tool reads "
                "exists in the schema but has never been refreshed, so it holds no rows and "
                "cannot be read at all. Nothing whatsoever follows about whether data exists for "
                "this location or day -- say that the warehouse view backing this answer is not "
                "built, and do not report the subject as absent, zero or unaffected."
            ),
        }
    )


async def _unbuilt_planes(relations: Sequence[str]) -> list[dict[str, Any]]:
    """Return one entry per named relation that is missing or has never been refreshed."""
    resolved = _plane_state.get()
    if resolved is None:
        resolved = {}
    unknown = [name for name in relations if name not in resolved]
    if unknown:
        rows = await _fetch(_PLANE_POPULATED_SQL, {"relation_names": unknown})
        observed = {str(row["relation_name"]): bool(row["is_populated"]) for row in rows}
        # A relation the probe did not answer for at all is treated as unbuilt: silence about a
        # plane is not evidence that the plane is fine.
        for name in unknown:
            resolved[name] = observed.get(name, False)
    return [
        {"relation": name, "state": "missing_or_unpopulated"} for name in relations if not resolved.get(name, False)
    ]


def _lane_never_written_refusal(tool_name: str, lanes: Sequence[str]) -> str:
    """State that a Parquet lane holds nothing at all, rather than answering an empty result."""
    _record(tool_name, 0, {"error": "parquet_lane_never_written", "lanes": list(lanes)})
    return _payload(
        {
            "error": "parquet_lane_never_written",
            "unwritten_lanes": list(lanes),
            "note": (
                "This is a REFUSAL, not an absence. The Parquet lane this tool reads has never "
                "written a single day at the rung the agent reads, so there is nothing to read at "
                "all. Nothing whatsoever follows about whether data exists for this location or "
                "day -- say that the lane backing this answer has never been published, and do "
                "not report the subject as absent, zero or unaffected."
            ),
        }
    )


def _serving_refusal(tool_name: str, exc: ServingRefusalError) -> str:
    """Report a serving fault as a refusal; it is a statement about this process, never about content."""
    _record(tool_name, 0, {"error": "parquet_serving_refused", "code": exc.code})
    return _payload(
        {
            "error": "parquet_serving_refused",
            "refusal_code": exc.code,
            "refusal_detail": exc.message,
            "note": (
                "This is a REFUSAL, not an absence. The warehouse could be reached and declined to "
                "state what this day holds -- a half-written export, a read past its memory "
                "budget, or every serving slot busy. It is a fact about the serving process and "
                "not about the data, so nothing follows about this location or day."
            ),
        }
    )


def _availability_refusal(tool_name: str, evidence: Sequence[LaneEvidence]) -> str:
    """State that a lane cannot prove its coverage, rather than reporting the day uncovered."""
    unproven = [
        {
            "lane": lane.layer,
            "reason": "not_a_registered_parquet_lane" if lane.unregistered else lane.withheld_reason,
        }
        for lane in evidence
        if not lane.proven
    ]
    _record(tool_name, 0, {"error": "parquet_availability_withheld", "lanes": [entry["lane"] for entry in unproven]})
    return _payload(
        {
            "error": "parquet_availability_withheld",
            "unproven_lanes": unproven,
            "note": (
                "This is a REFUSAL, not an absence. Coverage is answered from each lane's published "
                "availability index, and at least one lane behind this surface has no index this "
                "process will trust -- never bootstrapped, stale past its own source ceiling, "
                "malformed, or failing its checksum. The alternative evidence is a whole-stream "
                "object listing, which this tool will not pay on a request path. Say that the "
                "surface cannot currently prove its coverage; do NOT say the day is empty."
            ),
        }
    )


def _refuses_serving_faults(
    tool_name: str,
) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
    """Turn any serving refusal raised beneath a tool into that tool's typed refusal payload.

    Typed all the way through -- `function` and the returned callable are both
    `Callable[..., Awaitable[str]]`, never `Any` -- because every tool this decorates is
    async-returning-`str`. A looser `Any` here does not just blur this function's own body; it makes
    every `query_*` tool decorated with it lose its return type at the call site, since mypy resolves
    a decorator application from the DECLARED signature, not from what `decorate` happens to return.
    """

    def decorate(function: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        @wraps(function)
        async def guarded(*args: Any, **kwargs: Any) -> str:
            try:
                return await function(*args, **kwargs)
            except ServingRefusalError as exc:
                return _serving_refusal(tool_name, exc)

        return guarded

    return decorate


def _window_states(window: LaneWindow, first_day: date, last_day: date) -> dict[str, int]:
    """Count the four states across a requested range, so an empty answer explains itself."""
    counts = {"published": 0, "governed_absence": 0, "day_not_written": 0, "lane_never_written": 0}
    for day in _day_span(first_day, last_day):
        state = window.state_of(day)
        counts[state] = counts.get(state, 0) + 1
    return counts


def _scanned_bounds(scanned: warehouse.ScannedSpan) -> dict[str, Any]:
    """The span a read actually addressed, always reported beside the span that was asked for."""
    return {
        "requested_from": scanned.requested_from,
        "requested_through": scanned.requested_through,
        "scanned_from": scanned.scanned_from,
        "scanned_through": scanned.scanned_through,
        "scanned_day_count": len(scanned.days),
        "window_narrowed_by_scan_budget": scanned.narrowed,
        "max_scanned_day_partitions": warehouse.MAX_SCANNED_DAY_PARTITIONS,
    }


def _filter_by_name(rows: list[dict[str, Any]], names: Sequence[str], column: str) -> list[dict[str, Any]]:
    """Apply the caller's optional name filter after the read, never inside it.

    The filter is a Python step because the group count the statements return is bounded by the
    governed contract itself -- 19 signal names across three lanes -- so filtering afterwards costs
    nothing and keeps an empty list parameter out of a DuckDB prepared statement, where an untyped
    empty list has no element type to infer.
    """
    if not names:
        return rows
    wanted = set(names)
    return [row for row in rows if row.get(column) in wanted]


# --- Query implementations ---------------------------------------------------------
#
# Each is a plain async function so tests can call it directly; the model-facing tool
# below is a thin decorated wrapper whose signature is the published schema.


@_refuses_serving_faults("signals_near_point")
async def query_signals_near_point(  # noqa: PLR0913 - the parameter list is the published tool schema.
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    days_back: int = DEFAULT_DAYS_BACK,
    signal_names: list[str] | None = None,
    as_of: datetime | None = None,
) -> str:
    """Summarise governed signal observations near a point, from the Parquet signal plane."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("signals_near_point")
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    window_days = _clamp_int(days_back, 1, MAX_DAYS_BACK)
    names = _clean_names(signal_names)
    day_through = (as_of or datetime.now(UTC)).date()
    day_from = day_through - timedelta(days=window_days)
    window = await warehouse.lane_window(layer=SIGNAL_PLANE_LANE, first_day=day_from, last_day=day_through)
    if not window.lane_written:
        return _lane_never_written_refusal("signals_near_point", (SIGNAL_PLANE_LANE,))
    window.raise_on_unserveable(_day_span(day_from, day_through))
    scanned = warehouse.narrow_to_budget(
        window.published_days(day_from, day_through),
        requested_from=day_from,
        requested_through=day_through,
    )
    measured = await warehouse.scan(
        parquet_reads.SIGNAL_WINDOW_SUMMARY,
        _signal_scope_parameters(longitude, latitude, radius),
        part_keys=window.part_keys(scanned.days),
        operation="agent_signals_near_point",
        layer=SIGNAL_PLANE_LANE,
        required_columns=SIGNAL_PLANE_COLUMNS,
    )
    rows = _filter_by_name(measured, names, "signal_name")[:DEFAULT_SUMMARY_ROWS]
    _record("signals_near_point", len(rows), {"radius_meters": radius, "days_back": window_days})
    return _payload(
        {
            "applied_bounds": {
                "radius_meters": radius,
                "days_back": window_days,
                "day_from": day_from,
                "day_through": day_through,
                "signal_names": names,
                "max_summary_rows": DEFAULT_SUMMARY_ROWS,
                "max_days_back": MAX_DAYS_BACK,
                **_scanned_bounds(scanned),
            },
            "signal_summaries": rows,
            "window_day_states": _window_states(window, day_from, day_through),
            "note": (
                "Read from the Parquet signal plane the map itself paints from, so this can never "
                "disagree with what the user sees. It covers the 19 signal names under contract, "
                "and only readings the ingest lane accepted. window_day_states says what every day "
                "of the window IS: published days held rows, governed_absence days were looked at "
                "and the upstream had nothing, and day_not_written days were never written at all "
                "-- so an empty signal_summaries with governed_absence days is a measured absence "
                "and one with day_not_written days is a gap about which nothing follows. "
                "days_back is a SCAN BUDGET, not the depth of the record: the lane may run years "
                "deeper than max_days_back. observation_coverage_on_day is MEANT to answer how much "
                "deeper, but it currently refuses on every lane -- no lane has a published "
                "availability receipt yet -- so that is not a working next step today. "
                "A signal outside the governed contract is absent here because it is out of scope, "
                "not because it was unmeasured."
            ),
        }
    )


@_refuses_serving_faults("drought_history_at_point")
async def query_drought_history_at_point(
    longitude: float,
    latitude: float,
    weeks_back: int = DEFAULT_WEEKS_BACK,
    as_of: datetime | None = None,
) -> str:
    """Return the weekly U.S. Drought Monitor severity covering a point, from the Parquet release lane."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("drought_history_at_point")
    window_weeks = _clamp_int(weeks_back, 1, MAX_WEEKS_BACK)
    reference = (as_of or datetime.now(UTC)).date()
    valid_date_from = reference - timedelta(days=window_weeks * _DAYS_PER_WEEK)
    lane = surface_lanes("drought-areas")[0]
    # One year below the window as well, so the OLDEST release in the answer can still name the
    # release before it. prev_valid_date came from a PostgreSQL index over the whole table; here it
    # comes from the listing, which has to reach past the window's floor to see the same neighbour.
    window = await warehouse.lane_years(layer=lane, years=range(valid_date_from.year - 1, reference.year + 1))
    if not window.lane_written:
        return _lane_never_written_refusal("drought_history_at_point", (lane,))
    releases = sorted(window.statuses.data)
    in_window = [day for day in releases if valid_date_from <= day <= reference]
    window.raise_on_unserveable(in_window)
    scanned = warehouse.narrow_to_budget(in_window, requested_from=valid_date_from, requested_through=reference)
    severity = await warehouse.scan(
        parquet_reads.DROUGHT_RELEASE_SEVERITY,
        [longitude, latitude],
        part_keys=window.part_keys(scanned.days),
        operation="agent_drought_history_at_point",
        layer=lane,
        required_columns=DROUGHT_LANE_COLUMNS,
    )
    rows = _drought_rows(severity, releases=releases, scanned=scanned.days)
    covered = [row for row in rows if row.get("severity_class") is not None]
    _record(
        "drought_history_at_point",
        len(rows),
        {"weeks_back": window_weeks, "releases_with_drought": len(covered)},
    )
    return _payload(
        {
            "applied_bounds": {
                "weeks_back": window_weeks,
                "valid_date_from": valid_date_from,
                "max_releases": MAX_WEEKS_BACK,
                "max_weeks_back": MAX_WEEKS_BACK,
                **_scanned_bounds(scanned),
            },
            "severity_scale": (
                "0 = D0 abnormally dry, 1 = D1 moderate, 2 = D2 severe, 3 = D3 extreme, 4 = D4 exceptional"
            ),
            "weekly_severity": rows,
            "releases_returned": len(rows),
            "releases_with_drought_over_point": len(covered),
            "note": (
                "One row per PUBLISHED U.S. Drought Monitor release inside the scanned span, read "
                "from the Parquet drought lane -- the same lane the map paints. Every release in "
                "the span appears, including ones that published no drought class over this point: "
                "those carry severity_class null and covering_class_count 0, which means 'this "
                "release existed and found no drought here', a fact. An EMPTY weekly_severity list "
                "is a different claim entirely -- it means no release was published in the span at "
                "all, so nothing is known either way, and you must not report that as the absence "
                "of drought. prev_valid_date and next_valid_date give the neighbouring releases so "
                "a day between two Tuesdays can be answered with the real gap stated. weeks_back "
                "is a SCAN BUDGET rather than the depth of the record; observation_coverage_on_day "
                "is MEANT to answer how far the lane runs but currently refuses on every lane -- no "
                "lane has a published availability receipt yet -- so that is not a working next "
                "step today."
            ),
        }
    )


def _drought_rows(
    severity: list[dict[str, Any]],
    *,
    releases: Sequence[date],
    scanned: Sequence[date],
) -> list[dict[str, Any]]:
    """Attach each scanned release's neighbours, taken from the lane's whole listed release set."""
    ordered = list(releases)
    position = {day: index for index, day in enumerate(ordered)}
    scanned_days = set(scanned)
    rows: list[dict[str, Any]] = []
    for entry in severity:
        valid_date = entry["valid_date"]
        if valid_date not in scanned_days:
            # A part file whose own valid_date disagrees with the partition day it was written
            # under. Dropping it is the fail-closed direction: the caller asked about the days it
            # scanned, and a row from another day would answer a question nobody put.
            continue
        index = position.get(valid_date)
        rows.append(
            {
                "valid_date": valid_date,
                "prev_valid_date": ordered[index - 1] if index else None,
                "next_valid_date": ordered[index + 1] if index is not None and index + 1 < len(ordered) else None,
                "published_class_count": entry["published_class_count"],
                "severity_class": entry["severity_class"],
                "covering_class_count": entry["covering_class_count"],
                "published_at": entry["published_at"],
            }
        )
    rows.sort(key=lambda row: row["valid_date"], reverse=True)
    return rows


@_refuses_serving_faults("fire_history_near_point")
async def query_fire_history_near_point(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    years_back: int = DEFAULT_FIRE_YEARS_BACK,
    as_of: datetime | None = None,
) -> str:
    """Summarise served fire detections and burn perimeters near a point, lane by lane."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("fire_history_near_point")
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    window_years = _clamp_int(years_back, 1, MAX_FIRE_YEARS_BACK)
    reference = (as_of or datetime.now(UTC)).date()
    observed_day_from = reference - timedelta(days=window_years * _DAYS_PER_YEAR)
    history = {lane.layer: lane for lane in await warehouse.lane_evidence(FIRE_LANE_NAMES)}
    summaries: list[dict[str, Any]] = []
    scanned_spans: dict[str, Any] = {}
    for lane in FIRE_LANE_NAMES:
        window = await warehouse.lane_window(layer=lane, first_day=observed_day_from, last_day=reference)
        if not window.lane_written:
            summaries.append(
                {
                    "layer_name": lane,
                    "lane_state": "never_written",
                    "layer_history": _lane_history(history.get(lane)),
                }
            )
            continue
        window.raise_on_unserveable(_day_span(observed_day_from, reference))
        scanned = warehouse.narrow_to_budget(
            window.published_days(observed_day_from, reference),
            requested_from=observed_day_from,
            requested_through=reference,
        )
        scanned_spans[lane] = _scanned_bounds(scanned)
        rows = await _lane_rows(
            lane,
            part_keys=window.part_keys(scanned.days),
            longitude=longitude,
            latitude=latitude,
            radius_meters=radius,
            row_limit=MAX_FIRE_FEATURE_FANOUT,
            operation="agent_fire_history_near_point",
        )
        summaries.append(
            _fire_lane_summary(
                lane,
                rows,
                evidence=history.get(lane),
                day_states=_window_states(window, observed_day_from, reference),
            )
        )
    _record("fire_history_near_point", len(summaries), {"radius_meters": radius, "years_back": window_years})
    return _payload(
        {
            "applied_bounds": {
                "radius_meters": radius,
                "years_back": window_years,
                "observed_day_from": observed_day_from,
                "lane_names": list(FIRE_LANE_NAMES),
                "max_features_scanned": MAX_FIRE_FEATURE_FANOUT,
                "max_years_back": MAX_FIRE_YEARS_BACK,
                "scanned_spans": scanned_spans,
            },
            "layer_summaries": summaries,
            "note": (
                "feature_count, nearest_distance_m and the earliest/latest_observed_day pair are "
                "scoped to the radius and to the scanned span. distance_basis says what "
                "nearest_distance_m measured: 'point' is the exact geodesic distance to a "
                "detection cell's centre, 'centroid' is the distance to a perimeter polygon's "
                "centroid, because the warehouse reader has no geodesic distance to a polygon "
                "edge -- covers_probe_point is the exact test for whether a perimeter contains "
                "the point. layer_history describes the WHOLE lane and is not scoped to the "
                "radius, so a lane whose latest_day is months old has stopped ingesting, which is "
                "a different fact from there being no fire near this point; when it reports "
                "withheld, the lane could not prove its history and nothing follows about its "
                "depth. years_back is a SCAN BUDGET rather than the depth of the record, and the "
                "hardest-clamped one of the three: observation_coverage_on_day is MEANT to answer "
                "how much deeper this lane runs but currently refuses on every lane -- no lane has "
                "a published availability receipt yet -- so that is not a working next step today. "
                "A satellite detection is a thermal anomaly, not a confirmed fire perimeter."
            ),
        }
    )


def _fire_lane_summary(
    lane: str,
    rows: list[dict[str, Any]],
    *,
    evidence: LaneEvidence | None,
    day_states: dict[str, int],
) -> dict[str, Any]:
    """Fold one lane's in-radius rows into the summary shape, beside its whole-lane history."""
    count_column = FIRE_LANE_FEATURE_COUNT_COLUMN.get(lane)
    feature_count = sum(int(row.get(count_column) or 0) for row in rows) if count_column else len(rows)
    distances = [row["distance_meters"] for row in rows if row.get("distance_meters") is not None]
    observed_days = [row["observed_day"] for row in rows if row.get("observed_day") is not None]
    return {
        "layer_name": lane,
        "lane_state": "written",
        "feature_count": feature_count,
        "row_count": len(rows),
        "nearest_distance_m": round(min(distances), 1) if distances else None,
        "distance_basis": rows[0]["distance_basis"] if rows else None,
        "covers_probe_point": any(bool(row.get("covers_probe_point")) for row in rows),
        "earliest_observed_day": min(observed_days) if observed_days else None,
        "latest_observed_day": max(observed_days) if observed_days else None,
        "features_truncated": len(rows) >= MAX_FIRE_FEATURE_FANOUT,
        "layer_history": _lane_history(evidence),
        "window_day_states": day_states,
    }


def _lane_history(evidence: LaneEvidence | None) -> dict[str, Any]:
    """Describe a whole lane's published history, or state that it could not prove one.

    A DISCRIMINATED SHAPE, not three nullable fields. A lane whose index is withheld and a lane with
    genuinely no days would otherwise render identically as nulls, and that is the same collapse the
    four warehouse states exist to prevent.
    """
    if evidence is None or not evidence.proven:
        return {
            "state": "withheld",
            "reason": (
                "not_a_registered_parquet_lane"
                if evidence is not None and evidence.unregistered
                else (evidence.withheld_reason if evidence is not None else "availability_unpublished")
            ),
        }
    published = evidence.published_days
    return {
        "state": "available",
        "lane_nature": evidence.nature,
        "earliest_day": published[0] if published else None,
        "latest_day": published[-1] if published else None,
        "published_day_count": len(published),
        "source_ceiling_day": evidence.source_ceiling_day,
        "coverage_authority": "availability",
    }


async def _lane_rows(  # noqa: PLR0913 - one argument per coordinate of a bounded lane read
    lane: str,
    *,
    part_keys: Sequence[str],
    longitude: float,
    latitude: float,
    radius_meters: float,
    row_limit: int,
    operation: str,
) -> list[dict[str, Any]]:
    """Read the nearest rows of one lane, choosing the statement from its REGISTERED spatial support.

    `spatial_support` is imported from the serving reader rather than re-derived, so the agent and
    the map agree about which column a lane's position lives in. A lane that declares neither a
    coordinate pair nor a WKB column is refused rather than answered for the whole world.
    """
    support = spatial_support(lane, "observed")
    west, south, east, north = _bbox_bounds(longitude, latitude, radius_meters)
    if isinstance(support, PointSupport):
        rows = await warehouse.scan(
            parquet_reads.point_lane_rows(support),
            # The probe is LATITUDE FIRST for the geodesic distance; see `_signal_scope_parameters`.
            [west, east, south, north, latitude, longitude, radius_meters, row_limit],
            part_keys=part_keys,
            operation=operation,
            layer=lane,
            required_columns=(support.longitude_column, support.latitude_column),
        )
        for row in rows:
            row["distance_basis"] = "point"
            row["centroid_longitude"] = row.get(support.longitude_column)
            row["centroid_latitude"] = row.get(support.latitude_column)
        return rows
    if isinstance(support, GeometrySupport):
        rows = await warehouse.scan(
            parquet_reads.geometry_lane_rows(support),
            # Envelope in geometry order, then the probe LATITUDE FIRST for the distance, then the
            # probe the ordinary way round for the exact point-in-polygon test.
            [west, south, east, north, latitude, longitude, longitude, latitude, row_limit],
            part_keys=part_keys,
            operation=operation,
            layer=lane,
            required_columns=(support.geometry_column,),
        )
        for row in rows:
            row["distance_basis"] = "centroid"
            row["distance_meters"] = row.pop("centroid_distance_meters", None)
        return rows
    raise _no_spatial_support(lane, support)


def _no_spatial_support(lane: str, support: NoSpatialSupport) -> ServingRefusalError:
    """Refuse a proximity question of a lane with no spatial extent, rather than answering the world."""
    return ServingRefusalError(
        "bbox_unsupported",
        f"{lane} cannot answer a proximity question: {support.reason}. Answering the whole world "
        "to a radius request would silently widen the answer",
    )


@_refuses_serving_faults("forecast_summary_for_cell")
async def query_forecast_summary_for_cell(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    metric_names: list[str] | None = None,
    as_of: datetime | None = None,
) -> str:
    """Return published daily forecast values for the analysis cell nearest a point."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("forecast_summary_for_cell")
    unbuilt = await _unbuilt_planes([FORECAST_DAILY_RELATION])
    if unbuilt:
        return _plane_refusal("forecast_summary_for_cell", unbuilt)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    names = _clean_names(metric_names)
    reference = as_of or datetime.now(UTC)
    valid_day_from = datetime(reference.year, reference.month, reference.day, tzinfo=UTC)
    through_day = reference.date()
    first_day = through_day - timedelta(days=CELL_UNIVERSE_DAYS)
    cell_window = await warehouse.lane_window(layer=SIGNAL_PLANE_LANE, first_day=first_day, last_day=through_day)
    if not cell_window.lane_written:
        # A lane that never wrote anything is state 4 of 4, never an absence: reporting resolved_cell
        # as null here would let the payload's own note assert a cause -- "no cell reported nearby" --
        # that is not the cause. Refuse before the forecast read even starts.
        return _lane_never_written_refusal("forecast_summary_for_cell", (SIGNAL_PLANE_LANE,))
    cells = await _admitted_signal_cells(
        cell_window,
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius,
        first_day=first_day,
        through_day=through_day,
        operation="agent_forecast_summary_for_cell",
    )
    rows: list[dict[str, Any]] = []
    nearest = cells[0] if cells else None
    if nearest is not None:
        rows = await _fetch(
            _FORECAST_SQL,
            {
                "cell_id": str(nearest["cell_id"]),
                "cell_distance_m": float(nearest["distance_meters"]),
                "valid_day_from": valid_day_from,
                "metric_names": names,
                "row_limit": DEFAULT_FORECAST_ROWS,
            },
        )
    _record("forecast_summary_for_cell", len(rows), {"radius_meters": radius})
    return _payload(
        {
            "applied_bounds": {
                "radius_meters": radius,
                "valid_day_from": valid_day_from,
                "metric_names": names,
                "max_rows": DEFAULT_FORECAST_ROWS,
                "cell_universe_days": CELL_UNIVERSE_DAYS,
            },
            "resolved_cell": nearest,
            "forecast_values": rows,
            "note": (
                "Only published, finalized, validated forecasts are visible here, pre-aggregated "
                "to one row per valid DAY -- mean_point_value with the widest p10/p90 band the "
                "day's steps reported, and contributing_forecast_points saying how many steps "
                "that was. This view covers ML-method forecasts on series enabled for daily "
                "aggregation ONLY, so a published forecast produced another way is out of scope "
                "here rather than absent. resolved_cell is null when NO analysis cell reported "
                "inside the radius in the last cell_universe_days, which is a statement about "
                "which cells have reported recently and not about the forecast plane. The "
                "forecast plane itself is a governed ML relation in PostgreSQL, not environmental "
                "data, and is deliberately not a Parquet lane."
            ),
        }
    )


async def _admitted_signal_cells(  # noqa: PLR0913 - one coordinate per bounded read; caller pre-resolves window
    window: LaneWindow,
    *,
    longitude: float,
    latitude: float,
    radius_meters: float,
    first_day: date,
    through_day: date,
    operation: str,
) -> list[dict[str, Any]]:
    """The analysis cells that reported near a point recently, nearest first, from the signal plane.

    Takes an already-resolved `window` rather than resolving its own: the caller must decide what
    `lane_never_written` means BEFORE this runs, because an empty return here is indistinguishable
    from "no cell reported nearby" and the two are different claims.
    """
    scanned = warehouse.narrow_to_budget(
        window.published_days(first_day, through_day),
        requested_from=first_day,
        requested_through=through_day,
    )
    return await warehouse.scan(
        parquet_reads.SIGNAL_ADMITTED_CELLS,
        _signal_scope_parameters(longitude, latitude, radius_meters),
        part_keys=window.part_keys(scanned.days),
        operation=operation,
        layer=SIGNAL_PLANE_LANE,
        required_columns=SIGNAL_PLANE_COLUMNS,
    )


@_refuses_serving_faults("signal_value_on_day")
async def query_signal_value_on_day(
    longitude: float,
    latitude: float,
    day: str,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    signal_names: list[str] | None = None,
) -> str:
    """Report what each governed signal measured on exactly the caller's day, and the audit for it."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("signal_value_on_day")
    selected_day = _parse_day(day)
    if selected_day is None:
        return _day_error("signal_value_on_day", day)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    names = _clean_names(signal_names)
    window = await warehouse.lane_window(layer=SIGNAL_PLANE_LANE, first_day=selected_day, last_day=selected_day)
    if not window.lane_written:
        return _lane_never_written_refusal("signal_value_on_day", (SIGNAL_PLANE_LANE,))
    window.raise_on_unserveable([selected_day])
    state = window.state_of(selected_day)
    measured: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    if state == "published":
        measured, cells = await warehouse.scan_all(
            (
                (parquet_reads.SIGNAL_DAY_VALUES, _signal_scope_parameters(longitude, latitude, radius)),
                (parquet_reads.SIGNAL_ADMITTED_CELLS, _signal_scope_parameters(longitude, latitude, radius)),
            ),
            part_keys=window.part_keys([selected_day]),
            operation="agent_signal_value_on_day",
            layer=SIGNAL_PLANE_LANE,
            required_columns=SIGNAL_PLANE_COLUMNS,
        )
    rows = _filter_by_name(measured, names, "signal_name")[:MAX_DAY_SUMMARY_ROWS]
    governed = await _signal_coverage_audit(cells, day=selected_day, names=names)
    _record(
        "signal_value_on_day",
        len(rows),
        {"requested_day": selected_day, "radius_meters": radius, "coverage_audit_rows": len(governed)},
    )
    return _payload(
        {
            "requested_day": selected_day,
            "applied_bounds": {
                "requested_day": selected_day,
                "radius_meters": radius,
                "signal_names": names,
                "max_summary_rows": MAX_DAY_SUMMARY_ROWS,
                "max_coverage_audit_rows": MAX_COVERAGE_AUDIT_ROWS,
                "max_cells_scanned": MAX_CELL_FANOUT,
            },
            "day_state": await _day_state(window, selected_day, state),
            "signals_on_day": rows,
            "signals_on_day_truncated": len(rows) >= MAX_DAY_SUMMARY_ROWS,
            "coverage_audit_on_day": governed,
            "coverage_audit_on_day_truncated": len(governed) >= MAX_COVERAGE_AUDIT_ROWS,
            "cells_in_radius": len(cells),
            "note": (
                "Every row in signals_on_day was measured ON requested_day and on no other day; "
                "nothing here is borrowed from a neighbouring day, because the day IS the Parquet "
                "partition that was read and no timestamp was ever cast to a date to decide it. "
                "day_state is the warehouse's own verdict and must be read BEFORE the rows: "
                "published means the day holds data, governed_absence means the lane looked and "
                "the upstream had nothing (its evidence says why), day_not_written means the day "
                "was never written and NOTHING follows from the empty list. A signal absent from a "
                "published day had no accepted reading UNLESS signals_on_day_truncated is true, in "
                "which case the list hit its row cap. coverage_audit_on_day is what the ingest "
                "lane recorded for a window covering the day, read over exactly the cells this "
                "answer came from: status no_data means the upstream published nothing, partial "
                "means fewer cells landed than expected, and an empty audit means nothing was "
                "recorded either way. For the nearest days that do carry a reading call "
                "signal_neighbors_in_time, and never quote one of those as this day's value."
            ),
        }
    )


async def _day_state(window: LaneWindow, day: date, state: str) -> dict[str, Any]:
    """Render the warehouse's verdict on one day, carrying a governed absence's own evidence.

    An absence is only ever reported WITH its marker: `read_absence_evidence` fails closed when the
    marker is unreadable or undecodable, and that refusal is the right answer, because an absence
    with no evidence is indistinguishable from a silent failure.
    """
    verdict: dict[str, Any] = {"state": state, "lane": window.layer, "zoom_tier": window.tier}
    if state == "governed_absence":
        evidence = await warehouse.absence_evidence(window, day)
        verdict["absence"] = {
            "reason": evidence.reason,
            "upstream_response": evidence.upstream_response,
            "recorded_at": evidence.recorded_at,
            "run_id": evidence.run_id,
        }
    return verdict


async def _signal_coverage_audit(
    cells: Sequence[dict[str, Any]],
    *,
    day: date,
    names: Sequence[str],
) -> list[dict[str, Any]]:
    """Read the ingest lane's absence ledger over exactly the cells the value answer came from.

    STILL A POSTGRESQL READ, and deliberately. `agri.signal_coverage_audit` is a governance record
    of what an upstream was asked for and what it answered -- the retirement inventory classes it
    "keep" -- and it is the one question the Parquet plane cannot answer: a governed-absence marker
    settles a whole lane-day, while this ledger is grained by signal, cell and fetched window and
    says WHY nothing landed for one of them.
    """
    if not cells:
        return []
    day_start, day_end = _day_bounds(day)
    return await _fetch(
        _COVERAGE_ON_DAY_SQL,
        {
            "cell_ids": [str(cell["cell_id"]) for cell in cells],
            "cell_distances": [float(cell["distance_meters"]) for cell in cells],
            "day_start": day_start,
            "day_end": day_end,
            "signal_names": list(names),
            "row_limit": MAX_COVERAGE_AUDIT_ROWS,
        },
    )


@_refuses_serving_faults("signal_neighbors_in_time")
async def query_signal_neighbors_in_time(  # noqa: PLR0913 - the parameter list is the published tool schema.
    longitude: float,
    latitude: float,
    day: str,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    neighbor_days: int = DEFAULT_NEIGHBOR_DAYS,
    signal_names: list[str] | None = None,
) -> str:
    """Return the nearest accepted reading each side of the caller's day, carrying its real gap."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("signal_neighbors_in_time")
    selected_day = _parse_day(day)
    if selected_day is None:
        return _day_error("signal_neighbors_in_time", day)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    window_days = _clamp_int(neighbor_days, 1, MAX_NEIGHBOR_DAYS)
    names = _clean_names(signal_names)
    search_from = selected_day - timedelta(days=window_days)
    search_through = selected_day + timedelta(days=window_days)
    window = await warehouse.lane_window(layer=SIGNAL_PLANE_LANE, first_day=search_from, last_day=search_through)
    if not window.lane_written:
        return _lane_never_written_refusal("signal_neighbors_in_time", (SIGNAL_PLANE_LANE,))
    window.raise_on_unserveable(_day_span(search_from, search_through))
    neighbours = [day for day in window.published_days(search_from, search_through) if day != selected_day]
    scanned = warehouse.narrow_to_budget(neighbours, requested_from=search_from, requested_through=search_through)
    measured = await warehouse.scan(
        parquet_reads.SIGNAL_TIME_NEIGHBORS,
        # The day is bound FOUR times: DuckDB counts positional parameters by appearance, and the
        # statement names this one day in both arms and in both gap expressions.
        [*_signal_scope_parameters(longitude, latitude, radius), *([selected_day] * 4)],
        part_keys=window.part_keys(scanned.days),
        operation="agent_signal_neighbors_in_time",
        layer=SIGNAL_PLANE_LANE,
        required_columns=SIGNAL_PLANE_COLUMNS,
    )
    rows = _filter_by_name(measured, names, "signal_name")[:MAX_TEMPORAL_NEIGHBOR_ROWS]
    _record(
        "signal_neighbors_in_time",
        len(rows),
        {"requested_day": selected_day, "radius_meters": radius, "neighbor_days": window_days},
    )
    return _payload(
        {
            "requested_day": selected_day,
            "applied_bounds": {
                "requested_day": selected_day,
                "radius_meters": radius,
                "neighbor_days": window_days,
                "searched_from": search_from,
                "searched_through": search_through,
                "signal_names": names,
                "max_rows": MAX_TEMPORAL_NEIGHBOR_ROWS,
                **_scanned_bounds(scanned),
            },
            "temporal_neighbors": rows,
            "temporal_neighbors_truncated": len(rows) >= MAX_TEMPORAL_NEIGHBOR_ROWS,
            "window_day_states": _window_states(window, search_from, search_through),
            "note": (
                "Each row is the nearest accepted reading on a day OTHER than requested_day. "
                "side says whether it precedes or follows, observed_day is that reading's own "
                "date, distance_days is the real gap in days and day_offset the same gap signed, "
                "and nearest_cell_distance_m is how far its cell sits from the point. Never "
                "report one of these as the value on requested_day -- say which day it came from "
                "and how far away that is. A signal missing its before or after row has no "
                "accepted reading on that side between scanned_from and scanned_through, which is "
                "a statement about the days actually read and not about all of history -- UNLESS "
                "temporal_neighbors_truncated is true, in which case the list hit its row cap and "
                "the missing side may simply have been cut. window_day_states says how many days "
                "in the window were governed absences and how many were never written at all."
            ),
        }
    )


@_refuses_serving_faults("nearest_signal_cells")
async def query_nearest_signal_cells(  # noqa: PLR0913 - the parameter list is the published tool schema.
    longitude: float,
    latitude: float,
    day: str,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    cell_count: int = DEFAULT_NEAREST_CELLS,
    grid_names: list[str] | None = None,
) -> str:
    """List the analysis cells nearest a point with their real distances and what they hold that day."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("nearest_signal_cells")
    selected_day = _parse_day(day)
    if selected_day is None:
        return _day_error("nearest_signal_cells", day)
    grids = _clean_names(grid_names)
    if grids:
        return _grid_filter_refusal(grids)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    returned_cells = _clamp_int(cell_count, 1, MAX_NEAREST_CELLS)
    universe_from = selected_day - timedelta(days=CELL_UNIVERSE_DAYS)
    window = await warehouse.lane_window(layer=SIGNAL_PLANE_LANE, first_day=universe_from, last_day=selected_day)
    if not window.lane_written:
        return _lane_never_written_refusal("nearest_signal_cells", (SIGNAL_PLANE_LANE,))
    window.raise_on_unserveable(_day_span(universe_from, selected_day))
    scanned = warehouse.narrow_to_budget(
        window.published_days(universe_from, selected_day),
        requested_from=universe_from,
        requested_through=selected_day,
    )
    rows = await warehouse.scan(
        parquet_reads.SIGNAL_CELL_DAY_COUNTS,
        [*_signal_scope_parameters(longitude, latitude, radius), selected_day, returned_cells],
        part_keys=window.part_keys(scanned.days),
        operation="agent_nearest_signal_cells",
        layer=SIGNAL_PLANE_LANE,
        required_columns=SIGNAL_PLANE_COLUMNS,
    )
    _record(
        "nearest_signal_cells",
        len(rows),
        {"requested_day": selected_day, "radius_meters": radius, "cell_count": returned_cells},
    )
    return _payload(
        {
            "requested_day": selected_day,
            "applied_bounds": {
                "requested_day": selected_day,
                "radius_meters": radius,
                "cell_count": returned_cells,
                "grid_names": grids,
                "max_cells_scanned": MAX_CELL_FANOUT,
                "cell_universe_days": CELL_UNIVERSE_DAYS,
                **_scanned_bounds(scanned),
            },
            "day_state": await _day_state(window, selected_day, window.state_of(selected_day)),
            "nearest_cells": rows,
            "note": (
                "Cells are ordered by their real distance from the requested point, measured to "
                "the cell centroid in metres on the curved earth, and are listed whether or not "
                "they hold anything on requested_day. THE CELL LIST IS OBSERVED, NOT DECLARED: "
                "agri.spatial_cell, the registry that used to answer 'which cells exist here', is "
                "gone, so a cell appears only if it reported at least once in the "
                "cell_universe_days before the requested day. A cell that has been silent longer "
                "than that is missing from this list, which is a statement about recent reporting "
                "and not about the grid. cell_id is the plane's own identifier; the grid name and "
                "resolution the registry carried have no Parquet source and are deliberately "
                "omitted rather than returned empty. observation_count_on_day counts rows on the "
                "GOVERNED SIGNAL plane only, so a 0 is not a claim that the cell is empty of "
                "everything -- read day_state first, because on a governed_absence or "
                "day_not_written day every count is 0 for a reason that has nothing to do with "
                "the cells."
            ),
        }
    )


def _grid_filter_refusal(grids: Sequence[str]) -> str:
    """Refuse a grid filter the Parquet signal plane cannot apply, rather than ignoring it."""
    _record("nearest_signal_cells", 0, {"error": "grid_filter_unavailable", "grid_names": list(grids)})
    return _payload(
        {
            "error": "grid_filter_unavailable",
            "received_grid_names": list(grids),
            "note": (
                "This is a REFUSAL, not an absence. The grid a cell belongs to was a column of "
                "agri.spatial_cell, which has been retired; the Parquet signal plane carries each "
                "cell's id and centroid but no grid name, so this filter cannot be applied. "
                "Answering while silently ignoring the filter would report cells from every grid "
                "as though they were the grid asked for. Call this tool again without grid_names "
                "to get every cell near the point."
            ),
        }
    )


@_refuses_serving_faults("observation_coverage_on_day")
async def query_observation_coverage_on_day(
    surface_name: str,
    day: str,
) -> str:
    """Say whether one catalogue surface covers the caller's day, and how that sits in its history."""
    selected_day = _parse_day(day)
    if selected_day is None:
        return _day_error("observation_coverage_on_day", day)
    surface = surface_name.strip()[:MAX_NAME_LENGTH]
    if surface not in AGENT_SURFACE_NAMES:
        return _surface_error("observation_coverage_on_day", surface_name)
    lanes = surface_lanes(surface)
    if not lanes:
        return _surface_not_on_parquet("observation_coverage_on_day", surface)
    evidence = await warehouse.lane_evidence(lanes)
    if any(not lane.proven for lane in evidence):
        return _availability_refusal("observation_coverage_on_day", evidence)
    covered = warehouse.surface_covered_days(evidence)
    coverage = _surface_coverage(surface, selected_day, evidence=evidence, covered=covered)
    _record(
        "observation_coverage_on_day",
        1,
        {"surface_name": surface, "requested_day": selected_day, "is_covered": bool(coverage["is_covered"])},
    )
    return _payload(
        {
            "requested_day": selected_day,
            "surface_name": surface,
            "coverage": coverage,
            "note": (
                "Read from each lane's published availability index -- the same evidence the map's "
                "time slider is built from -- so this cannot disagree with which days the slider "
                "offers. A surface backed by several lanes is covered only on days EVERY lane "
                "published, because a day one depth or one statistic is missing is a day the map "
                "cannot draw. is_covered false is a fact about the day, and the history fields say "
                "which KIND of absence it is: a day before earliest_observed_day is outside this "
                "lane's published history, a day after source_ceiling_day is past what the source "
                "itself could have published and may simply not exist yet, and a day between the "
                "two is a genuine hole. lane_states names each lane's own verdict for the day, "
                "including a governed absence's recorded reason. For the nearest days that ARE "
                "covered call observation_temporal_neighbors."
            ),
        }
    )


def _surface_not_on_parquet(tool_name: str, surface: str) -> str:
    """Refuse a surface that has no Parquet lane, naming where it does live."""
    _record(tool_name, 0, {"error": "surface_not_served_from_parquet", "surface_name": surface})
    return _payload(
        {
            "error": "surface_not_served_from_parquet",
            "received_surface_name": surface,
            "postgresql_only_surface_names": list(POSTGRESQL_ONLY_SURFACE_NAMES),
            "note": (
                "This is a REFUSAL, not an absence. Coverage is answered from the Parquet "
                "availability index, and this surface has no Parquet lane: it is community data a "
                "user writes rather than environmental data an upstream publishes, so it stays in "
                "PostgreSQL and has no published day index. feature_value_near_point can still "
                "list its features for a day. Nothing follows about whether the surface holds "
                "anything."
            ),
        }
    )


def _surface_coverage(
    surface: str,
    day: date,
    *,
    evidence: Sequence[LaneEvidence],
    covered: frozenset[date],
) -> dict[str, Any]:
    """Fold every lane behind a surface into one coverage verdict for one day."""
    ordered = sorted(covered)
    lane_states = []
    observation_count = 0
    for lane in evidence:
        entry = lane.days.get(day)
        lane_states.append(
            {
                "lane": lane.layer,
                "lane_nature": lane.nature,
                "state": entry.state if entry is not None else "not_published",
                "row_count": entry.row_count if entry is not None else 0,
                "published_at": entry.published_at if entry is not None else None,
                "absence_reason": entry.absence_reason if entry is not None else None,
                "source_ceiling_day": lane.source_ceiling_day,
            }
        )
        if entry is not None and entry.state == "published":
            observation_count += entry.row_count
    return {
        "surface_name": surface,
        "requested_day": day,
        "is_covered": day in covered,
        "observation_count": observation_count,
        "lane_states": lane_states,
        "earliest_observed_day": ordered[0] if ordered else None,
        "latest_observed_day": ordered[-1] if ordered else None,
        "observed_day_count": len(ordered),
        "source_ceiling_day": min(
            (lane.source_ceiling_day for lane in evidence if lane.source_ceiling_day is not None),
            default=None,
        ),
        "coverage_authority": "availability",
    }


@_refuses_serving_faults("observation_temporal_neighbors")
async def query_observation_temporal_neighbors(
    surface_name: str,
    day: str,
    neighbor_days: int = DEFAULT_SURFACE_NEIGHBOR_DAYS,
) -> str:
    """Return the nearest covered day each side of the caller's day, carrying its real gap."""
    selected_day = _parse_day(day)
    if selected_day is None:
        return _day_error("observation_temporal_neighbors", day)
    surface = surface_name.strip()[:MAX_NAME_LENGTH]
    if surface not in AGENT_SURFACE_NAMES:
        return _surface_error("observation_temporal_neighbors", surface_name)
    lanes = surface_lanes(surface)
    if not lanes:
        return _surface_not_on_parquet("observation_temporal_neighbors", surface)
    evidence = await warehouse.lane_evidence(lanes)
    if any(not lane.proven for lane in evidence):
        return _availability_refusal("observation_temporal_neighbors", evidence)
    window_days = _clamp_int(neighbor_days, 1, MAX_SURFACE_NEIGHBOR_DAYS)
    search_from = selected_day - timedelta(days=window_days)
    search_through = selected_day + timedelta(days=window_days)
    covered = warehouse.surface_covered_days(evidence)
    rows = _surface_neighbors(
        surface,
        selected_day,
        evidence=evidence,
        covered=covered,
        search_from=search_from,
        search_through=search_through,
    )
    _record(
        "observation_temporal_neighbors",
        len(rows),
        {"surface_name": surface, "requested_day": selected_day, "neighbor_days": window_days},
    )
    return _payload(
        {
            "requested_day": selected_day,
            "surface_name": surface,
            "applied_bounds": {
                "requested_day": selected_day,
                "neighbor_days": window_days,
                "searched_from": search_from,
                "searched_through": search_through,
            },
            "temporal_neighbors": rows,
            "note": (
                "At most two rows -- the nearest COVERED day before requested_day and the nearest "
                "one after it. distance_days is the real gap and day_offset the same gap signed. "
                "These are neighbours, never answers: say 'the nearest observation is six days "
                "earlier', never quote one as the value on requested_day. A missing side has no "
                "covered day on that side between searched_from and searched_through, which is a "
                "statement about the window searched and not about all of history."
            ),
        }
    )


def _surface_neighbors(  # noqa: PLR0913 - one argument per coordinate of the neighbour question
    surface: str,
    day: date,
    *,
    evidence: Sequence[LaneEvidence],
    covered: frozenset[date],
    search_from: date,
    search_through: date,
) -> list[dict[str, Any]]:
    """The nearest covered day each side, each carrying its own gap and how much landed on it."""
    before = [entry for entry in sorted(covered) if search_from <= entry < day]
    after = [entry for entry in sorted(covered) if day < entry <= search_through]
    rows: list[dict[str, Any]] = []
    for side, candidate in (("before", before[-1] if before else None), ("after", after[0] if after else None)):
        if candidate is None:
            continue
        rows.append(
            {
                "side": side,
                "surface_name": surface,
                "observed_day": candidate,
                "day_offset": (candidate - day).days,
                "distance_days": abs((candidate - day).days),
                "observation_count": sum(lane.days[candidate].row_count for lane in evidence if candidate in lane.days),
                "lane_count": len(evidence),
            }
        )
    rows.sort(key=lambda row: (row["distance_days"], row["side"]))
    return rows


@_refuses_serving_faults("feature_value_near_point")
async def query_feature_value_near_point(  # noqa: PLR0913 - the parameter list is the published tool schema.
    surface_name: str,
    day: str,
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    feature_count: int = DEFAULT_SURFACE_FEATURE_ROWS,
) -> str:
    """List the nearest published features of one layer dated to the caller's day, with distances."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("feature_value_near_point")
    selected_day = _parse_day(day)
    if selected_day is None:
        return _day_error("feature_value_near_point", day)
    surface = surface_name.strip()[:MAX_NAME_LENGTH]
    if surface not in FEATURE_SURFACE_NAMES:
        return _feature_surface_error(surface_name)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    returned_features = _clamp_int(feature_count, 1, MAX_SURFACE_FEATURE_ROWS)
    if surface in POSTGRESQL_ONLY_SURFACE_NAMES:
        return await _postgresql_features(
            surface,
            selected_day,
            longitude=longitude,
            latitude=latitude,
            radius_meters=radius,
            returned_features=returned_features,
        )
    lane = surface_lanes(surface)[0]
    window = await warehouse.lane_window(layer=lane, first_day=selected_day, last_day=selected_day)
    if not window.lane_written:
        return _lane_never_written_refusal("feature_value_near_point", (lane,))
    window.raise_on_unserveable([selected_day])
    state = window.state_of(selected_day)
    rows: list[dict[str, Any]] = []
    if state == "published":
        rows = await _lane_rows(
            lane,
            part_keys=window.part_keys([selected_day]),
            longitude=longitude,
            latitude=latitude,
            radius_meters=radius,
            row_limit=returned_features,
            operation="agent_feature_value_near_point",
        )
    features = [_feature_row(row, served_day=selected_day) for row in rows]
    _record(
        "feature_value_near_point",
        len(features),
        {"surface_name": surface, "requested_day": selected_day, "radius_meters": radius},
    )
    return _payload(
        {
            "requested_day": selected_day,
            "surface_name": surface,
            "applied_bounds": {
                "requested_day": selected_day,
                "radius_meters": radius,
                "feature_count": returned_features,
                "max_feature_rows": MAX_SURFACE_FEATURE_ROWS,
                "parquet_lane": lane,
                "projected_columns": list(get_stream_schema(lane, "observed").column_names),
                "search_shape": "bounding_box" if _is_geometry_lane(lane) else "radius",
            },
            "day_state": await _day_state(window, selected_day, state),
            "features": features,
            "features_truncated": len(features) >= returned_features,
            "note": (
                "Every feature here is dated to requested_day by the PARTITION it was written "
                "under, which is the same day key the map's own Parquet client asks for, so a "
                "feature the map draws on that day is a feature this can return. Each carries "
                "distance_meters and distance_basis: 'point' is the exact geodesic distance to the "
                "row's own coordinate, 'centroid' is the distance to a polygon's centroid, and for "
                "a polygon lane covers_probe_point is the exact answer to 'is the point inside "
                "this feature'. On a polygon lane the search shape is the BOUNDING BOX around the "
                "radius rather than the circle, so a corner feature slightly beyond the radius can "
                "appear. properties carries the lane's own registered columns, typed, rather than "
                "a projection of a JSON blob. Read day_state before the list: an empty list on a "
                "governed_absence day is a measured absence, an empty list on a day_not_written "
                "day supports no conclusion at all, and an empty list on a published day means "
                "this layer published nothing inside the search box -- call "
                "observation_coverage_on_day to tell those apart."
            ),
        }
    )


def _is_geometry_lane(lane: str) -> bool:
    """True when the lane's registered schema declares WKB rather than a coordinate pair."""
    return isinstance(spatial_support(lane, "observed"), GeometrySupport)


def _feature_row(row: dict[str, Any], *, served_day: date) -> dict[str, Any]:
    """Split one lane row into the fixed proximity fields and the lane's own columns."""
    carried = {
        "served_day": served_day,
        "distance_meters": row.get("distance_meters"),
        "distance_basis": row.get("distance_basis"),
        "centroid_longitude": row.get("centroid_longitude"),
        "centroid_latitude": row.get("centroid_latitude"),
    }
    if "covers_probe_point" in row:
        carried["covers_probe_point"] = row["covers_probe_point"]
    reserved = {
        "distance_meters",
        "distance_basis",
        "centroid_longitude",
        "centroid_latitude",
        "covers_probe_point",
    }
    carried["properties"] = {name: value for name, value in row.items() if name not in reserved}
    return carried


async def _postgresql_features(  # noqa: PLR0913 - one argument per coordinate of the bounded read
    surface: str,
    day: date,
    *,
    longitude: float,
    latitude: float,
    radius_meters: float,
    returned_features: int,
) -> str:
    """Answer for the one community layer that stays in PostgreSQL, in its original shape.

    RUNBOOK section 0.26.1 keeps `interventions` in PostgreSQL: it is data a user writes, not data
    an upstream publishes, so it has no registered Parquet lane and inventing one would be a
    fiction. This is the only PostgreSQL read left in the feature path and it touches no
    environmental layer.
    """
    rows = await _fetch(
        _FEATURE_NEAR_POINT_SQL,
        {
            "surface_name": surface,
            "day": day,
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius_meters,
            "bbox_degrees": _bbox_degrees(radius_meters, latitude),
            "property_keys": list(FEATURE_PROPERTY_KEYS),
            "row_limit": returned_features,
        },
    )
    _record(
        "feature_value_near_point",
        len(rows),
        {"surface_name": surface, "requested_day": day, "radius_meters": radius_meters},
    )
    return _payload(
        {
            "requested_day": day,
            "surface_name": surface,
            "applied_bounds": {
                "requested_day": day,
                "radius_meters": radius_meters,
                "feature_count": returned_features,
                "max_feature_rows": MAX_SURFACE_FEATURE_ROWS,
                "projected_property_keys": list(FEATURE_PROPERTY_KEYS),
                "served_from": "postgresql",
                "search_shape": "radius",
            },
            "features": rows,
            "features_truncated": len(rows) >= returned_features,
            "note": (
                "This layer is community data a user writes, so it is served from PostgreSQL "
                "rather than from a Parquet lane and has no published day index. Every feature is "
                "dated to requested_day by the same rule the map's tiles use, carries "
                "distance_meters from the requested point and its own observed_day, and its "
                "properties are projected to a fixed key list -- a key you do not see was not "
                "requested rather than being empty. An empty list means nothing was published "
                "dated to that day inside the search box."
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

    Returns one summary row per signal, support and unit: how many accepted readings there
    were, over what window, their value range, and how far the nearest contributing analysis
    cell was. Covers the governed signal contract (weather, soil moisture and temperature,
    humidity, radiation and similar) as served to the map itself, so it can never disagree
    with what the user is looking at. Radius, time window and row count are capped by the
    service; values beyond the cap are clamped and the applied bounds are reported back to you.

    The time window is a SCAN BUDGET and not the depth of the record: the lane usually runs years
    deeper than days_back allows in one call. observation_coverage_on_day is meant to answer how far
    back it goes, but it currently refuses on every lane -- no lane has a published availability
    receipt -- so that is not a working next step today.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        radius_meters: Search radius around the point in metres; capped at 50000.
        days_back: How far back to look in days; capped at 120.
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
    """Return the U.S. Drought Monitor severity that covered a coordinate, release by release.

    One row per published weekly release, carrying the highest severity class whose polygon
    covered the point (0 = D0 abnormally dry through 4 = D4 exceptional drought). Releases that
    published no drought class over the point still appear, with a null severity -- that is a
    measured "no drought that week", and it is different from an empty result, which means no
    release was published in the window at all. Each row also carries the previous and next
    release dates so a day between two weekly releases can be answered with the real gap. Read
    from the same drought lane the map paints. The lookback is capped at 120 weeks per call.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        weeks_back: How many weeks of drought history to return; capped at 120.
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

    Returns one row per served fire lane: how many features fell within the radius, how
    close the nearest was, and the calendar day range they span -- plus layer_history, which
    describes the whole lane and tells you whether an empty radius means "no fire here" or "this
    lane stopped ingesting". Satellite detections are thermal anomalies rather than confirmed
    fires; burn perimeters are post-fire mapped boundaries. Radius is capped at 50000 metres and
    the lookback at 2 years per call; observation_coverage_on_day is meant to answer about the
    deeper record but currently refuses on every lane, so that is not a working next step today.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        radius_meters: Search radius around the point in metres; capped at 50000.
        years_back: How many years of fire history to include; capped at 2.
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

    Resolves the point to the single nearest analysis cell that has reported recently, then
    returns that cell's published forecast values aggregated to one row per valid day -- metric,
    unit, issue time, valid day, mean point value, the p10/p90 uncertainty band, and how many
    forecast steps were folded into the day. Only published, finalized and validated forecasts are
    visible, and only machine-learning forecasts on series enabled for daily aggregation. Row
    count is capped at 120.

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


@beta_async_tool
async def signal_value_on_day(
    longitude: float,
    latitude: float,
    day: str,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    signal_names: list[str] | None = None,
) -> str:
    """Report what PlantGeo's governed signals measured near a coordinate on ONE specific day.

    Use this whenever you need a value for the day the map is showing. It answers for that day
    and no other: a signal missing from the result had no accepted reading that day, and nothing
    is ever substituted from a neighbouring day. Each row carries the nearest contributing cell's
    own reading and distance, the spread across every cell in range, and the real timestamps the
    row was built from. Read day_state first -- it says whether the day holds data, was looked at
    and found deliberately empty by the upstream, or was never written at all. The result also
    carries what the ingest lane recorded for that day, so a missing value can be explained.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        day: The calendar day to answer for, as ISO YYYY-MM-DD. Use the day the caller selected.
        radius_meters: Search radius around the point in metres; capped at 50000.
        signal_names: Optional exact signal names to restrict to. Omit for every signal.
    """
    return await query_signal_value_on_day(
        longitude=longitude,
        latitude=latitude,
        day=day,
        radius_meters=radius_meters,
        signal_names=signal_names,
    )


@beta_async_tool
async def signal_neighbors_in_time(  # noqa: PLR0913 - the parameter list is the published tool schema.
    longitude: float,
    latitude: float,
    day: str,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    neighbor_days: int = DEFAULT_NEIGHBOR_DAYS,
    signal_names: list[str] | None = None,
) -> str:
    """Find the nearest reading before and after a given day, each with how far away it really is.

    Call this when signal_value_on_day returned nothing for the day you were asked about. For
    each signal it returns at most two rows -- the closest accepted reading earlier than that day
    and the closest one later -- carrying that reading's own observation date, the real gap in
    days, and the distance of the cell it came from. These are neighbours, never answers: report
    them as "nearest reading is six days earlier", never as the value on the day requested. A
    signal missing a side simply has no reading there within the days actually scanned.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        day: The calendar day to search around, as ISO YYYY-MM-DD.
        radius_meters: Search radius around the point in metres; capped at 50000.
        neighbor_days: How many days each side of the day to search; capped at 180.
        signal_names: Optional exact signal names to restrict to. Omit for every signal.
    """
    return await query_signal_neighbors_in_time(
        longitude=longitude,
        latitude=latitude,
        day=day,
        radius_meters=radius_meters,
        neighbor_days=neighbor_days,
        signal_names=signal_names,
    )


@beta_async_tool
async def nearest_signal_cells(  # noqa: PLR0913 - the parameter list is the published tool schema.
    longitude: float,
    latitude: float,
    day: str,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    cell_count: int = DEFAULT_NEAREST_CELLS,
    grid_names: list[str] | None = None,
) -> str:
    """List the analysis cells nearest a coordinate, each with its real distance in metres.

    Use this to judge whether a reading is actually relevant to the point you were asked about,
    and to see where the measurements physically are. Cells come back nearest first with their
    centroid coordinates and how many accepted observations each one holds on the given day --
    including cells holding nothing, which are listed with a count of zero rather than omitted.
    The list is of cells that have REPORTED recently, not of every cell a grid declares, because
    the cell registry has been retired. That count covers the governed signal plane only, so zero
    never means the cell is empty of everything. If you quote a value from a cell, quote its
    distance with it.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        day: The calendar day to count observations for, as ISO YYYY-MM-DD.
        radius_meters: Search radius around the point in metres; capped at 50000.
        cell_count: How many cells to return, nearest first; capped at 25.
        grid_names: Deprecated. The Parquet signal plane carries no grid name, so supplying this
            is refused rather than silently ignored. Omit it.
    """
    return await query_nearest_signal_cells(
        longitude=longitude,
        latitude=latitude,
        day=day,
        radius_meters=radius_meters,
        cell_count=cell_count,
        grid_names=grid_names,
    )


@beta_async_tool
async def observation_coverage_on_day(
    surface_name: str,
    day: str,
) -> str:
    """Say whether one map surface has any observation on a given day, and how deep its history is.

    Works for every surface the map publishes, not just the signal grids -- fire detections, burn
    severity, evacuation zones, sensors, vegetation, water gauges, weather observations,
    watersheds, soil survey, fire perimeters, the drought release set, the three soil-field
    streams and the nine climate-field streams. Answers from the same published availability index
    the map's time slider is built from, so it can never disagree with which days the slider
    offers, and it covers the lane's WHOLE history rather than a scan budget.

    Call this FIRST when you are asked about a layer on a specific day. It tells you whether the
    day is covered, how many rows landed, and where the day sits relative to the layer's earliest
    and latest published days and its source's own ceiling -- which is how you distinguish "before
    this lane's history begins" from "past what the source could have published" from "a real hole
    in the middle".

    Args:
        surface_name: The map surface to ask about, exactly as the map names it, e.g.
            "vegetation", "fire-detections", "drought-areas", "climate-field-air-temperature".
        day: The calendar day to answer for, as ISO YYYY-MM-DD. Use the day the caller selected.
    """
    return await query_observation_coverage_on_day(surface_name=surface_name, day=day)


@beta_async_tool
async def observation_temporal_neighbors(
    surface_name: str,
    day: str,
    neighbor_days: int = DEFAULT_SURFACE_NEIGHBOR_DAYS,
) -> str:
    """Find the nearest observed day before and after a given day, for any map surface.

    Call this when observation_coverage_on_day reported the day uncovered. It returns at most two
    rows -- the closest covered day earlier than the one asked about and the closest one later --
    each carrying its real gap in days and how many rows that day held.

    These are neighbours, never answers. Report them as "the nearest observation is nine days
    earlier", never as the value on the day requested. A missing side means no covered day exists
    on that side inside the searched window, which is a claim about the window and not about all
    of history.

    Args:
        surface_name: The map surface to ask about, exactly as the map names it.
        day: The calendar day to search around, as ISO YYYY-MM-DD.
        neighbor_days: How many days each side of the day to search; capped at 180.
    """
    return await query_observation_temporal_neighbors(
        surface_name=surface_name,
        day=day,
        neighbor_days=neighbor_days,
    )


@beta_async_tool
async def feature_value_near_point(  # noqa: PLR0913 - the parameter list is the published tool schema.
    surface_name: str,
    day: str,
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    feature_count: int = DEFAULT_SURFACE_FEATURE_ROWS,
) -> str:
    """List the nearest published features of one map layer dated to ONE specific day.

    The spatial half of answering about a feature-backed layer at the day the map is showing:
    fire detections, fire perimeters, burn severity, evacuation zones, sensors, vegetation,
    water gauges, weather observations, watersheds, soil survey and interventions. Each feature
    comes back with its real distance in metres, the basis that distance was measured on, its
    centroid, and its lane's own typed columns under properties.

    Features are dated by the partition day the map's own client asks for, so a feature the map
    draws on that day is a feature this returns. Read day_state before the list: an empty list on
    a governed_absence day is a measured absence, and an empty list on a day_not_written day
    supports no conclusion at all. Pair it with observation_coverage_on_day to tell "nothing near
    this point" apart from "nothing anywhere that day". The cell-grid signal streams and the
    drought release set have no individual features and are refused by name rather than answered
    with an empty list.

    Args:
        surface_name: The feature-backed map layer to ask about, exactly as the map names it,
            e.g. "fire-detections", "water-gauges", "vegetation".
        day: The calendar day to answer for, as ISO YYYY-MM-DD. Use the day the caller selected.
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        radius_meters: Search radius around the point in metres; capped at 50000.
        feature_count: How many features to return, nearest first; capped at 50.
    """
    return await query_feature_value_near_point(
        surface_name=surface_name,
        day=day,
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius_meters,
        feature_count=feature_count,
    )


WAREHOUSE_TOOLS: Final = (
    signals_near_point,
    drought_history_at_point,
    fire_history_near_point,
    forecast_summary_for_cell,
    signal_value_on_day,
    signal_neighbors_in_time,
    nearest_signal_cells,
    observation_coverage_on_day,
    observation_temporal_neighbors,
    feature_value_near_point,
)
