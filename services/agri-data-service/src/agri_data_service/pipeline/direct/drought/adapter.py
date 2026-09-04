"""The lane adapter: fetch one settled USDM release under the lane-day lock, then write its base rung."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.usdm import usdm_source_url
from agri_data_service.pipeline.direct.drought.rows import drought_release_table
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome
from agri_data_service.warehouse.schemas.drought import DROUGHT_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.drought.source import DroughtDaySource
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`. There is no
#: `kind=forecast` sibling for this lane (`warehouse/schemas/drought.py` module docstring, `horizon: none`).
DROUGHT_DIRECT_KIND: Final = "observed"


class DirectDroughtError(RuntimeError):
    """Raised when a release Tuesday cannot support a complete direct Parquet publication."""


class DroughtSourceUnsettledError(RuntimeError):
    """Raised when USDM has not published a Tuesday yet and nothing proves the release moved past it."""


def no_mirrored_past_proof() -> str | None:
    """Fail-closed default: nothing proves USDM has published a release Tuesday later than this one."""
    return None


@dataclass(slots=True)
class DirectDroughtAdapter:
    """Fetch and write one USDM release while the caller holds the shared lane-day advisory lock."""

    fetch_source: Callable[[], Awaitable[DroughtDaySource]]
    #: What proves USDM has published a LATER release Tuesday, or `None` when nothing does. See
    #: `pipeline/direct/AGENTS.md`, "An all-null day is a refusal until the mirror is proven past it" --
    #: the same rule applied to a `release_series`' unpublished week rather than a daily series' fill value.
    mirrored_past_proof: Callable[[], str | None] = no_mirrored_past_proof
    source: DroughtDaySource | None = field(default=None, init=False)
    #: Recorded as well as raised because `gap_fill._export_one_day` turns every adapter exception
    #: into `raised`, and the forward walk has to tell "not published yet, come back next tick" from
    #: a real failure.
    unsettled_refusal: DroughtSourceUnsettledError | None = field(default=None, init=False)

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
            raise DirectDroughtError(f"the fetch closure for {day} returned source day {source.day}")
        self.source = source
        if source.release is None:
            proof = self.mirrored_past_proof()
            if proof is None:
                # USDM HAS NOT ANSWERED, IT HAS NOT YET PUBLISHED. A governed absence for the newest
                # owed Tuesday would claim USDM had nothing for it, permanently, about a release that
                # may simply not be out yet -- exactly the trap `pipeline/direct/AGENTS.md`, "An
                # all-null day is a refusal until the mirror is proven past it" names for climate/soil.
                self.unsettled_refusal = DroughtSourceUnsettledError(
                    f"drought {day.isoformat()}: USDM's archive has no release for this Tuesday "
                    f"({usdm_source_url(day.isoformat())}), and no later Tuesday is published either, so "
                    "nothing proves the release has moved past it. It is refused rather than governed absent"
                )
                raise self.unsettled_refusal
            return normalise_export_outcome(
                store.write_absence(
                    self._absence(day, run_id=run_id, proof=proof),
                    layer=DROUGHT_STREAM,
                    kind=DROUGHT_DIRECT_KIND,
                    zoom=LANE_BASE_ZOOM_TIER,
                    day=day,
                )
            )
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        return normalise_export_outcome(
            store.write_partition(
                drought_release_table(source.release, ingested_at=source.fetched_at),
                layer=DROUGHT_STREAM,
                kind=DROUGHT_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=day,
            )
        )

    def _absence(self, day: date, *, run_id: str, proof: str) -> GovernedAbsence:
        """Carry the requested URL AND the published-past proof into the marker; both justify the claim."""
        return GovernedAbsence(
            reason="USDM's archive has no release for this Tuesday",
            upstream_response=json.dumps(
                {"requested_url": usdm_source_url(day.isoformat()), "proof_mirror_moved_past_day": proof},
                sort_keys=True,
            ),
            recorded_at=datetime.now(UTC),
            run_id=run_id,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> None:
        """Retract an earlier absence a now-found release disproves, at every tier, before the write.

        EVERY TIER, not only the base rung -- an absence is written at one tier but PROPAGATED up the
        ladder, so a base-only retraction would leave the three coarse rungs asserting a governed
        absence over a Tuesday that now carries rows: a `conflict` at three rungs out of four.
        """
        retracted = tuple(
            tier for tier in ZOOM_TIERS if store.absence_exists(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, tier, day)
        )
        if not retracted:
            return
        for tier in retracted:
            store.clear_absence_marker(DROUGHT_STREAM, DROUGHT_DIRECT_KIND, tier, day)
        # stderr, because stdout carries the one terminal report a caller parses.
        print(
            json.dumps(
                {
                    "event": "drought_forward_absence_retracted",
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
    "DROUGHT_DIRECT_KIND",
    "DirectDroughtAdapter",
    "DirectDroughtError",
    "DroughtSourceUnsettledError",
    "no_mirrored_past_proof",
]
