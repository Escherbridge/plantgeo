"""`verify` (P4): gates G-V1 to G-V5 of DESIGN 2.8, plus the `--cogs` check of the 4326 COGs.

G-V1 (primary, offline): the archived `soil_grid_cache` REST readings vs the CAPTURED native pixel containing
each point. G-V2: lane vs capture at 10,000 sampled cell centres. G-V3: Pearson r of lane vs cache. G-V4: ISRIC
REST at stratified centres; an outage is `deferred`, never `pass`. G-V5: internal texture sums and plausibility.
See `pipeline/direct/soil_properties/AGENTS.md`, "Verify".
"""

from __future__ import annotations

import asyncio
import csv
import io
import itertools
import json
import math
import tarfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpx
import numpy as np
import rasterio  # type: ignore[import-untyped]
from rasterio.warp import transform as transform_coordinates  # type: ignore[import-untyped]

from agri_data_service.config import settings
from agri_data_service.pipeline.direct.soil_properties.capture import CAPTURE_MANIFEST_NAME, read_capture_manifest
from agri_data_service.pipeline.direct.soil_properties.products import (
    CELL_CENTRE_OFFSET_DEGREES,
    ISRIC_DEPTH_LABELS,
    LATTICE_COLUMNS,
    LATTICE_DEGREES,
    LATTICE_NORTH,
    LATTICE_ROWS,
    LATTICE_WEST,
    PROPERTY_UNITS,
    RELEASE_DAY,
    RELEASE_ID,
    SOURCE_RELEASE,
)
from agri_data_service.pipeline.direct.soil_properties.publish import archive_prefix
from agri_data_service.pipeline.direct.soil_properties.source import fail, require_clean_proj_environment
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore
from agri_data_service.warehouse.schemas.soil_properties import (
    SOIL_DEPTH_INTERVALS,
    SOIL_PROPERTIES_STREAM,
    SOIL_PROPERTIES_VALUE_COLUMNS,
    SOIL_PROPERTY_CODES,
    soil_value_column,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Mapping, Sequence

    import numpy.typing as npt
    import pyarrow as pa  # type: ignore[import-untyped]

VERIFICATION_REPORT_NAME: Final = f"verification-{SOURCE_RELEASE}.json"
SAMPLE_SEED: Final = 20_200_602
DEFAULT_SAMPLE_CELLS: Final = 10_000
DEFAULT_REST_POINTS: Final = 30
COG_SAMPLE_PIXELS: Final = 2_000
G_V1_EXACT_SHARE: Final = 0.99
G_V2_EXACT_SHARE: Final = 1.0
G_V3_MIN_PEARSON: Final = 0.95
G_V4_EXACT_SHARE: Final = 0.98
G_V5_TEXTURE_SHARE: Final = 0.99
COG_WITHIN_NEIGHBOURHOOD_SHARE: Final = 0.99
MIN_CORRELATION_PAIRS: Final = 3
TEXTURE_SUM_RANGE: Final = (950, 1050)
#: Physical plausibility ranges (DESIGN 2.8 G-V5); generous physical limits, not regional expectations.
PLAUSIBLE_PHYSICAL: Final[Mapping[str, tuple[float, float]]] = {
    "phh2o": (3.0, 10.5),
    "soc": (0.0, 1000.0),
    "nitrogen": (0.0, 100.0),
    "bdod": (0.2, 2.2),
    "cec": (0.0, 500.0),
    "ocd": (0.0, 1000.0),
    "clay": (0.0, 100.0),
    "sand": (0.0, 100.0),
    "silt": (0.0, 100.0),
    "cfvo": (0.0, 100.0),
}
#: `public.soil_grid_cache` columns (docs/schema.dbml) -> ISRIC code; the cache is 0-5 cm only.
REFERENCE_COLUMNS: Final[Mapping[str, str]] = {
    "ph": "phh2o",
    "organic_carbon": "soc",
    "nitrogen": "nitrogen",
    "bulk_density": "bdod",
    "cec": "cec",
    "ocd": "ocd",
}
_LATITUDE_KEYS: Final = ("lat", "latitude")
_LONGITUDE_KEYS: Final = ("lon", "lng", "longitude")
_REFERENCE_MEMBER_MARKER: Final = "soil_grid_cache"
_COPY_PREFIX: Final = "COPY "
_COPY_END: Final = "\\."
_COPY_NULL: Final = "\\N"
_PG_CUSTOM_DUMP_MAGIC: Final = b"PGDMP"
MAX_REFERENCE_ARCHIVE_BYTES: Final = 512 * 1024 * 1024
REST_URL: Final = "https://rest.isric.org/soilgrids/v2.0/properties/query"
REST_TIMEOUT_SECONDS: Final = 60.0
#: ~5 calls a minute (DESIGN 2.8); ISRIC throttles harder than that.
REST_INTERVAL_SECONDS: Final = 12.0
_HTTP_OK: Final = 200
MISSING: Final = np.int32(np.iinfo(np.int32).min)


class RestUnavailableError(Exception):
    """The ISRIC REST service failed or throttled; G-V4 is `deferred`, never `pass`."""


# --- Captured native rasters ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapturedRaster:
    """One captured native window in memory, sampled by WGS84 coordinates with an exact transform."""

    values: npt.NDArray[np.int32]
    transform: Any
    crs: Any

    @classmethod
    def from_file(cls, path: Path) -> CapturedRaster:
        """Load a captured GeoTIFF; nodata becomes `MISSING`."""
        with rasterio.open(path) as dataset:
            values = dataset.read(1).astype(np.int32)
            if dataset.nodata is not None:
                values[values == np.int32(dataset.nodata)] = MISSING
            return cls(values=values, transform=dataset.transform, crs=dataset.crs)

    def locate(self, longitudes: Sequence[float], latitudes: Sequence[float]) -> tuple[Any, Any, Any]:
        """(rows, columns, inside) of the native pixels containing each point."""
        xs, ys = transform_coordinates("EPSG:4326", self.crs, list(longitudes), list(latitudes))
        inverse = ~self.transform
        columns = np.empty(len(xs), dtype=np.int64)
        rows = np.empty(len(xs), dtype=np.int64)
        for index, (x, y) in enumerate(zip(xs, ys, strict=True)):
            column, row = inverse * (x, y)
            columns[index] = math.floor(column)
            rows[index] = math.floor(row)
        height, width = self.values.shape
        inside = (rows >= 0) & (rows < height) & (columns >= 0) & (columns < width)
        return rows, columns, inside

    def values_at(self, longitudes: Sequence[float], latitudes: Sequence[float]) -> npt.NDArray[np.int32]:
        """The native pixel value containing each point, `MISSING` outside the window or on nodata."""
        rows, columns, inside = self.locate(longitudes, latitudes)
        result = np.full(len(rows), MISSING, dtype=np.int32)
        result[inside] = self.values[rows[inside], columns[inside]]
        return result

    def neighbourhoods(self, longitudes: Sequence[float], latitudes: Sequence[float]) -> list[frozenset[int]]:
        """The valid values of the 3 x 3 native neighbourhood around each point's pixel."""
        if not longitudes:
            return []
        rows, columns, _inside = self.locate(longitudes, latitudes)
        height, width = self.values.shape
        found: list[frozenset[int]] = []
        for row, column in zip(rows, columns, strict=True):
            window = self.values[max(row - 1, 0) : min(row + 2, height), max(column - 1, 0) : min(column + 2, width)]
            found.append(frozenset(int(value) for value in window.ravel() if value != MISSING))
        return found


def load_captured_rasters(capture_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Path]:
    """Lane column -> captured GeoTIFF path, from the verified manifest."""
    depth_by_label = {label: interval for interval, label in ISRIC_DEPTH_LABELS.items()}
    return {
        soil_value_column(entry["property"], depth_by_label[entry["depth"]]): capture_dir / entry["path"]
        for entry in manifest["files"]
    }


# --- The reference archive (soil_grid_cache) ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReferencePoint:
    """One cached 0-5 cm REST reading: a point and ISRIC-code -> value (physical units unless mapped)."""

    longitude: float
    latitude: float
    values: dict[str, float]


def mapped_from_physical(value: float, divisor: int) -> int:
    """Round half up (decimal, not binary) of physical x divisor: the ISRIC mapped integer."""
    return int((Decimal(str(value)) * divisor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _first(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    return next((record[key] for key in keys if record.get(key) not in (None, "", _COPY_NULL)), None)


def reference_points(records: Sequence[Mapping[str, Any]]) -> list[ReferencePoint]:
    """Cache rows with a coordinate, keeping each mapped property value that is present."""
    points: list[ReferencePoint] = []
    for record in records:
        longitude, latitude = _first(record, _LONGITUDE_KEYS), _first(record, _LATITUDE_KEYS)
        if longitude is None or latitude is None:
            continue
        values = {
            code: float(record[column])
            for column, code in REFERENCE_COLUMNS.items()
            if record.get(column) not in (None, "", _COPY_NULL)
        }
        if values:
            points.append(ReferencePoint(float(longitude), float(latitude), values))
    return points


def _copy_block_records(text: str) -> list[dict[str, Any]]:
    """Rows of a pg_dump plain-text `COPY ... soil_grid_cache (cols) FROM stdin;` block."""
    records: list[dict[str, Any]] = []
    lines = iter(text.splitlines())
    for line in lines:
        if not (line.startswith(_COPY_PREFIX) and _REFERENCE_MEMBER_MARKER in line and "(" in line):
            continue
        columns = [name.strip().strip('"') for name in line[line.index("(") + 1 : line.index(")")].split(",")]
        for row in lines:
            if row == _COPY_END:
                return records
            records.append(dict(zip(columns, row.split("\t"), strict=False)))
    return records


def parse_reference_member(name: str, payload: bytes) -> list[dict[str, Any]]:
    """Records from one archive member: CSV/TSV, JSON/JSONL, or a plain-SQL COPY block."""
    if payload.startswith(_PG_CUSTOM_DUMP_MAGIC):
        raise fail(
            f"{name} is a pg_dump custom archive; extract soil_grid_cache to CSV (P0.4) and pass that path",
            stage="verify",
            code="reference_unreadable",
        )
    text = payload.decode("utf-8", errors="replace")
    lowered = name.lower()
    if lowered.endswith((".csv", ".tsv")):
        dialect = "excel-tab" if lowered.endswith(".tsv") else "excel"
        return list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if lowered.endswith(".jsonl"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if lowered.endswith(".json"):
        loaded = json.loads(text)
        return list(loaded) if isinstance(loaded, list) else list(loaded.get("rows", []))
    return _copy_block_records(text)


def read_reference_archive(payload: bytes, name: str) -> list[ReferencePoint]:
    """`soil_grid_cache` rows from a tar(.gz) preserve set, or from a single extracted file."""
    if not tarfile.is_tarfile(io.BytesIO(payload)):
        return reference_points(parse_reference_member(name, payload))
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            body = b"" if handle is None else handle.read()
            if _REFERENCE_MEMBER_MARKER not in member.name and _REFERENCE_MEMBER_MARKER.encode() not in body:
                continue
            points = reference_points(parse_reference_member(member.name, body))
            if points:
                return points
    raise fail(f"{name} holds no readable soil_grid_cache rows", stage="verify", code="reference_unreadable")


def load_reference(source: str) -> list[ReferencePoint]:
    """Read `--reference-archive` from a local path, else as an object-store key (never printed)."""
    path = Path(source)
    if path.is_file():
        return read_reference_archive(path.read_bytes(), path.name)
    payload = BotoObjectStoreBackend.from_credentials(settings.require_object_store()).get(source)
    if payload is None:
        raise fail(f"reference archive {source} does not exist", stage="verify", code="reference_missing")
    if len(payload) > MAX_REFERENCE_ARCHIVE_BYTES:
        raise fail(f"reference archive {source} is larger than expected", stage="verify", code="reference_unreadable")
    return read_reference_archive(payload, source.rsplit("/", 1)[-1])


def expected_mapped(point: ReferencePoint, code: str, units: str) -> int:
    """The cached value as an ISRIC mapped integer."""
    value = point.values[code]
    return round(value) if units == "mapped" else mapped_from_physical(value, PROPERTY_UNITS[code].divisor)


# --- Gates ---------------------------------------------------------------------------------------------


def _share(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def gate_reference_exact(
    reference: Sequence[ReferencePoint], raster_for: Callable[[str], CapturedRaster], *, units: str
) -> dict[str, Any]:
    """G-V1: the captured 0-5 cm native pixel equals the cached REST value; mismatches sit in the 3 x 3."""
    properties: dict[str, Any] = {}
    for code in SOIL_PROPERTY_CODES:
        points = [point for point in reference if code in point.values]
        if not points:
            continue
        raster = raster_for(soil_value_column(code, "0_5"))
        longitudes = [point.longitude for point in points]
        latitudes = [point.latitude for point in points]
        captured = raster.values_at(longitudes, latitudes)
        expected = [expected_mapped(point, code, units) for point in points]
        compared = [(index, want) for index, want in enumerate(expected) if captured[index] != MISSING]
        mismatched = [index for index, want in compared if int(captured[index]) != want]
        neighbourhoods = raster.neighbourhoods(
            [longitudes[index] for index in mismatched], [latitudes[index] for index in mismatched]
        )
        outside = [
            index for index, around in zip(mismatched, neighbourhoods, strict=True) if expected[index] not in around
        ]
        share = _share(len(compared) - len(mismatched), len(compared))
        properties[code] = {
            "compared": len(compared),
            "exact_share": share,
            "mismatches": len(mismatched),
            "mismatches_outside_neighbourhood": len(outside),
            "passed": share is not None and share >= G_V1_EXACT_SHARE and not outside,
        }
    passed = bool(properties) and all(entry["passed"] for entry in properties.values())
    return {"gate": "G-V1", "status": "pass" if passed else "fail", "properties": properties}


def sample_rows(row_count: int, size: int, seed: int = SAMPLE_SEED) -> npt.NDArray[np.int64]:
    """A reproducible random sample of row indices without replacement."""
    generator = np.random.default_rng(seed)
    return np.sort(generator.choice(row_count, size=min(size, row_count), replace=False)).astype(np.int64)


def centres(lane: pa.Table, rows: npt.NDArray[np.int64]) -> tuple[list[float], list[float]]:
    """Cell centres (origin + half a cell) of the named lane rows."""
    longitudes = lane.column("cell_longitude").to_numpy()[rows] + CELL_CENTRE_OFFSET_DEGREES
    latitudes = lane.column("cell_latitude").to_numpy()[rows] + CELL_CENTRE_OFFSET_DEGREES
    return longitudes.tolist(), latitudes.tolist()


def gate_lane_matches_capture(
    lane: pa.Table, raster_for: Callable[[str], CapturedRaster], *, sample_size: int = DEFAULT_SAMPLE_CELLS
) -> dict[str, Any]:
    """G-V2: every sampled lane value equals the captured native pixel at its cell centre (100%)."""
    rows = sample_rows(lane.num_rows, sample_size)
    longitudes, latitudes = centres(lane, rows)
    columns: dict[str, Any] = {}
    for column in SOIL_PROPERTIES_VALUE_COLUMNS:
        captured = raster_for(column).values_at(longitudes, latitudes)
        stored = np.rint(lane.column(column).to_numpy()[rows]).astype(np.int64)
        exact = int(np.sum(captured.astype(np.int64) == stored))
        columns[column] = {"sampled": len(rows), "exact_share": _share(exact, len(rows))}
    shares = [entry["exact_share"] for entry in columns.values()]
    passed = all(share is not None and share >= G_V2_EXACT_SHARE for share in shares)
    return {"gate": "G-V2", "status": "pass" if passed else "fail", "sample_seed": SAMPLE_SEED, "columns": columns}


def lane_grid_index(lane: pa.Table) -> npt.NDArray[np.int64]:
    """A (rows x columns) grid of lane row indices, -1 where the lattice cell holds no row."""
    grid = np.full((LATTICE_ROWS, LATTICE_COLUMNS), -1, dtype=np.int64)
    columns = np.rint((lane.column("cell_longitude").to_numpy() - LATTICE_WEST) / LATTICE_DEGREES).astype(np.int64)
    rows = np.rint((LATTICE_NORTH - lane.column("cell_latitude").to_numpy()) / LATTICE_DEGREES).astype(np.int64) - 1
    grid[rows, columns] = np.arange(lane.num_rows, dtype=np.int64)
    return grid


def containing_row(grid: npt.NDArray[np.int64], longitude: float, latitude: float) -> int | None:
    """The lane row of the lattice cell containing a point, or None."""
    column = math.floor((longitude - LATTICE_WEST) / LATTICE_DEGREES)
    row = math.floor((LATTICE_NORTH - latitude) / LATTICE_DEGREES)
    if not (0 <= row < LATTICE_ROWS and 0 <= column < LATTICE_COLUMNS):
        return None
    index = int(grid[row, column])
    return None if index < 0 else index


def gate_representativeness(lane: pa.Table, reference: Sequence[ReferencePoint], *, units: str) -> dict[str, Any]:
    """G-V3: Pearson r >= 0.95 per property between the lane's containing cell and the cached value."""
    grid = lane_grid_index(lane)
    properties: dict[str, Any] = {}
    for code in SOIL_PROPERTY_CODES:
        values = lane.column(soil_value_column(code, "0_5")).to_numpy()
        pairs = [
            (float(values[row]), float(expected_mapped(point, code, units)))
            for point in reference
            if code in point.values and (row := containing_row(grid, point.longitude, point.latitude)) is not None
        ]
        if not pairs:
            continue
        pearson = float(np.corrcoef(np.array(pairs).T)[0, 1]) if len(pairs) >= MIN_CORRELATION_PAIRS else float("nan")
        finite = math.isfinite(pearson)
        properties[code] = {
            "pairs": len(pairs),
            "pearson_r": round(pearson, 4) if finite else None,
            "passed": finite and pearson >= G_V3_MIN_PEARSON,
        }
    passed = bool(properties) and all(entry["passed"] for entry in properties.values())
    return {"gate": "G-V3", "status": "pass" if passed else "fail", "properties": properties}


def stratified_rows(lane: pa.Table, count: int, seed: int = SAMPLE_SEED) -> list[int]:
    """One random row per equal-width latitude band, `count` bands over the lane's latitude span."""
    if count <= 0 or lane.num_rows == 0:
        return []
    latitudes = lane.column("cell_latitude").to_numpy()
    edges = np.linspace(float(latitudes.min()), float(latitudes.max()) + LATTICE_DEGREES, count + 1)
    generator = np.random.default_rng(seed)
    chosen: list[int] = []
    for lower, upper in itertools.pairwise(edges):
        candidates = np.nonzero((latitudes >= lower) & (latitudes < upper))[0]
        if candidates.size:
            chosen.append(int(generator.choice(candidates)))
    return chosen


def parse_rest_answer(answer: Mapping[str, Any]) -> dict[str, int | None]:
    """ISRIC `properties/query` JSON -> lane column -> mapped mean (None where the service masks it)."""
    depth_by_label = {label: interval for interval, label in ISRIC_DEPTH_LABELS.items()}
    values: dict[str, int | None] = {}
    for layer in (answer.get("properties") or {}).get("layers") or []:
        code = layer.get("name")
        if code not in PROPERTY_UNITS:
            continue
        for depth in layer.get("depths") or []:
            interval = depth_by_label.get(str(depth.get("label", "")).replace(" ", ""))
            if interval is None:
                continue
            mean = (depth.get("values") or {}).get("mean")
            values[soil_value_column(code, interval)] = None if mean is None else int(mean)
    return values


def fetch_rest(client: httpx.Client, longitude: float, latitude: float) -> dict[str, int | None]:
    """All properties and depths at one point in ONE call; any failure is `RestUnavailableError`."""
    # Typed to httpx's own `params` element type (invariant `list[tuple[...]]`, not a subtype of it):
    # a narrower `str | float` annotation here type-checks locally but fails at the `client.get` call.
    parameters: list[tuple[str, str | int | float | bool | None]] = [
        ("lon", longitude),
        ("lat", latitude),
        ("value", "mean"),
    ]
    parameters += [("property", code) for code in SOIL_PROPERTY_CODES]
    parameters += [("depth", label) for label in ISRIC_DEPTH_LABELS.values()]
    try:
        response = client.get(REST_URL, params=parameters, timeout=REST_TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        raise RestUnavailableError(f"{type(error).__name__}") from error
    if response.status_code != _HTTP_OK:
        raise RestUnavailableError(f"HTTP {response.status_code}")
    try:
        return parse_rest_answer(response.json())
    except (ValueError, AttributeError) as error:
        raise RestUnavailableError("malformed answer") from error


def gate_rest(
    lane: pa.Table,
    raster_for: Callable[[str], CapturedRaster],
    *,
    points: int,
    fetch: Callable[[float, float], dict[str, int | None]],
    pause: Callable[[], None],
) -> dict[str, Any]:
    """G-V4: lane vs ISRIC REST at stratified centres; >= 98% exact, mismatches in the 3 x 3.

    An outage, an unexpected answer shape or all-null means is `deferred`, never `fail` (review m1); a
    column REST never answered cannot pass, so it defers the gate unless another column failed. Zero
    points is `not_run`, which the verdict reports as `incomplete`.
    """
    rows = stratified_rows(lane, points) if points > 0 else []
    if not rows:
        return {"gate": "G-V4", "status": "not_run", "reason": "no_rest_points"}
    longitudes, latitudes = centres(lane, np.array(rows, dtype=np.int64))
    answers: list[dict[str, int | None]] = []
    try:
        for index, (longitude, latitude) in enumerate(zip(longitudes, latitudes, strict=True)):
            if index:
                pause()
            answers.append(fetch(longitude, latitude))
    except RestUnavailableError as error:
        return {"gate": "G-V4", "status": "deferred", "reason": str(error), "answered_points": len(answers)}
    problem = rest_answers_problem(answers)
    if problem is not None:
        return {"gate": "G-V4", "status": "deferred", "reason": problem, "answered_points": len(answers)}
    columns: dict[str, Any] = {}
    for column in SOIL_PROPERTIES_VALUE_COLUMNS:
        stored = np.rint(lane.column(column).to_numpy()[rows]).astype(np.int64)
        compared = [(index, answer[column]) for index, answer in enumerate(answers) if answer.get(column) is not None]
        mismatched = [index for index, want in compared if int(stored[index]) != want]
        around = raster_for(column).neighbourhoods(
            [longitudes[index] for index in mismatched], [latitudes[index] for index in mismatched]
        )
        outside = [index for index, near in zip(mismatched, around, strict=True) if answers[index][column] not in near]
        share = _share(len(compared) - len(mismatched), len(compared))
        columns[column] = {
            "compared": len(compared),
            "exact_share": share,
            "mismatches_outside_neighbourhood": len(outside),
            # None: REST answered nothing for this column, so it can neither pass nor fail.
            "passed": None if share is None else share >= G_V4_EXACT_SHARE and not outside,
        }
    if any(entry["passed"] is False for entry in columns.values()):
        status = "fail"
    elif any(entry["passed"] is None for entry in columns.values()):
        status = "deferred"
    else:
        status = "pass"
    result: dict[str, Any] = {"gate": "G-V4", "status": status, "points": len(rows), "columns": columns}
    if status == "deferred":
        result["reason"] = "columns_without_rest_values"
    return result


def rest_answers_problem(answers: Sequence[Mapping[str, int | None]]) -> str | None:
    """Why REST's answers cannot be compared at all, or None: an unexpected shape or all-null means."""
    if any(not answer for answer in answers):
        return "unexpected_answer_shape"
    if all(value is None for answer in answers for value in answer.values()):
        return "all_null_means"
    return None


def gate_internal(lane: pa.Table) -> dict[str, Any]:
    """G-V5: sand + silt + clay within 950-1050 g/kg for >= 99% of rows per depth; physical plausibility."""
    texture: dict[str, Any] = {}
    for interval in SOIL_DEPTH_INTERVALS:
        total = sum(lane.column(soil_value_column(code, interval)).to_numpy() for code in ("sand", "silt", "clay"))
        inside = int(np.sum((total >= TEXTURE_SUM_RANGE[0]) & (total <= TEXTURE_SUM_RANGE[1])))
        share = _share(inside, lane.num_rows)
        texture[interval] = {"share_within": share, "passed": share is not None and share >= G_V5_TEXTURE_SHARE}
    plausibility: dict[str, Any] = {}
    for column in SOIL_PROPERTIES_VALUE_COLUMNS:
        code = column.split("_", 1)[0]
        low, high = PLAUSIBLE_PHYSICAL[code]
        physical = lane.column(column).to_numpy() / PROPERTY_UNITS[code].divisor
        outside = int(np.sum((physical < low) | (physical > high)))
        plausibility[column] = {"outside_range": outside, "passed": outside == 0}
    passed = all(entry["passed"] for entry in (*texture.values(), *plausibility.values()))
    return {"gate": "G-V5", "status": "pass" if passed else "fail", "texture": texture, "plausibility": plausibility}


def verdict(gates: Mapping[str, Mapping[str, Any]]) -> str:
    """P5 may proceed on G-V1, G-V2, G-V3, G-V5 pass plus G-V4 pass-or-deferred.

    A G-V4 that never ran (`--rest-points 0`) is `incomplete`, never `pass` (review m1).
    """
    required = all(gates[name]["status"] == "pass" for name in ("G-V1", "G-V2", "G-V3", "G-V5"))
    rest = gates["G-V4"]["status"]
    if not required or rest == "fail":
        return "fail"
    if rest == "not_run":
        return "incomplete"
    return "pass" if rest in {"pass", "deferred"} else "fail"


# --- The 4326 COGs (WS-B products) ------------------------------------------------------------------


def check_cog(cog: Path, raster: CapturedRaster, *, samples: int = COG_SAMPLE_PIXELS) -> dict[str, Any]:
    """A bilinear COG pixel must lie within the min..max of the captured 3 x 3 neighbourhood at its centre."""
    with rasterio.open(cog) as dataset:
        values = dataset.read(1)
        nodata = dataset.nodata
        valid = values != nodata if nodata is not None else np.ones(values.shape, dtype=bool)
        valid_rows, valid_columns = np.nonzero(valid)
        chosen = sample_rows(valid_rows.size, samples)
        xs, ys = rasterio.transform.xy(dataset.transform, valid_rows[chosen], valid_columns[chosen])
        cog_values = values[valid_rows[chosen], valid_columns[chosen]].astype(np.int64)
    around = raster.neighbourhoods(list(np.atleast_1d(xs)), list(np.atleast_1d(ys)))
    checked = [(int(value), near) for value, near in zip(cog_values, around, strict=True) if near]
    within = sum(1 for value, near in checked if min(near) <= value <= max(near))
    share = _share(within, len(checked))
    passed = share is not None and share >= COG_WITHIN_NEIGHBOURHOOD_SHARE
    return {"cog": cog.name, "checked": len(checked), "within_share": share, "passed": passed}


def check_cogs(cog_dir: Path, raster_paths: Mapping[str, Path]) -> dict[str, Any]:
    """Every `<p>_<depth>_mean_4326.tif` in the directory against its capture."""
    results = []
    for column, capture in sorted(raster_paths.items()):
        code, interval = column.removesuffix("cm").split("_", 1)
        cog = cog_dir / f"{code}_{ISRIC_DEPTH_LABELS[interval]}_mean_4326.tif"
        if cog.is_file():
            results.append(check_cog(cog, CapturedRaster.from_file(capture)))
    passed = bool(results) and all(entry["passed"] for entry in results)
    return {"check": "cogs", "status": "pass" if passed else "fail", "results": results}


# --- The verb ------------------------------------------------------------------------------------------


class _RasterCache:
    """Open each captured raster once, on first use; the thirty never all sit in memory at once."""

    def __init__(self, paths: Mapping[str, Path], *, keep: int = 2) -> None:
        self._paths = paths
        self._keep = keep
        self._open: dict[str, CapturedRaster] = {}

    def __call__(self, column: str) -> CapturedRaster:
        if column not in self._open:
            if len(self._open) >= self._keep:
                self._open.pop(next(iter(self._open)))
            self._open[column] = CapturedRaster.from_file(self._paths[column])
        return self._open[column]


def verify_cogs(options: argparse.Namespace, capture_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """P2's `verify --cogs`: the 4326 COGs against the capture only; no lane, no reference, local report."""
    result = check_cogs(options.cogs, load_captured_rasters(capture_dir, manifest))
    report = {
        "verb": "verify",
        "mode": "cogs",
        "release_id": RELEASE_ID,
        "manifest_sha256": manifest["manifest_sha256"],
        "verified_at": datetime.now(UTC).isoformat(),
        "cogs": result,
        "status": result["status"],
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    (capture_dir / f"verification-cogs-{stamp}.json").write_text(rendered, encoding="utf-8")
    return report


def verify_release(options: argparse.Namespace) -> dict[str, Any]:
    """Run every gate against the published z13 and the capture; write and archive the report."""
    require_clean_proj_environment("verify")
    if options.capture_dir is None:
        raise fail("--capture-dir is required for verify", stage="verify", code="invalid_arguments")
    capture_dir: Path = options.capture_dir
    manifest = read_capture_manifest(capture_dir, stage="verify")
    if options.cogs is not None:
        return verify_cogs(options, capture_dir, manifest)
    if options.reference_archive is None:
        raise fail(
            "--reference-archive is required: G-V1 is the primary gate", stage="verify", code="invalid_arguments"
        )
    rasters = _RasterCache(load_captured_rasters(capture_dir, manifest))
    report: dict[str, Any] = {
        "verb": "verify",
        "mode": "lane",
        "release_id": RELEASE_ID,
        "manifest_sha256": manifest["manifest_sha256"],
        "verified_at": datetime.now(UTC).isoformat(),
    }
    reference = load_reference(options.reference_archive)
    lane = ObjectStore.from_settings().read_partition(SOIL_PROPERTIES_STREAM, "observed", 13, RELEASE_DAY)
    with httpx.Client() as client:
        gates = {
            "G-V1": gate_reference_exact(reference, rasters, units=options.reference_units),
            "G-V2": gate_lane_matches_capture(lane, rasters, sample_size=options.sample_cells),
            "G-V3": gate_representativeness(lane, reference, units=options.reference_units),
            "G-V4": gate_rest(
                lane,
                rasters,
                points=options.rest_points,
                fetch=lambda longitude, latitude: fetch_rest(client, longitude, latitude),
                pause=lambda: time.sleep(REST_INTERVAL_SECONDS),
            ),
            "G-V5": gate_internal(lane),
        }
    report.update(reference_points=len(reference), lane_rows=lane.num_rows, gates=gates, status=verdict(gates))
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    (capture_dir / VERIFICATION_REPORT_NAME).write_text(rendered, encoding="utf-8")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    key = f"{archive_prefix(str(manifest['manifest_sha256']))}/verification-{SOURCE_RELEASE}-{stamp}.json"
    storage = BotoAvailabilityStorage.from_settings()
    storage.put_immutable(key, rendered.encode("utf-8"), content_type="application/json")
    report["archived_report_key"] = key
    report["capture_manifest"] = str(capture_dir / CAPTURE_MANIFEST_NAME)
    return report


async def run_verify(options: argparse.Namespace) -> dict[str, Any]:
    """The `verify` verb: GDAL, network and object-store work runs off the event loop."""
    return await asyncio.to_thread(verify_release, options)


__all__ = [
    "VERIFICATION_REPORT_NAME",
    "CapturedRaster",
    "ReferencePoint",
    "RestUnavailableError",
    "check_cog",
    "check_cogs",
    "gate_internal",
    "gate_lane_matches_capture",
    "gate_reference_exact",
    "gate_representativeness",
    "gate_rest",
    "lane_grid_index",
    "mapped_from_physical",
    "parse_rest_answer",
    "read_reference_archive",
    "reference_points",
    "rest_answers_problem",
    "run_verify",
    "stratified_rows",
    "verdict",
    "verify_cogs",
    "verify_release",
]
