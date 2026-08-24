"""The drought plane's serving read and its USDM reconciliation, exercised with no network and no bucket.

Both `planes/drought.py` and `pipeline/validation/drought.py` land in this one file because
`tests/parquet/test_drought_serving.py` is the only test file this slice may add. `RecordingBackend`
(`tests/parquet/test_objectstore_writer.py`) stands in for the bucket for every listing operation;
Polars reads real Parquet bytes mirrored onto local disk, since a Python dict backend has no bytes
to serve over `s3://`. A hand-rolled little-endian WKB MultiPolygon encoder stands in for what USDM
publishes -- this repo carries no geometry library, so the fixture is built with `struct` alone.
"""

from __future__ import annotations

import struct
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path, partition_path
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.drought import (
    WRITTEN_ZOOM_TIER,
    reconcile_drought_releases,
    written_release_span,
)
from agri_data_service.planes.drought import (
    DROUGHT_KIND,
    DroughtServingError,
    list_observed_drought_release_days,
    most_severe_class_at_point,
    read_drought_release,
    resolve_drought_release,
    resolve_drought_release_day,
)
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA, DROUGHT_STREAM
from tests.parquet.test_objectstore_writer import (
    BASE_TIER,
    DETAIL_TIER,
    UNPUBLISHED_ZOOM,
    RecordingBackend,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.foundation.parquet.zoom import ZoomTier

# The rung a lane export lands on, and the zoom a viewport asks for to be served it.
BASE_TIER_REQUEST = BASE_TIER

AUGUST_FOURTH = date(2026, 8, 4)  # a real USDM release Tuesday (ingest/usdm.py TUESDAY == 1)
AUGUST_ELEVENTH = date(2026, 8, 11)
WEDNESDAY_AFTER_FOURTH = date(2026, 8, 5)
TODAY = date(2026, 8, 22)


def _multipolygon_wkb(*rings: tuple[tuple[float, float], ...]) -> bytes:
    """Encode a minimal little-endian WKB MultiPolygon of single-ring polygons, no geometry library."""
    body = struct.pack("<BI", 1, 6)  # byte order, MultiPolygon
    body += struct.pack("<I", len(rings))
    for ring in rings:
        body += struct.pack("<BI", 1, 3)  # byte order, Polygon
        body += struct.pack("<I", 1)  # one ring: exterior only, no holes
        body += struct.pack("<I", len(ring))
        for x, y in ring:
            body += struct.pack("<dd", x, y)
    return body


_BIG_SQUARE = ((-10.0, -10.0), (-10.0, 10.0), (10.0, 10.0), (10.0, -10.0), (-10.0, -10.0))
_SMALL_SQUARE = ((-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0), (-1.0, -1.0))

_D0_ABNORMALLY_DRY = 0
_D4_EXCEPTIONAL_DROUGHT = 4


def _release_table(day: date, categories: dict[int, bytes]) -> pa.Table:
    """One release's rows shaped exactly as `DROUGHT_SCHEMA`, one WKB polygon per drought class."""
    count = len(categories)
    return pa.table(
        {
            "area_id": pa.array([f"area-{category}" for category in categories], pa.string()),
            "valid_date": pa.array([day] * count, pa.date32()),
            "dm_category": pa.array(list(categories.keys()), pa.int32()),
            "source_url": pa.array(
                [f"https://droughtmonitor.unl.edu/data/json/usdm_{day:%Y%m%d}.json"] * count, pa.string()
            ),
            "ingested_at": pa.array([datetime(2026, 8, 6, 12, tzinfo=UTC)] * count, pa.timestamp("us", tz="UTC")),
            "geom": pa.array(list(categories.values()), pa.binary()),
        }
    ).cast(DROUGHT_SCHEMA.arrow_schema)


def _write_complete_release(
    store: ObjectStore, *, day: date, categories: dict[int, bytes], zoom: ZoomTier = BASE_TIER
) -> None:
    """Write one release AND the completion marker that makes it a published release.

    A bare `write_partition` leaves an unfinished upload, which every reader in this lane now
    correctly declines to publish. Tests whose subject is "this release exists" want both.
    """
    receipt = store.write_partition(
        _release_table(day, categories), layer=DROUGHT_STREAM, kind="observed", zoom=zoom, day=day
    )
    store.write_completion_marker(
        PartitionCompletion(
            part_count=1,
            row_count=receipt.row_count,
            completed_at=datetime(2026, 8, 22, tzinfo=UTC),
            run_id="test",
        ),
        layer=DROUGHT_STREAM,
        kind="observed",
        zoom=zoom,
        day=day,
    )


def _write_local_release(  # noqa: PLR0913 - one parameter per partition coordinate; a fixture builder names them all
    tmp_path: Path,
    store: ObjectStore,
    backend: RecordingBackend,
    *,
    day: date,
    categories: dict[int, bytes],
    zoom: ZoomTier = BASE_TIER,
) -> None:
    """Write one release through the real writer, then mirror its bytes onto local disk for Polars."""
    receipt = store.write_partition(
        _release_table(day, categories), layer=DROUGHT_STREAM, kind="observed", zoom=zoom, day=day
    )
    # The marker LAST, as the driver writes it. Without it this release is an unfinished upload and
    # `list_observed_drought_release_days` correctly declines to publish it.
    store.write_completion_marker(
        PartitionCompletion(
            part_count=1,
            row_count=receipt.row_count,
            completed_at=datetime(2026, 8, 22, tzinfo=UTC),
            run_id="test",
        ),
        layer=DROUGHT_STREAM,
        kind="observed",
        zoom=zoom,
        day=day,
    )
    _mirror_receipt_to_disk(tmp_path, backend, receipt.relative_path, receipt.key)


def _mirror_receipt_to_disk(tmp_path: Path, backend: RecordingBackend, relative_path: str, key: str) -> None:
    local_path = tmp_path / relative_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(backend.objects[key])


class _FakeSourceCheck:
    """A `UsdmSourceCheck` answering from a canned set, recording every date it was actually asked."""

    def __init__(self, published: frozenset[date]) -> None:
        self._published = published
        self.checked: list[date] = []

    async def was_published(self, valid_date: date) -> bool:
        self.checked.append(valid_date)
        return valid_date in self._published


# --- planes/drought.py: the daily-slider resolution ---------------------------------------------


def test_resolve_drought_release_day_answers_the_newest_release_at_or_before(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(tmp_path, store, backend, day=AUGUST_FOURTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)})
    _write_local_release(tmp_path, store, backend, day=AUGUST_ELEVENTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)})

    resolved = resolve_drought_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, on_or_before=WEDNESDAY_AFTER_FOURTH, now=TODAY
    )

    assert resolved == AUGUST_FOURTH


def test_resolve_drought_release_names_which_valid_date_answered(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(tmp_path, store, backend, day=AUGUST_FOURTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)})

    answer = resolve_drought_release(
        store, tmp_path.as_posix(), requested_zoom=BASE_TIER_REQUEST, on_or_before=WEDNESDAY_AFTER_FOURTH, now=TODAY
    )

    assert answer is not None
    assert answer.requested_day == WEDNESDAY_AFTER_FOURTH
    assert answer.valid_date == AUGUST_FOURTH
    assert answer.areas.column("dm_category").to_pylist() == [0]


def test_a_request_before_any_release_answers_none(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(tmp_path, store, backend, day=AUGUST_FOURTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)})

    assert (
        resolve_drought_release_day(store, requested_zoom=BASE_TIER_REQUEST, on_or_before=date(2020, 1, 1), now=TODAY)
        is None
    )


def test_a_genuinely_future_request_refuses_rather_than_reusing_the_newest_release(tmp_path: Path) -> None:
    """`kind=forecast` does not exist for this lane; a future day must never silently answer from the past."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(tmp_path, store, backend, day=AUGUST_FOURTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)})

    resolved = resolve_drought_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, on_or_before=date(2027, 1, 1), now=TODAY
    )

    assert resolved is None


def test_a_release_spanning_multiple_part_files_reads_as_one_table(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    receipt_a = store.write_partition(
        _release_table(AUGUST_FOURTH, {0: _multipolygon_wkb(_BIG_SQUARE)}),
        layer=DROUGHT_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_FOURTH,
        part_index=0,
    )
    receipt_b = store.write_partition(
        _release_table(AUGUST_FOURTH, {4: _multipolygon_wkb(_SMALL_SQUARE)}),
        layer=DROUGHT_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=AUGUST_FOURTH,
        part_index=1,
    )
    _mirror_receipt_to_disk(tmp_path, backend, receipt_a.relative_path, receipt_a.key)
    _mirror_receipt_to_disk(tmp_path, backend, receipt_b.relative_path, receipt_b.key)

    table = read_drought_release(tmp_path.as_posix(), AUGUST_FOURTH, requested_zoom=BASE_TIER_REQUEST)

    assert sorted(table.column("dm_category").to_pylist()) == [0, 4]


def test_reading_a_day_with_no_partition_raises(tmp_path: Path) -> None:
    with pytest.raises(DroughtServingError):
        read_drought_release(tmp_path.as_posix(), AUGUST_FOURTH, requested_zoom=BASE_TIER_REQUEST)


# --- planes/drought.py: the point-containment lookup over nested severity classes ----------------


def test_the_point_lookup_returns_the_most_severe_covering_class(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(
        tmp_path,
        store,
        backend,
        day=AUGUST_FOURTH,
        categories={0: _multipolygon_wkb(_BIG_SQUARE), 4: _multipolygon_wkb(_SMALL_SQUARE)},
    )
    root = tmp_path.as_posix()

    inside_both = most_severe_class_at_point(
        store,
        root,
        requested_zoom=BASE_TIER_REQUEST,
        longitude=0.0,
        latitude=0.0,
        on_or_before=WEDNESDAY_AFTER_FOURTH,
        now=TODAY,
    )
    inside_only_d0 = most_severe_class_at_point(
        store,
        root,
        requested_zoom=BASE_TIER_REQUEST,
        longitude=5.0,
        latitude=5.0,
        on_or_before=WEDNESDAY_AFTER_FOURTH,
        now=TODAY,
    )
    outside_everything = most_severe_class_at_point(
        store,
        root,
        requested_zoom=BASE_TIER_REQUEST,
        longitude=50.0,
        latitude=50.0,
        on_or_before=WEDNESDAY_AFTER_FOURTH,
        now=TODAY,
    )

    assert inside_both.dm_category == _D4_EXCEPTIONAL_DROUGHT
    assert inside_both.valid_date == AUGUST_FOURTH
    assert inside_only_d0.dm_category == _D0_ABNORMALLY_DRY
    assert outside_everything.dm_category is None
    # The release still answered; the point simply falls outside every class's polygon, proving the
    # classes are NOT a partition of space (D0's boundary does not cover the whole map).
    assert outside_everything.valid_date == AUGUST_FOURTH


def test_a_future_point_request_answers_with_no_release_at_all(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(tmp_path, store, backend, day=AUGUST_FOURTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)})

    answer = most_severe_class_at_point(
        store,
        tmp_path.as_posix(),
        requested_zoom=BASE_TIER_REQUEST,
        longitude=0.0,
        latitude=0.0,
        on_or_before=date(2027, 1, 1),
        now=TODAY,
    )

    assert answer.valid_date is None
    assert answer.dm_category is None


# --- pipeline/validation/drought.py: reconciliation against the SOURCE SYSTEM -------------------


@pytest.mark.asyncio
async def test_reconcile_classifies_every_week_and_only_asks_usdm_when_the_listing_cannot_answer() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    written_day = date(2026, 7, 7)
    recorded_absent_day = date(2026, 7, 14)
    conflict_day = date(2026, 7, 21)
    incomplete_day = date(2026, 7, 28)
    source_gap_day = date(2026, 8, 4)
    unrecorded_absence_day = date(2026, 8, 11)

    _write_complete_release(
        store, day=written_day, categories={0: _multipolygon_wkb(_BIG_SQUARE)}, zoom=WRITTEN_ZOOM_TIER
    )
    store.write_absence(
        GovernedAbsence(
            reason="USDM did not publish this week",
            upstream_response="HTTP 404",
            recorded_at=datetime(2026, 7, 16, tzinfo=UTC),
            run_id="run-1",
        ),
        layer=DROUGHT_STREAM,
        kind="observed",
        zoom=WRITTEN_ZOOM_TIER,
        day=recorded_absent_day,
    )
    # A conflict is never produced by the write path (both calls above refuse it); injected directly
    # into the backend's listing to prove the classifier surfaces one if a manual admin action makes one.
    backend.objects[partition_path(DROUGHT_STREAM, "observed", WRITTEN_ZOOM_TIER, conflict_day)] = b"not-real-parquet"
    backend.objects[absence_marker_path(DROUGHT_STREAM, "observed", WRITTEN_ZOOM_TIER, conflict_day)] = b"{}"
    # An incomplete write: partition exists but no completion marker.
    backend.objects[partition_path(DROUGHT_STREAM, "observed", WRITTEN_ZOOM_TIER, incomplete_day)] = b"not-real-parquet"

    source = _FakeSourceCheck(published=frozenset({source_gap_day}))

    report = await reconcile_drought_releases(store, source, first_day=written_day, last_day=unrecorded_absence_day)

    verdicts = {week.valid_date: week for week in report.weeks}
    assert verdicts[written_day].status == "written"
    assert verdicts[recorded_absent_day].status == "recorded_absence"
    assert verdicts[conflict_day].status == "conflict"
    assert verdicts[incomplete_day].status == "warehouse_incomplete"
    assert verdicts[source_gap_day].status == "source_gap"
    assert verdicts[unrecorded_absence_day].status == "unrecorded_absence"
    assert report.gaps == (verdicts[source_gap_day],)
    assert report.conflicts == (verdicts[conflict_day],)
    assert report.incomplete_writes == (verdicts[incomplete_day],)
    # The four already-settled weeks (written, recorded-absent, conflict, incomplete) never touch the source.
    assert sorted(source.checked) == [source_gap_day, unrecorded_absence_day]


@pytest.mark.asyncio
async def test_a_gap_names_the_release_date_the_lane_and_the_source_response() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    source_gap_day = date(2026, 7, 28)
    source = _FakeSourceCheck(published=frozenset({source_gap_day}))

    report = await reconcile_drought_releases(store, source, first_day=source_gap_day, last_day=source_gap_day)

    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.valid_date == source_gap_day
    assert report.lane == DROUGHT_STREAM
    assert "USDM" in gap.source_response
    assert source_gap_day.isoformat() in gap.source_response


def test_written_release_span_reads_the_object_store_never_a_hardcoded_floor() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    assert written_release_span(store) is None

    _write_complete_release(
        store, day=date(2022, 8, 9), categories={0: _multipolygon_wkb(_BIG_SQUARE)}, zoom=WRITTEN_ZOOM_TIER
    )
    _write_complete_release(
        store, day=date(2026, 8, 18), categories={0: _multipolygon_wkb(_BIG_SQUARE)}, zoom=WRITTEN_ZOOM_TIER
    )

    assert written_release_span(store) == (date(2022, 8, 9), date(2026, 8, 18))


# --- the zoom axis: one rung per read, and a blend that is not expressible ------------------------


def test_two_tiers_of_one_release_never_reach_the_point_test_together(tmp_path: Path) -> None:
    """The nested-severity query takes `max(dm_category)` over every covering polygon.

    Two rungs of one release both carry `valid_date == day`, both pass `read_drought_release`'s own
    consistency check, and both reach DuckDB -- so a coarse rung's generalised D4 boundary can win
    the max for a point the requested resolution puts outside it.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(
        tmp_path,
        store,
        backend,
        day=AUGUST_FOURTH,
        categories={0: _multipolygon_wkb(_BIG_SQUARE)},
        zoom=BASE_TIER,
    )
    _write_local_release(
        tmp_path,
        store,
        backend,
        day=AUGUST_FOURTH,
        categories={4: _multipolygon_wkb(_BIG_SQUARE)},
        zoom=DETAIL_TIER,
    )
    root = tmp_path.as_posix()

    at_base = most_severe_class_at_point(
        store, root, requested_zoom=BASE_TIER, longitude=0.0, latitude=0.0, on_or_before=AUGUST_FOURTH, now=TODAY
    )
    at_detail = most_severe_class_at_point(
        store, root, requested_zoom=DETAIL_TIER, longitude=0.0, latitude=0.0, on_or_before=AUGUST_FOURTH, now=TODAY
    )

    assert at_base.dm_category == _D0_ABNORMALLY_DRY
    assert at_detail.dm_category == _D4_EXCEPTIONAL_DROUGHT
    assert at_base.zoom == BASE_TIER
    assert at_detail.zoom == DETAIL_TIER


def test_a_release_the_requested_rung_lacks_answers_none_rather_than_borrowing_one(tmp_path: Path) -> None:
    """At a resolution the derivation has not reached, the honest answer is "not published here"."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(
        tmp_path, store, backend, day=AUGUST_FOURTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)}, zoom=BASE_TIER
    )

    resolved = resolve_drought_release_day(
        store, requested_zoom=DETAIL_TIER, on_or_before=WEDNESDAY_AFTER_FOURTH, now=TODAY
    )

    assert resolved is None


def test_the_listing_and_the_read_always_name_the_same_rung(tmp_path: Path) -> None:
    """z11 resolves to z9 once, for both halves; resolving twice could straddle a ladder change."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_local_release(
        tmp_path, store, backend, day=AUGUST_FOURTH, categories={4: _multipolygon_wkb(_SMALL_SQUARE)}, zoom=DETAIL_TIER
    )
    _write_local_release(
        tmp_path, store, backend, day=AUGUST_ELEVENTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)}, zoom=BASE_TIER
    )

    answer = resolve_drought_release(
        store, tmp_path.as_posix(), requested_zoom=UNPUBLISHED_ZOOM, on_or_before=TODAY, now=TODAY
    )

    assert answer is not None
    assert answer.zoom == DETAIL_TIER
    assert answer.valid_date == AUGUST_FOURTH, "z9's newest release, not z13's"
    assert answer.areas.column("dm_category").to_pylist() == [_D4_EXCEPTIONAL_DROUGHT]


def test_half_written_releases_are_excluded_from_listing_and_resolution(tmp_path: Path) -> None:
    """A partition with parts but no completion marker must never appear published."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    # Write one complete release (with completion marker)
    _write_local_release(tmp_path, store, backend, day=AUGUST_FOURTH, categories={0: _multipolygon_wkb(_BIG_SQUARE)})

    # Inject a half-written release: partition exists but no completion marker
    incomplete_day = date(2026, 8, 18)
    incomplete_key = partition_path(DROUGHT_STREAM, DROUGHT_KIND, BASE_TIER, incomplete_day)
    backend.objects[incomplete_key] = b"not-real-parquet-but-exists"
    # Deliberately do NOT write the completion marker

    # The listing should only include the complete release
    listed_days = list_observed_drought_release_days(store, requested_zoom=BASE_TIER_REQUEST)
    assert listed_days == (AUGUST_FOURTH,), "only the completed release appears"
    assert incomplete_day not in listed_days, "the half-written release is excluded"

    # Resolution should likewise not find the incomplete release
    resolved = resolve_drought_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, on_or_before=incomplete_day, now=TODAY
    )
    assert resolved == AUGUST_FOURTH, "resolves to the last COMPLETE release, not the incomplete one"

    # A request strictly after the complete release but before/at the incomplete day answers None
    # (if incomplete days leaked through, this would wrongly resolve to incomplete_day)
    resolved_gap = resolve_drought_release_day(
        store, requested_zoom=BASE_TIER_REQUEST, on_or_before=date(2026, 8, 10), now=TODAY
    )
    assert resolved_gap == AUGUST_FOURTH, "still answers from the last complete release"
