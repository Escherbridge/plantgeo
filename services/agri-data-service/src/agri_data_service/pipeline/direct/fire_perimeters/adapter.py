"""The lane adapter: write ONE version's base rung from an already-fetched population.

Substituted onto the registered lane with `dataclasses.replace` by `forward.py`, exactly as
`drought/adapter.py` is, so the whole shared finalizer runs unchanged around it: `fill_one_lane_day`
takes the lane-day lock, `_fill_static_day` brackets the export with two watermark reads,
`_export_one_day` calls this, and `_finalize_written_day` prunes the surplus parts, derives z9/z5/z0
and writes the base rung's completion marker LAST.

IT PERFORMS NO FETCH, which is the one structural difference from `drought/adapter.py`. That adapter
fetches inside the lock because a release Tuesday is chosen from the calendar; this lane's VERSION
DAY is chosen from the fetched population itself, so the fetch has already happened by the time any
day exists to pass here. `forward.py` holds the single capture and hands it to both the substituted
watermark and this adapter -- see `source.py`, "ONE FETCH PER TURN".

AN EMPTY POPULATION IS REFUSED BY NAME, NEVER GOVERNED ABSENT. `_export_one_day` converts
`EmptyPartitionError` into a governed absence, which is right for a `daily_series` (the day is
settled and the source genuinely had nothing) and WRONG here: `resolve_static_lane` states that for
a `static_lookup` "the day is a VERSION STAMP, so 'nothing at version W' contradicts a watermark that
asserts version W exists over N published rows -- it is a FAILED READ of that version, not a settled
fact, and it must be retried". Raising this package's own error keeps the outcome `raised`, which is
a failing lane outcome an operator sees, instead of a marker that would then have to be retracted.
`pipeline/lanes/fire_perimeters.py` reasons the same way in prose but still lets the empty table
reach `write_partition`; this refuses one call earlier and says which watermark it contradicts.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.fire_perimeters.products import FIRE_PERIMETERS_DIRECT_KIND
from agri_data_service.pipeline.direct.fire_perimeters.rows import (
    MAX_PART_PAYLOAD_BYTES,
    chunk_row_indices_by_geometry_bytes,
    fire_perimeters_table,
)
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_STREAM

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.fire_perimeters.rows import FirePerimeterPopulation
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


class DirectFirePerimetersError(RuntimeError):
    """Raised when a version cannot be published completely from the population in hand."""


@dataclass(slots=True)
class DirectFirePerimetersAdapter:
    """Write one WFIGS version's base rung while the caller holds the shared lane-day advisory lock.

    `population` is captured ONCE per turn and reused across every bounded publish retry. That is
    deliberate and it is the opposite of `drought/adapter.py`'s refetch-per-attempt rule: drought
    refetches so a stale release can never overwrite a newer one, but here a refetch would change the
    population UNDER the version day already derived from it, publishing content that never matched
    its own stamp. A genuinely newer population is a NEW version at a NEW day, which is the next
    turn's job.
    """

    population: FirePerimeterPopulation
    #: Every part written by the most recent call, for the caller's terminal report.
    parts_written: int = 0

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> LaneRunResult:
        """Roll back the statement-timeout transaction, then write the version's parts."""
        # The session is PostgreSQL as coordination only -- it holds the session-scoped lane-day
        # advisory lock, which survives this rollback (`gap_fill.postgres_lane_day_lock`). Nothing
        # below reads or writes a single row, so holding a snapshot across an object-store upload
        # would pin a production xmin horizon for no benefit.
        await session.rollback()
        if not self.population.rows:
            raise DirectFirePerimetersError(
                f"fire-perimeters {day.isoformat()}: the population is empty, which contradicts the watermark "
                "that scheduled this version. A static lane's day is a version stamp, so an empty read of it is "
                "a failed read to retry -- never a governed absence, which would claim WFIGS published nothing "
                "at this version permanently"
            )
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        table = fire_perimeters_table(self.population, snapshot_day=day)
        geometry_lengths = [len(value.as_py()) for value in table.column("geometry_wkb")]
        chunks = chunk_row_indices_by_geometry_bytes(geometry_lengths, max_bytes=MAX_PART_PAYLOAD_BYTES)
        receipts = tuple(
            store.write_partition(
                table.take(pa.array(indices, type=pa.int64())),
                layer=FIRE_PERIMETERS_STREAM,
                kind=FIRE_PERIMETERS_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=day,
                part_index=part_index,
            )
            for part_index, indices in enumerate(chunks)
        )
        self.parts_written = len(receipts)
        return normalise_export_outcome(receipts)

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> None:
        """Retract an earlier absence this version disproves, at EVERY tier, before the first write.

        `write_partition` refuses outright when a governed-absence marker covers the day it is asked
        to write ("retracting it is a manual admin action"), so without this a version day that once
        carried a marker could never be published again. It CAN carry one: the retired `daily_series`
        shape wrote 287 governed-absence days beside its 45 partition days, and this lane's version
        stamps now land on ordinary calendar days that may be among them.

        EVERY TIER, not only the base rung -- an absence is written at one tier but PROPAGATED up the
        ladder, so a base-only retraction would leave the three coarse rungs asserting a governed
        absence over a version that now carries rows: a `conflict` at three rungs out of four.
        Copied in shape from `drought/adapter.py::_retract_disproven_absence`, which closed the same
        hole for the same reason.
        """
        retracted = tuple(
            tier
            for tier in ZOOM_TIERS
            if store.absence_exists(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, tier, day)
        )
        if not retracted:
            return
        for tier in retracted:
            store.clear_absence_marker(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, tier, day)
        # stderr, because stdout carries the one terminal report a caller parses.
        print(
            json.dumps(
                {
                    "event": "fire_perimeters_forward_absence_retracted",
                    "day": day.isoformat(),
                    "run_id": run_id,
                    "tier": LANE_BASE_ZOOM_TIER,
                    "tiers": list(retracted),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )


__all__ = ["DirectFirePerimetersAdapter", "DirectFirePerimetersError"]
