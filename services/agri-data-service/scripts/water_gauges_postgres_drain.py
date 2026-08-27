"""Resume the complete published water-gauges Postgres-to-Parquet drain."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    PartitionDayStatus,
    partition_day_statuses,
    try_parse_completion_marker_path,
)
from agri_data_service.pipeline.lanes.water_gauges import SOURCE_DAY_COUNTS_SQL
from agri_data_service.pipeline.parquet.gap_fill import (
    fill_one_lane_day,
    postgres_lane_day_lock,
    statement_timeout,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, LaneRegistration
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

LAYER: Final = "water-gauges"
KIND: Final = "observed"
BULK_STATEMENT_TIMEOUT_SECONDS: Final = 600
DEFAULT_MAX_DAY_ATTEMPTS: Final = 5
MAX_DAY_ATTEMPTS: Final = 10
DEFAULT_RETRY_BASE_SECONDS: Final = 2.0
MAX_RETRY_BASE_SECONDS: Final = 60.0
DEFAULT_CONTENTION_POLL_SECONDS: Final = 15.0
MAX_CONTENTION_POLL_SECONDS: Final = 60.0
DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 900.0
MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0


@dataclass(frozen=True, slots=True)
class BaseDay:
    """One real Postgres source day's current z13 publication state."""

    day: date
    source_rows: int
    status: PartitionDayStatus
    parquet_rows: int | None

    @property
    def needs_fill(self) -> bool:
        """Whether the source day is missing, unfinished, or count-stale at z13."""
        return self.status != "data" or self.parquet_rows != self.source_rows


def emit(event: str, **fields: object) -> None:
    """Write one stable JSON progress record."""
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), sort_keys=True), flush=True)


def parse_day(value: str) -> date:
    """Parse one explicit publisher-named repair day."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, received {value!r}") from error


def arguments() -> argparse.Namespace:
    """Parse explicit content-repair and bounded retry controls."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-day", action="append", default=[], type=parse_day)
    parser.add_argument("--force-from", type=parse_day)
    parser.add_argument("--force-through", type=parse_day)
    parser.add_argument("--max-day-attempts", type=int, default=DEFAULT_MAX_DAY_ATTEMPTS)
    parser.add_argument("--retry-base-seconds", type=float, default=DEFAULT_RETRY_BASE_SECONDS)
    parser.add_argument("--contention-poll-seconds", type=float, default=DEFAULT_CONTENTION_POLL_SECONDS)
    parser.add_argument("--contention-timeout-seconds", type=float, default=DEFAULT_CONTENTION_TIMEOUT_SECONDS)
    parsed = parser.parse_args()
    if (parsed.force_from is None) != (parsed.force_through is None):
        parser.error("--force-from and --force-through must be supplied together")
    if parsed.force_from is not None and parsed.force_from > parsed.force_through:
        parser.error("--force-from must not be after --force-through")
    if not 1 <= parsed.max_day_attempts <= MAX_DAY_ATTEMPTS:
        parser.error(f"--max-day-attempts must be between 1 and {MAX_DAY_ATTEMPTS}")
    if not 0 < parsed.retry_base_seconds <= MAX_RETRY_BASE_SECONDS:
        parser.error(f"--retry-base-seconds must be within (0, {MAX_RETRY_BASE_SECONDS:g}]")
    if not 0 < parsed.contention_poll_seconds <= MAX_CONTENTION_POLL_SECONDS:
        parser.error(f"--contention-poll-seconds must be within (0, {MAX_CONTENTION_POLL_SECONDS:g}]")
    if not 0 < parsed.contention_timeout_seconds <= MAX_CONTENTION_TIMEOUT_SECONDS:
        parser.error(f"--contention-timeout-seconds must be within (0, {MAX_CONTENTION_TIMEOUT_SECONDS:g}]")
    return parsed


async def read_source_day_counts(session: AsyncSession) -> dict[date, int]:
    """Count the exact published Postgres population on its publisher-named day axis."""
    await session.execute(statement_timeout(BULK_STATEMENT_TIMEOUT_SECONDS))
    result = await session.execute(SOURCE_DAY_COUNTS_SQL)
    counts: dict[date, int] = {}
    for row in result.mappings():
        observed_day = row["observed_day"]
        if not isinstance(observed_day, date):
            raise RuntimeError("a published water-gauges row has no publisher-named observation day")
        counts[observed_day] = int(row["row_count"])
    await session.rollback()
    if not counts:
        raise RuntimeError("production Postgres returned no published water-gauges source days")
    return counts


def read_completion_counts(
    backend: BotoObjectStoreBackend,
    store: ObjectStore,
    keys: tuple[str, ...],
    source_days: set[date],
) -> dict[date, int]:
    """Read z13 completion receipts for real source days only."""
    counts: dict[date, int] = {}
    for relative_key in keys:
        marker = try_parse_completion_marker_path(relative_key)
        if marker is None or marker.day not in source_days:
            continue
        payload = backend.get(store.key_for(relative_key))
        if payload is None:
            raise RuntimeError(f"listed completion marker disappeared before census: {relative_key}")
        completion = PartitionCompletion.from_json_bytes(payload)
        counts[marker.day] = completion.row_count
    return counts


def read_base_days(
    backend: BotoObjectStoreBackend,
    store: ObjectStore,
    source_counts: dict[date, int],
) -> tuple[BaseDay, ...]:
    """Join actual Postgres source days to their z13 status and completion row count."""
    source_days = set(source_counts)
    first_day = min(source_days)
    last_day = max(source_days)
    keys = store.list_partition_keys(LAYER, KIND, BASE_ZOOM_TIER)
    statuses = partition_day_statuses(
        layer=LAYER,
        kind=KIND,
        zoom=BASE_ZOOM_TIER,
        first_day=first_day,
        last_day=last_day,
        keys=keys,
    )
    completion_counts = read_completion_counts(backend, store, keys, source_days)
    days = tuple(
        BaseDay(
            day=day,
            source_rows=source_counts[day],
            status=statuses[day],
            parquet_rows=completion_counts.get(day),
        )
        for day in sorted(source_days)
    )
    markerless_data = [item.day.isoformat() for item in days if item.status == "data" and item.parquet_rows is None]
    if markerless_data:
        raise RuntimeError(f"data days had no readable completion receipt: {markerless_data[:10]}")
    return days


def census_payload(days: tuple[BaseDay, ...]) -> dict[str, object]:
    """Render the source and z13 census without dumping the full 1,520-day set."""
    status_counts = Counter(item.status for item in days)
    stale = [item for item in days if item.status == "data" and item.parquet_rows != item.source_rows]
    missing = [item for item in days if item.status in {"missing", "incomplete"}]
    unsafe = [item for item in days if item.status in {"absent", "conflict"}]
    return {
        "source": {
            "days": len(days),
            "rows": sum(item.source_rows for item in days),
            "first_day": days[0].day.isoformat(),
            "last_day": days[-1].day.isoformat(),
        },
        "z13": {
            "statuses": dict(sorted(status_counts.items())),
            "rows_from_completion_receipts": sum(item.parquet_rows or 0 for item in days if item.status == "data"),
            "missing_or_incomplete_days": len(missing),
            "stale_days": len(stale),
            "unsafe_days": len(unsafe),
        },
        "targets": len(missing) + len(stale),
        "stale": [
            {"day": item.day.isoformat(), "source_rows": item.source_rows, "parquet_rows": item.parquet_rows}
            for item in stale
        ],
        "unsafe": [{"day": item.day.isoformat(), "status": item.status} for item in unsafe],
    }


async def fill_targets(  # noqa: PLR0913 - bounded operator controls stay explicit
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    days: tuple[BaseDay, ...],
    *,
    run_id: str,
    force_days: frozenset[date],
    max_day_attempts: int,
    retry_base_seconds: float,
    contention_poll_seconds: float,
    contention_timeout_seconds: float,
) -> Counter[str]:
    """Fill every selected source day with bounded retries and contention polling."""
    outcomes: Counter[str] = Counter()
    available = {item.day for item in days}
    unknown = sorted(force_days - available)
    if unknown:
        raise RuntimeError(f"forced repair days are absent from PostgreSQL: {[day.isoformat() for day in unknown]}")
    targets = tuple(item for item in days if item.needs_fill or item.day in force_days)
    for index, item in enumerate(targets, start=1):
        failed_attempts = 0
        attempts = 0
        contention_started = time.monotonic()
        while True:
            attempts += 1
            try:
                outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                    session,
                    store,
                    lane,
                    day=item.day,
                    run_id=run_id,
                    now=lambda: datetime.now(UTC),
                    today=datetime.now(UTC).date(),
                    lane_day_lock=postgres_lane_day_lock,
                    statement_timeout_seconds=BULK_STATEMENT_TIMEOUT_SECONDS,
                )
            except Exception as error:
                outcome, parts, rows, written_bytes = "raised", 0, 0, 0
                detail = f"{type(error).__name__}: {error}"
            emit(
                "water_gauges_day_attempt",
                index=index,
                total=len(targets),
                day=item.day.isoformat(),
                forced=item.day in force_days,
                attempt=attempts,
                previous_status=item.status,
                previous_rows=item.parquet_rows,
                source_rows=item.source_rows,
                outcome=outcome,
                parts=parts,
                rows=rows,
                bytes=written_bytes,
                detail=detail,
            )
            if outcome == "written":
                outcomes[outcome] += 1
                break
            if outcome == "contended":
                if time.monotonic() - contention_started >= contention_timeout_seconds:
                    raise RuntimeError(f"{item.day}: lane lock remained contended after {attempts} attempts")
                await asyncio.sleep(contention_poll_seconds)
                continue
            if outcome == "raised":
                failed_attempts += 1
                if failed_attempts < max_day_attempts:
                    await asyncio.sleep(min(retry_base_seconds * (2 ** (failed_attempts - 1)), 60.0))
                    continue
            raise RuntimeError(f"{item.day}: repair ended outcome={outcome}: {detail}")
        emit(
            "water_gauges_day_checkpoint",
            index=index,
            total=len(targets),
            day=item.day.isoformat(),
            outcome="written",
            attempts=attempts,
            rows=rows,
            bytes=written_bytes,
        )
    return outcomes


async def main() -> int:
    """Run the mutating drain and prove final z13 row-count parity."""
    args = arguments()
    credentials = settings.require_object_store()
    backend = BotoObjectStoreBackend.from_credentials(credentials)
    store = ObjectStore(backend, prefix=settings.object_store_prefix)
    lane = LANE_REGISTRY[LAYER]
    database_url = settings.require_local_source_loader_database_url()
    run_id = f"water-gauges-postgres-drain-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    force_days = set(args.force_day)
    if args.force_from is not None:
        span = (args.force_through - args.force_from).days
        force_days.update(
            args.force_from.fromordinal(args.force_from.toordinal() + offset) for offset in range(span + 1)
        )

    async with local_source_loader_session(database_url) as session:
        source_counts = await read_source_day_counts(session)
        before = read_base_days(backend, store, source_counts)
        before_payload = census_payload(before)
        emit("water_gauges_census_before", run_id=run_id, **before_payload)
        unsafe = [item for item in before if item.status in {"absent", "conflict"}]
        if unsafe:
            raise RuntimeError(
                "actual Postgres source days include governed absence or conflict states; refusing to retract them"
            )
        emit(
            "water_gauges_repair_selection",
            run_id=run_id,
            forced_days=len(force_days),
            forced_first=min(force_days).isoformat() if force_days else None,
            forced_last=max(force_days).isoformat() if force_days else None,
        )
        outcomes = await fill_targets(
            session,
            store,
            lane,
            before,
            run_id=run_id,
            force_days=frozenset(force_days),
            max_day_attempts=args.max_day_attempts,
            retry_base_seconds=args.retry_base_seconds,
            contention_poll_seconds=args.contention_poll_seconds,
            contention_timeout_seconds=args.contention_timeout_seconds,
        )
        latest_source_counts = await read_source_day_counts(session)

    after = read_base_days(backend, store, latest_source_counts)
    after_payload = census_payload(after)
    emit("water_gauges_census_after", run_id=run_id, outcomes=dict(sorted(outcomes.items())), **after_payload)
    clean = all(item.status == "data" and item.parquet_rows == item.source_rows for item in after)
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
