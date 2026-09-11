"""Bounded daily-value replay preserves native support and refuses partial input."""

# ruff: noqa: PLR2004

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.water_gauges_recovery.prepare import prepare_water_recovery
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_SCHEMA

if TYPE_CHECKING:
    from pathlib import Path

CLOCK = "2026-09-11T18:00:00+00:00"


def _series(site: str = "14137000", *, flow: str = "-2.5") -> dict[str, object]:
    return {
        "sourceInfo": {
            "siteName": "Sandy River",
            "siteCode": [{"value": site}],
            "geoLocation": {"geogLocation": {"latitude": 45.4, "longitude": -122.1}},
            "timeZoneInfo": {
                "defaultTimeZone": {"zoneOffset": "-08:00"},
                "daylightSavingsTimeZone": {"zoneOffset": "-07:00"},
            },
        },
        "variable": {
            "variableCode": [{"value": "00060"}],
            "unit": {"unitCode": "ft3/s"},
            "noDataValue": -999999.0,
            "options": {"option": [{"name": "Statistic", "optionCode": "00003"}]},
        },
        "values": [{"value": [{"dateTime": "2022-08-05T00:00:00.000", "value": flow, "qualifiers": ["A"]}]}],
    }


def _capture(
    root: Path,
    series: list[dict[str, object]],
    *,
    end_day: str = "2022-08-06",
    bbox: list[float] | None = None,
    raw_body: bytes | None = None,
) -> str:
    root.mkdir()
    (root / "responses").mkdir()
    body = raw_body if raw_body is not None else json.dumps({"value": {"timeSeries": series}}).encode()
    digest = hashlib.sha256(body).hexdigest()
    (root / "responses" / f"{digest}.json").write_bytes(body)
    manifest = {
        "schema_version": "nwis-daily-source-capture/v1",
        "start_day": "2022-08-05",
        "end_day_exclusive": end_day,
        "bbox": bbox or [-123, 45, -122, 46],
        "parameter_cd": "00060",
        "statistic_cd": "00003",
        "site_type": "ST",
        "site_status_filter": "all",
        "capture_started_at": CLOCK,
        "capture_finished_at": CLOCK,
        "request_count": 1,
        "responses": [
            {
                "tile": "-123,45,-122,46",
                "sha256": digest,
                "byte_length": len(body),
                "captured_at": CLOCK,
                "status_code": 200,
                "transport_complete": True,
            }
        ],
    }
    raw_manifest = json.dumps(manifest).encode()
    (root / "source-manifest.json").write_bytes(raw_manifest)
    return hashlib.sha256(raw_manifest).hexdigest()


def test_daily_mean_four_rungs_preserve_units_sign_and_publisher_day(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, [_series(), _series("14138000", flow="-999999")])
    candidate = prepare_water_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert candidate["apply"] is False
    assert candidate["upstream_population_complete"] is False
    assert candidate["counts"] == {
        "raw_readings": 2,
        "sentinel_readings": 1,
        "identical_duplicates": 0,
        "normalized_readings": 1,
    }
    for path in output.glob("day=*/zoom=*/part-0.parquet"):
        table = pq.ParquetFile(path).read()
        assert table.schema == WATER_GAUGES_SCHEMA.arrow_schema
        row = table.to_pylist()[0]
        assert row["observed_day"] == date(2022, 8, 5)
        assert row["observed_at"] == datetime(2022, 8, 5, 8, tzinfo=UTC)
        assert row["flow_cfs"] == -2.5
        assert row["data_available_at"] is None
        assert row["ingested_at"] == datetime.fromisoformat(CLOCK)
    assert len(list(output.glob("day=*/zoom=*/part-0.parquet"))) == 4
    assert (output / "source-manifest.json").read_bytes() == (source / "source-manifest.json").read_bytes()


def test_conflicting_duplicate_is_refused_instead_of_first_wins(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, [_series(), _series(flow="10")])
    with pytest.raises(ValueError, match="conflicting"):
        prepare_water_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert not output.exists()


def test_identical_duplicate_retains_one_native_grain(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, [_series(), _series()])
    candidate = prepare_water_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert candidate["counts"] == {
        "raw_readings": 2,
        "sentinel_readings": 0,
        "identical_duplicates": 1,
        "normalized_readings": 1,
    }


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [
        ("zone", "valid source standard-time offset"),
        ("units", "ft3/s units"),
        ("statistic", "daily mean statistic 00003"),
        ("malformed", "whole ASCII decimal"),
        ("numeric_underscore", "whole ASCII decimal"),
        ("undercovered", "undercovers its requested days"),
        ("missing_tile", "every canonical request tile"),
        ("empty", "empty or unreported days"),
    ],
)
def test_unproven_or_malformed_source_support_refuses_before_output(
    tmp_path: Path, mode: str, expected_error: str
) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    series = _series()
    body = json.dumps({"value": {"timeSeries": [series]}}).encode()
    if mode == "zone":
        body = body.replace(b'"-08:00"', b'"invalid"')
    elif mode == "units":
        body = body.replace(b'"ft3/s"', b'"m3/s"')
    elif mode == "statistic":
        body = body.replace(b'"00003"', b'"00001"')
    elif mode == "malformed":
        body = body.replace(b'"-2.5"', b'"-2.5junk"')
    elif mode == "numeric_underscore":
        body = body.replace(b'"-2.5"', b'"1_0"')
    elif mode == "empty":
        body = b'{"value":{"timeSeries":[]}}'
    digest = _capture(
        source,
        [series],
        raw_body=body,
        end_day="2022-08-07" if mode == "undercovered" else "2022-08-06",
        bbox=[-130, 45, -122, 46] if mode == "missing_tile" else None,
    )
    with pytest.raises(ValueError, match=expected_error):
        prepare_water_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert not output.exists()


def test_duplicate_json_object_keys_are_not_silently_overwritten(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, [], raw_body=b'{"value":{"timeSeries":[]},"value":{"timeSeries":[]}}')
    with pytest.raises(ValueError, match="duplicate JSON"):
        prepare_water_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert not output.exists()


def test_hash_mismatch_refuses_even_when_json_is_still_well_formed(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "candidate"
    digest = _capture(source, [_series()])
    for path in (source / "responses").iterdir():
        path.write_bytes(path.read_bytes().replace(b"-2.5", b"-3.5"))
    with pytest.raises(ValueError, match="SHA-256"):
        prepare_water_recovery(source_root=source, expected_manifest_sha256=digest, output=output)
    assert not output.exists()
