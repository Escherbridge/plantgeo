"""Read-only, bounded warehouse tools the agent graph exposes to the model.

Every tool here is a SELECT against a least-privilege reader session, with caps on radius,
time window and row count baked in rather than left to the model. See agent/AGENTS.md,
"Tool contract", for why the bounds are enforced here and not in the prompt, and
"Reading the pre-aggregated planes" for why the sources are matviews rather than raw tables.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import cos, radians
from typing import TYPE_CHECKING, Any, Final

from anthropic import beta_async_tool
from sqlalchemy import ARRAY, Text, bindparam, text

from agri_data_service.db.engine import published_reader_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.firms import resolve_firms_layer_name
from agri_data_service.ingest.mtbs import resolve_burn_severity_layer_name

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
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
# Ten years. Safe as of the pre-aggregation layer (2026-08-15): the window read is now an index
# range over geo.mv_signal_cell_daily, which holds one row per cell-day rather than one per
# reading, so a decade costs a longer contiguous scan instead of a wider one.
MAX_DAYS_BACK: Final = 3_650

DEFAULT_WEEKS_BACK: Final = 52
MAX_WEEKS_BACK: Final = 520

DEFAULT_FIRE_YEARS_BACK: Final = 5
MAX_FIRE_YEARS_BACK: Final = 45

DEFAULT_FORECAST_ROWS: Final = 40
MAX_FORECAST_ROWS: Final = 120

# --- Selected-day bounds -----------------------------------------------------------
#
# Bounds for the tools that answer at the day the UI has selected. See agent/AGENTS.md,
# "Answering at the selected day", for what each one is measured against and why.

# 19 signal names are under contract across the three lanes (execution/coverage_contract.py,
# verified against agri.data_source and agri.signal_observation 2026-08-11). Rows now group by
# signal x support key x unit -- the pre-aggregated rollup carries no source_parameter column --
# so 40 leaves a lane room to publish a second unit spelling without an answer being truncated.
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

# --- Generic-surface bounds --------------------------------------------------------
#
# Bounds for the three tools that answer for ANY catalogue surface rather than for the signal
# plane alone. See agent/AGENTS.md, "The generic surface triad".

# The census walk is an index range stopped after one row per side, so the window bounds the
# ANSWER and not the work. 180 days matches MAX_NEIGHBOR_DAYS so both temporal tools make the
# same claim about how far "no neighbour" was searched.
DEFAULT_SURFACE_NEIGHBOR_DAYS: Final = 30
MAX_SURFACE_NEIGHBOR_DAYS: Final = 180

# geo.features holds about 4.97 million published rows carrying roughly 1,467 MB of TOASTed
# `properties` between them (measured 2026-08-15). Fifty nearest features is more than any
# answer has used and small enough that the per-row property projection stays free.
DEFAULT_SURFACE_FEATURE_ROWS: Final = 12
MAX_SURFACE_FEATURE_ROWS: Final = 50

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

# --- The catalogue the agent and the map share -------------------------------------
#
# HAND-SPELLED, and deliberately not derived. docs/layer-lane-standard.md section 9 requires the
# slider capability catalogue to be asserted against a hand-spelled list precisely because a
# generated list drifts silently with the thing it is meant to check. The same reasoning applies
# here: if this tuple were built from a query against geo.layers, a layer that vanished from the
# database would vanish from the agent's vocabulary too, and the agent would answer "I do not
# know that surface" instead of "that surface stopped being served".
#
# 24 names, matching the catalogue exactly as of 2026-08-15: 11 geo.layers rows, the 4
# SLIDER_STREAM_LAYER_NAMES entries (src/types/time-slider.ts), and the 9
# `climate-field-<signal>` names CLIMATE_FIELD_SIGNAL_IDS produces
# (src/lib/environmental/climate-field.ts).

# The 11 geo.layers rows -- the feature-backed half, seeded by drizzle 0001, 0011, 0013 and 0017.
FEATURE_SURFACE_NAMES: Final = (
    "burn-severity",
    "evacuation-zones",
    "fire-detections",
    "fire-perimeters",
    "interventions",
    "sensors",
    "soil-survey",
    "vegetation",
    "watersheds",
    "water-gauges",
    "weather-observations",
)

# The 13 stream names, which are NOT geo.layers rows: one polygon-backed release set and twelve
# signal-backed cell-grid streams.
STREAM_SURFACE_NAMES: Final = (
    "climate-field-air-temperature",
    "climate-field-dew-point",
    "climate-field-precipitation",
    "climate-field-relative-humidity",
    "climate-field-shortwave-radiation",
    "climate-field-soil-wetness-profile",
    "climate-field-soil-wetness-root-zone",
    "climate-field-soil-wetness-surface",
    "climate-field-wind-speed",
    "drought-areas",
    "soil-field-moisture",
    "soil-field-temperature",
    "soil-field-vpd",
)

AGENT_SURFACE_NAMES: Final = tuple(sorted(FEATURE_SURFACE_NAMES + STREAM_SURFACE_NAMES))

# The property keys a feature answer may carry, hand-spelled and capped.
#
# `SELECT properties` on geo.features is how a bounded row count becomes an unbounded byte
# count -- the column carries roughly 1,467 MB of TOAST across 4.97 million rows (measured
# 2026-08-15), so a fifty-row answer can be tens of megabytes. Every key below is one the
# served layers actually publish, harvested 2026-08-15 from the eight `geo.*_tiles` functions in
# drizzle/ and the `properties->>` reads in src/lib/server/services/. A key not listed is not
# hidden data; it is data no reader has asked for yet.
FEATURE_PROPERTY_KEYS: Final = (
    "acqDate",
    "acqTime",
    "acres",
    "areasqkm",
    "assessmentType",
    "cellKey",
    "cloudCover",
    "county",
    "description",
    "drainageClass",
    "evacuationAreaName",
    "evacuationLevelLabel",
    "fireDiscoveryDateTime",
    "fireId",
    "fireName",
    "fireType",
    "fireYear",
    "gridName",
    "hutype",
    "hydric",
    "id",
    "ignitionDate",
    "name",
    "ndvi",
    "network",
    "observedAt",
    "polygonDateTime",
    "populationWithin",
    "priority",
    "product",
    "resolutionMetres",
    "sampleCount",
    "sceneId",
    "severity",
    "severityClass",
    "siteNo",
    "source",
    "states",
    "status",
    "structuresWithin",
    "tohuc",
    "updatedAt",
)

# --- The pre-aggregated relations, named once --------------------------------------
#
# Every one of these is a MATERIALIZED VIEW, which can exist while holding nothing: PostgreSQL
# creates it WITH NO DATA and raises rather than returning zero rows until a REFRESH has run.
# agri.mv_forecast_ml_daily_serving shipped in exactly that state. Naming the relations here lets
# a tool probe the plane it is about to read and refuse by name, instead of raising at the model
# or -- far worse -- returning an empty result the model reads as an absence.

SIGNAL_ROLLUP_RELATION: Final = "geo.mv_signal_cell_daily"
FEATURE_CENSUS_RELATION: Final = "geo.mv_feature_observation_day"
SIGNAL_CENSUS_RELATION: Final = "geo.mv_signal_observation_day"
DROUGHT_CENSUS_RELATION: Final = "geo.mv_drought_observation_day"
DROUGHT_RELEASE_RELATION: Final = "geo.mv_drought_release_index"
FORECAST_DAILY_RELATION: Final = "agri.mv_forecast_ml_daily_serving"

# geo.v_observation_day_census is a plain VIEW over these three, and a view reports itself
# populated even when the matviews beneath it are not -- so the probe names the three, never
# the view that unions them.
CENSUS_RELATIONS: Final = (
    FEATURE_CENSUS_RELATION,
    SIGNAL_CENSUS_RELATION,
    DROUGHT_CENSUS_RELATION,
)

# --- Bounding-box prefilter arithmetic ---------------------------------------------
#
# An exact "within N metres" test needs a ::geography cast, and that cast makes the predicate
# unusable by idx_features_geom / drought_areas_geom_gist, which are GiST indexes over the
# GEOMETRY column. No geography index exists on either table. So a cheap `geom && box` test runs
# first and the exact distance is computed only on the survivors.
#
# The box has to be built in degrees. A degree of latitude is a fixed 110,574 m (WGS84 mean); a
# degree of longitude is 111,320 m only at the equator and shrinks by cos(latitude). Sizing the
# box on the latitude figure alone would clip its eastern and western edges at any distance from
# the equator and silently drop real features, which is the exact failure this prefilter must not
# introduce -- so the wider of the two axes wins, with a margin for the ellipsoid error the
# spherical figures leave behind.
_METERS_PER_DEGREE_LATITUDE: Final = 110_574.0
_METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR: Final = 111_320.0
_BBOX_SAFETY_MARGIN: Final = 1.05
# Floors the cosine so the divide cannot explode within ~0.6 degrees of a pole. At that latitude
# the box degenerates to most of the meridian anyway and the exact test does the real work.
_MIN_LATITUDE_COSINE: Final = 0.01

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
_VALUE_ON_DAY_SQL: Final = text(load_query_sql("agent/signal_value_on_day.sql")).bindparams(
    bindparam("signal_names", type_=ARRAY(Text))
)
_COVERAGE_ON_DAY_SQL: Final = text(load_query_sql("agent/signal_coverage_on_day.sql")).bindparams(
    bindparam("signal_names", type_=ARRAY(Text))
)
_TIME_NEIGHBORS_SQL: Final = text(load_query_sql("agent/signal_neighbors_in_time.sql")).bindparams(
    bindparam("signal_names", type_=ARRAY(Text))
)
_NEAREST_CELLS_SQL: Final = text(load_query_sql("agent/nearest_signal_cells.sql")).bindparams(
    bindparam("grid_names", type_=ARRAY(Text))
)
_PLANE_POPULATED_SQL: Final = text(load_query_sql("agent/materialized_plane_populated.sql")).bindparams(
    bindparam("relation_names", type_=ARRAY(Text))
)
_SURFACE_COVERAGE_SQL: Final = text(load_query_sql("agent/observation_coverage_on_day.sql"))
_SURFACE_NEIGHBORS_SQL: Final = text(load_query_sql("agent/observation_temporal_neighbors.sql"))
_FEATURE_NEAR_POINT_SQL: Final = text(load_query_sql("agent/feature_value_near_point.sql")).bindparams(
    bindparam("property_keys", type_=ARRAY(Text))
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
# One run's answers to "is this plane built". A matview never becomes unpopulated again once it
# has been refreshed, so the answer cannot go stale inside a run, and caching it keeps a ten-tool
# run to one catalog probe instead of ten.
_plane_state: ContextVar[dict[str, bool] | None] = ContextVar("agri_agent_plane_state", default=None)


@asynccontextmanager
async def run_context(
    *,
    session_provider: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Bind one run's session provider and yield the ledger the tools append to."""
    ledger: list[dict[str, Any]] = []
    provider_token = _session_provider.set(session_provider or published_reader_session)
    ledger_token = _tool_ledger.set(ledger)
    plane_token = _plane_state.set({})
    try:
        yield ledger
    finally:
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
    """Half-width in degrees of a box that certainly contains everything within the radius."""
    cosine = max(cos(radians(latitude)), _MIN_LATITUDE_COSINE)
    longitude_degrees = radius_meters / (_METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * cosine)
    latitude_degrees = radius_meters / _METERS_PER_DEGREE_LATITUDE
    return max(longitude_degrees, latitude_degrees) * _BBOX_SAFETY_MARGIN


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
    """Run one bounded read on a fresh least-privilege reader session."""
    async with _session_provider.get()() as session:
        result = await session.execute(statement, parameters)
        return [dict(row) for row in result.mappings().all()]


# --- Refusing on an unbuilt plane --------------------------------------------------
#
# A matview that has never been refreshed raises on read. Catching that raise and returning an
# empty list would be the single most damaging thing this module could do: "no drought here" and
# "the drought plane has never been built" would become the same answer. So the plane is probed
# first and a MISSING plane produces a typed refusal that names it.


async def _unbuilt_planes(relations: Sequence[str]) -> list[dict[str, Any]]:
    """Return one entry per named relation that is missing or has never been refreshed."""
    # Outside a run_context there is nowhere to cache, so the probe runs and its answers live only
    # for this call. Inside one they are remembered, because a matview cannot become unpopulated
    # again once refreshed and a ten-tool run should not ask the catalog ten times.
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


def _plane_refusal(tool_name: str, unbuilt: list[dict[str, Any]]) -> str:
    """State that a pre-aggregated plane has never been built, rather than answering nothing."""
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
    """Summarise governed signal observations near a point, from the daily rollup."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("signals_near_point")
    unbuilt = await _unbuilt_planes([SIGNAL_ROLLUP_RELATION])
    if unbuilt:
        return _plane_refusal("signals_near_point", unbuilt)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    window_days = _clamp_int(days_back, 1, MAX_DAYS_BACK)
    names = _clean_names(signal_names)
    day_through = (as_of or datetime.now(UTC)).date()
    day_from = day_through - timedelta(days=window_days)
    rows = await _fetch(
        _SIGNALS_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius,
            "cell_limit": MAX_CELL_FANOUT,
            "day_from": day_from,
            "day_through": day_through,
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
                "day_from": day_from,
                "day_through": day_through,
                "signal_names": names,
                "max_summary_rows": DEFAULT_SUMMARY_ROWS,
            },
            "signal_summaries": rows,
            "note": (
                "Read from the governed daily rollup the map itself paints from, so this can "
                "never disagree with what the user sees. It covers the 19 signal names under "
                "contract, and only readings the ingest lane accepted. Empty means the warehouse "
                "holds no accepted governed observation for this radius and window; it does not "
                "mean the condition is absent, and a signal outside the governed contract is "
                "absent here because it is out of scope, not because it was unmeasured."
            ),
        }
    )


async def query_drought_history_at_point(
    longitude: float,
    latitude: float,
    weeks_back: int = DEFAULT_WEEKS_BACK,
    as_of: datetime | None = None,
) -> str:
    """Return the weekly U.S. Drought Monitor severity covering a point, from the served plane."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("drought_history_at_point")
    unbuilt = await _unbuilt_planes([DROUGHT_RELEASE_RELATION])
    if unbuilt:
        return _plane_refusal("drought_history_at_point", unbuilt)
    window_weeks = _clamp_int(weeks_back, 1, MAX_WEEKS_BACK)
    reference = (as_of or datetime.now(UTC)).date()
    valid_date_from = reference - timedelta(days=window_weeks * _DAYS_PER_WEEK)
    rows = await _fetch(
        _DROUGHT_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "valid_date_from": valid_date_from.isoformat(),
            "row_limit": MAX_WEEKS_BACK,
        },
    )
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
            },
            "severity_scale": (
                "0 = D0 abnormally dry, 1 = D1 moderate, 2 = D2 severe, 3 = D3 extreme, 4 = D4 exceptional"
            ),
            "weekly_severity": rows,
            "releases_returned": len(rows),
            "releases_with_drought_over_point": len(covered),
            "note": (
                "One row per PUBLISHED U.S. Drought Monitor release inside the window, read from "
                "geo.drought_areas -- the same plane the map paints. Every release in the window "
                "appears, including ones that published no drought class over this point: those "
                "carry severity_class null and covering_class_count 0, which means 'this release "
                "existed and found no drought here', a fact. An EMPTY weekly_severity list is a "
                "different claim entirely -- it means no release was published in the window at "
                "all, so nothing is known either way, and you must not report that as the absence "
                "of drought. prev_valid_date and next_valid_date give the neighbouring releases "
                "so a day between two Tuesdays can be answered with the real gap stated."
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
    unbuilt = await _unbuilt_planes([FEATURE_CENSUS_RELATION])
    if unbuilt:
        return _plane_refusal("fire_history_near_point", unbuilt)
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
            "bbox_degrees": _bbox_degrees(radius, latitude),
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
                "feature_count, nearest_distance_m and the earliest/latest_observed_day pair are "
                "scoped to the radius. The columns prefixed layer_ are NOT -- they describe the "
                "whole served layer, so a layer whose layer_latest_observed_day is months old has "
                "stopped ingesting, which is a different fact from there being no fire near this "
                "point. A satellite detection is a thermal anomaly, not a confirmed fire "
                "perimeter. Counts are capped at max_features_scanned and taken nearest-first."
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
    rows = await _fetch(
        _FORECAST_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius,
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
            },
            "forecast_values": rows,
            "note": (
                "Only published, finalized, validated forecasts are visible here, pre-aggregated "
                "to one row per valid DAY -- mean_point_value with the widest p10/p90 band the "
                "day's steps reported, and contributing_forecast_points saying how many steps "
                "that was. This view covers ML-method forecasts on series enabled for daily "
                "aggregation ONLY, so a published forecast produced another way is out of scope "
                "here rather than absent. Empty means no analysis cell within the radius has such "
                "a forecast."
            ),
        }
    )


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
    unbuilt = await _unbuilt_planes([SIGNAL_ROLLUP_RELATION])
    if unbuilt:
        return _plane_refusal("signal_value_on_day", unbuilt)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    names = _clean_names(signal_names)
    day_start, day_end = _day_bounds(selected_day)
    # The spatial and signal scope both statements must share verbatim. Reading the audit over a
    # wider set of cells than the value would let an absence recorded somewhere else appear to
    # explain this point.
    scope: dict[str, Any] = {
        "longitude": longitude,
        "latitude": latitude,
        "radius_meters": radius,
        "cell_limit": MAX_CELL_FANOUT,
        "signal_names": names,
    }
    measured = await _fetch(
        _VALUE_ON_DAY_SQL,
        {**scope, "day": selected_day, "row_limit": MAX_DAY_SUMMARY_ROWS},
    )
    governed = await _fetch(
        _COVERAGE_ON_DAY_SQL,
        # The one thing the two cannot share: the audit is grained by the WINDOW a lane fetched
        # rather than by a day, so it tests overlap against the day's two UTC midnights while the
        # rollup matches the day itself. Same day, spelled the way each relation is grained.
        {**scope, "day_start": day_start, "day_end": day_end, "row_limit": MAX_COVERAGE_AUDIT_ROWS},
    )
    _record(
        "signal_value_on_day",
        len(measured),
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
            "signals_on_day": measured,
            "signals_on_day_truncated": len(measured) >= MAX_DAY_SUMMARY_ROWS,
            "coverage_audit_on_day": governed,
            "coverage_audit_on_day_truncated": len(governed) >= MAX_COVERAGE_AUDIT_ROWS,
            "note": (
                "Every row in signals_on_day was measured ON requested_day and on no other day; "
                "nothing here is borrowed from a neighbouring day. It is read from the same "
                "governed daily rollup the map paints, so it cannot disagree with the screen. A "
                "signal absent from signals_on_day had no accepted reading that day UNLESS "
                "signals_on_day_truncated is true, in which case the list hit its row cap and a "
                "missing signal may simply have been cut. coverage_audit_on_day is what "
                "the ingest lane already recorded for a window covering it: status no_data means "
                "the upstream published nothing and the day will not be refetched, partial means "
                "fewer cells landed than expected, and an empty audit means nothing was recorded "
                "either way. For the nearest days that do carry a reading call "
                "signal_neighbors_in_time, and never quote one of those as this day's value."
            ),
        }
    )


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
    unbuilt = await _unbuilt_planes([SIGNAL_ROLLUP_RELATION])
    if unbuilt:
        return _plane_refusal("signal_neighbors_in_time", unbuilt)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    window_days = _clamp_int(neighbor_days, 1, MAX_NEIGHBOR_DAYS)
    names = _clean_names(signal_names)
    search_from = selected_day - timedelta(days=window_days)
    search_through = selected_day + timedelta(days=window_days)
    rows = await _fetch(
        _TIME_NEIGHBORS_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius,
            "cell_limit": MAX_CELL_FANOUT,
            "day": selected_day,
            "search_from": search_from,
            "search_through": search_through,
            "signal_names": names,
            "row_limit": MAX_TEMPORAL_NEIGHBOR_ROWS,
        },
    )
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
            },
            "temporal_neighbors": rows,
            "temporal_neighbors_truncated": len(rows) >= MAX_TEMPORAL_NEIGHBOR_ROWS,
            "note": (
                "Each row is the nearest accepted reading on a day OTHER than requested_day. "
                "side says whether it precedes or follows, observed_day is that reading's own "
                "date, distance_days is the real gap in days and day_offset the same gap signed, "
                "and nearest_cell_distance_m is how far its cell sits from the point. Never "
                "report one of these as the value on requested_day -- say which day it came from "
                "and how far away that is. A signal missing its before or after row has no "
                "accepted reading on that side between searched_from and searched_through, which "
                "is a statement about the window searched and not about all of history -- unless "
                "temporal_neighbors_truncated is true, in which case the list hit its row cap and "
                "the missing side may simply have been cut."
            ),
        }
    )


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
    unbuilt = await _unbuilt_planes([SIGNAL_ROLLUP_RELATION])
    if unbuilt:
        return _plane_refusal("nearest_signal_cells", unbuilt)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    returned_cells = _clamp_int(cell_count, 1, MAX_NEAREST_CELLS)
    grids = _clean_names(grid_names)
    rows = await _fetch(
        _NEAREST_CELLS_SQL,
        {
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius,
            "cell_limit": MAX_CELL_FANOUT,
            "grid_names": grids,
            "day": selected_day,
            "row_limit": returned_cells,
        },
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
            },
            "nearest_cells": rows,
            "note": (
                "Cells are ordered by their real distance from the requested point, measured to "
                "the cell centroid in metres on the curved earth, and are listed whether or not "
                "they hold anything. observation_count_on_day counts rows on the GOVERNED SIGNAL "
                "plane only (weather, soil, air quality and similar). Layers that land on the "
                "forecast plane -- Sentinel-2 NDVI above all -- are not counted here at all, so a "
                "0 is not a claim that the cell is empty; it is a claim about governed signal "
                "observations. If the nearest cell holds nothing on requested_day and a farther "
                "one does, quoting the farther one is only honest with its distance attached. An "
                "empty list means no cell of any listed grid falls inside the radius at all."
            ),
        }
    )


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
    unbuilt = await _unbuilt_planes(CENSUS_RELATIONS)
    if unbuilt:
        return _plane_refusal("observation_coverage_on_day", unbuilt)
    rows = await _fetch(_SURFACE_COVERAGE_SQL, {"surface_name": surface, "day": selected_day})
    coverage = rows[0] if rows else {}
    _record(
        "observation_coverage_on_day",
        len(rows),
        {"surface_name": surface, "requested_day": selected_day, "is_covered": bool(coverage.get("is_covered"))},
    )
    return _payload(
        {
            "requested_day": selected_day,
            "surface_name": surface,
            "coverage": coverage,
            "note": (
                "Read from the same observed-day census the map's time slider is built from, so "
                "this cannot disagree with which days the slider offers. is_covered false is a "
                "fact about the day, and the three history columns say which KIND of absence it "
                "is: a day before earliest_observed_day is outside this lane's declared horizon, "
                "a day after latest_observed_day is past its live edge and may simply not be "
                "published yet, and a day between the two is a genuine hole in a lane that "
                "otherwise covers it. Name which of the three it is rather than saying only that "
                "the day is empty. For the nearest days that ARE covered call "
                "observation_temporal_neighbors."
            ),
        }
    )


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
    unbuilt = await _unbuilt_planes(CENSUS_RELATIONS)
    if unbuilt:
        return _plane_refusal("observation_temporal_neighbors", unbuilt)
    window_days = _clamp_int(neighbor_days, 1, MAX_SURFACE_NEIGHBOR_DAYS)
    search_from = selected_day - timedelta(days=window_days)
    search_through = selected_day + timedelta(days=window_days)
    rows = await _fetch(
        _SURFACE_NEIGHBORS_SQL,
        {
            "surface_name": surface,
            "day": selected_day,
            "search_from": search_from,
            "search_through": search_through,
        },
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
    rows = await _fetch(
        _FEATURE_NEAR_POINT_SQL,
        {
            "surface_name": surface,
            "day": selected_day,
            "longitude": longitude,
            "latitude": latitude,
            "radius_meters": radius,
            "bbox_degrees": _bbox_degrees(radius, latitude),
            "property_keys": list(FEATURE_PROPERTY_KEYS),
            "row_limit": returned_features,
        },
    )
    _record(
        "feature_value_near_point",
        len(rows),
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
                "projected_property_keys": list(FEATURE_PROPERTY_KEYS),
            },
            "features": rows,
            "features_truncated": len(rows) >= returned_features,
            "note": (
                "Every feature here is dated to requested_day by the SAME rule the map's tiles "
                "use, so a feature the map draws on that day is a feature this can return. Each "
                "carries distance_meters from the requested point and its own observed_day. An "
                "empty list means this layer published nothing dated to that day inside the "
                "search box -- call observation_coverage_on_day to find out whether the day is "
                "covered anywhere at all, which distinguishes 'nothing near you' from 'nothing "
                "that day'. properties is projected to a fixed key list, so a key you do not see "
                "was not requested rather than being empty."
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
    """Return the U.S. Drought Monitor severity that covered a coordinate, release by release.

    One row per published weekly release, carrying the highest severity class whose polygon
    covered the point (0 = D0 abnormally dry through 4 = D4 exceptional drought). Releases that
    published no drought class over the point still appear, with a null severity -- that is a
    measured "no drought that week", and it is different from an empty result, which means no
    release was published in the window at all. Each row also carries the previous and next
    release dates so a day between two weekly releases can be answered with the real gap. Read
    from the same drought polygons the map paints. The lookback is capped at 520 weeks.

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
    close the nearest was, and the calendar day range they span -- plus, in the columns prefixed
    layer_, the whole layer's served day range, which tells you whether an empty radius means
    "no fire here" or "this layer stopped ingesting". Satellite detections are thermal anomalies
    rather than confirmed fires; burn perimeters are post-fire mapped boundaries. Radius is
    capped at 50000 metres and the lookback at 45 years.

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
    that cell's published forecast values aggregated to one row per valid day -- metric, unit,
    issue time, valid day, mean point value, the p10/p90 uncertainty band, and how many forecast
    steps were folded into the day. Only published, finalized and validated forecasts are
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
    row was built from. The result also carries what the ingest lane recorded for that day --
    status no_data means the upstream published nothing, which is why a value is missing.

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
    signal missing a side simply has no reading there within the searched window.

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
    centroid coordinates, their grid and resolution, and how many accepted observations each one
    holds on the given day -- including cells holding nothing, which are listed with a count of
    zero rather than omitted. That count covers the governed signal plane only; forecast-plane
    layers such as Sentinel-2 NDVI are not counted, so zero never means the cell is empty of
    everything. If you quote a value from a cell, quote its distance with it.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        day: The calendar day to count observations for, as ISO YYYY-MM-DD.
        radius_meters: Search radius around the point in metres; capped at 50000.
        cell_count: How many cells to return, nearest first; capped at 25.
        grid_names: Optional exact grid names to restrict to. Omit for every grid.
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
    watersheds, soil survey, interventions, fire perimeters, the drought release set, the three
    soil-field streams and the nine climate-field streams. Answers from the same observed-day
    census the map's time slider is built from, so it can never disagree with which days the
    slider offers.

    Call this FIRST when you are asked about a layer on a specific day. It tells you whether the
    day is covered, how many observations landed, and where the day sits relative to the layer's
    earliest and latest served days -- which is how you distinguish "before this lane's history
    begins" from "past its live edge" from "a real hole in the middle".

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
    each carrying its real gap in days and how many observations that day held.

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
    comes back with its real distance in metres, its own observation day, its centroid, and a
    fixed projection of its published properties.

    Features are dated by exactly the rule the map's tiles use, so a feature the map draws on
    that day is a feature this returns. An empty list means the layer published nothing dated to
    that day inside the search box -- pair it with observation_coverage_on_day to tell "nothing
    near this point" apart from "nothing anywhere that day". The cell-grid signal streams and the
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
