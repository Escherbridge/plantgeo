"""Contract tests for the bounded job-event retention plan."""

from datetime import UTC, datetime

import pytest

from agri_data_service.db.maintenance import (
    MaintenanceBusyError,
    maintain_job_event_partitions,
    partition_days,
)

_EXPECTED_PARTITION_DAYS = 37


class _BusyResult:
    def scalar_one(self) -> bool:
        return False


class _BusyConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: object, _parameters: object = None) -> _BusyResult:
        self.statements.append(str(statement))
        return _BusyResult()


def test_partition_days_cover_hot_and_future_windows_in_utc() -> None:
    days = partition_days(
        now=datetime(2026, 7, 19, 23, 30, tzinfo=UTC),
        retention_days=30,
        future_days=7,
    )

    assert len(days) == _EXPECTED_PARTITION_DAYS
    assert days[0].isoformat() == "2026-06-20"
    assert days[-1].isoformat() == "2026-07-26"


def test_partition_days_normalize_non_utc_clock() -> None:
    days = partition_days(
        now=datetime.fromisoformat("2026-07-19T23:30:00-06:00"),
        retention_days=1,
        future_days=1,
    )

    assert [day.isoformat() for day in days] == ["2026-07-20", "2026-07-21"]


@pytest.mark.parametrize(
    ("retention_days", "future_days"),
    [(0, 7), (366, 7), (30, 0), (30, 32)],
)
def test_partition_days_reject_unbounded_windows(
    retention_days: int,
    future_days: int,
) -> None:
    with pytest.raises(ValueError, match="must be between"):
        partition_days(
            now=datetime(2026, 7, 19, tzinfo=UTC),
            retention_days=retention_days,
            future_days=future_days,
        )


@pytest.mark.asyncio
async def test_partition_maintenance_fails_fast_when_fence_is_busy() -> None:
    connection = _BusyConnection()

    with pytest.raises(MaintenanceBusyError, match="already running"):
        await maintain_job_event_partitions(  # type: ignore[arg-type]
            connection,
            now=datetime(2026, 7, 19, tzinfo=UTC),
        )

    assert len(connection.statements) == 1
    assert "pg_try_advisory_xact_lock" in connection.statements[0]
    assert "LOCK TABLE" not in connection.statements[0]
