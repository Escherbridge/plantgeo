"""Orchestrate the bounded fire-detections forward publication turn."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.geography.bounding_box import inline_bbox_value
from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.pipeline.constants import FIRE_DETECTIONS_DIRECT_WRITER_START_DAY, LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct import (
    LANE_DAY_OUTCOMES,
    REFUSE_WHOLE_RELEASE,
    USAGE_ERROR_ON_UNCONFIGURED_BBOX,
    DirectWriterContract,
)
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, LaneRegistration
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_STREAM

from .adapter import DirectFireDetectionsAdapter
from .models import FireDaySource, FireForwardConfig
from .products import (
    FIRE_DIRECT_DEFAULT_CONTENTION_TIMEOUT_SECONDS,
    FIRE_DIRECT_DEFAULT_LOOKBACK_DAYS,
    FIRE_DIRECT_DEFAULT_MAX_DAYS,
    FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY,
    FIRE_DIRECT_DEFAULT_RETRY_ATTEMPTS,
    FIRE_DIRECT_DEFAULT_RETRY_BASE_SECONDS,
    FIRE_DIRECT_DEFAULT_RETRY_MAX_SECONDS,
    FIRE_DIRECT_KIND,
    FIRE_DIRECT_MAX_CONTENTION_TIMEOUT_SECONDS,
    FIRE_DIRECT_MAX_DAYS,
    FIRE_DIRECT_MAX_LOOKBACK_DAYS,
    FIRE_DIRECT_MAX_RETRY_ATTEMPTS,
    FIRE_DIRECT_MAX_RETRY_BASE_SECONDS,
    FIRE_DIRECT_MAX_RETRY_MAX_SECONDS,
    FIRE_DIRECT_MIN_DELAY_SECONDS,
    FIRE_DIRECT_RUN_ID_PREFIX,
    FIRE_DIRECT_STATEMENT_TIMEOUT_SECONDS,
)
from .source import fetch_fire_day
from .support import (
    emit,
    pending_days,
    retry_async,
    retry_delay,
    tier_status_counts,
    tier_status_day,
    tier_status_window,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage


WRITER_CONTRACT: Final = DirectWriterContract(
    slug="fire-detections",
    identity_defect=REFUSE_WHOLE_RELEASE,
    geometry_defect=REFUSE_WHOLE_RELEASE,
    unconfigured_bbox=USAGE_ERROR_ON_UNCONFIGURED_BBOX,
    turn_outcomes=LANE_DAY_OUTCOMES,
    flags_absent_on_purpose={
        "--product": "one stream; NRT/SP FIRMS products are folded into one day, and `source_products` "
        "reports which answered.",
        "--time-budget-seconds": "this turn is bounded in four independent source dimensions; "
        "a wall-clock budget could abort a day mid-ladder.",
        "--max-records": "the ceiling is per exact UTC day, and `--max-records-per-day` says so.",
    },
    policy_basis="Every bound is on the source, not the schedule: the lane owns days at or after "
    "FIRE_DETECTIONS_DIRECT_WRITER_START_DAY and no flag can move that boundary. A complete zero-row "
    "response records a governed z13 absence; a later non-empty response explicitly retracts it.",
)


async def run_fire_forward(config: FireForwardConfig) -> dict[str, object]:
    """Refresh a bounded newest-first slice of settled FIRMS days."""
    _validate_config(config)
    run_id = config.run_id or f"{FIRE_DIRECT_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = datetime.now(UTC).date()
    lane = LANE_REGISTRY[FIRE_DETECTIONS_STREAM]
    availability = AvailabilityExtensionTally()
    settled_through = today - timedelta(days=lane.publication_lag_days)
    first_day = max(settled_through - timedelta(days=config.lookback_days - 1), config.forward_start_day)
    if first_day > settled_through:
        report: dict[str, object] = {
            "status": "completed",
            "run_id": run_id,
            "layer": FIRE_DETECTIONS_STREAM,
            "namespace": f"layer={FIRE_DETECTIONS_STREAM}/kind={FIRE_DIRECT_KIND}/",
            "first_day": first_day.isoformat(),
            "settled_through": settled_through.isoformat(),
            "forward_start_day": config.forward_start_day.isoformat(),
            "force_day": None if config.force_day is None else config.force_day.isoformat(),
            "days_published": 0,
            **availability.to_summary(),
            "results": [],
            "remaining_window_backlog": [],
            "tier_status_counts": {},
            "detail": "no direct-owned day has reached the settled publication boundary",
        }
        emit({"event": "fire_detections_forward_noop", **report})
        return report
    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    statuses = await retry_async(
        "initial fire-detections R2 census",
        lambda: asyncio.to_thread(tier_status_window, store, first_day, settled_through),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    if config.force_day is not None:
        if not first_day <= config.force_day <= settled_through:
            raise PipelineOperationError(
                f"forced day {config.force_day} must be settled and inside the bounded NRT window "
                f"{first_day}..{settled_through}",
                code="invalid_forced_day",
                lane=FIRE_DETECTIONS_STREAM,
                stage="selection",
            )
        pending: tuple[date, ...] = (config.force_day,)
    else:
        pending_days(statuses)
        pending = tuple(reversed(tuple(statuses[LANE_BASE_ZOOM_TIER])))[: config.max_days]
    emit(
        {
            "event": "fire_detections_forward_started",
            "run_id": run_id,
            "layer": FIRE_DETECTIONS_STREAM,
            "namespace": f"layer={FIRE_DETECTIONS_STREAM}/kind={FIRE_DIRECT_KIND}/",
            "first_day": first_day.isoformat(),
            "settled_through": settled_through.isoformat(),
            "forward_start_day": config.forward_start_day.isoformat(),
            "force_day": None if config.force_day is None else config.force_day.isoformat(),
            "selected_days": [day.isoformat() for day in pending],
        }
    )
    results: list[dict[str, object]] = []
    async with local_source_loader_session(settings.require_local_source_loader_database_url()) as session:
        for day in pending:
            result = await _publish_day_with_retries(
                session,
                store,
                lane,
                day,
                today=today,
                run_id=run_id,
                config=config,
                availability_storage=availability_storage,
                availability=availability,
            )
            results.append(result)
            emit({"event": "fire_detections_forward_day_complete", "run_id": run_id, **result})
    final_statuses = await retry_async(
        "final fire-detections R2 census",
        lambda: asyncio.to_thread(tier_status_window, store, first_day, settled_through),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    window_backlog = pending_days(final_statuses)
    remaining = tuple(day for day in window_backlog if day in set(pending))
    if remaining:
        raise PipelineOperationError(
            f"the bounded forward window still has {len(remaining)} unfilled day(s): "
            f"{', '.join(day.isoformat() for day in remaining)}",
            code="window_incomplete",
            lane=FIRE_DETECTIONS_STREAM,
            stage="verification",
            retryable=True,
        )
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": FIRE_DETECTIONS_STREAM,
        "namespace": f"layer={FIRE_DETECTIONS_STREAM}/kind={FIRE_DIRECT_KIND}/",
        "first_day": first_day.isoformat(),
        "settled_through": settled_through.isoformat(),
        "forward_start_day": config.forward_start_day.isoformat(),
        "force_day": None if config.force_day is None else config.force_day.isoformat(),
        "days_published": len(results),
        **availability.to_summary(),
        "results": results,
        "remaining_window_backlog": [day.isoformat() for day in window_backlog],
        "tier_status_counts": tier_status_counts(final_statuses),
    }


async def _publish_day_with_retries(  # noqa: PLR0913
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    today: date,
    run_id: str,
    config: FireForwardConfig,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Acquire once, then refetch and republish under that lock for every bounded attempt."""
    deadline = time.monotonic() + config.contention_timeout_seconds
    while True:
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if granted:
                return await _publish_locked_day_with_retries(
                    session,
                    store,
                    lane,
                    day,
                    today=today,
                    run_id=run_id,
                    config=config,
                    availability_storage=availability_storage,
                    availability=availability,
                )
        await session.rollback()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PipelineOperationError(
                f"lane-day contention for {day} exceeded {config.contention_timeout_seconds:g}s",
                code="lock_timeout",
                lane=FIRE_DETECTIONS_STREAM,
                stage="lock",
                retryable=True,
            )
        delay = min(
            remaining,
            retry_delay(1, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds),
        )
        emit(
            {
                "event": "fire_detections_forward_contention",
                "run_id": run_id,
                "day": day.isoformat(),
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)


async def _publish_locked_day_with_retries(  # noqa: PLR0913
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    today: date,
    run_id: str,
    config: FireForwardConfig,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Refetch before every write/verification attempt while one advisory lock remains held."""
    for write_attempt in range(1, config.retry_attempts + 1):
        adapter = DirectFireDetectionsAdapter(
            lambda: fetch_fire_day(
                day=day,
                bbox=config.bbox,
                max_records=config.max_records_per_day,
                retry_attempts=1,
                retry_base_seconds=config.retry_base_seconds,
                retry_max_seconds=config.retry_max_seconds,
            )
        )
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                replace(lane, adapter=adapter),
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=today,
                lane_day_lock=unlocked_lane_day,
                statement_timeout_seconds=FIRE_DIRECT_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except Exception as error:
            with suppress(Exception):
                await session.rollback()
            outcome = "raised"
            parts = rows = written_bytes = 0
            detail = f"{day.isoformat()}: {type(error).__name__}: {error}"
        source = adapter.source
        if outcome in {"blocked", "absent", "written"} and source is None:
            raise PipelineOperationError(
                f"fire-detections {day} returned {outcome} without a completed locked source fetch",
                code="missing_source_evidence",
                lane=FIRE_DETECTIONS_STREAM,
                stage="publication",
            )
        if outcome in {"blocked", "absent"}:
            if outcome == "blocked":
                raise PipelineOperationError(
                    detail or f"fire-detections {day} is blocked",
                    code="publication_blocked",
                    lane=FIRE_DETECTIONS_STREAM,
                    stage="publication",
                    retryable=True,
                )
            assert source is not None
            return _source_result(
                source, outcome=outcome, parts=parts, rows=rows, written_bytes=written_bytes, detail=detail
            )
        verified = False
        verification_error: Exception | None = None
        try:
            day_statuses = await asyncio.to_thread(tier_status_day, store, day)
            verified = all(status == "data" for status in day_statuses.values())
        except Exception as error:
            verification_error = error
        if outcome == "written" and verified:
            assert source is not None
            return _source_result(
                source, outcome=outcome, parts=parts, rows=rows, written_bytes=written_bytes, detail=detail
            )
        if write_attempt >= config.retry_attempts:
            suffix = (
                f"; verification failed: {type(verification_error).__name__}: {verification_error}"
                if verification_error
                else ""
            )
            raise PipelineOperationError(
                f"fire-detections {day} did not publish a complete four-tier ladder after "
                f"{write_attempt} attempt(s): outcome={outcome}, detail={detail}{suffix}",
                code="ladder_incomplete",
                lane=FIRE_DETECTIONS_STREAM,
                stage="publication",
                retryable=True,
            )
        delay = retry_delay(write_attempt, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds)
        emit(
            {
                "event": "fire_detections_forward_r2_retry",
                "run_id": run_id,
                "day": day.isoformat(),
                "attempt": write_attempt,
                "outcome": outcome,
                "detail": detail,
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)
    raise AssertionError("bounded fire-detections publish attempts exhausted")


def _source_result(  # noqa: PLR0913
    source: FireDaySource, *, outcome: str, parts: int, rows: int, written_bytes: int, detail: str | None
) -> dict[str, object]:
    """Render source evidence captured inside the lane-day lock."""
    return {
        "day": source.day.isoformat(),
        "outcome": outcome,
        "raw_records": source.raw_records,
        "deduplicated_records": source.deduplicated_records,
        "source_products": list(source.source_products),
        "product_counts": dict(source.product_counts),
        "base_rows": source.table.num_rows,
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
    }


def _positive_int_env(name: str, default: int, *, maximum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        parsed = int(raw) if raw else default
    except ValueError:
        parsed = default
    parsed = max(1, parsed)
    return min(parsed, maximum) if maximum is not None else parsed


def _positive_float_env(name: str, default: float, *, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        parsed = float(raw) if raw else default
    except ValueError as error:
        raise PipelineOperationError(f"{name} must be a number, got {raw!r}", code="invalid_environment") from error
    if not math.isfinite(parsed) or not FIRE_DIRECT_MIN_DELAY_SECONDS <= parsed <= maximum:
        raise PipelineOperationError(
            f"{name} must be finite and between {FIRE_DIRECT_MIN_DELAY_SECONDS:g} and {maximum:g}, got {parsed!r}",
            code="invalid_environment",
        )
    return parsed


def _validate_config(config: FireForwardConfig) -> None:
    """Fail closed on ownership drift and every process-bound knob."""
    if config.forward_start_day != FIRE_DETECTIONS_DIRECT_WRITER_START_DAY:
        raise PipelineOperationError(
            f"the direct-writer boundary is pinned to {FIRE_DETECTIONS_DIRECT_WRITER_START_DAY}; "
            f"got {config.forward_start_day}",
            code="ownership_boundary",
            lane=FIRE_DETECTIONS_STREAM,
            stage="configuration",
        )
    bounds = (
        ("lookback_days", config.lookback_days, 1, FIRE_DIRECT_MAX_LOOKBACK_DAYS),
        ("max_records_per_day", config.max_records_per_day, 1, FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY),
        ("retry_attempts", config.retry_attempts, 1, FIRE_DIRECT_MAX_RETRY_ATTEMPTS),
    )
    for name, value, minimum, maximum in bounds:
        if not minimum <= value <= maximum:
            raise PipelineOperationError(
                f"{name} must be between {minimum} and {maximum}", code="invalid_configuration"
            )
    if not config.lookback_days <= config.max_days <= FIRE_DIRECT_MAX_DAYS:
        raise PipelineOperationError(
            f"max_days must cover the lookback and be at most {FIRE_DIRECT_MAX_DAYS}", code="invalid_configuration"
        )
    for name, float_value, float_maximum in (
        ("retry_base_seconds", config.retry_base_seconds, FIRE_DIRECT_MAX_RETRY_BASE_SECONDS),
        ("retry_max_seconds", config.retry_max_seconds, FIRE_DIRECT_MAX_RETRY_MAX_SECONDS),
        ("contention_timeout_seconds", config.contention_timeout_seconds, FIRE_DIRECT_MAX_CONTENTION_TIMEOUT_SECONDS),
    ):
        if not math.isfinite(float_value) or not FIRE_DIRECT_MIN_DELAY_SECONDS <= float_value <= float_maximum:
            raise PipelineOperationError(
                f"{name} must be finite and between {FIRE_DIRECT_MIN_DELAY_SECONDS:g} and {float_maximum:g}, "
                f"got {float_value!r}",
                code="invalid_configuration",
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise PipelineOperationError(
            "retry_max_seconds must be at least retry_base_seconds", code="invalid_configuration"
        )


def _optional_day_env(name: str) -> date | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise PipelineOperationError(f"{name} must be an ISO date, got {raw!r}", code="invalid_environment") from error


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only fire-detections CLI."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--bbox", default=None, help="west,south,east,north; defaults to INGEST_BBOX")
    built.add_argument("--run-id", default=None, help="correlate this turn's records; generated when omitted")
    built.add_argument(
        "--lookback-days",
        type=int,
        default=_positive_int_env(
            "FIRE_FORWARD_LOOKBACK_DAYS", FIRE_DIRECT_DEFAULT_LOOKBACK_DAYS, maximum=FIRE_DIRECT_MAX_LOOKBACK_DAYS
        ),
    )
    built.add_argument(
        "--max-days",
        type=int,
        default=_positive_int_env("FIRE_FORWARD_MAX_DAYS", FIRE_DIRECT_DEFAULT_MAX_DAYS),
    )
    built.add_argument(
        "--max-records-per-day",
        type=int,
        default=_positive_int_env("FIRE_FORWARD_MAX_RECORDS_PER_DAY", FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY),
    )
    built.add_argument(
        "--retry-attempts",
        type=int,
        default=_positive_int_env("FIRE_FORWARD_RETRY_ATTEMPTS", FIRE_DIRECT_DEFAULT_RETRY_ATTEMPTS),
    )
    built.add_argument(
        "--retry-base-seconds",
        type=float,
        default=_positive_float_env(
            "FIRE_FORWARD_RETRY_BASE_SECONDS",
            FIRE_DIRECT_DEFAULT_RETRY_BASE_SECONDS,
            maximum=FIRE_DIRECT_MAX_RETRY_BASE_SECONDS,
        ),
    )
    built.add_argument(
        "--retry-max-seconds",
        type=float,
        default=_positive_float_env(
            "FIRE_FORWARD_RETRY_MAX_SECONDS",
            FIRE_DIRECT_DEFAULT_RETRY_MAX_SECONDS,
            maximum=FIRE_DIRECT_MAX_RETRY_MAX_SECONDS,
        ),
    )
    built.add_argument(
        "--contention-timeout-seconds",
        type=float,
        default=_positive_float_env(
            "FIRE_FORWARD_CONTENTION_TIMEOUT_SECONDS",
            FIRE_DIRECT_DEFAULT_CONTENTION_TIMEOUT_SECONDS,
            maximum=FIRE_DIRECT_MAX_CONTENTION_TIMEOUT_SECONDS,
        ),
    )
    built.add_argument(
        "--forward-start-day",
        type=date.fromisoformat,
        default=_optional_day_env("FIRE_FORWARD_START_DAY") or FIRE_DETECTIONS_DIRECT_WRITER_START_DAY,
        help=f"pinned ownership boundary; must equal {FIRE_DETECTIONS_DIRECT_WRITER_START_DAY}",
    )
    built.add_argument(
        "--force-day",
        type=date.fromisoformat,
        default=None,
        help="re-publish one already-settled YYYY-MM-DD inside the bounded NRT lookback",
    )
    return built


def _parse_args(argv: Sequence[str] | None = None) -> FireForwardConfig:
    """Parse and validate CLI/environment settings at the operator boundary."""
    built = parser()
    arguments = built.parse_args(inline_bbox_value(list(argv) if argv is not None else sys.argv[1:]))
    bbox = resolve_bounded_bbox(arguments.bbox)
    if bbox is None:
        built.error("--bbox or INGEST_BBOX is required")
    lookback_days = max(1, min(FIRE_DIRECT_MAX_LOOKBACK_DAYS, arguments.lookback_days))
    config = FireForwardConfig(
        bbox=bbox,
        lookback_days=lookback_days,
        max_days=max(lookback_days, min(FIRE_DIRECT_MAX_DAYS, arguments.max_days)),
        max_records_per_day=max(1, min(FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY, arguments.max_records_per_day)),
        retry_attempts=max(1, min(FIRE_DIRECT_MAX_RETRY_ATTEMPTS, arguments.retry_attempts)),
        retry_base_seconds=arguments.retry_base_seconds,
        retry_max_seconds=arguments.retry_max_seconds,
        contention_timeout_seconds=arguments.contention_timeout_seconds,
        forward_start_day=arguments.forward_start_day,
        force_day=arguments.force_day,
        run_id=arguments.run_id,
    )
    try:
        _validate_config(config)
    except PipelineOperationError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal JSON report on stdout."""
    config = _parse_args(argv)
    try:
        report = await run_fire_forward(config)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = ["FireForwardConfig", "main", "parser", "run_fire_forward"]
