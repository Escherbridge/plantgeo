"""Backfill every USDM release Tuesday the direct writer has not yet published, oldest first, bounded per turn.

Reaches parity with what PostgreSQL `geo.drought_areas` already holds by re-fetching each historical
Tuesday straight from USDM's own archive -- the SAME source `ingest/usdm_history.py` walked to fill
Postgres in the first place -- rather than ever reading Postgres. `parity.py` is the separate,
read-only tool that PROVES this walk has closed the gap; this module never reads Postgres itself,
which is what makes it safe to keep running after PostgreSQL drops `geo.drought_areas` entirely.

Reuses `forward.py`'s locked publish-and-verify machinery unchanged: the two writers differ only in
which weeks they select and in which direction, never in how a week is fetched, repaired, locked,
written or verified. See `pipeline/direct/AGENTS.md`, "Drought".
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.pipeline.direct.drought.adapter import DirectDroughtError
from agri_data_service.pipeline.direct.drought.forward import (
    DROUGHT_DIRECT_ALL_TIERS,
    DROUGHT_TIME_BUDGET_OUTCOME,
    DroughtForwardConfig,
    DroughtForwardConfigError,
    _publish_release_with_retries,
    _retry_async,
    _skipped_result,
    _tier_status_counts,
    _tier_status_for_weeks,
    _validate_config,
    emit,
)
from agri_data_service.pipeline.direct.drought.forward import (
    parser as forward_parser,
)
from agri_data_service.pipeline.direct.drought.products import (
    drought_lane_registration,
    newest_settled_tuesday,
    release_weeks,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.drought import DROUGHT_STREAM

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier

DROUGHT_BACKFILL_RUN_ID_PREFIX: Final = "drought-backfill:"


async def run_drought_backfill(config: DroughtForwardConfig) -> dict[str, object]:
    """Publish the OLDEST unfilled release(s) in the full floor-to-settled window, up to `max_days`."""
    _validate_config(config)
    run_id = config.run_id or f"{DROUGHT_BACKFILL_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    lane = drought_lane_registration()
    settled_through = newest_settled_tuesday(today=today, publication_lag_days=lane.publication_lag_days)
    weeks = release_weeks(lane.history_floor, settled_through)
    deadline = time.monotonic() + config.time_budget_seconds
    availability = AvailabilityExtensionTally()

    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    statuses = await _retry_async(
        "initial drought backfill R2 census",
        lambda: asyncio.to_thread(_tier_status_for_weeks, store, weeks),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    pending = _owed_weeks_oldest_first(statuses, weeks)[: config.max_days]
    emit(
        {
            "event": "drought_backfill_started",
            "run_id": run_id,
            "layer": DROUGHT_STREAM,
            "history_floor": lane.history_floor.isoformat(),
            "settled_through": settled_through.isoformat(),
            "total_release_weeks": len(weeks),
            "selected_weeks": [week.isoformat() for week in pending],
        }
    )

    results: list[dict[str, object]] = []
    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        for day in pending:
            if time.monotonic() >= deadline:
                results.append(_skipped_result(day, outcome=DROUGHT_TIME_BUDGET_OUTCOME))
                continue
            result = await _publish_release_with_retries(
                session,
                store,
                lane,
                day,
                today=today,
                run_id=run_id,
                config=config,
                deadline=deadline,
                statuses=statuses,
                availability_storage=availability_storage,
                availability=availability,
            )
            results.append(result)
            emit({"event": "drought_backfill_release_complete", "run_id": run_id, **result})

    final_statuses = await _retry_async(
        "final drought backfill R2 census",
        lambda: asyncio.to_thread(_tier_status_for_weeks, store, weeks),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    remaining_owed = _owed_weeks_oldest_first(final_statuses, weeks)
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": DROUGHT_STREAM,
        "history_floor": lane.history_floor.isoformat(),
        "settled_through": settled_through.isoformat(),
        "total_release_weeks": len(weeks),
        "days_published": len(results),
        **availability.to_summary(),
        "results": results,
        "remaining_owed_weeks": len(remaining_owed),
        "backfill_complete": len(remaining_owed) == 0,
        "tier_status_counts": _tier_status_counts(final_statuses),
    }


def _owed_weeks_oldest_first(
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
    weeks: Sequence[date],
) -> tuple[date, ...]:
    """Every release Tuesday this backfill still owes, oldest first -- real gaps before rechecks.

    Unlike `forward.py::_pending_weeks`, a governed absence here is ALWAYS re-examined, not only
    within a recent window: a backfill turn is a full-history walk anyway, so bounding the recheck
    would only hide an old, wrongly-governed absence from the one driver whose job is closing gaps.
    """
    pending: list[date] = []
    rechecks: list[date] = []
    for day in weeks:
        rung = {tier: statuses[tier].get(day, "missing") for tier in DROUGHT_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise DirectDroughtError(f"drought {day.isoformat()} has a data/absence conflict: {rung}")
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise DirectDroughtError(
                    f"drought {day.isoformat()} is absent at z{LANE_BASE_ZOOM_TIER} but carries derived parts: {rung}"
                )
            rechecks.append(day)
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return (*pending, *rechecks)


def parser() -> argparse.ArgumentParser:
    """Reuse the forward parser's exact flags: this walker is bounded by the same knobs, just reordered."""
    built = forward_parser()
    built.description = __doc__
    return built


def parse_args(argv: Sequence[str] | None = None) -> DroughtForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded backfill turn."""
    built = parser()
    arguments = built.parse_args(argv)
    config = DroughtForwardConfig(
        max_days=arguments.max_days,
        time_budget_seconds=arguments.time_budget_seconds,
        retry_attempts=arguments.retry_attempts,
        retry_base_seconds=arguments.retry_base_seconds,
        retry_max_seconds=arguments.retry_max_seconds,
        contention_timeout_seconds=arguments.contention_timeout_seconds,
        run_id=arguments.run_id,
    )
    try:
        _validate_config(config)
    except DroughtForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded backfill turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_drought_backfill(config)
    except Exception as error:  # the one terminal failure report a caller parses
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "DROUGHT_BACKFILL_RUN_ID_PREFIX",
    "main",
    "parse_args",
    "parser",
    "run_drought_backfill",
]
