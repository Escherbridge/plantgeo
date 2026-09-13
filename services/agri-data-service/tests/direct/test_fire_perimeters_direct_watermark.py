"""Reproducing the fire-perimeters source watermark from content, with no PostgreSQL to ask.

The digest tests are pure. The `read_direct_watermark` tests drive a real `ObjectStore` over the
shared in-memory `RecordingBackend`, writing versions through the real `write_partition` /
`write_completion_marker` so the listing, the marker and the read-back are the production ones. No
DuckDB is needed here: every population is built as row dicts directly, since geometry conversion is
`support.py`'s subject rather than this module's.

`geometry_wkb` is therefore a PLACEHOLDER (`b"\\x01wkb-<identifier>"`), and safe as one: the
watermark is a digest, so these bytes are only ever hashed, never parsed. That stops being true the
moment a row reaches `fill_one_lane_day`, whose z9/z5/z0 `GeometrySimplification` really does read
them -- see `test_fire_perimeters_direct_adapter.py::valid_square_wkb` for what such a test needs.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.fire_perimeters.products import FIRE_PERIMETERS_DIRECT_KIND
from agri_data_service.pipeline.direct.fire_perimeters.rows import FirePerimeterPopulation
from agri_data_service.pipeline.direct.fire_perimeters.watermark import (
    DIGESTED_COLUMNS,
    VERSION_STAMP_COLUMNS,
    PublishedLadder,
    completed_row_count,
    content_digest,
    read_direct_watermark,
    read_published_ladder,
    read_published_version,
    table_content_digest,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_SCHEMA, FIRE_PERIMETERS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

FETCHED_AT = datetime(2026, 9, 6, 14, 30, tzinfo=UTC)
EARLIER_FETCH = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
VERSION_DAY = date(2026, 9, 5)
EXPORT_INSTANT = datetime(2026, 9, 5, 9, 0, 30, tzinfo=UTC)


def _row(identifier: str, *, captured_at: datetime = FETCHED_AT, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "feature_id": f"direct:{identifier}",
        "unique_fire_identifier": identifier,
        "observed_day": date(2026, 8, 30),
        "incident_name": f"{identifier} Fire",
        "irwin_id": f"irwin-{identifier}",
        "fire_discovery_at": datetime(2026, 7, 16, 1, 7, tzinfo=UTC),
        "polygon_at": datetime(2026, 8, 30, 18, 45, tzinfo=UTC),
        "gis_acres": 1234.5,
        "fire_cause": "Natural",
        "incident_type_category": "WF",
        "poo_state": "US-OR",
        "percent_contained": 30.0,
        "severity": "moderate",
        "status": "published",
        "data_available_at": None,
        "updated_at": captured_at,
        "geometry_wkb": b"\x01wkb-" + identifier.encode(),
    }
    row.update(overrides)
    return row


def _population(*rows: dict[str, Any], fetched_at: datetime = FETCHED_AT) -> FirePerimeterPopulation:
    return FirePerimeterPopulation(rows=tuple(rows), fetched_at=fetched_at, rejected=0, collapsed=0)


def _publish(
    store: ObjectStore,
    backend: RecordingBackend,
    rows: list[dict[str, Any]],
    *,
    day: date = VERSION_DAY,
    export_instant: datetime | None = EXPORT_INSTANT,
) -> None:
    """Write one complete version the way the writer writes it: parts, then the completion marker."""
    table = pa.Table.from_pylist(
        [{**row, "snapshot_day": day} for row in rows], schema=FIRE_PERIMETERS_SCHEMA.arrow_schema
    )
    receipt = store.write_partition(
        table, layer=FIRE_PERIMETERS_STREAM, kind=FIRE_PERIMETERS_DIRECT_KIND, zoom=LANE_BASE_ZOOM_TIER, day=day
    )
    backend.set_last_modified(receipt.key, export_instant)
    store.write_completion_marker(
        PartitionCompletion(
            part_count=1, row_count=len(rows), completed_at=export_instant or FETCHED_AT, run_id="test-publish"
        ),
        layer=FIRE_PERIMETERS_STREAM,
        kind=FIRE_PERIMETERS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )


def _store() -> tuple[ObjectStore, RecordingBackend]:
    backend = RecordingBackend()
    return ObjectStore(backend), backend


def test_exactly_the_two_version_stamps_are_excluded_from_the_digest() -> None:
    """Digesting either would report 'changed' on every turn and restore the run clock this lane escaped."""
    assert VERSION_STAMP_COLUMNS == ("snapshot_day", "updated_at")
    assert set(DIGESTED_COLUMNS) == set(FIRE_PERIMETERS_SCHEMA.column_names) - set(VERSION_STAMP_COLUMNS)
    assert "geometry_wkb" in DIGESTED_COLUMNS
    assert "observed_day" in DIGESTED_COLUMNS


def test_the_digest_ignores_the_capture_instant_and_the_version_stamp() -> None:
    early = content_digest([_row("OR-A", captured_at=EARLIER_FETCH)])
    late = content_digest([_row("OR-A", captured_at=FETCHED_AT)])

    assert early == late


def test_the_digest_is_stable_under_row_reordering() -> None:
    """Sorting on the identifier is what stops part ordering from deciding whether the set changed."""
    forwards = content_digest([_row("OR-A"), _row("OR-B")])
    backwards = content_digest([_row("OR-B"), _row("OR-A")])

    assert forwards == backwards


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("percent_contained", 90.0),
        ("gis_acres", 9999.0),
        ("severity", "low"),
        ("incident_name", "Renamed Fire"),
        ("observed_day", date(2026, 9, 1)),
        ("geometry_wkb", b"\x01a-different-shape"),
    ],
)
def test_every_digested_column_moves_the_digest(column: str, value: Any) -> None:
    """One of these is the attribute clock, one is the geometry clock; a blind column is a frozen lane."""
    before = content_digest([_row("OR-A")])
    after = content_digest([_row("OR-A", **{column: value})])

    assert before != after


def test_a_new_incident_moves_the_digest_which_is_the_created_at_clock() -> None:
    before = content_digest([_row("OR-A")])
    after = content_digest([_row("OR-A"), _row("OR-B")])

    assert before != after


def test_field_boundaries_cannot_be_forged_by_a_value_that_looks_like_a_separator() -> None:
    """Length-prefixed and type-tagged, so two different populations can never hash the same."""
    left = content_digest([_row("OR-A", incident_name="ab", fire_cause="c")])
    right = content_digest([_row("OR-A", incident_name="a", fire_cause="bc")])

    assert left != right


def test_a_null_is_distinguishable_from_an_empty_string() -> None:
    assert content_digest([_row("OR-A", fire_cause=None)]) != content_digest([_row("OR-A", fire_cause="")])


def test_the_table_digest_matches_the_row_digest_for_the_same_population() -> None:
    """Both sides of every comparison must go through one encoding or the detector disagrees with itself."""
    rows = [_row("OR-A"), _row("OR-B")]
    table = pa.Table.from_pylist(
        [{**row, "snapshot_day": VERSION_DAY} for row in rows], schema=FIRE_PERIMETERS_SCHEMA.arrow_schema
    )

    assert table_content_digest(table) == content_digest(rows)


def test_an_empty_fetch_reports_no_day_which_resolves_as_source_empty() -> None:
    """A transient empty WFIGS answer must leave the previous version serving, never blank the map."""
    store, _ = _store()

    reading = read_direct_watermark(store, _population(), PublishedLadder(None, None, None, 0, ()))

    assert reading.watermark.day is None
    assert reading.watermark.instant is None
    assert "0 perimeters" in reading.watermark.basis


def test_with_nothing_published_the_fetch_instant_is_the_change_instant() -> None:
    store, _ = _store()

    reading = read_direct_watermark(store, _population(_row("OR-A")), PublishedLadder(None, None, None, 0, ()))

    assert reading.watermark.day == FETCHED_AT.date()
    assert reading.watermark.instant == FETCHED_AT
    assert reading.published is None


def test_an_identical_population_leaves_the_watermark_stuck_at_the_published_version() -> None:
    """The stickiness `max(feature.updated_at)` has when an hourly poll finds an incident unchanged."""
    store, backend = _store()
    rows = [_row("OR-A", captured_at=EARLIER_FETCH), _row("OR-B", captured_at=EARLIER_FETCH)]
    _publish(store, backend, rows)
    ladder = read_published_ladder(store)

    reading = read_direct_watermark(store, _population(_row("OR-A"), _row("OR-B")), ladder)

    assert reading.watermark.day == VERSION_DAY
    assert reading.watermark.instant == EARLIER_FETCH
    assert reading.short_circuited is False
    assert "has NOT changed" in reading.watermark.basis


def test_a_changed_attribute_promotes_the_fetch_instant_even_at_an_equal_row_count() -> None:
    """The read-back is the only thing that can see a containment revision; the count cannot."""
    store, backend = _store()
    _publish(store, backend, [_row("OR-A", captured_at=EARLIER_FETCH, percent_contained=30.0)])
    ladder = read_published_ladder(store)

    reading = read_direct_watermark(store, _population(_row("OR-A", percent_contained=85.0)), ladder)

    assert reading.watermark.day == FETCHED_AT.date()
    assert reading.watermark.instant == FETCHED_AT
    assert reading.short_circuited is False
    assert reading.published is not None


def test_a_different_row_count_is_settled_by_the_marker_without_reading_the_version_back() -> None:
    """One small marker GET instead of ~23 MB of parts, on the commonest change of all."""
    store, backend = _store()
    _publish(store, backend, [_row("OR-A", captured_at=EARLIER_FETCH)])
    ladder = read_published_ladder(store)

    reading = read_direct_watermark(store, _population(_row("OR-A"), _row("OR-B")), ladder)

    assert reading.short_circuited is True
    assert reading.published is None
    assert reading.watermark.instant == FETCHED_AT
    assert "never read back" in reading.watermark.basis


def test_a_capture_instant_later_than_its_own_export_is_discarded_as_clock_skew() -> None:
    """Believing it would re-publish an identical ~23 MB version every tick, forever, on a green report."""
    store, backend = _store()
    rows = [_row("OR-A", captured_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC))]
    _publish(store, backend, rows, export_instant=datetime(2026, 9, 5, 9, 0, tzinfo=UTC))
    ladder = read_published_ladder(store)

    reading = read_direct_watermark(store, _population(_row("OR-A")), ladder)

    assert reading.watermark.day == VERSION_DAY
    assert reading.watermark.instant is None
    assert "clock skew" in reading.watermark.basis


def test_the_ladder_ignores_a_version_that_never_asserted_completion() -> None:
    """A half-uploaded version would otherwise resolve the lane `current` on top of a partial snapshot."""
    store, backend = _store()
    table = pa.Table.from_pylist(
        [{**_row("OR-A"), "snapshot_day": VERSION_DAY}], schema=FIRE_PERIMETERS_SCHEMA.arrow_schema
    )
    receipt = store.write_partition(
        table,
        layer=FIRE_PERIMETERS_STREAM,
        kind=FIRE_PERIMETERS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=VERSION_DAY,
    )
    backend.set_last_modified(receipt.key, EXPORT_INSTANT)

    ladder = read_published_ladder(store)

    assert ladder.newest_data_day is None
    assert ladder.stranded_days == (VERSION_DAY,)
    assert ladder.version_count == 0


def test_the_ladder_reports_the_newest_complete_version_and_its_export_instant() -> None:
    store, backend = _store()
    _publish(store, backend, [_row("OR-A")], day=date(2026, 9, 1), export_instant=datetime(2026, 9, 1, tzinfo=UTC))
    _publish(store, backend, [_row("OR-A")], day=VERSION_DAY, export_instant=EXPORT_INSTANT)

    ladder = read_published_ladder(store)

    assert ladder.newest_data_day == VERSION_DAY
    assert ladder.newest_data_instant == EXPORT_INSTANT
    assert ladder.version_count == 2


def test_the_completion_marker_row_count_is_readable_and_none_when_absent() -> None:
    store, backend = _store()
    _publish(store, backend, [_row("OR-A"), _row("OR-B")])

    assert completed_row_count(store, VERSION_DAY) == 2
    assert completed_row_count(store, date(2026, 1, 1)) is None


def test_a_published_version_reads_back_with_its_capture_instant_and_digest() -> None:
    store, backend = _store()
    rows = [_row("OR-A", captured_at=EARLIER_FETCH)]
    _publish(store, backend, rows)

    published = read_published_version(store, VERSION_DAY)

    assert published.day == VERSION_DAY
    assert published.row_count == 1
    assert published.captured_at == EARLIER_FETCH
    assert published.digest == content_digest(rows)
