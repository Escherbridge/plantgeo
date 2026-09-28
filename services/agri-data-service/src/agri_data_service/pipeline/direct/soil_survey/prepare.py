"""Replay archived SDA bytes into one shard's Morton-chunked native z13 parts; see `AGENTS.md`.

Ported from `archive/freshness-integrated-candidate-20260914`'s `prepare.py`. `_verify_census`,
`_verified_page`, `_verified_captures` and `_save_candidate_manifest` keep the archive's logic per
the wave-0 equivalence verdict; parts, geometry quality (owner Q4) and replay are new.
"""

from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from pydantic import ValidationError

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.soil_survey.receipts import (
    AreaCapture,
    SoilSurveyError,
    digest,
    encoded,
    require_area,
)
from agri_data_service.foundation.soil_survey.release import (
    MAX_MANIFEST_BYTES,
    MAX_PART_ROWS,
    MAX_PREPARATION_ROWS,
    MAX_PREPARATION_SECONDS,
    MAX_SURVEY_AREAS,
    NATIVE_RUNG,
    AreaQuality,
    Bounds,
    Candidate,
    GeometryQuality,
    Part,
    is_wgs84_extent,
    require_shard,
    union_bounds,
)
from agri_data_service.pipeline.direct.soil_survey.capture import read_blob, save_blob, validate_page
from agri_data_service.pipeline.direct.soil_survey.source import (
    PAGE_COLUMNS,
    page_keys,
    page_query,
    polygon_query,
    summary_query,
    table_rows,
)
from agri_data_service.warehouse.parquet.tiers import derivation_session
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from duckdb import DuckDBPyConnection

    from agri_data_service.foundation.soil_survey.receipts import CapturePage

_NATIVE_GEOMETRY = load_query_sql("pipeline/ssurgo_native_geometry.sql")
_PART_BOUNDS = load_query_sql("pipeline/ssurgo_part_bounds.sql")

#: Default wall-clock budget per shard: the plan's <=1,200 s target under the 1,800 s cap (R5).
DEFAULT_PREPARATION_SECONDS: Final = 1200
#: Morton quantisation: 10^7 integer steps per degree (~1.1 cm), so a centroid never needs float division.
COORDINATE_STEPS_PER_DEGREE: Final = 10_000_000
_LONGITUDE_OFFSET_STEPS: Final = 180 * COORDINATE_STEPS_PER_DEGREE
_LATITUDE_OFFSET_STEPS: Final = 90 * COORDINATE_STEPS_PER_DEGREE
_POLYGONAL: Final = frozenset({"POLYGON", "MULTIPOLYGON"})
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_BBOX_COLUMNS: Final = ("bbox_west", "bbox_south", "bbox_east", "bbox_north")
_PART_SCHEMA_METADATA: Final = {b"ssurgo:rung": str(NATIVE_RUNG).encode()}
_SOURCE_TABLE_SCHEMA: Final = pa.schema([pa.field("mupolygonkey", pa.string()), pa.field("wkt", pa.string())])
# Column positions in `ssurgo_native_geometry.sql`'s result row.
_ORIGINAL_BOUNDS: Final = slice(5, 9)
_REPAIRED_BOUNDS: Final = slice(13, 17)
_REPAIRED_FACTS: Final = slice(9, 13)


@dataclass(frozen=True, slots=True)
class NativeGeometry:
    """One delineation's served WKB, its coordinate bounds and its Q4 quality label."""

    wkb: bytes
    bounds: Bounds
    quality: GeometryQuality


@dataclass(frozen=True, slots=True)
class PreparedRow:
    """One schema-shaped row plus the typed fields spatial chunking needs."""

    native_key: int
    bounds: Bounds
    quality: GeometryQuality
    values: dict[str, object]


def quantised_degrees(value: float) -> int:
    """Integer steps of `1 / COORDINATE_STEPS_PER_DEGREE` degree, by multiplication only."""
    return round(value * COORDINATE_STEPS_PER_DEGREE)


def _spread_bits(value: int) -> int:
    """Interleave a zero bit above each of the low 32 bits (Morton "part1by1")."""
    value &= 0xFFFFFFFF
    value = (value | (value << 16)) & 0x0000FFFF0000FFFF
    value = (value | (value << 8)) & 0x00FF00FF00FF00FF
    value = (value | (value << 4)) & 0x0F0F0F0F0F0F0F0F
    value = (value | (value << 2)) & 0x3333333333333333
    return (value | (value << 1)) & 0x5555555555555555


def morton_key(bounds: Bounds) -> int:
    """Morton (Z-order) key of a bbox's integer-quantised centroid."""
    west, south, east, north = (quantised_degrees(value) for value in bounds)
    column = (west + east) // 2 + _LONGITUDE_OFFSET_STEPS
    row = (south + north) // 2 + _LATITUDE_OFFSET_STEPS
    return _spread_bits(column) | (_spread_bits(row) << 1)


def spatial_order(rows: Sequence[PreparedRow]) -> tuple[PreparedRow, ...]:
    """Rows sorted by Morton key, ties broken on the native key: independent of page order."""
    return tuple(sorted(rows, key=lambda row: (morton_key(row.bounds), row.native_key)))


def spatial_chunks(
    rows: Sequence[PreparedRow], *, part_rows: int = MAX_PART_ROWS
) -> tuple[tuple[PreparedRow, ...], ...]:
    """Consecutive chunks of at most `part_rows` Morton-ordered rows; one chunk becomes one part."""
    if not 1 <= part_rows <= MAX_PART_ROWS:
        raise SoilSurveyError(f"part_rows must be 1..{MAX_PART_ROWS}")
    ordered = spatial_order(rows)
    return tuple(ordered[start : start + part_rows] for start in range(0, len(ordered), part_rows))


def _row_bounds(values: Sequence[object], *, positive: bool, key: str) -> Bounds:
    if any(value is None for value in values):
        raise SoilSurveyError(f"native SSURGO geometry {key} has no coordinate bounds")
    west, south, east, north = (float(str(value)) for value in values)
    bounds = (west, south, east, north)
    if not is_wgs84_extent(bounds, positive=positive):
        raise SoilSurveyError(f"native SSURGO geometry {key} is not within WGS84 extent")
    return bounds


def _wkb(value: object, *, key: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise SoilSurveyError(f"native SSURGO geometry {key} did not render as WKB")
    return bytes(value)


def _classified(result: Sequence[object]) -> tuple[str, NativeGeometry]:
    """Apply owner Q4: keep valid, keep a polygonal repair, else serve the original with a label."""
    key = str(result[0])
    original_empty, original_type, original_valid, original_wkb = result[1:5]
    if original_empty is not False or original_type not in _POLYGONAL:
        raise SoilSurveyError(f"native SSURGO geometry {key} is empty or not a Polygon/MultiPolygon")
    if original_valid is True:
        bounds = _row_bounds(result[_ORIGINAL_BOUNDS], positive=True, key=key)
        return key, NativeGeometry(_wkb(original_wkb, key=key), bounds, "valid")
    repaired_empty, repaired_type, repaired_valid, repaired_wkb = result[_REPAIRED_FACTS]
    if repaired_empty is False and repaired_type in _POLYGONAL and repaired_valid is True:
        bounds = _row_bounds(result[_REPAIRED_BOUNDS], positive=True, key=key)
        return key, NativeGeometry(_wkb(repaired_wkb, key=key), bounds, "repaired")
    bounds = _row_bounds(result[_ORIGINAL_BOUNDS], positive=False, key=key)
    return key, NativeGeometry(_wkb(original_wkb, key=key), bounds, "invalid_unrepaired")


def _native_geometries(
    session: DuckDBPyConnection, source_rows: Sequence[dict[str, str | None]]
) -> dict[str, NativeGeometry]:
    """Parse and classify one page of WKT in one DuckDB statement."""
    table = pa.Table.from_pylist(
        [{"mupolygonkey": row["mupolygonkey"], "wkt": row["geom"]} for row in source_rows],
        schema=_SOURCE_TABLE_SCHEMA,
    )
    session.register("ssurgo_source", table)
    try:
        results = session.execute(_NATIVE_GEOMETRY).fetchall()
    except duckdb.Error as error:
        raise SoilSurveyError("native SSURGO WKT does not parse as geometry") from error
    finally:
        session.unregister("ssurgo_source")
    classified = dict(_classified(result) for result in results)
    if len(classified) != len(source_rows):
        raise SoilSurveyError("native geometry classification changed the page's delineation grain")
    return classified


def _row(
    source: dict[str, str | None], geometry: NativeGeometry, *, release_day: date, captured_at: datetime
) -> PreparedRow:
    key, mukey, wkt, saverest = (source[name] for name in ("mupolygonkey", "mukey", "geom", "saverest"))
    if key is None or not mukey or not wkt or not saverest:
        raise SoilSurveyError("SSURGO native identity, vintage or geometry is missing")
    hydric = source["hydricrating"]
    if hydric not in {None, "", "Yes", "No", "Unranked"}:
        raise SoilSurveyError("unrecognized USDA hydric rating")
    values: dict[str, object] = {
        "natural_key": f"usda-sda:{key}",
        "mupolygonkey": key,
        "mukey": mukey,
        "map_unit_name": source["muname"],
        "soil_series": source["compname"],
        "drainage_class": source["drainagecl"],
        "hydric_rating": {"Yes": True, "No": False}.get(hydric or ""),
        "land_capability_class": source["nirrcapcl"],
        "survey_area_symbol": source["areasymbol"],
        "survey_area_vintage": datetime.combine(date.fromisoformat(saverest[:10]), datetime.min.time(), UTC),
        "geometry_id": None,
        "last_confirmed_at": captured_at,
        "release_day": release_day,
        "geometry_wkb": geometry.wkb,
        "producer": "usda-sda",
        "geometry_quality": geometry.quality,
        **dict(zip(_BBOX_COLUMNS, geometry.bounds, strict=True)),
    }
    return PreparedRow(native_key=int(key), bounds=geometry.bounds, quality=geometry.quality, values=values)


def _verify_part(session: DuckDBPyConnection, table: pa.Table, chunk: Sequence[PreparedRow]) -> Bounds:
    """Prove the part's bytes against `ssurgo_part_bounds.sql`; return its union bounds."""
    session.register("ssurgo_part", table)
    try:
        result = session.execute(_PART_BOUNDS).fetchone()
    except duckdb.Error as error:
        raise SoilSurveyError("prepared SSURGO part does not decode as WKB") from error
    finally:
        session.unregister("ssurgo_part")
    expected_bounds = union_bounds([row.bounds for row in chunk])
    expected = (
        len(chunk),
        len(chunk),
        0,
        sum(row.quality == "repaired" for row in chunk),
        sum(row.quality == "invalid_unrepaired" for row in chunk),
    )
    if result is None or tuple(int(value) for value in result[:5]) != expected:
        raise SoilSurveyError("prepared SSURGO part disagrees with its rows, keys, bounds or quality labels")
    if tuple(float(value) for value in result[5:9]) != expected_bounds:
        raise SoilSurveyError("prepared SSURGO part bounds differ from its rows' union")
    return expected_bounds


def _part(session: DuckDBPyConnection, root: Path, area: str, chunk: Sequence[PreparedRow]) -> Part:
    schema = SOIL_SURVEY_SCHEMA.arrow_schema.with_metadata(_PART_SCHEMA_METADATA)
    table = pa.Table.from_pylist([row.values for row in chunk], schema=schema)
    bounds = _verify_part(session, table, chunk)
    output = io.BytesIO()
    pq.write_table(table, output, compression="zstd")
    return Part(
        blob=save_blob(root, output.getvalue()),
        area=area,
        rung=NATIVE_RUNG,
        row_count=len(chunk),
        repaired_rows=sum(row.quality == "repaired" for row in chunk),
        labelled_rows=sum(row.quality == "invalid_unrepaired" for row in chunk),
        bbox=bounds,
    )


def _verified_captures(root: Path, areas: Sequence[str]) -> tuple[AreaCapture, ...]:
    captures = tuple(
        AreaCapture.model_validate_json((root / f"{require_area(area)}.json").read_bytes()) for area in sorted(areas)
    )
    if any(capture.closing is None for capture in captures):
        raise SoilSurveyError("all declared survey areas must finish before candidate preparation")
    rows = sum(capture.opening.count for capture in captures)
    if rows > MAX_PREPARATION_ROWS:
        raise SoilSurveyError(f"shard holds {rows} rows, over the {MAX_PREPARATION_ROWS}-row preparation cap")
    return captures


def _verify_census(root: Path, capture: AreaCapture) -> None:
    for summary in (capture.opening, capture.closing):
        if summary is None:
            raise SoilSurveyError("missing closing source census")
        if summary.query_sha256 != digest(summary_query(summary.area).encode()):
            raise SoilSurveyError("source census query identity changed")
        census = table_rows(read_blob(root, summary.response), ("delineation_count", "saverest"))
        if census != ({"delineation_count": str(summary.count), "saverest": summary.saverest},):
            raise SoilSurveyError("source census receipt does not match archived bytes")


def _verified_page(root: Path, capture: AreaCapture, page: CapturePage) -> tuple[dict[str, str | None], ...]:
    payload = read_blob(root, page.response)
    query = page_query(capture.opening.area, page.after_key, page.page_size)
    if page.keys_query_sha256 != digest(query.encode()):
        raise SoilSurveyError("source inventory query identity changed")
    keys_payload = read_blob(root, page.keys_response)
    native_keys = page_keys(keys_payload, after=page.after_key, page_size=page.page_size)
    query = polygon_query(capture.opening.area, native_keys)
    if page.query_sha256 != digest(query.encode()):
        raise SoilSurveyError("source page query identity changed")
    rows = table_rows(payload, PAGE_COLUMNS)
    if tuple(row["mupolygonkey"] for row in rows) != native_keys:
        raise SoilSurveyError("source page differs from native-key inventory")
    if validate_page(payload, capture, page.after_key, page.page_size) != (page.last_key, page.row_count):
        raise SoilSurveyError("page receipt differs from archived source")
    return rows


def _area_rows(
    session: DuckDBPyConnection, root: Path, capture: AreaCapture, *, release_day: date, deadline: float
) -> list[PreparedRow]:
    rows: list[PreparedRow] = []
    for page in capture.pages:
        if time.monotonic() >= deadline:
            raise SoilSurveyError("candidate preparation deadline reached; immutable parts may be reused")
        source_rows = _verified_page(root, capture, page)
        geometries = _native_geometries(session, source_rows)
        for source in source_rows:
            geometry = geometries[str(source["mupolygonkey"])]
            rows.append(_row(source, geometry, release_day=release_day, captured_at=page.captured_at))
    if len(rows) != capture.opening.count:
        raise SoilSurveyError(f"{capture.opening.area}: replayed rows differ from the area census")
    return rows


def _area_quality(area: str, rows: Sequence[PreparedRow]) -> AreaQuality:
    return AreaQuality(
        area=area,
        valid_rows=sum(row.quality == "valid" for row in rows),
        repaired_rows=sum(row.quality == "repaired" for row in rows),
        labelled_rows=sum(row.quality == "invalid_unrepaired" for row in rows),
    )


def _require_preparation_scope(shard: str, areas: Sequence[str], time_budget_seconds: float) -> None:
    try:
        require_shard(shard)
    except ValueError as error:
        raise SoilSurveyError(str(error)) from error
    if not 1 <= len(areas) <= MAX_SURVEY_AREAS or len(set(areas)) != len(areas):
        raise SoilSurveyError(f"a shard needs 1..{MAX_SURVEY_AREAS} unique survey areas, not {len(areas)}")
    if not 1 <= time_budget_seconds <= MAX_PREPARATION_SECONDS:
        raise SoilSurveyError(f"preparation needs a time budget of 1..{MAX_PREPARATION_SECONDS} seconds")


def build_candidate(
    root: Path, shard: str, areas: Sequence[str], *, time_budget_seconds: float = DEFAULT_PREPARATION_SECONDS
) -> Candidate:
    """Replay every capture of one shard into verified parts; writes part blobs, never a manifest."""
    _require_preparation_scope(shard, areas, time_budget_seconds)
    deadline = time.monotonic() + time_budget_seconds
    captures = _verified_captures(root, areas)
    release_day = max(capture.opening.vintage for capture in captures)
    parts: list[Part] = []
    quality: list[AreaQuality] = []
    seen: set[int] = set()
    with derivation_session() as session:
        for capture in captures:
            _verify_census(root, capture)
            rows = _area_rows(session, root, capture, release_day=release_day, deadline=deadline)
            keys = {row.native_key for row in rows}
            if len(keys) != len(rows) or not seen.isdisjoint(keys):
                raise SoilSurveyError("duplicate mupolygonkey across candidate pages or areas")
            seen.update(keys)
            for chunk in spatial_chunks(rows):
                if time.monotonic() >= deadline:
                    raise SoilSurveyError("candidate preparation deadline reached; immutable parts may be reused")
                parts.append(_part(session, root, capture.opening.area, chunk))
            quality.append(_area_quality(capture.opening.area, rows))
    try:
        return Candidate(
            shard=shard,
            captured_at=max(capture.closing.checked_at for capture in captures if capture.closing is not None),
            release_day=release_day,
            areas=captures,
            quality=tuple(quality),
            parts=tuple(parts),
        )
    except ValidationError as error:
        raise SoilSurveyError(f"shard {shard} does not form a candidate: {error}") from error


def candidate_manifest_path(root: Path, sha256: str) -> Path:
    """Local path of one prepared shard manifest, named by the SHA-256 of its canonical bytes."""
    if _SHA256_PATTERN.fullmatch(sha256) is None:
        raise SoilSurveyError("a candidate manifest is named by a lowercase 64-hex SHA-256")
    return root / f"candidate-{sha256}.json"


def _save_candidate_manifest(root: Path, candidate: Candidate) -> str:
    payload = encoded(candidate)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise SoilSurveyError(f"candidate manifest is {len(payload)} bytes, over the {MAX_MANIFEST_BYTES}-byte cap")
    sha = digest(payload)
    manifest = candidate_manifest_path(root, sha)
    if manifest.exists():
        if manifest.read_bytes() != payload:
            raise SoilSurveyError("immutable candidate manifest changed")
        return sha
    manifest_blob = save_blob(root, payload)
    try:
        manifest.hardlink_to(root / "objects" / manifest_blob.sha256)
    except FileExistsError:
        if manifest.read_bytes() != payload:
            raise SoilSurveyError("immutable candidate manifest changed") from None
    except OSError as error:
        raise SoilSurveyError("candidate manifests need a filesystem with hard links") from error
    return sha


def prepare_candidate(
    root: Path, shard: str, areas: Sequence[str], *, time_budget_seconds: float = DEFAULT_PREPARATION_SECONDS
) -> tuple[Candidate, str]:
    """Build one shard candidate and save its immutable manifest; returns it with its SHA-256."""
    candidate = build_candidate(root, shard, areas, time_budget_seconds=time_budget_seconds)
    return candidate, _save_candidate_manifest(root, candidate)


def load_candidate_manifest(root: Path, sha256: str) -> tuple[Candidate, bytes]:
    """Read a local manifest whose file bytes must hash to its name and be canonical JSON."""
    payload = candidate_manifest_path(root, sha256).read_bytes()
    if digest(payload) != sha256:
        raise SoilSurveyError("candidate manifest bytes do not hash to the SHA-256 in its name")
    candidate = Candidate.model_validate_json(payload)
    if encoded(candidate) != payload:
        raise SoilSurveyError("candidate manifest is not canonical JSON")
    return candidate, payload


def verify_candidate_replay(
    root: Path, sha256: str, *, time_budget_seconds: float = DEFAULT_PREPARATION_SECONDS
) -> Candidate:
    """Re-prepare a saved shard offline and refuse unless it reproduces the manifest byte for byte."""
    candidate, payload = load_candidate_manifest(root, sha256)
    for part in candidate.parts:
        read_blob(root, part.blob)
    rebuilt = build_candidate(
        root,
        candidate.shard,
        [area.opening.area for area in candidate.areas],
        time_budget_seconds=time_budget_seconds,
    )
    if encoded(rebuilt) != payload:
        raise SoilSurveyError("candidate does not replay from its archived source receipts")
    return candidate


__all__ = [
    "COORDINATE_STEPS_PER_DEGREE",
    "DEFAULT_PREPARATION_SECONDS",
    "NativeGeometry",
    "PreparedRow",
    "build_candidate",
    "candidate_manifest_path",
    "load_candidate_manifest",
    "morton_key",
    "prepare_candidate",
    "quantised_degrees",
    "spatial_chunks",
    "spatial_order",
    "verify_candidate_replay",
]
