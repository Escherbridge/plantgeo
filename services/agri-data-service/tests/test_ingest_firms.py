"""FIRMS ingestion: CSV parity, the day-range clamp, freshness rejection, constellation merge, pinned keys."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.firms import (
    FIRMS_SOURCE,
    FIRMS_VIIRS_SOURCES,
    build_fire_detection_write,
    fetch_active_fires,
    firms_day_range,
    parse_firms_csv,
    run_fire_ingestion_job,
)
from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.ingest.policy import PACIFIC_NORTHWEST_COVERAGE_BBOX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

# Captured 2026-08-03 read-only from production `geo.features` on the `fire-detections` layer:
# the third element is the exact `properties->>'id'` the TypeScript job stored.
RECORDED_DETECTION = (
    {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "1106"},
    [-113.26495, 47.83797],
    "N:2026-08-02:1106:47.8380:-113.2649",
    datetime(2026, 8, 2, 11, 6, tzinfo=UTC),
)
# Trap T3: 3133 of the 6297 stored rows carry a three-digit acqTime, raw in the key and padded in the timestamp.
RECORDED_THREE_DIGIT_DETECTION = (
    {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "926"},
    [-114.3224, 46.94507],
    "N:2026-08-02:926:46.9451:-114.3224",
    datetime(2026, 8, 2, 9, 26, tzinfo=UTC),
)

# The real VIIRS product header (nasa-firms.ts:47): no "brightness" column, only the 4um channel
# "bright_ti4". MODIS is the only FIRMS product that ever publishes "brightness".
VIIRS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,frp"
MODIS_STYLE_HEADER = "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,confidence,version,frp"
FRESH_ROW = "47.83797,-113.26495,312.4,0.4,0.4,2026-08-02,1106,N,n,2.0NRT,12.3"
STALE_ROW = "47.84259,-113.26685,298.1,0.4,0.4,2020-01-01,1106,N,l,2.0NRT,4.2"
OBSERVED_AT = datetime(2026, 8, 2, 11, 6, tzinfo=UTC)


class RecordingWriter:
    """A feature writer that records what a job handed it, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes = list(writes)
        return len(self.writes)


@pytest.fixture(autouse=True)
def _clear_firms_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS", "FIRMS_DAY_RANGE", "FIRMS_LAYER_ID"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("NASA_FIRMS_KEY", "test-key")


def _feature(properties: dict[str, object], coordinates: list[float]) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": dict(properties),
    }


def _csv(*rows: str, header: str = VIIRS_HEADER) -> str:
    return "\n".join((header, *rows))


def _csv_response(*rows: str, header: str = VIIRS_HEADER) -> httpx.Response:
    return httpx.Response(200, content=_csv(*rows, header=header).encode(), headers={"content-type": "text/csv"})


def test_the_csv_is_parsed_by_header_name_not_by_column_position() -> None:
    features = parse_firms_csv(_csv(FRESH_ROW), "VIIRS_SNPP_NRT")
    assert len(features) == 1
    assert features[0]["properties"] == {
        "brightness": 312.4,
        "confidence": "n",
        "frp": 12.3,
        "satellite": "N",
        "acqDate": "2026-08-02",
        "acqTime": "1106",
        # The product that answered, recorded as provenance and as the near-real-time versus
        # standard-processing discriminator `satellite` cannot supply.
        "product": "VIIRS_SNPP_NRT",
        # The instrument's pixel and one confidence scale, so a consumer can tell a VIIRS FRP apart
        # from a MODIS one without a product lookup table of its own.
        "spatialSupportMeters": 375,
        "confidenceNormalized": "nominal",
    }
    assert features[0]["geometry"] == {"type": "Point", "coordinates": [-113.26495, 47.83797]}


def test_viirs_brightness_is_read_from_bright_ti4_when_the_modis_column_is_absent() -> None:
    # A parser that only ever looked for "brightness" would silently zero this field on every real
    # production VIIRS row, since VIIRS never publishes a "brightness" column -- only "bright_ti4".
    features = parse_firms_csv(_csv(FRESH_ROW, header=VIIRS_HEADER), "VIIRS_SNPP_NRT")
    assert features[0]["properties"]["brightness"] == 312.4


def test_the_modis_brightness_column_takes_priority_over_bright_ti4_when_both_are_present() -> None:
    header = f"{MODIS_STYLE_HEADER},bright_ti4"
    features = parse_firms_csv(_csv(f"{FRESH_ROW},111.1", header=header), "VIIRS_SNPP_NRT")
    assert features[0]["properties"]["brightness"] == 312.4


def test_a_header_missing_a_column_falls_back_to_the_requested_product_not_a_fixed_label() -> None:
    # Python's negative indexing would silently read the LAST cell where JavaScript reads undefined.
    # The TypeScript satellite fallback is the *requested* constellation product, not a hardcoded label.
    features = parse_firms_csv(
        "latitude,longitude,acq_date,acq_time\n47.5,-113.5,2026-08-02,1106",
        "VIIRS_NOAA20_NRT",
    )
    assert features[0]["properties"]["satellite"] == "VIIRS_NOAA20_NRT"
    assert features[0]["properties"]["confidence"] == "nominal"
    # A radiometric channel the product did not publish must be ABSENT, never zero-filled: the
    # TypeScript's `parseFloat(cell) || 0` is what wrote `brightness: 0` onto all 6,297 stored
    # detections and made the read model serve an unread channel as a measured 0 K.
    assert "brightness" not in features[0]["properties"]
    assert "frp" not in features[0]["properties"]


def test_a_short_row_and_an_unparseable_coordinate_are_both_skipped() -> None:
    assert parse_firms_csv(_csv("47.5,-113.5,1"), "VIIRS_SNPP_NRT") == []
    assert parse_firms_csv(_csv("not-a-number,-113.5,1,2,3,2026-08-02,1106,N,n,2,4"), "VIIRS_SNPP_NRT") == []


def test_a_header_only_payload_carries_no_detections() -> None:
    assert parse_firms_csv(VIIRS_HEADER, "VIIRS_SNPP_NRT") == []
    assert parse_firms_csv("", "VIIRS_SNPP_NRT") == []


@pytest.mark.parametrize(
    # The `99 -> 5` case read `99 -> 10` until 2026-08-05, when the API was measured to answer
    # `400 Invalid day range. Expects [1..5].` for 6, 7 and 10 while 1-5 answered HTTP 200. The old
    # ceiling was a value the clamp advertised as legal and every product refused.
    ("configured", "expected"),
    [("", 2), ("5abc", 2), ("-3", 2), ("0", 1), ("3", 3), ("99", 5)],
)
def test_the_day_range_accepts_only_a_plain_integer(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: int,
) -> None:
    monkeypatch.setenv("FIRMS_DAY_RANGE", configured)
    assert firms_day_range() == expected


def test_a_recorded_production_detection_still_keys_to_the_stored_external_id() -> None:
    properties, coordinates, stored_external_id, observed_at = RECORDED_DETECTION
    write = build_fire_detection_write(
        _feature(properties, coordinates),
        "fire-detections",
        timedelta(days=2),
        observed_at,
    )
    assert write is not None
    assert write.external_id == stored_external_id
    assert write.natural_key == f"firms:{stored_external_id}"
    assert write.properties["observedAt"] == "2026-08-02T11:06:00.000Z"
    assert write.properties["source"] == "NASA FIRMS"
    assert write.channel == "layer:fire-detections"


def test_a_three_digit_acquisition_time_keys_raw_and_dates_padded() -> None:
    properties, coordinates, stored_external_id, observed_at = RECORDED_THREE_DIGIT_DETECTION
    write = build_fire_detection_write(
        _feature(properties, coordinates),
        "fire-detections",
        timedelta(days=2),
        observed_at,
    )
    assert write is not None
    assert write.external_id == stored_external_id
    assert write.properties["observedAt"] == "2026-08-02T09:26:00.000Z"


def test_a_stale_detection_is_dropped_rather_than_written() -> None:
    properties, coordinates, _, observed_at = RECORDED_DETECTION
    stale_now = observed_at + timedelta(days=5)
    feature = _feature(properties, coordinates)
    assert build_fire_detection_write(feature, "fire-detections", timedelta(days=2), stale_now) is None


@pytest.mark.parametrize(
    ("properties", "coordinates"),
    [
        ({"acqDate": "2026-08-02", "acqTime": "1106"}, [-113.2, 47.8]),
        ({"satellite": "N", "acqDate": "2026-08-02", "acqTime": "1106"}, []),
        ({"satellite": "N", "acqDate": "not-a-date", "acqTime": "1106"}, [-113.2, 47.8]),
    ],
)
def test_a_detection_with_no_native_key_is_dropped_rather_than_synthesised(
    properties: dict[str, object],
    coordinates: list[float],
) -> None:
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    assert build_fire_detection_write(_feature(properties, coordinates), "l", timedelta(days=2), now) is None


async def test_an_unset_bbox_is_skipped_and_never_failed() -> None:
    writer = RecordingWriter()
    result = await run_fire_ingestion_job(writer)
    assert result.source == FIRMS_SOURCE
    assert result.status == "skipped"
    assert result.reason == "INGEST_BBOX is not configured"
    assert writer.writes == []


async def test_a_missing_api_key_fails_one_fetch_before_any_request_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NASA_FIRMS_KEY", raising=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="NASA_FIRMS_KEY"):
            await fetch_active_fires(client, PACIFIC_NORTHWEST_COVERAGE_BBOX, 2, "VIIRS_SNPP_NRT")


async def test_a_missing_api_key_fails_every_satellite_and_the_job_raises_rather_than_writing_nothing_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # All three constellation fetches fail identically, so the job must raise rather than report a
    # quiet "ingested, wrote nothing" -- that is what turns a broken key into a red cron run.
    monkeypatch.delenv("NASA_FIRMS_KEY", raising=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="NASA_FIRMS_KEY"):
            await run_fire_ingestion_job(RecordingWriter(), bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX, client=client)


async def test_every_constellation_product_is_queried_and_duplicates_merge_to_one_row() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return _csv_response(FRESH_ROW, STALE_ROW)

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_fire_ingestion_job(
            writer,
            bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
            client=client,
            now=OBSERVED_AT,
        )

    assert len(requested) == len(FIRMS_VIIRS_SOURCES)
    assert all(any(source in url for url in requested) for source in FIRMS_VIIRS_SOURCES)
    assert result.status == "ingested"
    # Every product answered with the same two rows, so six were seen and one survived merge + freshness.
    assert result.records_seen == 2 * len(FIRMS_VIIRS_SOURCES)
    assert result.records_written == 1
    assert result.details["rejected"] == len(FIRMS_VIIRS_SOURCES)
    assert [write.external_id for write in writer.writes] == ["N:2026-08-02:1106:47.8380:-113.2649"]


async def test_one_unavailable_satellite_never_discards_the_others_detections() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "VIIRS_NOAA20_NRT" in str(request.url):
            return httpx.Response(503)
        return _csv_response(FRESH_ROW)

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_fire_ingestion_job(
            writer,
            bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
            client=client,
            now=OBSERVED_AT,
        )

    assert result.status == "ingested"
    assert result.records_written == 1
    assert result.reason is not None
    assert "VIIRS_NOAA20_NRT" in result.reason


async def test_the_job_fails_only_when_every_satellite_is_unavailable() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(503))) as client:
        with pytest.raises(UpstreamHttpError):
            await run_fire_ingestion_job(
                RecordingWriter(),
                bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
                client=client,
                now=OBSERVED_AT,
            )


async def test_the_job_reports_truncation_when_the_merged_constellation_exceeds_the_source_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_MAX_SOURCE_RECORDS", "1000")
    # 4-decimal-place spacing so every row keys to a distinct natural key after the identity's
    # toFixed(4) rounding: 5-digit spacing (the original fixture's mistake) aliases ~10 rows onto
    # every merged key once the constellation's Map-style dedup collapses them, which would make
    # this fixture assert a truncation that never happens.
    rows = [f"47.{index:04d},-113.26495,312.4,0.4,0.4,2026-08-02,1106,N,n,2.0NRT,12.3" for index in range(1_001)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: _csv_response(*rows))) as client:
        result = await run_fire_ingestion_job(
            RecordingWriter(),
            bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
            client=client,
            now=OBSERVED_AT,
        )
    # All three constellation products answer identically, so 3003 were seen and 1001 unique
    # detections survived the merge -- capped at the configured 1000.
    assert result.records_seen == 1_001 * len(FIRMS_VIIRS_SOURCES)
    assert result.truncated is True
    assert result.records_written == 1_000
