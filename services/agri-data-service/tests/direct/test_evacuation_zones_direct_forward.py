"""The forward turn's decisions: which version is owed, which is not, and what refuses to be guessed.

The whole point of this lane's direct writer is that currency is decided by CONTENT rather than by
the retired Postgres watermark, so most of what matters here is `read_published_snapshot` and the
digest comparison built on it. Every store is an in-memory `RecordingBackend`: no bucket, no
database, no upstream.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path
from agri_data_service.pipeline.direct.evacuation_zones.forward import (
    EVACUATION_ZONES_MAX_DAYS,
    EvacuationZonesForwardConfig,
    EvacuationZonesForwardConfigError,
    _captured_watermark,
    _unchanged_row_stamps,
    _validate_config,
    parse_args,
    parser,
    read_published_snapshot,
)
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
)
from agri_data_service.pipeline.direct.evacuation_zones.rows import (
    content_digest,
    evacuation_zones_table,
    natural_key_for,
    split_into_parts,
)
from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource
from agri_data_service.pipeline.direct.evacuation_zones.watermark import EvacuationZonesWatermarkError
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.parquet.test_objectstore_writer import RecordingBackend

RUN_ID = "evacuation-zones-forward-test"
FETCHED_AT = datetime(2026, 9, 3, 17, 30, tzinfo=UTC)
DAY = date(2026, 9, 3)

SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-123.0, 44.0], [-122.9, 44.0], [-122.9, 44.1], [-123.0, 44.1], [-123.0, 44.0]]],
}


def zone(global_id: str, *, level: int = 3) -> dict[str, Any]:
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
        "lastEditedDate": "2026-09-03T17:29:00Z",
        "geometry": SQUARE,
    }


def source(*zones: dict[str, Any], fetched_at: datetime = FETCHED_AT) -> EvacuationZonesSource:
    return EvacuationZonesSource(bbox="-125,42,-111,49", fetched_at=fetched_at, zones=tuple(zones))


def publish_version(store: ObjectStore, *, day: date, captured: EvacuationZonesSource) -> None:
    """Write a complete part-plus-marker version, the only state the forward turn counts as published."""
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
            part_count=len(parts), row_count=table.num_rows, completed_at=captured.fetched_at, run_id=RUN_ID
        ),
        layer=EVACUATION_ZONES_STREAM,
        kind=EVACUATION_ZONES_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )


def publish_absence(store: ObjectStore, *, day: date) -> None:
    store.write_absence(
        GovernedAbsence(
            reason="Oregon OEM published no evacuation areas within this run's bounds",
            upstream_response="{}",
            recorded_at=datetime(2026, 9, 2, tzinfo=UTC),
            run_id=RUN_ID,
        ),
        layer=EVACUATION_ZONES_STREAM,
        kind=EVACUATION_ZONES_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )


def test_a_lane_that_has_never_published_reports_no_version() -> None:
    assert read_published_snapshot(ObjectStore(RecordingBackend())) is None


def test_an_unchanged_source_digests_equal_to_the_published_version_so_nothing_is_owed() -> None:
    """DO NOT DELETE. This is what replaces the Postgres watermark's whole job.

    `lane_watermark_evacuation_zones.sql` existed to stop the lane re-snapshotting on every tick.
    The digest comparison is what stops it now, and it does so without reading a single Postgres
    column -- all three the watermark read are being deleted.
    """
    store = ObjectStore(RecordingBackend())
    published_capture = source(zone("{A}"), zone("{B}"), fetched_at=datetime(2026, 9, 2, 9, tzinfo=UTC))
    publish_version(store, day=date(2026, 9, 2), captured=published_capture)

    published = read_published_snapshot(store)
    captured_again = evacuation_zones_table(source(zone("{A}"), zone("{B}")), snapshot_day=DAY)

    assert published is not None
    assert published.day == date(2026, 9, 2)
    assert published.digest == content_digest(captured_again)


def test_an_evacuation_level_moving_makes_the_published_version_stale_within_the_same_hour() -> None:
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=DAY, captured=source(zone("{A}", level=1)))

    published = read_published_snapshot(store)
    escalated = evacuation_zones_table(source(zone("{A}", level=3)), snapshot_day=DAY)

    assert published is not None
    assert published.digest != content_digest(escalated)


def test_a_published_governed_absence_is_read_as_the_empty_set_not_as_nothing_published() -> None:
    """A quiet spell is an ANSWER. The next quiet capture must digest equal to it and write nothing."""
    store = ObjectStore(RecordingBackend())
    publish_absence(store, day=DAY)

    published = read_published_snapshot(store)
    quiet_again = evacuation_zones_table(source(), snapshot_day=date(2026, 9, 4))

    assert published is not None
    assert published.absent is True
    assert published.digest == content_digest(quiet_again)


def test_a_half_written_version_never_answers_as_the_published_one() -> None:
    """Parts with no completion marker are a killed upload, not a version. Trusting one would compare
    this capture against a snapshot that was never finished and could silently decide nothing is owed."""
    store = ObjectStore(RecordingBackend())
    table = evacuation_zones_table(source(zone("{A}")), snapshot_day=DAY)
    store.write_partition(
        table,
        layer=EVACUATION_ZONES_STREAM,
        kind=EVACUATION_ZONES_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=DAY,
    )

    assert read_published_snapshot(store) is None


def test_a_version_carrying_both_data_and_an_absence_marker_is_refused_never_resolved() -> None:
    """Silently preferring one of two contradictory claims is what `planes` returns `conflicted`
    rather than do. A writer may not be laxer than the reader it feeds."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    publish_version(store, day=DAY, captured=source(zone("{A}")))
    # Placed straight onto the backend: `write_absence` refuses a day that already holds data, which
    # is exactly the guard that makes this state an anomaly rather than a routine one -- but an
    # interrupted admin retraction can still leave it, and the reader must not resolve it silently.
    backend.put(
        store.key_for(
            absence_marker_path(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
        ),
        b"{}",
        content_type="application/json",
    )

    # `EvacuationZonesWatermarkError` since 2026-09-06, not `DirectEvacuationZonesError`: this read
    # moved into `watermark.py` so the registered resolver and this writer share one implementation,
    # and that module may not import `adapter.py` (which imports the registry) without closing a cycle.
    with pytest.raises(EvacuationZonesWatermarkError, match="both a data partition and a governed-absence"):
        read_published_snapshot(store)


def test_the_newest_version_answers_even_when_an_older_one_is_still_present() -> None:
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=date(2026, 9, 1), captured=source(zone("{OLD}")))
    publish_version(store, day=date(2026, 9, 3), captured=source(zone("{NEW}")))

    published = read_published_snapshot(store)

    assert published is not None
    assert published.day == date(2026, 9, 3)
    assert set(published.row_digests) == {natural_key_for("{NEW}")}


def test_only_rows_whose_source_content_held_still_carry_their_old_change_stamp_forward() -> None:
    store = ObjectStore(RecordingBackend())
    yesterday = datetime(2026, 9, 2, 9, tzinfo=UTC)
    publish_version(store, day=date(2026, 9, 2), captured=source(zone("{A}"), zone("{B}"), fetched_at=yesterday))
    published = read_published_snapshot(store)
    captured = evacuation_zones_table(source(zone("{A}"), zone("{B}", level=1)), snapshot_day=DAY)

    carried = _unchanged_row_stamps(captured, published)

    assert carried == {natural_key_for("{A}"): yesterday}


@pytest.mark.asyncio
async def test_the_static_lane_bracket_sees_one_capture_and_therefore_one_instant() -> None:
    """`_fill_static_day` reads the watermark before AND after the export and treats two equal
    instants as proof the source held still. This resolver answers with the capture being published,
    so the bracket resolves on the first attempt -- deliberately inert, because the race it closes
    is unreachable for a writer whose next turn compares content rather than instants."""
    captured = source(zone("{A}"))
    resolve = _captured_watermark(captured, DAY)

    before = await resolve(None, None, today=DAY)
    after = await resolve(None, None, today=DAY)

    assert before.instant == after.instant == FETCHED_AT
    assert before.day == DAY
    assert "content comparison" in before.basis


@pytest.mark.parametrize("max_days", [0, EVACUATION_ZONES_MAX_DAYS + 1, 7])
def test_asking_for_more_than_one_version_a_turn_is_refused(max_days: int) -> None:
    """A static lookup has exactly one current state, and Oregon publishes no archive a second
    version could come from -- so a backlog is unrepresentable, not merely absent."""
    with pytest.raises(EvacuationZonesForwardConfigError, match="static_lookup"):
        _validate_config(_config(max_days=max_days))


def test_retry_max_below_retry_base_is_refused() -> None:
    with pytest.raises(EvacuationZonesForwardConfigError):
        _validate_config(_config(retry_base_seconds=30.0, retry_max_seconds=5.0))


def test_a_non_finite_time_budget_is_refused() -> None:
    with pytest.raises(EvacuationZonesForwardConfigError):
        _validate_config(_config(time_budget_seconds=float("inf")))


def test_the_parser_has_no_product_flag_because_this_lane_has_exactly_one() -> None:
    flags = {action.option_strings[0] for action in parser()._actions if action.option_strings}

    assert "--product" not in flags
    assert {"--max-days", "--bbox", "--run-id"} <= flags


def test_default_args_parse_to_a_single_bounded_version() -> None:
    config = parse_args([])

    assert config.max_days == 1
    assert config.bbox is None
    assert config.state == "oregon"


def test_a_bbox_whose_first_ordinate_is_negative_survives_two_argv_tokens() -> None:
    """THE TWO ARGV TOKENS ARE THE TEST, and every western-US bbox starts with a negative longitude.

    `--bbox` and its value arrive separately, exactly as a shell hands them over, and the value's
    leading `-125` is not a pure negative number -- so argparse reads it as another option and
    raises "argument --bbox: expected one argument" unless `parse_args` rewrites it to
    `--bbox=-125,...` first. Passing one pre-joined `--bbox=...` token here would exercise the CLI
    without exercising the guard, which is how four sibling writers shipped the same crash.
    """
    config = parse_args(["--bbox", "-125,42,-111,49"])

    assert config.bbox == "-125,42,-111,49"


def _config(**overrides: Any) -> EvacuationZonesForwardConfig:
    defaults: dict[str, Any] = {
        "max_days": 1,
        "time_budget_seconds": 300.0,
        "retry_attempts": 5,
        "retry_base_seconds": 5.0,
        "retry_max_seconds": 60.0,
        "contention_timeout_seconds": 300.0,
    }
    return EvacuationZonesForwardConfig(**{**defaults, **overrides})
