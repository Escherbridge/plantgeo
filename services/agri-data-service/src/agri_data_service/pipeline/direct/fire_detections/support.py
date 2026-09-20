"""Shared publication support for the fire-detections package."""

from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import date
from typing import TYPE_CHECKING, TypeVar

from agri_data_service.foundation.parquet.paths import PartitionDayStatus, partition_day_statuses
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_STREAM

from .products import FIRE_DIRECT_ALL_TIERS, FIRE_DIRECT_KIND, MONTHS_PER_YEAR

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

T = TypeVar("T")


def emit(payload: Mapping[str, object]) -> None:
    """Emit a structured progress event without contaminating the terminal stdout report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    """Return the bounded jittered retry delay used for source, R2, and write retries."""
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


async def retry_async[T](
    label: str, operation: Callable[[], Awaitable[T]], *, attempts: int, base_seconds: float, max_seconds: float
) -> T:
    """Retry a source or R2 operation while retaining the historical source/R2 event name."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as error:
            last_error = error
            if attempt >= attempts:
                break
            delay = retry_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds)
            emit(
                {
                    "event": "fire_detections_forward_retry",
                    "operation": label,
                    "attempt": attempt,
                    "error_type": type(error).__name__,
                    "retry_in_seconds": round(delay, 3),
                }
            )
            await asyncio.sleep(delay)
    assert last_error is not None
    raise PipelineOperationError(
        f"{label} failed after {attempts} attempts", lane="fire-detections", stage="retry"
    ) from last_error


def tier_status_window(
    store: ObjectStore, first_day: date, last_day: date
) -> dict[ZoomTier, dict[date, PartitionDayStatus]]:
    """Read every ladder rung over the bounded publication window."""
    keys_by_tier: dict[ZoomTier, list[str]] = {tier: [] for tier in FIRE_DIRECT_ALL_TIERS}
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        for tier in FIRE_DIRECT_ALL_TIERS:
            keys_by_tier[tier].extend(
                store.list_partition_keys(
                    FIRE_DETECTIONS_STREAM, FIRE_DIRECT_KIND, tier, year=cursor.year, month=cursor.month
                )
            )
        cursor = date(
            cursor.year + (1 if cursor.month == MONTHS_PER_YEAR else 0),
            1 if cursor.month == MONTHS_PER_YEAR else cursor.month + 1,
            1,
        )
    return {
        tier: partition_day_statuses(
            layer=FIRE_DETECTIONS_STREAM,
            kind=FIRE_DIRECT_KIND,
            zoom=tier,
            first_day=first_day,
            last_day=last_day,
            keys=keys,
        )
        for tier, keys in keys_by_tier.items()
    }


def tier_status_day(store: ObjectStore, day: date) -> dict[ZoomTier, PartitionDayStatus]:
    """Read the four physical status markers for one direct-owned day."""
    return {
        tier: partition_day_statuses(
            layer=FIRE_DETECTIONS_STREAM,
            kind=FIRE_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(
                FIRE_DETECTIONS_STREAM, FIRE_DIRECT_KIND, tier, year=day.year, month=day.month
            ),
        )[day]
        for tier in FIRE_DIRECT_ALL_TIERS
    }


def pending_days(statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]]) -> tuple[date, ...]:
    """Identify incomplete data days and fail closed on data/absence contradictions."""
    pending: list[date] = []
    for day in reversed(tuple(statuses[FIRE_DIRECT_ALL_TIERS[0]])):
        rung = {tier: statuses[tier][day] for tier in FIRE_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise PipelineOperationError(
                f"fire-detections {day} has a data/absence conflict: {rung}",
                lane="fire-detections",
                stage="census",
            )
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise PipelineOperationError(
                    f"fire-detections {day} is absent at z{LANE_BASE_ZOOM_TIER} but carries derived parts: {rung}",
                    lane="fire-detections",
                    stage="census",
                )
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return tuple(pending)


def tier_status_counts(statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]]) -> dict[str, dict[str, int]]:
    """Summarize window completion state in the historical terminal-report shape."""
    return {
        f"z{tier}": {
            status: sum(1 for held in by_day.values() if held == status)
            for status in ("data", "absent", "missing", "incomplete", "conflict")
        }
        for tier, by_day in statuses.items()
    }
