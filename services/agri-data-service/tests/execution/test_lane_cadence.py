"""An hourly lane opens a NEW bucket every hour: a settled bucket never holds the lane past its own cadence.

Pins the production shape measured 2026-09-15 (`.omc/research/runbook-20260915-shortwave/prod-logs`): the
climate lane's ledger shows distinct `ran`/`succeeded` runs for 00:40, 01:40, 12:40, 13:40 and 23:40. A
"once per UTC day" reading of those logs came from grepping the child's report as TEXT -- Railway parses a
JSON stdout line into structured fields with an empty `message`, so only the reports long enough to be
chunked as raw text matched. This test fails if the planner ever lets a `succeeded` bucket block the next.
"""

# ruff: noqa: PLR2004 - assertion literals are the measured facts under test

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_DEFINITION_PREFIX,
    LANE_SPECS,
    ActivationConfig,
    LatestRun,
    scheduled_bucket,
)
from agri_data_service.execution.lane_ids import CLIMATE_DIRECT_LANE_ID
from agri_data_service.jobs import JobDefinitionRecord

if TYPE_CHECKING:
    from agri_data_service.execution.job_executor_service import LaneExecutionSpec

ACTIVE = ActivationConfig(frozenset({CLIMATE_DIRECT_LANE_ID}))
BUCKET_0040 = datetime(2026, 9, 15, 0, 40, tzinfo=UTC)
BUCKET_0140 = datetime(2026, 9, 15, 1, 40, tzinfo=UTC)


def _definition(spec: LaneExecutionSpec) -> JobDefinitionRecord:
    return JobDefinitionRecord(
        id=uuid.uuid4(),
        name=f"{EXECUTOR_DEFINITION_PREFIX}{spec.lane_id}",
        version="2",
        handler="plantgeo.executor.command.v1",
        queue_name="default",
        concurrency_key=None,
        max_attempts=5,
        lease_seconds=spec.command_timeout_seconds + 120,
        time_budget_seconds=spec.command_timeout_seconds + 30,
        retry_policy={},
        parameters={},
    )


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> dict[str, LatestRun | None]:
    """Pin the planner's ledger reads to one lane whose newest checkpoint the test sets."""
    held: dict[str, LatestRun | None] = {"latest": None}

    async def load_or_register(_session: object, spec: LaneExecutionSpec) -> JobDefinitionRecord:
        return _definition(spec)

    async def checkpoint(_session: object, _spec: LaneExecutionSpec) -> LatestRun | None:
        return held["latest"]

    async def rollback(_session: object) -> None:
        return None

    async def no_repairs(_session: object, _activation: ActivationConfig, *, forward_due: set[str]) -> tuple:
        del forward_due
        return [], []

    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", load_or_register)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", checkpoint)
    monkeypatch.setattr(job_executor_service, "_rollback_planning_transaction", rollback)
    monkeypatch.setattr(job_executor_service, "_plan_repair_runs", no_repairs)
    only_climate = MappingProxyType({CLIMATE_DIRECT_LANE_ID: LANE_SPECS[CLIMATE_DIRECT_LANE_ID]})
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", only_climate)
    return held


def _succeeded(bucket: datetime) -> LatestRun:
    return LatestRun(run_id=uuid.uuid4(), scheduled_for=bucket, status="succeeded", work_claimable=False)


async def _plan(now: datetime) -> tuple[list[str], list[datetime]]:
    results, due = await job_executor_service._plan_active_lanes(object(), ACTIVE, now)  # type: ignore[arg-type]
    return [result.state for result in results], [candidate.scheduled_for for candidate in due]


def test_the_climate_lane_is_registered_hourly() -> None:
    spec = LANE_SPECS[CLIMATE_DIRECT_LANE_ID]
    assert spec.cadence_seconds == 3600
    assert spec.schedule == "40 * * * *"
    assert scheduled_bucket(spec, datetime(2026, 9, 15, 1, 41, tzinfo=UTC)) == BUCKET_0140


async def test_a_succeeded_bucket_holds_the_lane_only_until_its_own_cadence_elapses(
    ledger: dict[str, LatestRun | None],
) -> None:
    ledger["latest"] = _succeeded(BUCKET_0040)

    states, due = await _plan(datetime(2026, 9, 15, 1, 39, 51, tzinfo=UTC))
    assert (states, due) == (["not_due"], []), "inside the 00:40 bucket the lane is settled, as the ticks logged"

    states, due = await _plan(datetime(2026, 9, 15, 1, 40, 21, tzinfo=UTC))
    assert (states, due) == ([], [BUCKET_0140]), "the 01:40 bucket opens the moment the clock reaches it"


async def test_a_day_of_downtime_coalesces_to_the_current_hour_not_to_tomorrow(
    ledger: dict[str, LatestRun | None],
) -> None:
    ledger["latest"] = _succeeded(BUCKET_0040 - timedelta(days=1))

    states, due = await _plan(datetime(2026, 9, 15, 0, 41, tzinfo=UTC))
    assert (states, due) == ([], [BUCKET_0040])


async def test_a_lane_with_no_checkpoint_is_due_now(ledger: dict[str, LatestRun | None]) -> None:
    ledger["latest"] = None
    states, due = await _plan(datetime(2026, 9, 15, 12, 41, tzinfo=UTC))
    assert (states, due) == ([], [datetime(2026, 9, 15, 12, 40, tzinfo=UTC)])
