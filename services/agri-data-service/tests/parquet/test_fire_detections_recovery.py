"""Complete source-day replay, preserved bytes and full FIRMS ladders."""

# ruff: noqa: PLR2004

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.fire_detections_recovery.prepare import prepare_fire_recovery
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_SCHEMA

if TYPE_CHECKING:
    from pathlib import Path

CLOCK = "2026-09-11T18:00:00+00:00"
HEADER = "latitude,longitude,acq_date,acq_time,satellite,confidence,frp\n"
ROW = '45.4,-122.1,2022-08-05,1200,N,"high","10.0"\n'


def _response(root: Path, body: bytes) -> dict[str, object]:
    digest = hashlib.sha256(body).hexdigest()
    (root / "responses" / f"{digest}.csv").write_bytes(body)
    return {
        "sha256": digest,
        "byte_length": len(body),
        "captured_at": CLOCK,
        "status_code": 200,
        "transport_complete": True,
    }


def _capture(root: Path, products: dict[str, str], *, availability: str | None = None) -> str:
    root.mkdir()
    (root / "responses").mkdir()
    available = availability or "data_id,min_date,max_date\n" + "".join(
        f"{product},2020-01-01,2023-01-01\n" for product in products
    )
    manifest = {
        "schema_version": "firms-source-capture/v1",
        "day": "2022-08-05",
        "bbox": [-123, 45, -122, 46],
        "capture_started_at": CLOCK,
        "capture_finished_at": CLOCK,
        "request_count": len(products) + 1,
        "availability": _response(root, available.encode()),
        "products": [
            {"product": product, "response": _response(root, body.encode())} for product, body in products.items()
        ],
    }
    body = json.dumps(manifest).encode()
    (root / "source-manifest.json").write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def test_full_ladder_retains_schema_counts_and_quoted_values(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, {"VIIRS_SNPP_SP": HEADER + ROW})
    candidate = prepare_fire_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert candidate["apply"] is False
    assert candidate["upstream_population_complete"] is False
    assert candidate["required_rungs"] == [0, 5, 9, 13]
    assert (output / "source-manifest.json").read_bytes() == (source / "source-manifest.json").read_bytes()
    for path in output.glob("day=*/zoom=*/part-0.parquet"):
        table = pq.ParquetFile(path).read()
        assert table.schema == FIRE_DETECTIONS_SCHEMA.arrow_schema
        row = table.to_pylist()[0]
        assert row["detection_count"] == 1
        assert row["frp_sum"] == 10.0
        assert row["frp_observation_count"] == 1
        assert row["high_confidence_detection_count"] == 1
    for path in (source / "responses").iterdir():
        assert (output / "responses" / path.name).read_bytes() == path.read_bytes()


def test_missing_applicable_product_refuses_partial_constellation(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(
        source,
        {"VIIRS_SNPP_SP": HEADER + ROW},
        availability="data_id,min_date,max_date\nVIIRS_SNPP_SP,2020-01-01,2023-01-01\nMODIS_SP,2020-01-01,2023-01-01\n",
    )
    with pytest.raises(ValueError, match="every applicable product"):
        prepare_fire_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert not output.exists()


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        (HEADER + ROW + ROW.replace("10.0", "11.0"), "conflicting FIRMS detections"),
        (HEADER + ROW + "broken\n", "CSV is malformed"),
        (HEADER, "empty FIRMS captures"),
        (HEADER + ROW.replace("10.0", "1_0"), "whole ASCII decimal"),
    ],
)
def test_conflicts_malformed_rows_and_empty_population_refuse(tmp_path: Path, body: str, expected_error: str) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, {"VIIRS_SNPP_SP": body})
    with pytest.raises(ValueError, match=expected_error):
        prepare_fire_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert not output.exists()


def test_standard_processing_supersedes_nrt_without_adding_both(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, {"VIIRS_SNPP_NRT": HEADER + ROW.replace("10.0", "8.0"), "VIIRS_SNPP_SP": HEADER + ROW})
    candidate = prepare_fire_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    table = pq.ParquetFile(output / "day=2022-08-05/zoom=13/part-0.parquet").read()
    assert candidate["raw_records"] == 2
    assert candidate["deduplicated_records"] == 1
    assert table.to_pylist()[0]["frp_sum"] == 10.0


def test_full_hash_prevents_same_length_response_replacement(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, {"VIIRS_SNPP_SP": HEADER + ROW})
    for path in (source / "responses").iterdir():
        if path.read_bytes().startswith(b"latitude"):
            path.write_bytes(path.read_bytes().replace(b"10.0", b"11.0"))
    with pytest.raises(ValueError, match="SHA-256"):
        prepare_fire_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert not output.exists()
