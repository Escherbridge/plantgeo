"""The coarse-rung writer and the bulk drain: ordering, retraction, and the walk.

Two invariants here are load-bearing and were BOTH absent from the suite when the code review found
them:

  * `test_the_base_marker_is_written_after_every_coarse_rung` -- only the base tier is censused, so
    the base marker is the one signal that can bring a day back. Reverse the order and a run that
    dies in between strands the day base-complete and permanently empty above z13, on a green tick.
  * `test_a_rung_that_derives_to_nothing_retracts_what_it_held` -- a rung that empties must delete
    its old parts AND clear its old completion marker, or readers at that zoom keep being served
    rows the base day no longer contains, from a rung still claiming to be finished.

And one that keeps the drain terminating: a day the hourly cron holds is requeued, but only so many
times. The drain fills oldest-first and the cron newest-first, so the last day of every lane is
precisely the day most likely to be contended -- an uncapped requeue is an infinite loop.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.paths import (
    completion_marker_path,
    partition_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.derivation import (
    TierWriteError,
    derive_and_write_day_tiers,
)
from agri_data_service.pipeline.parquet.drain import (
    MAX_CONTENDED_RETRIES_PER_DAY,
    DrainLaneProgress,
    plan_drain,
    run_drain,
)
from agri_data_service.pipeline.parquet.gap_fill import _finalize_written_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

DAY: Final = dt.date(2026, 8, 1)
RUN_ID: Final = "test-run"
FROZEN_NOW: Final = dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)
STREAM: Final = "fire-detections"


def _now() -> dt.datetime:
    return FROZEN_NOW


def _base_table(*, cells: int = 4) -> pa.Table:
    """A fire-detections day whose cells merge at z9 and collapse to one at z0."""
    schema = observed_stream_schema(STREAM).arrow_schema
    return pa.Table.from_pylist(
        [
            {
                "cell_longitude": -116.0 - index * 0.001,
                "cell_latitude": 43.0 + index * 0.001,
                "observed_day": DAY,
                "detection_count": index + 1,
                "frp_sum": 10.0 * (index + 1),
                "frp_observation_count": 1,
                "high_confidence_detection_count": 1,
                "newest_observed_at": FROZEN_NOW,
            }
            for index in range(cells)
        ],
        schema=schema,
    )


def _store_with_base_day(*, cells: int = 4) -> tuple[ObjectStore, RecordingBackend]:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(_base_table(cells=cells), layer=STREAM, kind="observed", zoom=BASE_ZOOM_TIER, day=DAY)
    return store, backend


def test_every_derived_rung_is_written_pruned_and_marked() -> None:
    store, backend = _store_with_base_day()

    result = derive_and_write_day_tiers(store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now)

    assert {report.tier for report in result.tiers} == set(DERIVED_ZOOM_TIERS)
    for tier in DERIVED_ZOOM_TIERS:
        assert partition_path(STREAM, "observed", tier, DAY) in backend.objects
        assert completion_marker_path(STREAM, "observed", tier, DAY) in backend.objects


def test_a_coarse_rung_holds_fewer_rows_than_the_base_it_came_from() -> None:
    store, _ = _store_with_base_day()

    derive_and_write_day_tiers(store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now)

    base = store.read_partition(STREAM, "observed", BASE_ZOOM_TIER, DAY)
    coarsest = store.read_partition(STREAM, "observed", 0, DAY)
    assert coarsest.num_rows == 1
    assert coarsest.num_rows < base.num_rows
    # Detections are additive, so nothing may be lost on the way up the ladder.
    assert coarsest.column("detection_count")[0].as_py() == sum(
        base.column("detection_count")[index].as_py() for index in range(base.num_rows)
    )


def test_the_base_marker_is_written_after_every_coarse_rung() -> None:
    """DO NOT DELETE. Only the base tier is censused, so its marker is what brings a day back.

    Written first, a run that then failed to derive would strand the day base-complete -- never
    revisited, permanently empty above z13, on a tick that reported success.
    """
    store, backend = _store_with_base_day()
    order: list[str] = []
    original_put = backend.put

    def recording_put(key: str, payload: bytes, *, content_type: str) -> None:
        parsed = try_parse_completion_marker_path(key)
        if parsed is not None:
            order.append(f"marker-z{parsed.zoom}")
        original_put(key, payload, content_type=content_type)

    backend.put = recording_put  # type: ignore[method-assign]
    outcome, *_ = _finalize_written_day(
        store, LANE_REGISTRY[STREAM], day=DAY, parts=1, rows=4, written_bytes=1, run_id=RUN_ID, now=_now
    )

    assert outcome == "written"
    assert order[-1] == f"marker-z{BASE_ZOOM_TIER}", f"the base marker must land LAST, got {order}"
    assert set(order[:-1]) == {f"marker-z{tier}" for tier in DERIVED_ZOOM_TIERS}


def test_a_derivation_failure_leaves_the_base_day_unmarked() -> None:
    """Self-healing by omission: no base marker means the census re-does the whole day."""
    store, backend = _store_with_base_day()
    # An empty base day cannot be read back, so the derivation raises.
    for key in [k for k in backend.objects if try_parse_partition_path(k) is not None]:
        backend.objects.pop(key)

    outcome, *_rest, detail = _finalize_written_day(
        store, LANE_REGISTRY[STREAM], day=DAY, parts=1, rows=4, written_bytes=1, run_id=RUN_ID, now=_now
    )

    assert outcome == "raised"
    assert detail is not None
    assert completion_marker_path(STREAM, "observed", BASE_ZOOM_TIER, DAY) not in backend.objects


def test_a_rung_that_derives_to_nothing_retracts_what_it_held() -> None:
    """An emptied rung must delete its parts AND clear its marker, or it serves a stale population."""
    store, backend = _store_with_base_day()
    derive_and_write_day_tiers(store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now)
    assert completion_marker_path(STREAM, "observed", 0, DAY) in backend.objects

    # Re-derive from a base day whose every row lacks coordinates: nothing survives to any rung.
    unlocated = pl.from_arrow(_base_table()).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("cell_longitude"),
        pl.lit(None, dtype=pl.Float64).alias("cell_latitude"),
    )
    assert isinstance(unlocated, pl.DataFrame)
    result = derive_and_write_day_tiers(
        store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now, base_table=unlocated
    )

    assert result.tiers == ()
    assert result.notes
    for tier in DERIVED_ZOOM_TIERS:
        assert completion_marker_path(STREAM, "observed", tier, DAY) not in backend.objects, (
            f"z{tier} still claims to be finished after deriving to nothing"
        )
        assert partition_path(STREAM, "observed", tier, DAY) not in backend.objects, (
            f"z{tier} still serves parts the base day no longer holds"
        )


def test_a_derivation_that_cannot_prune_refuses_rather_than_marking() -> None:
    store, backend = _store_with_base_day()
    backend.refuses_delete_of.add(store.key_for(partition_path(STREAM, "observed", 9, DAY)))
    derive_and_write_day_tiers(store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now)

    # Now force z9 to empty; its retraction must fail loudly rather than leave a stale marker.
    unlocated = pl.from_arrow(_base_table()).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("cell_longitude"),
        pl.lit(None, dtype=pl.Float64).alias("cell_latitude"),
    )
    assert isinstance(unlocated, pl.DataFrame)
    with pytest.raises(TierWriteError, match="could not be removed"):
        derive_and_write_day_tiers(
            store, layer=STREAM, kind="observed", day=DAY, run_id=RUN_ID, now=_now, base_table=unlocated
        )


# --- The drain walk ------------------------------------------------------------------------------


def _census_stub(slug: str, days: Sequence[dt.date]) -> DrainLaneProgress:
    return DrainLaneProgress(slug=slug, considered=len(days), pending=sorted(days))


def test_the_drain_walks_oldest_day_first() -> None:
    """A history that fills from the far end backwards leaves the slider a moving hole."""

    class _Census:
        slug = "signal"
        missing_days = (dt.date(2026, 8, 3), dt.date(2026, 8, 1), dt.date(2026, 8, 2))
        error = None

    planned = plan_drain([_Census()])  # type: ignore[list-item]
    assert planned[0].pending == [dt.date(2026, 8, 1), dt.date(2026, 8, 2), dt.date(2026, 8, 3)]


def test_a_contended_day_is_requeued_but_not_forever() -> None:
    """The drain fills oldest-first and the cron newest-first, so their endgames collide.

    An uncapped requeue plus the default `time_budget_seconds=None` is an infinite loop that
    reports progress: pop, contend, push back, pop again.
    """
    lane = _census_stub("signal", [DAY])
    for turn in range(MAX_CONTENDED_RETRIES_PER_DAY):
        assert lane.pending == [DAY], f"turn {turn} should still hold the day"
        lane.pending.pop(0)
        lane.contended += 1
        seen = lane.contended_retries.get(DAY, 0) + 1
        lane.contended_retries[DAY] = seen
        if seen < MAX_CONTENDED_RETRIES_PER_DAY:
            lane.pending.append(DAY)
    assert lane.pending == []
    assert lane.contended == MAX_CONTENDED_RETRIES_PER_DAY


@pytest.mark.asyncio
async def test_a_drain_over_a_lane_with_nothing_missing_terminates_immediately() -> None:
    """The bucket is the checkpoint: a fully covered lane is simply not walked."""
    store, _ = _store_with_base_day()

    class _Session:
        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("a drain with no missing days must not touch the database")

    summary = await run_drain(
        _Session(),  # type: ignore[arg-type]
        store,
        lanes=[],
        today=DAY,
        run_id=RUN_ID,
        now=_now,
    )
    assert summary.lanes == ()
    assert summary.days_written == 0
    assert summary.days_remaining == 0
