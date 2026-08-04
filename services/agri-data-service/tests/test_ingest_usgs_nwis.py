"""USGS NWIS ingestion: bbox tiling, gauge parsing, and the wall-clock identity fallback kept by owner ruling."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.policy import PACIFIC_NORTHWEST_COVERAGE_BBOX
from agri_data_service.ingest.usgs_nwis import (
    USGS_STREAMFLOW_SOURCE,
    build_gauge_write,
    classify_condition,
    format_tile_ordinate,
    infer_trend,
    parse_gauge,
    run_water_ingestion_job,
    tile_bbox,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

# Captured 2026-08-03 read-only from production `geo.features` on the `water-gauges` layer.
RECORDED_GAUGE_EXTERNAL_ID = "05014500:2026-08-02T18:30:00.000-06:00"

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


class RecordingWriter:
    """A feature writer that records what a job handed it, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes = list(writes)
        return len(self.writes)


def _series(site_number: str, reading_time: str | None, *, value: str = "123.0") -> dict[str, object]:
    values: list[dict[str, object]] = []
    if reading_time is not None:
        values = [{"value": value, "dateTime": reading_time}]
    return {
        "sourceInfo": {
            "siteName": f"Gauge {site_number}",
            "siteCode": [{"value": site_number}],
            "geoLocation": {"geogLocation": {"latitude": 47.5, "longitude": -113.5}},
        },
        "values": [{"value": values, "qualifier": [{"qualifierCode": "P"}]}],
        "variable": {"variableCode": [{"value": "00060"}]},
    }


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS", "WATER_GAUGES_LAYER_ID"):
        monkeypatch.delenv(variable, raising=False)


def test_a_tile_ordinate_is_joined_the_way_javascript_joins_it() -> None:
    assert format_tile_ordinate(-125.0) == "-125"
    assert format_tile_ordinate(-113.1234567) == "-113.123457"


def test_the_coverage_bbox_tiles_into_four_degree_squares_covering_the_whole_extent() -> None:
    tiles = tile_bbox(PACIFIC_NORTHWEST_COVERAGE_BBOX)
    assert tiles[0] == "-125,42,-121,46"
    assert len(tiles) == 8
    assert all(len(tile.split(",")) == 4 for tile in tiles)
    # The last row and column are smaller rather than overhanging the bbox.
    assert tiles[-1] == "-113,46,-111,49"


def test_a_condition_is_unknown_without_a_percentile_and_graded_with_one() -> None:
    assert classify_condition(None) == "unknown"
    assert classify_condition(80) == "above_normal"
    assert classify_condition(50) == "normal"
    assert classify_condition(12) == "below_normal"
    assert classify_condition(6) == "low"
    assert classify_condition(1) == "critically_low"


def test_a_trend_declines_only_on_the_estimated_qualifier() -> None:
    assert infer_trend(None) == "stable"
    assert infer_trend([{"qualifierCode": "P"}]) == "stable"
    assert infer_trend([{"qualifierCode": "E"}]) == "declining"


def test_a_gauge_with_a_reading_keeps_the_upstream_reading_time_verbatim() -> None:
    gauge = parse_gauge(_series("05014500", "2026-08-02T18:30:00.000-06:00"), NOW)
    assert gauge["updatedAt"] == "2026-08-02T18:30:00.000-06:00"
    assert gauge["updatedAtIsWallClock"] is False
    assert gauge["flowCfs"] == 123.0
    assert gauge["condition"] == "unknown"


def test_a_silent_gauge_keeps_the_wall_clock_fallback_and_is_flagged_for_the_operator() -> None:
    # Owner ruling: `usgs-water.ts:183` is ported as-is, so this gauge mints a fresh id every run.
    # The flag and the per-run count are the agreed metric to watch. See ingest/AGENTS.md.
    gauge = parse_gauge(_series("05014500", None), NOW)
    assert gauge["updatedAt"] == "2026-08-03T12:00:00.000Z"
    assert gauge["updatedAtIsWallClock"] is True
    assert gauge["flowCfs"] is None


def test_a_recorded_production_gauge_still_keys_to_the_stored_external_id() -> None:
    gauge = parse_gauge(_series("05014500", "2026-08-02T18:30:00.000-06:00"), NOW)
    write = build_gauge_write(gauge, "water-gauges")
    assert write is not None
    assert write.external_id == RECORDED_GAUGE_EXTERNAL_ID
    assert write.natural_key == f"usgs-nwis:{RECORDED_GAUGE_EXTERNAL_ID}"
    assert write.channel == "layer:water-gauges"
    assert write.properties["source"] == "USGS NWIS"
    assert write.properties["geometry"] == {"type": "Point", "coordinates": [-113.5, 47.5]}
    # The operator-only flag never reaches the stored payload.
    assert "updatedAtIsWallClock" not in write.properties


def test_a_gauge_with_no_site_number_is_dropped_rather_than_keyed_on_an_empty_prefix() -> None:
    gauge = parse_gauge(_series("", "2026-08-02T18:30:00.000-06:00"), NOW)
    assert build_gauge_write(gauge, "water-gauges") is None


async def test_an_unset_bbox_is_skipped_and_never_failed() -> None:
    result = await run_water_ingestion_job(RecordingWriter())
    assert result.source == USGS_STREAMFLOW_SOURCE
    assert result.status == "skipped"
    assert result.reason == "INGEST_BBOX is not configured"


async def test_the_job_dedupes_boundary_sites_across_tiles_and_counts_wall_clock_identities() -> None:
    time_series = [_series("05014500", None), _series("05014600", "2026-08-02T18:30:00.000-06:00")]
    payload = {"value": {"timeSeries": time_series}}
    response = httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        result = await run_water_ingestion_job(
            writer,
            bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
            client=client,
            now=NOW,
        )

    # Eight tiles each answer with the same two sites; deduping by site number leaves two gauges.
    assert result.records_seen == 2
    assert result.records_written == 2
    assert result.details["wall_clock_identities"] == 1
    assert result.details["rejected"] == 0
    assert {write.external_id for write in writer.writes} == {
        "05014500:2026-08-03T12:00:00.000Z",
        "05014600:2026-08-02T18:30:00.000-06:00",
    }
