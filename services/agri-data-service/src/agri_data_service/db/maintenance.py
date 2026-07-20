"""Narrow operational maintenance for Alembic-owned database objects."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

_PARTITION_NAME = re.compile(r"^job_event_p(?P<day>\d{8})$")
_ADVISORY_LOCK_KEY = "agri.job_event_partition_maintenance.v1"
_MAX_RETENTION_DAYS = 365
_MAX_FUTURE_DAYS = 31


class MaintenanceBusyError(RuntimeError):
    """Another partition-maintenance transaction already owns the fence."""


@dataclass(frozen=True)
class JobEventMaintenanceResult:
    """Machine-readable maintenance outcome."""

    retained_from: date
    covered_through: date
    created_partitions: int
    moved_default_rows: int
    dropped_partitions: int
    deleted_expired_default_rows: int
    default_rows_remaining: int

    def to_dict(self) -> dict[str, str | int]:
        return {key: value.isoformat() if isinstance(value, date) else value for key, value in asdict(self).items()}


def partition_days(
    *,
    now: datetime,
    retention_days: int,
    future_days: int,
) -> tuple[date, ...]:
    """Return every UTC day that must have an attached hot partition."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not 1 <= retention_days <= _MAX_RETENTION_DAYS:
        raise ValueError("retention_days must be between 1 and 365")
    if not 1 <= future_days <= _MAX_FUTURE_DAYS:
        raise ValueError("future_days must be between 1 and 31")

    current_day = now.astimezone(UTC).date()
    first_day = current_day - timedelta(days=retention_days - 1)
    final_day = current_day + timedelta(days=future_days)
    return tuple(first_day + timedelta(days=offset) for offset in range((final_day - first_day).days + 1))


async def maintain_job_event_partitions(
    connection: AsyncConnection,
    *,
    now: datetime,
    retention_days: int = 30,
    future_days: int = 7,
) -> JobEventMaintenanceResult:
    """Create hot partitions, drain the default, and prune expired partitions."""
    days = partition_days(
        now=now,
        retention_days=retention_days,
        future_days=future_days,
    )
    retained_from = days[0]
    covered_through = days[-1]

    lock_result = await connection.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": _ADVISORY_LOCK_KEY},
    )
    if not bool(lock_result.scalar_one()):
        raise MaintenanceBusyError("job-event partition maintenance is already running")
    await connection.execute(text("SET LOCAL lock_timeout = '15s'"))
    await connection.execute(text("LOCK TABLE agri.job_event_default IN ACCESS EXCLUSIVE MODE"))

    existing = await _attached_partition_names(connection)
    created = 0
    moved = 0
    for day in days:
        partition_name = _partition_name(day)
        if partition_name in existing:
            continue
        moved += await _create_and_attach_partition(connection, day)
        existing.add(partition_name)
        created += 1

    dropped = 0
    for partition_name in sorted(existing):
        partition_day = _partition_day(partition_name)
        if partition_day is None or partition_day >= retained_from:
            continue
        await connection.execute(text(f'DROP TABLE agri."{partition_name}"'))
        dropped += 1

    cutoff = _day_start(retained_from)
    deleted_result = await connection.execute(
        text(
            "WITH deleted AS ("
            "DELETE FROM agri.job_event_default "
            "WHERE occurred_at < :cutoff RETURNING 1"
            ") SELECT count(*) FROM deleted"
        ),
        {"cutoff": cutoff},
    )
    deleted = int(deleted_result.scalar_one())
    remaining_result = await connection.execute(text("SELECT count(*) FROM agri.job_event_default"))
    remaining = int(remaining_result.scalar_one())

    return JobEventMaintenanceResult(
        retained_from=retained_from,
        covered_through=covered_through,
        created_partitions=created,
        moved_default_rows=moved,
        dropped_partitions=dropped,
        deleted_expired_default_rows=deleted,
        default_rows_remaining=remaining,
    )


async def _attached_partition_names(connection: AsyncConnection) -> set[str]:
    result = await connection.execute(
        text(
            "SELECT child.relname "
            "FROM pg_inherits inheritance "
            "JOIN pg_class parent ON parent.oid = inheritance.inhparent "
            "JOIN pg_namespace namespace ON namespace.oid = parent.relnamespace "
            "JOIN pg_class child ON child.oid = inheritance.inhrelid "
            "JOIN pg_namespace child_namespace ON child_namespace.oid = child.relnamespace "
            "WHERE namespace.nspname = 'agri' AND parent.relname = 'job_event' "
            "AND child_namespace.nspname = 'agri'"
        )
    )
    return {str(name) for name in result.scalars() if name != "job_event_default"}


async def _create_and_attach_partition(
    connection: AsyncConnection,
    day: date,
) -> int:
    partition_name = _partition_name(day)
    constraint_name = f"ck_{partition_name}_utc_day"
    start = _day_start(day)
    end = start + timedelta(days=1)
    start_literal = start.isoformat()
    end_literal = end.isoformat()

    await connection.execute(
        text(
            f'CREATE TABLE agri."{partition_name}" '
            "(LIKE agri.job_event INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING STORAGE)"
        )
    )
    await connection.execute(
        text(
            f'ALTER TABLE agri."{partition_name}" ADD CONSTRAINT "{constraint_name}" '
            f"CHECK (occurred_at >= TIMESTAMPTZ '{start_literal}' "
            f"AND occurred_at < TIMESTAMPTZ '{end_literal}')"
        )
    )
    moved_result = await connection.execute(
        text(
            "WITH moved AS ("
            "DELETE FROM agri.job_event_default "
            "WHERE occurred_at >= :start AND occurred_at < :end RETURNING *"
            f'), inserted AS (INSERT INTO agri."{partition_name}" '
            "SELECT * FROM moved RETURNING 1) "
            "SELECT count(*) FROM inserted"
        ),
        {"start": start, "end": end},
    )
    moved = int(moved_result.scalar_one())
    await connection.execute(
        text(
            f'ALTER TABLE agri.job_event ATTACH PARTITION agri."{partition_name}" '
            f"FOR VALUES FROM ('{start_literal}') TO ('{end_literal}')"
        )
    )
    return moved


def _partition_name(day: date) -> str:
    return f"job_event_p{day:%Y%m%d}"


def _partition_day(name: str) -> date | None:
    match = _PARTITION_NAME.fullmatch(name)
    if match is None:
        return None
    value = match.group("day")
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)
