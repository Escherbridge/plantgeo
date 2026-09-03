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
from dataclasses import replace
from typing import TYPE_CHECKING, Final

import duckdb
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    derived_empty_completion_marker_path,
    partition_path,
)
from agri_data_service.pipeline.parquet import drain
from agri_data_service.pipeline.parquet.derivation import (
    DerivationResult,
    DerivedTierReport,
    derive_and_write_day_tiers,
)
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
    build_gap_census,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import GovernedAbsenceConflictError, ObjectStore
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.zoom import ZoomTier

STREAM: Final = "fire-detections"
#: A second registered lane, so "once per lane" is distinguishable from "once per walk".
OTHER_STREAM: Final = "water-gauges"
DAY: Final = dt.date(2026, 8, 1)
OLDER_DAY: Final = dt.date(2026, 7, 30)
OLDEST_DAY: Final = dt.date(2026, 7, 29)
RUN_ID: Final = "test-drain"
FROZEN_NOW: Final = dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)
# The four file kinds the retired layout could hold for one day: two parts and the two markers.
LEGACY_FILE_KINDS: Final = ("part-0.parquet", "part-1.parquet", "absent.json", "_complete.json")
# Two published days, so a walk that lost a lane's tally on the first is visibly short on the second.
TWO_LANE_DAYS: Final = 2
# Three published days, which is also `len(DERIVED_ZOOM_TIERS)` -- the two are unrelated and both are 3.
THREE_LANE_DAYS: Final = 3


def _now() -> dt.datetime:
    return FROZEN_NOW


class _RefusingSession:
    """A session that fails any statement: proof a ladder drain never queries Postgres.

    `rollback` RECORDS rather than refuses, and the earlier spelling of this fake ("a ladder drain
    has no transaction to roll back") was true of the fake and false of a real `AsyncSession`.
    SQLAlchemy 2.0 autobegins on the advisory-lock statement, so a walk that never rolled back would
    hold ONE transaction open from the first lane-day to the last -- hours, against a production
    backend, under `idle_in_transaction_session_timeout`. The count is asserted below.
    """

    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("a ladder drain must derive from the bucket, never from the database")

    async def rollback(self) -> None:
        self.rollbacks += 1


class _LockRaisingSession:
    """A session whose every statement raises, as one left in a failed transaction does.

    `PendingRollbackError` is the measured shape: a session that succeeded once and then behaves as
    rolled-back raises on the NEXT statement, which for the ladder path is `pg_try_advisory_lock`
    itself -- before any derivation, outside anything the derivation's own handler covers.
    """

    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("PendingRollbackError: this Session's transaction has been rolled back")

    async def rollback(self) -> None:
        self.rollbacks += 1


class _RecordingAvailabilityStorage:
    """A stand-in for `AvailabilityStorage`: it only has to be identifiable, never readable."""

    def read(self, key: str, *, max_bytes: int) -> None:
        raise AssertionError(f"this drain must not read availability object {key!r} ({max_bytes} bytes)")

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        raise AssertionError(f"this drain must not publish {key!r} ({len(payload)} {content_type} bytes)")

    def compare_and_swap(self, key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        raise AssertionError(
            f"this drain must not advance {key!r} from {expected_etag!r} ({len(payload)} {content_type} bytes)"
        )


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
    """DO NOT DELETE. This is the whole-bucket half of the answer `build_gap_census` gives per window."""
    store, _ = _store()
    _publish_base_day(store, DAY)

    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store)

    assert census.incomplete_days == (DAY,)
    assert census.ladder_complete_day_count == 0


def test_a_rung_that_derived_to_nothing_leaves_the_census_rather_than_being_reselected_forever() -> None:
    """DO NOT DELETE. This is the loop the derived-empty receipt exists to break.

    A rung whose generalisation drops every base row is retracted: no parts, and -- before the
    receipt -- no marker either, which is indistinguishable through a listing from a rung nobody ever
    derived. The day was base-complete, so every future ladder census re-selected it, every repair
    re-derived nothing, and `_rung_objects` counted it `derived_to_zero_rows` on a green tick forever.
    """
    store, backend = _store()
    _publish_base_day(store, DAY)
    derive_and_write_day_tiers(store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now)

    unlocated = pl.from_arrow(_base_table(DAY)).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("cell_longitude"),
        pl.lit(None, dtype=pl.Float64).alias("cell_latitude"),
    )
    assert isinstance(unlocated, pl.DataFrame)
    result = derive_and_write_day_tiers(
        store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now, base_table=unlocated
    )

    assert set(result.emptied) == set(DERIVED_ZOOM_TIERS), "every rung dropped every row"
    for tier in DERIVED_ZOOM_TIERS:
        # The receipt names its own claim: an emptied rung is published under `_complete.empty.json`,
        # and the ordinary name it held from the first, row-bearing derivation must be gone.
        payload = backend.objects[store.key_for(derived_empty_completion_marker_path(STREAM, "observed", tier, DAY))]
        receipt = PartitionCompletion.from_json_bytes(payload)
        assert receipt.derived_empty
        assert (receipt.part_count, receipt.row_count) == (0, 0)
        assert store.key_for(completion_marker_path(STREAM, "observed", tier, DAY)) not in backend.objects
        assert store.key_for(partition_path(STREAM, "observed", tier, DAY)) not in backend.objects
    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store)
    assert census.incomplete_days == (), "a rung that is honestly empty has finished, so the day is complete"
    assert census.ladder_complete_day_count == 1


def test_an_emptied_rung_is_refused_while_a_governed_absence_still_claims_it() -> None:
    """Two markers making different claims about one rung is the state the whole contract prevents.

    The base rung demonstrably holds rows, so a coarse absence above it is the stranded ladder
    `_finalize_written_day` retracts -- which it can only do if the derivation raises the error it
    watches for, rather than quietly writing an empty-completion receipt beside the absence.
    """
    store, backend = _store()
    _publish_base_day(store, DAY)
    backend.put(
        store.key_for(absence_marker_path(STREAM, "observed", DERIVED_ZOOM_TIERS[-1], DAY)),
        b'{"reason": "upstream published nothing"}',
        content_type="application/json",
    )
    unlocated = pl.from_arrow(_base_table(DAY)).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("cell_longitude"),
        pl.lit(None, dtype=pl.Float64).alias("cell_latitude"),
    )
    assert isinstance(unlocated, pl.DataFrame)

    with pytest.raises(GovernedAbsenceConflictError, match="governed-absence marker"):
        derive_and_write_day_tiers(
            store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now, base_table=unlocated
        )


def test_direct_owned_days_are_excluded_from_missing_but_remain_ladder_eligible() -> None:
    direct_day = dt.date(2026, 8, 25)
    store, _ = _store()
    _publish_base_day(store, direct_day)
    fire = replace(LANE_REGISTRY[STREAM], history_floor=dt.date(2026, 8, 24))

    missing = build_gap_census((fire,), store, today=dt.date(2026, 8, 30))[0]
    ladder = build_lane_ladder_census(fire, store)

    assert missing.last_day == dt.date(2026, 8, 24)
    assert direct_day not in missing.missing_days
    assert ladder.incomplete_days == (direct_day,)


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


def test_a_census_over_no_rungs_is_refused_rather_than_reporting_every_day_complete() -> None:
    """`tiers=()` is VACUOUSLY complete: the intersection loop never runs, so `complete == base_data`.

    Reachable the day `ZOOM_TIERS` is reduced to one entry, which would make `DERIVED_ZOOM_TIERS`
    empty and every lane read as ladder-complete over a warehouse with no ladder at all.
    """
    store, _ = _store()
    _publish_base_day(store, DAY)

    with pytest.raises(ValueError, match="over no rungs"):
        build_lane_ladder_census(LANE_REGISTRY[STREAM], store, tiers=())


def test_a_truncated_census_truncates_the_partial_days_with_the_incomplete_ones() -> None:
    """A subset may never be reported larger than the set containing it: measured `incomplete=1, partial=3`."""
    store, _ = _store()
    for day in (OLDEST_DAY, OLDER_DAY, DAY):
        _publish_base_day(store, day)
    for day in (OLDER_DAY, DAY):  # partial ladders, and NOT the day a cap of one selects
        _mark_rung(store, 9, day)

    census = build_lane_ladder_census(LANE_REGISTRY[STREAM], store, max_days_per_lane=1)

    assert census.incomplete_days == (OLDEST_DAY,)
    assert set(census.partial_ladder_days) <= set(census.incomplete_days), (
        "partial ladders are a SUBSET of the days this census selected"
    )
    report = ladder_census_report([census])
    assert report["incomplete_ladder_days"] == 1
    assert census.truncated is True


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
    assert summary.days_written == 0, "a day that wrote no part must not be counted as written"


@pytest.mark.asyncio
async def test_a_day_with_one_emptied_rung_is_reported_even_though_it_wrote() -> None:
    """A DAY IS NOT THE UNIT OF EMPTINESS. One retracted rung carries no marker, so the ladder census
    re-selects this day forever -- and it is invisible to a check that only asks whether the DAY wrote."""
    store, _ = _store()
    _publish_base_day(store, DAY)

    def one_rung_of_three(*_args: object, **_kwargs: object) -> DerivationResult:
        return DerivationResult(
            tiers=(DerivedTierReport(tier=9, part_count=1, row_count=2, byte_count=64),),
            notes=("z5 and z0 dropped every base row",),
            emptied=(5, 0),
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
        derive_tiers=one_rung_of_three,
    )

    assert summary.lanes[0].emptied == [DAY], "a partly-emptied ladder is re-selected forever and must be named"
    assert summary.days_written == 1, "it did write z9, so it is also a written day"


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


# --- Fault isolation and session discipline ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_session_that_raises_on_the_lock_is_recorded_rather_than_ending_the_walk() -> None:
    """DO NOT DELETE. The unguarded `async with` carried this out of `run_drain` and lost EVERY lane's tally.

    `pg_try_advisory_lock` is a real statement. A session left in a failed transaction raises THERE,
    before any derivation, so the derivation's own handler never sees it -- and the module docstring
    promises that "one unparseable day in 2003 must not cost fire-detections the other ~9,000".
    """
    store, _ = _store()
    _publish_base_day(store, DAY)
    _publish_base_day(store, OLDER_DAY)
    session = _LockRaisingSession()

    summary = await run_drain(
        session,  # type: ignore[arg-type]
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=postgres_lane_day_lock,
    )

    assert [failure.outcome for failure in summary.failures] == ["raised", "raised"], (
        "a summary must come back, with BOTH days accounted for"
    )
    assert summary.days_written == 0
    assert session.rollbacks >= TWO_LANE_DAYS, "a failed lane-day must still end its own transaction"


@pytest.mark.asyncio
async def test_every_ladder_lane_day_ends_its_own_transaction() -> None:
    """SQLAlchemy 2.0 autobegins on the lock statement, and a multi-hour walk that never ends it holds
    ONE transaction open from the first day to the last -- which `idle_in_transaction_session_timeout` kills."""
    store, _ = _store()
    _publish_base_day(store, DAY)
    _publish_base_day(store, OLDER_DAY)
    session = _RefusingSession()

    await run_drain(
        session,  # type: ignore[arg-type]
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=unlocked_lane_day,
    )

    assert session.rollbacks == TWO_LANE_DAYS, "one rollback per lane-day, on the success path too"


@pytest.mark.asyncio
async def test_one_guarded_duckdb_session_serves_the_whole_ladder_walk() -> None:
    """`derivation_session` advertises reuse; before this it was reachable from no driver at all, so every
    geometry rung of every day opened a fresh DuckDB and re-ran `LOAD spatial` -- ~3x per geometry day."""
    store, _ = _store()
    for day in (DAY, OLDER_DAY, OLDEST_DAY):
        _publish_base_day(store, day)
    handed: list[object] = []
    spill_settings: list[str] = []

    def record_connection(*_args: object, connection: object = None, **_kwargs: object) -> DerivationResult:
        handed.append(connection)
        # Read INSIDE the walk: `run_drain` closes the session it opened on the way out.
        assert isinstance(connection, duckdb.DuckDBPyConnection)
        row = connection.execute("SELECT current_setting('max_temp_directory_size')").fetchone()
        assert row is not None
        spill_settings.append(str(row[0]))
        return DerivationResult(tiers=(DerivedTierReport(tier=9, part_count=1, row_count=1, byte_count=32),), notes=())

    await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="ladder",
        now=_now,
        lane_day_lock=unlocked_lane_day,
        derive_tiers=record_connection,
    )

    assert len(handed) == THREE_LANE_DAYS
    assert len({id(held) for held in handed}) == 1, "the walk must hand its deriver ONE session, reused"
    assert spill_settings == ["0 bytes"] * THREE_LANE_DAYS, (
        "the walk's shared session must carry the derivation guards; spilling is the load-bearing one"
    )


@pytest.mark.asyncio
async def test_a_drain_hands_its_availability_storage_to_every_exported_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drain writes the same terminal lane-days the cron writes, so it owes the same index entries.

    Without the kwarg the extension step is silently inert -- `fill_one_lane_day` returns early on
    `availability_storage is None` -- and a bulk repair leaves the published index thousands of days
    behind the bucket while every rung it wrote looks healthy.
    """
    store, _ = _store()
    storage = _RecordingAvailabilityStorage()
    handed: list[object] = []

    async def record(*_args: object, **kwargs: object) -> tuple[str, int, int, int, None]:
        handed.append(kwargs.get("availability_storage"))
        return ("written", 1, 1, 32, None)

    monkeypatch.setattr(drain, "fill_one_lane_day", record)

    await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="missing",
        max_days_per_lane=1,
        now=_now,
        lane_day_lock=unlocked_lane_day,
        availability_storage=storage,  # type: ignore[arg-type]
    )

    assert handed == [storage], "the export path must receive the drain's own storage, not None"


@pytest.mark.asyncio
async def test_a_drain_given_no_availability_storage_stays_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the default is None, so a caller that has bootstrapped no index publishes nothing."""
    store, _ = _store()
    handed: list[object] = []

    async def record(*_args: object, **kwargs: object) -> tuple[str, int, int, int, None]:
        handed.append(kwargs.get("availability_storage"))
        return ("written", 1, 1, 32, None)

    monkeypatch.setattr(drain, "fill_one_lane_day", record)

    await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="missing",
        max_days_per_lane=1,
        now=_now,
        lane_day_lock=unlocked_lane_day,
    )

    assert handed == [None]


@pytest.mark.asyncio
async def test_a_drain_retries_each_lane_s_owed_availability_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DO NOT DELETE. Nothing else in a drain comes back for a claim an earlier turn could not finish.

    `build_gap_census` never revisits a base-complete day, so a terminal day whose availability step
    failed is recoverable ONLY through its retry claim -- and until this pass existed the bulk drain
    wrote the claim and then walked past it forever. ONCE per lane, not once per round-robin turn:
    the retry re-verifies every physical part of a day, so repeating it per turn would spend a
    multi-hour repair re-reading the same backlog.
    """
    store, _ = _store()
    storage = _RecordingAvailabilityStorage()
    retried: list[str] = []

    async def record_retry(*_args: object, **kwargs: object) -> tuple[object, ...]:
        retried.append(str(kwargs["lane"]))
        assert kwargs["availability"] is storage, "the retry must use the drain's own storage"
        return ()

    async def record_day(*_args: object, **kwargs: object) -> tuple[str, int, int, int, None]:
        del kwargs
        return ("written", 1, 1, 32, None)

    monkeypatch.setattr(drain, "retry_pending_availability", record_retry)
    monkeypatch.setattr(drain, "fill_one_lane_day", record_day)

    await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM], LANE_REGISTRY[OTHER_STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="missing",
        days_per_lane_turn=1,
        max_days_per_lane=TWO_LANE_DAYS,
        now=_now,
        lane_day_lock=unlocked_lane_day,
        availability_storage=storage,  # type: ignore[arg-type]
    )

    assert retried == [STREAM, OTHER_STREAM], "one retry pass per lane, before that lane takes a day"


@pytest.mark.asyncio
async def test_a_drain_with_no_availability_storage_retries_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the ledger belongs to the index, so an unwired run must not read it at all."""
    store, _ = _store()

    async def refuse_retry(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        raise AssertionError("a drain with no availability storage read the retry ledger")

    async def record_day(*_args: object, **kwargs: object) -> tuple[str, int, int, int, None]:
        del kwargs
        return ("written", 1, 1, 32, None)

    monkeypatch.setattr(drain, "retry_pending_availability", refuse_retry)
    monkeypatch.setattr(drain, "fill_one_lane_day", record_day)

    summary = await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="missing",
        max_days_per_lane=1,
        now=_now,
        lane_day_lock=unlocked_lane_day,
    )

    assert summary.to_report()["availability_retry_owed"] == 0


@pytest.mark.asyncio
async def test_a_raising_retry_pass_never_stops_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """An owed index entry is a smaller loss than a drain that stops; the lane still drains its history."""
    store, _ = _store()
    storage = _RecordingAvailabilityStorage()

    async def raise_retry(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        raise RuntimeError("the retry ledger could not be listed")

    async def record_day(*_args: object, **kwargs: object) -> tuple[str, int, int, int, None]:
        del kwargs
        return ("written", 1, 1, 32, None)

    monkeypatch.setattr(drain, "retry_pending_availability", raise_retry)
    monkeypatch.setattr(drain, "fill_one_lane_day", record_day)

    summary = await run_drain(
        _session(),
        store,
        lanes=[LANE_REGISTRY[STREAM]],
        today=DAY,
        run_id=RUN_ID,
        selection="missing",
        max_days_per_lane=1,
        now=_now,
        lane_day_lock=unlocked_lane_day,
        availability_storage=storage,  # type: ignore[arg-type]
    )

    report = summary.to_report()
    assert report["days_written"] == 1, "the walk continued past an unreadable ledger"
    assert report["availability_retry_owed"] == 1, "and the unread ledger is a number, not silence"


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


def test_a_zoom_day_that_is_marker_only_does_not_supersede_its_legacy_copy() -> None:
    """DO NOT DELETE. A completion marker whose parts were deleted underneath it is `missing`.

    No reader serves it -- `COVERED_PARTITION_STATUSES` is `data`/`absent` -- so "a newer copy is
    published where readers actually look" is FALSE for it, and deleting the legacy copy on that
    basis destroys the only readable copy of the day.
    """
    store, backend = _store()
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=4, completed_at=FROZEN_NOW, run_id=RUN_ID),
        layer=STREAM,
        kind="observed",
        zoom=BASE_ZOOM_TIER,
        day=DAY,
    )
    key = _put_legacy(store, backend, STREAM, DAY, "part-0.parquet")

    swept = _swept(store, backend, dry_run=False)

    assert swept.superseded == (), "a marker with no parts under it supersedes nothing"
    assert [found.day for found in swept.orphaned] == [DAY]
    assert swept.removed == ()
    assert key in backend.objects, "the sweep deleted the only readable copy of this day"


def test_a_zoom_day_that_is_half_written_does_not_supersede_its_legacy_copy() -> None:
    """DO NOT DELETE. `write_partition` clears the completion marker as it uploads `part-0`, so ANY day
    the hourly cron is mid-re-export reads `incomplete` inside this sweep's window -- and `published` is
    computed once, at the start of the layer."""
    store, backend = _store()
    store.write_partition(_base_table(DAY), layer=STREAM, kind="observed", zoom=BASE_ZOOM_TIER, day=DAY)
    key = _put_legacy(store, backend, STREAM, DAY, "part-0.parquet")

    swept = _swept(store, backend, dry_run=False)

    assert swept.superseded == (), "a half-written export supersedes nothing"
    assert [found.day for found in swept.orphaned] == [DAY]
    assert key in backend.objects


def test_a_governed_absence_in_the_zoom_layout_does_supersede_its_legacy_copy() -> None:
    """`absent` IS servable -- a reader answers it as a governed empty day -- so the legacy bytes are surplus."""
    store, backend = _store()
    backend.put(
        store.key_for(absence_marker_path(STREAM, "observed", BASE_ZOOM_TIER, DAY)),
        b'{"reason": "upstream published nothing"}',
        content_type="application/json",
    )
    key = _put_legacy(store, backend, STREAM, DAY, "part-0.parquet")

    swept = _swept(store, backend, dry_run=False)

    assert [found.day for found in swept.superseded] == [DAY]
    assert key not in backend.objects


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
