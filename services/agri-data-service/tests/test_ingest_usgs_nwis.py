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
    is_missing_value_sentinel,
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


def _series_with_readings(site_number: str, readings: list[tuple[str, str]]) -> dict[str, object]:
    """A time series carrying an explicit ordered list of (dateTime, value) readings."""
    series = _series(site_number, None)
    series["values"] = [
        {
            "value": [{"value": value, "dateTime": reading_time} for reading_time, value in readings],
            "qualifier": [{"qualifierCode": "P"}],
        }
    ]
    return series


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
    assert gauge is not None
    assert gauge["updatedAt"] == "2026-08-02T18:30:00.000-06:00"
    assert gauge["updatedAtIsWallClock"] is False
    assert gauge["flowCfs"] == 123.0
    assert gauge["condition"] == "unknown"


def test_a_silent_gauge_keeps_the_wall_clock_fallback_and_is_flagged_for_the_operator() -> None:
    # Owner ruling: `usgs-water.ts:183` is ported as-is, so this gauge mints a fresh id every run.
    # The flag and the per-run count are the agreed metric to watch. See ingest/AGENTS.md.
    gauge = parse_gauge(_series("05014500", None), NOW)
    assert gauge is not None
    assert gauge["updatedAt"] == "2026-08-03T12:00:00.000Z"
    assert gauge["updatedAtIsWallClock"] is True
    assert gauge["flowCfs"] is None


def test_a_recorded_production_gauge_still_keys_to_the_stored_external_id() -> None:
    gauge = parse_gauge(_series("05014500", "2026-08-02T18:30:00.000-06:00"), NOW)
    assert gauge is not None
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
    assert gauge is not None
    assert build_gauge_write(gauge, "water-gauges") is None


def test_the_missing_value_sentinel_is_dropped_rather_than_written_as_a_reading() -> None:
    # The forward-path half of the archive path's rule. -999999 arrives as an ordinary numeric
    # string; writing it poisons every percentile and colour ramp computed from streamflow.
    assert parse_gauge(_series("12024000", "2026-08-07T19:30:00.000-07:00", value="-999999"), NOW) is None


def test_a_sentinel_only_gauge_is_dropped_outright_and_never_given_the_wall_clock_fallback() -> None:
    # The T5 wall-clock fallback exists for a gauge NWIS reported no reading for at all. A sentinel
    # gauge is the other case: NWIS did report, and reported that it measured nothing. Routing it
    # through T5 would write a null flow at a real timestamp -- a fabricated observation of an
    # absence -- and routing it through the wall clock would mint a fresh feature id every 30
    # minutes on top of that.
    silent = parse_gauge(_series("12024000", None), NOW)
    assert silent is not None
    assert silent["updatedAtIsWallClock"] is True

    assert parse_gauge(_series("12024000", "2026-08-07T19:30:00.000-07:00", value="-999999"), NOW) is None


def test_genuine_reverse_flow_is_kept_because_the_sentinel_is_matched_by_value_not_by_sign() -> None:
    # validation.py's USGS_NO_DATA_SENTINEL records reverse flow reaching -172,000 cfs at these
    # gauges, so a "negative means missing" guard would delete real measurements.
    gauge = parse_gauge(_series("12024000", "2026-08-07T19:30:00.000-07:00", value="-172000"), NOW)
    assert gauge is not None
    assert gauge["flowCfs"] == -172000.0
    assert is_missing_value_sentinel(-172000.0) is False
    assert is_missing_value_sentinel(-999999.0) is True
    assert is_missing_value_sentinel(None) is False


def test_an_earlier_real_reading_is_preferred_over_a_trailing_sentinel_in_the_same_response() -> None:
    # Unreachable under the current query, which pins no `period` and so returns exactly one reading
    # per site (measured 2026-08-07: 194 of 194 series). It is asserted anyway so that adding a
    # `period` keeps the real reading rather than silently discarding the gauge.
    gauge = parse_gauge(
        _series_with_readings(
            "12024000",
            [("2026-08-07T19:15:00.000-07:00", "483"), ("2026-08-07T19:30:00.000-07:00", "-999999")],
        ),
        NOW,
    )
    assert gauge is not None
    assert gauge["flowCfs"] == 483.0
    assert gauge["updatedAt"] == "2026-08-07T19:15:00.000-07:00"
    assert gauge["updatedAtIsWallClock"] is False


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
    assert result.details["sentinel_gauges"] == 0
    assert {write.external_id for write in writer.writes} == {
        "05014500:2026-08-03T12:00:00.000Z",
        "05014600:2026-08-02T18:30:00.000-06:00",
    }


async def test_the_job_writes_no_sentinel_gauge_and_counts_the_dropped_sites_once_across_tiles() -> None:
    time_series = [
        _series("12024000", "2026-08-07T19:30:00.000-07:00", value="-999999"),
        _series("12024400", "2026-08-07T20:00:00.000-07:00", value="-999999"),
        _series("05014600", "2026-08-02T18:30:00.000-06:00"),
    ]
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

    # All eight tiles answer with the same three sites, so the two sentinel sites are counted once
    # each rather than sixteen times, and only the reporting gauge reaches the warehouse.
    assert result.details["sentinel_gauges"] == 2
    assert result.records_seen == 1
    assert result.records_written == 1
    assert [write.external_id for write in writer.writes] == ["05014600:2026-08-02T18:30:00.000-06:00"]
    assert all(write.properties["flowCfs"] != -999999 for write in writer.writes)
