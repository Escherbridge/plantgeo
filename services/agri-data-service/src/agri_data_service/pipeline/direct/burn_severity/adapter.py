"""The lane adapter: fetch one governed MTBS release day under the lane-day lock, then write its base rung.

NEVER CONSTRUCTS `TerminalEvidence` AND NEVER PASSES `provenance=`. Like
`pipeline/direct/drought/adapter.py`, this module hands `store.write_partition` / `store.write_absence`
a validated table or a `GovernedAbsence`; the shared finalizer (`gap_fill.fill_one_lane_day` ->
`_bind_rung`/`_rung_objects_from_ledger`) builds every `TerminalEvidence` from the real
written-object ledger, so provenance defaults to `digested` by construction. See
`pipeline/direct/AGENTS.md`, "Drought" -- "The `provenance=` trap does not apply here, by
construction" -- restated for this lane rather than imported, since that section documents
drought's own adapter, not a shared helper. The bootstrap compiler
(`scripts/compile_availability_bootstrap.py`) is the ONLY caller that legitimately passes
`provenance=manifest_trusted`; this is the forward/backfill path, not that one.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.burn_severity.rows import burn_severity_release_day_table
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA, BURN_SEVERITY_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.burn_severity.source import BurnSeverityDaySource
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`. There is
#: no `kind=forecast` sibling for this lane (`docs/lanes/burn-severity.md` section 7: `horizon:
#: none`, "ship no `method/monte_carlo/burn-severity.py`").
BURN_SEVERITY_DIRECT_KIND: Final = "observed"

#: MTBS's own paging ceiling (`ingest/mtbs.py:180-187`): a 50-row page of full-resolution polygon is
#: already ~10.7 MB and the host 500s above ~100 rows. A part file is capped at the same row count
#: the source itself proved safe to move in one piece -- the identical value
#: `pipeline/lanes/burn_severity.py::MAX_ROWS_PER_PART` uses for the Postgres-reading exporter this
#: writer replaces. Restated here rather than imported: `pipeline/lanes/burn_severity.py` is the
#: file the join agent lands the registration swap in, and this worker may not edit it.
MAX_ROWS_PER_PART: Final = 100


class DirectBurnSeverityError(RuntimeError):
    """Raised when a governed release day cannot support a complete direct Parquet publication."""


@dataclass(slots=True)
class DirectBurnSeverityAdapter:
    """Fetch and write one MTBS release day while the caller holds the shared lane-day advisory lock.

    Unlike `drought`'s adapter, there is no `mirrored_past_proof`/`unsettled_refusal` machinery here:
    every day this adapter is ever asked to publish is a day `products.py::governed_release_days`
    already proves has an established MTBS completion date (`docs/lanes/burn-severity.md` section 3),
    so there is no "not yet published" state to poll for the way USDM's weekly Tuesday has one. A
    fetch that returns zero records is a real, permanent fact about the bounding box (a cohort with
    no fires inside it), never a "come back later" refusal.
    """

    fetch_source: Callable[[], Awaitable[BurnSeverityDaySource]]
    source: BurnSeverityDaySource | None = field(default=None, init=False)

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> LaneRunResult:
        """Rollback the timeout transaction, fetch under the session lock, then write z13."""
        await session.rollback()
        source = await self.fetch_source()
        if source.day != day:
            raise DirectBurnSeverityError(f"the fetch closure for {day} returned source day {source.day}")
        self.source = source
        if not source.records:
            return normalise_export_outcome(
                store.write_absence(
                    self._absence(run_id=run_id, ignition_years=source.ignition_years),
                    layer=BURN_SEVERITY_STREAM,
                    kind=BURN_SEVERITY_DIRECT_KIND,
                    zoom=LANE_BASE_ZOOM_TIER,
                    day=day,
                )
            )
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        table = burn_severity_release_day_table(source.records, observed_day=day)
        sorted_table = table.sort_by([(column, "ascending") for column in BURN_SEVERITY_SCHEMA.sort_columns])
        receipts = tuple(
            store.write_partition(
                sorted_table.slice(start, MAX_ROWS_PER_PART),
                layer=BURN_SEVERITY_STREAM,
                kind=BURN_SEVERITY_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=day,
                part_index=part_index,
            )
            for part_index, start in enumerate(range(0, sorted_table.num_rows, MAX_ROWS_PER_PART))
        )
        return normalise_export_outcome(receipts)

    def _absence(self, *, run_id: str, ignition_years: tuple[int, ...]) -> GovernedAbsence:
        """Carry the requested ignition-year cohorts into the marker, so the claim states what was asked."""
        return GovernedAbsence(
            reason=(
                "no MTBS fire from any ignition-year cohort resolving to this release day fell inside "
                "this deployment's bounding box"
            ),
            upstream_response=json.dumps({"ignition_years": list(ignition_years)}, sort_keys=True),
            recorded_at=datetime.now(UTC),
            run_id=run_id,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> None:
        """Retract an earlier absence a now-found cohort disproves, at every tier, before the write.

        EVERY TIER, not only the base rung -- an absence is written at one tier but PROPAGATED up the
        ladder, so a base-only retraction would leave the three coarse rungs asserting a governed
        absence over a release day that now carries rows: a `conflict` at three rungs out of four.
        `docs/lanes/burn-severity.md` section 6 notes MTBS does occasionally REISSUE released data
        without opening a new fire year, so a day once empty inside this bounding box can later gain
        rows on a re-fetch -- matching `pipeline/direct/drought/adapter.py`'s identical retraction.
        """
        retracted = tuple(
            tier
            for tier in ZOOM_TIERS
            if store.absence_exists(BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, tier, day)
        )
        if not retracted:
            return
        for tier in retracted:
            store.clear_absence_marker(BURN_SEVERITY_STREAM, BURN_SEVERITY_DIRECT_KIND, tier, day)
        # stderr, because stdout carries the one terminal report a caller parses.
        print(
            json.dumps(
                {
                    "event": "burn_severity_forward_absence_retracted",
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


__all__ = [
    "BURN_SEVERITY_DIRECT_KIND",
    "MAX_ROWS_PER_PART",
    "DirectBurnSeverityAdapter",
    "DirectBurnSeverityError",
]
