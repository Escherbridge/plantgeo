"""USGS NWIS source adapter: bbox tiling, gauge parsing, and the wall-clock identity fallback kept by owner ruling.

The forward `geo.features` job this file also covered (`run_water_ingestion_job`) was deleted
2026-09-06 with its `ingest-streamflow` verb and `postgres-streamflow` lane.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest import http as http_module
from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamHttpError,
    UpstreamTransportError,
    upstream_client,
)
from agri_data_service.ingest.policy import PACIFIC_NORTHWEST_COVERAGE_BBOX
from agri_data_service.ingest.usgs_nwis import (
    USGS_TILE_RETRY_MAX_ATTEMPTS,
    build_gauge_write,
    classify_condition,
    fetch_streamflow_gauges,
    format_tile_ordinate,
    is_missing_value_sentinel,
    parse_gauge,
    tile_bbox,
)
from agri_data_service.pipeline.direct.water_gauges import tables_by_publisher_day

if TYPE_CHECKING:
    from collections.abc import Callable

_EMPTY_SERIES_PAYLOAD = {"value": {"timeSeries": []}}


async def _no_sleep(_delay: float) -> None:
    """Skip the retry ladder's backoff wait so a tile-retry test runs at full speed."""


# Captured 2026-08-03 read-only from production `geo.features` on the `water-gauges` layer.
RECORDED_GAUGE_EXTERNAL_ID = "05014500:2026-08-02T18:30:00.000-06:00"

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


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


@pytest.mark.parametrize("qualifiers", [None, [], [{"qualifierCode": "P"}], [{"qualifierCode": "E"}]])
def test_gauge_qualifiers_never_fabricate_a_trend_or_flow_condition(qualifiers: object) -> None:
    series = _series("14018500", "2026-09-10T10:15:00Z", value="17.7")
    series["values"] = [{"value": [{"value": "17.7", "dateTime": "2026-09-10T10:15:00Z"}], "qualifier": qualifiers}]
    gauge = parse_gauge(series, NOW)
    assert gauge is not None
    assert gauge["flowCfs"] == 17.7
    assert gauge["updatedAt"] == "2026-09-10T10:15:00Z"
    assert gauge["percentile"] is None
    assert gauge["condition"] == "unknown"
    assert gauge["trend"] is None
    write = build_gauge_write(gauge, "water-gauges")
    assert write is not None
    assert write.properties["trend"] is None
    table = tables_by_publisher_day([gauge], ingested_at=NOW)[date(2026, 9, 10)]
    row = table.to_pylist()[0]
    assert row["flow_cfs"] == 17.7
    assert row["trend"] is None
    assert row["condition"] == "unknown"


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


# THREE JOB TESTS STOOD HERE AND ARE DELETED WITH THEIR SUBJECT (2026-09-06): the unset-bbox skip, the
# cross-tile site dedupe, and the sentinel-gauge drop count all exercised `run_water_ingestion_job`, the
# deleted `geo.features` forward writer. `pipeline/parquet/water_gauges_forward.py` replaced it and
# drives the SAME `fetch_streamflow_gauges` (which owns the tiling and the dedupe) and `build_gauge_write`
# still covered above, so the parsing and identity contract is unchanged and is asserted here; the
# writer-level behaviour is asserted against the Parquet writer in `tests/parquet/`.


# --- the USGS forward per-tile retry policy (plan 0W.2 GL-2) ----------------------------------------

_ONE_TILE_BBOX = "-113,46,-111,48"  # tile_bbox() returns exactly one tile for this span
_TWO_TILE_BBOX = "-125,42,-117,46"  # tile_bbox() returns exactly two tiles for this span


def _sequenced_handler(responses: list[httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return remaining.pop(0)

    return handler


def _always(status: int) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status)

    return handler


_USGS_TEST_BOUNDS = UpstreamBounds(max_bytes=1024 * 1024, timeout_seconds=5.0)


def _notes_contain(error: BaseException, text: str) -> bool:
    """True when `text` appears in one of the exception's own `add_note`d notes (never `str(error)`)."""
    return any(text in note for note in getattr(error, "__notes__", None) or ())


async def test_a_transient_tile_failure_recovers_within_the_usgs_retry_budget() -> None:
    handler = _sequenced_handler([httpx.Response(503), httpx.Response(200, json=_EMPTY_SERIES_PAYLOAD)])
    async with upstream_client(_USGS_TEST_BOUNDS, transport=httpx.MockTransport(handler)) as client:
        result = await fetch_streamflow_gauges(client, _ONE_TILE_BBOX, sleep=_no_sleep)
    assert result.gauges == []


async def test_a_persistent_tile_failure_raises_the_original_type_naming_the_failed_tile() -> None:
    async with upstream_client(_USGS_TEST_BOUNDS, transport=httpx.MockTransport(_always(503))) as client:
        with pytest.raises(UpstreamHttpError) as excinfo:
            await fetch_streamflow_gauges(client, _ONE_TILE_BBOX, sleep=_no_sleep)
    assert excinfo.value.status == httpx.codes.SERVICE_UNAVAILABLE
    assert _notes_contain(excinfo.value, _ONE_TILE_BBOX)


async def test_a_non_retryable_tile_status_fails_on_the_first_attempt() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(400)

    async with upstream_client(_USGS_TEST_BOUNDS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamHttpError) as excinfo:
            await fetch_streamflow_gauges(client, _ONE_TILE_BBOX, sleep=_no_sleep)
    assert calls["n"] == 1
    assert _notes_contain(excinfo.value, _ONE_TILE_BBOX)


async def test_probe_mode_gives_a_failing_tile_exactly_one_attempt() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(503)

    async with upstream_client(_USGS_TEST_BOUNDS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamHttpError):
            await fetch_streamflow_gauges(client, _ONE_TILE_BBOX, probe=True, sleep=_no_sleep)
    assert calls["n"] == 1


async def test_a_persistent_tile_failure_uses_the_full_usgs_attempt_budget_outside_probe() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(503)

    async with upstream_client(_USGS_TEST_BOUNDS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamHttpError):
            await fetch_streamflow_gauges(client, _ONE_TILE_BBOX, sleep=_no_sleep)
    assert calls["n"] == USGS_TILE_RETRY_MAX_ATTEMPTS


async def test_all_or_nothing_is_kept_when_one_of_two_tiles_fails_permanently() -> None:
    """A tile that never recovers still fails the whole gather, even though its sibling tile answers cleanly."""

    def handler(request: httpx.Request) -> httpx.Response:
        bbox = request.url.params.get("bBox", "")
        if bbox.startswith("-125,"):
            return httpx.Response(503)
        return httpx.Response(200, json=_EMPTY_SERIES_PAYLOAD)

    async with upstream_client(_USGS_TEST_BOUNDS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamHttpError) as excinfo:
            await fetch_streamflow_gauges(client, _TWO_TILE_BBOX, sleep=_no_sleep)
    assert _notes_contain(excinfo.value, "-125,42,-121,46")


async def test_a_transport_failure_names_the_failing_tile(monkeypatch: pytest.MonkeyPatch) -> None:
    """`UpstreamTransportError` is never caught by `retry_upstream`, so the tile note has to survive that path too."""
    monkeypatch.setattr(http_module, "TRANSPORT_RETRY_BASE_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("refused")

    async with upstream_client(_USGS_TEST_BOUNDS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamTransportError) as excinfo:
            await fetch_streamflow_gauges(client, _ONE_TILE_BBOX, sleep=_no_sleep)
    assert _notes_contain(excinfo.value, _ONE_TILE_BBOX)
