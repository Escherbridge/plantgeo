"""The registered evacuation-zones watermark: a content comparison, and never a database session.

This lane's replacement clock was a DESIGN DECISION rather than a transcription -- Oregon OEM
publishes no column that dates a change -- so the decisions themselves are what is pinned here:
`watermark_for_capture`'s four answers, and above all the one trap the retired SQL file named in its
own header. `last_edited_date` is re-stamped on unchanged areas every few minutes; a clock built on
it re-snapshots the whole layer every tick, which is "exactly the behaviour being removed".

No network and no bucket: the fetch is monkeypatched and every store is an in-memory
`RecordingBackend`. DuckDB is real, because the digest covers repaired `geometry_wkb`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.direct.evacuation_zones import watermark as watermark_module
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
)
from agri_data_service.pipeline.direct.evacuation_zones.rows import (
    content_digest,
    evacuation_zones_table,
    split_into_parts,
)
from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource
from agri_data_service.pipeline.direct.evacuation_zones.watermark import (
    EvacuationZonesWatermarkError,
    read_evacuation_zones_source_watermark,
    read_published_snapshot,
    watermark_for_capture,
)
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.lane_contract import SourceWatermark

BBOX = "-125,42,-111,49"
FETCHED_AT = datetime(2026, 9, 6, 3, 30, tzinfo=UTC)
YESTERDAY_AT = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-123.0, 44.0], [-122.9, 44.0], [-122.9, 44.1], [-123.0, 44.1], [-123.0, 44.0]]],
}


def zone(global_id: str, *, level: int = 3, last_edited: str = "2026-09-05T17:29:00Z") -> dict[str, Any]:
    """One parsed Oregon OEM area, in the exact shape `parse_evacuation_zone_collection` produces."""
    return {
        "globalId": global_id,
        "evacuationAreaName": "Milepost 97",
        "fireName": "Milepost 97 Fire",
        "county": "Douglas",
        "hazardType": "Wildfire",
        "editorName": "OEM Sync",
        "evacuationLevel": level,
        "evacuationLevelLabel": {1: "Be Ready", 2: "Be Set", 3: "Go Now"}[level],
        "severity": {1: "moderate", 2: "high", 3: "critical"}[level],
        "structuresWithin": 12.0,
        "addressesWithin": 30.0,
        "populationWithin": 74.0,
        "createdAt": datetime(2025, 4, 14, tzinfo=UTC),
        "createdDate": "2025-04-14T00:00:00Z",
        "lastEditedDate": last_edited,
        "geometry": SQUARE,
    }


def capture(*zones: dict[str, Any], fetched_at: datetime = FETCHED_AT) -> EvacuationZonesSource:
    return EvacuationZonesSource(bbox=BBOX, fetched_at=fetched_at, zones=tuple(zones))


def publish(store: ObjectStore, *, day: date, captured: EvacuationZonesSource) -> None:
    """Write a complete part-plus-marker version, the only state `read_published_snapshot` counts."""
    table = evacuation_zones_table(captured, snapshot_day=day)
    parts = split_into_parts(table)
    for index, part in enumerate(parts):
        store.write_partition(
            part,
            layer=EVACUATION_ZONES_STREAM,
            kind=EVACUATION_ZONES_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
            part_index=index,
        )
    store.write_completion_marker(
        PartitionCompletion(
            part_count=len(parts),
            row_count=table.num_rows,
            completed_at=captured.fetched_at,
            run_id="evacuation-zones-watermark-test",
        ),
        layer=EVACUATION_ZONES_STREAM,
        kind=EVACUATION_ZONES_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )


def watermark_of(source: EvacuationZonesSource, store: ObjectStore) -> SourceWatermark:
    """Digest one capture and settle it against whatever `store` already holds."""
    table = evacuation_zones_table(source, snapshot_day=source.fetched_at.date())
    return watermark_for_capture(
        source, captured_digest=content_digest(table), published=read_published_snapshot(store)
    )


def test_a_restamped_last_edited_date_alone_reports_no_change() -> None:
    """DO NOT DELETE. THIS IS THE TRAP THE REPLACEMENT WATERMARK EXISTS TO AVOID.

    `sql/pipeline/lane_watermark_evacuation_zones.sql`'s own header named it: Oregon's sync re-stamps
    an unchanged area's edit clock every few minutes, so a watermark keyed to `last_edited_date`
    would promote a new version on essentially every poll and re-publish the whole layer forever.
    `rows.CONTENT_DIGEST_COLUMNS` cannot see that field at all -- it is not even stored -- so the
    same areas digest identically and the watermark stays STICKY at the published version's day.
    """
    store = ObjectStore(RecordingBackend())
    published_day = date(2026, 9, 5)
    publish(store, day=published_day, captured=capture(zone("{A}"), zone("{B}"), fetched_at=YESTERDAY_AT))

    restamped = capture(
        zone("{A}", last_edited="2026-09-06T03:29:00Z"),
        zone("{B}", last_edited="2026-09-06T03:29:30Z"),
    )
    watermark = watermark_of(restamped, store)

    assert watermark.day == published_day
    assert watermark.instant is None, "a sticky answer carries no instant; byte equality already settled it"
    assert "has NOT changed" in watermark.basis


def test_an_evacuation_level_moving_promotes_the_capture_instant_to_the_version_day() -> None:
    """The event `created_date` cannot see: the same areas, one of them escalated to Level 3.

    Oregon leaves `created_date` alone when a level is raised, which is why the retired watermark
    could not key to it either. The content digest sees it, and the version day becomes the UTC date
    of the capture that first saw it -- the same rule `forward.py` publishes under.
    """
    store = ObjectStore(RecordingBackend())
    publish(store, day=date(2026, 9, 5), captured=capture(zone("{A}", level=1), fetched_at=YESTERDAY_AT))

    escalated = capture(zone("{A}", level=3))
    watermark = watermark_of(escalated, store)

    assert watermark.day == FETCHED_AT.date()
    assert watermark.instant == FETCHED_AT
    assert "The set CHANGED" in watermark.basis


def test_a_retired_area_is_a_change_even_though_nothing_about_the_survivors_moved() -> None:
    """The accumulation the Postgres chain structurally could not retire, seen as a change.

    Oregon simply stops returning a closed area; no column anywhere is updated. Live 2026-09-06 the
    feed held 116 areas against 718 published rows for exactly this reason.
    """
    store = ObjectStore(RecordingBackend())
    publish(store, day=date(2026, 9, 5), captured=capture(zone("{A}"), zone("{B}"), fetched_at=YESTERDAY_AT))

    watermark = watermark_of(capture(zone("{A}")), store)

    assert watermark.day == FETCHED_AT.date()
    assert "The set CHANGED" in watermark.basis


def test_a_lane_with_nothing_published_owes_its_first_version_at_the_capture_day() -> None:
    watermark = watermark_of(capture(zone("{A}")), ObjectStore(RecordingBackend()))

    assert watermark.day == FETCHED_AT.date()
    assert "no version is published yet" in watermark.basis


def test_an_empty_capture_is_source_empty_and_never_a_version_day() -> None:
    """`day=None` is what `resolve_static_lane` reads as `source_empty`, and it is the honest answer.

    Answering with a DAY here would be worse than useless: for a static lookup a governed-absence
    marker at the watermark day is treated as a FAILED READ to retry, so a quiet Oregon would report
    permanently stale rather than "nothing to version".
    """
    store = ObjectStore(RecordingBackend())
    publish(store, day=date(2026, 9, 5), captured=capture(zone("{A}"), fetched_at=YESTERDAY_AT))

    watermark = watermark_for_capture(
        capture(), captured_digest="unused-when-empty", published=read_published_snapshot(store)
    )

    assert watermark.day is None
    assert watermark.instant is None
    assert "no population to version" in watermark.basis


@pytest.mark.asyncio
async def test_reading_the_watermark_captures_the_source_and_opens_no_database_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the function `lane_registry._evacuation_zones_watermark` actually calls.

    It takes an object STORE and no session, because currency here is decided by comparing this
    capture against the published version -- the three Postgres columns the retired query read are in
    tables this track drops.
    """
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    store = ObjectStore(RecordingBackend())
    published_day = date(2026, 9, 5)
    publish(store, day=published_day, captured=capture(zone("{A}"), fetched_at=YESTERDAY_AT))
    asked: list[dict[str, Any]] = []

    async def fake_fetch(bbox: str, **retries: float) -> EvacuationZonesSource:
        asked.append({"bbox": bbox, **retries})
        return capture(zone("{A}", last_edited="2026-09-06T03:29:00Z"))

    monkeypatch.setattr(watermark_module, "fetch_evacuation_zones_snapshot", fake_fetch)

    watermark = await read_evacuation_zones_source_watermark(store)

    assert [entry["bbox"] for entry in asked] == [BBOX]
    assert watermark.day == published_day
    assert "has NOT changed" in watermark.basis


@pytest.mark.asyncio
async def test_an_unconfigured_bbox_is_an_unread_watermark_not_an_empty_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a life-safety layer, "no active evacuation areas" is the one wrong answer to invent."""
    monkeypatch.delenv("INGEST_BBOX", raising=False)

    with pytest.raises(EvacuationZonesWatermarkError, match="unread watermark, never an empty source"):
        await read_evacuation_zones_source_watermark(ObjectStore(RecordingBackend()))
