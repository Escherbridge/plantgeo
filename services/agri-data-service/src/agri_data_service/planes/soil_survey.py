"""The DuckDB/Polars serving read for `soil-survey`: point lookups over the current release day.

Layer L3: may import foundation, method, warehouse, pipeline; may NOT import interface.

STATIC layer, `horizon: none` (docs/lanes/soil-survey.md section 7): this lane writes only
`kind=observed` -- there is no `kind=forecast` sibling to read. Its nature is `static_lookup`
(`foundation/parquet/lane_contract.py`): every release is a full re-export of the whole published
SSURGO set, dated at the source's own vintage watermark rather than at a run date, so "latest
release day" and "current state" still mean the same partition -- there are simply far fewer of
them now that the lane no longer re-snapshots on a schedule.

No DuckDB spatial extension is used here -- it is not installable offline in this environment
(`INSTALL spatial` requires network), and this repo carries no `shapely` dependency either. The
point-in-polygon test below is a minimal, dependency-free reader for the plain (non-EWKB) 2D
Polygon/MultiPolygon WKB `ST_AsBinary` produces (`warehouse/schemas/soil_survey.py`'s
`geometry_wkb` column) -- see `AGENTS.md` in this directory for why that is an honest trade, not a
shortcut, given the schema carries no bounding-box column to index or prune on.

`zoom` IS required on every function that resolves a release, and a `SoilSurveyRelease` carries the
tier it resolved at so the read cannot drift off it: `relative_paths` are already tier-pinned keys,
so once a release is resolved, every downstream scan is confined to that rung by construction rather
than by remembering to pass an argument along. This is also the lane where a blended scan would be
worst: a delineation published at two rungs makes the point lookup return the same `mupolygonkey`
twice against `DEFAULT_MAX_POINT_MATCHES=8`, so the cap silently halves the distinct soils reported.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.paths import completed_partition_days, try_parse_partition_path
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA, SOIL_SURVEY_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# This lane writes only this one kind; see the module docstring.
SOIL_SURVEY_KIND: Final[PartitionKind] = "observed"

# Boundary-adjacent delineations can legitimately tie at one point; unbounded growth is not a
# "point lookup" any more, so a caller asking for more must say so explicitly.
DEFAULT_MAX_POINT_MATCHES: Final = 8

_WKB_TYPE_POLYGON: Final = 3
_WKB_TYPE_MULTIPOLYGON: Final = 6
# One byte-order marker + one uint32 geometry-type code, per the WKB spec.
_WKB_HEADER_LENGTH: Final = 5


class SoilSurveyReadError(RuntimeError):
    """Raised when a soil-survey release cannot be read or resolved as requested."""


class SoilSurveyGeometryDecodeError(SoilSurveyReadError):
    """Raised when `geometry_wkb` is not a 2D Polygon/MultiPolygon this decoder supports.

    Raised rather than swallowed: silently treating an undecodable delineation as "does not
    contain the point" would be a wrong-but-plausible answer, not an honest gap.
    """


@dataclass(frozen=True, slots=True)
class SoilSurveyRelease:
    """One release day's part files AT ONE TIER, resolved from an object LISTING -- never a scan.

    `zoom` is not decoration: `relative_paths` are keys under one `zoom=NN/` prefix, so this object
    is the seam that keeps every later scan on one rung without re-passing the tier.
    """

    day: date
    zoom: ZoomTier
    relative_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SoilSurveyPointLookupResult:
    """One point lookup's answer: which release day and tier it actually ran against, and what matched."""

    release_day: date | None
    zoom: ZoomTier
    matches: pl.DataFrame


def resolve_soil_survey_release(store: ObjectStore, day: date, *, requested_zoom: int) -> SoilSurveyRelease | None:
    """Resolve one named release day's part files at one tier, or `None` when nothing was written for it.

    `None` covers a day nobody ever exported, a day the writer marked a governed absence for, a day
    this tier has not been derived to yet, and a day whose export was killed mid-upload (part files
    present but no completion marker) -- all four mean "no data to read at this resolution", and
    this function never falls through to another day or another rung to answer instead
    (`layer-lanes.md` section 2: a future- or gap-date request is an honest empty answer, never a
    silent substitution).
    """
    zoom = serving_zoom_tier(requested_zoom)
    keys = store.list_partition_keys(SOIL_SURVEY_STREAM, SOIL_SURVEY_KIND, zoom, year=day.year, month=day.month)
    completed = completed_partition_days(keys, layer=SOIL_SURVEY_STREAM, kind=SOIL_SURVEY_KIND, zoom=zoom)
    if day not in completed:
        return None
    candidates = (try_parse_partition_path(key) for key in keys)
    parts = sorted(
        (parsed for parsed in candidates if parsed is not None and parsed.day == day),
        key=lambda parsed: parsed.part_index,
    )
    if not parts:
        return None
    return SoilSurveyRelease(day=day, zoom=zoom, relative_paths=tuple(part.key for part in parts))


def resolve_latest_soil_survey_release(store: ObjectStore, *, requested_zoom: int) -> SoilSurveyRelease | None:
    """Find the most recently written release day at one tier from the object listing alone -- never a scan.

    This lane is a `static_lookup`: every release is a full re-export, so the newest release day
    already IS the current published state. `None` means the lane has never been exported AT THIS
    TIER, which for a rung the derivation step has not reached is the honest answer rather than the
    base tier's release wearing a resolution it does not have. Only completed releases are
    candidates: a half-written newest release must not shadow the last good one.
    """
    zoom = serving_zoom_tier(requested_zoom)
    keys = store.list_partition_keys(SOIL_SURVEY_STREAM, SOIL_SURVEY_KIND, zoom)
    completed = completed_partition_days(keys, layer=SOIL_SURVEY_STREAM, kind=SOIL_SURVEY_KIND, zoom=zoom)
    if not completed:
        return None
    parsed = [parsed for parsed in (try_parse_partition_path(key) for key in keys) if parsed is not None]
    completed_entries = [entry for entry in parsed if entry.day in completed]
    if not completed_entries:
        return None
    latest_day = max(entry.day for entry in completed_entries)
    parts = sorted((entry for entry in parsed if entry.day == latest_day), key=lambda entry: entry.part_index)
    return SoilSurveyRelease(day=latest_day, zoom=zoom, relative_paths=tuple(part.key for part in parts))


def s3_path_for(credentials: ObjectStoreCredentials, store: ObjectStore) -> Callable[[str], str]:
    """Return a `path_for` resolver reading one part file from this store's own bucket and prefix."""

    def _resolve(relative_path: str) -> str:
        return f"s3://{credentials.bucket}/{store.key_for(relative_path)}"

    return _resolve


def _scan_release(
    release: SoilSurveyRelease,
    *,
    storage_options: Mapping[str, str],
    path_for: Callable[[str], str],
) -> pl.LazyFrame:
    """Build ONE lazy frame spanning every part file of a release day.

    A release this large spills across up to thousands of `part-N.parquet` files
    (`docs/lanes/soil-survey.md` section 5, point 8: 1,507,623 delineations for the PNW envelope
    alone). Passing the whole path list to one `scan_parquet` call is what makes that many files
    read back as one logical day rather than a table per part the caller must union by hand.
    """
    if not release.relative_paths:
        raise SoilSurveyReadError("a soil-survey release must resolve at least one part file")
    paths = [path_for(relative_path) for relative_path in release.relative_paths]
    options = dict(storage_options) if storage_options else None
    return pl.scan_parquet(paths, storage_options=options)


def read_soil_survey_by_mupolygonkeys(
    release: SoilSurveyRelease,
    *,
    mupolygonkeys: Sequence[str],
    storage_options: Mapping[str, str],
    path_for: Callable[[str], str],
) -> pl.DataFrame:
    """Read one release day narrowed to specific delineations, on the lane's own grain."""
    if not mupolygonkeys:
        raise SoilSurveyReadError("a mupolygonkey lookup needs at least one key; an empty list reads nothing")
    lazy_frame = _scan_release(release, storage_options=storage_options, path_for=path_for)
    return lazy_frame.filter(pl.col("mupolygonkey").is_in(list(mupolygonkeys))).collect()


def _point_in_geometry(payload: bytes, *, longitude: float, latitude: float) -> bool:
    return wkb_polygon_contains_point(payload, longitude=longitude, latitude=latitude)


def find_soil_survey_at_point(  # noqa: PLR0913 - one parameter per required lookup input
    release: SoilSurveyRelease,
    *,
    longitude: float,
    latitude: float,
    storage_options: Mapping[str, str],
    path_for: Callable[[str], str],
    max_matches: int = DEFAULT_MAX_POINT_MATCHES,
) -> pl.DataFrame:
    """Find every delineation whose polygon contains one point, without materialising the release.

    There is no bounding-box column in this schema, so an unindexed point predicate genuinely has
    to look at `geometry_wkb` -- this is the honest cost of a spatial lookup against a grain that
    carries no spatial index, documented rather than hidden behind a false O(1) claim. What this
    function still avoids is `collect()`-ing the whole 1.5-million-row release before filtering in
    Python: the predicate and the row cap are expressed on the LAZY scan, so a streaming engine can
    stop pulling further row groups once `max_matches` rows have satisfied it, rather than
    materialising every row's geometry up front.
    """
    if max_matches < 1:
        raise SoilSurveyReadError(f"max_matches must be at least 1, got {max_matches}")
    lazy_frame = _scan_release(release, storage_options=storage_options, path_for=path_for)
    contains_point = pl.col("geometry_wkb").map_elements(
        lambda payload: _point_in_geometry(payload, longitude=longitude, latitude=latitude),
        return_dtype=pl.Boolean,
    )
    return lazy_frame.filter(contains_point).limit(max_matches).collect()


def soil_survey_at_point(  # noqa: PLR0913 - one parameter per required lookup input
    store: ObjectStore,
    *,
    longitude: float,
    latitude: float,
    requested_zoom: int,
    storage_options: Mapping[str, str],
    path_for: Callable[[str], str],
    day: date | None = None,
    max_matches: int = DEFAULT_MAX_POINT_MATCHES,
) -> SoilSurveyPointLookupResult:
    """Answer "what soil is at this location" at one tier, for the current release or one named release day.

    `day=None` resolves the most recently written release. A `day` with no partition (a future
    date, a day the lane recorded a governed absence for, or a day this tier has not been derived
    to) returns `release_day=None` and an empty, schema-shaped result -- never a silent
    fall-through to whatever the latest release happens to hold instead.
    """
    zoom = serving_zoom_tier(requested_zoom)
    release = (
        resolve_latest_soil_survey_release(store, requested_zoom=zoom)
        if day is None
        else resolve_soil_survey_release(store, day, requested_zoom=zoom)
    )
    if release is None:
        empty = pl.from_arrow(SOIL_SURVEY_SCHEMA.arrow_schema.empty_table())
        return SoilSurveyPointLookupResult(release_day=None, zoom=zoom, matches=empty)  # type: ignore[arg-type]
    matches = find_soil_survey_at_point(
        release,
        longitude=longitude,
        latitude=latitude,
        storage_options=storage_options,
        path_for=path_for,
        max_matches=max_matches,
    )
    return SoilSurveyPointLookupResult(release_day=release.day, zoom=release.zoom, matches=matches)


def _unpack_uint32(data: bytes, offset: int, byte_order: str) -> int:
    return int(struct.unpack_from(byte_order + "I", data, offset)[0])


def _unpack_point(data: bytes, offset: int, byte_order: str) -> tuple[float, float]:
    x, y = struct.unpack_from(byte_order + "dd", data, offset)
    return float(x), float(y)


def _read_ring(data: bytes, offset: int, byte_order: str) -> tuple[list[tuple[float, float]], int]:
    point_count = _unpack_uint32(data, offset, byte_order)
    offset += 4
    points: list[tuple[float, float]] = []
    for _ in range(point_count):
        points.append(_unpack_point(data, offset, byte_order))
        offset += 16
    return points, offset


def _read_polygon_rings(data: bytes, offset: int, byte_order: str) -> tuple[list[list[tuple[float, float]]], int]:
    ring_count = _unpack_uint32(data, offset, byte_order)
    offset += 4
    rings: list[list[tuple[float, float]]] = []
    for _ in range(ring_count):
        ring, offset = _read_ring(data, offset, byte_order)
        rings.append(ring)
    return rings, offset


def _byte_order_marker(data: bytes, offset: int) -> str:
    if offset >= len(data) or data[offset] not in (0, 1):
        raise SoilSurveyGeometryDecodeError(f"invalid WKB byte-order marker at offset {offset}")
    return "<" if data[offset] == 1 else ">"


def _decode_wkb_polygons(payload: bytes) -> list[list[list[tuple[float, float]]]]:
    """Decode a plain (non-EWKB) 2D WKB Polygon or MultiPolygon into one or more ring sets.

    Standard `ST_AsBinary` output carries no SRID header (see `warehouse/schemas/soil_survey.py`'s
    `geometry_wkb` column comment) and this lane's grain is expected to be Polygon or MultiPolygon
    only -- any other geometry type is refused rather than silently treated as a non-match.
    """
    if len(payload) < _WKB_HEADER_LENGTH:
        raise SoilSurveyGeometryDecodeError("geometry_wkb is shorter than a WKB header")
    byte_order = _byte_order_marker(payload, 0)
    geometry_type = _unpack_uint32(payload, 1, byte_order)
    offset = _WKB_HEADER_LENGTH
    if geometry_type == _WKB_TYPE_POLYGON:
        rings, _ = _read_polygon_rings(payload, offset, byte_order)
        return [rings]
    if geometry_type == _WKB_TYPE_MULTIPOLYGON:
        polygon_count = _unpack_uint32(payload, offset, byte_order)
        offset += 4
        polygons: list[list[list[tuple[float, float]]]] = []
        for _ in range(polygon_count):
            member_byte_order = _byte_order_marker(payload, offset)
            member_type = _unpack_uint32(payload, offset + 1, member_byte_order)
            if member_type != _WKB_TYPE_POLYGON:
                raise SoilSurveyGeometryDecodeError(f"multipolygon member has unsupported type {member_type}")
            rings, offset = _read_polygon_rings(payload, offset + _WKB_HEADER_LENGTH, member_byte_order)
            polygons.append(rings)
        return polygons
    raise SoilSurveyGeometryDecodeError(
        f"unsupported WKB geometry type {geometry_type}; this lane's grain is Polygon/MultiPolygon delineations"
    )


def _ring_contains_point(ring: Sequence[tuple[float, float]], x: float, y: float) -> bool:
    """Standard ray-casting point-in-polygon test over one closed ring."""
    inside = False
    count = len(ring)
    previous_index = count - 1
    for index in range(count):
        point_x, point_y = ring[index]
        previous_x, previous_y = ring[previous_index]
        crosses = (point_y > y) != (previous_y > y)
        if crosses and x < (previous_x - point_x) * (y - point_y) / (previous_y - point_y) + point_x:
            inside = not inside
        previous_index = index
    return inside


def wkb_polygon_contains_point(payload: bytes, *, longitude: float, latitude: float) -> bool:
    """Return whether `(longitude, latitude)` falls inside a WKB Polygon/MultiPolygon, holes excluded."""
    for rings in _decode_wkb_polygons(payload):
        if not rings:
            continue
        exterior, *holes = rings
        if not _ring_contains_point(exterior, longitude, latitude):
            continue
        if any(_ring_contains_point(hole, longitude, latitude) for hole in holes):
            continue
        return True
    return False
