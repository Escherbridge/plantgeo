"""The lane adapter: fetch one settled day under the lane-day lock, then write its base rung.

NEVER CONSTRUCTS `TerminalEvidence` AND NEVER PASSES `provenance=`. Like `pipeline/direct/soil/adapter.py`
and `pipeline/direct/climate/adapter.py`, this module hands `store.write_partition` /
`store.write_absence` a validated table or a `GovernedAbsence`; the shared finalizer
(`gap_fill.fill_one_lane_day` -> `_bind_rung`/`_rung_objects_from_ledger`) builds every
`TerminalEvidence` from the real written-object ledger, so provenance defaults to `digested` by
construction. `scripts/compile_availability_bootstrap.py` is the ONLY caller that legitimately
passes `provenance=manifest_trusted`, and it is the bootstrap path, not this one -- see
`pipeline/direct/AGENTS.md`, "Availability bootstrap" (D3), and this track's brief, "CRITICAL TRAP".
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.vegetation.products import (
    VEGETATION_DIRECT_KIND,
    VEGETATION_DIRECT_WRITER_START_DAY,
)
from agri_data_service.pipeline.direct.vegetation.rows import vegetation_day_table
from agri_data_service.pipeline.direct.vegetation.source import VegetationSourceError, VegetationSourceUnsettledError
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM as VEGETATION_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.vegetation.source import VegetationDaySource
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


class DirectVegetationError(RuntimeError):
    """Raised when a day cannot support a complete direct publication."""


def refuse_pre_ownership_day(day: date) -> None:
    """Refuse any day at or before the ownership handoff boundary, whatever asked for it.

    THE GUARD IS ON THE ADAPTER, not only on the entry point -- the generic gap-fill driver can reach
    a registered lane too, and a ceiling only the CLI honours is not a ceiling (`refuse_immutable_day`,
    `pipeline/direct/soil/adapter.py`, records the identical judgement). Unlike soil's boundary, this
    one is not an immutable-snapshot cutoff; it is the day the legacy `postgres-vegetation` owner was
    proven inactive.

    ONE EXCLUSIVE BOUNDARY, AND THE BOUNDARY DAY BELONGS TO BACKFILL. `backfill.py::backfill_ceiling`
    is `VEGETATION_DIRECT_WRITER_START_DAY` ITSELF and `forward.py::history_floor` is the day after,
    so the two windows abut with no gap and no overlap: `backfill_ceiling() + 1 day == history_floor()`
    is asserted in `tests/direct/test_vegetation_adapter.py`, not merely described here. An earlier
    revision ceilinged backfill at `START - 1` while this guard already refused `START`, which left
    `START` owned by NEITHER driver at any value of the constant -- and invisibly, since forward's
    census begins above it, backfill's window ended below it, and `parity.py` bounded its own window
    by Postgres's MIN/MAX, so a stopped `postgres-vegetation` put the hole outside every report.
    """
    if day <= VEGETATION_DIRECT_WRITER_START_DAY:
        raise DirectVegetationError(
            f"vegetation {day.isoformat()} is at or before the ownership handoff boundary "
            f"{VEGETATION_DIRECT_WRITER_START_DAY.isoformat()}; that range belongs to backfill.py against "
            "the still-authoritative Postgres governed plane, not to this forward writer"
        )


def no_mirrored_past_proof() -> str | None:
    """The fail-closed default: nothing proves the catalogue has moved past the day being written."""
    return None


@dataclass(slots=True)
class DirectVegetationAdapter:
    """Fetch and write one day while the caller holds that lane-day advisory lock."""

    fetch_source: Callable[[], Awaitable[VegetationDaySource]]
    #: What proves Earth Search has moved PAST this day, or `None` when nothing does. A whole-grid
    #: empty answer is only a governed absence once that is known; see `pipeline/direct/AGENTS.md`,
    #: "An all-null day is a refusal until the mirror is proven past it" -- restated here for "zero
    #: cells filled" rather than "every value null", the shape sparse NDVI actually takes.
    mirrored_past_proof: Callable[[], str | None] = no_mirrored_past_proof
    source: VegetationDaySource | None = field(default=None, init=False)
    #: The refusal this attempt made instead of governing an unproven empty day. Recorded as well as
    #: raised because `gap_fill._export_one_day` turns every adapter exception into `raised`, and the
    #: forward walk has to tell "not settled yet, come back next tick" from a real failure.
    unsettled_refusal: VegetationSourceUnsettledError | None = field(default=None, init=False)

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> LaneRunResult:
        """Roll back the timeout transaction, fetch under the session lock, then write one base rung."""
        refuse_pre_ownership_day(day)
        await session.rollback()
        try:
            source = await self.fetch_source()
        except VegetationSourceError as error:
            raise DirectVegetationError(f"vegetation {day.isoformat()}: {error}") from error
        if source.day != day:
            raise DirectVegetationError(
                f"the fetch closure for vegetation {day.isoformat()} returned {source.day.isoformat()}"
            )
        self.source = source
        if source.is_governed_absence:
            proof = self.mirrored_past_proof()
            if proof is None:
                self.unsettled_refusal = VegetationSourceUnsettledError(
                    f"vegetation {day.isoformat()}: Earth Search answered zero usable cells across all "
                    f"{source.receipt.cells_requested} support cells, and no later settled day is already "
                    "published with values, so nothing proves the catalogue has moved past this day. It is "
                    "refused rather than governed as absent"
                )
                raise self.unsettled_refusal
            return normalise_export_outcome(
                store.write_absence(
                    self._absence(source, run_id=run_id, proof=proof),
                    layer=VEGETATION_STREAM,
                    kind=VEGETATION_DIRECT_KIND,
                    zoom=LANE_BASE_ZOOM_TIER,
                    day=day,
                )
            )
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        return normalise_export_outcome(
            store.write_partition(
                vegetation_day_table(day=day, values=source.values, data_available_at=source.receipt.retrieved_at),
                layer=VEGETATION_STREAM,
                kind=VEGETATION_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=day,
            )
        )

    def _absence(self, source: VegetationDaySource, *, run_id: str, proof: str) -> GovernedAbsence:
        """Carry the source receipt AND the mirrored-past proof into the marker; both justify the claim."""
        return GovernedAbsence(
            reason=(
                f"Earth Search answered zero usable Sentinel-2 L2A cell readings across all "
                f"{source.receipt.cells_requested} support cells of vegetation on {source.day.isoformat()}"
            ),
            upstream_response=json.dumps(
                {"proof_mirror_moved_past_day": proof, "receipt": source.receipt.as_event()}, sort_keys=True
            ),
            recorded_at=datetime.now(UTC),
            run_id=run_id,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> None:
        """Retract an earlier absence this response disproves, inside the lock, before the first write.

        EVERY TIER, not only the base rung -- the identical propagation `pipeline/direct/soil/adapter.py`
        documents: an absence is written at one tier but must be cleared at all four, or a base-only
        retraction leaves the derived rungs asserting an absence over a day that now carries rows.
        """
        retracted = tuple(
            tier for tier in ZOOM_TIERS if store.absence_exists(VEGETATION_STREAM, VEGETATION_DIRECT_KIND, tier, day)
        )
        if not retracted:
            return
        for tier in retracted:
            store.clear_absence_marker(VEGETATION_STREAM, VEGETATION_DIRECT_KIND, tier, day)
        # stderr, because stdout carries the one terminal report a caller parses.
        print(
            json.dumps(
                {
                    "event": "vegetation_forward_absence_retracted",
                    "day": day.isoformat(),
                    "layer": VEGETATION_STREAM,
                    "run_id": run_id,
                    "tier": LANE_BASE_ZOOM_TIER,
                    "tiers": list(retracted),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )


__all__ = [
    "DirectVegetationAdapter",
    "DirectVegetationError",
    "no_mirrored_past_proof",
    "refuse_pre_ownership_day",
]
