"""Prepare bounded daily-mean NWIS Parquet candidates from captured response bytes."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.water_gauges import tables_by_publisher_day
from agri_data_service.pipeline.water_gauges_recovery.models import MAX_MANIFEST_BYTES, WaterSourceCapture
from agri_data_service.pipeline.water_gauges_recovery.normalize import normalize_responses
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, derive_tier
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_SCHEMA, WATER_GAUGES_STREAM

if TYPE_CHECKING:
    from pathlib import Path


def _read(path: Path, *, byte_length: int, sha256: str) -> bytes:
    """Verify exact bounded complete bytes against their independently pinned digest."""
    if path.stat().st_size != byte_length:
        raise ValueError("NWIS source file length differs from its receipt")
    with path.open("rb") as handle:
        body = handle.read(byte_length + 1)
    if len(body) != byte_length or hashlib.sha256(body).hexdigest() != sha256:
        raise ValueError("NWIS source file failed complete SHA-256 verification")
    return body


def prepare_water_recovery(*, source_root: Path, expected_manifest_sha256: str, output: Path) -> dict[str, object]:
    """Create all four local rungs for each validated source day without publication."""
    manifest = source_root / "source-manifest.json"
    size = manifest.stat().st_size
    if not 0 < size <= MAX_MANIFEST_BYTES:
        raise ValueError("NWIS source manifest exceeds its byte bound")
    manifest_bytes = _read(manifest, byte_length=size, sha256=expected_manifest_sha256)
    capture = WaterSourceCapture.model_validate_json(manifest_bytes)
    bodies: list[tuple[str, bytes]] = []
    for response in capture.responses:
        path = (source_root / "responses" / f"{response.sha256}.json").resolve(strict=True)
        if not path.is_relative_to(source_root.resolve(strict=True)):
            raise ValueError("NWIS captured response escapes its supplied root")
        bodies.append((response.tile, _read(path, byte_length=response.byte_length, sha256=response.sha256)))
    records, counts = normalize_responses(bodies, capture=capture)
    tables = tables_by_publisher_day(records, ingested_at=capture.capture_finished_at)
    expected_days = {
        capture.start_day + timedelta(days=offset)
        for offset in range((capture.end_day_exclusive - capture.start_day).days)
    }
    if set(tables) != expected_days:
        raise ValueError("NWIS window has empty or unreported days requiring separate governed-absence decisions")
    ladder = {}
    for day, table in tables.items():
        native = pl.from_arrow(table)
        if not isinstance(native, pl.DataFrame):
            raise TypeError("NWIS normalization did not produce a DataFrame")
        for tier in ZOOM_TIERS:
            frame = native if tier == BASE_ZOOM_TIER else derive_tier(native, stream=WATER_GAUGES_STREAM, tier=tier)
            arrow = frame.to_arrow().cast(WATER_GAUGES_SCHEMA.arrow_schema)
            arrow.validate(full=True)
            if arrow.num_rows == 0:
                raise ValueError("NWIS derivation discarded a nonempty source day")
            ladder[(day, tier)] = arrow
    output.mkdir(parents=True, exist_ok=False)
    (output / "responses").mkdir()
    for response, (_, body) in zip(capture.responses, bodies, strict=True):
        (output / "responses" / f"{response.sha256}.json").write_bytes(body)
    (output / "source-manifest.json").write_bytes(manifest_bytes)
    parts = []
    for (day, tier), table in ladder.items():
        relative = f"day={day.isoformat()}/zoom={tier}/part-0.parquet"
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
        "schema_version": "nwis-daily-recovery-candidate/v1",
        "apply": False,
        "state": "local_prepared_not_published",
        "source_manifest_sha256": expected_manifest_sha256,
        "capture": capture.model_dump(mode="json"),
        "counts": counts,
        "statistical_support": (
            "USGS parameter 00060 discharge, daily mean statistic 00003, ft3/s, publisher standard-time day"
        ),
        "required_rungs": list(ZOOM_TIERS),
        "parts": parts,
        "upstream_population_complete": False,
        "availability_published": False,
        "data_available_at": None,
        "admission_requires": [
            "full governed AOI coverage",
            "prior complete ladder and absence snapshot",
            "explicit daily-mean versus instantaneous support reconciliation",
            "lossless native-grain merge comparison",
            "exclusive historical lane-day ownership",
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
