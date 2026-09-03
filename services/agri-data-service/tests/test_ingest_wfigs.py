"""WFIGS ingestion: the null-severity contract, epoch timestamp parity, retry policy, and pinned production keys."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest import wfigs
from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError
from agri_data_service.ingest.wfigs import (
    MAX_RECORD_COUNT,
    WFIGS_BOUNDS,
    WFIGS_GEOMETRY_PRECISION,
    WFIGS_HISTORY_CAPABILITY,
    WFIGS_PERIMETER_HISTORY_EARLIEST,
    WFIGS_SOURCE,
    WFIGS_TOTAL_BYTE_BUDGET,
    build_perimeter_write,
    build_query_url,
    epoch_milliseconds_to_iso,
    fetch_fire_perimeters,
    fetch_fire_perimeters_walk,
    parse_perimeter_collection,
    perimeter_severity,
    run_fire_perimeters_ingestion_job,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

# Captured 2026-08-03 read-only from production `geo.features` on the `fire-perimeters` layer.
RECORDED_FIRE_IDENTIFIER = "2026-ID1AX-000618"
POLYGON_EPOCH_MILLISECONDS = 1_784_087_760_000  # 2026-07-15T03:56:00.000Z

SQUARE_RING = [[-113.0, 47.0], [-113.0, 47.1], [-112.9, 47.1], [-112.9, 47.0], [-113.0, 47.0]]


class RecordingWriter:
    """A feature writer that records what a job handed it, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes = list(writes)
        return len(self.writes)


async def _no_sleep(_delay: float) -> None:
    """Skip the backoff wait so a retry test runs at full speed."""


def _json_response(payload: dict[str, object]) -> httpx.Response:
    """Build a 200 JSON response from a payload dict, so a page fixture fits on one call site line."""
    return httpx.Response(200, content=json.dumps(payload).encode(), headers={"content-type": "application/json"})


def _collection(**property_overrides: object) -> dict[str, object]:
    properties: dict[str, object] = {
        "attr_UniqueFireIdentifier": RECORDED_FIRE_IDENTIFIER,
        "attr_IrwinID": "irwin-1",
        "poly_IncidentName": "Test Fire",
        "attr_FireDiscoveryDateTime": 1_783_000_000_000,
        "poly_PolygonDateTime": POLYGON_EPOCH_MILLISECONDS,
        "poly_GISAcres": 1234.5,
        "attr_FireCause": "Natural",
        "attr_IncidentTypeCategory": "WF",
        "attr_POOState": "US-ID",
        "attr_PercentContained": 10.0,
    }
    properties.update(property_overrides)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [SQUARE_RING]},
                "properties": properties,
            }
        ],
    }


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS", "FIRE_PERIMETERS_LAYER_ID"):
        monkeypatch.delenv(variable, raising=False)


def test_no_reported_containment_grades_as_unknown_rather_than_the_lowest_severity() -> None:
    # `src/lib/map/layers.ts:71` renders against this contract; "low" here would paint a lie.
    assert perimeter_severity(None) is None
    assert perimeter_severity("50") is None
    assert perimeter_severity(True) is None
    assert perimeter_severity(float("nan")) is None


def test_containment_grades_across_the_documented_bands() -> None:
    assert perimeter_severity(0) == "critical"
    assert perimeter_severity(24.9) == "critical"
    assert perimeter_severity(25) == "high"
    assert perimeter_severity(50) == "moderate"
    assert perimeter_severity(75) == "low"
    assert perimeter_severity(1_000) == "low"


def test_an_epoch_millisecond_field_renders_as_javascript_rendered_it() -> None:
    assert epoch_milliseconds_to_iso(POLYGON_EPOCH_MILLISECONDS) == "2026-07-15T03:56:00.000Z"
    assert epoch_milliseconds_to_iso(0) == "1970-01-01T00:00:00.000Z"


def test_an_absent_or_unreal_epoch_field_becomes_null() -> None:
    assert epoch_milliseconds_to_iso(None) is None
    assert epoch_milliseconds_to_iso("2026-07-15") is None
    assert epoch_milliseconds_to_iso(9e15) is None


def test_the_query_url_asks_for_a_bounded_geojson_page() -> None:
    url = build_query_url("-125,42,-111,49")
    assert "f=geojson" in url
    assert f"resultRecordCount={MAX_RECORD_COUNT}" in url
    assert "resultOffset=0" in url
    assert f"geometryPrecision={WFIGS_GEOMETRY_PRECISION}" in url
    assert "spatialRel=esriSpatialRelIntersects" in url
    assert "resultOffset=100" in build_query_url("-125,42,-111,49", offset=100)


def test_an_arcgis_error_payload_answered_with_http_200_is_still_a_failure() -> None:
    with pytest.raises(UpstreamPayloadError, match="WFIGS API error: Too many requests"):
        parse_perimeter_collection({"error": {"code": 429, "message": "Too many requests"}})


def test_an_unexpected_collection_shape_is_refused() -> None:
    with pytest.raises(UpstreamPayloadError, match="unexpected feature collection"):
        parse_perimeter_collection({"type": "FeatureCollection", "features": [{"type": "Feature"}]})


def test_a_perimeter_parses_into_the_stored_property_shape() -> None:
    page = parse_perimeter_collection(_collection())
    assert page.exceeded_transfer_limit is False
    perimeters = page.perimeters
    assert perimeters[0]["uniqueFireIdentifier"] == RECORDED_FIRE_IDENTIFIER
    assert perimeters[0]["polygonDateTime"] == "2026-07-15T03:56:00.000Z"
    assert perimeters[0]["gisAcres"] == 1234.5


def test_a_page_that_exceeded_the_transfer_limit_reports_it() -> None:
    collection = _collection()
    collection["properties"] = {"exceededTransferLimit": True}
    assert parse_perimeter_collection(collection).exceeded_transfer_limit is True


def test_a_recorded_production_perimeter_still_keys_to_the_bare_fire_identifier() -> None:
    perimeter = parse_perimeter_collection(_collection()).perimeters[0]
    write = build_perimeter_write(perimeter, "fire-perimeters")
    assert write is not None
    assert write.external_id == RECORDED_FIRE_IDENTIFIER
    assert write.natural_key == f"wfigs:{RECORDED_FIRE_IDENTIFIER}"
    assert write.channel == "layer:fire-perimeters"
    assert write.properties["severity"] == "critical"
    assert write.properties["source"] == "WFIGS Interagency Fire Perimeters"


def test_a_perimeter_with_no_containment_stores_a_null_severity() -> None:
    perimeter = parse_perimeter_collection(_collection(attr_PercentContained=None)).perimeters[0]
    write = build_perimeter_write(perimeter, "fire-perimeters")
    assert write is not None
    assert write.properties["severity"] is None
    assert write.properties["percentContained"] is None


def test_the_history_declaration_names_the_wfigs_perimeter_record_floor() -> None:
    # Declared, not implemented: nothing fetches a past WFIGS window yet. A typed refusal here would
    # be a FALSE refusal -- WFIGS does publish historical perimeter services beside `_Current`.
    assert WFIGS_HISTORY_CAPABILITY.supported is True
    assert WFIGS_HISTORY_CAPABILITY.earliest == WFIGS_PERIMETER_HISTORY_EARLIEST
    assert WFIGS_PERIMETER_HISTORY_EARLIEST.tzinfo is not None
    assert WFIGS_PERIMETER_HISTORY_EARLIEST.year == 2020


async def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            content=json.dumps(_collection()).encode(),
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        perimeters, more_remaining = await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)
    assert len(attempts) == 2
    assert len(perimeters) == 1
    assert more_remaining is False


async def test_a_non_transient_failure_is_not_retried() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamHttpError):
            await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)
    assert len(attempts) == 1


async def test_several_throttle_responses_are_survived_before_success() -> None:
    # Regression for the 2026-08-10 crash: two retries ~3s apart was not enough. This drives the
    # busy response all the way to the last attempt the budget allows, and it still recovers.
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < wfigs.MAX_ATTEMPTS:
            return _json_response({"error": {"message": "Too many requests."}})
        return _json_response(_collection())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        perimeters, more_remaining = await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)

    assert len(attempts) == wfigs.MAX_ATTEMPTS
    assert len(perimeters) == 1
    assert more_remaining is False


async def test_a_sustained_throttle_still_fails_loudly_rather_than_retrying_forever() -> None:
    # The exact production failure mode: WFIGS answers "too many requests" on every single attempt.
    # A wider budget must still fail the run once that budget is spent -- never a silent empty
    # success and never a partial write.
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return _json_response({"error": {"message": "Too many requests."}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamPayloadError, match="Too many requests"):
            await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)

    assert len(attempts) == wfigs.MAX_ATTEMPTS


async def test_the_wall_clock_ceiling_stops_retrying_before_the_attempt_budget_is_spent() -> None:
    # A fake clock that jumps a whole ceiling's worth of time on every sleep proves the ceiling --
    # not just MAX_ATTEMPTS -- bounds the loop, independent of how many attempts remain.
    clock_seconds = 0.0

    async def jump_clock(_delay: float) -> None:
        nonlocal clock_seconds
        clock_seconds += wfigs.RETRY_WALL_CLOCK_CEILING_SECONDS

    def monotonic() -> float:
        return clock_seconds

    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return _json_response({"error": {"message": "Too many requests."}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamPayloadError, match="Too many requests"):
            await fetch_fire_perimeters(client, "-125,42,-111,49", jump_clock, monotonic)

    assert 1 <= len(attempts) < wfigs.MAX_ATTEMPTS


async def test_a_second_page_is_fetched_only_when_the_upstream_says_more_remain() -> None:
    requested_offsets: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_offsets.append(request.url.params.get("resultOffset"))
        collection = _collection(attr_UniqueFireIdentifier=f"2026-ID1AX-{len(requested_offsets):06d}")
        collection["properties"] = {"exceededTransferLimit": len(requested_offsets) == 1}
        return _json_response(collection)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        perimeters, more_remaining = await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)

    assert requested_offsets == ["0", "1"]
    assert len(perimeters) == 2
    assert more_remaining is False


async def test_the_ceiling_stops_paging_even_when_the_upstream_says_more_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the record ceiling well below one page so this test does not need a real 100-row page to
    # observe the stop-at-the-ceiling behaviour resolve_max_source_records() drives in production.
    monkeypatch.setattr(wfigs, "resolve_max_source_records", lambda: 1)
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        collection = _collection()
        collection["properties"] = {"exceededTransferLimit": True}
        return _json_response(collection)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        perimeters, more_remaining = await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)

    assert len(attempts) == 1
    assert len(perimeters) == 1
    assert more_remaining is True


async def test_an_unset_bbox_is_skipped_and_never_failed() -> None:
    result = await run_fire_perimeters_ingestion_job(RecordingWriter())
    assert result.source == WFIGS_SOURCE
    assert result.status == "skipped"
    assert result.reason == "INGEST_BBOX is not configured"


async def test_the_job_writes_the_perimeters_it_fetched() -> None:
    response = httpx.Response(
        200,
        content=json.dumps(_collection()).encode(),
        headers={"content-type": "application/json"},
    )
    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        result = await run_fire_perimeters_ingestion_job(writer, bbox="-125,42,-111,49", client=client)

    assert result.status == "ingested"
    assert result.records_seen == 1
    assert result.records_written == 1
    assert result.truncated is False
    assert [write.external_id for write in writer.writes] == [RECORDED_FIRE_IDENTIFIER]


async def test_the_job_reports_truncation_when_the_upstream_says_more_perimeters_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wfigs, "resolve_max_source_records", lambda: 1)

    def handler(_request: httpx.Request) -> httpx.Response:
        collection = _collection()
        collection["properties"] = {"exceededTransferLimit": True}
        return _json_response(collection)

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_fire_perimeters_ingestion_job(writer, bbox="-125,42,-111,49", client=client)

    assert result.status == "ingested"
    assert result.truncated is True


# --- The 2026-09-02 repair: a page the byte cap refuses, and a record no page can hold ---
#
# `postgres-fire-perimeters` entered retry backoff with `UpstreamPayloadError: upstream response
# exceeded the byte limit`. Nothing about that was transient: a 100-record page of large 2026
# perimeters is simply over `WFIGS_BOUNDS.max_bytes`, and every retry asked for the same page again.
# `is_retryable_failure` correctly declines to retry it, so the lane failed, retried at the JOB level,
# and burned its way to backoff -- once an hour, indefinitely.


def _oversized_response() -> httpx.Response:
    """A body whose declared `content-length` alone is over the cap, so it is refused before it is read."""
    return httpx.Response(
        200,
        content=json.dumps(_collection()).encode(),
        headers={"content-type": "application/json", "content-length": str(WFIGS_BOUNDS.max_bytes + 1)},
    )


def _record_count_of(request: httpx.Request) -> int:
    return int(request.url.params["resultRecordCount"])


async def test_a_page_over_the_byte_cap_is_halved_until_it_fits_rather_than_failing_the_lane() -> None:
    asked: list[tuple[str | None, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.params.get("resultOffset"), _record_count_of(request)))
        if _record_count_of(request) > 50:
            return _oversized_response()
        return _json_response(_collection())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        perimeters, outcome = await fetch_fire_perimeters_walk(client, "-125,42,-111,49", _no_sleep)

    assert asked == [("0", MAX_RECORD_COUNT), ("0", 50)]
    assert len(perimeters) == 1
    assert outcome.oversized == ()
    assert outcome.truncated is False


async def test_a_single_perimeter_over_the_cap_is_named_skipped_and_never_fatal() -> None:
    """The record that fits in no page: a governed source refusal carrying its object id and size."""
    asked: list[tuple[str | None, int, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("resultOffset")
        geometry = request.url.params.get("returnGeometry")
        asked.append((offset, _record_count_of(request), geometry))
        if geometry == "false":
            # The identity probe: attributes only, so the record that could not be transferred with
            # its geometry can still be NAMED. Nothing from this answer is ever written.
            return _json_response(_collection())
        if offset == "0":
            return _oversized_response()
        return _json_response(_collection(attr_UniqueFireIdentifier="2026-ID1AX-000619"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        perimeters, outcome = await fetch_fire_perimeters_walk(client, "-125,42,-111,49", _no_sleep)

    # 100 -> 50 -> 25 -> 12 -> 6 -> 3 -> 1 at offset 0, then the identity probe, then offset 1.
    ladder = [count for offset, count, geometry in asked if offset == "0" and geometry is None]
    assert ladder == [100, 50, 25, 12, 6, 3, 1]
    assert ("0", 1, "false") in asked

    assert len(outcome.oversized) == 1
    refused = outcome.oversized[0]
    assert refused.offset == 0
    assert refused.identity == RECORDED_FIRE_IDENTIFIER
    assert refused.declared_bytes == WFIGS_BOUNDS.max_bytes + 1
    # The lane kept going and collected the perimeter AFTER the one it could not have.
    assert [perimeter["uniqueFireIdentifier"] for perimeter in perimeters] == ["2026-ID1AX-000619"]


async def test_a_skipped_perimeter_makes_the_two_tuple_caller_report_truncation() -> None:
    """`fetch_fire_perimeters` answers a caller that compares perimeter SETS, and a skip changes the set.

    The flag means "are you seeing all of them". A governed oversized-record refusal is a real,
    named loss, so folding it into `truncated` is the same judgement
    `run_fire_perimeters_ingestion_job` already makes -- and without it a set-comparing caller reads
    a short set as complete and concludes the missing perimeters were retired upstream.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("returnGeometry") == "false":
            return _json_response(_collection())
        if request.url.params.get("resultOffset") == "0":
            return _oversized_response()
        return _json_response(_collection(attr_UniqueFireIdentifier="2026-ID1AX-000619"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        walk_records, outcome = await fetch_fire_perimeters_walk(client, "-125,42,-111,49", _no_sleep)
        records, more_remaining = await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)

    assert outcome.truncated is False, "the walk itself did not stop early; only a record was skipped"
    assert len(outcome.oversized) == 1
    assert more_remaining is True, "an oversized skip is a real loss and must not read as a complete set"
    assert [record["uniqueFireIdentifier"] for record in records] == [
        perimeter["uniqueFireIdentifier"] for perimeter in walk_records
    ]


async def test_a_skipped_perimeter_is_stated_in_the_run_outcome_rather_than_only_logged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("returnGeometry") == "false":
            return _json_response(_collection())
        if request.url.params.get("resultOffset") == "0":
            return _oversized_response()
        return _json_response(_collection(attr_UniqueFireIdentifier="2026-ID1AX-000619"))

    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_fire_perimeters_ingestion_job(writer, bbox="-125,42,-111,49", client=client)

    assert result.status == "ingested"
    assert result.records_written == 1
    assert result.details["oversized_records"] == 1
    # Truncated, because a named skip means the caller is NOT seeing everything -- the same answer
    # the record ceiling and the transfer limit already give, for a reason the reason line states.
    assert result.truncated is True
    assert result.reason is not None
    assert RECORDED_FIRE_IDENTIFIER in result.reason
    assert str(WFIGS_BOUNDS.max_bytes) in result.reason


async def test_the_two_tuple_contract_the_validation_pipeline_calls_is_unchanged() -> None:
    """`pipeline/validation/fire_perimeters.py` unpacks (perimeters, more_remaining) and is not owned here."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: _json_response(_collection()))) as client:
        perimeters, more_remaining = await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)

    assert len(perimeters) == 1
    assert more_remaining is False


def test_one_run_carries_a_total_transfer_budget_well_above_a_single_page() -> None:
    """Per-request bounds never bounded a RUN: 200 pages at the per-page cap is 3.2 GB."""
    assert WFIGS_BOUNDS.max_bytes < WFIGS_TOTAL_BYTE_BUDGET
    assert wfigs.MAX_PAGES * WFIGS_BOUNDS.max_bytes > WFIGS_TOTAL_BYTE_BUDGET
