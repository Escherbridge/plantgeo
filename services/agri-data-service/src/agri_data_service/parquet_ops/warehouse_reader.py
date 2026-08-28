"""Object-store listing and DuckDB row-reader ports behind Parquet operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from agri_data_service.foundation.parquet.paths import (
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
    zoom_prefix,
)
from agri_data_service.parquet_ops import faults
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.request_params import BoundingBox, ReadScope
    from agri_data_service.parquet_ops.wire import ServedRow
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend

#: One serving listing's key budget. Well under the pipeline's 500,000: a request path that has to
#: walk half a million keys has already lost, and the census memoizes rather than paying it twice.
MAX_LISTED_KEYS_PER_REQUEST: Final = 200_000

#: DuckDB's `filename` column, renamed so it cannot collide with a lane's own column and so a row
#: dict never carries it to the wire by accident.
SOURCE_KEY_COLUMN: Final = "_serving_source_key"

#: The clipped geometry, held under its own name for one subquery so the clip is computed ONCE and
#: still filtered on. Leading underscore for the same reason as `SOURCE_KEY_COLUMN`.
CLIPPED_GEOMETRY_COLUMN: Final = "_serving_clipped_geometry"

#: `hive_partitioning=false` is not optional: with it on, DuckDB injects `layer`, `kind`, `zoom`,
#: `year`, `month` and `day` from the path, and `day` would ride to the wire as a lane's own column.
PARQUET_SOURCE: Final = "read_parquet(?, hive_partitioning=false, filename=true, union_by_name=true)"

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
    # True when the caller reports truncation PER DAY, which is the only case where an
    # unpositioned-row probe still changes an answer once the row budget is already exhausted.
    per_day_truncation: bool = False


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
            self._refuse_unapplicable_bbox(key_of_uri, support, read.scope)
        statement, parameters = _scan_statement(support, read.scope.bbox, uris=uris, row_budget=read.row_budget)
        cursor = self.session.connection.execute(statement, parameters)
        columns = [description[0] for description in cursor.description or ()]
        fetched = cursor.fetchall()
        budget_exhausted = len(fetched) > read.row_budget
        rows = tuple(_attributed_row(columns, values, key_of_uri) for values in fetched[: read.row_budget])
        return RowReadResult(
            rows=rows,
            budget_exhausted=budget_exhausted,
            unpositioned_rows=self._unpositioned_rows(uris, support, read, budget_exhausted=budget_exhausted),
        )

    def _refuse_unapplicable_bbox(
        self,
        key_of_uri: Mapping[str, str],
        support: SpatialSupport,
        scope: ReadScope,
    ) -> None:
        """Refuse a viewport that EVERY object in the read cannot answer, one object at a time."""
        if isinstance(support, NoSpatialSupport):
            raise faults.bbox_unsupported(layer=scope.layer, reason=support.reason)
        required = frozenset(
            (support.longitude_column, support.latitude_column)
            if isinstance(support, PointSupport)
            else (support.geometry_column,)
        )
        # PER OBJECT, never per union. `union_by_name=true` makes a probe's column set the UNION over
        # the whole read, so a mixed key set -- some days re-exported, some not -- passes as soon as
        # ONE object carries the columns. The predicate then evaluates NULL for every object that does
        # not and DROPS its rows, answering `published, rows: [], truncated: false` for days that hold
        # rows. Measured 2026-08-25 against two local parts: one row returned out of two.
        columns_by_object: dict[str, set[str]] = {}
        for file_name, column in self.session.connection.execute(
            "SELECT file_name, name FROM parquet_schema(?)", [list(key_of_uri)]
        ).fetchall():
            columns_by_object.setdefault(str(file_name), set()).add(str(column))
        # Iterating what was ASKED FOR rather than what came back: an object the probe did not report
        # is an object whose columns are unproven, and an unproven object is exactly the one whose
        # rows the predicate would silently drop. Refusing it is the fail-closed direction.
        for uri, key in sorted(key_of_uri.items(), key=lambda entry: entry[1]):
            missing = tuple(sorted(required - columns_by_object.get(uri, set())))
            if missing:
                raise faults.bbox_columns_absent(layer=scope.layer, columns=missing, key=key)

    def _unpositioned_rows(
        self,
        uris: list[str],
        support: SpatialSupport,
        read: RowRead,
        *,
        budget_exhausted: bool,
    ) -> int:
        """Report whether the viewport left rows it could not judge; a nullable pair is the only source."""
        if read.scope.bbox is None or not isinstance(support, PointSupport) or not support.nullable:
            return 0
        if budget_exhausted and not read.per_day_truncation:
            # `truncated` is already forced, and this caller reports one flag for the whole read, so
            # the answer cannot change. A second scan of every part file to confirm it is pure cost.
            return 0
        # EXISTENCE, not a census: the caller only ever compares this against zero, and a bounded probe
        # stops at the first null instead of counting every one over every part file.
        statement = (
            "SELECT count(*) FROM (SELECT 1 FROM read_parquet(?, hive_partitioning=false, union_by_name=true) "
            f'WHERE "{support.longitude_column}" IS NULL OR "{support.latitude_column}" IS NULL LIMIT 1)'
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


def _scan_statement(
    support: SpatialSupport,
    bbox: BoundingBox | None,
    *,
    uris: list[str],
    row_budget: int,
) -> tuple[str, list[object]]:
    """Build one bounded, key-ordered scan; a geometry lane under a viewport is clipped in a subquery."""
    limit = row_budget + 1
    if isinstance(support, GeometrySupport) and bbox is not None:
        return _clipped_scan(support, bbox, uris=uris, limit=limit)
    predicate, predicate_parameters = _predicate(support, bbox)
    statement = f"SELECT {_projection(support)} FROM {PARQUET_SOURCE} {predicate} ORDER BY filename LIMIT ?"
    return (statement, [uris, *predicate_parameters, limit])


def _projection(support: SpatialSupport) -> str:
    """Build the select list: the lane's own columns, with unclipped WKB rendered as GeoJSON."""
    if not isinstance(support, GeometrySupport):
        return f"* EXCLUDE (filename), filename AS {SOURCE_KEY_COLUMN}"
    column = support.geometry_column
    geometry = f'ST_AsGeoJSON(ST_GeomFromWKB("{column}"))'
    return f'* EXCLUDE (filename, "{column}"), {geometry} AS "{column}", filename AS {SOURCE_KEY_COLUMN}'


def _clipped_scan(
    support: GeometrySupport,
    bbox: BoundingBox,
    *,
    uris: list[str],
    limit: int,
) -> tuple[str, list[object]]:
    """Clip a geometry lane to the viewport, dropping any row whose clip collapses to a lower dimension."""
    # CLIP BEFORE PROBING. Measured 2026-08-25 on the 2026-08-04 USDM release: the largest polygon
    # falls from 124,676 vertices to 6,151 against the PNW envelope, with no precision loss inside
    # the region actually requested. `ST_Simplify` was evaluated and rejected -- it can move a
    # boundary and flip a cell -- so clipping is the lever, not simplification.
    #
    # `ST_Intersects` is true for BOUNDARY CONTACT, so an edge-touching polygon clips to a LINESTRING
    # and a corner-touching one to a POINT -- served under a schema that promises a Polygon, and
    # undrawable by a fill renderer. `ST_CollectionExtract` at the SOURCE geometry's own dimension
    # keeps only the parts of that dimension and yields an EMPTY geometry when the clip collapsed,
    # which the outer predicate then drops. A straddling polygon, a crossing line and a point on the
    # envelope boundary all survive -- each of those clips at its own dimension. Measured 2026-08-25.
    column = support.geometry_column
    geometry = f'ST_GeomFromWKB("{column}")'
    envelope = "ST_MakeEnvelope(?, ?, ?, ?)"
    clipped = (
        f"ST_CollectionExtract(ST_Intersection({geometry}, {envelope}), CAST(ST_Dimension({geometry}) + 1 AS INTEGER))"
    )
    scan = (
        f'SELECT * EXCLUDE (filename, "{column}"), {clipped} AS {CLIPPED_GEOMETRY_COLUMN}, '
        f"filename AS {SOURCE_KEY_COLUMN} FROM {PARQUET_SOURCE} "
        f"WHERE ST_Intersects({geometry}, {envelope})"
    )
    statement = (
        f"SELECT * EXCLUDE ({CLIPPED_GEOMETRY_COLUMN}), "
        f'ST_AsGeoJSON({CLIPPED_GEOMETRY_COLUMN}) AS "{column}" FROM ({scan}) '
        f"WHERE NOT ST_IsEmpty({CLIPPED_GEOMETRY_COLUMN}) ORDER BY {SOURCE_KEY_COLUMN} LIMIT ?"
    )
    return (statement, [*bbox.as_envelope_arguments, uris, *bbox.as_envelope_arguments, limit])


def _predicate(support: SpatialSupport, bbox: BoundingBox | None) -> tuple[str, list[object]]:
    """Build the viewport predicate, or nothing at all for an unbounded read."""
    if bbox is None:
        return ("", [])
    if isinstance(support, PointSupport):
        return (
            f'WHERE "{support.longitude_column}" BETWEEN ? AND ? AND "{support.latitude_column}" BETWEEN ? AND ?',
            [bbox.west, bbox.east, bbox.south, bbox.north],
        )
    if isinstance(support, NoSpatialSupport):
        raise faults.bbox_unsupported(layer="this lane", reason=support.reason)
    raise ValueError("a geometry lane under a viewport is built by `_clipped_scan`, never by this predicate")


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
