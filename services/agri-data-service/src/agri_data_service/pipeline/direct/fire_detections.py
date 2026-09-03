"""Fetch settled NASA FIRMS days and publish the fire-detections Parquet ladder directly."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import (
    PartitionDayStatus,
    partition_day_statuses,
)
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.firms import (
    FIRMS_BOUNDS,
    build_fire_detection_write,
    collapse_history_records,
    fetch_active_fires,
    fetch_product_availability,
    products_covering_span,
)
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.fire_detections import FIRE_DETECTIONS_DIRECT_WRITER_START_DAY
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import (
    LANE_REGISTRY,
    LaneRegistration,
    LaneRunResult,
    normalise_export_outcome,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.fire_detections import (
    FIRE_DETECTIONS_CELL_SIZE_DEGREES,
    FIRE_DETECTIONS_SCHEMA,
    FIRE_DETECTIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

FIRE_DIRECT_KIND: Final = "observed"
FIRE_DIRECT_ALL_TIERS: Final[tuple[ZoomTier, ...]] = (
    LANE_BASE_ZOOM_TIER,
    *DERIVED_ZOOM_TIERS,
)
FIRE_DIRECT_RUN_ID_PREFIX: Final = "fire-detections-forward:"
FIRE_DIRECT_DEFAULT_LOOKBACK_DAYS: Final = 5
FIRE_DIRECT_MAX_LOOKBACK_DAYS: Final = 5
FIRE_DIRECT_DEFAULT_MAX_DAYS: Final = 5
FIRE_DIRECT_MAX_DAYS: Final = 5
FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY: Final = 50_000
FIRE_DIRECT_DEFAULT_RETRY_ATTEMPTS: Final = 8
FIRE_DIRECT_MAX_RETRY_ATTEMPTS: Final = 20
FIRE_DIRECT_DEFAULT_RETRY_BASE_SECONDS: Final = 5.0
FIRE_DIRECT_DEFAULT_RETRY_MAX_SECONDS: Final = 60.0
FIRE_DIRECT_DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 900.0
FIRE_DIRECT_MAX_RETRY_BASE_SECONDS: Final = 60.0
FIRE_DIRECT_MAX_RETRY_MAX_SECONDS: Final = 300.0
FIRE_DIRECT_MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
FIRE_DIRECT_STATEMENT_TIMEOUT_SECONDS: Final = 120
FIRE_DIRECT_MIN_DELAY_SECONDS: Final = 0.1
MONTHS_PER_YEAR: Final = 12
POINT_COORDINATE_COUNT: Final = 2
_CELL_SIZE: Final = Decimal(str(FIRE_DETECTIONS_CELL_SIZE_DEGREES))


class DirectFireDetectionsError(RuntimeError):
    """Raised when a source day cannot support a complete direct Parquet publication."""


@dataclass(slots=True)
class DirectFireDetectionsAdapter:
    """Fetch and write one base table while the caller holds the shared lane-day lock."""

    fetch_source: Callable[[], Awaitable[FireDaySource]]
    source: FireDaySource | None = field(default=None, init=False)

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
            raise DirectFireDetectionsError(f"the fetch closure for {day} returned source day {source.day}")
        self.source = source
        if source.table.num_rows == 0:
            return normalise_export_outcome(
                store.write_absence(
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
                    zoom=LANE_BASE_ZOOM_TIER,
                    day=day,
                )
            )
        # EVERY TIER, not only the base rung. An absence is propagated up the whole ladder, so a
        # base-only retraction leaves the three coarse rungs asserting a governed absence over a day
        # that now carries rows -- a `conflict` at three rungs out of four. `gap_fill` heals it on a
        # later tick, but only after a tick spent on a day that was already correct.
        retracted = tuple(
            tier for tier in ZOOM_TIERS if store.absence_exists(FIRE_DETECTIONS_STREAM, FIRE_DIRECT_KIND, tier, day)
        )
        if retracted:
            for tier in retracted:
                store.clear_absence_marker(FIRE_DETECTIONS_STREAM, FIRE_DIRECT_KIND, tier, day)
            _emit(
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
                source.table,
                layer=FIRE_DETECTIONS_STREAM,
                kind=FIRE_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=day,
            )
        )


@dataclass(frozen=True, slots=True)
class FireForwardConfig:
    """Bound every source request, run, retry series, and contention wait."""

    bbox: str
    lookback_days: int
    max_days: int
    max_records_per_day: int
    retry_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    contention_timeout_seconds: float
    forward_start_day: date = FIRE_DETECTIONS_DIRECT_WRITER_START_DAY
    force_day: date | None = None


@dataclass(frozen=True, slots=True)
class FireDaySource:
    """The complete, deduplicated FIRMS answer used to author one UTC day."""

    day: date
    raw_records: int
    deduplicated_records: int
    source_products: tuple[str, ...]
    product_counts: Mapping[str, int]
    table: pa.Table


@dataclass(slots=True)
class FireCellTotals:
    """Typed additive values for one 0.005-degree fire cell."""

    newest_observed_at: datetime | None
    detection_count: int = 0
    frp_sum: Decimal = Decimal("0")
    frp_observation_count: int = 0
    high_confidence_detection_count: int = 0


def fire_table_from_features(  # noqa: PLR0912, PLR0913
    features: Sequence[Mapping[str, object]],
    *,
    day: date,
    fetched_at: datetime,
    max_records: int,
    raw_record_count: int | None = None,
    source_products: tuple[str, ...] = (),
    product_counts: Mapping[str, int] | None = None,
) -> FireDaySource:
    """Conform one complete source day to the registered 0.005-degree cell-day schema."""
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise DirectFireDetectionsError("the source fetch clock must include a timezone")
    actual_raw_records = len(features) if raw_record_count is None else raw_record_count
    if actual_raw_records > max_records:
        raise DirectFireDetectionsError(
            f"FIRMS returned {actual_raw_records} records for {day}, over the fail-closed {max_records}-record day cap"
        )

    writes = {}
    rejected = 0
    for feature in features:
        write = build_fire_detection_write(feature, FIRE_DETECTIONS_STREAM, None, fetched_at)
        if write is None:
            rejected += 1
            continue
        observed_at = write.identity.observed_at
        if observed_at is None or observed_at.astimezone(UTC).date() != day:
            raise DirectFireDetectionsError(
                f"the exact-day FIRMS response for {day} contained detection {write.external_id!r} from {observed_at}"
            )
        writes[write.external_id] = write
    if rejected:
        raise DirectFireDetectionsError(
            f"FIRMS returned {rejected} unkeyable or invalid records for {day}; refusing a partial day"
        )

    cells: dict[tuple[Decimal, Decimal], FireCellTotals] = {}
    for write in writes.values():
        geometry = write.properties.get("geometry")
        coordinates = geometry.get("coordinates") if isinstance(geometry, Mapping) else None
        if not isinstance(coordinates, list) or len(coordinates) < POINT_COORDINATE_COUNT:
            raise DirectFireDetectionsError(f"FIRMS detection {write.external_id!r} has no point coordinates")
        longitude = _finite_decimal(coordinates[0], field="longitude", external_id=write.external_id)
        latitude = _finite_decimal(coordinates[1], field="latitude", external_id=write.external_id)
        cell = (_floor_cell(longitude), _floor_cell(latitude))
        candidate_observed_at = write.identity.observed_at
        if candidate_observed_at is None:
            raise DirectFireDetectionsError(
                f"validated FIRMS detection {write.external_id!r} lost its observation timestamp"
            )
        held = cells.setdefault(
            cell,
            FireCellTotals(newest_observed_at=candidate_observed_at),
        )
        held.detection_count += 1
        frp = write.properties.get("frp")
        if isinstance(frp, int | float) and not isinstance(frp, bool):
            held.frp_sum += _finite_decimal(frp, field="frp", external_id=write.external_id)
            held.frp_observation_count += 1
        if write.properties.get("confidenceNormalized") == "high":
            held.high_confidence_detection_count += 1
        held_observed_at = held.newest_observed_at
        if held_observed_at is None or candidate_observed_at > held_observed_at:
            held.newest_observed_at = candidate_observed_at

    rows = []
    for (longitude, latitude), aggregate in sorted(cells.items()):
        frp_count = aggregate.frp_observation_count
        rows.append(
            {
                "cell_longitude": float(longitude),
                "cell_latitude": float(latitude),
                "observed_day": day,
                "detection_count": aggregate.detection_count,
                "frp_sum": float(aggregate.frp_sum) if frp_count else None,
                "frp_observation_count": frp_count,
                "high_confidence_detection_count": aggregate.high_confidence_detection_count,
                "newest_observed_at": aggregate.newest_observed_at,
            }
        )
    table = pa.Table.from_pylist(rows, schema=FIRE_DETECTIONS_SCHEMA.arrow_schema)
    return FireDaySource(
        day=day,
        raw_records=actual_raw_records,
        deduplicated_records=len(writes),
        source_products=source_products,
        product_counts={} if product_counts is None else dict(product_counts),
        table=table,
    )


async def fetch_fire_day(  # noqa: PLR0913
    *,
    day: date,
    bbox: str,
    max_records: int,
    retry_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> FireDaySource:
    """Fetch every NRT constellation member for one exact settled day, retrying as one unit."""

    async def fetch_once() -> FireDaySource:
        async with upstream_client(FIRMS_BOUNDS) as client:
            availability = await fetch_product_availability(client)
            products = products_covering_span(availability, day, 1)
            if not products:
                raise DirectFireDetectionsError(f"FIRMS availability lists no product covering settled day {day}")
            answers = await asyncio.gather(
                *(fetch_active_fires(client, bbox, 1, product, day) for product in products),
                return_exceptions=True,
            )
        failures: list[str] = []
        successful_answers: list[tuple[str, list[dict[str, object]]]] = []
        for product, answer in zip(products, answers, strict=True):
            if isinstance(answer, BaseException):
                failures.append(f"{product}: {type(answer).__name__}")
            else:
                successful_answers.append((product, answer))
        if failures:
            raise DirectFireDetectionsError(
                f"FIRMS did not return the complete constellation for {day}: {'; '.join(failures)}"
            )
        product_counts = {product: len(answer) for product, answer in successful_answers}
        raw_features = [feature for _product, answer in successful_answers for feature in answer]
        features = collapse_history_records(raw_features)
        return fire_table_from_features(
            features,
            day=day,
            fetched_at=datetime.now(UTC),
            max_records=max_records,
            raw_record_count=len(raw_features),
            source_products=tuple(products),
            product_counts=product_counts,
        )

    return await _retry_async(
        f"FIRMS {day.isoformat()} source fetch",
        fetch_once,
        attempts=retry_attempts,
        base_seconds=retry_base_seconds,
        max_seconds=retry_max_seconds,
    )


async def run_fire_forward(config: FireForwardConfig) -> dict[str, object]:
    """Refresh a bounded newest-first slice of settled FIRMS days."""
    _validate_config(config)
    run_id = f"{FIRE_DIRECT_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = datetime.now(UTC).date()
    lane = LANE_REGISTRY[FIRE_DETECTIONS_STREAM]
    # ONE TALLY FOR THE WHOLE RUN, on EVERY report this function can return. Without it every
    # availability verdict lands only in a day's detail string -- and `ladder_incomplete` and
    # `retry_claim_failed` both mean a day that is in the bucket and permanently outside the index.
    availability = AvailabilityExtensionTally()
    settled_through = today - timedelta(days=lane.publication_lag_days)
    first_day = settled_through - timedelta(days=config.lookback_days - 1)
    first_day = max(first_day, config.forward_start_day)
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
        _emit({"event": "fire_detections_forward_noop", **report})
        return report
    store = ObjectStore.from_settings()
    # Beside the object store, so an unwired bucket fails identically and nothing opens a socket here.
    availability_storage = BotoAvailabilityStorage.from_settings()
    statuses = await _retry_async(
        "initial fire-detections R2 census",
        lambda: asyncio.to_thread(_tier_status_window, store, first_day, settled_through),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    if config.force_day is not None:
        if not first_day <= config.force_day <= settled_through:
            raise DirectFireDetectionsError(
                f"forced day {config.force_day} must be settled and inside the bounded NRT window "
                f"{first_day}..{settled_through}"
            )
        pending: tuple[date, ...] = (config.force_day,)
    else:
        _pending_days(statuses)
        pending = tuple(reversed(tuple(statuses[LANE_BASE_ZOOM_TIER])))[: config.max_days]
    _emit(
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
    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
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
            _emit({"event": "fire_detections_forward_day_complete", "run_id": run_id, **result})

    final_statuses = await _retry_async(
        "final fire-detections R2 census",
        lambda: asyncio.to_thread(_tier_status_window, store, first_day, settled_through),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    window_backlog = _pending_days(final_statuses)
    selected = set(pending)
    remaining = tuple(day for day in window_backlog if day in selected)
    if remaining:
        raise DirectFireDetectionsError(
            f"the bounded forward window still has {len(remaining)} unfilled day(s): "
            f"{', '.join(day.isoformat() for day in remaining)}"
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
        "tier_status_counts": _tier_status_counts(final_statuses),
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
            raise DirectFireDetectionsError(
                f"lane-day contention for {day} exceeded {config.contention_timeout_seconds:g}s"
            )
        delay = min(
            remaining,
            _retry_delay(
                1,
                base_seconds=config.retry_base_seconds,
                max_seconds=config.retry_max_seconds,
            ),
        )
        _emit(
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
        direct_lane = replace(lane, adapter=adapter)
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                direct_lane,
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
            raise DirectFireDetectionsError(
                f"fire-detections {day} returned {outcome} without a completed locked source fetch"
            )
        if outcome in {"blocked", "absent"}:
            if outcome == "blocked":
                raise DirectFireDetectionsError(detail or f"fire-detections {day} is blocked")
            assert source is not None
            return _source_result(
                source,
                outcome=outcome,
                parts=parts,
                rows=rows,
                written_bytes=written_bytes,
                detail=detail,
            )

        verified = False
        verification_error: Exception | None = None
        try:
            day_statuses = await asyncio.to_thread(_tier_status_day, store, day)
            verified = all(status == "data" for status in day_statuses.values())
        except Exception as error:
            verification_error = error
        if outcome == "written" and verified:
            assert source is not None
            return _source_result(
                source,
                outcome=outcome,
                parts=parts,
                rows=rows,
                written_bytes=written_bytes,
                detail=detail,
            )
        if write_attempt >= config.retry_attempts:
            suffix = (
                f"; verification failed: {type(verification_error).__name__}: {verification_error}"
                if verification_error is not None
                else ""
            )
            raise DirectFireDetectionsError(
                f"fire-detections {day} did not publish a complete four-tier ladder after "
                f"{write_attempt} attempt(s): outcome={outcome}, detail={detail}{suffix}"
            )
        delay = _retry_delay(
            write_attempt,
            base_seconds=config.retry_base_seconds,
            max_seconds=config.retry_max_seconds,
        )
        _emit(
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
    source: FireDaySource,
    *,
    outcome: str,
    parts: int,
    rows: int,
    written_bytes: int,
    detail: str | None,
) -> dict[str, object]:
    """Render the source evidence captured inside the lane-day lock."""
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


def _tier_status_window(
    store: ObjectStore,
    first_day: date,
    last_day: date,
) -> dict[ZoomTier, dict[date, PartitionDayStatus]]:
    keys_by_tier: dict[ZoomTier, list[str]] = {tier: [] for tier in FIRE_DIRECT_ALL_TIERS}
    cursor = date(first_day.year, first_day.month, 1)
    while cursor <= last_day:
        for tier in FIRE_DIRECT_ALL_TIERS:
            keys_by_tier[tier].extend(
                store.list_partition_keys(
                    FIRE_DETECTIONS_STREAM,
                    FIRE_DIRECT_KIND,
                    tier,
                    year=cursor.year,
                    month=cursor.month,
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


def _tier_status_day(store: ObjectStore, day: date) -> dict[ZoomTier, PartitionDayStatus]:
    return {
        tier: partition_day_statuses(
            layer=FIRE_DETECTIONS_STREAM,
            kind=FIRE_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(
                FIRE_DETECTIONS_STREAM,
                FIRE_DIRECT_KIND,
                tier,
                year=day.year,
                month=day.month,
            ),
        )[day]
        for tier in FIRE_DIRECT_ALL_TIERS
    }


def _pending_days(
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
) -> tuple[date, ...]:
    first_tier = FIRE_DIRECT_ALL_TIERS[0]
    days = tuple(statuses[first_tier])
    pending: list[date] = []
    for day in reversed(days):
        rung = {tier: statuses[tier][day] for tier in FIRE_DIRECT_ALL_TIERS}
        if "conflict" in rung.values():
            raise DirectFireDetectionsError(f"fire-detections {day} has a data/absence conflict: {rung}")
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            if any(rung[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
                raise DirectFireDetectionsError(
                    f"fire-detections {day} is absent at z{LANE_BASE_ZOOM_TIER} but carries derived parts: {rung}"
                )
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return tuple(pending)


def _tier_status_counts(
    statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]],
) -> dict[str, dict[str, int]]:
    return {
        f"z{tier}": {
            status: sum(1 for held in by_day.values() if held == status)
            for status in ("data", "absent", "missing", "incomplete", "conflict")
        }
        for tier, by_day in statuses.items()
    }


async def _retry_async[T](
    label: str,
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_seconds: float,
    max_seconds: float,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as error:
            last_error = error
            if attempt >= attempts:
                break
            delay = _retry_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds)
            _emit(
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
    raise DirectFireDetectionsError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


def _floor_cell(value: Decimal) -> Decimal:
    return (value / _CELL_SIZE).to_integral_value(rounding=ROUND_FLOOR) * _CELL_SIZE


def _finite_decimal(value: object, *, field: str, external_id: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise DirectFireDetectionsError(f"FIRMS detection {external_id!r} has an invalid {field}: {value!r}") from error
    if not parsed.is_finite():
        raise DirectFireDetectionsError(f"FIRMS detection {external_id!r} has a non-finite {field}: {value!r}")
    return parsed


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
        raise DirectFireDetectionsError(f"{name} must be a number, got {raw!r}") from error
    if not math.isfinite(parsed) or not FIRE_DIRECT_MIN_DELAY_SECONDS <= parsed <= maximum:
        raise DirectFireDetectionsError(
            f"{name} must be finite and between {FIRE_DIRECT_MIN_DELAY_SECONDS:g} and {maximum:g}, got {parsed!r}"
        )
    return parsed


def _validate_config(config: FireForwardConfig) -> None:
    """Fail closed on ownership drift and every process-bound knob."""
    if config.forward_start_day != FIRE_DETECTIONS_DIRECT_WRITER_START_DAY:
        raise DirectFireDetectionsError(
            f"the direct-writer boundary is pinned to {FIRE_DETECTIONS_DIRECT_WRITER_START_DAY}; "
            f"got {config.forward_start_day}"
        )
    if not 1 <= config.lookback_days <= FIRE_DIRECT_MAX_LOOKBACK_DAYS:
        raise DirectFireDetectionsError(f"lookback_days must be between 1 and {FIRE_DIRECT_MAX_LOOKBACK_DAYS}")
    if not config.lookback_days <= config.max_days <= FIRE_DIRECT_MAX_DAYS:
        raise DirectFireDetectionsError(f"max_days must cover the lookback and be at most {FIRE_DIRECT_MAX_DAYS}")
    if not 1 <= config.max_records_per_day <= FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY:
        raise DirectFireDetectionsError(
            f"max_records_per_day must be between 1 and {FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY}"
        )
    if not 1 <= config.retry_attempts <= FIRE_DIRECT_MAX_RETRY_ATTEMPTS:
        raise DirectFireDetectionsError(f"retry_attempts must be between 1 and {FIRE_DIRECT_MAX_RETRY_ATTEMPTS}")
    float_bounds = {
        "retry_base_seconds": (config.retry_base_seconds, FIRE_DIRECT_MAX_RETRY_BASE_SECONDS),
        "retry_max_seconds": (config.retry_max_seconds, FIRE_DIRECT_MAX_RETRY_MAX_SECONDS),
        "contention_timeout_seconds": (
            config.contention_timeout_seconds,
            FIRE_DIRECT_MAX_CONTENTION_TIMEOUT_SECONDS,
        ),
    }
    for name, (value, maximum) in float_bounds.items():
        if not math.isfinite(value) or not FIRE_DIRECT_MIN_DELAY_SECONDS <= value <= maximum:
            raise DirectFireDetectionsError(
                f"{name} must be finite and between {FIRE_DIRECT_MIN_DELAY_SECONDS:g} and {maximum:g}, got {value!r}"
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise DirectFireDetectionsError("retry_max_seconds must be at least retry_base_seconds")


def _optional_day_env(name: str) -> date | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise DirectFireDetectionsError(f"{name} must be an ISO date, got {raw!r}") from error


def _parse_args(argv: Sequence[str] | None = None) -> FireForwardConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    forward_start_default = _optional_day_env("FIRE_FORWARD_START_DAY")
    if forward_start_default is None:
        forward_start_default = FIRE_DETECTIONS_DIRECT_WRITER_START_DAY
    parser.add_argument("--bbox", default=None, help="west,south,east,north; defaults to INGEST_BBOX")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=_positive_int_env(
            "FIRE_FORWARD_LOOKBACK_DAYS",
            FIRE_DIRECT_DEFAULT_LOOKBACK_DAYS,
            maximum=FIRE_DIRECT_MAX_LOOKBACK_DAYS,
        ),
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=_positive_int_env("FIRE_FORWARD_MAX_DAYS", FIRE_DIRECT_DEFAULT_MAX_DAYS),
    )
    parser.add_argument(
        "--max-records-per-day",
        type=int,
        default=_positive_int_env("FIRE_FORWARD_MAX_RECORDS_PER_DAY", FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY),
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=_positive_int_env("FIRE_FORWARD_RETRY_ATTEMPTS", FIRE_DIRECT_DEFAULT_RETRY_ATTEMPTS),
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=_positive_float_env(
            "FIRE_FORWARD_RETRY_BASE_SECONDS",
            FIRE_DIRECT_DEFAULT_RETRY_BASE_SECONDS,
            maximum=FIRE_DIRECT_MAX_RETRY_BASE_SECONDS,
        ),
    )
    parser.add_argument(
        "--retry-max-seconds",
        type=float,
        default=_positive_float_env(
            "FIRE_FORWARD_RETRY_MAX_SECONDS",
            FIRE_DIRECT_DEFAULT_RETRY_MAX_SECONDS,
            maximum=FIRE_DIRECT_MAX_RETRY_MAX_SECONDS,
        ),
    )
    parser.add_argument(
        "--contention-timeout-seconds",
        type=float,
        default=_positive_float_env(
            "FIRE_FORWARD_CONTENTION_TIMEOUT_SECONDS",
            FIRE_DIRECT_DEFAULT_CONTENTION_TIMEOUT_SECONDS,
            maximum=FIRE_DIRECT_MAX_CONTENTION_TIMEOUT_SECONDS,
        ),
    )
    parser.add_argument(
        "--forward-start-day",
        type=date.fromisoformat,
        default=forward_start_default,
        help=f"pinned ownership boundary; must equal {FIRE_DETECTIONS_DIRECT_WRITER_START_DAY}",
    )
    parser.add_argument(
        "--force-day",
        type=date.fromisoformat,
        default=None,
        help="re-publish one already-settled YYYY-MM-DD inside the bounded NRT lookback",
    )
    arguments = parser.parse_args(argv)
    bbox = resolve_bounded_bbox(arguments.bbox)
    if bbox is None:
        parser.error("--bbox or INGEST_BBOX is required")
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
    )
    try:
        _validate_config(config)
    except DirectFireDetectionsError as error:
        parser.error(str(error))
    return config


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


async def _main(argv: Sequence[str] | None = None) -> int:
    config = _parse_args(argv)
    try:
        report = await run_fire_forward(config)
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(error).__name__}: {error}"},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))


__all__ = [
    "DirectFireDetectionsAdapter",
    "DirectFireDetectionsError",
    "FireDaySource",
    "FireForwardConfig",
    "fetch_fire_day",
    "fire_table_from_features",
    "run_fire_forward",
]
