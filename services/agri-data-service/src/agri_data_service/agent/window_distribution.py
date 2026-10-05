"""Window distribution at a point: each lane's per-day values over a calendar window, in ONE DuckDB aggregation.

Rules, per-lane measures and the governed-absence decision: agent/AGENTS.md, "Window distribution (2026-10-04)".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal

import duckdb

from agri_data_service.agent import warehouse
from agri_data_service.agent.day_tolerance import day_tolerance
from agri_data_service.agent.selection_reads import SelectionReader, nearest_relation, point_support_ctes
from agri_data_service.agent.selection_scope import MAX_FEATURES, Selection, support_lattice, utc_today
from agri_data_service.agent.surfaces import AGENT_SURFACE_NAMES, APP_SURFACE_NAMES, surface_lanes, surface_region_layer
from agri_data_service.foundation.region import is_layer_bound, load_region
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.authorized_serving import verified_serving_session
from agri_data_service.parquet_ops.request_params import ReadScope, RequestError, parse_calendar_day
from agri_data_service.parquet_ops.serving import resolve_window
from agri_data_service.parquet_ops.warehouse_reader import (
    GeometrySupport,
    PointSupport,
    RowReadResult,
    day_of_part_key,
    spatial_support,
)
from agri_data_service.parquet_ops.wire import GovernedAbsenceDay
from agri_data_service.pipeline.direct.climate.products import CLIMATE_FIELD_PRODUCT_BY_STREAM
from agri_data_service.pipeline.direct.soil.products import SOIL_FIELD_PRODUCT_BY_STREAM
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.warehouse_reader import RowRead
    from agri_data_service.parquet_ops.wire import DayEnvelope

#: The longest window one call summarises: the web's largest preset is 365 days, plus a leap day.
MAX_WINDOW_DAYS: Final = 366
#: Every lane this tool reads is an observation, so no window day may lie after UTC today.
WINDOW_KIND: Final[PartitionKind] = "observed"
#: The map zoom a call names no zoom for: the base rung, the finest support the map paints.
DEFAULT_ZOOM: Final = 13.0
#: Two lattice corners closer than this are the same support (both come from one SQL expression).
_SAME_SUPPORT_DEGREES: Final = 1e-9

DayReducer = Literal["avg", "sum", "max"]


@dataclass(frozen=True, slots=True)
class WindowMeasure:
    """One numeric column of a lane, how one day's rows at the point collapse to one value, and its label."""

    value_column: str
    #: The label when the lane carries none of its own, and on an entry with no data in the window.
    signal_name: str
    unit: str | None
    #: Mirrors the lane's coarse-rung `ColumnAggregation`: `avg` intensive, `sum` additive counts.
    day_reducer: DayReducer
    #: The lane's own per-row label and unit columns, when it writes them; the SQL groups by them.
    name_column: str | None = None
    unit_column: str | None = None
    #: The measured value a `governed_absence` day contributes, or None when an absence is not a value.
    absence_value: float | None = None


def _signal_plane(signal_name: str, unit: str) -> tuple[WindowMeasure, ...]:
    return (
        WindowMeasure(
            "normalized_value", signal_name, unit, "avg", name_column="signal_name", unit_column="normalized_unit"
        ),
    )


_WATER_GAUGE_FLOW: Final = (WindowMeasure("flow_cfs", "streamflow", "ft^3/s", "avg"),)

#: Curated, like `SPARSE_AREA_LANES`: which column of each dated lane IS the value at a point. A
#: dated lane missing here is refused (`no_window_measure`); `test_every_dated_lane_has_a_window_measure`
#: pins the catalogue. Only fire-detections' absence is a measured zero (FIRMS answered, zero records).
WINDOW_MEASURES: Final[Mapping[str, tuple[WindowMeasure, ...]]] = MappingProxyType(
    {
        **{
            stream: _signal_plane(product.signal_name, product.normalized_unit)
            for stream, product in CLIMATE_FIELD_PRODUCT_BY_STREAM.items()
        },
        **{
            stream: _signal_plane(product.signal_name, product.normalized_unit)
            for stream, product in SOIL_FIELD_PRODUCT_BY_STREAM.items()
        },
        "vegetation": (
            WindowMeasure(
                "metric_value", "ndvi", "unitless", "avg", name_column="metric_name", unit_column="metric_unit"
            ),
        ),
        "fire-detections": (
            WindowMeasure("detection_count", "fire_detection_count", "count", "sum", absence_value=0.0),
        ),
        "sensors": (
            WindowMeasure(
                "value", "sensor_value", None, "avg", name_column="measurement_name", unit_column="unit_code"
            ),
        ),
        "water-gauges": _WATER_GAUGE_FLOW,
        "water-gauges-daily": _WATER_GAUGE_FLOW,
        "weather-observations": (
            WindowMeasure("temperature_c", "air_temperature", "C", "avg"),
            WindowMeasure("relative_humidity_pct", "relative_humidity", "%", "avg"),
            WindowMeasure("wind_speed_ms", "wind_speed", "m/s", "avg"),
            WindowMeasure("precipitation_mm", "precipitation", "mm", "avg"),
        ),
        # Highest USDM class whose area covers the point that day (D0..D4), as drought_history_at_point.
        "drought": (WindowMeasure("dm_category", "drought_category", "usdm_class", "max"),),
    }
)


@dataclass(frozen=True, slots=True)
class DistributionWindow:
    """A validated point, its map tile and serving rung, and the inclusive window after the today clamp."""

    selection: Selection
    first: date
    last: date
    requested_last: date

    @property
    def days_in_window(self) -> int:
        """Calendar days of the clamped window, both ends included."""
        return (self.last - self.first).days + 1


def parse_window(  # noqa: PLR0913 - one argument per published tool parameter
    *,
    longitude: float,
    latitude: float,
    range_start: str,
    range_end: str,
    zoom: float | None,
    today: date,
) -> DistributionWindow:
    """Refuse a reversed or over-long range; clamp the end to `today` (an observation dated later does not exist)."""
    first = parse_calendar_day(str(range_start), "range_start")
    requested_last = parse_calendar_day(str(range_end), "range_end")
    if requested_last < first:
        raise RequestError("range_end must not be before range_start")
    if (requested_last - first).days + 1 > MAX_WINDOW_DAYS:
        raise RequestError(f"the window may span at most {MAX_WINDOW_DAYS} calendar days")
    last = min(requested_last, today)
    if last < first:
        raise RequestError("the whole window lies after today (UTC); observations dated later do not exist")
    selection = Selection.parse(
        longitude=longitude,
        latitude=latitude,
        zoom=DEFAULT_ZOOM if zoom is None else zoom,
        day=last.isoformat(),
        range_start=first.isoformat(),
        range_end=last.isoformat(),
        time_scale="day",
        page_start=0,
    )
    return DistributionWindow(selection=selection, first=first, last=last, requested_last=requested_last)


# --- SQL ---------------------------------------------------------------------------------------


def _literal(text: str | None) -> str:
    """Render a code-owned label as a SQL string literal; labels are constants here, never caller input."""
    if text is None:
        return "NULL::VARCHAR"
    return "'" + text.replace("'", "''") + "'"


def _measure_branches(measures: Sequence[WindowMeasure], *, from_rows: bool) -> list[str]:
    """One `(measure_index, signal_name, unit, day, value)` branch per measure, plus one per counted absence."""
    branches: list[str] = []
    for index, measure in enumerate(measures):
        if from_rows:
            value = f'CAST("{measure.value_column}" AS DOUBLE)'
            name = (
                f'coalesce(CAST("{measure.name_column}" AS VARCHAR), {_literal(measure.signal_name)})'
                if measure.name_column
                else _literal(measure.signal_name)
            )
            unit = (
                f'coalesce(CAST("{measure.unit_column}" AS VARCHAR), {_literal(measure.unit)})'
                if measure.unit_column
                else _literal(measure.unit)
            )
            branches.append(
                f"SELECT {index} AS measure_index, {name} AS signal_name, {unit} AS unit, days.day AS day, "
                f"{measure.day_reducer}({value}) AS value "
                f"FROM target JOIN days USING (filename) WHERE {value} IS NOT NULL AND isfinite({value}) GROUP BY ALL"
            )
        if measure.absence_value is not None:
            absence_value = repr(float(measure.absence_value))
            branches.append(
                f"SELECT {index} AS measure_index, {_literal(measure.signal_name)} AS signal_name, "
                f"{_literal(measure.unit)} AS unit, absent.day AS day, {absence_value}::DOUBLE AS value FROM absent"
            )
    return branches


def distribution_statement(target_ctes: str | None, measures: Sequence[WindowMeasure]) -> str:
    """ONE aggregation over every per-day value at the point: quantile_cont p10/median/p90, min/max/avg, days.

    `target_ctes` defines `target` (the chosen support's rows, with `filename`); None aggregates the
    counted governed absences alone. Parameters after the target's: part filenames, their days, absent days.
    """
    ctes = []
    if target_ctes is not None:
        ctes.append(target_ctes)
        ctes.append("days AS (SELECT unnest(?::VARCHAR[]) AS filename, unnest(?::DATE[]) AS day)")
    ctes.append("absent AS (SELECT unnest(?::DATE[]) AS day)")
    branches = _measure_branches(measures, from_rows=target_ctes is not None)
    ctes.append("day_values AS (\n    " + "\n    UNION ALL ".join(branches) + "\n)")
    return f"""-- agent_window_distribution
WITH {", ".join(ctes)}
SELECT measure_index, signal_name, unit, count(DISTINCT day) AS days_with_data,
    min(value) AS min, quantile_cont(value, 0.1) AS p10, quantile_cont(value, 0.5) AS median,
    quantile_cont(value, 0.9) AS p90, max(value) AS max, avg(value) AS mean
FROM day_values
GROUP BY measure_index, signal_name, unit
ORDER BY measure_index, signal_name NULLS FIRST, unit NULLS FIRST
"""


def _point_target(support: PointSupport, *, exposed: bool) -> str:
    """Every row in the chosen lattice support (corner bound by the two parameters after the part list)."""
    return (
        point_support_ctes(support, exposed=exposed)
        + """, target AS (
    SELECT * FROM supports
    WHERE abs(support_west - ?::DOUBLE) <= """
        + repr(_SAME_SUPPORT_DEGREES)
        + " AND abs(support_south - ?::DOUBLE) <= "
        + repr(_SAME_SUPPORT_DEGREES)
        + "\n)"
    )


def _geometry_target(support: GeometrySupport) -> str:
    """Every polygon row whose exact geometry contains the probe (parameters: part list, longitude, latitude)."""
    geometry = f'"{support.geometry_column}"'
    return f"""source AS (
    SELECT * EXCLUDE ({geometry}), ST_GeomFromWKB({geometry}) AS geometry
    FROM read_parquet(?, union_by_name=true, hive_partitioning=false, filename=true)
), target AS (
    SELECT * EXCLUDE (geometry) FROM source WHERE ST_Intersects(geometry, ST_Point(?, ?))
)"""


# --- One lane ----------------------------------------------------------------------------------


@dataclass
class _WindowPlan:
    """Capture the part files `resolve_window` would read; the window is aggregated, never loaded as rows."""

    keys: tuple[str, ...] = ()

    def read_rows(self, read: RowRead) -> RowReadResult:
        self.keys = read.keys
        return RowReadResult(rows=(), budget_exhausted=False, unpositioned_rows=0)


@dataclass(frozen=True, slots=True)
class _Spatial:
    """Where the window's values come from: the covering support, the nearest one, or the region-wide absence."""

    relation: str
    distance_km: float | None
    basis: str | None
    #: The chosen point-lattice support corner; None for a geometry lane (exact containment) or no rows.
    corner: tuple[float, float] | None = None

    def to_wire(self) -> dict[str, Any]:
        return {"spatial_relation": self.relation, "distance_km": self.distance_km, "distance_km_basis": self.basis}


_NO_SPATIAL: Final = {"spatial_relation": None, "distance_km": None, "distance_km_basis": None}
#: A counted governed absence is region-wide ("FIRMS returned zero detections"), so it covers the point.
_REGION_WIDE: Final = _Spatial("covers", 0.0, "covers")


def _window_spatial(supports: list[dict[str, Any]], lane: str) -> _Spatial | None:
    """The covering support when any day's support contains the point, else the one nearest across the window."""
    if not supports:
        return None
    lead = supports[0]
    if lead.get("covers_probe_point") is True:
        relation = "covers"
    else:
        measured = [row for row in supports if row.get("distance_km") is not None]
        if not measured:
            return None
        lead = lead if lead.get("nearest_cell") is True else min(measured, key=lambda row: row["distance_km"])
        relation = nearest_relation(lane)
    corner = (
        (float(lead["support_west"]), float(lead["support_south"]))
        if lead.get("support_west") is not None and lead.get("support_south") is not None
        else None
    )
    return _Spatial(relation, lead.get("distance_km"), lead.get("distance_km_basis"), corner)


@dataclass
class _LaneAnswer:
    """What one lane's window read produced, before it is rendered per measure."""

    day_states: dict[str, int]
    spatial: _Spatial | None
    groups: list[dict[str, Any]] = field(default_factory=list)


def _aggregate(connection: Any, statement: str, parameters: list[object]) -> list[dict[str, Any]]:
    cursor = connection.execute(statement, parameters)
    columns = [entry[0] for entry in cursor.description or ()]
    return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]


def _absence_only(
    connection: Any, day_states: dict[str, int], measures: Sequence[WindowMeasure], absent: list[date]
) -> _LaneAnswer:
    """No row anywhere in the window: only the counted governed absences (region-wide zeros) answer."""
    if not absent:
        return _LaneAnswer(day_states, None)
    return _LaneAnswer(
        day_states, _REGION_WIDE, _aggregate(connection, distribution_statement(None, measures), [absent])
    )


def _read_lane(session: ServingSession, surface: str, lane: str, window: DistributionWindow) -> _LaneAnswer:
    """Classify every window day through `resolve_window`, find the point's support once, aggregate once."""
    selection = window.selection
    measures = WINDOW_MEASURES[lane]
    scope = ReadScope(layer=lane, kind=WINDOW_KIND, tier=selection.tier, bbox=selection.bbox)
    listing = warehouse.source().authorized_listing(scope)
    plan = _WindowPlan()
    envelopes: Sequence[DayEnvelope] = resolve_window(
        listing, plan, scope=scope, first_day=window.first, last_day=window.last
    )
    day_states = dict(Counter(str(envelope.to_wire()["state"]) for envelope in envelopes))
    counts_absence = any(measure.absence_value is not None for measure in measures)
    absent = [envelope.requested_day for envelope in envelopes if isinstance(envelope, GovernedAbsenceDay)]
    absent = absent if counts_absence else []
    if not plan.keys:
        return _absence_only(session.connection, day_states, measures, absent)
    support = spatial_support(lane, WINDOW_KIND)
    with verified_serving_session(listing, session, plan.keys) as verified:
        uris = [verified.object_uri(key) for key in plan.keys]
        days = [day_of_part_key(key) for key in plan.keys]
        # ONE nearest search for the whole window: the selection SQL runs over every window part at once.
        reader = SelectionReader(session, listing, selection, surface, nearest_search=True)
        spatial = _window_spatial(
            reader.select_support(verified, lane, WINDOW_KIND, uris, limit=MAX_FEATURES + 1), lane
        )
        if spatial is None:
            return _absence_only(verified.connection, day_states, measures, absent)
        if spatial.relation == "nearest_area_outside":
            # Outside every area is the answer, not a value: no statistics are computed.
            return _LaneAnswer(day_states, spatial)
        tail: list[object] = [uris, days, absent]
        if isinstance(support, PointSupport) and spatial.corner is not None:
            exposed = "allowed_client_exposure" in get_stream_schema(lane, WINDOW_KIND).column_names
            statement = distribution_statement(_point_target(support, exposed=exposed), measures)
            lattice = support_lattice(surface, selection.tier)
            parameters: list[object] = [
                *lattice,
                *selection.bbox.as_envelope_arguments,
                selection.longitude,
                selection.latitude,
                uris,
                *spatial.corner,
                *tail,
            ]
        elif isinstance(support, GeometrySupport) and spatial.relation == "covers":
            statement = distribution_statement(_geometry_target(support), measures)
            parameters = [uris, selection.longitude, selection.latitude, *tail]
        else:
            # A nearest POLYGON of a tiling lane has no fixed identity across days; no dated lane has one.
            return _LaneAnswer(day_states, spatial)
        return _LaneAnswer(day_states, spatial, _aggregate(verified.connection, statement, parameters))


def _stats(group: dict[str, Any]) -> dict[str, float | None]:
    return {key: group[key] for key in ("min", "p10", "median", "p90", "max", "mean")}


def _lane_entry(  # noqa: PLR0913 - one argument per wire field the caller decides
    lane: str,
    *,
    signal_name: str,
    unit: str | None,
    window: DistributionWindow,
    state: str,
    spatial: dict[str, Any],
    days_with_data: int = 0,
    stats: dict[str, float | None] | None = None,
    static: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "parquet_lane": lane,
        "signal_name": signal_name,
        "unit": unit,
        "days_in_window": window.days_in_window,
        "days_with_data": days_with_data,
        "stats": stats,
        **spatial,
        "static": static,
        "state": state,
        **extra,
    }


def _render_lane(lane: str, window: DistributionWindow, answer: _LaneAnswer) -> list[dict[str, Any]]:
    """One entry per measure (per label group when the lane labels its rows), in measure order."""
    measures = WINDOW_MEASURES[lane]
    spatial = answer.spatial.to_wire() if answer.spatial is not None else dict(_NO_SPATIAL)
    absence_value = next((m.absence_value for m in measures if m.absence_value is not None), None)
    extra: dict[str, Any] = {"day_states": answer.day_states, "governed_absence_as_value": absence_value}
    outside = answer.spatial is not None and answer.spatial.relation == "nearest_area_outside"
    entries: list[dict[str, Any]] = []
    for index, measure in enumerate(measures):
        groups = [group for group in answer.groups if group["measure_index"] == index and group["days_with_data"]]
        if not groups:
            # Outside every area is a published answer with no statistics; otherwise nothing in the window.
            state = "published" if outside else "no_data_in_window"
            entries.append(
                _lane_entry(
                    lane,
                    signal_name=measure.signal_name,
                    unit=measure.unit,
                    window=window,
                    state=state,
                    spatial=spatial,
                    **extra,
                )
            )
            continue
        entries.extend(
            _lane_entry(
                lane,
                signal_name=str(group["signal_name"]),
                unit=group["unit"],
                window=window,
                state="published",
                spatial=spatial,
                days_with_data=int(group["days_with_data"]),
                stats=_stats(group),
                **extra,
            )
            for group in groups
        )
    return entries


def _label_only(
    lane: str, window: DistributionWindow, state: str, *, static: bool = False, **extra: Any
) -> dict[str, Any]:
    """A lane with no statistics to give: static, a release lane, or refused. Labelled by its first measure."""
    measures = WINDOW_MEASURES.get(lane, ())
    return _lane_entry(
        lane,
        signal_name=measures[0].signal_name if measures else lane,
        unit=measures[0].unit if measures else None,
        window=window,
        state=state,
        spatial=dict(_NO_SPATIAL),
        static=static,
        **extra,
    )


async def lane_distribution(surface: str, lane: str, window: DistributionWindow) -> list[dict[str, Any]]:
    """Every entry one lane contributes: per-measure statistics, or the single reason it has none."""
    mode = day_tolerance(lane).mode
    if mode == "static":
        return [_label_only(lane, window, "static_not_applicable", static=True)]
    if mode == "as_of" or lane not in WINDOW_MEASURES:
        code = "release_lane_not_distributed" if mode == "as_of" else "no_window_measure"
        message = (
            "One release answers every day of a window; read it with surface_evidence_for_selection."
            if mode == "as_of"
            else "This lane declares no per-day numeric value at a point."
        )
        return [_label_only(lane, window, "refused", refusal_code=code, message=message)]

    def work(session: ServingSession) -> _LaneAnswer:
        try:
            return _read_lane(session, surface, lane, window)
        except duckdb.Error as error:
            raise faults.read_over_budget(operation="agent_distribution_at_point") from error

    try:
        answer = await warehouse.source().run(work, operation="agent_distribution_at_point")
    except faults.ServingRefusalError as error:
        return [_label_only(lane, window, "refused", refusal_code=error.code, message=error.message)]
    return _render_lane(lane, window, answer)


# --- One surface -------------------------------------------------------------------------------


def refusal(code: str, message: str, **context: object) -> dict[str, Any]:
    """A whole-call refusal in the result's own shape: `lanes` is empty and the reason is typed."""
    return {"state": "refused", "refusal_code": code, "message": message, "lanes": [], **context}


NOTE: Final = (
    "stats summarise the per-day values at the point over the window: days without data are excluded, "
    "never zero-filled, so read days_with_data against days_in_window. spatial_relation covers means the "
    "point's own cell; nearest_cell means the nearest cell or station across the window, distance_km away: "
    "say so, never that it is the value here. nearest_area_outside means the point lies inside no area of "
    "this layer on any day (for drought, no drought area): that is the answer, and there are no stats. "
    "governed_absence_as_value is the measured value a governed-absence day counted as (fire-detections: 0); "
    "null means absences were excluded. range_end is clamped to today (UTC)."
)


async def distribution(  # noqa: PLR0913 - one argument per published tool parameter
    surface_name: str,
    longitude: float,
    latitude: float,
    range_start: str,
    range_end: str,
    zoom: float | None = None,
) -> dict[str, Any]:
    """The distribution of one surface's lanes at a point over an inclusive calendar window."""
    context = {"surface": surface_name, "range_start": range_start, "range_end": range_end}
    try:
        window = parse_window(
            longitude=longitude,
            latitude=latitude,
            range_start=range_start,
            range_end=range_end,
            zoom=zoom,
            today=utc_today(),
        )
    except (RequestError, ValueError, TypeError) as error:
        return refusal("invalid_window", str(error), **context)
    if surface_name not in AGENT_SURFACE_NAMES:
        return refusal("unknown_surface", "Use list_environmental_layers for accepted surface names.", **context)
    if surface_name in APP_SURFACE_NAMES:
        return refusal(
            "app_surface_not_distributed",
            "This surface is answered by the map application's reader; use surface_evidence_for_selection.",
            **context,
        )
    binding = surface_region_layer(surface_name)
    if binding is not None and not is_layer_bound(load_region(), binding):
        return refusal("not_available_in_region", "This region binds no admitted source for this surface.", **context)
    lanes = surface_lanes(surface_name)
    if not lanes:
        return refusal("parquet_lane_not_published", "No governed map-serving lane serves this surface.", **context)
    entries: list[dict[str, Any]] = []
    for lane in lanes:
        entries.extend(await lane_distribution(surface_name, lane, window))
    return {
        "surface": surface_name,
        "range_start": window.first.isoformat(),
        "range_end": window.last.isoformat(),
        "requested_range_end": window.requested_last.isoformat(),
        "range_end_clamped": window.last != window.requested_last,
        "zoom_tier": window.selection.tier,
        "lanes": entries,
        "note": NOTE,
    }


__all__ = [
    "MAX_WINDOW_DAYS",
    "WINDOW_MEASURES",
    "DistributionWindow",
    "WindowMeasure",
    "distribution",
    "distribution_statement",
    "lane_distribution",
    "parse_window",
]
