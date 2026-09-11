"""Build native and generalized soil-survey candidates from preserved Parquet; see AGENTS.md."""

from __future__ import annotations

import hashlib
import io
import json
from typing import TYPE_CHECKING

import polars as pl
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.pipeline.soil_survey_restore.models import (
    MAX_PART_BYTES,
    MAX_SOURCE_BYTES,
    SoilSurveyPreservation,
    SourcePart,
)
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS, derive_tier
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA, SOIL_SURVEY_STREAM

if TYPE_CHECKING:
    from pathlib import Path

    from duckdb import DuckDBPyConnection

MAX_MANIFEST_BYTES = 256 * 1024
MAX_BATCH_ROWS = 500
_NATIVE_GEOMETRY_SQL = load_query_sql("pipeline/soil_survey_restore_geometry.sql")


def _read(root: Path, part: SourcePart) -> bytes:
    path = root / part.path
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("soil-survey source escapes the preserved input directory")
    with path.open("rb") as handle:
        payload = handle.read(part.byte_count + 1)
    if len(payload) != part.byte_count or hashlib.sha256(payload).hexdigest() != part.sha256:
        raise ValueError("soil-survey preserved part identity disagrees")
    return payload


def _native_batches(root: Path, manifest: SoilSurveyPreservation) -> tuple[pl.DataFrame, ...]:
    batches: list[pl.DataFrame] = []
    keys: set[str] = set()
    decoded_total = 0
    for part in manifest.parts:
        reader = pq.ParquetFile(io.BytesIO(_read(root, part)))
        if reader.metadata.num_rows != part.row_count or reader.schema_arrow != SOIL_SURVEY_SCHEMA.arrow_schema:
            raise ValueError("soil-survey preserved row count or exact native schema disagrees")
        decoded_bytes = sum(reader.metadata.row_group(index).total_byte_size for index in range(reader.num_row_groups))
        decoded_total += decoded_bytes
        if decoded_bytes > MAX_PART_BYTES or decoded_total > MAX_SOURCE_BYTES:
            raise ValueError("soil-survey preserved part exceeds its decoded byte budget")
        for batch in reader.iter_batches(batch_size=MAX_BATCH_ROWS):
            frame = pl.from_arrow(batch)
            if not isinstance(frame, pl.DataFrame):
                raise ValueError("soil-survey native Parquet batch is not tabular")
            frame_keys = frame.get_column("mupolygonkey").to_list()
            if len(set(frame_keys)) != frame.height or any(key in keys for key in frame_keys):
                raise ValueError("soil-survey duplicate native delineation; map-unit keys cannot deduplicate it")
            if any(not isinstance(key, str) or not key.strip() for key in frame_keys):
                raise ValueError("soil-survey native delineations require nonempty source keys")
            if frame.filter(pl.col("release_day") != manifest.release_day).height:
                raise ValueError("soil-survey native release day differs from preservation evidence")
            for field in SOIL_SURVEY_SCHEMA.arrow_schema:
                if not field.nullable and frame.get_column(field.name).null_count():
                    raise ValueError(f"soil-survey native field {field.name} contains nulls")
            if frame.filter(pl.col("natural_key") != pl.concat_str(pl.lit("usda-sda:"), pl.col("mupolygonkey"))).height:
                raise ValueError("soil-survey native delineation identity differs from its source key")
            if frame.filter(pl.col("producer") != "usda-sda").height:
                raise ValueError("soil-survey preserved producer differs from USDA SDA")
            keys.update(frame_keys)
            batches.append(frame)
    return tuple(batches)


def _validate_native_geometry(frame: pl.DataFrame, connection: DuckDBPyConnection) -> None:
    connection.register("soil_restore_native", frame.to_arrow())
    try:
        result = connection.execute(_NATIVE_GEOMETRY_SQL).fetchone()
        if result is None or result[0] is not True:
            raise ValueError("soil-survey native geometry must be valid nonempty WGS84 polygons")
    finally:
        connection.unregister("soil_restore_native")


def _put(root: Path, frame: pl.DataFrame, *, rung: int, index: int) -> dict[str, object]:
    buffer = io.BytesIO()
    table = conform_to_stream_schema(frame.to_arrow(), SOIL_SURVEY_SCHEMA)
    pq.write_table(table, buffer, compression="zstd", write_statistics=True)
    payload = buffer.getvalue()
    if len(payload) > MAX_PART_BYTES:
        raise ValueError("soil-survey prepared part exceeds its byte budget")
    relative = f"zoom={rung:02}/part-{index}.parquet"
    path = root / relative
    path.parent.mkdir(exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
    return {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "rows": frame.height,
    }


def prepare_soil_survey(
    *, source_root: Path, expected_manifest_sha256: str, output: Path, connection: DuckDBPyConnection
) -> dict[str, object]:
    """Prepare every rung together; callers supply an already-loaded local spatial session."""
    with (source_root / "manifest.json").open("rb") as handle:
        payload = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES or hashlib.sha256(payload).hexdigest() != expected_manifest_sha256:
        raise ValueError("soil-survey preservation manifest identity disagrees")
    manifest = SoilSurveyPreservation.model_validate_json(payload)
    with (source_root / "preservation-receipt.json").open("rb") as handle:
        preservation = handle.read(MAX_MANIFEST_BYTES + 1)
    if (
        len(preservation) > MAX_MANIFEST_BYTES
        or hashlib.sha256(preservation).hexdigest() != manifest.preservation_receipt_sha256
    ):
        raise ValueError("soil-survey independent preservation receipt identity disagrees")
    batches = _native_batches(source_root, manifest)
    for native in batches:
        _validate_native_geometry(native, connection)
    output.mkdir(parents=True, exist_ok=False)
    receipts: list[dict[str, object]] = []
    for index, batch in enumerate(batches):
        native = batch.sort("mupolygonkey")
        for rung in DERIVED_ZOOM_TIERS:
            derived = derive_tier(native, stream=SOIL_SURVEY_STREAM, tier=rung, connection=connection)
            coarse = pl.from_arrow(conform_to_stream_schema(derived.to_arrow(), SOIL_SURVEY_SCHEMA))
            if not isinstance(coarse, pl.DataFrame):
                raise ValueError("soil-survey conformed derived rung is not tabular")
            if not native.drop("geometry_wkb").equals(coarse.drop("geometry_wkb")):
                raise ValueError("soil-survey generalization changed or lost native delineations/attributes")
            receipts.append(_put(output, coarse, rung=rung, index=index))
        receipts.append(_put(output, native, rung=13, index=index))
    receipt: dict[str, object] = {
        "schema_version": "soil-survey-restoration-candidate/v1",
        "state": "local_prepared_not_published",
        "source_manifest_sha256": expected_manifest_sha256,
        "preservation": manifest.model_dump(mode="json"),
        "required_rungs": [0, 5, 9, 13],
        "native_rows": sum(part.row_count for part in manifest.parts),
        "parts": receipts,
        "geometry": "topology_preserving_simplification_from_native_per_rung_without_area_floor",
        "availability_published": False,
        "upstream_population_complete": False,
    }
    receipt_bytes = json.dumps(receipt, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    with (output / "candidate.json").open("xb") as handle:
        handle.write(receipt_bytes)
    with (output / "candidate.sha256").open("x", encoding="ascii") as handle:
        handle.write(hashlib.sha256(receipt_bytes).hexdigest() + "\n")
    return receipt
