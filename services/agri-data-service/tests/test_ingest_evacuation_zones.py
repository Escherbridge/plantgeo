"""Oregon OEM evacuation-zone ingestion: GlobalID identity, the created-date observed_at rule, and honest skips."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest import evacuation_zones
from agri_data_service.ingest.evacuation_zones import (
    EVACUATION_ZONES_CHANNEL,
    EVACUATION_ZONES_PRODUCER,
    EVACUATION_ZONES_SOURCE,
    MAX_RECORD_COUNT,
    UNEXPECTED_SHAPE_REASON,
    build_evacuation_zone_identity,
    build_evacuation_zone_write,
    build_query_url,
    epoch_milliseconds_to_datetime,
    evacuation_level,
    evacuation_level_label,
    evacuation_severity,
    fetch_evacuation_zones,
    is_retryable_failure,
    parse_evacuation_zone_collection,
    run_evacuation_zones_ingestion_job,
)
from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError
from agri_data_service.ingest.identity import MissingNativeKeyError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

RECORDED_GLOBAL_ID = "47569dae-8535-405b-930e-51bc28167c97"
LAST_EDITED_EPOCH_MILLISECONDS = 1_785_848_400_000  # 2026-08-04T13:00:00.000Z
CREATED_EPOCH_MILLISECONDS = 1_785_142_800_000  # 2026-07-27T09:00:00.000Z
SQUARE_RING = [[-123.0, 44.0], [-123.0, 44.1], [-122.9, 44.1], [-122.9, 44.0], [-123.0, 44.0]]


class RecordingWriter:
    """A feature writer that records what a job handed it, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes = list(writes)
        return len(self.writes)


async def _no_sleep(_delay: float) -> None:
    """Skip the backoff wait so a retry test runs at full speed."""


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS", "EVACUATION_ZONES_LAYER_ID"):
        monkeypatch.delenv(variable, raising=False)


def _zone(**overrides: object) -> dict[str, object]:
    zone: dict[str, object] = {
        "globalId": RECORDED_GLOBAL_ID,
        "evacuationAreaName": "Test Area",
        "fireName": "Test Fire",
        "county": "Marion",
        "hazardType": "Fire",
        "editorName": "oem-sync",
        "evacuationLevel": 3,
        "evacuationLevelLabel": "Go Now",
        "severity": "critical",
        "structuresWithin": 12.0,
        "addressesWithin": 15.0,
        "populationWithin": 30.0,
        "createdAt": datetime(2026, 7, 27, 9, 0, tzinfo=UTC),
        "createdDate": "2026-07-27T09:00:00.000Z",
        "lastEditedDate": "2026-08-04T13:00:00.000Z",
        "geometry": {"type": "Polygon", "coordinates": [SQUARE_RING]},
    }
    zone.update(overrides)
    return zone


def _collection(**property_overrides: object) -> dict[str, object]:
    properties: dict[str, object] = {
        "GlobalID": RECORDED_GLOBAL_ID,
        "Fire_Name": "Test Fire",
        "Fire_Evacuation_Level": 3,
        "created_date": CREATED_EPOCH_MILLISECONDS,
        "last_edited_date": LAST_EDITED_EPOCH_MILLISECONDS,
        "County": "Marion",
        "Evac_Area_Name": "Test Area",
        "StructuresWithin": 12.0,
        "AddressesWithin": 15.0,
        "PopulationWithin": 30.0,
        "HazardType": "Fire",
        "Editor_Name": "oem-sync",
    }
    properties.update(property_overrides)
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [SQUARE_RING]}, "properties": properties}
        ],
    }


def test_the_identity_is_stable_across_two_runs_over_the_same_payload() -> None:
    zone = _zone()
    first = build_evacuation_zone_identity(zone)
    second = build_evacuation_zone_identity(zone)
    assert first.natural_key == second.natural_key
    assert first.natural_key == f"{EVACUATION_ZONES_PRODUCER}:{RECORDED_GLOBAL_ID}"


def test_observed_at_comes_from_the_upstream_creation_stamp_never_the_run_clock() -> None:
    # build_evacuation_zone_identity takes no "now" parameter; the only timestamp source is the zone itself.
    identity = build_evacuation_zone_identity(_zone(createdAt=datetime(2025, 3, 21, 17, 4, tzinfo=UTC)))
    assert identity.observed_at == datetime(2025, 3, 21, 17, 4, tzinfo=UTC)


def test_the_edit_clock_never_dates_a_zone_however_recently_it_was_re_stamped() -> None:
    # Oregon's sync re-stamps an unchanged area's `last_edited_date` every few minutes, so a live sample
    # finds most of the layer "edited" within the hour. Dating by it would put version_valid_from minutes
    # before the cron ran for areas that are months old -- a clock reading laundered through the payload.
    zone = _zone(
        createdAt=datetime(2025, 3, 21, 17, 4, tzinfo=UTC),
        lastEditedDate="2026-08-04T14:15:05.000Z",
    )
    assert build_evacuation_zone_identity(zone).observed_at == datetime(2025, 3, 21, 17, 4, tzinfo=UTC)


def test_a_zone_with_no_creation_stamp_dates_as_unknown_rather_than_now() -> None:
    identity = build_evacuation_zone_identity(_zone(createdAt=None))
    assert identity.observed_at is None


def test_a_zone_missing_its_global_id_raises_rather_than_being_synthesised() -> None:
    with pytest.raises(MissingNativeKeyError, match="globalId"):
        build_evacuation_zone_identity(_zone(globalId=None))
    with pytest.raises(MissingNativeKeyError, match="globalId"):
        build_evacuation_zone_identity(_zone(globalId="  "))


def test_a_non_datetime_creation_stamp_is_refused() -> None:
    with pytest.raises(ValueError, match="createdAt"):
        build_evacuation_zone_identity(_zone(createdAt="2026-07-27T09:00:00.000Z"))


def test_a_feature_with_a_blank_global_id_is_refused_at_parse_time() -> None:
    with pytest.raises(UpstreamPayloadError, match=UNEXPECTED_SHAPE_REASON):
        parse_evacuation_zone_collection(_collection(GlobalID=""))


@pytest.mark.parametrize(
    ("level", "label", "grade"), [(1, "Be Ready", "moderate"), (2, "Be Set", "high"), (3, "Go Now", "critical")]
)
def test_evacuation_levels_grade_across_oregons_published_scale(level: int, label: str, grade: str) -> None:
    assert evacuation_level(level) == level
    assert evacuation_level_label(level) == label
    assert evacuation_severity(level) == grade


@pytest.mark.parametrize("value", [None, 0, 4, "3", True, float("nan")])
def test_an_off_scale_or_unreal_evacuation_level_is_unknown_rather_than_guessed(value: object) -> None:
    assert evacuation_level(value) is None


def test_epoch_milliseconds_reads_as_a_utc_instant_and_refuses_the_unreal() -> None:
    assert epoch_milliseconds_to_datetime(LAST_EDITED_EPOCH_MILLISECONDS) == datetime(2026, 8, 4, 13, 0, tzinfo=UTC)
    assert epoch_milliseconds_to_datetime(None) is None
    assert epoch_milliseconds_to_datetime("2026-08-04") is None
    assert epoch_milliseconds_to_datetime(True) is None
    assert epoch_milliseconds_to_datetime(9e15) is None


def test_a_recorded_production_zone_parses_and_keys_to_the_bare_global_id() -> None:
    parsed = parse_evacuation_zone_collection(_collection())
    zone = parsed.zones[0]
    assert zone["globalId"] == RECORDED_GLOBAL_ID
    assert zone["evacuationLevel"] == 3
    assert zone["severity"] == "critical"
    assert zone["createdAt"] == datetime(2026, 7, 27, 9, 0, tzinfo=UTC)

    write = build_evacuation_zone_write(zone, "evacuation-zones")
    assert write is not None
    assert write.natural_key == f"{EVACUATION_ZONES_PRODUCER}:{RECORDED_GLOBAL_ID}"
    assert write.channel == EVACUATION_ZONES_CHANNEL
    assert write.properties["globalId"] == RECORDED_GLOBAL_ID
    assert write.properties["createdDate"] == "2026-07-27T09:00:00.000Z"
    # The upstream sync re-stamps an unchanged area's edit clock every few minutes; storing it would
    # mark every poll a "changed" refresh for the whole layer, and dating by it would be a clock
    # reading laundered through the payload. It is parsed, and then neither stored nor used to date.
    assert "lastEditedDate" not in write.properties


def test_an_arcgis_error_payload_answered_with_http_200_is_still_a_failure() -> None:
    with pytest.raises(UpstreamPayloadError, match="Oregon OEM evacuation API error"):
        parse_evacuation_zone_collection({"error": {"code": 429, "message": "Too many requests"}})


def test_an_unexpected_collection_shape_is_refused() -> None:
    with pytest.raises(UpstreamPayloadError, match=UNEXPECTED_SHAPE_REASON):
        parse_evacuation_zone_collection({"type": "FeatureCollection", "features": [{"type": "Feature"}]})


def test_the_query_url_asks_for_a_bounded_geojson_page() -> None:
    url = build_query_url("-125,42,-111,49")
    assert "f=geojson" in url
    assert f"resultRecordCount={MAX_RECORD_COUNT}" in url
    assert "resultOffset=0" in url
    assert "spatialRel=esriSpatialRelIntersects" in url
    assert "resultOffset=1000" in build_query_url("-125,42,-111,49", offset=1_000)


def test_only_a_busy_or_transient_upstream_is_retried() -> None:
    assert is_retryable_failure(UpstreamHttpError(429))
    assert is_retryable_failure(UpstreamHttpError(503))
    assert is_retryable_failure(UpstreamPayloadError("Oregon OEM evacuation API error: service is busy"))
    assert not is_retryable_failure(UpstreamHttpError(400))
    assert not is_retryable_failure(UpstreamPayloadError(UNEXPECTED_SHAPE_REASON))


async def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200, content=json.dumps(_collection()).encode(), headers={"content-type": "application/json"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        zones, more_remaining = await fetch_evacuation_zones(client, "-125,42,-111,49", _no_sleep)
    assert len(attempts) == 2
    assert len(zones) == 1
    assert more_remaining is False


async def test_the_ceiling_stops_paging_even_when_the_upstream_says_more_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the record ceiling well below one page so this test does not need 1000 fake rows to observe
    # the stop-at-the-ceiling behaviour resolve_max_source_records() drives in production.
    monkeypatch.setattr(evacuation_zones, "resolve_max_source_records", lambda: 1)
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        body = _collection()
        body["properties"] = {"exceededTransferLimit": True}
        return httpx.Response(200, content=json.dumps(body).encode(), headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        zones, more_remaining = await fetch_evacuation_zones(client, "-125,42,-111,49")

    assert len(attempts) == 1
    assert len(zones) == 1
    assert more_remaining is True


async def test_an_unset_bbox_is_skipped_and_never_failed() -> None:
    result = await run_evacuation_zones_ingestion_job(RecordingWriter())
    assert result.source == EVACUATION_ZONES_SOURCE
    assert result.status == "skipped"
    assert result.reason == "INGEST_BBOX is not configured"


async def test_the_job_writes_the_zones_it_fetched_and_reports_nothing_rejected() -> None:
    response = httpx.Response(
        200, content=json.dumps(_collection()).encode(), headers={"content-type": "application/json"}
    )
    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        result = await run_evacuation_zones_ingestion_job(writer, bbox="-125,42,-111,49", client=client)

    assert result.status == "ingested"
    assert result.records_seen == 1
    assert result.records_written == 1
    assert result.truncated is False
    assert result.details["rejected"] == 0
    assert [write.external_id for write in writer.writes] == [RECORDED_GLOBAL_ID]


async def test_the_job_respects_the_record_ceiling_and_reports_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evacuation_zones, "resolve_max_source_records", lambda: 1)
    two_zone_collection = _collection()
    second_feature = json.loads(json.dumps(two_zone_collection["features"][0]))
    second_feature["properties"]["GlobalID"] = "b2c3d4e5-1111-2222-3333-444455556666"
    two_zone_collection["features"].append(second_feature)
    response = httpx.Response(
        200, content=json.dumps(two_zone_collection).encode(), headers={"content-type": "application/json"}
    )
    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        result = await run_evacuation_zones_ingestion_job(writer, bbox="-125,42,-111,49", client=client)

    assert result.records_seen == 2
    assert result.records_written == 1
    assert result.truncated is True
    assert len(writer.writes) == 1
