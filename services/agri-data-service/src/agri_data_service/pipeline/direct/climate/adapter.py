"""The lane adapter: fetch one settled product-day under the lane-day lock, then write its base rung."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.climate.rows import climate_day_table
from agri_data_service.pipeline.direct.climate.source import ClimateSourceError, ClimateSourceUnsettledError
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.climate.source import ClimateDaySource
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`.
CLIMATE_DIRECT_KIND: Final = "observed"


class DirectClimateFieldError(RuntimeError):
    """Raised when a product-day cannot support a complete direct publication."""


def refuse_immutable_day(product: ClimateFieldProduct, day: date) -> None:
    """Refuse any day this product's immutable snapshot history already owns, whatever asked for it.

    THE GUARD IS ON THE ADAPTER, not only on the entry point, because the generic gap-fill driver can
    reach a registered lane too. A ceiling that only the CLI honours is not a ceiling.
    """
    if day <= product.snapshot_last_day:
        raise DirectClimateFieldError(
            f"{product.stream} {day.isoformat()} is at or below its immutable snapshot last day "
            f"{product.snapshot_last_day.isoformat()}; those days are immutable and no forward writer may "
            "republish them"
        )


def no_mirrored_past_proof() -> str | None:
    """The fail-closed default: nothing proves the source has published past the day being written."""
    return None


@dataclass(slots=True)
class DirectClimateFieldAdapter:
    """Fetch and write one product-day while the caller holds that lane-day advisory lock."""

    product: ClimateFieldProduct
    fetch_source: Callable[[], Awaitable[ClimateDaySource]]
    #: What proves POWER has published PAST this day, or `None` when nothing does. An all-fill answer
    #: is only a governed absence once that is known; see `pipeline/direct/AGENTS.md`,
    #: "An all-null day is a refusal until the mirror is proven past it".
    mirrored_past_proof: Callable[[], str | None] = no_mirrored_past_proof
    source: ClimateDaySource | None = field(default=None, init=False)
    #: The refusal this attempt made instead of governing an unproven all-fill day. Recorded as well
    #: as raised because `gap_fill._export_one_day` turns every adapter exception into `raised`, and
    #: the forward walk has to tell "not settled yet, come back next tick" from a real failure.
    unsettled_refusal: ClimateSourceUnsettledError | None = field(default=None, init=False)

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> LaneRunResult:
        """Roll back the timeout transaction, fetch under the session lock, then write one base rung."""
        refuse_immutable_day(self.product, day)
        await session.rollback()
        try:
            source = await self.fetch_source()
        except ClimateSourceError as error:
            raise DirectClimateFieldError(f"{self.product.stream} {day.isoformat()}: {error}") from error
        if source.day != day or source.product.stream != self.product.stream:
            raise DirectClimateFieldError(
                f"the fetch closure for {self.product.stream} {day.isoformat()} returned "
                f"{source.product.stream} {source.day.isoformat()}"
            )
        self.source = source
        if source.is_governed_absence:
            proof = self.mirrored_past_proof()
            if proof is None:
                # THE SOURCE HAS NOT ANSWERED, IT HAS NOT YET ARRIVED. POWER fills a day with its
                # sentinel until the inputs land, so "every value a fill value" at the settled edge
                # is the ordinary shape of a day the release has not reached. A governed absence for
                # it claims the SOURCE HAD NOTHING -- permanently, until a later turn re-selects the
                # day -- about a day that simply was not published yet.
                self.unsettled_refusal = ClimateSourceUnsettledError(
                    f"{self.product.stream} {day.isoformat()}: POWER answered for all "
                    f"{source.fill_value_cells} support cells and every {self.product.source_parameter} value was a "
                    f"fill value, and no later settled day of this product is published with values, so nothing "
                    f"proves the release has moved past this day. It is refused rather than governed as absent"
                )
                raise self.unsettled_refusal
            return normalise_export_outcome(
                store.write_absence(
                    self._absence(source, run_id=run_id, proof=proof),
                    layer=self.product.stream,
                    kind=CLIMATE_DIRECT_KIND,
                    zoom=LANE_BASE_ZOOM_TIER,
                    day=day,
                )
            )
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        return normalise_export_outcome(
            store.write_partition(
                climate_day_table(self.product, day=day, values=source.values, receipt=source.receipt),
                layer=self.product.stream,
                kind=CLIMATE_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=day,
            )
        )

    def _absence(self, source: ClimateDaySource, *, run_id: str, proof: str) -> GovernedAbsence:
        """Carry the source receipt AND the published-past proof into the marker; both justify the claim."""
        return GovernedAbsence(
            reason=(
                f"NASA POWER answered for all {source.fill_value_cells} support cells of "
                f"{self.product.stream} on {source.day.isoformat()} and every "
                f"{self.product.source_parameter} value was a fill value"
            ),
            upstream_response=json.dumps(
                {"proof_mirror_moved_past_day": proof, "receipt": source.receipt.as_event()}, sort_keys=True
            ),
            recorded_at=datetime.now(UTC),
            run_id=run_id,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> None:
        """Retract an earlier absence this response disproves, inside the lock, before the first write.

        POWER revises a fill-value day into real values once its inputs land. The inverse stays
        fail-closed: a later all-fill response never removes published data.

        EVERY TIER, not only the base rung. An absence is written at one tier but PROPAGATED up the
        ladder, so a base-only retraction leaves z0/z05/z09 asserting a governed absence over a day
        that now carries rows -- a `conflict` at three rungs out of four. `gap_fill` heals it on a
        later tick, but only after a tick spent on a day that was already correct.
        """
        retracted = tuple(
            tier for tier in ZOOM_TIERS if store.absence_exists(self.product.stream, CLIMATE_DIRECT_KIND, tier, day)
        )
        if not retracted:
            return
        for tier in retracted:
            store.clear_absence_marker(self.product.stream, CLIMATE_DIRECT_KIND, tier, day)
        # stderr, because stdout carries the one terminal report a caller parses.
        print(
            json.dumps(
                {
                    "event": "climate_forward_absence_retracted",
                    "day": day.isoformat(),
                    "layer": self.product.stream,
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
    "CLIMATE_DIRECT_KIND",
    "DirectClimateFieldAdapter",
    "DirectClimateFieldError",
    "no_mirrored_past_proof",
    "refuse_immutable_day",
]
