"""Receipt-verified numeric evidence from the same lane, rung, and day as a map tile."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import duckdb

from agri_data_service.agent import warehouse
from agri_data_service.agent.selection_scope import MAX_FEATURES, PAGE_DAYS, evidence_days, support_lattice
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.authorized_serving import verified_serving_session
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.parquet_ops.serving import day_status_sets, resolve_day, resolve_release
from agri_data_service.parquet_ops.warehouse_reader import GeometrySupport, PointSupport, RowReadResult, spatial_support
from agri_data_service.parquet_ops.wire import PublishedDay
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.agent.selection_scope import Selection
    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.warehouse_reader import RowRead, WarehouseListing


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


@dataclass
class SelectionReader:
    """A bounded spatial adapter inside the map's ordinary day/release resolver."""

    session: ServingSession
    listing: WarehouseListing
    selection: Selection
    surface: str
    cached_rows: dict[tuple[str, ...], RowReadResult] = field(default_factory=dict)

    def read_rows(self, read: RowRead) -> RowReadResult:
        """Verify authorized bytes before selecting numeric source support intersecting the tile."""
        selected = self.selection
        support = spatial_support(read.scope.layer, read.scope.kind)
        bbox = selected.bbox.as_envelope_arguments
        cap = min(MAX_FEATURES, read.row_budget)
        if read.keys in self.cached_rows:
            return self.cached_rows[read.keys]
        with verified_serving_session(self.listing, self.session, read.keys) as verified:
            uris = [verified.object_uri(key) for key in read.keys]
            required: tuple[str, ...]
            if isinstance(support, PointSupport):
                required = (support.longitude_column, support.latitude_column)
                exposed = "allowed_client_exposure" in get_stream_schema(read.scope.layer, "observed").column_names
                statement = point_selection_statement(support, exposed=exposed)
                parameters: list[object] = [
                    *support_lattice(self.surface, selected.tier),
                    *bbox,
                    selected.longitude,
                    selected.latitude,
                    uris,
                    cap + 1,
                ]
                distance_basis = "source_coordinate"
            elif isinstance(support, GeometrySupport):
                required = (support.geometry_column,)
                statement = geometry_selection_statement(support)
                parameters = [
                    uris,
                    selected.latitude,
                    selected.longitude,
                    selected.longitude,
                    selected.latitude,
                    *bbox,
                    cap + 1,
                ]
                distance_basis = "geometry_centroid"
            else:
                raise faults.bbox_unsupported(layer=read.scope.layer, reason=support.reason)
            self._require_columns(verified, uris, required, read.scope.layer)
            cursor = verified.connection.execute(statement, parameters)
            columns = [entry[0] for entry in cursor.description or ()]
            rows = [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
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
        for row in rows:
            row["distance_basis"] = distance_basis
        result = RowReadResult(
            rows=tuple((key_of_uri[str(row.pop("filename"))], row) for row in rows[:cap]),
            budget_exhausted=len(rows) > cap,
            unpositioned_rows=unpositioned,
        )
        self.cached_rows[read.keys] = result
        return result

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


def feature(row: dict[str, Any], *, day: date, selected: date) -> dict[str, Any]:
    """Separate provenance and actual support from the lane's original measurement columns."""
    reserved = {
        "distance_meters",
        "distance_basis",
        "centroid_longitude",
        "centroid_latitude",
        "covers_probe_point",
        "support_west",
        "support_south",
        "support_east",
        "support_north",
    }
    contains = row.get("covers_probe_point") is True
    result: dict[str, Any] = {
        "served_day": day.isoformat(),
        "distance_days": abs((day - selected).days),
        "temporal_relation": "selected_day" if day == selected else "before" if day < selected else "after",
        "distance_meters": row.get("distance_meters"),
        "distance_basis": row.get("distance_basis"),
        "centroid_longitude": row.get("centroid_longitude"),
        "centroid_latitude": row.get("centroid_latitude"),
        "covers_probe_point": contains,
        "spatial_relation": "contains_selection" if contains else "intersects_selection_tile",
        "properties": {key: value for key, value in row.items() if key not in reserved},
    }
    if "support_west" in row:
        result["support_bbox"] = [row["support_west"], row["support_south"], row["support_east"], row["support_north"]]
    return result


async def lane_selection(
    surface: str, lane: str, nature: str | None, selected: Selection, page_days: int = PAGE_DAYS
) -> dict[str, Any]:
    """Read one lane's selected day and balanced history under one authorized inventory."""
    scope = ReadScope(layer=lane, kind="observed", tier=selected.tier, bbox=selected.bbox)

    def work(session: ServingSession) -> dict[str, Any]:
        listing = warehouse.source().authorized_listing(scope)
        reader = SelectionReader(session, listing, selected, surface)
        if nature in {"static_lookup", "release_series"}:
            page = selected.page(page_days)
        else:
            keys = tuple(
                key
                for year in range(selected.first.year, selected.last.year + 1)
                for key in listing.list_keys(lane, "observed", selected.tier, year=year)
            )
            published = set(day_status_sets(keys, layer=lane, kind="observed", tier=selected.tier).data)
            schedule = evidence_days(selected.first, selected.last, selected.day, published)
            page = tuple(sorted(schedule[selected.page_start : selected.page_start + page_days]))
        days = tuple(sorted({*page, selected.day}))
        results: dict[date, dict[str, Any]] = {}
        for day in days:
            try:
                envelope = (
                    resolve_release(listing, reader, scope=scope, as_of=day)
                    if nature in {"static_lookup", "release_series"}
                    else resolve_day(listing, reader, scope=scope, day=day)
                )
                if isinstance(envelope, PublishedDay):
                    result: dict[str, Any] = {
                        "state": "published",
                        "requested_day": day.isoformat(),
                        "served_day": envelope.served_day.isoformat(),
                        "features": [
                            feature(dict(row), day=envelope.served_day, selected=selected.day) for row in envelope.rows
                        ],
                        "features_truncated": envelope.truncated,
                    }
                    if envelope.mtbs_snapshot is not None:
                        result["mtbs_snapshot"] = envelope.mtbs_snapshot.to_wire()
                else:
                    result = {**envelope.to_wire(), "features": [], "features_truncated": False}
            except faults.ServingRefusalError as error:
                result = {
                    "state": "refused",
                    "requested_day": day.isoformat(),
                    "refusal_code": error.code,
                    "message": str(error),
                    "features": [],
                }
            results[day] = result
        return {
            "parquet_lane": lane,
            "lane_nature": nature,
            "selected": results[selected.day],
            "history": [results[day] for day in page],
        }

    try:
        return await warehouse.source().run(work, operation="agent_surface_evidence_for_selection")
    except duckdb.Error as error:
        raise faults.read_over_budget(operation="agent_surface_evidence_for_selection") from error
