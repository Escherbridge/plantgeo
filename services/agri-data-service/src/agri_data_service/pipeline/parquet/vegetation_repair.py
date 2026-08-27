"""Integrated vegetation repair and exact-audit command contract."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.db.vegetation_publication import (
    acknowledge_vegetation_publication,
    enqueue_vegetation_publication,
    postgres_vegetation_publication_barrier,
    unlocked_vegetation_publication_barrier,
    vegetation_day_fingerprints,
)
from agri_data_service.pipeline.parquet.gap_fill import postgres_lane_day_lock
from agri_data_service.pipeline.parquet.vegetation_forward import (
    VEGETATION_FORWARD_MAX_ATTEMPTS,
    VEGETATION_FORWARD_RETRY_BASE_SECONDS,
    VegetationPublicationDrainSummary,
    _drain_pending_vegetation,
)
from agri_data_service.pipeline.validation.vegetation_exact import (
    DEFAULT_PROGRESS_EVERY_DAYS,
    DEFAULT_READ_ATTEMPTS,
    ExactVegetationReport,
    reconcile_exact_vegetation,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

VEGETATION_REPAIR_MAX_DAYS: Final = 10_000
VEGETATION_REPAIR_TIME_BUDGET_SECONDS: Final = 7_200.0


@dataclass(frozen=True, slots=True)
class VegetationRepairAuditReport:
    """The opening evidence, single-writer repairs, and independent closing evidence."""

    opening: ExactVegetationReport
    drain: VegetationPublicationDrainSummary
    closing: ExactVegetationReport
    repair_days: tuple[date, ...]

    @property
    def is_clean(self) -> bool:
        return self.drain.is_complete and self.closing.is_clean

    def to_summary(self) -> dict[str, object]:
        return {
            "clean": self.is_clean,
            "closing": self.closing.to_summary(),
            "opening": self.opening.to_summary(),
            "publication": self.drain.to_details(),
            "repair_day_count": len(self.repair_days),
            "repair_days": [day.isoformat() for day in self.repair_days],
        }


async def repair_and_reconcile_exact_vegetation(  # noqa: PLR0913
    session: AsyncSession,
    store: ObjectStore,
    *,
    cell_ids: Sequence[UUID],
    first_day: date,
    last_day: date,
    coverage_last_day: date,
    max_days: int = VEGETATION_REPAIR_MAX_DAYS,
    time_budget_seconds: float = VEGETATION_REPAIR_TIME_BUDGET_SECONDS,
    read_attempts: int = DEFAULT_READ_ATTEMPTS,
    progress_every_days: int = DEFAULT_PROGRESS_EVERY_DAYS,
    progress: Callable[[dict[str, object]], None] | None = None,
    barrier_held: bool = False,
) -> VegetationRepairAuditReport:
    """Hold one barrier while an opening audit authors repairs and a closing audit proves parity."""
    if max_days <= 0 or max_days > VEGETATION_REPAIR_MAX_DAYS:
        raise ValueError(f"max_days must be between 1 and {VEGETATION_REPAIR_MAX_DAYS}")
    if time_budget_seconds <= 0:
        raise ValueError("time_budget_seconds must be positive")
    barrier = unlocked_vegetation_publication_barrier if barrier_held else postgres_vegetation_publication_barrier
    async with barrier(session):
        opening = await reconcile_exact_vegetation(
            session,
            store,
            cell_ids=cell_ids,
            first_day=first_day,
            last_day=last_day,
            coverage_last_day=coverage_last_day,
            read_attempts=read_attempts,
            progress_every_days=progress_every_days,
            progress=progress,
            barrier_held=True,
        )
        source_targets = {
            target.day: target
            for target in await vegetation_day_fingerprints(session, first_day=first_day, last_day=last_day)
        }
        await session.rollback()
        repair_days = tuple(sorted({finding.day for finding in opening.findings} & source_targets.keys()))
        if repair_days:
            await enqueue_vegetation_publication(
                session,
                tuple(source_targets[day] for day in repair_days),
                force=True,
            )
            await session.commit()
        drain = await _drain_pending_vegetation(
            session,
            store,
            through_day=last_day,
            defensive_day_count=0,
            source_revision=0,
            max_days_per_run=max_days,
            time_budget_seconds=time_budget_seconds,
            max_attempts=VEGETATION_FORWARD_MAX_ATTEMPTS,
            retry_base_seconds=VEGETATION_FORWARD_RETRY_BASE_SECONDS,
            lane_day_lock=postgres_lane_day_lock,
            sleep=asyncio.sleep,
            monotonic=time.monotonic,
        )
        closing = await reconcile_exact_vegetation(
            session,
            store,
            cell_ids=cell_ids,
            first_day=first_day,
            last_day=last_day,
            coverage_last_day=coverage_last_day,
            read_attempts=read_attempts,
            progress_every_days=progress_every_days,
            progress=progress,
            barrier_held=True,
        )
        if drain.is_complete and closing.is_clean:
            await enqueue_vegetation_publication(session, tuple(source_targets.values()))
            for target in source_targets.values():
                if not await acknowledge_vegetation_publication(session, target):
                    raise RuntimeError(
                        f"vegetation publication fingerprint advanced while enrolling {target.day.isoformat()}"
                    )
            await session.commit()
        return VegetationRepairAuditReport(
            opening=opening,
            drain=drain,
            closing=closing,
            repair_days=repair_days,
        )


__all__ = [
    "VEGETATION_REPAIR_MAX_DAYS",
    "VEGETATION_REPAIR_TIME_BUDGET_SECONDS",
    "VegetationRepairAuditReport",
    "repair_and_reconcile_exact_vegetation",
]
