"""Read-only, bounded warehouse tools the agent graph exposes to the model.

Every environmental answer here is a bounded read of the day-partitioned Parquet warehouse, through
`agent/warehouse.py`, with caps on radius, time window and row count baked in rather than left to
the model. The only database-backed tool is the separate species/profile lookup; environmental
surfaces never retry PostgreSQL when a Parquet lane is missing. See agent/AGENTS.md, "Tool contract",
for why the bounds are enforced here and not in the prompt, and "Reading the Parquet warehouse" for
what a four-state answer means.
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
from typing import TYPE_CHECKING, Annotated, Any, Final

from anthropic import beta_async_tool
from pydantic import Field

from agri_data_service.agent import parquet_reads, warehouse
from agri_data_service.agent.botanical_occurrences import (
    botanical_occurrence_current_release,
)
from agri_data_service.agent.selection_evidence import catalogue, parse_selection, retrieve
from agri_data_service.agent.surfaces import (
    AGENT_SURFACE_NAMES,
    FEATURE_SURFACE_NAMES,
    FIRE_LANE_NAMES,
    FIRE_REGION_LAYERS,
    STREAM_SURFACE_NAMES,
    surface_lanes,
    surface_region_layer,
)
from agri_data_service.db.engine import published_reader_session
from agri_data_service.foundation.region import is_layer_bound, load_region
from agri_data_service.parquet_ops.coverage import registered_census_lanes
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.warehouse_reader import (
    GeometrySupport,
    NoSpatialSupport,
    PointSupport,
    spatial_support,
)
from agri_data_service.parquet_ops.wire import PublishedDay
from agri_data_service.planes.botanical_species_information import (
    DEFAULT_COMPANION_LIMIT,
    MAX_COMPANION_LIMIT,
    SpeciesInformationRequestError,
    encode_species_information,
    parse_species_information_request,
    read_species_information,
)
from agri_data_service.planes.botanical_species_information import (
    invalid_request as invalid_species_information_request,
)
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.agent.warehouse import AgentWarehouseSource, LaneEvidence, LaneWindow
    from agri_data_service.parquet_ops.warehouse_reader import WarehouseListing

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

DEFAULT_WEEKS_BACK: Final = 52
MAX_WEEKS_BACK: Final = 120

DEFAULT_FIRE_YEARS_BACK: Final = 1
MAX_FIRE_YEARS_BACK: Final = 2

DEFAULT_FORECAST_ROWS: Final = 40
MAX_FORECAST_ROWS: Final = 120

# Surface metadata queries report their bounded neighbour window.
DEFAULT_SURFACE_NEIGHBOR_DAYS: Final = 30
MAX_SURFACE_NEIGHBOR_DAYS: Final = 180

DEFAULT_SURFACE_FEATURE_ROWS: Final = 12
MAX_SURFACE_FEATURE_ROWS: Final = 50

# Pre-aggregation caps: how much one read may gather before it collapses to a summary.
MAX_FIRE_FEATURE_FANOUT: Final = 2_000
MAX_NAME_FILTER_ENTRIES: Final = 12
MAX_NAME_LENGTH: Final = 150

_DAYS_PER_WEEK: Final = 7
_DAYS_PER_YEAR: Final = 365

_MIN_LONGITUDE: Final = -180.0
_MAX_LONGITUDE: Final = 180.0
_MIN_LATITUDE: Final = -90.0
_MAX_LATITUDE: Final = 90.0

# Required columns are checked against the published objects before reading.
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

# --- Ambient run state -------------------------------------------------------------
#
# Tool functions are module-level and their signatures are the model-facing schema, so the
# session factory, the warehouse source and the run ledger cannot be parameters. They travel in
# context variables the graph sets for the duration of one run, which also makes them trivial to
# stub.

_session_provider: ContextVar[Callable[[], AbstractAsyncContextManager[AsyncSession]]] = ContextVar(
    "agri_agent_session_provider", default=published_reader_session
)
_allowed_species_id: ContextVar[str | None] = ContextVar("agri_agent_allowed_species_id", default=None)
_tool_ledger: ContextVar[list[dict[str, Any]] | None] = ContextVar("agri_agent_tool_ledger", default=None)


@asynccontextmanager
async def run_context(
    *,
    session_provider: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
    warehouse_source: AgentWarehouseSource | None = None,
    allowed_species_id: str | None = None,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Bind one run's species lookup session and Parquet source, and yield the tool ledger."""
    ledger: list[dict[str, Any]] = []
    provider_token = _session_provider.set(session_provider or published_reader_session)
    species_token = _allowed_species_id.set(allowed_species_id)
    ledger_token = _tool_ledger.set(ledger)
    source_token = warehouse.set_source(warehouse_source)
    try:
        yield ledger
    finally:
        warehouse.reset_source(source_token)
        _tool_ledger.reset(ledger_token)
        _allowed_species_id.reset(species_token)
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
                "the cell-grid streams and the drought release set, which have no "
                "individual features to return -- use surface_evidence_for_selection. "
                "Nothing was queried, so nothing follows "
                "about the surface that was named."
            ),
        }
    )


SpeciesUuid = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        description="Exact canonical agri.species UUID; botanical names are not accepted.",
    ),
]
CompanionLimit = Annotated[
    int,
    Field(ge=1, le=MAX_COMPANION_LIMIT, description="Maximum approved companion rows to return."),
]


@beta_async_tool
async def species_information(
    species_id: SpeciesUuid,
    companion_limit: CompanionLimit = DEFAULT_COMPANION_LIMIT,
) -> str:
    """Read one unpublished authoring species profile with explicit provenance, missingness, and claim limits."""
    try:
        allowed_species_id = _allowed_species_id.get()
        if allowed_species_id == "" or (allowed_species_id is not None and species_id != allowed_species_id):
            raise SpeciesInformationRequestError(
                "species_id must exactly match the canonical UUID supplied with this agent request"
            )
        request = parse_species_information_request({"species_id": species_id, "companion_limit": str(companion_limit)})
        result = await read_species_information(request, session_provider=_session_provider.get())
    except SpeciesInformationRequestError as error:
        result = invalid_species_information_request(str(error))
    _record(
        "species_information",
        0,
        {
            "profile_count": int(result["state"] == "found"),
            "state": result["state"],
            "publication_state": "not_published",
            "evidence_domain": "botanical_reference",
        },
    )
    return encode_species_information(result).decode("utf-8")


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


def _region_absence(tool_name: str, layer_slugs: Sequence[str]) -> str | None:
    """Refuse a layer this deployment's region binds no source for, or `None` when all are bound.

    `federation.md` §2, "the platform must run with a layer unbound": the tool stays REGISTERED --
    a vocabulary that changes per region would make the agent answer "I do not know that surface"
    for a layer the platform does have -- and answers a governed absence naming the layer and the
    region. Never a crash, never an empty success, and never a fall back to the pilot's source.

    A tool naming SEVERAL layers refuses when ANY of them is unbound, the same intersection rule
    `surfaces.py` already states for a surface backed by several lanes: the alternative is a lane
    loop reporting the unbound half as "never written", which is an absence claim about a layer
    this region was never going to hold. The refusal names exactly which layers were unbound.
    """
    region = load_region()
    unbound = [layer_slug for layer_slug in layer_slugs if not is_layer_bound(region, layer_slug)]
    if not unbound:
        return None
    _record(tool_name, 0, {"error": "not_available_in_region", "layers": unbound, "region": region.slug})
    return _payload(
        {
            "error": "not_available_in_region",
            "unbound_layers": unbound,
            "region_slug": region.slug,
            "region_display_name": region.display_name,
            "note": (
                "This is a REFUSAL, not an absence. This deployment covers "
                f"{region.display_name} and its region manifest binds no data source for the "
                "layer(s) named above, so the platform holds nothing for them ANYWHERE in this "
                "region -- there is no day, location or filter that would answer. Say that the "
                "layer is not available in this region; do NOT report the subject as absent, "
                "zero, unaffected, or as a gap in the record."
            ),
        }
    )


def _surface_region_absence(tool_name: str, surface: str) -> str | None:
    """`_region_absence` for one catalogue surface; `None` for a surface with no manifest layer."""
    layer_slug = surface_region_layer(surface)
    return None if layer_slug is None else _region_absence(tool_name, (layer_slug,))


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


# --- Query implementations ---------------------------------------------------------
#
# Each is a plain async function so tests can call it directly; the model-facing tool
# below is a thin decorated wrapper whose signature is the published schema.


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
    region_absence = _region_absence("drought_history_at_point", ("drought",))
    if region_absence is not None:
        return region_absence
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
        evidence_source=window.evidence_source,
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
                "is a SCAN BUDGET rather than the depth of the record. Use observation_coverage_on_day "
                "and observation_temporal_neighbors for published coverage; a refusal means the "
                "index cannot prove that coverage, not that no historical releases exist."
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
    region_absence = _region_absence("fire_history_near_point", FIRE_REGION_LAYERS)
    if region_absence is not None:
        return region_absence
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
            evidence_source=window.evidence_source,
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
                "depth. years_back is a SCAN BUDGET rather than the depth of the record. Use "
                "observation_coverage_on_day and observation_temporal_neighbors for published "
                "coverage; a withheld index cannot establish historical depth. "
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
    evidence_source: WarehouseListing,
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
            # The geodesic distance binds latitude before longitude.
            [west, east, south, north, latitude, longitude, radius_meters, row_limit],
            part_keys=part_keys,
            operation=operation,
            layer=lane,
            evidence_source=evidence_source,
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
            evidence_source=evidence_source,
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
    """Refuse forecast reads until the replacement Parquet lane is published."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("forecast_summary_for_cell")
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    names = _clean_names(metric_names)
    _record(
        "forecast_summary_for_cell",
        0,
        {
            "error": "forecast_parquet_lane_not_published",
            "radius_meters": radius,
            "metric_names": names,
            "as_of": as_of.isoformat() if as_of is not None else None,
        },
    )
    return _payload(
        {
            "applied_bounds": {
                "radius_meters": radius,
                "metric_names": names,
                "max_rows": DEFAULT_FORECAST_ROWS,
            },
            "resolved_cell": None,
            "forecast_values": [],
            "error": "forecast_parquet_lane_not_published",
            "note": (
                "Forecast schemas and materialized views were retired in the PostgreSQL-to-Parquet "
                "cutover. No database or forecast fallback was queried. Publish the governed "
                "forecast Parquet lane before enabling this tool; this refusal says nothing about "
                "whether a forecast exists for the requested point."
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
    # Asked BEFORE the lane question: a layer this region binds no source for has no lane question.
    region_absence = _surface_region_absence("observation_coverage_on_day", surface)
    if region_absence is not None:
        return region_absence
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
    """Refuse a surface that has no published Parquet lane."""
    _record(tool_name, 0, {"error": "surface_not_served_from_parquet", "surface_name": surface})
    return _payload(
        {
            "error": "surface_not_served_from_parquet",
            "received_surface_name": surface,
            "note": (
                "This is a REFUSAL, not an absence. Coverage is answered from the Parquet "
                "availability index, and this surface has no published Parquet lane. PostgreSQL is "
                "not queried as a fallback and no synthetic feature payload is fabricated. Nothing "
                "follows about whether the surface holds anything."
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
    region_absence = _surface_region_absence("observation_temporal_neighbors", surface)
    if region_absence is not None:
        return region_absence
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
async def query_feature_value_near_point(  # noqa: PLR0913, PLR0911 - schema; region gate is 7th return.
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
    region_absence = _surface_region_absence("feature_value_near_point", surface)
    if region_absence is not None:
        return region_absence
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    returned_features = _clamp_int(feature_count, 1, MAX_SURFACE_FEATURE_ROWS)
    lanes = surface_lanes(surface)
    if not lanes:
        return _surface_not_on_parquet("feature_value_near_point", surface)
    lane = lanes[0]
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
            evidence_source=window.evidence_source,
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


async def _surface_lane_result(  # noqa: PLR0913 - one coordinate per bounded surface read.
    lane: str,
    *,
    selected_day: date,
    longitude: float,
    latitude: float,
    radius_meters: float,
    row_limit: int,
) -> dict[str, Any]:
    """Read a daily partition or the map's applicable snapshot and retain both calendar days."""
    nature = next((entry.nature for entry in registered_census_lanes() if entry.layer == lane), None)

    async def read(keys: tuple[str, ...], evidence_source: WarehouseListing) -> list[dict[str, Any]]:
        return await _lane_rows(
            lane,
            part_keys=keys,
            longitude=longitude,
            latitude=latitude,
            radius_meters=radius_meters,
            row_limit=row_limit,
            operation="agent_surface_value_near_point",
            evidence_source=evidence_source,
        )

    rows: list[dict[str, Any]] = []
    served_day: date | None = None
    if nature in {"static_lookup", "release_series"}:
        envelope = await warehouse.release_rows(layer=lane, as_of=selected_day, row_limit=row_limit, read=read)
        day_state = envelope.to_wire()
        day_state.pop("rows", None)
        day_state.pop("truncated", None)
        served_day = getattr(envelope, "served_day", None)
        if isinstance(envelope, PublishedDay):
            rows = [dict(row) for row in envelope.rows]
    else:
        window = await warehouse.lane_window(layer=lane, first_day=selected_day, last_day=selected_day)
        window.raise_on_unserveable([selected_day])
        state = window.state_of(selected_day)
        day_state = await _day_state(window, selected_day, state)
        if state in {"published", "governed_absence"}:
            served_day = selected_day
        if state == "published":
            rows = await read(window.part_keys([selected_day]), window.evidence_source)
    return {
        "parquet_lane": lane,
        "lane_nature": nature,
        "requested_day": selected_day,
        "served_day": served_day,
        "day_state": day_state,
        "features": [_feature_row(row, served_day=served_day) for row in rows] if served_day is not None else [],
        "features_truncated": len(rows) >= row_limit,
        "search_shape": "bounding_box" if _is_geometry_lane(lane) else "radius",
    }


@_refuses_serving_faults("surface_value_near_point")
async def query_surface_value_near_point(  # noqa: PLR0913, PLR0911 - schema; region gate is 7th return.
    surface_name: str,
    day: str,
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    feature_count: int = DEFAULT_SURFACE_FEATURE_ROWS,
) -> str:
    """Read each map-owned lane of a surface, keeping depth and metric values separate."""
    if not _valid_coordinate(longitude, latitude):
        return _coordinate_error("surface_value_near_point")
    selected_day = _parse_day(day)
    if selected_day is None:
        return _day_error("surface_value_near_point", day)
    surface = surface_name.strip()[:MAX_NAME_LENGTH]
    if surface not in AGENT_SURFACE_NAMES:
        return _surface_error("surface_value_near_point", surface_name)
    region_absence = _surface_region_absence("surface_value_near_point", surface)
    if region_absence is not None:
        return region_absence
    if surface == "drought-areas":
        return _payload({"error": "use_drought_history_at_point", "requested_day": selected_day})
    lanes = surface_lanes(surface)
    if not lanes:
        return _surface_not_on_parquet("surface_value_near_point", surface)
    radius = _clamp(radius_meters, MIN_RADIUS_METERS, MAX_RADIUS_METERS)
    count = _clamp_int(feature_count, 1, MAX_SURFACE_FEATURE_ROWS)
    results = [
        await _surface_lane_result(
            lane,
            selected_day=selected_day,
            longitude=longitude,
            latitude=latitude,
            radius_meters=radius,
            row_limit=count,
        )
        for lane in lanes
    ]
    _record(
        "surface_value_near_point",
        sum(len(lane["features"]) for lane in results),
        {"surface_name": surface, "requested_day": selected_day, "radius_meters": radius},
    )
    return _payload(
        {
            "surface_name": surface,
            "requested_day": selected_day,
            "applied_bounds": {"radius_meters": radius, "feature_count_per_lane": count, "lane_count": len(lanes)},
            "lanes": results,
            "note": (
                "Values come from each surface's own map-serving Parquet lane. Daily lanes read requested_day; "
                "static and release lanes use the latest applicable snapshot at or before requested_day, "
                "with its own served_day stated separately. A snapshot date is not a daily observation. "
                "Read every lane's day_state before interpreting its features; a missing depth or metric "
                "is unknown, never zero. Each feature carries its distance and original typed properties. "
                "For polygons distance is to the centroid and membership is a bounding-box search; "
                "covers_probe_point answers exact containment. Neighbours remain observations at their "
                "own coordinates and dates, not measurements at the requested point."
            ),
        }
    )


def _history_anchor(raw_day: str | None) -> datetime | None:
    """Resolve an optional history anchor without substituting the current day for malformed input."""
    if raw_day is None:
        return None
    parsed = _parse_day(raw_day)
    if parsed is None:
        raise ValueError("as_of_day must be an ISO calendar day")
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


# --- Model-facing tools ------------------------------------------------------------


@beta_async_tool
async def drought_history_at_point(
    longitude: float,
    latitude: float,
    weeks_back: int = DEFAULT_WEEKS_BACK,
    as_of_day: str | None = None,
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
        as_of_day: End the history window on this ISO YYYY-MM-DD; use the caller's selected day.
    """
    return await query_drought_history_at_point(
        longitude=longitude,
        latitude=latitude,
        weeks_back=weeks_back,
        as_of=_history_anchor(as_of_day),
    )


@beta_async_tool
async def fire_history_near_point(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    years_back: int = DEFAULT_FIRE_YEARS_BACK,
    as_of_day: str | None = None,
) -> str:
    """Summarise served satellite fire detections and mapped burn perimeters near a coordinate.

    Returns one row per served fire lane: how many features fell within the radius, how
    close the nearest was, and the calendar day range they span -- plus layer_history, which
    describes the whole lane and tells you whether an empty radius means "no fire here" or "this
    lane stopped ingesting". Satellite detections are thermal anomalies rather than confirmed
    fires; burn perimeters are post-fire mapped boundaries. Radius is capped at 50000 metres and
    the lookback at 2 years per call. Use observation_coverage_on_day and observation_temporal_neighbors
    for published coverage; withheld availability evidence cannot establish the depth of history.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        radius_meters: Search radius around the point in metres; capped at 50000.
        years_back: How many years of fire history to include; capped at 2.
        as_of_day: End the history window on this ISO YYYY-MM-DD; use the caller's selected day.
    """
    return await query_fire_history_near_point(
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius_meters,
        years_back=years_back,
        as_of=_history_anchor(as_of_day),
    )


@beta_async_tool
async def forecast_summary_for_cell(
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    metric_names: list[str] | None = None,
) -> str:
    """Return forecast_parquet_lane_not_published until a governed forecast Parquet lane is admitted.

    Forecast values and a resolved cell are unavailable; no database or forecast fallback is queried.

    Args:
        longitude: WGS84 longitude in decimal degrees, -180 to 180.
        latitude: WGS84 latitude in decimal degrees, -90 to 90.
        radius_meters: Requested search radius, in metres; bounded but not searched while unavailable.
        metric_names: Optional requested metric names; retained as bounds without reading forecasts.
    """
    return await query_forecast_summary_for_cell(
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius_meters,
        metric_names=metric_names,
    )


@beta_async_tool
async def observation_coverage_on_day(
    surface_name: str,
    day: str,
) -> str:
    """Say whether one map surface has any observation on a given day, and how deep its history is.

    Works for every surface the map publishes -- fire detections, burn
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
    this point" apart from "nothing anywhere that day". The cell-grid streams and the
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


@beta_async_tool
async def surface_value_near_point(  # noqa: PLR0913 - published bounded tool schema.
    surface_name: str,
    day: str,
    longitude: float,
    latitude: float,
    radius_meters: float = DEFAULT_RADIUS_METERS,
    feature_count: int = DEFAULT_SURFACE_FEATURE_ROWS,
) -> str:
    """Read any feature, climate-field or soil-field map surface on the caller's selected day.

    Reads the surface's declared product lanes.
    Air-temperature mean/min/max and soil depths are returned separately with their own day states.
    Static and release lanes use the latest applicable snapshot at or before the selected day,
    reporting its served_day separately; daily lanes require the exact selected day.
    Missing/unwritten lanes are unknown, and neighbours retain real distances and original dates.
    Drought uses drought_history_at_point with as_of_day instead; interventions explicitly refuse
    until their governed Parquet lane is published.

    Args:
        surface_name: Exact name from the map surface catalogue, including climate-field and soil-field names.
        day: ISO YYYY-MM-DD; use the caller's selected day or explicitly labelled historical comparison day.
        longitude: WGS84 longitude, -180 to 180.
        latitude: WGS84 latitude, -90 to 90.
        radius_meters: Search radius in metres, capped at 50000.
        feature_count: Nearest values per lane, capped at 50.
    """
    return await query_surface_value_near_point(
        surface_name=surface_name,
        day=day,
        longitude=longitude,
        latitude=latitude,
        radius_meters=radius_meters,
        feature_count=feature_count,
    )


async def query_surface_evidence_for_selection(  # noqa: PLR0913 - public selection contract
    surface_name: str,
    day: str,
    longitude: float,
    latitude: float,
    range_start: str,
    range_end: str,
    time_scale: str = "day",
    zoom: float = 13,
    page_start: int = 0,
) -> str:
    """Read the selected tile and exact active temporal range through the common evidence reader."""
    selection = parse_selection(
        day=day,
        longitude=longitude,
        latitude=latitude,
        range_start=range_start,
        range_end=range_end,
        time_scale=time_scale,
        zoom=zoom,
        page_start=page_start,
    )
    if isinstance(selection, dict):
        return _payload(selection)
    result = await retrieve(surface_name, selection)
    row_count = sum(
        len(lane.get("selected", {}).get("features", []))
        + sum(len(entry.get("features", [])) for entry in lane.get("history", []))
        for lane in result.get("lanes", [])
    )
    _record(
        "surface_evidence_for_selection",
        row_count,
        {
            "surface_name": surface_name,
            "requested_day": day,
            "range_start": range_start,
            "range_end": range_end,
            "zoom": zoom,
        },
    )
    return _payload(result)


@beta_async_tool
async def list_environmental_layers() -> str:
    """Discover every map layer and its numeric evidence reader, including climate, vegetation, VPD,
    soil, water, fires, biodiversity, raster soil products and community surfaces. Availability is
    resolved at the actual selection; a catalogue entry alone never proves a measurement.
    """
    return _payload(catalogue())


@beta_async_tool
async def surface_evidence_for_selection(  # noqa: PLR0913 - public selection contract
    surface_name: str,
    day: str,
    longitude: float,
    latitude: float,
    range_start: str,
    range_end: str,
    time_scale: str = "day",
    zoom: float = 13,
    page_start: int = 0,
) -> str:
    """Retrieve map-tile numeric evidence for any catalogue layer at the exact selected day and range.

    Uses coordinate containment in the same map support, including sparse climate grids and VPD;
    never uses a fixed point radius or reads tile colors. History pages span the full active range
    and keep dates, governed absences, refusals, distances, source support and truncation explicit.
    Use this tool for all environmental analysis and RAG. Start with list_environmental_layers.

    Args:
        surface_name: Exact catalogue name; VPD is soil-field-vpd, NDVI is vegetation.
        day: Required ISO YYYY-MM-DD selected day; never silently substitute today or latest.
        longitude: WGS84 selected longitude.
        latitude: WGS84 selected latitude.
        range_start: Inclusive active calendar range start, ISO YYYY-MM-DD.
        range_end: Inclusive active calendar range end, ISO YYYY-MM-DD.
        time_scale: Active day, week, month, year or all scale; explicit range remains authoritative.
        zoom: Selected map zoom from 0 to 22; uses the map's published serving rung.
        page_start: History continuation offset from next_page_start; zero for the balanced first page.
    """
    return await query_surface_evidence_for_selection(
        surface_name,
        day,
        longitude,
        latitude,
        range_start,
        range_end,
        time_scale,
        zoom,
        page_start,
    )


WAREHOUSE_TOOLS: Final = (
    list_environmental_layers,
    surface_evidence_for_selection,
    observation_coverage_on_day,
    observation_temporal_neighbors,
    species_information,
    botanical_occurrence_current_release,
)
