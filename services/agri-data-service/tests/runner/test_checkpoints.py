"""Source checkpoints through real turns: a fully valued unit answer is replayed instead of re-bought.

The checkpoint store is the real `SourceResponseCheckpoints` over in-memory conditional storage.
"""

from __future__ import annotations

from datetime import timedelta

from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.runner.fakes import (
    TODAY,
    MemoryLaneStore,
    ScriptedUpstream,
    days_between,
    grid_lane,
    grid_table,
    ports_for,
    run,
    spec_for,
    unwritten_by_day,
)
from tests.runner.fixtures.grid_refuse import CELLS, GRID_STREAM, GridRefuseStrategy, GridWorld

EDGE = TODAY - timedelta(days=5)
SHORT_DAY = EDGE - timedelta(days=1)
PER_DAY = GridRefuseStrategy(span_days=1)


def _seeded(world: GridWorld) -> MemoryLaneStore:
    store = MemoryLaneStore()
    settled = days_between(EDGE - timedelta(days=13), EDGE - timedelta(days=2))
    world.publish(settled)
    for day in settled:
        store.publish(GRID_STREAM, day, grid_table(day, {cell: world.values[(day, cell)] for cell in CELLS}))
    world.publish([SHORT_DAY])
    world.values[(SHORT_DAY, "c4")] = None
    return store


async def test_a_complete_unit_answer_is_replayed_next_turn_and_only_the_incomplete_unit_is_asked_again() -> None:
    """Per-parameter eligibility: the null-bearing chunk is never kept, the fully valued one is never re-bought."""
    world, storage = GridWorld(), MemoryAvailabilityStorage()
    store = _seeded(world)
    checkpoints = SourceResponseCheckpoints(storage)
    _, first = await run(
        spec_for(grid_lane()),
        ports_for(PER_DAY, store, upstream=ScriptedUpstream(world.answer), checkpoint_store=checkpoints),
    )
    world.values[(SHORT_DAY, "c4")] = 4.0
    second_upstream = ScriptedUpstream(world.answer)

    exit_code, second = await run(
        spec_for(grid_lane()), ports_for(PER_DAY, store, upstream=second_upstream, checkpoint_store=checkpoints)
    )

    assert unwritten_by_day(first)[SHORT_DAY.isoformat()]["reason"] == "refused_partial"
    assert first["checkpoints_retained"] == 1
    assert exit_code == 0
    assert [send.parameters["cells"] for send in second_upstream.fan_out_sends] == ["c3,c4"]
    assert second["checkpoint_restores"] == 1
    assert store.status(GRID_STREAM, SHORT_DAY) == "data"
    assert store.tables[(GRID_STREAM, SHORT_DAY)].num_rows == len(CELLS)


async def test_a_compare_turn_reads_checkpoints_but_never_writes_one() -> None:
    world, storage = GridWorld(), MemoryAvailabilityStorage()
    store = _seeded(world)
    world.values[(SHORT_DAY, "c4")] = 4.0

    _, report = await run(
        spec_for(grid_lane(executor="legacy"), compare=True),
        ports_for(
            PER_DAY,
            store,
            upstream=ScriptedUpstream(world.answer),
            compare=True,
            checkpoint_store=SourceResponseCheckpoints(storage),
        ),
    )

    assert report["checkpoints_retained"] == 0
    assert storage.objects == {}
