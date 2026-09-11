"""Prepare one complete FIRMS source day without a writer or publisher."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import date
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.firms import (
    collapse_history_records,
    parse_firms_csv,
    parse_product_availability,
    processing_tier,
    products_covering,
)
from agri_data_service.ingest.identity import build_firms_identity
from agri_data_service.pipeline.direct.fire_detections import fire_table_from_features
from agri_data_service.pipeline.fire_detections_recovery.models import (
    MAX_MANIFEST_BYTES,
    MAX_RECORDS,
    CapturedResponse,
    FireSourceCapture,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, derive_tier
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_SCHEMA, FIRE_DETECTIONS_STREAM

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

MAX_CELL_CHARACTERS: Final = 4096
CSV_REQUIRED: Final = {"latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence"}
DECIMAL_TEXT: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
CONFIDENCE_WORDS: Final = {"l", "n", "h", "low", "nominal", "high"}
MAX_CONFIDENCE_PERCENT: Final = 100


def _number(value: str) -> float:
    """Require the same whole decimal text the retained JavaScript-style parser reads."""
    if DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError("FIRMS numeric cell is not a whole ASCII decimal")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("FIRMS numeric cell is non-finite")
    return result


def _read(path: Path, *, byte_length: int, sha256: str) -> bytes:
    """Read exact bounded complete bytes and verify the independently supplied digest."""
    if path.stat().st_size != byte_length:
        raise ValueError("FIRMS captured file length differs from its receipt")
    with path.open("rb") as handle:
        body = handle.read(byte_length + 1)
    if len(body) != byte_length or hashlib.sha256(body).hexdigest() != sha256:
        raise ValueError("FIRMS captured file failed complete SHA-256 verification")
    return body


def _body(root: Path, response: CapturedResponse) -> bytes:
    """Resolve one content-addressed captured CSV beneath its supplied root."""
    path = (root / "responses" / f"{response.sha256}.csv").resolve(strict=True)
    if not path.is_relative_to(root.resolve(strict=True)):
        raise ValueError("FIRMS response path escapes its source root")
    return _read(path, byte_length=response.byte_length, sha256=response.sha256)


def _csv_rows(body: bytes, *, required: set[str]) -> Iterator[dict[str, str]]:
    """Validate the whole CSV instead of silently discarding malformed rows."""
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig")), strict=True)
    header = [value.strip().lower() for value in next(reader, [])]
    if len(set(header)) != len(header) or not required.issubset(header):
        raise ValueError("FIRMS CSV has missing or duplicate headers")
    if any(len(value) > MAX_CELL_CHARACTERS or any(char in value for char in "\n\r,") for value in header):
        raise ValueError("FIRMS CSV has an unsupported header cell")
    for index, row in enumerate(reader):
        if index >= MAX_RECORDS or len(row) != len(header):
            raise ValueError("FIRMS CSV is malformed or exceeds the record cap")
        if any(len(value) > MAX_CELL_CHARACTERS or any(char in value for char in "\n\r,") for value in row):
            raise ValueError("FIRMS CSV has an unsupported multiline, comma or oversized cell")
        yield dict(zip(header, (value.strip() for value in row), strict=True))


def _availability(body: bytes) -> str:
    """Refuse malformed and duplicate availability claims before product selection."""
    seen: set[str] = set()
    rows = list(_csv_rows(body, required={"data_id", "min_date", "max_date"}))
    for row in rows:
        product = row["data_id"]
        first, last = date.fromisoformat(row["min_date"]), date.fromisoformat(row["max_date"])
        if not product or product in seen or first > last:
            raise ValueError("FIRMS availability contains duplicate, empty or inverted product windows")
        seen.add(product)
    if not seen:
        raise ValueError("FIRMS availability cannot establish a product population")
    return _parser_text(rows)


def _parser_text(rows: list[dict[str, str]]) -> str:
    """Pass validated decoded cells to the legacy comma parser without quote loss."""
    if not rows:
        return ""
    return "\n".join([",".join(rows[0]), *(",".join(row.values()) for row in rows)])


def _features(body: bytes, *, product: str, capture: FireSourceCapture) -> list[dict[str, object]]:
    """Validate source numbers and acquisition support before the existing pure parser."""
    rows = list(_csv_rows(body, required=CSV_REQUIRED))
    west, south, east, north = capture.bbox
    for row in rows:
        lon, lat = _number(row["longitude"]), _number(row["latitude"])
        if not math.isfinite(lon) or not math.isfinite(lat) or not west <= lon <= east or not south <= lat <= north:
            raise ValueError("FIRMS detection lies outside the captured bbox")
        acquisition = row["acq_date"]
        clock = row["acq_time"].zfill(4)
        if acquisition != capture.day.isoformat() or re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d", clock) is None:
            raise ValueError("FIRMS detection has an invalid or off-day acquisition time")
        if not row["satellite"] or not row["confidence"]:
            raise ValueError("FIRMS detection lacks source satellite or confidence")
        if (
            row["confidence"].lower() not in CONFIDENCE_WORDS
            and not 0 <= _number(row["confidence"]) <= MAX_CONFIDENCE_PERCENT
        ):
            raise ValueError("FIRMS detection confidence is outside its source scale")
        for field in ("frp", "brightness", "bright_ti4"):
            if row.get(field):
                _number(row[field])
    parsed = parse_firms_csv(_parser_text(rows), product)
    if len(parsed) != len(rows):
        raise ValueError("FIRMS parser rejected part of the captured source population")
    return parsed


def _refuse_conflicts(features: list[dict[str, object]]) -> None:
    """Permit established SP precedence but refuse conflicting equal-tier identities."""
    seen: dict[tuple[str, int], str] = {}
    for feature in features:
        properties, geometry = feature.get("properties"), feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            raise ValueError("FIRMS detection has no source identity")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list):
            raise ValueError("FIRMS detection has no point coordinates")
        key = build_firms_identity(properties, coordinates).natural_key
        grain = key, processing_tier(properties.get("product"))
        fingerprint = json.dumps(feature, allow_nan=False, sort_keys=True)
        if grain in seen and seen[grain] != fingerprint:
            raise ValueError("conflicting FIRMS detections share an identity and processing tier")
        seen[grain] = fingerprint


def prepare_fire_recovery(*, source_root: Path, expected_manifest_sha256: str, output: Path) -> dict[str, object]:
    """Create a local four-rung candidate after exact-day captured-source validation."""
    manifest_path = source_root / "source-manifest.json"
    size = manifest_path.stat().st_size
    if not 0 < size <= MAX_MANIFEST_BYTES:
        raise ValueError("FIRMS source manifest exceeds its bound")
    manifest_bytes = _read(manifest_path, byte_length=size, sha256=expected_manifest_sha256)
    capture = FireSourceCapture.model_validate_json(manifest_bytes)
    availability_body = _body(source_root, capture.availability)
    applicable = products_covering(parse_product_availability(_availability(availability_body)), capture.day)
    if not applicable or set(applicable) != {item.product for item in capture.products}:
        raise ValueError("captured FIRMS responses do not cover every applicable product")
    bodies = {capture.availability.sha256: availability_body}
    features: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    for item in capture.products:
        body = _body(source_root, item.response)
        parsed = _features(body, product=item.product, capture=capture)
        counts[item.product] = len(parsed)
        features.extend(parsed)
        bodies[item.response.sha256] = body
        if len(features) > MAX_RECORDS:
            raise ValueError("complete FIRMS day exceeds the total record cap")
    _refuse_conflicts(features)
    source = fire_table_from_features(
        collapse_history_records(features),
        day=capture.day,
        fetched_at=capture.capture_finished_at,
        max_records=MAX_RECORDS,
        raw_record_count=len(features),
        source_products=applicable,
        product_counts=counts,
    )
    if source.table.num_rows == 0:
        raise ValueError("empty FIRMS captures require a separate governed-absence decision")
    native = pl.from_arrow(source.table)
    if not isinstance(native, pl.DataFrame):
        raise TypeError("FIRMS source did not produce a DataFrame")
    ladder = {
        tier: native if tier == BASE_ZOOM_TIER else derive_tier(native, stream=FIRE_DETECTIONS_STREAM, tier=tier)
        for tier in ZOOM_TIERS
    }
    if any(frame.get_column("detection_count").sum() != source.deduplicated_records for frame in ladder.values()):
        raise ValueError("FIRMS derivation lost or duplicated source detections")
    conformed = {tier: frame.to_arrow().cast(FIRE_DETECTIONS_SCHEMA.arrow_schema) for tier, frame in ladder.items()}
    for table in conformed.values():
        table.validate(full=True)
    output.mkdir(parents=True, exist_ok=False)
    (output / "responses").mkdir()
    for digest, body in bodies.items():
        (output / "responses" / f"{digest}.csv").write_bytes(body)
    (output / "source-manifest.json").write_bytes(manifest_bytes)
    parts = []
    for tier, table in conformed.items():
        relative = f"day={capture.day.isoformat()}/zoom={tier}/part-0.parquet"
        path = output / relative
        path.parent.mkdir(parents=True)
        pq.write_table(table, path, compression="zstd")
        body = path.read_bytes()
        parts.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(body).hexdigest(),
                "byte_length": len(body),
                "rows": table.num_rows,
            }
        )
    candidate: dict[str, object] = {
        "schema_version": "firms-recovery-candidate/v1",
        "apply": False,
        "state": "local_prepared_not_published",
        "source_manifest_sha256": expected_manifest_sha256,
        "capture": capture.model_dump(mode="json"),
        "raw_records": len(features),
        "deduplicated_records": source.deduplicated_records,
        "source_products": list(applicable),
        "product_counts": counts,
        "required_rungs": list(ZOOM_TIERS),
        "parts": parts,
        "upstream_population_complete": False,
        "availability_published": False,
        "admission_requires": [
            "full governed AOI coverage",
            "prior complete ladder and absence snapshot",
            "exclusive historical lane-day ownership",
            "whole-day replacement comparison",
            "generic terminal and availability finalization",
        ],
        "rollback": (
            "Restore the pinned prior complete ladder and availability generation under the same lane-day barrier; "
            "retain immutable candidate bytes."
        ),
    }
    payload = json.dumps(candidate, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    (output / "candidate.json").write_bytes(payload)
    (output / "candidate.sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n", encoding="ascii")
    return candidate
