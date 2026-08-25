"""Reading the object store on a request path: what a tier holds, and the rows behind it.

Layer L4. Two ports so the routes can be tested without a network -- a LISTING (which days exist)
and a ROW READER (what those days hold). See `AGENTS.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from agri_data_service.foundation.parquet.paths import (
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
    zoom_prefix,
)
from agri_data_service.interface.http import faults
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.interface.http.duckdb_session import ServingSession
    from agri_data_service.interface.http.request_params import BoundingBox, ReadScope
    from agri_data_service.interface.http.wire import ServedRow
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend

#: One serving listing's key budget. Well under the pipeline's 500,000: a request path that has to
#: walk half a million keys has already lost, and the census memoizes rather than paying it twice.
MAX_LISTED_KEYS_PER_REQUEST: Final = 200_000

#: DuckDB's `filename` column, renamed so it cannot collide with a lane's own column and so a row
#: dict never carries it to the wire by accident.
SOURCE_KEY_COLUMN: Final = "_serving_source_key"

#: Longitude/latitude pairs the twelve lanes actually use, in the order they are looked for.
POINT_COLUMN_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("cell_longitude", "cell_latitude"),
    ("station_longitude", "station_latitude"),
    ("longitude", "latitude"),
)

#: WKB geometry columns the twelve lanes actually use.
GEOMETRY_COLUMN_NAMES: Final[tuple[str, ...]] = ("geom", "geometry_wkb")


@dataclass(frozen=True, slots=True)
class PointSupport:
    """The lane carries a representative point, so a bbox is an ordinary range predicate."""

    longitude_column: str
    latitude_column: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class GeometrySupport:
    """The lane carries WKB, so a bbox is an intersection -- and the geometry is CLIPPED to it."""

    geometry_column: str


@dataclass(frozen=True, slots=True)
class NoSpatialSupport:
    """The lane has no spatial extent at all; `calendar` is the whole of this case."""

    reason: str


type SpatialSupport = PointSupport | GeometrySupport | NoSpatialSupport


def spatial_support(layer: str, kind: PartitionKind) -> SpatialSupport:
    """Decide how a bbox narrows one lane, from its REGISTERED schema and never from a guess."""
    schema = get_stream_schema(layer, kind).arrow_schema
    names = frozenset(schema.names)
    for longitude, latitude in POINT_COLUMN_PAIRS:
        if longitude in names and latitude in names:
            nullable = bool(schema.field(longitude).nullable or schema.field(latitude).nullable)
            return PointSupport(longitude_column=longitude, latitude_column=latitude, nullable=nullable)
    for column in GEOMETRY_COLUMN_NAMES:
        if column in names:
            return GeometrySupport(geometry_column=column)
    return NoSpatialSupport(reason="its registered schema declares neither a coordinate pair nor a WKB column")


class WarehouseListing(Protocol):
    """What exists: one tier's object keys, and the bytes of one marker."""

    def list_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]: ...

    def read_object(self, relative_key: str) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class ObjectStoreListing:
    """`WarehouseListing` over one bucket, addressing the frozen layout through `foundation.parquet`."""

    backend: ObjectStoreBackend
    prefix: str = ""

    def key_for(self, relative_key: str) -> str:
        """Return the absolute bucket key for a path expressed in the frozen layout."""
        return f"{self.prefix}{relative_key}"

    def list_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Return every part file, absence marker and completion marker of ONE tier, optionally narrowed."""
        scope = _listing_scope(layer, kind, tier, year, month)
        found: list[str] = []
        for listed in self.backend.list_objects(self.key_for(scope)):
            if not listed.key.startswith(self.prefix):
                continue
            relative_key = listed.key[len(self.prefix) :]
            if not _is_layout_object(relative_key):
                continue
            found.append(relative_key)
            if len(found) > MAX_LISTED_KEYS_PER_REQUEST:
                raise ValueError(f"listing {scope!r} exceeded the {MAX_LISTED_KEYS_PER_REQUEST}-key serving budget")
        return tuple(sorted(found))

    def read_object(self, relative_key: str) -> bytes | None:
        """Return one object's bytes, or `None` when it is not there."""
        return self.backend.get(self.key_for(relative_key))


@dataclass(frozen=True, slots=True)
class RowRead:
    """One bounded scan: which part files, narrowed how, and how many rows may come back."""

    scope: ReadScope
    keys: tuple[str, ...]
    row_budget: int


@dataclass(frozen=True, slots=True)
class RowReadResult:
    """Rows attributed to the part file each came from, plus what the read did NOT serve."""

    rows: tuple[tuple[str, ServedRow], ...]
    budget_exhausted: bool
    unpositioned_rows: int


class PartitionRowReader(Protocol):
    """What those days hold: rows, bounded, attributed to their source key."""

    def read_rows(self, read: RowRead) -> RowReadResult: ...


@dataclass(frozen=True, slots=True)
class DuckDbRowReader:
    """`PartitionRowReader` over one memory-capped DuckDB session; every read is bounded and ordered."""

    session: ServingSession

    def read_rows(self, read: RowRead) -> RowReadResult:
        """Read one bounded, key-ordered batch, clipping geometry to the viewport when one is given."""
        if not read.keys:
            return RowReadResult(rows=(), budget_exhausted=False, unpositioned_rows=0)
        support = spatial_support(read.scope.layer, read.scope.kind)
        key_of_uri = {self.session.object_uri(key): key for key in read.keys}
        uris = list(key_of_uri)
        if read.scope.bbox is not None:
            self._refuse_unapplicable_bbox(uris, support, read.scope)
        projection, projection_parameters = _projection(support, read.scope.bbox)
        predicate, predicate_parameters = _predicate(support, read.scope.bbox)
        statement = (
            f"SELECT {projection} FROM read_parquet(?, hive_partitioning=false, filename=true, union_by_name=true) "
            f"{predicate} ORDER BY filename LIMIT ?"
        )
        parameters: list[object] = [*projection_parameters, uris, *predicate_parameters, read.row_budget + 1]
        cursor = self.session.connection.execute(statement, parameters)
        columns = [description[0] for description in cursor.description or ()]
        fetched = cursor.fetchall()
        budget_exhausted = len(fetched) > read.row_budget
        rows = tuple(_attributed_row(columns, values, key_of_uri) for values in fetched[: read.row_budget])
        return RowReadResult(
            rows=rows,
            budget_exhausted=budget_exhausted,
            unpositioned_rows=self._unpositioned_rows(uris, support, read.scope.bbox),
        )

    def _refuse_unapplicable_bbox(self, uris: list[str], support: SpatialSupport, scope: ReadScope) -> None:
        """Refuse a viewport the objects cannot answer, rather than widening the read to the world."""
        if isinstance(support, NoSpatialSupport):
            raise faults.bbox_unsupported(layer=scope.layer, reason=support.reason)
        required = (
            (support.longitude_column, support.latitude_column)
            if isinstance(support, PointSupport)
            else (support.geometry_column,)
        )
        cursor = self.session.connection.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=true) LIMIT 0", [uris]
        )
        present = {description[0] for description in cursor.description or ()}
        missing = tuple(column for column in required if column not in present)
        if missing:
            raise faults.bbox_columns_absent(layer=scope.layer, columns=missing)

    def _unpositioned_rows(self, uris: list[str], support: SpatialSupport, bbox: BoundingBox | None) -> int:
        """Count rows a viewport could not judge; only a nullable coordinate pair can produce one."""
        if bbox is None or not isinstance(support, PointSupport) or not support.nullable:
            return 0
        statement = (
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=false, union_by_name=true) "
            f'WHERE "{support.longitude_column}" IS NULL OR "{support.latitude_column}" IS NULL'
        )
        counted = self.session.connection.execute(statement, [uris]).fetchone()
        return int(counted[0]) if counted is not None else 0


def _attributed_row(
    columns: list[str],
    values: tuple[object, ...],
    key_of_uri: Mapping[str, str],
) -> tuple[str, ServedRow]:
    """Split one fetched tuple into the RELATIVE key it came from and the lane's own cells."""
    row = dict(zip(columns, values, strict=True))
    source_uri = str(row.pop(SOURCE_KEY_COLUMN))
    key = key_of_uri.get(source_uri)
    if key is None:
        raise ValueError(f"row attributed to {source_uri!r}, which is not one of the keys this read asked for")
    return (key, row)


def _projection(support: SpatialSupport, bbox: BoundingBox | None) -> tuple[str, list[object]]:
    """Build the select list: the lane's own columns, with WKB rendered as GeoJSON and clipped."""
    if not isinstance(support, GeometrySupport):
        return (f"* EXCLUDE (filename), filename AS {SOURCE_KEY_COLUMN}", [])
    column = support.geometry_column
    if bbox is None:
        geometry = f'ST_AsGeoJSON(ST_GeomFromWKB("{column}"))'
        return (f'* EXCLUDE (filename, "{column}"), {geometry} AS "{column}", filename AS {SOURCE_KEY_COLUMN}', [])
    # CLIP BEFORE PROBING. Measured 2026-08-25 on the 2026-08-04 USDM release: the largest polygon
    # falls from 124,676 vertices to 6,151 against the PNW envelope, with no precision loss inside
    # the region actually requested. `ST_Simplify` was evaluated and rejected -- it can move a
    # boundary and flip a cell -- so clipping is the lever, not simplification.
    geometry = f'ST_AsGeoJSON(ST_Intersection(ST_GeomFromWKB("{column}"), ST_MakeEnvelope(?, ?, ?, ?)))'
    return (
        f'* EXCLUDE (filename, "{column}"), {geometry} AS "{column}", filename AS {SOURCE_KEY_COLUMN}',
        list(bbox.as_envelope_arguments),
    )


def _predicate(support: SpatialSupport, bbox: BoundingBox | None) -> tuple[str, list[object]]:
    """Build the viewport predicate, or nothing at all for an unbounded read."""
    if bbox is None:
        return ("", [])
    if isinstance(support, PointSupport):
        return (
            f'WHERE "{support.longitude_column}" BETWEEN ? AND ? AND "{support.latitude_column}" BETWEEN ? AND ?',
            [bbox.west, bbox.east, bbox.south, bbox.north],
        )
    if isinstance(support, GeometrySupport):
        return (
            f'WHERE ST_Intersects(ST_GeomFromWKB("{support.geometry_column}"), ST_MakeEnvelope(?, ?, ?, ?))',
            list(bbox.as_envelope_arguments),
        )
    raise faults.bbox_unsupported(layer="this lane", reason=support.reason)


def _listing_scope(layer: str, kind: PartitionKind, tier: ZoomTier, year: int | None, month: int | None) -> str:
    """Narrow a listing to one tier, then optionally to a year and a month inside it."""
    prefix = zoom_prefix(layer, kind, tier)
    if year is None:
        if month is not None:
            raise ValueError("narrowing a listing to a month requires the year as well")
        return prefix
    if month is None:
        return f"{prefix}year={year:04d}/"
    return f"{prefix}year={year:04d}/month={month:02d}/"


def _is_layout_object(relative_key: str) -> bool:
    """True for a part file, an absence marker or a completion marker of the frozen layout."""
    return (
        try_parse_partition_path(relative_key) is not None
        or try_parse_absence_marker_path(relative_key) is not None
        or try_parse_completion_marker_path(relative_key) is not None
    )


def part_keys_for_day(
    keys: tuple[str, ...],
    *,
    layer: str,
    kind: PartitionKind,
    tier: ZoomTier,
    day: date,
) -> tuple[str, ...]:
    """Return one day's part files at one tier, in part-index order."""
    parsed = []
    for key in keys:
        partition = try_parse_partition_path(key)
        if partition is not None and (partition.layer, partition.kind, partition.zoom, partition.day) == (
            layer,
            kind,
            tier,
            day,
        ):
            parsed.append((partition.part_index, key))
    return tuple(key for _, key in sorted(parsed))


def day_of_part_key(relative_key: str) -> date:
    """Return the partition day a part file belongs to, parsed by the layout module and nowhere else."""
    partition = try_parse_partition_path(relative_key)
    if partition is None:
        raise ValueError(f"{relative_key!r} is not a partition path of the frozen layout")
    return partition.day
