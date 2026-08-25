"""The ladder census, the ladder walk, and the retirement of the pre-zoom key layout.

THREE THINGS HERE ARE LOAD-BEARING AND NONE OF THEM WAS COVERED BEFORE.

  * A base-complete day missing a coarse rung is INVISIBLE to `build_gap_census`, which walks
    `GAP_FILL_ZOOM_TIER` and nothing else. Nothing would ever bring such a day back, so it stays
    empty at every zoom under 13 forever on a green tick. Measured against production on 2026-08-25:
    ~1,040 such lane-days across eleven lanes, every one of them written before the fusion shipped.
  * The ladder walk takes THE SAME advisory lock key as the export path. It is a second writer of
    three of that path's four objects, and a repair sweep holding a different key -- or none --
    would race the hourly cron on precisely the days it is repairing.
  * The pre-zoom layout is invisible to every parser in this service, so `prune_surplus_parts` skips
    it and `list_partition_objects` filters it out. 2,274 keys and 645.7 MB of it were still in the
    bucket on 2026-08-25, and nothing would ever have collected them. The sweep that does must
    refuse a CURRENT key living under the same layer prefix, which is the one test below that would
    turn a cleanup into data loss if it ever went red.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    partition_path,
)
from agri_data_service.pipeline.parquet.derivation import DerivationResult, DerivedTierReport
from agri_data_service.pipeline.parquet.drain import (
    LegacyLayoutRetirement,
    build_lane_ladder_census,
    ladder_census_report,
    legacy_layout_report,
    plan_ladder_drain,
    retire_legacy_layout_objects,
    run_drain,
)
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.zoom import ZoomTier

STREAM: Final = "fire-detections"
DAY: Final = dt.date(2026, 8, 1)
OLDER_DAY: Final = dt.date(2026, 7, 30)
OLDEST_DAY: Final = dt.date(2026, 7, 29)
RUN_ID: Final = "test-drain"
FROZEN_NOW: Final = dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)
# The four file kinds the retired layout could hold for one day: two parts and the two markers.
LEGACY_FILE_KINDS: Final = ("part-0.parquet", "part-1.parquet", "absent.json", "_complete.json")


def _now() -> dt.datetime:
    return FROZEN_NOW


class _RefusingSession:
    """A session that fails any statement: proof a ladder drain never queries Postgres."""

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("a ladder drain must derive from the bucket, never from the database")

    async def rollback(self) -> None:
        raise AssertionError("a ladder drain has no transaction to roll back")


def _session() -> AsyncSession:
    return _RefusingSession()  # type: ignore[return-value]


def _base_table(day: date, *, cells: int = 4) -> pa.Table:
    """A fire-detections day whose cells merge at z9 and collapse to one at z0."""
    return pa.Table.from_pylist(
        [
            {
                "cell_longitude": -116.0 - index * 0.001,
                "cell_latitude": 43.0 + index * 0.001,
                "observed_day": day,
                "detection_count": index + 1,
                "frp_sum": 10.0 * (index + 1),
                "frp_observation_count": 1,
                "high_confidence_detection_count": 1,
                "newest_observed_at": FROZEN_NOW,
            }
            for index in range(cells)
        ],
        schema=observed_stream_schema(STREAM).arrow_schema,
    )


def _store() -> tuple[ObjectStore, RecordingBackend]:
    backend = RecordingBackend()
    return ObjectStore(backend), backend


def _publish_base_day(store: ObjectStore, day: date, *, cells: int = 4) -> None:
    """Write and MARK one base day, which is what makes it a candidate for a ladder repair."""
    store.write_partition(_base_table(day, cells=cells), layer=STREAM, kind="observed", zoom=BASE_ZOOM_TIER, day=day)
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=cells, completed_at=FROZEN_NOW, run_id=RUN_ID),
        layer=STREAM,
        kind="observed",
        zoom=BASE_ZOOM_TIER,
        day=day,
    )


def _mark_rung(store: ObjectStore, tier: ZoomTier, day: date) -> None:
    """Declare one coarse rung of one day finished, as `_write_tier` does after its parts land."""
    store.write_partition(_base_table(day, cells=1), layer=STREAM, kind="observed", zoom=tier, day=day)
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=1, completed_at=FROZEN_NOW, run_id=RUN_ID),
        layer=STREAM,
        kind="observed",
        zoom=tier,
        day=day,
    )


def _complete_ladder(store: ObjectStore, day: date) -> None:
    for tier in DERIVED_ZOOM_TIERS:
        _mark_rung(store, tier, day)


# --- The ladder census ---------------------------------------------------------------------------


def test_a_day_complete_at_every_rung_is_not_selected() -> None:
    store, _ = _store()
    _publish_base_day(store, DAY)
    _complete_ladder(store, DAY)

    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store)

    assert census.base_day_count == 1
    assert census.ladder_complete_day_count == 1
    assert census.incomplete_days == ()


def test_a_base_complete_day_missing_a_rung_is_selected() -> None:
    """DO NOT DELETE. `build_gap_census` walks the base tier alone, so nothing else brings this day back."""
    store, _ = _store()
    _publish_base_day(store, DAY)

    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store)

    assert census.incomplete_days == (DAY,)
    assert census.ladder_complete_day_count == 0


def test_a_day_carrying_some_rungs_is_reported_as_a_partial_ladder() -> None:
    """A run that died mid-ladder is a different incident from a day written before the fusion existed."""
    store, _ = _store()
    _publish_base_day(store, DAY)
    _publish_base_day(store, OLDER_DAY)
    _mark_rung(store, 9, DAY)  # z9 landed, z5 and z0 did not

    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store)

    assert set(census.incomplete_days) == {DAY, OLDER_DAY}
    assert census.partial_ladder_days == (DAY,), "only the half-derived day is a partial ladder"


def test_a_governed_absent_base_day_is_counted_and_never_selected() -> None:
    """An absent base day has no rows to derive from, and this driver may not mint a governed absence."""
    store, backend = _store()
    backend.put(
        store.key_for(absence_marker_path(STREAM, "observed", BASE_ZOOM_TIER, DAY)),
        b'{"reason": "upstream published nothing"}',
        content_type="application/json",
    )

    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store)

    assert census.base_absent_days == 1
    assert census.incomplete_days == ()


def test_a_day_with_parts_but_no_base_marker_is_left_to_the_missing_drain() -> None:
    """`incomplete` at the base is a half-written export, which is the other selection's work."""
    store, _ = _store()
    store.write_partition(_base_table(DAY), layer=STREAM, kind="observed", zoom=BASE_ZOOM_TIER, day=DAY)

    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store)

    assert census.base_day_count == 0
    assert census.incomplete_days == ()


def test_a_listing_failure_stops_the_lane_rather_than_reading_as_no_gaps() -> None:
    store, backend = _store()
    _publish_base_day(store, DAY)

    def explode(_prefix: str) -> object:
        raise OSError("bucket unreachable")

    backend.list_objects = explode  # type: ignore[method-assign]
    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store)

    assert census.error is not None
    assert census.incomplete_days == ()
    assert plan_ladder_drain([census])[0].done, "a lane whose listing failed must not be walked"


def test_the_ladder_report_names_lanes_rather_than_only_summing_them() -> None:
    store, _ = _store()
    _publish_base_day(store, DAY)
    _mark_rung(store, 9, DAY)

    report = ladder_census_report([build_lane_ladder_census(LANE_REGISTRY[STREAM], store)])

    assert report["incomplete_ladder_days"] == 1
    assert report["lanes_with_incomplete_ladders"] == [STREAM]
    assert report["lanes_with_partial_ladders"] == [STREAM]


def test_the_ladder_walk_goes_oldest_day_first() -> None:
    """Same rule as the export drain: a history that fills backwards leaves the slider a moving hole."""
    store, _ = _store()
    for day in (DAY, OLDEST_DAY, OLDER_DAY):
        _publish_base_day(store, day)

    planned = plan_ladder_drain([build_lane_ladder_census(LANE_REGISTRY[STREAM], store)])

    assert planned[0].pending == [OLDEST_DAY, OLDER_DAY, DAY]


# --- The ladder walk -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_ladder_drain_derives_every_rung_without_touching_the_database() -> None:
    """The whole reason this selection is cheap: `signal` measured 151 s for ONE cold Postgres day."""
    store, backend = _store()
    _publish_base_day(store, DAY)

    summary = await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=unlocked_lane_day,
    )

    assert summary.days_written == 1
    assert summary.days_remaining == 0
    for tier in DERIVED_ZOOM_TIERS:
        assert completion_marker_path(STREAM, "observed", tier, DAY) in backend.objects, f"z{tier} was not derived"


@pytest.mark.asyncio
async def test_a_ladder_drain_takes_the_same_lane_day_lock_as_the_export_path() -> None:
    """DO NOT DELETE. A repair holding a different key does not exclude the cron at all."""
    store, _ = _store()
    _publish_base_day(store, DAY)
    taken: list[str] = []

    @asynccontextmanager
    async def recording_lock(_session: AsyncSession, key: str) -> AsyncIterator[bool]:
        taken.append(key)
        yield True

    await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=recording_lock,
    )

    assert taken == [_lane_day_lock_key(LANE_REGISTRY[STREAM], DAY)]


@pytest.mark.asyncio
async def test_a_ladder_drain_never_rewrites_the_base_rung_it_derived_from() -> None:
    store, backend = _store()
    _publish_base_day(store, DAY)
    base_part = store.key_for(partition_path(STREAM, "observed", BASE_ZOOM_TIER, DAY))
    base_marker = store.key_for(completion_marker_path(STREAM, "observed", BASE_ZOOM_TIER, DAY))
    before = (backend.objects[base_part], backend.objects[base_marker])

    await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=unlocked_lane_day,
    )

    assert (backend.objects[base_part], backend.objects[base_marker]) == before
    assert base_part not in backend.deleted


@pytest.mark.asyncio
async def test_a_contended_ladder_day_is_requeued_by_the_shared_walk() -> None:
    """The two selections share one walk, so `contended` behaves identically in both."""
    store, _ = _store()
    _publish_base_day(store, DAY)

    @asynccontextmanager
    async def always_held(_session: AsyncSession, _key: str) -> AsyncIterator[bool]:
        yield False

    summary = await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=always_held,
    )

    lane = summary.lanes[0]
    assert lane.contended > 1, "a contended day must come back on a later turn"
    assert lane.abandoned, "and must eventually stop being retried rather than spinning forever"
    assert summary.days_written == 0


@pytest.mark.asyncio
async def test_a_derivation_failure_is_reported_and_changes_nothing() -> None:
    """A base rung that no longer matches its lane's schema reads exactly like this."""
    store, backend = _store()
    _publish_base_day(store, DAY)
    base_marker = store.key_for(completion_marker_path(STREAM, "observed", BASE_ZOOM_TIER, DAY))

    def refuse(*_args: object, **_kwargs: object) -> DerivationResult:
        raise ValueError("the base table does not carry cell_longitude")

    summary = await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=unlocked_lane_day,
        derive_tiers=refuse,
    )

    assert summary.days_written == 0
    assert [failure.outcome for failure in summary.failures] == ["raised"]
    assert "retracting and re-exporting" in (summary.failures[0].detail or "")
    assert base_marker in backend.objects, "a failed repair must leave the published base day alone"


@pytest.mark.asyncio
async def test_a_day_whose_every_rung_empties_is_reported_rather_than_silently_looping() -> None:
    """The one place the bucket-as-checkpoint rule does not self-terminate, so it is named instead."""
    store, _ = _store()
    _publish_base_day(store, DAY)

    def empties(*_args: object, **_kwargs: object) -> DerivationResult:
        return DerivationResult(tiers=(), notes=("every base row was dropped at this rung",))

    summary = await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=unlocked_lane_day,
        derive_tiers=empties,
    )

    assert summary.lanes[0].emptied == [DAY]
    assert summary.to_report()["emptied_ladders"] == 1


@pytest.mark.asyncio
async def test_a_ladder_summary_counts_the_rungs_it_wrote() -> None:
    store, _ = _store()
    _publish_base_day(store, DAY)

    def three_rungs(*_args: object, **_kwargs: object) -> DerivationResult:
        return DerivationResult(
            tiers=tuple(
                DerivedTierReport(tier=tier, part_count=1, row_count=2, byte_count=64) for tier in DERIVED_ZOOM_TIERS
            ),
            notes=(),
        )

    summary = await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=unlocked_lane_day,
        derive_tiers=three_rungs,
    )

    lane = summary.lanes[0]
    assert (lane.parts, lane.rows, lane.written_bytes) == (3, 6, 192)


# --- Retiring the pre-zoom layout ----------------------------------------------------------------


def _legacy_key(store: ObjectStore, layer: str, day: date, file_name: str = "part-0.parquet") -> str:
    """One object in the layout as it stood before the zoom axis: the same path, minus `zoom=NN/`."""
    return store.key_for(
        f"layer={layer}/kind=observed/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}/{file_name}"
    )


def _put_legacy(store: ObjectStore, backend: RecordingBackend, layer: str, day: date, file_name: str) -> str:
    key = _legacy_key(store, layer, day, file_name)
    backend.put(key, b"pre-zoom bytes", content_type="application/octet-stream")
    return key


def _swept(store: ObjectStore, backend: RecordingBackend, **options: object) -> LegacyLayoutRetirement:
    return retire_legacy_layout_objects(store, backend, layers=[STREAM], **options)[0]  # type: ignore[arg-type]


def test_a_dry_run_reports_the_pre_zoom_residue_and_deletes_nothing() -> None:
    store, backend = _store()
    _publish_base_day(store, DAY)
    key = _put_legacy(store, backend, STREAM, DAY, "part-0.parquet")

    swept = _swept(store, backend)

    assert len(swept.superseded) == 1
    assert swept.removed == ()
    assert key in backend.objects
    assert swept.byte_count == len(b"pre-zoom bytes")


def test_a_superseded_pre_zoom_object_is_removed() -> None:
    """Superseded means the zoom layout already holds that day: removing it can lose nothing."""
    store, backend = _store()
    _publish_base_day(store, DAY)
    key = _put_legacy(store, backend, STREAM, DAY, "part-0.parquet")

    swept = _swept(store, backend, dry_run=False)

    assert len(swept.removed) == 1
    assert key not in backend.objects


def test_an_orphaned_pre_zoom_object_is_kept_unless_it_is_asked_for_by_name() -> None:
    """A legacy day the zoom layout does not hold is the only copy in this bucket."""
    store, backend = _store()
    _publish_base_day(store, DAY)
    orphan = _put_legacy(store, backend, STREAM, OLDER_DAY, "part-0.parquet")

    kept = _swept(store, backend, dry_run=False)
    assert kept.removed == ()
    assert len(kept.orphaned) == 1
    assert orphan in backend.objects

    taken = _swept(store, backend, dry_run=False, include_orphaned=True)
    assert len(taken.removed) == 1
    assert orphan not in backend.objects


def test_a_current_zoom_layout_object_is_never_removed() -> None:
    """DO NOT DELETE. This is the assertion between a cleanup and data loss."""
    store, backend = _store()
    _publish_base_day(store, DAY)
    _complete_ladder(store, DAY)
    published = sorted(backend.objects)
    _put_legacy(store, backend, STREAM, DAY, "part-0.parquet")

    _swept(store, backend, dry_run=False, include_orphaned=True)

    assert sorted(backend.objects) == published, "the sweep removed a key of the LIVE layout"


def test_a_pre_zoom_object_of_another_layer_is_not_swept() -> None:
    store, backend = _store()
    _publish_base_day(store, DAY)
    other = _put_legacy(store, backend, "vegetation", DAY, "part-0.parquet")

    swept = _swept(store, backend, dry_run=False, include_orphaned=True)

    assert other in backend.objects
    assert all(entry.layer == STREAM for entry in (*swept.superseded, *swept.orphaned))


def test_every_pre_zoom_file_kind_is_swept_not_only_the_parquet() -> None:
    """The retired layout carried absence and completion markers too, and they are just as invisible."""
    store, backend = _store()
    _publish_base_day(store, DAY)
    for file_name in LEGACY_FILE_KINDS:
        _put_legacy(store, backend, STREAM, DAY, file_name)

    swept = _swept(store, backend, dry_run=False)

    assert len(swept.removed) == len(LEGACY_FILE_KINDS)
    assert {entry.file_name for entry in swept.superseded} == set(LEGACY_FILE_KINDS)


def test_a_delete_that_fails_is_reported_rather_than_raised() -> None:
    store, backend = _store()
    _publish_base_day(store, DAY)
    key = _put_legacy(store, backend, STREAM, DAY, "part-0.parquet")
    backend.refuses_delete_of.add(key)

    swept = _swept(store, backend, dry_run=False)

    assert swept.removed == ()
    assert len(swept.failures) == 1
    assert key in backend.objects


def test_a_sweep_failure_is_isolated_to_its_own_layer() -> None:
    store, backend = _store()

    def explode(_prefix: str) -> object:
        raise OSError("bucket unreachable")

    backend.list_objects = explode  # type: ignore[method-assign]
    report = legacy_layout_report(retire_legacy_layout_objects(store, backend, layers=[STREAM, "vegetation"]))

    assert report["layers_with_errors"] == [STREAM, "vegetation"]
    assert report["removed"] == 0
