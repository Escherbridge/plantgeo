"""Soil-survey serving: the day-partitioned point-lookup lane below, and the admitted-release
native-geometry reader appended at the end of this file (port slice S3; see that section's own
banner and `AGENTS.md` in this directory, "Two soil-survey read paths in one file").

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

import io
import json
import math
import re
import struct
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final, cast

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.observability.logging import get_logger
from agri_data_service.foundation.parquet.paths import completed_partition_days, try_parse_partition_path
from agri_data_service.foundation.parquet.zoom import MAX_REQUEST_ZOOM, MIN_REQUEST_ZOOM, serving_zoom_tier
from agri_data_service.foundation.soil_survey.receipts import (
    WGS84_MAX_LATITUDE,
    WGS84_MAX_LONGITUDE,
    SoilSurveyError,
    digest,
    verify_blob,
)
from agri_data_service.foundation.soil_survey.release import (
    MAX_PART_ROWS,
    MAX_PARTS_PER_VIEWPORT,
    MAX_RELEASE_BYTES,
    MAX_VIEWPORT_BYTES,
    MAX_VIEWPORT_ROWS,
    NATIVE_RUNG,
    Candidate,
    Release,
    manifest_key,
    release_key,
)
from agri_data_service.pipeline.direct.soil_survey.overview import (
    MAX_OVERVIEW_BYTES,
    MAX_OVERVIEW_CELLS,
    OVERVIEW_CELL_DEGREES,
    OVERVIEW_FLOOR_DEGREES_BY_TIER,
    decode_overview,
    drainage_class_id,
    overview_key,
)
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA, SOIL_SURVEY_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date

    from duckdb import DuckDBPyConnection

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.foundation.soil_survey.release import Bounds, Part, ShardRef
    from agri_data_service.pipeline.parquet.availability_storage import AvailabilityStorage
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

logger = get_logger(__name__)

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
    longitude, latitude = struct.unpack_from(byte_order + "dd", data, offset)
    return float(longitude), float(latitude)


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


def _ring_contains_point(ring: Sequence[tuple[float, float]], longitude: float, latitude: float) -> bool:
    """Crossing-number (even-odd ray casting) test over one closed ring; see Franklin's PNPOLY."""
    inside = False
    count = len(ring)
    previous_index = count - 1
    for index in range(count):
        point_longitude, point_latitude = ring[index]
        previous_longitude, previous_latitude = ring[previous_index]
        straddles_ray = (point_latitude > latitude) != (previous_latitude > latitude)
        if straddles_ray:
            crossing_longitude = (previous_longitude - point_longitude) * (latitude - point_latitude) / (
                previous_latitude - point_latitude
            ) + point_longitude
            if longitude < crossing_longitude:
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


# --- Admitted release read path (port slice S3, push P3) --------------------------------------
#
# Everything below reads `foundation.soil_survey.release`'s `Release` / `ShardRef` / `Candidate` /
# `Part` types -- content-addressed objects an operator explicitly stages and admits (owner Q3: a
# settings pin on the release index) -- NOT the day-partitioned `SOIL_SURVEY_STREAM` lane the
# functions above serve. See `AGENTS.md` in this directory, "Two soil-survey read paths in one
# file", for why both live here rather than one superseding the other.
#
# Q1 (owner, 2026-09-27): this port serves ONE geometry rung, z13 ("native"). A request that
# resolves below it (`serving_zoom_tier(requested_zoom) != NATIVE_RUNG`) is refused with
# `soil_survey_zoom_in` before any storage is touched -- there is no coarser rung to fall back to,
# and claiming one would answer a whole-region viewport with bytes nobody budgeted (`release.py`'s
# `MAX_PARTS_PER_VIEWPORT`/`MAX_VIEWPORT_BYTES`/`MAX_VIEWPORT_ROWS`). Vector PMTiles below z13 is
# its own later, separately gated artifact (Go-5).
#
# Q4 (owner, 2026-09-27): "repair else quarantine label and serve always" -- every native row this
# lane ever captured is served, whether its geometry is `valid`, `repaired`, or
# `invalid_unrepaired`. That decision is why SELECTION here reads ONLY the four `bbox_*` columns
# every row already carries (never a GEOS predicate such as `ST_Intersects`/`ST_Covers` against
# `geometry_wkb`, which would silently reject an invalid ring and turn "always serve" into "usually
# serve") and why every served feature carries its own `geometryQuality` label rather than a
# candidate-wide summary.
#
# F10 (plan §3 step 9, §6 "Part fetches run outside run_serving_read"): object-store reads --
# loading the release index, each touched shard's manifest, and each touched part's bytes -- all
# happen in `gather_admitted_soil_survey_viewport` below, a plain function that opens no DuckDB
# connection. Only `run_admitted_soil_survey_query` (registering the assembled Arrow table and
# running the SQL) is meant to run inside `parquet_ops.duckdb_session.run_serving_read`'s bounded
# slot; `interface/http/soil_survey.py` is what wires the two together, so a slot is never held
# while this module is still doing bucket I/O.
#
# `DEFAULT_MAX_POINT_MATCHES` (module top) is shared with the point-lookup lane above: both mean
# "a sane cap on how many delineations legitimately tie at one point".

_VIEWPORT_SQL: Final = load_query_sql("planes/ssurgo_viewport.sql")
_POINT_SQL: Final = load_query_sql("planes/ssurgo_point.sql")

#: Structural ceiling on point-query bbox candidates, tied to the caps that already bound how many
#: rows a gathered table can ever hold (`MAX_PARTS_PER_VIEWPORT` parts x `MAX_PART_ROWS` rows/part) --
#: NOT the caller's own match cap (`DEFAULT_MAX_POINT_MATCHES`). `ssurgo_point.sql` is only the bbox
#: prefilter (review finding 2): `_filter_point_candidates_by_ring` below narrows its candidates to
#: an exact ring test before `DEFAULT_MAX_POINT_MATCHES` truncation ever applies.
_MAX_POINT_CANDIDATE_ROWS: Final = MAX_PARTS_PER_VIEWPORT * MAX_PART_ROWS


@dataclass(frozen=True, slots=True)
class SoilSurveyViewport:
    """One validated SSURGO request: its bbox, the requested map zoom, and an optional exact point.

    Validation here is shape-only (finite, ordered, within WGS84, a legal web-map zoom) and never
    depends on I/O or on the admitted release -- an invalid request is always a 400, regardless of
    whether a release is admitted. Whether `requested_zoom` actually resolves to the one rung this
    port publishes is a SEPARATE, later question (`at_native_rung` below): a low zoom is a well
    formed request that gets a 200 `soil_survey_zoom_in` answer, never a `ValueError`.
    """

    bbox: Bounds
    requested_zoom: int
    point: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        west, south, east, north = self.bbox
        if not all(math.isfinite(value) for value in self.bbox):
            raise ValueError("SSURGO bbox must be finite")
        if not -WGS84_MAX_LONGITUDE <= west <= east <= WGS84_MAX_LONGITUDE or not (
            -WGS84_MAX_LATITUDE <= south <= north <= WGS84_MAX_LATITUDE
        ):
            raise ValueError("SSURGO bbox must be within WGS84")
        if self.point is None and (west == east or south == north):
            raise ValueError("SSURGO viewport must have positive area")
        if self.point is not None and self.bbox != (*self.point, *self.point):
            raise ValueError("point request must use its exact degenerate bounds")
        if not MIN_REQUEST_ZOOM <= self.requested_zoom <= MAX_REQUEST_ZOOM:
            raise ValueError(f"SSURGO requested zoom must be within {MIN_REQUEST_ZOOM}..{MAX_REQUEST_ZOOM}")

    @property
    def at_native_rung(self) -> bool:
        """True once `requested_zoom` resolves to z13, the only rung this port ever publishes."""
        return serving_zoom_tier(self.requested_zoom) == NATIVE_RUNG


def soil_survey_unavailable(reason: str, *, requested_zoom: int) -> dict[str, object]:
    """The `availability: "unavailable"` envelope: no source bound, no release admitted, or a
    request that resolves below the native rung. Every case answers HTTP 200 (`interface/http/
    AGENTS.md`: "a refusal is serving/transport state, never warehouse content") and none of them
    has opened storage to say so.
    """
    return {
        "type": "FeatureCollection",
        "features": [],
        "availability": "unavailable",
        "reason": reason,
        "truncated": False,
        "revision": None,
        "servedZoom": NATIVE_RUNG,
        "requestedZoom": requested_zoom,
        "temporalScope": {"kind": "static_reference", "selectedDaySupported": False},
        "spatialCoverage": None,
    }


def _bbox_overlaps(candidate: Bounds, request_bbox: Bounds) -> bool:
    """True when two WGS84 boxes share any area or edge; the ONLY spatial test this read path runs."""
    west, south, east, north = request_bbox
    return candidate[2] >= west and candidate[3] >= south and candidate[0] <= east and candidate[1] <= north


def _bbox_area(bbox: Bounds) -> float:
    """Plain WGS84 square-degree area; no projection, just `(east-west) * (north-south)`."""
    west, south, east, north = bbox
    return (east - west) * (north - south)


#: R12, "z13 detail ceiling" (plan §7 S3 row; review finding 4): unlike the occurrence plane
#: retired 2026-10-03 (git history at b1745b0f), this lane has NO coarser rung a big request could fall back to
#: (Q1) -- so its ceiling has to stay generous enough for a legitimate single- or multi-shard read
#: (the fixture shard in `tests/planes/test_soil_survey_admitted_reader.py` alone spans ~12 square
#: degrees), not squeezed to that other lane's "exact point" scale. `1600.0` instead matches its
#: COARSE_SUPPORT ceiling -- this platform's own precedent for "the largest sane single-request
#: extent," roughly 11x the whole PNW pilot envelope (`REGION_ENVELOPE`, ~144 square degrees) -- so
#: a legitimate viewport clears it easily while a world- or continent-scale request
#: (`?bbox=-180,-90,180,90`, ~64800 square degrees) is refused BEFORE any shard is even touched
#: (`gather_admitted_soil_survey_viewport` checks this first), rather than walking every admitted
#: shard's manifest just to discover it was always going to be refused by the part/byte cap below.
MAX_SOIL_SURVEY_VIEWPORT_SQUARE_DEGREES: Final = 1600.0


def load_admitted_release(storage: AvailabilityStorage, admitted_sha256: str) -> Release:
    """Load and verify the pinned release index at its content-addressed key.

    Unlike a `Part` or a `ShardRef.manifest`, the settings pin (`config.py::
    ssurgo_admitted_release_sha256`) carries only a SHA-256, never a byte count, so this cannot use
    `verify_blob`'s `Blob` shape -- the check is the same one `verify_blob` runs, spelled out by
    hand: read no more than `MAX_RELEASE_BYTES`, and refuse anything whose digest disagrees.
    """
    if re.fullmatch(r"[0-9a-f]{64}", admitted_sha256) is None:
        raise SoilSurveyError("SSURGO admission must pin an exact release SHA-256")
    stored = storage.read(release_key(admitted_sha256), max_bytes=MAX_RELEASE_BYTES)
    if stored is None or digest(stored.payload) != admitted_sha256:
        raise SoilSurveyError("admitted SSURGO release index is missing or corrupt")
    return Release.model_validate_json(stored.payload)


def render_soil_survey_status(
    *, region_slug: str, release: Release | None = None, admitted_sha256: str | None = None, reason: str | None = None
) -> dict[str, object]:
    """Describe a verified static release index without claiming viewport or temporal coverage."""
    return {
        "availability": "published" if release is not None else "unavailable",
        "reason": reason,
        "regionSlug": region_slug,
        "temporalScope": {"kind": "static_reference", "selectedDaySupported": False},
        "requiredRungs": [NATIVE_RUNG],
        "publication": None
        if release is None
        else {
            "revision": admitted_sha256,
            "releaseDay": release.release_day.isoformat(),
            "capturedAt": release.captured_at.isoformat(),
            "declaredAreaCount": len(release.scope.areas),
            "publishedAreaCount": sum(len(shard.areas) for shard in release.shards),
            "pendingAreaCount": len(release.pending_areas),
        },
    }


def _load_shard_candidate(storage: AvailabilityStorage, shard: ShardRef) -> Candidate:
    """Load and verify one admitted shard's staged manifest against the release's own pin of it."""
    stored = storage.read(manifest_key(shard.manifest.sha256), max_bytes=shard.manifest.byte_count)
    payload = verify_blob(shard.manifest, None if stored is None else stored.payload)
    return Candidate.model_validate_json(payload)


@dataclass(frozen=True, slots=True)
class GatheredSoilSurveyViewport:
    """The bytes a viewport read assembled, before any DuckDB session ever opens.

    `table` is `None` for an honest empty answer -- no admitted shard's declared bbox reaches this
    viewport -- distinct from every other field here, which is populated even then.
    """

    table: pa.Table | None
    touched_shard_count: int
    touched_areas: tuple[str, ...]


def gather_admitted_soil_survey_viewport(
    request: SoilSurveyViewport,
    *,
    storage: AvailabilityStorage,
    release: Release,
) -> GatheredSoilSurveyViewport:
    """Prune shards, then parts, by their bbox columns only; fetch and verify what survives.

    Every object read and every digest check happens here, OUTSIDE any DuckDB serving slot (F10).
    The R12 area ceiling is checked FIRST, before any shard is even touched. The part/byte caps are
    then checked TWICE, deliberately: once against each part's DECLARED `blob.byte_count` before a
    single byte is fetched (cheap, from manifests already in hand), and once against the
    DECOMPRESSED Parquet metadata as each part is actually read (`MAX_VIEWPORT_BYTES` bounds serving
    memory, not wire bytes, and a compressed part can decompress far larger).
    """
    area = _bbox_area(request.bbox)
    if area > MAX_SOIL_SURVEY_VIEWPORT_SQUARE_DEGREES:
        raise SoilSurveyError(
            f"SSURGO viewport spans {area:.2f} square degrees, over the "
            f"{MAX_SOIL_SURVEY_VIEWPORT_SQUARE_DEGREES}-square-degree z13 detail ceiling (R12); zoom in or narrow it"
        )
    touched_shards = [shard for shard in release.shards if _bbox_overlaps(shard.bbox, request.bbox)]
    matched: list[tuple[str, Part]] = [
        (shard.shard, part)
        for shard in touched_shards
        for part in _load_shard_candidate(storage, shard).parts
        if part.rung == NATIVE_RUNG and _bbox_overlaps(part.bbox, request.bbox)
    ]
    declared_bytes = sum(part.blob.byte_count for _, part in matched)
    if len(matched) > MAX_PARTS_PER_VIEWPORT or declared_bytes > MAX_VIEWPORT_BYTES:
        logger.warning(
            "soil_survey_viewport_over_cap",
            parts_touched=len(matched),
            shards_touched=len(touched_shards),
            declared_bytes=declared_bytes,
            cap_parts=MAX_PARTS_PER_VIEWPORT,
            cap_bytes=MAX_VIEWPORT_BYTES,
        )
        raise SoilSurveyError(
            f"SSURGO viewport touches {len(matched)} parts ({declared_bytes} declared bytes), over the "
            f"{MAX_PARTS_PER_VIEWPORT}-part/{MAX_VIEWPORT_BYTES}-byte budget; narrow the viewport"
        )
    tables: list[pa.Table] = []
    uncompressed_bytes = 0
    touched_areas: set[str] = set()
    for _shard_id, part in matched:
        stored = storage.read(part.blob.key, max_bytes=part.blob.byte_count)
        payload = verify_blob(part.blob, None if stored is None else stored.payload)
        parquet_file = pq.ParquetFile(io.BytesIO(payload))
        uncompressed_bytes += sum(
            parquet_file.metadata.row_group(index).total_byte_size
            for index in range(parquet_file.metadata.num_row_groups)
        )
        if uncompressed_bytes > MAX_VIEWPORT_BYTES:
            raise SoilSurveyError("SSURGO viewport exceeds the decompressed Parquet budget; narrow the viewport")
        table = parquet_file.read()
        if table.num_rows != part.row_count or not table.schema.equals(
            SOIL_SURVEY_SCHEMA.arrow_schema, check_metadata=False
        ):
            raise SoilSurveyError("admitted SSURGO part differs from its schema or row-count receipt")
        required_columns = ("geometry_wkb", "bbox_west", "bbox_south", "bbox_east", "bbox_north")
        if any(table[column].null_count for column in required_columns):
            raise SoilSurveyError("admitted SSURGO part lacks geometry or spatial bounds")
        tables.append(table)
        touched_areas.add(part.area)
    if not tables:
        return GatheredSoilSurveyViewport(table=None, touched_shard_count=len(touched_shards), touched_areas=())
    table = pa.concat_tables(tables)
    keys = table["mupolygonkey"].to_pylist()
    if len(set(keys)) != len(keys):
        raise SoilSurveyError("admitted SSURGO parts duplicate native polygon identities")
    return GatheredSoilSurveyViewport(
        table=table, touched_shard_count=len(touched_shards), touched_areas=tuple(sorted(touched_areas))
    )


def _filter_point_candidates_by_ring(
    candidates: list[dict[str, object]], *, longitude: float, latitude: float
) -> list[dict[str, object]]:
    """Narrow bbox candidates to an exact ring test (review finding 2).

    `ssurgo_point.sql` selects by `bbox_*` only, so dense/riparian SSURGO coverage can hand back
    delineations whose box reaches the point but whose true ring does not. This is the exact-match
    narrowing pass: it uses `wkb_polygon_contains_point`, the SAME dependency-free ray-cast the
    day-partitioned lane above already relies on -- never a GEOS predicate, so Q4's "never
    ST_Intersects/ST_Covers against geometry_wkb" rule still holds.

    Every candidate reaching this function already survived `ST_GeomFromWKB`/`ST_AsGeoJSON` in the
    SQL above -- DuckDB's spatial parser, not this module's own minimal one -- so a row whose
    `geometry_wkb` this function's OWN decoder cannot parse (a WKB variant DuckDB accepts and this
    lane's dependency-free reader does not, e.g. a Z/M dimension or an unsupported geometry type) is
    KEPT rather than dropped, matching Q4's "always serve, label don't hide" stance: a decode gap in
    this lane's own reader is not grounds to withhold a delineation DuckDB could already render.
    """
    matches: list[dict[str, object]] = []
    for row in candidates:
        payload = row.get("geometry_wkb")
        if not isinstance(payload, (bytes, bytearray)):
            matches.append(row)
            continue
        try:
            contains = wkb_polygon_contains_point(bytes(payload), longitude=longitude, latitude=latitude)
        except (SoilSurveyGeometryDecodeError, struct.error):
            matches.append(row)
            continue
        if contains:
            matches.append(row)
    return matches


def run_admitted_soil_survey_query(
    connection: DuckDBPyConnection,
    table: pa.Table,
    request: SoilSurveyViewport,
) -> list[dict[str, object]]:
    """Register the gathered table and run the one bbox-column query this request needs.

    The ONLY function in this read path meant to run inside `run_serving_read`'s bounded slot
    (F10): it opens no object-store connection and does no digest work -- one DuckDB registration,
    one parameterised `SELECT`, and (point requests only) the exact ring narrowing above, which is
    pure Python over already-fetched rows.

    A viewport matching over `MAX_VIEWPORT_ROWS` rows is refused outright (`SoilSurveyError`, mapped
    to 503 `soil_survey_read_refused` upstream) rather than silently truncated: truncation would
    serve a spatially arbitrary subset with holes the caller cannot tell from real coverage (review
    finding 3, disposition F2 "503, not truncation").
    """
    connection.register("ssurgo_view", table)
    try:
        west, south, east, north = request.bbox
        if request.point is None:
            result = connection.execute(_VIEWPORT_SQL, [west, south, east, north, MAX_VIEWPORT_ROWS + 1])
            rows = cast("list[dict[str, object]]", result.fetch_arrow_table().to_pylist())
            if len(rows) > MAX_VIEWPORT_ROWS:
                raise SoilSurveyError(
                    f"SSURGO viewport matches over the {MAX_VIEWPORT_ROWS}-row serving budget; narrow the viewport"
                )
            return rows
        longitude, latitude = request.point
        result = connection.execute(_POINT_SQL, [longitude, longitude, latitude, latitude, _MAX_POINT_CANDIDATE_ROWS])
        candidates = cast("list[dict[str, object]]", result.fetch_arrow_table().to_pylist())
        return _filter_point_candidates_by_ring(candidates, longitude=longitude, latitude=latitude)
    finally:
        connection.unregister("ssurgo_view")


def _feature_from_row(row: Mapping[str, object], *, admitted_sha256: str) -> dict[str, object]:
    vintage = row["survey_area_vintage"]
    if not isinstance(vintage, datetime):
        # `SOIL_SURVEY_SCHEMA` declares this column non-nullable timestamp; a non-datetime here
        # means the admitted release itself is malformed, so fail closed rather than fabricate a
        # date (`_READ_FAULTS` in `interface/http/soil_survey.py` maps this to 503).
        raise SoilSurveyError(f"survey_area_vintage must be a datetime, got {type(vintage).__name__}")
    return {
        "type": "Feature",
        "id": row["natural_key"],
        "geometry": json.loads(row["geometry_json"]),  # type: ignore[arg-type]
        "properties": {
            "mupolygonkey": row["mupolygonkey"],
            "mukey": row["mukey"],
            "muname": row["map_unit_name"],
            "soilSeries": row["soil_series"],
            "drainageClass": drainage_class_id(row["drainage_class"]),
            "hydric": row["hydric_rating"],
            "landCapabilityClass": row["land_capability_class"],
            "areaSymbol": row["survey_area_symbol"],
            "surveyAreaVintage": vintage.date().isoformat(),
            "geometryQuality": row["geometry_quality"],
            "geometryRepresentation": "native",
            "source": "usda-sda",
            "releaseSha256": admitted_sha256,
        },
    }


def render_served_soil_survey(
    rows: Sequence[Mapping[str, object]],
    *,
    release: Release,
    request: SoilSurveyViewport,
    touched_areas: tuple[str, ...],
    admitted_sha256: str,
) -> dict[str, object]:
    """Build the `availability: "published"` envelope from the SQL result and the release index."""
    row_limit = MAX_VIEWPORT_ROWS if request.point is None else DEFAULT_MAX_POINT_MATCHES
    features = [_feature_from_row(row, admitted_sha256=admitted_sha256) for row in rows[:row_limit]]
    served_areas = {area.area for shard in release.shards for area in shard.areas}
    logger.info(
        "soil_survey_viewport_served",
        rows=len(features),
        truncated=len(rows) > row_limit,
        areas_touched=len(touched_areas),
    )
    return {
        "type": "FeatureCollection",
        "features": features,
        "availability": "published",
        "reason": None,
        "truncated": len(rows) > row_limit,
        "revision": admitted_sha256,
        "servedZoom": NATIVE_RUNG,
        "requestedZoom": request.requested_zoom,
        "temporalScope": {"kind": "static_reference", "selectedDaySupported": False},
        "releaseDay": release.release_day.isoformat(),
        "capturedAt": release.captured_at.isoformat(),
        "spatialCoverage": {
            "viewportAreas": list(touched_areas),
            "declaredAreaCount": len(served_areas),
            "pendingAreaCount": len(release.pending_areas),
        },
    }


# --- Overview below the native rung -------------------------------------------------------------
#
# A request below z13 answers from the overview `pipeline/direct/soil_survey/overview.py` derived
# from the SAME admitted release, when one has been published at `overview_key(<release sha>)`; with
# none published it is still `soil_survey_zoom_in`. See `AGENTS.md` in this directory, "SSURGO
# overview below z13".


def load_soil_survey_overview(storage: AvailabilityStorage, admitted_sha256: str) -> pl.DataFrame | None:
    """The admitted release's overview, or None when none has been published for it."""
    stored = storage.read(overview_key(admitted_sha256), max_bytes=MAX_OVERVIEW_BYTES)
    return None if stored is None else decode_overview(stored.payload, release_sha256=admitted_sha256)


def _overview_cells_in(overview: pl.DataFrame, bbox: Bounds, degrees: float) -> pl.DataFrame:
    west, south, east, north = bbox
    # East/north are exclusive: a cell that only touches the viewport's edge is not in view.
    return overview.filter(
        (pl.col("cell_degrees") == degrees)
        & pl.col("col").is_between(
            math.floor(west / degrees), max(math.floor(west / degrees), math.ceil(east / degrees) - 1)
        )
        & pl.col("row").is_between(
            math.floor(south / degrees), max(math.floor(south / degrees), math.ceil(north / degrees) - 1)
        )
    )


def select_overview_cells(overview: pl.DataFrame, request: SoilSurveyViewport) -> tuple[float, pl.DataFrame, bool]:
    """The finest rung the tier allows whose viewport fits `MAX_OVERVIEW_CELLS`; else the coarsest, centre-first.

    Returns `(cell degrees, cells, truncated)`.
    """
    floor = OVERVIEW_FLOOR_DEGREES_BY_TIER[serving_zoom_tier(request.requested_zoom)]
    allowed = [degrees for degrees in OVERVIEW_CELL_DEGREES if degrees >= floor]
    for degrees in allowed:
        cells = _overview_cells_in(overview, request.bbox, degrees)
        if cells.height <= MAX_OVERVIEW_CELLS:
            return degrees, cells, False
    west, south, east, north = request.bbox
    degrees = allowed[-1]
    centre_col, centre_row = (west + east) / 2 / degrees - 0.5, (south + north) / 2 / degrees - 0.5
    nearest = cells.sort((pl.col("col") - centre_col) ** 2 + (pl.col("row") - centre_row) ** 2)
    return degrees, nearest.head(MAX_OVERVIEW_CELLS), True


def _overview_feature(cell: Mapping[str, object], degrees: float) -> dict[str, object]:
    col, row = cast("int", cell["col"]), cast("int", cell["row"])
    west, east = round(col * degrees, 6), round((col + 1) * degrees, 6)
    south, north = round(row * degrees, 6), round((row + 1) * degrees, 6)
    hydric = cell["hydric_fraction"]
    return {
        "type": "Feature",
        "id": f"overview:{degrees}:{col}:{row}",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
        },
        "properties": {
            # The legacy aggregate shape the map's hover and the soil panel already caption.
            "aggregated": True,
            "drainageClass": cell["drainage_class"],
            "dominantShare": round(cast("float", cell["dominant_share"]), 4),
            "mappedShare": round(cast("float", cell["mapped_share"]), 4),
            "hydricFraction": None if hydric is None else round(cast("float", hydric), 4),
            "mapUnitCount": cell["map_unit_count"],
            "cellDegrees": degrees,
            "geometryRepresentation": "overview_cell",
            "source": "usda-sda",
        },
    }


def render_soil_survey_overview(
    overview: pl.DataFrame, *, release: Release, request: SoilSurveyViewport, admitted_sha256: str
) -> dict[str, object]:
    """The `published` envelope for a below-z13 request, carrying overview cells instead of map units."""
    degrees, cells, truncated = select_overview_cells(overview, request)
    served_areas = {area.area for shard in release.shards for area in shard.areas}
    logger.info("soil_survey_overview_served", cells=cells.height, cell_degrees=degrees, truncated=truncated)
    return {
        "type": "FeatureCollection",
        "features": [_overview_feature(cell, degrees) for cell in cells.iter_rows(named=True)],
        "availability": "published",
        "reason": None,
        "truncated": truncated,
        "revision": admitted_sha256,
        "servedZoom": serving_zoom_tier(request.requested_zoom),
        "requestedZoom": request.requested_zoom,
        "temporalScope": {"kind": "static_reference", "selectedDaySupported": False},
        "releaseDay": release.release_day.isoformat(),
        "capturedAt": release.captured_at.isoformat(),
        # An overview cell is not attributed to survey areas, so none are claimed as touched.
        "spatialCoverage": {
            "viewportAreas": [],
            "declaredAreaCount": len(served_areas),
            "pendingAreaCount": len(release.pending_areas),
        },
    }
