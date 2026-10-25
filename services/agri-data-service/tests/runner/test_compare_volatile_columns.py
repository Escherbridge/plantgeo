"""G6 `--compare` against a bucket the legacy writer stamped at its own capture instant (`volatile_columns`).

A lane's rows carry a capture-time column (`ingested_at`), so a rebuilt day never byte-matches the
published one. The stream's declared `volatile_columns` leave it out of both digests; content still
decides. See `pipeline/runner/AGENTS.md` "Volatile columns".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from tests.runner.fakes import (
    NOW,
    TODAY,
    MemoryLaneStore,
    ScriptedUpstream,
    days_between,
    grid_lane,
    grid_table,
    ports_for,
    run,
    spec_for,
)
from tests.runner.fixtures.grid_refuse import CELLS, GRID_STREAM, GridRefuseStrategy, GridWorld

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from agri_data_service.pipeline.runner.contract import SourceResponse

EDGE: Final = TODAY - timedelta(days=5)
WINDOW: Final = days_between(EDGE - timedelta(days=13), EDGE)
#: The legacy writer's own capture instant, days before this turn's fetch.
LEGACY_INGESTED_AT: Final = NOW - timedelta(days=3)


def _stamped(table: pa.Table, instant: datetime) -> pa.Table:
    return table.append_column("ingested_at", pa.array([instant] * table.num_rows, pa.timestamp("us", tz="UTC")))


@dataclass(frozen=True)
class StampedGridStrategy(GridRefuseStrategy):
    """The grid fixture plus the capture-time column real lanes stamp from `retrieved_at`."""

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """The grid rows, each stamped with this fetch's `retrieved_at`."""
        return _stamped(super().rows(day, responses), responses[0].retrieved_at)


@pytest.mark.parametrize(
    ("volatile_columns", "drift", "equal", "differ"),
    [
        pytest.param(["ingested_at"], False, len(WINDOW), 0, id="volatile-only-delta-is-equal"),
        pytest.param([], False, 0, len(WINDOW), id="undeclared-capture-column-differs-every-day"),
        pytest.param(["ingested_at"], True, len(WINDOW) - 1, 1, id="content-delta-still-differs"),
    ],
)
async def test_compare_ignores_declared_volatile_columns_and_still_sees_content(
    volatile_columns: list[str], drift: bool, equal: int, differ: int
) -> None:
    """Published at the legacy instant, rebuilt now: only the declared column is blind; a value change is not."""
    store, world = MemoryLaneStore(), GridWorld()
    world.publish(WINDOW)
    for day in WINDOW:
        published = grid_table(day, {cell: world.values[(day, cell)] for cell in CELLS})
        store.publish(GRID_STREAM, day, _stamped(published, LEGACY_INGESTED_AT))
    drifted = WINDOW[4]
    if drift:
        world.values[(drifted, "c2")] = 99.0
    streams = [{"slug": GRID_STREAM, "floor_basis": "compare fixture", "volatile_columns": volatile_columns}]
    lane = grid_lane(executor="legacy", streams=streams)
    strategy = StampedGridStrategy(span_days=1)

    exit_code, report = await run(
        spec_for(lane, compare=True), ports_for(strategy, store, upstream=ScriptedUpstream(world.answer), compare=True)
    )

    assert exit_code == 0
    assert store.journal == []
    result = report["compare_result"][GRID_STREAM]
    assert (result["days_equal"], result["days_differ"]) == (equal, differ)
    if drift:
        assert result["differing_days"] == [drifted.isoformat()]
