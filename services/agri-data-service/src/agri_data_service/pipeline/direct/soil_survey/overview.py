"""The coarse SSURGO drainage overview served below z13, derived from one admitted release.

Polygon rasterization by the cell-centre rule (even-odd scanline fill), then a majority resample onto
three nested cell grids. See `AGENTS.md` in this directory, "Overview below z13".
"""

from __future__ import annotations

import io
import math
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

import numpy as np
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.soil_survey.receipts import ROOT, SoilSurveyError, digest, verify_blob
from agri_data_service.foundation.soil_survey.release import (
    MAX_RELEASE_BYTES,
    NATIVE_RUNG,
    Candidate,
    Release,
    manifest_key,
    release_key,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.pipeline.parquet.availability_storage import StoredAvailabilityObject

OVERVIEW_FORMAT: Final = "ssurgo-overview/v1"
#: Sample pitch of the rasterization; every overview cell size is a whole multiple of it.
SAMPLE_DEGREES: Final = 0.0025
#: Overview cell sizes, finest first; each divides the next, so coarser rungs are exact sums.
OVERVIEW_CELL_DEGREES: Final[tuple[float, ...]] = (0.025, 0.05, 0.2)
#: The finest overview cell each ladder tier below the native rung may draw.
OVERVIEW_FLOOR_DEGREES_BY_TIER: Final[dict[int, float]] = {9: 0.025, 5: 0.05, 0: 0.2}
#: Cells one viewport answer may carry; mirrored by the web reader's zod cap.
MAX_OVERVIEW_CELLS: Final = 4000
MAX_OVERVIEW_BYTES: Final = 64 * 1024 * 1024

_FINEST_SAMPLES: Final = round(OVERVIEW_CELL_DEGREES[0] / SAMPLE_DEGREES)
_WKB_POLYGON: Final = 3
_WKB_MULTIPOLYGON: Final = 6
_KEY_OFFSET: Final = 1 << 20
_KEY_SPAN: Final = 1 << 21
#: Bounds one scanline chunk's (rows x edges) working matrices for a very large delineation.
_MAX_CHUNK_CELLS: Final = 2_000_000

OVERVIEW_SCHEMA: Final = pa.schema(
    [
        pa.field("cell_degrees", pa.float64(), nullable=False),
        pa.field("col", pa.int32(), nullable=False),
        pa.field("row", pa.int32(), nullable=False),
        pa.field("drainage_class", pa.string(), nullable=True),
        pa.field("dominant_share", pa.float32(), nullable=False),
        pa.field("mapped_share", pa.float32(), nullable=False),
        pa.field("hydric_fraction", pa.float32(), nullable=True),
        pa.field("map_unit_count", pa.int32(), nullable=False),
    ]
)


class ReadableStorage(Protocol):
    """The one read the builder needs; satisfied by the bucket and by `LocalCandidateStorage`."""

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None: ...


def overview_key(release_sha256: str) -> str:
    """Bucket key of the overview derived from one admitted release; its existence is the publish gate."""
    return f"{ROOT}/overviews/{release_sha256}.parquet"


def drainage_class_id(value: object) -> str | None:
    """The served drainage-class id (`"Moderately well drained"` -> `"moderately-well-drained"`)."""
    return value.lower().replace(" ", "-") if isinstance(value, str) else None


def wkb_rings(payload: bytes) -> list[np.ndarray]:
    """Every ring of a plain 2D WKB Polygon/MultiPolygon as an (n, 2) array; `SoilSurveyError` otherwise."""
    rings: list[np.ndarray] = []

    def header(offset: int) -> tuple[str, int]:
        if offset >= len(payload) or payload[offset] not in (0, 1):
            raise SoilSurveyError(f"invalid WKB byte-order marker at offset {offset}")
        order = "<" if payload[offset] == 1 else ">"
        return order, int(struct.unpack_from(order + "I", payload, offset + 1)[0])

    def polygon(offset: int, order: str) -> int:
        (ring_count,) = struct.unpack_from(order + "I", payload, offset)
        offset += 4
        for _ in range(ring_count):
            (point_count,) = struct.unpack_from(order + "I", payload, offset)
            offset += 4
            points = np.frombuffer(payload, dtype=np.dtype(order + "f8"), count=2 * point_count, offset=offset)
            rings.append(points.reshape(point_count, 2))
            offset += 16 * point_count
        return offset

    try:
        order, kind = header(0)
        if kind == _WKB_POLYGON:
            polygon(5, order)
        elif kind == _WKB_MULTIPOLYGON:
            (member_count,) = struct.unpack_from(order + "I", payload, 5)
            offset = 9
            for _ in range(member_count):
                member_order, member_kind = header(offset)
                if member_kind != _WKB_POLYGON:
                    raise SoilSurveyError(f"multipolygon member has unsupported type {member_kind}")
                offset = polygon(offset + 5, member_order)
        else:
            raise SoilSurveyError(f"unsupported WKB geometry type {kind}")
    except (struct.error, ValueError) as error:
        raise SoilSurveyError(f"truncated WKB geometry: {error}") from error
    return rings


def polygon_sample_cells(rings: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Finest-rung cells holding this polygon's sample centres, with a sample count per cell.

    Even-odd scanline fill over every ring at once (holes and disjoint parts need no special case);
    a sample at centre `((i + 0.5) * SAMPLE_DEGREES, (j + 0.5) * SAMPLE_DEGREES)` is inside when it
    lies in `[left crossing, right crossing)` on its row -- the GDAL rasterize cell-centre rule.
    """
    usable = [ring for ring in rings if len(ring) >= 2]  # noqa: PLR2004 - an edge needs two points
    empty = np.empty(0, dtype=np.int64)
    if not usable:
        return empty, empty, empty
    starts = np.concatenate(usable)
    ends = np.concatenate([np.roll(ring, -1, axis=0) for ring in usable])
    x1, y1, x2, y2 = starts[:, 0], starts[:, 1], ends[:, 0], ends[:, 1]
    first_row = math.ceil(float(min(y1.min(), y2.min())) / SAMPLE_DEGREES - 0.5)
    stop_row = math.ceil(float(max(y1.max(), y2.max())) / SAMPLE_DEGREES - 0.5)
    rows_per_chunk = max(1, _MAX_CHUNK_CELLS // len(x1))
    sample_columns: list[np.ndarray] = []
    sample_rows: list[np.ndarray] = []
    for chunk_start in range(first_row, stop_row, rows_per_chunk):
        rows = np.arange(chunk_start, min(chunk_start + rows_per_chunk, stop_row), dtype=np.int64)
        centres = ((rows + 0.5) * SAMPLE_DEGREES)[:, None]
        crossing = (y1 > centres) != (y2 > centres)
        with np.errstate(divide="ignore", invalid="ignore"):
            crossings = np.where(crossing, x1 + (centres - y1) * (x2 - x1) / (y2 - y1), np.inf)
        crossings.sort(axis=1)
        pairs = int(crossing.sum(axis=1).max()) // 2
        if pairs == 0:
            continue
        left = crossings[:, 0 : 2 * pairs : 2]
        right = crossings[:, 1 : 2 * pairs : 2]
        valid = np.isfinite(right)
        first_column = np.ceil(np.where(valid, left, 0.0) / SAMPLE_DEGREES - 0.5).astype(np.int64)
        stop_column = np.ceil(np.where(valid, right, 0.0) / SAMPLE_DEGREES - 0.5).astype(np.int64)
        lengths = np.where(valid, np.maximum(stop_column - first_column, 0), 0).ravel()
        if not lengths.any():
            continue
        offsets = np.repeat(np.cumsum(lengths) - lengths, lengths)
        sample_columns.append(np.repeat(first_column.ravel(), lengths) + np.arange(offsets.size) - offsets)
        sample_rows.append(np.repeat(np.broadcast_to(rows[:, None], left.shape).ravel(), lengths))
    if not sample_columns:
        return empty, empty, empty
    keys = (np.concatenate(sample_columns) // _FINEST_SAMPLES + _KEY_OFFSET) * _KEY_SPAN + (
        np.concatenate(sample_rows) // _FINEST_SAMPLES + _KEY_OFFSET
    )
    unique, counts = np.unique(keys, return_counts=True)
    return unique // _KEY_SPAN - _KEY_OFFSET, unique % _KEY_SPAN - _KEY_OFFSET, counts.astype(np.int64)


class OverviewAccumulator:
    """Collects every delineation's sampled cells; `table()` resamples them onto each overview rung."""

    def __init__(self) -> None:
        self._columns: list[np.ndarray] = []
        self._rows: list[np.ndarray] = []
        self._samples: list[np.ndarray] = []
        self._polygon_ids: list[np.ndarray] = []
        self._classes: list[str | None] = []
        self._hydric: list[bool | None] = []
        self.undecodable = 0

    @property
    def polygons(self) -> int:
        return len(self._classes)

    def add(self, geometry_wkb: bytes, drainage_class: object, hydric: bool | None) -> None:
        """Sample one delineation; an undecodable geometry is counted, never guessed at."""
        try:
            rings = wkb_rings(geometry_wkb)
        except SoilSurveyError:
            self.undecodable += 1
            return
        columns, rows, samples = polygon_sample_cells(rings)
        if columns.size == 0 and rings and len(rings[0]):
            # Smaller than one sample: still a surveyed map unit in its cell, with no area weight.
            centre = rings[0].mean(axis=0)
            columns = np.array([math.floor(centre[0] / OVERVIEW_CELL_DEGREES[0])], dtype=np.int64)
            rows = np.array([math.floor(centre[1] / OVERVIEW_CELL_DEGREES[0])], dtype=np.int64)
            samples = np.zeros(1, dtype=np.int64)
        self._columns.append(columns)
        self._rows.append(rows)
        self._samples.append(samples)
        self._polygon_ids.append(np.full(columns.size, len(self._classes), dtype=np.int64))
        self._classes.append(drainage_class_id(drainage_class))
        self._hydric.append(hydric)

    def table(self) -> pa.Table:
        """Majority resample: per cell, the class holding the most samples, plus shares and counts."""
        if not self._columns:
            return OVERVIEW_SCHEMA.empty_table()
        sampled = pl.DataFrame(
            {
                "col": np.concatenate(self._columns),
                "row": np.concatenate(self._rows),
                "samples": np.concatenate(self._samples),
                "polygon": np.concatenate(self._polygon_ids),
            }
        ).join(
            pl.DataFrame(
                {"polygon": np.arange(len(self._classes)), "drainage_class": self._classes, "hydric": self._hydric},
                schema={"polygon": pl.Int64, "drainage_class": pl.String, "hydric": pl.Boolean},
            ),
            on="polygon",
        )
        rungs = [self._rung(sampled, degrees) for degrees in OVERVIEW_CELL_DEGREES]
        return pl.concat(rungs).sort("cell_degrees", "row", "col").to_arrow().cast(OVERVIEW_SCHEMA)

    @staticmethod
    def _rung(sampled: pl.DataFrame, degrees: float) -> pl.DataFrame:
        factor = round(degrees / OVERVIEW_CELL_DEGREES[0])
        samples_per_side = round(degrees / SAMPLE_DEGREES)
        cells = sampled.with_columns(pl.col("col") // factor, pl.col("row") // factor)
        dominant = (
            cells.group_by("col", "row", "drainage_class")
            .agg(pl.col("samples").sum().alias("class_samples"))
            .sort(
                ["col", "row", "class_samples", "drainage_class"],
                descending=[False, False, True, False],
                nulls_last=True,
            )
            .group_by("col", "row", maintain_order=True)
            .first()
        )
        totals = cells.group_by("col", "row").agg(
            pl.col("samples").sum().alias("total_samples"),
            pl.col("polygon").n_unique().alias("map_unit_count"),
            pl.col("samples").filter(pl.col("hydric")).sum().alias("hydric_samples"),
            pl.col("samples").filter(pl.col("hydric").is_not_null()).sum().alias("rated_samples"),
        )
        return (
            totals.join(dominant, on=["col", "row"])
            .filter(pl.col("total_samples") > 0)
            .select(
                pl.lit(degrees, dtype=pl.Float64).alias("cell_degrees"),
                pl.col("col").cast(pl.Int32),
                pl.col("row").cast(pl.Int32),
                "drainage_class",
                (pl.col("class_samples") / pl.col("total_samples")).cast(pl.Float32).alias("dominant_share"),
                (pl.col("total_samples") / samples_per_side**2)
                .clip(upper_bound=1.0)
                .cast(pl.Float32)
                .alias("mapped_share"),
                pl.when(pl.col("rated_samples") > 0)
                .then(pl.col("hydric_samples") / pl.col("rated_samples"))
                .otherwise(None)
                .cast(pl.Float32)
                .alias("hydric_fraction"),
                pl.col("map_unit_count").cast(pl.Int32),
            )
        )


def encode_overview(table: pa.Table, *, release_sha256: str) -> bytes:
    """Parquet bytes stamped with the release they were derived from; `decode_overview` checks the stamp."""
    stamped = table.replace_schema_metadata(
        {b"format": OVERVIEW_FORMAT.encode(), b"release_sha256": release_sha256.encode()}
    )
    sink = io.BytesIO()
    pq.write_table(stamped, sink, compression="zstd")
    return sink.getvalue()


def decode_overview(payload: bytes, *, release_sha256: str) -> pl.DataFrame:
    """Read an overview, refusing one derived from any release other than the admitted one."""
    table = pq.read_table(io.BytesIO(payload))
    metadata = table.schema.metadata or {}
    if metadata.get(b"format") != OVERVIEW_FORMAT.encode():
        raise SoilSurveyError("SSURGO overview has an unknown format")
    if metadata.get(b"release_sha256") != release_sha256.encode():
        raise SoilSurveyError("SSURGO overview was derived from a different release than the admitted one")
    if not table.schema.equals(OVERVIEW_SCHEMA, check_metadata=False):
        raise SoilSurveyError("SSURGO overview differs from its schema")
    frame = pl.from_arrow(table)
    if not isinstance(frame, pl.DataFrame):
        raise SoilSurveyError("SSURGO overview did not decode to a table")
    return frame


def read_release(stored: bytes | None, release_sha256: str) -> Release:
    """Verify a release index's bytes against its SHA-256 and parse it."""
    if stored is None or len(stored) > MAX_RELEASE_BYTES or digest(stored) != release_sha256:
        raise SoilSurveyError(f"SSURGO release {release_sha256} is missing, oversized or corrupt")
    return Release.model_validate_json(stored)


def read_release_from(storage: ReadableStorage, release_sha256: str) -> Release:
    """Load one release index from the bucket by its content address."""
    stored = storage.read(release_key(release_sha256), max_bytes=MAX_RELEASE_BYTES)
    return read_release(None if stored is None else stored.payload, release_sha256)


@dataclass(frozen=True, slots=True)
class OverviewBuild:
    """One derived overview and the counts an operator checks before publishing it."""

    payload: bytes
    release_sha256: str
    parts: int
    polygons: int
    undecodable: int
    cells_by_degrees: dict[float, int]


def build_overview(release: Release, release_sha256: str, storage: ReadableStorage) -> OverviewBuild:
    """Read every native part of a release (digest-verified) and derive its overview."""
    accumulator = OverviewAccumulator()
    parts = 0
    for shard in release.shards:
        stored = storage.read(manifest_key(shard.manifest.sha256), max_bytes=shard.manifest.byte_count)
        manifest = verify_blob(shard.manifest, None if stored is None else stored.payload)
        candidate = Candidate.model_validate_json(manifest)
        for part in candidate.parts:
            if part.rung != NATIVE_RUNG:
                continue
            part_stored = storage.read(part.blob.key, max_bytes=part.blob.byte_count)
            payload = verify_blob(part.blob, None if part_stored is None else part_stored.payload)
            table = pq.read_table(io.BytesIO(payload), columns=["geometry_wkb", "drainage_class", "hydric_rating"])
            for geometry, drainage, hydric in zip(
                table["geometry_wkb"].to_pylist(),
                table["drainage_class"].to_pylist(),
                table["hydric_rating"].to_pylist(),
                strict=True,
            ):
                accumulator.add(geometry, drainage, hydric)
            parts += 1
    overview = accumulator.table()
    cell_degrees = overview["cell_degrees"].to_pylist()
    return OverviewBuild(
        payload=encode_overview(overview, release_sha256=release_sha256),
        release_sha256=release_sha256,
        parts=parts,
        polygons=accumulator.polygons,
        undecodable=accumulator.undecodable,
        cells_by_degrees={degrees: cell_degrees.count(degrees) for degrees in OVERVIEW_CELL_DEGREES},
    )


__all__ = [
    "MAX_OVERVIEW_BYTES",
    "MAX_OVERVIEW_CELLS",
    "OVERVIEW_CELL_DEGREES",
    "OVERVIEW_FLOOR_DEGREES_BY_TIER",
    "OVERVIEW_FORMAT",
    "OVERVIEW_SCHEMA",
    "SAMPLE_DEGREES",
    "OverviewAccumulator",
    "OverviewBuild",
    "ReadableStorage",
    "build_overview",
    "decode_overview",
    "drainage_class_id",
    "encode_overview",
    "overview_key",
    "polygon_sample_cells",
    "read_release",
    "read_release_from",
    "wkb_rings",
]
