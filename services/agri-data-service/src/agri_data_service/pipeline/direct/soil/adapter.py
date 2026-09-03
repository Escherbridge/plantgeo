"""The lane adapter: fetch one settled product-day under the lane-day lock, then write its base rung."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.soil.rows import soil_day_table
from agri_data_service.pipeline.direct.soil.source import SoilSourceError
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct
    from agri_data_service.pipeline.direct.soil.source import SoilDaySource
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`.
SOIL_DIRECT_KIND: Final = "observed"


class DirectSoilFieldError(RuntimeError):
    """Raised when a product-day cannot support a complete direct publication."""


def refuse_immutable_day(product: SoilFieldProduct, day: date) -> None:
    """Refuse any day this product's immutable snapshot history already owns, whatever asked for it.

    THE GUARD IS ON THE ADAPTER, not only on the entry point, because the generic gap-fill driver can
    reach a registered lane too. A ceiling that only the CLI honours is not a ceiling.
    """
    if day <= product.snapshot_last_day:
        raise DirectSoilFieldError(
            f"{product.stream} {day.isoformat()} is at or below its immutable snapshot last day "
            f"{product.snapshot_last_day.isoformat()}; those days are immutable and no forward writer may "
            "republish them"
        )


@dataclass(slots=True)
class DirectSoilFieldAdapter:
    """Fetch and write one product-day while the caller holds that lane-day advisory lock."""

    product: SoilFieldProduct
    fetch_source: Callable[[], Awaitable[SoilDaySource]]
    source: SoilDaySource | None = field(default=None, init=False)

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
        except SoilSourceError as error:
            raise DirectSoilFieldError(f"{self.product.stream} {day.isoformat()}: {error}") from error
        if source.day != day or source.product.stream != self.product.stream:
            raise DirectSoilFieldError(
                f"the fetch closure for {self.product.stream} {day.isoformat()} returned "
                f"{source.product.stream} {source.day.isoformat()}"
            )
        self.source = source
        if source.is_governed_absence:
            return normalise_export_outcome(
                store.write_absence(
                    self._absence(source, run_id=run_id),
                    layer=self.product.stream,
                    kind=SOIL_DIRECT_KIND,
                    zoom=LANE_BASE_ZOOM_TIER,
                    day=day,
                )
            )
        self._retract_disproven_absence(store, day=day, run_id=run_id)
        return normalise_export_outcome(
            store.write_partition(
                soil_day_table(self.product, day=day, values=source.values, receipt=source.receipt),
                layer=self.product.stream,
                kind=SOIL_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=day,
            )
        )

    def _absence(self, source: SoilDaySource, *, run_id: str) -> GovernedAbsence:
        """Carry the exact source receipt into the marker; an absence without one is a silent failure."""
        return GovernedAbsence(
            reason=(
                f"the Open-Meteo ERA5-Land archive answered for all {source.null_value_cells} support cells "
                f"of {self.product.stream} on {source.day.isoformat()} and every "
                f"{self.product.source_parameter} value was null"
            ),
            upstream_response=json.dumps(source.receipt.as_event(), sort_keys=True),
            recorded_at=datetime.now(UTC),
            run_id=run_id,
        )

    def _retract_disproven_absence(self, store: ObjectStore, *, day: date, run_id: str) -> None:
        """Retract an earlier absence this response disproves, inside the lock, before the first write.

        The archive backfills a day it first answered null for once the reanalysis lands. The inverse
        stays fail-closed: a later all-null response never removes published data.

        EVERY TIER, not only the base rung. An absence is written at one tier but PROPAGATED up the
        ladder, so a base-only retraction leaves z0/z05/z09 asserting a governed absence over a day
        that now carries rows -- a `conflict` at three rungs out of four.
        """
        retracted = tuple(
            tier for tier in ZOOM_TIERS if store.absence_exists(self.product.stream, SOIL_DIRECT_KIND, tier, day)
        )
        if not retracted:
            return
        for tier in retracted:
            store.clear_absence_marker(self.product.stream, SOIL_DIRECT_KIND, tier, day)
        # stderr, because stdout carries the one terminal report a caller parses.
        print(
            json.dumps(
                {
                    "event": "soil_forward_absence_retracted",
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
    "SOIL_DIRECT_KIND",
    "DirectSoilFieldAdapter",
    "DirectSoilFieldError",
    "refuse_immutable_day",
]
