"""The DuckDB/Polars serving read for the watersheds lane: HUC12-code and point-in-polygon lookups.

Layer L3: may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import `interface`.
STATIC layer, `horizon: none` (docs/lanes/watersheds.md section 7) -- this module never reads
`kind=forecast`: `_OBSERVED_KIND` is hardcoded everywhere below rather than accepted as a
parameter, so there is no code path that could silently fall through to a stream that does not
exist (`conductor/code_styleguides/layer-lanes.md` section 2).

Geometry dominates the row (~17.3 KB/row, measured on the real 9,396-row/10-part release) and
`ObjectStoreBackend` deliberately has no `get` (`pipeline/parquet/AGENTS.md`), so every read here
goes through Polars/DuckDB's own `s3://` scan (`polars_storage_options`, the seam that module
documents) rather than downloading whole objects through the writer's backend.

`zoom` IS a parameter, required on every public function, and this is the lane where the ladder was
always going to be load-bearing: the PostGIS era generalised HUC12 boundaries at 4/6/8/10/12
(`drizzle/0023_watershed_zoom_generalization.sql:149-155`), which is exactly the per-layer breakpoint
table `foundation/parquet/zoom.py` retired. One rung answers each request. Reading two would make
`lookup_watershed_by_huc12` raise "not unique" -- the same code raises the same message when a
release genuinely duplicates a basin -- and would make `find_containing_watersheds` return one basin
several times with edges that disagree along shared boundaries.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.config import settings as _default_settings
from agri_data_service.foundation.parquet.paths import completed_partition_days, day_prefix, try_parse_partition_path
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, polars_storage_options
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA, WATERSHEDS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.config import ObjectStoreCredentials, Settings
    from agri_data_service.foundation.parquet.zoom import ZoomTier

# The only stream this lane ever writes (`horizon: none`, docs/lanes/watersheds.md section 7).
_OBSERVED_KIND: Final = "observed"

# Standard OGC WKB geometry-type codes. No SRID flag is checked: `ST_AsBinary` (sql/pipeline/
# watersheds_day_export.sql) emits plain WKB, which carries no SRID header at all -- SRID 4326 is
# this lane's own known-out-of-band contract, never encoded in the bytes themselves.
_WKB_POLYGON_TYPE: Final = 3
_WKB_MULTIPOLYGON_TYPE: Final = 6
_WKB_LITTLE_ENDIAN_FLAG: Final = 1
_WKB_HEADER_BYTES: Final = 5  # 1 byte-order byte + 4-byte geometry-type code

_Point = tuple[float, float]
_Ring = tuple[_Point, ...]
_PolygonRings = tuple[_Ring, ...]  # rings[0] is the exterior; rings[1:] are holes


class WatershedGeometryError(ValueError):
    """Raised when a stored WKB payload cannot be decoded as a Polygon or MultiPolygon."""


@dataclass(frozen=True, slots=True)
class WatershedBoundary:
    """One served HUC12 row. `geom` is well-known binary; SRID 4326 is known out of band, since
    WKB carries no SRID header of its own."""

    huc12: str
    name: str | None
    areasqkm: float | None
    tohuc: str | None
    states: str | None
    hutype: str | None
    source: str
    observed_at: datetime | None
    data_available_at: datetime | None
    release_day: date
    feature_id: str
    geom: bytes


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected a string or null, got {type(value).__name__}")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"expected a number or null, got {type(value).__name__}")
    return float(value)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError(f"expected a timestamp or null, got {type(value).__name__}")
    return value


def _require_date(value: object, field: str) -> date:
    if not isinstance(value, date):
        raise ValueError(f"{field} must be a date, got {type(value).__name__}")
    return value


def _require_bytes(value: object, field: str) -> bytes:
    if not isinstance(value, bytes):
        raise ValueError(f"{field} must carry WKB bytes, got {type(value).__name__}")
    return value


def _row_to_boundary(row: Mapping[str, object]) -> WatershedBoundary:
    return WatershedBoundary(
        huc12=_require_str(row["huc12"], "huc12"),
        name=_optional_str(row.get("name")),
        areasqkm=_optional_float(row.get("areasqkm")),
        tohuc=_optional_str(row.get("tohuc")),
        states=_optional_str(row.get("states")),
        hutype=_optional_str(row.get("hutype")),
        source=_require_str(row["source"], "source"),
        observed_at=_optional_datetime(row.get("observed_at")),
        data_available_at=_optional_datetime(row.get("data_available_at")),
        release_day=_require_date(row["release_day"], "release_day"),
        feature_id=_require_str(row["feature_id"], "feature_id"),
        geom=_require_bytes(row["geom"], "geom"),
    )


def watersheds_object_store_root(credentials: ObjectStoreCredentials, *, prefix: str = "") -> str:
    """The `s3://` root Polars/DuckDB scan through, matching `ObjectStore`'s own key layout."""
    trimmed = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
    return f"s3://{credentials.bucket}/{trimmed}".rstrip("/")


def watersheds_release_source(source: Settings | None = None) -> tuple[str, dict[str, str]]:
    """Resolve the `(root, storage_options)` pair a real caller scans the release through."""
    resolved = _default_settings if source is None else source
    credentials = resolved.require_object_store()
    root = watersheds_object_store_root(credentials, prefix=resolved.object_store_prefix)
    return root, polars_storage_options(credentials)


def _watersheds_release_glob(root: str, zoom: ZoomTier, day: date) -> str:
    """Every part file of one release day AT ONE TIER -- a release is many parts and must read as one glob."""
    return f"{root.rstrip('/')}/{day_prefix(WATERSHEDS_STREAM, _OBSERVED_KIND, zoom, day)}part-*.parquet"


def scan_watersheds_release(
    root: str,
    day: date,
    *,
    requested_zoom: int,
    storage_options: Mapping[str, str] | None = None,
) -> pl.LazyFrame:
    """Lazily scan one release day at one tier; nothing is read until a caller filters, selects, and collects.

    `hive_partitioning=False` is deliberate: the frozen layout's `zoom=/year=/month=/day=` segments
    name this module's own understanding of "day" and "tier" (via `list_observed_release_days`,
    `day_prefix`), not a second, Polars-inferred set of columns to reconcile against.
    """
    return pl.scan_parquet(
        _watersheds_release_glob(root, serving_zoom_tier(requested_zoom), day),
        storage_options=dict(storage_options) if storage_options is not None else None,
        hive_partitioning=False,
    )


def list_observed_release_days(store: ObjectStore, *, requested_zoom: int) -> tuple[date, ...]:
    """Every distinct day carrying a COMPLETED observed release AT ONE TIER, sorted ascending.

    A ten-part release collapses to one entry here. A day holding part files without a completion
    marker is a half-written release and is excluded. Both this and
    `foundation.parquet.paths.partition_day_statuses` derive "day" the same way and apply the same
    completion rule, by parsing each key's `day=` segment and intersecting with
    `completed_partition_days`, never by reading row content.
    """
    tier = serving_zoom_tier(requested_zoom)
    keys = store.list_partition_keys(WATERSHEDS_STREAM, _OBSERVED_KIND, tier)
    completed = completed_partition_days(keys, layer=WATERSHEDS_STREAM, kind=_OBSERVED_KIND, zoom=tier)
    days_with_parts = {parsed.day for parsed in (try_parse_partition_path(key) for key in keys) if parsed is not None}
    return tuple(sorted(days_with_parts & completed))


def resolve_latest_observed_release_day(store: ObjectStore, *, requested_zoom: int, as_of: date) -> date | None:
    """The newest observed release at or before `as_of` at one tier; never a release dated after it.

    A HUC12 boundary is a rarely-republished snapshot (docs/lanes/watersheds.md section 7), so a
    request for a date after the newest release is honestly answered by that release -- nothing
    has been observed to have changed since. A request before this lane's first release has no
    honest answer and gets `None`, never the newest release borrowed backwards in time.

    "Newest" is newest AT THE REQUESTED TIER. That reuse-the-newest rule reaches forward in time and
    would happily reach across the ladder too if the listing let it, promising a generalised boundary
    for a rung the derivation step has not produced.
    """
    eligible = tuple(day for day in list_observed_release_days(store, requested_zoom=requested_zoom) if day <= as_of)
    return eligible[-1] if eligible else None


def lookup_watershed_by_huc12(
    root: str,
    day: date,
    huc12: str,
    *,
    requested_zoom: int,
    storage_options: Mapping[str, str] | None = None,
) -> WatershedBoundary | None:
    """One basin by its national key at one tier; the release is sorted by huc12, so this prunes by file range.

    Predicate pushdown on `huc12 == ...` lets Polars skip whole part files whose stored min/max
    huc12 range excludes the code, without decoding their geometry -- the release-wide equivalent
    `find_containing_watersheds` below never gets, because no such range exists for a point test.

    The "not unique" refusal below is why the tier scoping is not cosmetic: one basin published at
    two rungs matches this filter twice, and the message would blame the release for a duplicate the
    reader introduced.
    """
    frame = (
        scan_watersheds_release(root, day, requested_zoom=requested_zoom, storage_options=storage_options)
        .filter(pl.col("huc12") == huc12)
        .collect()
    )
    if frame.height == 0:
        return None
    if frame.height > 1:
        raise ValueError(f"huc12 {huc12!r} is not unique in the {day.isoformat()} release: {frame.height} rows matched")
    return _row_to_boundary(frame.row(0, named=True))


def find_containing_watersheds(  # noqa: PLR0913 - one parameter per point/release input, none foldable
    root: str,
    day: date,
    *,
    longitude: float,
    latitude: float,
    requested_zoom: int,
    storage_options: Mapping[str, str] | None = None,
) -> tuple[WatershedBoundary, ...]:
    """Every basin whose polygon contains one point at one tier -- a full-release geometry scan, honestly.

    No bounding-box column exists in this export to prune spatially before decoding geometry, so
    unlike the huc12 lookup above this cannot skip a single part file. What is bounded is
    materialisation, not I/O: one streaming collect projected to exactly the schema's own columns,
    never a second Python-side copy of the release held alongside the DataFrame.
    """
    frame = (
        scan_watersheds_release(root, day, requested_zoom=requested_zoom, storage_options=storage_options)
        .select(list(WATERSHEDS_SCHEMA.column_names))
        .collect(engine="streaming")
    )
    return tuple(
        _row_to_boundary(row)
        for row in frame.iter_rows(named=True)
        if point_is_within_watershed_geometry(row["geom"], longitude=longitude, latitude=latitude)
    )


def _read_ring(data: bytes, offset: int, endian: str) -> tuple[_Ring, int]:
    (count,) = struct.unpack_from(f"{endian}I", data, offset)
    offset += 4
    points: list[_Point] = []
    for _ in range(count):
        x, y = struct.unpack_from(f"{endian}dd", data, offset)
        points.append((x, y))
        offset += 16
    return tuple(points), offset


def _read_polygon_rings(data: bytes, offset: int, endian: str) -> tuple[_PolygonRings, int]:
    (ring_count,) = struct.unpack_from(f"{endian}I", data, offset)
    offset += 4
    rings: list[_Ring] = []
    for _ in range(ring_count):
        ring, offset = _read_ring(data, offset, endian)
        rings.append(ring)
    return tuple(rings), offset


def decode_polygon_rings(payload: bytes) -> tuple[_PolygonRings, ...]:
    """Decode a Polygon or MultiPolygon WKB (standard OGC, no SRID header) into its ring sets.

    A Polygon decodes to one polygon's rings; a MultiPolygon (WBD carries islands and disjoint
    basin parts) decodes to one entry per member polygon.
    """
    try:
        if len(payload) < _WKB_HEADER_BYTES:
            raise WatershedGeometryError("WKB payload too short to carry a geometry header")
        endian = "<" if payload[0] == _WKB_LITTLE_ENDIAN_FLAG else ">"
        (geometry_type,) = struct.unpack_from(f"{endian}I", payload, 1)
        offset = _WKB_HEADER_BYTES
        if geometry_type == _WKB_POLYGON_TYPE:
            rings, _ = _read_polygon_rings(payload, offset, endian)
            return (rings,)
        if geometry_type == _WKB_MULTIPOLYGON_TYPE:
            (polygon_count,) = struct.unpack_from(f"{endian}I", payload, offset)
            offset += 4
            polygons: list[_PolygonRings] = []
            for _ in range(polygon_count):
                sub_endian = "<" if payload[offset] == _WKB_LITTLE_ENDIAN_FLAG else ">"
                (sub_type,) = struct.unpack_from(f"{sub_endian}I", payload, offset + 1)
                if sub_type != _WKB_POLYGON_TYPE:
                    raise WatershedGeometryError(f"MultiPolygon member carries unexpected geometry type {sub_type}")
                offset += _WKB_HEADER_BYTES
                rings, offset = _read_polygon_rings(payload, offset, sub_endian)
                polygons.append(rings)
            return tuple(polygons)
        raise WatershedGeometryError(
            f"unsupported watersheds geometry type {geometry_type}; expected Polygon or MultiPolygon"
        )
    except (struct.error, IndexError) as exc:
        raise WatershedGeometryError(f"WKB payload is truncated or malformed: {exc}") from exc


def _point_in_ring(x: float, y: float, ring: Sequence[_Point]) -> bool:
    """Even-odd ray-casting test against one closed ring."""
    inside = False
    count = len(ring)
    j = count - 1
    for i in range(count):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_polygon_rings(x: float, y: float, rings: _PolygonRings) -> bool:
    """A point is inside a polygon-with-holes iff inside the exterior and outside every hole."""
    if not rings:
        return False
    if not _point_in_ring(x, y, rings[0]):
        return False
    return all(not _point_in_ring(x, y, hole) for hole in rings[1:])


def point_is_within_watershed_geometry(payload: bytes, *, longitude: float, latitude: float) -> bool:
    """Return whether `(longitude, latitude)` falls inside any polygon part of one WKB geometry."""
    return any(_point_in_polygon_rings(longitude, latitude, rings) for rings in decode_polygon_rings(payload))


__all__ = [
    "WatershedBoundary",
    "WatershedGeometryError",
    "decode_polygon_rings",
    "find_containing_watersheds",
    "list_observed_release_days",
    "lookup_watershed_by_huc12",
    "point_is_within_watershed_geometry",
    "resolve_latest_observed_release_day",
    "scan_watersheds_release",
    "watersheds_object_store_root",
    "watersheds_release_source",
]
