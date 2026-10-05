"""Receipt-verified numeric evidence from the same lane, rung, and day as a map tile.

Closest-datapoint rules (nearest day, nearest cell, static lanes): agent/AGENTS.md,
"Closest-datapoint reads (2026-10-04)".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import duckdb

from agri_data_service.agent import warehouse
from agri_data_service.agent.day_tolerance import day_tolerance, nearest_published_day
from agri_data_service.agent.selection_geodesy import (
    box_contains,
    distance_to_box_km,
    haversine_km,
    inscribed_radius_km,
    search_boxes,
)
from agri_data_service.agent.selection_scope import (
    MAX_FEATURES,
    PAGE_DAYS,
    evidence_days,
    schedule_ceiling,
    support_lattice,
    utc_today,
)
from agri_data_service.foundation.region import load_region
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.authorized_serving import verified_serving_session
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.parquet_ops.serving import day_status_sets, resolve_day, resolve_release
from agri_data_service.parquet_ops.warehouse_reader import GeometrySupport, PointSupport, RowReadResult, spatial_support
from agri_data_service.parquet_ops.wire import GovernedAbsenceDay, PublishedDay
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from agri_data_service.agent.day_tolerance import DayTolerance
    from agri_data_service.agent.selection_scope import Selection
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.warehouse_reader import RowRead, WarehouseListing
    from agri_data_service.parquet_ops.wire import DayEnvelope


def point_selection_statement(support: PointSupport, *, exposed: bool) -> str:
    """Select every support intersecting the tile, preferring the support containing the click."""
    longitude, latitude = f'"{support.longitude_column}"', f'"{support.latitude_column}"'
    exposure = "AND allowed_client_exposure IS TRUE" if exposed else ""
    return f"""-- agent_selection_point_rows
WITH limits AS (
    SELECT ?::DOUBLE AS size, ?::DOUBLE AS phase, ?::DOUBLE AS correction,
           ?::DOUBLE AS west, ?::DOUBLE AS south, ?::DOUBLE AS east, ?::DOUBLE AS north,
           ?::DOUBLE AS probe_longitude, ?::DOUBLE AS probe_latitude
), supports AS (
    SELECT data.*,
        CASE WHEN size = 0 THEN {longitude}
             ELSE floor(({longitude} + correction - phase) / size) * size + phase END AS support_west,
        CASE WHEN size = 0 THEN {latitude}
             ELSE floor(({latitude} + correction - phase) / size) * size + phase END AS support_south,
        size, west, south, east, north, probe_longitude, probe_latitude
    FROM read_parquet(?, union_by_name=true, hive_partitioning=false, filename=true) AS data CROSS JOIN limits
    WHERE {longitude} IS NOT NULL AND {latitude} IS NOT NULL {exposure}
), selected AS (
    SELECT * EXCLUDE (size, west, south, east, north, probe_longitude, probe_latitude),
        support_west + size AS support_east, support_south + size AS support_north,
        {longitude} AS centroid_longitude, {latitude} AS centroid_latitude,
        ST_Distance_Spheroid(
            ST_Point({latitude}, {longitude}), ST_Point(probe_latitude, probe_longitude)
        ) AS distance_meters,
        probe_longitude >= support_west AND probe_longitude <= support_west + size
            AND probe_latitude >= support_south AND probe_latitude <= support_south + size AS covers_probe_point
    FROM supports
    WHERE support_west <= east AND support_west + size >= west
      AND support_south <= north AND support_south + size >= south
)
SELECT * FROM selected ORDER BY covers_probe_point DESC, distance_meters, centroid_longitude, centroid_latitude LIMIT ?
"""


def geometry_selection_statement(support: GeometrySupport) -> str:
    """Keep original numeric attributes and exact tile/point intersection for a geometry layer."""
    geometry = f'"{support.geometry_column}"'
    return f"""-- agent_selection_geometry_rows
WITH source AS (
    SELECT * EXCLUDE ({geometry}), ST_GeomFromWKB({geometry}) AS geometry
    FROM read_parquet(?, union_by_name=true, hive_partitioning=false, filename=true)
), selected AS (
    SELECT * EXCLUDE (geometry),
        ST_X(ST_Centroid(geometry)) AS centroid_longitude,
        ST_Y(ST_Centroid(geometry)) AS centroid_latitude,
        ST_Distance_Spheroid(
            ST_Point(ST_Y(ST_Centroid(geometry)), ST_X(ST_Centroid(geometry))), ST_Point(?, ?)
        ) AS distance_meters,
        ST_Intersects(geometry, ST_Point(?, ?)) AS covers_probe_point
    FROM source WHERE ST_Intersects(geometry, ST_MakeEnvelope(?, ?, ?, ?))
)
SELECT * FROM selected ORDER BY covers_probe_point DESC, distance_meters LIMIT ?
"""


#: Rows one nearest-cell box query may return; the nearest by cell edge is among the nearest by centroid.
NEAREST_CELL_CANDIDATES: Final = MAX_FEATURES + 1

#: The one day state the selected day may borrow the nearest published day for: nothing was written.
_SUBSTITUTABLE_STATES: Final = frozenset({"day_not_written"})
#: Unpublished states that REPORT the nearest published day. A `governed_absence` is a published
#: answer (fire-detections: FIRMS returned zero detections), so it is annotated, never replaced.
_NEAREST_DAY_STATES: Final = frozenset({"day_not_written", "governed_absence"})
#: The distance basis of polygon support, whose nearest neighbour is information, never the value here.
_OUTSIDE_AREA_BASIS: Final = "geometry_centroid"

Box = tuple[float, float, float, float]


def _cell_identity(row: dict[str, Any]) -> tuple[object, ...]:
    """Which physical support a row is, so the nearest cell is never listed twice."""
    return tuple(
        row.get(key) for key in ("filename", "centroid_longitude", "centroid_latitude", "support_west", "support_south")
    )


@dataclass
class SelectionReader:
    """A bounded spatial adapter inside the map's ordinary day/release resolver."""

    session: ServingSession
    listing: WarehouseListing
    selection: Selection
    surface: str
    #: Search past the tile when nothing covers the point. On ONLY for the selected-day answer and
    #: its substitution read; history days stay tile-only samples.
    nearest_search: bool = False
    cached_rows: dict[tuple[tuple[str, ...], bool], RowReadResult] = field(default_factory=dict)

    def read_rows(self, read: RowRead) -> RowReadResult:
        """Verify authorized bytes, select support in the tile, and add the nearest support when none covers."""
        selected = self.selection
        support = spatial_support(read.scope.layer, read.scope.kind)
        cap = min(MAX_FEATURES, read.row_budget)
        cache_key = (read.keys, self.nearest_search)
        if cache_key in self.cached_rows:
            return self.cached_rows[cache_key]
        lattice = support_lattice(self.surface, selected.tier)
        with verified_serving_session(self.listing, self.session, read.keys) as verified:
            uris = [verified.object_uri(key) for key in read.keys]
            required: tuple[str, ...]
            parameters: Callable[[Box, int], list[object]]
            if isinstance(support, PointSupport):
                required = (support.longitude_column, support.latitude_column)
                exposed = "allowed_client_exposure" in get_stream_schema(read.scope.layer, "observed").column_names
                statement = point_selection_statement(support, exposed=exposed)
                distance_basis = "source_coordinate"

                def parameters(box: Box, limit: int) -> list[object]:
                    return [*lattice, *box, selected.longitude, selected.latitude, uris, limit]

            elif isinstance(support, GeometrySupport):
                required = (support.geometry_column,)
                statement = geometry_selection_statement(support)
                distance_basis = "geometry_centroid"

                def parameters(box: Box, limit: int) -> list[object]:
                    return [
                        uris,
                        selected.latitude,
                        selected.longitude,
                        selected.longitude,
                        selected.latitude,
                        *box,
                        limit,
                    ]

            else:
                raise faults.bbox_unsupported(layer=read.scope.layer, reason=support.reason)
            self._require_columns(verified, uris, required, read.scope.layer)
            point_lattice = isinstance(support, PointSupport)

            def select(box: Box, limit: int) -> list[dict[str, Any]]:
                cursor = verified.connection.execute(statement, parameters(box, limit))
                columns = [entry[0] for entry in cursor.description or ()]
                found = [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
                for row in found:
                    row["distance_basis"] = distance_basis
                    self._measure(row, point_lattice=point_lattice, size=lattice[0])
                return found

            rows = select(selected.bbox.as_envelope_arguments, cap + 1)
            if self.nearest_search and not any(row.get("covers_probe_point") is True for row in rows):
                # A station lane (point support, no lattice) never "covers" a point: its nearest
                # station IS the answer, found by one bounded query rather than six expanding boxes.
                nearest = (
                    self._nearest_station(select)
                    if point_lattice and lattice[0] == 0
                    else self._nearest_cell(select, start_degrees=max(lattice[0], self._tile_half_span()))
                )
                if nearest is not None:
                    nearest["nearest_cell"] = True
                    rows = [nearest, *(row for row in rows if _cell_identity(row) != _cell_identity(nearest))]
            key_of_uri = dict(zip(uris, read.keys, strict=True))
            unpositioned = 0
            if isinstance(support, PointSupport) and support.nullable:
                longitude, latitude = f'"{support.longitude_column}"', f'"{support.latitude_column}"'
                probe = verified.connection.execute(
                    f"SELECT count(*) FROM read_parquet(?, union_by_name=true, hive_partitioning=false) "
                    f"WHERE {longitude} IS NULL OR {latitude} IS NULL",
                    [uris],
                ).fetchone()
                unpositioned = int(probe[0]) if probe else 0
        result = RowReadResult(
            rows=tuple((key_of_uri[str(row.pop("filename"))], row) for row in rows[:cap]),
            budget_exhausted=len(rows) > cap,
            unpositioned_rows=unpositioned,
        )
        self.cached_rows[cache_key] = result
        return result

    def _tile_half_span(self) -> float:
        """Half the selection tile's widest span, so the first nearest-cell box already holds the tile."""
        west, south, east, north = self.selection.bbox.as_envelope_arguments
        return max(east - west, north - south) / 2

    def _measure(self, row: dict[str, Any], *, point_lattice: bool, size: float) -> None:
        """Attach the great-circle distance to the cell's own support, and say what it measured."""
        probe = (self.selection.longitude, self.selection.latitude)
        if row.get("covers_probe_point") is True:
            row["distance_km"], row["distance_km_basis"] = 0.0, "covers"
        elif point_lattice and "support_west" in row:
            west, south = float(row["support_west"]), float(row["support_south"])
            row["distance_km"] = round(distance_to_box_km(*probe, (west, south, west + size, south + size)), 3)
            row["distance_km_basis"] = "cell_edge" if size > 0 else "source_coordinate"
        elif row.get("centroid_longitude") is None or row.get("centroid_latitude") is None:
            row["distance_km"], row["distance_km_basis"] = None, "unpositioned"
        else:
            centroid = (float(row["centroid_longitude"]), float(row["centroid_latitude"]))
            row["distance_km"] = round(haversine_km(*probe, *centroid), 3)
            row["distance_km_basis"] = "geometry_centroid"

    def _nearest_station(self, select: Callable[[Box, int], list[dict[str, Any]]]) -> dict[str, Any] | None:
        """One query over the region envelope (widened to hold the probe), nearest first: the proven nearest."""
        envelope = load_region().envelope
        longitude, latitude = self.selection.longitude, self.selection.latitude
        box = (
            min(envelope.west, longitude),
            min(envelope.south, latitude),
            max(envelope.east, longitude),
            max(envelope.north, latitude),
        )
        found = [row for row in select(box, NEAREST_CELL_CANDIDATES) if row.get("distance_km") is not None]
        return min(found, key=lambda row: (row["distance_km"], repr(_cell_identity(row)))) if found else None

    def _nearest_cell(
        self, select: Callable[[Box, int], list[dict[str, Any]]], *, start_degrees: float
    ) -> dict[str, Any] | None:
        """Expanding-box nearest-neighbour search, stopped once proven nearest or the region is covered."""
        envelope = load_region().envelope
        region = (envelope.west, envelope.south, envelope.east, envelope.north)
        longitude, latitude = self.selection.longitude, self.selection.latitude
        best: dict[str, Any] | None = None
        for box in search_boxes(longitude, latitude, start_degrees):
            found = [row for row in select(box, NEAREST_CELL_CANDIDATES) if row.get("distance_km") is not None]
            if found:
                best = min(found, key=lambda row: (row["distance_km"], repr(_cell_identity(row))))
                # A hit inside the box's inscribed circle cannot be beaten by anything outside the box.
                if best["distance_km"] <= inscribed_radius_km(longitude, latitude, box):
                    return best
            if box_contains(box, region):
                return best
        return best

    @staticmethod
    def _require_columns(session: ServingSession, uris: list[str], columns: tuple[str, ...], layer: str) -> None:
        """Prove spatial columns on every object, never merely their schema union."""
        found: dict[str, set[str]] = {}
        for path, name in session.connection.execute(
            "SELECT file_name, name FROM parquet_schema(?)", [uris]
        ).fetchall():
            found.setdefault(str(path), set()).add(str(name))
        for path in uris:
            missing = set(columns) - found.get(path, set())
            if missing:
                raise faults.bbox_columns_absent(layer=layer, columns=tuple(sorted(missing)), key=path)


_RESERVED_FEATURE_KEYS: Final = frozenset(
    {
        "distance_meters",
        "distance_basis",
        "distance_km",
        "distance_km_basis",
        "nearest_cell",
        "centroid_longitude",
        "centroid_latitude",
        "covers_probe_point",
        "support_west",
        "support_south",
        "support_east",
        "support_north",
    }
)


def _nearest_relation(basis: object) -> str:
    """`nearest_area_outside` for a polygon (outside every area is itself the answer), else `nearest_cell`."""
    return "nearest_area_outside" if basis == _OUTSIDE_AREA_BASIS else "nearest_cell"


def _spatial_relation(row: dict[str, Any]) -> str:
    if row.get("covers_probe_point") is True:
        return "contains_selection"
    if row.get("nearest_cell") is True:
        return _nearest_relation(row.get("distance_km_basis"))
    return "intersects_selection_tile"


def feature(row: dict[str, Any], *, day: date, selected: date | None) -> dict[str, Any]:
    """Separate provenance and actual support from the lane's original measurement columns.

    `selected=None` marks a static lane: a version stamp has no temporal distance to report.
    """
    result: dict[str, Any] = {"served_day": day.isoformat()}
    if selected is not None:
        result["distance_days"] = abs((day - selected).days)
        result["temporal_relation"] = "selected_day" if day == selected else "before" if day < selected else "after"
    result.update(
        {
            "distance_meters": row.get("distance_meters"),
            "distance_basis": row.get("distance_basis"),
            "distance_km": row.get("distance_km"),
            "distance_km_basis": row.get("distance_km_basis"),
            "centroid_longitude": row.get("centroid_longitude"),
            "centroid_latitude": row.get("centroid_latitude"),
            "covers_probe_point": row.get("covers_probe_point") is True,
            "spatial_relation": _spatial_relation(row),
            "properties": {key: value for key, value in row.items() if key not in _RESERVED_FEATURE_KEYS},
        }
    )
    if "support_west" in row:
        result["support_bbox"] = [row["support_west"], row["support_south"], row["support_east"], row["support_north"]]
    return result


def spatial_summary(features: list[dict[str, Any]]) -> dict[str, Any]:
    """One lane-day's spatial answer: `covers` at 0 km, else the nearest support and its distance.

    A polygon lane's nearest area is `nearest_area_outside`: information, never the value at the point.
    """
    if any(entry["covers_probe_point"] for entry in features):
        return {"spatial_relation": "covers", "distance_km": 0.0, "distance_km_basis": "covers"}
    measured = [entry for entry in features if entry.get("distance_km") is not None]
    if not measured:
        return {}
    searched = {"nearest_cell", "nearest_area_outside"}
    nearest = min(measured, key=lambda entry: (entry["spatial_relation"] not in searched, entry["distance_km"]))
    return {
        "spatial_relation": _nearest_relation(nearest["distance_km_basis"]),
        "distance_km": nearest["distance_km"],
        "distance_km_basis": nearest["distance_km_basis"],
    }


def _render(envelope: DayEnvelope, *, selected_day: date | None) -> dict[str, Any]:
    """Render one resolved envelope as an evidence entry with features, offsets and a spatial summary."""
    if isinstance(envelope, PublishedDay):
        features = [feature(dict(row), day=envelope.served_day, selected=selected_day) for row in envelope.rows]
        result: dict[str, Any] = {
            "state": "published",
            "requested_day": envelope.requested_day.isoformat(),
            "served_day": envelope.served_day.isoformat(),
            "day_offset": (envelope.served_day - envelope.requested_day).days,
            "features": features,
            "features_truncated": envelope.truncated,
            **spatial_summary(features),
        }
        if envelope.mtbs_snapshot is not None:
            result["mtbs_snapshot"] = envelope.mtbs_snapshot.to_wire()
        return result
    wire: dict[str, Any] = dict(envelope.to_wire())
    if isinstance(envelope, GovernedAbsenceDay):
        wire["day_offset"] = (envelope.served_day - envelope.requested_day).days
    return {**wire, "features": [], "features_truncated": False}


def _refused(day: date, error: faults.ServingRefusalError) -> dict[str, Any]:
    return {
        "state": "refused",
        "requested_day": day.isoformat(),
        "refusal_code": error.code,
        "message": str(error),
        "features": [],
    }


async def lane_selection(  # noqa: PLR0913 - one argument per coordinate of a lane read
    surface: str,
    lane: str,
    nature: str | None,
    selected: Selection,
    page_days: int = PAGE_DAYS,
    *,
    today: date | None = None,
    kind: PartitionKind = "observed",
) -> dict[str, Any]:
    """Read one lane's selected day and balanced history, substituting the nearest day within tolerance.

    Reads per lane: the selected day, at most `page_days` other history days, and at most one
    substitution read; `selection_scope.RESERVED_READS_PER_LANE` budgets the last two outside the
    page. Only the selected-day answer searches past the tile for the nearest support.
    """
    scope = ReadScope(layer=lane, kind=kind, tier=selected.tier, bbox=selected.bbox)
    policy = day_tolerance(lane)
    ceiling = schedule_ceiling(kind, today or utc_today())

    def work(session: ServingSession) -> dict[str, Any]:
        listing = warehouse.source().authorized_listing(scope)
        reader = SelectionReader(session, listing, selected, surface)
        published: frozenset[date] = frozenset()
        if policy.mode == "nearest":
            # One year each side of the selected day too, so "the nearest is 40 days back" is findable.
            first_year = min(selected.first.year, selected.day.year - 1)
            last_year = max(selected.last.year, selected.day.year + 1)
            if ceiling is not None:
                last_year = min(last_year, ceiling.year)
            keys = tuple(
                key
                for year in range(first_year, last_year + 1)
                for key in listing.list_keys(lane, kind, selected.tier, year=year)
            )
            statuses = day_status_sets(keys, layer=lane, kind=kind, tier=selected.tier)
            published = frozenset(day for day in statuses.data if ceiling is None or day <= ceiling)
            schedule = evidence_days(selected.first, selected.last, selected.day, set(published), today=ceiling)
            page = tuple(sorted(schedule[selected.page_start : selected.page_start + page_days]))
        else:
            page = selected.page(page_days, today=ceiling)
        reads = 0

        def resolve(day: date, *, nearest: bool) -> dict[str, Any]:
            nonlocal reads
            reads += 1
            reader.nearest_search = nearest
            try:
                envelope = (
                    resolve_release(listing, reader, scope=scope, as_of=day)
                    if policy.mode == "as_of"
                    else resolve_day(listing, reader, scope=scope, day=day)
                )
                return _render(envelope, selected_day=selected.day)
            except faults.ServingRefusalError as error:
                return _refused(day, error)
            finally:
                reader.nearest_search = False

        answer = resolve(selected.day, nearest=True)
        history = [answer if day == selected.day else resolve(day, nearest=False) for day in page]
        sampled = dict(zip(page, history, strict=True))

        def substitute(day: date) -> dict[str, Any]:
            # A history read that already covers the point is the answer; only otherwise search again.
            read = sampled.get(day)
            return read if read is not None and read.get("spatial_relation") == "covers" else resolve(day, nearest=True)

        chosen = answer
        if policy.mode == "nearest" and answer["state"] in _NEAREST_DAY_STATES:
            chosen = _nearest_day(answer, substitute, published, selected.day, policy, ceiling)
        return {
            "parquet_lane": lane,
            "lane_nature": nature,
            **policy.to_wire(),
            "selected": chosen,
            "history": history,
            "lane_day_reads": reads,
        }

    try:
        return await warehouse.source().run(work, operation="agent_surface_evidence_for_selection")
    except duckdb.Error as error:
        raise faults.read_over_budget(operation="agent_surface_evidence_for_selection") from error


def _nearest_day(  # noqa: PLR0913 - the unpublished answer plus every input of the substitution
    unpublished: dict[str, Any],
    read: Callable[[date], dict[str, Any]],
    published: frozenset[date],
    requested: date,
    policy: DayTolerance,
    today: date | None,
) -> dict[str, Any]:
    """Name the nearest published day; serve it as `published_nearest` only for an unwritten day in tolerance.

    A `governed_absence` keeps its state: it is a published answer (a measured zero), and another
    day's numbers must never replace it. It gains `nearest_published_day`/`nearest_day_offset` only.
    """
    if policy.tolerance_days is None:
        return unpublished
    nearest = nearest_published_day(published, requested, tolerance_days=policy.tolerance_days, today=today)
    if nearest is None:
        return {**unpublished, "nearest_published_day": None, "nearest_day_offset": None}
    informed = {**unpublished, "nearest_published_day": nearest.day.isoformat(), "nearest_day_offset": nearest.offset}
    if unpublished["state"] not in _SUBSTITUTABLE_STATES or not nearest.within_tolerance:
        return informed
    served = read(nearest.day)
    if served["state"] != "published":
        return informed
    return {
        **served,
        "state": "published_nearest",
        "requested_day": requested.isoformat(),
        "served_day": nearest.day.isoformat(),
        "day_offset": nearest.offset,
        "requested_day_state": unpublished["state"],
    }


async def static_lane_selection(surface: str, lane: str, nature: str | None, selected: Selection) -> dict[str, Any]:
    """Read a static lane ONCE at its current published release; it takes no date and no history."""
    scope = ReadScope(layer=lane, kind="observed", tier=selected.tier, bbox=selected.bbox)
    policy = day_tolerance(lane)

    def work(session: ServingSession) -> dict[str, Any]:
        listing = warehouse.source().authorized_listing(scope)
        # A static lane's one read IS its selected answer, so it searches past the tile.
        reader = SelectionReader(session, listing, selected, surface, nearest_search=True)
        today = utc_today()
        try:
            envelope = resolve_release(listing, reader, scope=scope, as_of=today)
        except faults.ServingRefusalError as error:
            refused = _refused(today, error)
            refused.pop("requested_day")
            return {**refused, "static": True}
        rendered = _render(envelope, selected_day=None)
        rendered.pop("requested_day", None)
        rendered.pop("day_offset", None)
        if "served_day" in rendered:
            rendered["release_day"] = rendered.pop("served_day")
        return {**rendered, "static": True}

    try:
        result = await warehouse.source().run(work, operation="agent_surface_evidence_for_selection")
    except duckdb.Error as error:
        raise faults.read_over_budget(operation="agent_surface_evidence_for_selection") from error
    return {
        "parquet_lane": lane,
        "lane_nature": nature,
        "static": True,
        **policy.to_wire(),
        "selected": result,
        "history": [],
    }
