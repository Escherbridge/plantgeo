"""Backfill every governed MTBS release day the direct writer has not yet published, oldest first, bounded per turn.

Reaches parity with what PostgreSQL `geo.features` (layer='burn-severity') already holds by
re-fetching each governed release day straight from MTBS's own EDW feature service -- the SAME
paged-capture-and-parse step `ingest/mtbs.py` already implements for the Postgres path -- rather
than ever reading Postgres. `parity.py` is the separate, read-only tool that PROVES this walk has
closed the gap; this module never reads Postgres itself, which is what makes it safe to keep running
after PostgreSQL drops its `burn-severity` rows from `geo.features` entirely.

Reuses `forward.py`'s locked publish-and-verify machinery AND its day-selection function
(`_pending_days`) unchanged: the two writers differ only in which release days they select and in
which direction, never in how a day is fetched, repaired, locked, written or verified. Unlike
`drought`, there is no bounded backlog-scan window to mirror here --
`products.py::governed_release_days` holds at most a handful of entries today and grows only
through a governance action, so a full census every turn costs nothing. See
`pipeline/direct/AGENTS.md`, "Burn severity".
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
from agri_data_service.ingest.policy import UNCONFIGURED_BBOX_REASON, parse_bbox, resolve_bounded_bbox
from agri_data_service.pipeline.direct.burn_severity.forward import (
    BURN_SEVERITY_TIME_BUDGET_OUTCOME,
    BurnSeverityForwardConfig,
    BurnSeverityForwardConfigError,
    _pending_days,
    _publish_release_day_with_retries,
    _retry_async,
    _skipped_result,
    _tier_status_counts,
    _tier_status_for_days,
    _validate_config,
    emit,
)
from agri_data_service.pipeline.direct.burn_severity.forward import (
    parser as forward_parser,
)
from agri_data_service.pipeline.direct.burn_severity.products import (
    burn_severity_lane_registration,
    governed_release_days,
    release_days_by_ignition_year,
)
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_STREAM

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence
    from datetime import date

BURN_SEVERITY_BACKFILL_RUN_ID_PREFIX: Final = "burn-severity-backfill:"


async def run_burn_severity_backfill(config: BurnSeverityForwardConfig) -> dict[str, object]:
    """Publish the OLDEST unfilled governed release day(s), up to `max_days`, across the WHOLE set."""
    _validate_config(config)
    run_id = config.run_id or f"{BURN_SEVERITY_BACKFILL_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    lane = burn_severity_lane_registration()
    by_day = release_days_by_ignition_year()
    days = governed_release_days()
    availability = AvailabilityExtensionTally()

    resolved_bbox = resolve_bounded_bbox(config.bbox)
    if resolved_bbox is None:
        return _noop(run_id, days=days, availability=availability, detail=UNCONFIGURED_BBOX_REASON)
    if not days:
        return _noop(
            run_id,
            days=days,
            availability=availability,
            detail="MTBS_ANNUAL_RELEASE_DATES carries no governed release day",
        )
    bounding_box = parse_bbox(resolved_bbox)

    deadline = time.monotonic() + config.time_budget_seconds
    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    statuses = await _retry_async(
        "initial burn-severity backfill R2 census",
        lambda: asyncio.to_thread(_tier_status_for_days, store, days),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    pending = _pending_days(statuses, days, newest_first=False)[: config.max_days]
    emit(
        {
            "event": "burn_severity_backfill_started",
            "run_id": run_id,
            "layer": BURN_SEVERITY_STREAM,
            "history_floor": lane.history_floor.isoformat(),
            "governed_release_days": [day.isoformat() for day in days],
            "selected_days": [day.isoformat() for day in pending],
        }
    )

    results: list[dict[str, object]] = []
    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        for day in pending:
            if time.monotonic() >= deadline:
                results.append(_skipped_result(day, outcome=BURN_SEVERITY_TIME_BUDGET_OUTCOME))
                continue
            result = await _publish_release_day_with_retries(
                session,
                store,
                lane,
                day,
                ignition_years=by_day[day],
                bounding_box=bounding_box,
                run_id=run_id,
                config=config,
                deadline=deadline,
                today=today,
                availability_storage=availability_storage,
                availability=availability,
            )
            results.append(result)
            emit({"event": "burn_severity_backfill_release_complete", "run_id": run_id, **result})

    final_statuses = await _retry_async(
        "final burn-severity backfill R2 census",
        lambda: asyncio.to_thread(_tier_status_for_days, store, days),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    remaining_owed = _pending_days(final_statuses, days, newest_first=False)
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": BURN_SEVERITY_STREAM,
        "history_floor": lane.history_floor.isoformat(),
        "governed_release_days": [day.isoformat() for day in days],
        "days_published": len(results),
        **availability.to_summary(),
        "results": results,
        "remaining_owed_days": len(remaining_owed),
        "backfill_complete": len(remaining_owed) == 0,
        "tier_status_counts": _tier_status_counts(final_statuses),
    }


def _noop(
    run_id: str,
    *,
    days: tuple[date, ...],
    availability: AvailabilityExtensionTally,
    detail: str,
) -> dict[str, object]:
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": BURN_SEVERITY_STREAM,
        "governed_release_days": [day.isoformat() for day in days],
        "days_published": 0,
        **availability.to_summary(),
        "results": [],
        "remaining_owed_days": 0,
        "backfill_complete": True,
        "tier_status_counts": {},
        "detail": detail,
    }


def parser() -> argparse.ArgumentParser:
    """Reuse the forward parser's exact flags: this walker is bounded by the same knobs, just reordered."""
    built = forward_parser()
    built.description = __doc__
    return built


def parse_args(argv: Sequence[str] | None = None) -> BurnSeverityForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded backfill turn."""
    built = parser()
    arguments = built.parse_args(argv)
    config = BurnSeverityForwardConfig(
        max_days=arguments.max_days,
        time_budget_seconds=arguments.time_budget_seconds,
        retry_attempts=arguments.retry_attempts,
        retry_base_seconds=arguments.retry_base_seconds,
        retry_max_seconds=arguments.retry_max_seconds,
        contention_timeout_seconds=arguments.contention_timeout_seconds,
        run_id=arguments.run_id,
        bbox=arguments.bbox,
    )
    try:
        _validate_config(config)
    except BurnSeverityForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded backfill turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_burn_severity_backfill(config)
    except Exception as error:  # the one terminal failure report a caller parses
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "BURN_SEVERITY_BACKFILL_RUN_ID_PREFIX",
    "main",
    "parse_args",
    "parser",
    "run_burn_severity_backfill",
]
