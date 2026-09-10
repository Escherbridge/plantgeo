"""Build local-only signal coordinate candidates; see AGENTS.md signal coordinate preview."""

from __future__ import annotations

import io
import math
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.pipeline.parquet.signal_rewrite import LEGACY_SIGNAL_BASE_SCHEMA
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_SCHEMA
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS, derive_tier

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.foundation.parquet.zoom import ZoomTier

SOURCE_ROOT = "raw-canonical/signal-observation/snapshot=prod-20260826-full-signal-v1"
SOURCE_MANIFEST_SHA256 = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
SOURCE_COMPLETE_SHA256 = "7cb92dff8ba61f07c56be08d80fba16b23f71a6eb932b9a54165304d54b45134"
DIMENSION_SHA256 = "0807a35a8adee038c133fa9e429a70a74220a88c445d6d24488d06996dd84ed1"
DIMENSION_BYTES = 86_817
DIMENSION_ROWS = 1_965
MAX_BASE_ROWS = 100_000
_EWKB_POINT_WITH_SRID = 0x20000001
_EWKB_POINT_BYTES = 25
_WGS84 = 4326


@dataclass(frozen=True, slots=True)
class CoordinateCandidate:
    """Candidate tables and preservation facts, without publication authority."""

    tables: tuple[tuple[ZoomTier, pa.Table], ...]
    original_logical_sha256: str
    preserved_logical_sha256: str
    mapping_sha256: str
    distinct_cell_count: int


def decode_centroid(payload: bytes) -> tuple[float, float]:
    """Accept only finite, two-dimensional EWKB POINTs explicitly in EPSG:4326."""
    if len(payload) != _EWKB_POINT_BYTES or payload[0] not in (0, 1):
        raise ValueError("centroid must be an EWKB Point with explicit SRID")
    endian = "<" if payload[0] else ">"
    geometry_type, srid, longitude, latitude = struct.unpack(f"{endian}IIdd", payload[1:])
    if geometry_type != _EWKB_POINT_WITH_SRID or srid != _WGS84:
        raise ValueError("centroid must be a two-dimensional EPSG:4326 Point")
    if not (math.isfinite(longitude) and math.isfinite(latitude)):
        raise ValueError("centroid coordinates must be finite")
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):  # noqa: PLR2004 - WGS84 coordinate bounds
        raise ValueError("centroid coordinates are outside WGS84 bounds")
    return longitude, latitude


def coordinate_witness(table: pa.Table) -> dict[str, tuple[float, float]]:
    """Validate every dimension cell, including cells the candidate day does not use."""
    required = {"id", "cell_key", "grid_name", "centroid"}
    if not required <= set(table.column_names) or table.num_rows == 0:
        raise ValueError("spatial-cell dimension has no complete coordinate witness")
    cells: dict[str, tuple[float, float]] = {}
    grid_keys: set[tuple[str, str]] = set()
    for row in table.select(sorted(required)).to_pylist():
        identity, key, grid = row["id"], row["cell_key"], row["grid_name"]
        if not all(isinstance(value, str) and value.strip() for value in (identity, key, grid)):
            raise ValueError("spatial-cell identity and grid keys must be nonempty strings")
        if identity in cells or (grid, key) in grid_keys:
            raise ValueError("spatial-cell witness contains duplicate identities or grid keys")
        centroid = row["centroid"]
        if not isinstance(centroid, bytes):
            raise ValueError("spatial-cell centroid is not EWKB bytes")
        cells[identity] = decode_centroid(centroid)
        grid_keys.add((grid, key))
    return cells


def logical_sha256(table: pa.Table) -> str:
    """Hash normalized Arrow IPC while retaining original schema, values, and row order."""
    table = table.combine_chunks().replace_schema_metadata(None)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sha256_digest(sink.getvalue().to_pybytes())


def build_coordinate_candidate(base: pa.Table, dimension: pa.Table, *, day: date) -> CoordinateCandidate:
    """Append witnessed coordinates and derive all ordinary rungs without changing existing values."""
    if not base.schema.equals(LEGACY_SIGNAL_BASE_SCHEMA, check_metadata=False):
        raise ValueError("base is not the exact approved coordinate-less signal schema")
    if not 0 < base.num_rows <= MAX_BASE_ROWS:
        raise ValueError("base row count is outside the bounded preview budget")
    if set(base["observed_day"].to_pylist()) != {day}:
        raise ValueError("base rows do not all belong to the requested day")
    witness = coordinate_witness(dimension)
    ids = base["cell_id"].to_pylist()
    if any(identity is None or identity not in witness for identity in ids):
        raise ValueError("base has a null or unwitnessed cell identity")
    mapping = [(identity, *witness[identity]) for identity in sorted(set(ids))]
    corrected = base.append_column("cell_longitude", pa.array([witness[identity][0] for identity in ids]))
    corrected = corrected.append_column("cell_latitude", pa.array([witness[identity][1] for identity in ids]))
    corrected = conform_to_stream_schema(corrected, SIGNAL_PLANE_SCHEMA)
    preserved = corrected.select(base.column_names).cast(base.schema)
    before, after = logical_sha256(base), logical_sha256(preserved)
    if before != after:
        raise ValueError("coordinate completion changed an existing signal column")
    tables: list[tuple[ZoomTier, pa.Table]] = [(13, corrected)]
    frame = pl.from_arrow(corrected)
    if not isinstance(frame, pl.DataFrame):
        raise ValueError("signal table conversion did not produce a DataFrame")
    order = ["observed_day", "support_key", "signal_name", "normalized_unit", "cell_longitude", "cell_latitude"]
    for tier in DERIVED_ZOOM_TIERS:
        derived = derive_tier(frame, stream="signal", tier=tier).sort(order)
        tables.append((tier, conform_to_stream_schema(derived.to_arrow(), SIGNAL_PLANE_SCHEMA)))
    return CoordinateCandidate(tuple(tables), before, after, sha256_digest(canonical_json(mapping)), len(mapping))


def parquet_bytes(table: pa.Table) -> bytes:
    """Serialize a candidate locally with the normal writer's compression and statistics."""
    output = io.BytesIO()
    pq.write_table(table, output, compression=SIGNAL_PLANE_SCHEMA.compression, write_statistics=True)
    return output.getvalue()
