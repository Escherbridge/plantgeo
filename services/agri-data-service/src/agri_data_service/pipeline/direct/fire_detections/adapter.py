"""The fire-detections base-rung adapter used by the generic ladder writer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.pipeline.parquet.derivation import govern_day_absent
from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult, normalise_export_outcome
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_STREAM

from .products import FIRE_DIRECT_KIND
from .support import emit

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

    from .models import FireDaySource


@dataclass(slots=True)
class DirectFireDetectionsAdapter:
    """Fetch and write one base table while the caller holds the shared lane-day lock."""

    fetch_source: Callable[[], Awaitable[FireDaySource]]
    source: FireDaySource | None = field(default=None, init=False)

    async def __call__(self, session: AsyncSession, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:
        """Rollback the timeout transaction, fetch under the session lock, then write z13."""
        await session.rollback()
        source = await self.fetch_source()
        if source.day != day:
            raise PipelineOperationError(
                f"the fetch closure for {day} returned source day {source.day}", lane="fire-detections", stage="adapter"
            )
        self.source = source
        if source.table.num_rows == 0:
            return normalise_export_outcome(
                govern_day_absent(
                    store,
                    GovernedAbsence(
                        reason="the complete applicable-product FIRMS response held no detections for this day",
                        upstream_response=(
                            f"all {', '.join(source.source_products)} requests succeeded for {day.isoformat()} "
                            "and yielded zero accepted records"
                        ),
                        recorded_at=datetime.now(UTC),
                        run_id=run_id,
                    ),
                    layer=FIRE_DETECTIONS_STREAM,
                    kind=FIRE_DIRECT_KIND,
                    day=day,
                )
            )
        retracted = tuple(
            tier for tier in ZOOM_TIERS if store.absence_exists(FIRE_DETECTIONS_STREAM, FIRE_DIRECT_KIND, tier, day)
        )
        if retracted:
            for tier in retracted:
                store.clear_absence_marker(FIRE_DETECTIONS_STREAM, FIRE_DIRECT_KIND, tier, day)
            emit(
                {
                    "event": "fire_detections_forward_absence_retracted",
                    "run_id": run_id,
                    "day": day.isoformat(),
                    "tier": LANE_BASE_ZOOM_TIER,
                    "tiers": list(retracted),
                }
            )
        return normalise_export_outcome(
            store.write_partition(
                source.table, layer=FIRE_DETECTIONS_STREAM, kind=FIRE_DIRECT_KIND, zoom=LANE_BASE_ZOOM_TIER, day=day
            )
        )
