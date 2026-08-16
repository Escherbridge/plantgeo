"""The strategy-mv-refresh job: pure-unit coverage with no database.

Mirrors `test_jobs_lease.py`'s convention: the seam is `AsyncSession.execute`, and a small fake
session answers each statement by inspecting its SQL text (this lane's statements mostly have no
`-- marker` -- `REFRESH MATERIALIZED VIEW ...` and the row-count check are built dynamically per
view name, per `code_styleguides/sql.md`'s "SQL assembled dynamically ... stays in Python" rule --
so dispatch is by substring rather than by the `-- <name>` marker `test_jobs_lease.py` uses for its
extracted `.sql` files).
"""

# ruff: noqa: PLR2004 -- every assertion below names a literal statement count, row count or HTTP
# status; a named constant per literal would read no clearer than the number next to its assertion.

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sanic import json as sanic_json
from sqlalchemy.exc import OperationalError

from agri_data_service.jobs import dispatch as dispatch_module
from agri_data_service.jobs import scheduler as scheduler_module
from agri_data_service.jobs.dispatch import (
    LANE_DISPATCH,
    LaneDispatchRegistry,
    LanePauseState,
    register_dispatchable_lane,
)
from agri_data_service.jobs.registry import JOB_HANDLERS, JobInvocation
from agri_data_service.jobs.scheduler import StrategyMvRefreshScheduler
from agri_data_service.jobs.strategy_mv_refresh import (
    STRATEGY_MV_REFRESH_DEFINITION_NAME,
    STRATEGY_MV_REFRESH_HANDLER_TOKEN,
    STRATEGY_MV_REFRESH_LEASE_SECONDS,
    STRATEGY_MV_REFRESH_TIME_BUDGET_SECONDS,
    STRATEGY_RECOMMENDATION_VIEWS,
    StrategyMvRefreshContextError,
    StrategyMvRefreshLaneContext,
    UnknownStrategyMaterializedViewError,
    _run_bucket_key,
    current_strategy_mv_refresh_context,
    refresh_strategy_recommendation_views,
    strategy_mv_refresh_context,
    strategy_mv_refresh_definition_spec,
    strategy_mv_refresh_handler,
    trigger_strategy_mv_refresh,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

COARSE, REGIONAL, DETAIL = STRATEGY_RECOMMENDATION_VIEWS


def _trigger_request(lane_id: str) -> Any:
    """The one attribute `trigger_job` reads off a Sanic request."""
    return SimpleNamespace(json={"lane_id": lane_id})


async def _unpaused_state(_session: object, _lane_id: str) -> LanePauseState:
    """Stand in for the pause-state read, so a route test needs no session that answers SQL."""
    return LanePauseState(registered=True, paused=False)


class FakeResult:
    """The narrow slice of SQLAlchemy's Result this lane actually uses."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> Mapping[str, object] | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    """A minimal AsyncSession stand-in scripted with which views exist, carry a unique index, and fail."""

    def __init__(
        self,
        *,
        existing_views: frozenset[str] = frozenset(STRATEGY_RECOMMENDATION_VIEWS),
        unique_index_views: frozenset[str] = frozenset(STRATEGY_RECOMMENDATION_VIEWS),
        fail_on: frozenset[str] = frozenset(),
    ) -> None:
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0
        self._existing_views = existing_views
        self._unique_index_views = unique_index_views
        self._fail_on = fail_on

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> FakeResult:
        sql = str(statement)
        params = dict(parameters or {})
        self.statements.append((sql, params))
        if "to_regclass" in sql:
            qualified_name = params["qualified_name"]
            return FakeResult([{"view_exists": qualified_name in self._existing_views}])
        if "select_materialized_view_unique_index" in sql:
            qualified_name = f"{params['schema_name']}.{params['view_name']}"
            return FakeResult([{"has_unique_index": qualified_name in self._unique_index_views}])
        if sql.startswith("REFRESH MATERIALIZED VIEW"):
            target = sql.rsplit(" ", 1)[-1]
            if target in self._fail_on:
                raise OperationalError("REFRESH failed", {}, Exception("boom"))
            return FakeResult([])
        if sql.startswith("SELECT count(*)"):
            return FakeResult([{"row_count": 42}])
        return FakeResult([])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def refresh_statements(self) -> list[str]:
        return [sql for sql, _ in self.statements if sql.startswith("REFRESH MATERIALIZED VIEW")]


def _fake_invocation(*, payload: Mapping[str, object] | None = None) -> JobInvocation:
    async def _heartbeat() -> bool:
        return True

    return JobInvocation(
        shard_key="strategy-mv-refresh:test",
        kind="strategy_mv_refresh_batch",
        payload=payload or {},
        cursor=None,
        parameters={},
        attempt_number=1,
        max_attempts=3,
        progress_fraction=0.0,
        seconds_remaining=STRATEGY_MV_REFRESH_TIME_BUDGET_SECONDS,
        heartbeat=_heartbeat,
    )


# --- refresh_strategy_recommendation_views: order, statements, skip, fallback, failure ---


@pytest.mark.asyncio
async def test_refreshes_all_three_views_concurrently_in_coarse_regional_detail_order() -> None:
    session = FakeSession()

    report = await refresh_strategy_recommendation_views(session)

    assert [result.qualified_name for result in report.results] == [COARSE, REGIONAL, DETAIL]
    assert [result.status for result in report.results] == ["refreshed_concurrently"] * 3
    assert all(result.used_concurrently for result in report.results)
    assert all(result.row_count == 42 for result in report.results)
    assert not report.has_failures

    refreshed = session.refresh_statements()
    assert refreshed == [
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {COARSE}",
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {REGIONAL}",
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {DETAIL}",
    ]
    # Per view: exists check, unique-index check, REFRESH, row count, and the observability
    # write-back into agri.matview_refresh_state -- five statements, and two commits (the refresh
    # itself, then the state row), so 15 statements and 6 commits across three views. The
    # write-back is committed separately on purpose: an observability row must never be able to
    # roll back the refresh it is describing.
    assert len(session.statements) == 15
    assert session.commits == 6
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_skips_a_missing_view_without_touching_the_other_two() -> None:
    session = FakeSession(existing_views=frozenset({COARSE, REGIONAL}))

    report = await refresh_strategy_recommendation_views(session)

    by_view = {result.qualified_name: result for result in report.results}
    assert by_view[DETAIL].status == "skipped_missing"
    assert by_view[DETAIL].row_count is None
    assert by_view[DETAIL].elapsed_seconds == 0.0
    assert by_view[COARSE].status == "refreshed_concurrently"
    assert by_view[REGIONAL].status == "refreshed_concurrently"
    assert not report.has_failures

    # The missing view's ONLY statement is its existence check -- no unique-index check, no
    # REFRESH, no row count.
    detail_statements = [(sql, params) for sql, params in session.statements if params.get("qualified_name") == DETAIL]
    assert len(detail_statements) == 1
    assert "to_regclass" in detail_statements[0][0]
    assert session.refresh_statements() == [
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {COARSE}",
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {REGIONAL}",
    ]


@pytest.mark.asyncio
async def test_all_three_views_missing_is_reported_as_a_failure_not_a_quiet_success() -> None:
    session = FakeSession(existing_views=frozenset())

    report = await refresh_strategy_recommendation_views(session)

    assert [result.status for result in report.results] == ["skipped_missing"] * 3
    assert report.has_failures
    for view in STRATEGY_RECOMMENDATION_VIEWS:
        assert view in report.failure_summary()


@pytest.mark.asyncio
async def test_falls_back_to_plain_refresh_when_the_unique_index_is_missing() -> None:
    session = FakeSession(unique_index_views=frozenset({REGIONAL, DETAIL}))

    report = await refresh_strategy_recommendation_views(session)

    by_view = {result.qualified_name: result for result in report.results}
    assert by_view[COARSE].status == "refreshed_full"
    assert by_view[COARSE].used_concurrently is False
    assert by_view[REGIONAL].status == "refreshed_concurrently"
    assert by_view[DETAIL].status == "refreshed_concurrently"

    refreshed = session.refresh_statements()
    assert refreshed[0] == f"REFRESH MATERIALIZED VIEW {COARSE}"
    assert "CONCURRENTLY" not in refreshed[0]
    assert refreshed[1] == f"REFRESH MATERIALIZED VIEW CONCURRENTLY {REGIONAL}"
    assert refreshed[2] == f"REFRESH MATERIALIZED VIEW CONCURRENTLY {DETAIL}"


@pytest.mark.asyncio
async def test_a_failed_refresh_rolls_back_and_does_not_block_the_remaining_views() -> None:
    session = FakeSession(fail_on=frozenset({REGIONAL}))

    report = await refresh_strategy_recommendation_views(session)

    by_view = {result.qualified_name: result for result in report.results}
    assert by_view[REGIONAL].status == "failed"
    assert by_view[REGIONAL].row_count is None
    assert by_view[REGIONAL].detail == "OperationalError"
    assert by_view[COARSE].status == "refreshed_concurrently"
    assert by_view[DETAIL].status == "refreshed_concurrently"
    assert report.has_failures
    assert REGIONAL in report.failure_summary()

    assert session.rollbacks == 1
    # Two successful refreshes commit twice each (the REFRESH, then the observability write-back);
    # the failed view rolls its REFRESH back and still commits ITS state row -- recording a failure
    # is the one thing that must survive the failure.
    assert session.commits == 5
    # All three REFRESH statements were still attempted -- one view's failure never short-circuits
    # the loop.
    assert len(session.refresh_statements()) == 3


@pytest.mark.asyncio
async def test_refuses_a_view_outside_the_guardrailed_set() -> None:
    session = FakeSession()

    with pytest.raises(UnknownStrategyMaterializedViewError):
        await refresh_strategy_recommendation_views(session, views=("geo.mv_something_unreviewed",))

    assert session.statements == []


# --- the handler: session binding, completion vs. failure ---


@pytest.mark.asyncio
async def test_handler_completes_when_every_view_lands_clean() -> None:
    session = FakeSession()
    async with strategy_mv_refresh_context(StrategyMvRefreshLaneContext(session=session)):
        outcome = await strategy_mv_refresh_handler(_fake_invocation())

    assert outcome.kind == "completed"
    assert outcome.metrics["views"]


@pytest.mark.asyncio
async def test_handler_fails_when_a_view_failed() -> None:
    session = FakeSession(fail_on=frozenset({DETAIL}))
    async with strategy_mv_refresh_context(StrategyMvRefreshLaneContext(session=session)):
        outcome = await strategy_mv_refresh_handler(_fake_invocation())

    assert outcome.kind == "failed"
    assert outcome.failure_class == "strategy_mv_refresh_failed"
    assert DETAIL in (outcome.reason or "")


@pytest.mark.asyncio
async def test_handler_fails_when_every_view_is_missing() -> None:
    session = FakeSession(existing_views=frozenset())
    async with strategy_mv_refresh_context(StrategyMvRefreshLaneContext(session=session)):
        outcome = await strategy_mv_refresh_handler(_fake_invocation())

    assert outcome.kind == "failed"
    assert outcome.failure_class == "strategy_mv_refresh_failed"
    for view in STRATEGY_RECOMMENDATION_VIEWS:
        assert view in (outcome.reason or "")


@pytest.mark.asyncio
async def test_handler_refuses_to_run_with_no_bound_context() -> None:
    with pytest.raises(StrategyMvRefreshContextError):
        await strategy_mv_refresh_handler(_fake_invocation())


def test_current_context_refuses_when_unbound() -> None:
    with pytest.raises(StrategyMvRefreshContextError):
        current_strategy_mv_refresh_context()


# --- the definition spec: registered the same way the two existing lanes are ---


def test_definition_spec_matches_the_handler_token_and_stays_within_its_own_lease() -> None:
    spec = strategy_mv_refresh_definition_spec()

    assert spec.name == STRATEGY_MV_REFRESH_DEFINITION_NAME
    assert spec.handler == STRATEGY_MV_REFRESH_HANDLER_TOKEN
    assert spec.lease_seconds == STRATEGY_MV_REFRESH_LEASE_SECONDS
    assert spec.time_budget_seconds == STRATEGY_MV_REFRESH_TIME_BUDGET_SECONDS
    assert spec.lease_seconds > spec.time_budget_seconds
    assert spec.schedule is not None
    assert list(spec.parameters["views"]) == list(STRATEGY_RECOMMENDATION_VIEWS)


def test_handler_token_is_registered_at_import_time() -> None:
    assert STRATEGY_MV_REFRESH_HANDLER_TOKEN in JOB_HANDLERS


# --- the run-key bucketing that lets a poll tick and a manual trigger share one run ---


def test_run_bucket_key_groups_moments_inside_one_poll_interval() -> None:
    first = datetime(2026, 8, 14, 12, 0, 5, tzinfo=UTC)
    second = datetime(2026, 8, 14, 12, 14, 59, tzinfo=UTC)
    third = datetime(2026, 8, 14, 12, 15, 1, tzinfo=UTC)

    assert _run_bucket_key(first) == _run_bucket_key(second)
    assert _run_bucket_key(second) != _run_bucket_key(third)


# --- the periodic in-process driver ---


@pytest.mark.asyncio
async def test_strategy_mv_refresh_scheduler_lifecycle() -> None:
    scheduler = StrategyMvRefreshScheduler(poll_interval_seconds=60)
    assert scheduler._running is False

    await scheduler.start()
    assert scheduler._running is True
    assert scheduler._task is not None

    await scheduler.stop()
    assert scheduler._running is False


@pytest.mark.asyncio
async def test_the_periodic_driver_ticks_through_dispatch_so_a_pause_stops_the_schedule_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manual button and the timer must share one pause switch, not carry one each."""
    calls: list[tuple[object, str, str]] = []

    @asynccontextmanager
    async def _fake_get_scheduler_session() -> AsyncIterator[str]:
        yield "fake-session"

    async def _fake_dispatch(session: object, lane_id: str, *, requested_by: str) -> str:
        calls.append((session, lane_id, requested_by))
        return "paused-outcome"

    monkeypatch.setattr(scheduler_module, "get_scheduler_session", _fake_get_scheduler_session)
    monkeypatch.setattr(scheduler_module, "dispatch_lane", _fake_dispatch)

    outcome = await StrategyMvRefreshScheduler(poll_interval_seconds=60).run_once()

    assert outcome == "paused-outcome"
    assert calls == [("fake-session", STRATEGY_MV_REFRESH_DEFINITION_NAME, "strategy-mv-refresh-scheduler")]


# --- the manual-trigger route: one general path, dispatching this lane by name like any other ---


def test_this_lane_publishes_itself_to_the_dispatcher_so_the_route_needs_no_branch_for_it() -> None:
    lane = LANE_DISPATCH.lane_for(STRATEGY_MV_REFRESH_DEFINITION_NAME)
    assert lane.handler_token == STRATEGY_MV_REFRESH_HANDLER_TOKEN
    assert lane.trigger is trigger_strategy_mv_refresh


@pytest.mark.asyncio
async def test_trigger_route_dispatches_strategy_mv_refresh_to_the_real_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str]] = []

    class _FakeSummary:
        """The fields `dispatch_lane` logs, plus the payload the route returns."""

        job_run_id = None
        stop_reason = "no_claimable_work"
        succeeded = 1

        def to_summary(self) -> dict[str, object]:
            return {"stop_reason": "no_claimable_work", "succeeded": 1}

    @asynccontextmanager
    async def _fake_get_scheduler_session() -> AsyncIterator[str]:
        yield "fake-session"

    async def _fake_trigger(session: object, *, requested_by: str, now: object = None) -> _FakeSummary:
        del now
        calls.append((session, requested_by))
        return _FakeSummary()

    # The lane's registration captured the real trigger at import time, so the fake goes in through
    # a replacement registry rather than by patching the module attribute the decorator already read.
    registry = LaneDispatchRegistry()
    register_dispatchable_lane(
        lane_id=STRATEGY_MV_REFRESH_DEFINITION_NAME,
        handler_token=STRATEGY_MV_REFRESH_HANDLER_TOKEN,
        trigger=_fake_trigger,
        description="the strategy matview refresh, with a stubbed trigger",
        registry=registry,
    )
    monkeypatch.setattr(dispatch_module, "LANE_DISPATCH", registry)
    monkeypatch.setattr(scheduler_module, "LANE_DISPATCH", registry)
    monkeypatch.setattr(scheduler_module, "get_scheduler_session", _fake_get_scheduler_session)
    monkeypatch.setattr(dispatch_module, "read_lane_pause_state", _unpaused_state)

    response = await scheduler_module.trigger_job(_trigger_request(STRATEGY_MV_REFRESH_DEFINITION_NAME))

    assert response.status == 200
    assert calls == [("fake-session", "jobs-trigger-route")]


def test_sanic_json_import_matches_the_route_return_type() -> None:
    """A smoke check that this module's `json` import lines up with what the route builds."""
    response = sanic_json({"ok": True})
    assert response.status == 200
