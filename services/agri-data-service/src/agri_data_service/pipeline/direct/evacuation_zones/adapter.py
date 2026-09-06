"""The lane adapter: write one already-captured Oregon OEM snapshot's base rung under the lane-day lock.

THE FETCH IS NOT IN HERE, and that is the one place this package deliberately departs from
`drought/adapter.py`. Drought fetches inside the lock because its retry loop refetches; this lane
cannot. A `static_lookup` version stamp must carry ONE coherent capture -- refetching between write
attempts would publish a mixture of two states of a fast-moving multi-fire event under a single
version day, and would move the very digest `forward.py` used to decide the version was owed. So
`forward.py` captures once, decides, and hands the finished table down; the lock's job is to
serialise the write, prune and completion mark, which is what it was minted for
(`gap_fill.py::fill_one_lane_day`'s "the lock spans the whole day"). A fetch is a read of an external
system that no Postgres advisory lock could have protected anyway.

THE `provenance=` TRAP DOES NOT APPLY HERE, BY CONSTRUCTION. This module never constructs
`TerminalEvidence` and never passes `provenance=`. Data receipts and sha256s come entirely from
`gap_fill.py`'s own written-object ledger, populated by `store.write_partition` inside
`ObjectStore.recording_written_objects()`, so `provenance` defaults to `DIGESTED_PROVENANCE`
(`pipeline/parquet/availability_index.py`) and never the bootstrap compiler's `manifest_trusted`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    DIRECT_QUERY_URL,
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
)
from agri_data_service.pipeline.direct.evacuation_zones.rows import split_into_parts
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS

if TYPE_CHECKING:
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import AbsenceWriteReceipt, ObjectStore

#: Coarse rungs FIRST, the base rung LAST, matching `gap_fill.py::_ABSENCE_LADDER_TIERS` exactly: a
#: ladder written in this order leaves an interrupted run's day `missing` rather than
#: covered-but-empty above the base rung. Restated rather than imported because that name is private
#: to `gap_fill.py`; the ORDER is the contract and it is asserted in this package's tests.
_ABSENCE_LADDER_TIERS: Final[tuple[ZoomTier, ...]] = (*DERIVED_ZOOM_TIERS, LANE_BASE_ZOOM_TIER)


class DirectEvacuationZonesError(RuntimeError):
    """Raised when a captured snapshot cannot support a complete direct Parquet publication."""


@dataclass(slots=True)
class DirectEvacuationZonesAdapter:
    """Write one captured statewide snapshot while the caller holds the shared lane-day advisory lock."""

    #: The conformed, grain-sorted base-rung table `forward.py` built from `source`. Zero rows means
    #: Oregon genuinely published nothing statewide, which is a real answer for this lane, not a
    #: failed read: `docs/lanes/evacuation-zones.md` section 6 names a quiet day as a normal state.
    table: pa.Table
    source: EvacuationZonesSource

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> LaneRunResult:
        """Roll the timeout transaction back, then write this version's base rung or its absence."""
        await session.rollback()
        if self.table.num_rows == 0:
            return normalise_export_outcome(self._govern_quiet_day(store, day=day, run_id=run_id))
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        parts = split_into_parts(self.table)
        return normalise_export_outcome(
            tuple(
                store.write_partition(
                    part,
                    layer=EVACUATION_ZONES_STREAM,
                    kind=EVACUATION_ZONES_DIRECT_KIND,
                    zoom=LANE_BASE_ZOOM_TIER,
                    day=day,
                    part_index=index,
                )
                for index, part in enumerate(parts)
            )
        )

    def _govern_quiet_day(self, store: ObjectStore, *, day: date, run_id: str) -> AbsenceWriteReceipt:
        """Mark the WHOLE four-rung ladder absent, coarse rungs first and the base rung last.

        THE LADDER, NOT ONE RUNG, and the ordering is the safety property. `gap_fill.py`'s own
        `_govern_absent_day` writes `_ABSENCE_LADDER_TIERS` (coarse first, base last) because an
        interrupted run must leave the day `missing` rather than covered-but-empty above the base
        rung. This adapter cannot reach that helper -- it is only entered via `EmptyPartitionError`,
        and letting the empty table bubble there would publish that path's "THIS RUN DID NOT CONTACT
        THE UPSTREAM SOURCE SYSTEM" sentence, which is false here -- so the ladder is written by hand,
        in the same order, for the same reason.

        A base-rung-only marker would be worse than cosmetic on this lane: `planes/evacuation_zones.py`
        resolves "as of" ONE TIER at a time, so a z9 rung left `missing` through a quiet spell answers
        a coarse-zoom viewport with the last snapshot that HAD zones -- stale evacuation levels drawn
        over a state that has stood down.

        The base-rung receipt is returned because it is the rung `normalise_export_outcome` and every
        downstream census key on; the coarse markers are written, recorded in the store's ledger, and
        need no separate result.
        """
        blocked = tuple(
            (zoom, part)
            for zoom in _ABSENCE_LADDER_TIERS
            if (part := store.part_blocking_absence(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, zoom, day))
            is not None
        )
        if blocked:
            # A SAME-DAY STAND-DOWN, and it is refused rather than resolved. Version `day` already
            # holds published zones and this capture holds none, so governing the day absent would
            # first require DELETING a published life-safety snapshot on the strength of one empty
            # upstream answer -- and an empty answer from a glitching feed is indistinguishable here
            # from a genuine statewide stand-down. `planes/evacuation_zones.py` renders a governed
            # absence as an affirmative "zero active zones, a normal quiet state", so getting that
            # wrong tells someone inside a Level 3 area there is no evacuation. `ObjectStore`'s own
            # `write_absence` already encodes this judgement ("correcting a completed record is a
            # manual admin action"); this raises first only so the message names the situation
            # instead of surfacing a bare conflict.
            #
            # The exposure is bounded and small: it can only arise when zones were published EARLIER
            # THE SAME UTC DAY. Any other day, the absence ladder writes cleanly.
            rungs = ", ".join(f"z{zoom} ({part})" for zoom, part in blocked)
            raise DirectEvacuationZonesError(
                f"{day.isoformat()}: Oregon OEM returned zero areas but this version already holds "
                f"published zones at {rungs}. Standing the version down would delete a published "
                "life-safety snapshot on one empty answer, so it is refused: an admin retracts the "
                "version (ObjectStore.retract_partition_tier) if the stand-down is real, and the "
                "next tick republishes the zones by itself if the empty answer was not"
            )
        absence = self._absence(run_id=run_id)
        receipt: AbsenceWriteReceipt | None = None
        for zoom in _ABSENCE_LADDER_TIERS:
            receipt = store.write_absence(
                absence,
                layer=EVACUATION_ZONES_STREAM,
                kind=EVACUATION_ZONES_DIRECT_KIND,
                zoom=zoom,
                day=day,
            )
        assert receipt is not None  # `_ABSENCE_LADDER_TIERS` is never empty
        return receipt

    def _absence(self, *, run_id: str) -> GovernedAbsence:
        """Record a quiet statewide day, citing THE UPSTREAM this run actually contacted.

        Written here rather than left to `gap_fill.py::_govern_absent_day`, and the reason is a
        factual one: that helper's `upstream_response` states "THIS RUN DID NOT CONTACT THE UPSTREAM
        SOURCE SYSTEM -- this records what Postgres held at export time". For a source-direct writer
        that sentence is simply false, and a governed absence is exactly the object a later reader
        trusts to say what was asked and what answered. Letting `EmptyPartitionError` bubble into
        that path would have published the lie.
        """
        return GovernedAbsence(
            reason="Oregon OEM published no evacuation areas within this run's bounds",
            upstream_response=json.dumps(
                {
                    "requested_url": DIRECT_QUERY_URL,
                    "requested_bbox": self.source.bbox,
                    "fetched_at": self.source.fetched_at.isoformat(),
                    "zones_returned": self.source.zone_count,
                    "coverage": (
                        "Oregon only: Oregon OEM's Fire_Evacuation_Areas_Public is a single state "
                        "agency's current-state view and no equivalent government-run aggregator "
                        "exists for Washington, Idaho or western Montana"
                    ),
                },
                sort_keys=True,
            ),
            recorded_at=datetime.now(UTC),
            run_id=run_id,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> None:
        """Retract an earlier absence this capture disproves, at every tier, before the write.

        EVERY TIER, not only the base rung -- an absence is written at one tier but PROPAGATED up the
        ladder, so a base-only retraction would leave three coarse rungs asserting a governed absence
        over a version that now carries rows. Copied from `drought/adapter.py`, which closed the same
        hole; here the disproof is a statewide quiet spell ending, which for a life-safety layer is
        the single most important transition the lane has to get right.
        """
        retracted = tuple(
            tier
            for tier in ZOOM_TIERS
            if store.absence_exists(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, tier, day)
        )
        if not retracted:
            return
        for tier in retracted:
            store.clear_absence_marker(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, tier, day)
        # stderr, because stdout carries the one terminal report a caller parses.
        print(
            json.dumps(
                {
                    "event": "evacuation_zones_forward_absence_retracted",
                    "day": day.isoformat(),
                    "run_id": run_id,
                    "tier": LANE_BASE_ZOOM_TIER,
                    "tiers": list(retracted),
                    "zones_returned": self.source.zone_count,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )


__all__ = ["DirectEvacuationZonesAdapter", "DirectEvacuationZonesError"]
