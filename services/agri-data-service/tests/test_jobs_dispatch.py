"""The lane dispatcher: it runs real lanes by name, refuses unknown ones, and obeys the pause switch.

These tests stub `AsyncSession`, so they prove resolution, refusal and the pause decision -- never
that the SQL runs. Real-database coverage of the same paths is `test_jobs_dispatch_agri_db.py`.

This file replaces `test_inapp_scheduler.py`, which asserted that a hash function was deterministic
and that a boolean flag flipped when a background task started. Neither statement touched the job
ledger, and the scheduler both statements described polled `agri.job_schedules` -- a table nothing
in either migration tree creates. See `jobs/scheduler.py`'s module docstring.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from agri_data_service.app import create_app
from agri_data_service.jobs import dispatch as dispatch_module
from agri_data_service.jobs import scheduler as scheduler_module
from agri_data_service.jobs.dispatch import (
    LANE_DISPATCH,
    LaneDispatchRegistry,
    LaneHandlerMissingError,
    UnknownDispatchableLaneError,
    dispatch_lane,
    register_dispatchable_lane,
)
from agri_data_service.jobs.registry import JOB_HANDLERS, JobHandlerRegistry, job_handler
from agri_data_service.jobs.strategy_mv_refresh import (
    STRATEGY_MV_REFRESH_DEFINITION_NAME,
    STRATEGY_MV_REFRESH_HANDLER_TOKEN,
)
from agri_data_service.jobs.worker import JobRunError, JobSliceSummary, ShutdownSignal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

_HTTP_OK = 200
_HTTP_BAD_REQUEST = 400
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HTTP_SERVER_ERROR = 500
_RUN_ID = UUID("3f2504e0-4f89-11d3-9a0c-0305e82c3301")
_FAKE_TOKEN = "tests.fake_lane"
_FAKE_LANE = "fake-lane"


@dataclass
class _PauseRow:
    """One row of `select_job_definition_pause_state.sql`, as the driver would hand it back."""

    definition_count: int
    any_version_enabled: bool

    def items(self) -> Any:
        """Match the RowMapping surface `jobs.lease.fetch_row` normalises."""
        return {
            "definition_count": self.definition_count,
            "any_version_enabled": self.any_version_enabled,
        }.items()


class _Result:
    """The two-call shape `fetch_row` drives: `.mappings().first()`."""

    def __init__(self, row: _PauseRow | None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def first(self) -> _PauseRow | None:
        return self._row


@dataclass
class _Session:
    """A stub session that answers only the pause-state query, and remembers what it was asked."""

    row: _PauseRow | None
    parameters: list[Mapping[str, object]] = field(default_factory=list)

    async def execute(self, _statement: object, parameters: Mapping[str, object]) -> _Result:
        self.parameters.append(parameters)
        return _Result(self.row)


@dataclass
class _RecordingTrigger:
    """A lane entry point that records its call instead of touching a ledger."""

    summary: JobSliceSummary
    calls: list[tuple[object, str]] = field(default_factory=list)

    async def __call__(self, session: object, *, requested_by: str) -> JobSliceSummary:
        self.calls.append((session, requested_by))
        return self.summary


def _summary(definition_name: str = _FAKE_LANE) -> JobSliceSummary:
    return JobSliceSummary(
        definition_name=definition_name,
        worker_id="test-worker",
        job_run_id=_RUN_ID,
        stop_reason="no_claimable_work",
        claimed=1,
        succeeded=1,
        run_status="succeeded",
    )


@job_handler(_FAKE_TOKEN)
async def _fake_lane_handler(invocation: object) -> object:
    """Bind this file's token in the PROCESS-WIDE registry, exactly as a real lane's module does.

    The route tests below drive `trigger_job`, which calls `dispatch_lane` with its own default
    handler registry -- the real one. A lane whose token resolves to nothing there is an import
    fault (`LaneHandlerMissingError`), so the token has to be genuinely registered rather than
    monkeypatched in. It is never invoked: dispatch calls a lane's trigger, and this file's triggers
    are stubs that never reach a handler.
    """
    raise AssertionError(f"the test lane handler must never run (invocation={invocation!r})")


def _handlers(*tokens: str) -> JobHandlerRegistry:
    """A handler registry holding exactly the tokens a test wants to be resolvable."""
    registry = JobHandlerRegistry()

    async def _noop(_invocation: object) -> object:  # pragma: no cover - never driven in this file
        raise AssertionError("the stub handler must never run: dispatch calls a lane's trigger, not its handler")

    for token in tokens:
        registry.register(token, _noop)  # type: ignore[arg-type]
    return registry


def _registered(trigger: _RecordingTrigger, lane_id: str = _FAKE_LANE) -> LaneDispatchRegistry:
    registry = LaneDispatchRegistry()
    register_dispatchable_lane(
        lane_id=lane_id,
        handler_token=_FAKE_TOKEN,
        trigger=trigger,
        description="a test lane",
        registry=registry,
    )
    return registry


# --- Resolution and refusal ------------------------------------------------------------------


async def test_dispatch_runs_a_registered_lanes_own_trigger_and_returns_its_real_summary() -> None:
    trigger = _RecordingTrigger(summary=_summary())
    session = _Session(row=_PauseRow(definition_count=1, any_version_enabled=True))

    outcome = await dispatch_lane(
        session,  # type: ignore[arg-type]
        _FAKE_LANE,
        requested_by="test",
        registry=_registered(trigger),
        handlers=_handlers(_FAKE_TOKEN),
    )

    assert outcome.state == "dispatched"
    assert outcome.summary is not None
    assert outcome.summary.job_run_id == _RUN_ID
    assert trigger.calls == [(session, "test")]
    assert outcome.to_payload()["result"] == _summary().to_summary()


async def test_dispatch_threads_an_existing_shutdown_signal_into_the_lane_trigger() -> None:
    stop = ShutdownSignal()
    observed: list[object] = []

    async def _trigger(
        session: object,
        *,
        requested_by: str,
        stop: ShutdownSignal | None = None,
    ) -> JobSliceSummary:
        observed.extend((session, requested_by, stop))
        return _summary()

    registry = LaneDispatchRegistry()
    register_dispatchable_lane(
        lane_id=_FAKE_LANE,
        handler_token=_FAKE_TOKEN,
        trigger=_trigger,
        description="shutdown-aware test lane",
        registry=registry,
    )
    session = _Session(row=_PauseRow(definition_count=1, any_version_enabled=True))

    await dispatch_lane(
        session,  # type: ignore[arg-type]
        _FAKE_LANE,
        requested_by="test",
        registry=registry,
        handlers=_handlers(_FAKE_TOKEN),
        stop=stop,
    )

    assert observed == [session, "test", stop]


async def test_dispatch_is_general_and_carries_no_per_lane_branch() -> None:
    """Two differently named lanes reach their own triggers through the same call."""
    first, second = _RecordingTrigger(summary=_summary("alpha")), _RecordingTrigger(summary=_summary("beta"))
    registry = _registered(first, lane_id="alpha")
    register_dispatchable_lane(
        lane_id="beta",
        handler_token=_FAKE_TOKEN,
        trigger=second,
        description="a second test lane",
        registry=registry,
    )
    handlers = _handlers(_FAKE_TOKEN)
    session = _Session(row=_PauseRow(definition_count=0, any_version_enabled=False))

    for lane_id in ("alpha", "beta"):
        outcome = await dispatch_lane(
            session,  # type: ignore[arg-type]
            lane_id,
            requested_by="test",
            registry=registry,
            handlers=handlers,
        )
        assert outcome.state == "dispatched"
        assert outcome.summary is not None
        assert outcome.summary.definition_name == lane_id

    assert len(first.calls) == 1
    assert len(second.calls) == 1


async def test_an_unknown_lane_is_refused_by_name_and_the_refusal_lists_what_is_known() -> None:
    registry = _registered(_RecordingTrigger(summary=_summary()))
    session = _Session(row=None)

    with pytest.raises(UnknownDispatchableLaneError) as raised:
        await dispatch_lane(
            session,  # type: ignore[arg-type]
            "not-a-lane",
            requested_by="test",
            registry=registry,
            handlers=_handlers(_FAKE_TOKEN),
        )

    assert "not-a-lane" in str(raised.value)
    assert _FAKE_LANE in str(raised.value)
    # The refusal is decided before any ledger read, so an unknown lane costs no query.
    assert session.parameters == []


async def test_a_lane_whose_handler_token_resolves_to_nothing_is_an_import_fault_not_a_run() -> None:
    trigger = _RecordingTrigger(summary=_summary())
    session = _Session(row=_PauseRow(definition_count=1, any_version_enabled=True))

    with pytest.raises(LaneHandlerMissingError):
        await dispatch_lane(
            session,  # type: ignore[arg-type]
            _FAKE_LANE,
            requested_by="test",
            registry=_registered(trigger),
            handlers=_handlers(),
        )

    assert trigger.calls == []


# --- The pause switch ------------------------------------------------------------------------


async def test_a_paused_lane_is_skipped_before_its_trigger_can_re_enable_it() -> None:
    """The whole point of checking first: `ensure_job_definition` writes `enabled = EXCLUDED.enabled`.

    A trigger that ran would upsert the definition with `enabled=True` and silently undo the pause,
    so this asserts the trigger is never reached rather than only that the outcome says "paused".
    """
    trigger = _RecordingTrigger(summary=_summary())
    session = _Session(row=_PauseRow(definition_count=2, any_version_enabled=False))

    outcome = await dispatch_lane(
        session,  # type: ignore[arg-type]
        _FAKE_LANE,
        requested_by="test",
        registry=_registered(trigger),
        handlers=_handlers(_FAKE_TOKEN),
    )

    assert outcome.state == "paused"
    assert outcome.summary is None
    assert trigger.calls == []
    assert session.parameters == [{"name": _FAKE_LANE}]


async def test_one_enabled_version_beside_a_disabled_one_is_not_paused() -> None:
    """`load_job_definition` takes the newest ENABLED version, so any enabled row means it can still run."""
    trigger = _RecordingTrigger(summary=_summary())
    session = _Session(row=_PauseRow(definition_count=2, any_version_enabled=True))

    outcome = await dispatch_lane(
        session,  # type: ignore[arg-type]
        _FAKE_LANE,
        requested_by="test",
        registry=_registered(trigger),
        handlers=_handlers(_FAKE_TOKEN),
    )

    assert outcome.state == "dispatched"
    assert len(trigger.calls) == 1


async def test_a_lane_no_deploy_has_registered_yet_is_not_reported_as_paused() -> None:
    trigger = _RecordingTrigger(summary=_summary())
    session = _Session(row=_PauseRow(definition_count=0, any_version_enabled=False))

    outcome = await dispatch_lane(
        session,  # type: ignore[arg-type]
        _FAKE_LANE,
        requested_by="test",
        registry=_registered(trigger),
        handlers=_handlers(_FAKE_TOKEN),
    )

    assert outcome.state == "dispatched"
    assert len(trigger.calls) == 1


# --- The HTTP surface ------------------------------------------------------------------------


@pytest.fixture
def route_registry(monkeypatch: pytest.MonkeyPatch) -> LaneDispatchRegistry:
    """Swap the process-wide dispatch registry for an empty one, in both modules that bound it."""
    registry = LaneDispatchRegistry()
    monkeypatch.setattr(dispatch_module, "LANE_DISPATCH", registry)
    monkeypatch.setattr(scheduler_module, "LANE_DISPATCH", registry)
    return registry


def _bind_session(monkeypatch: pytest.MonkeyPatch, session: _Session) -> None:
    @asynccontextmanager
    async def _factory() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(scheduler_module, "get_scheduler_session", _factory)


def _request(**body: object) -> Any:
    return SimpleNamespace(json=dict(body) if body else None)


async def test_the_trigger_route_runs_a_lane_and_returns_the_ledgers_own_numbers(
    monkeypatch: pytest.MonkeyPatch,
    route_registry: LaneDispatchRegistry,
) -> None:
    trigger = _RecordingTrigger(summary=_summary())
    register_dispatchable_lane(
        lane_id=_FAKE_LANE,
        handler_token=_FAKE_TOKEN,
        trigger=trigger,
        description="a test lane",
        registry=route_registry,
    )
    _bind_session(monkeypatch, _Session(row=_PauseRow(definition_count=1, any_version_enabled=True)))

    response = await scheduler_module.trigger_job(_request(lane_id=_FAKE_LANE))
    payload = json.loads(response.body)

    assert response.status == _HTTP_OK
    assert payload["state"] == "dispatched"
    assert payload["result"]["job_run_id"] == str(_RUN_ID)
    assert payload["result"]["succeeded"] == 1


async def test_the_trigger_route_404s_an_unknown_lane_and_names_every_lane_it_can_run(
    monkeypatch: pytest.MonkeyPatch,
    route_registry: LaneDispatchRegistry,
) -> None:
    register_dispatchable_lane(
        lane_id=_FAKE_LANE,
        handler_token=_FAKE_TOKEN,
        trigger=_RecordingTrigger(summary=_summary()),
        description="a test lane",
        registry=route_registry,
    )
    _bind_session(monkeypatch, _Session(row=None))

    response = await scheduler_module.trigger_job(_request(lane_id="firms-fire"))
    payload = json.loads(response.body)

    assert response.status == _HTTP_NOT_FOUND
    assert payload["known_lanes"] == [_FAKE_LANE]
    assert "firms-fire" in payload["error"]


async def test_the_trigger_route_reports_a_ledger_failure_as_a_500_carrying_its_own_message(
    monkeypatch: pytest.MonkeyPatch,
    route_registry: LaneDispatchRegistry,
) -> None:
    """A lane that raises must reach the caller as a failure, never as a cheerful 200."""

    async def _failing_trigger(session: object, *, requested_by: str) -> JobSliceSummary:
        del session, requested_by
        raise JobRunError("the run could not be opened")

    register_dispatchable_lane(
        lane_id=_FAKE_LANE,
        handler_token=_FAKE_TOKEN,
        trigger=_failing_trigger,
        description="a test lane",
        registry=route_registry,
    )
    _bind_session(monkeypatch, _Session(row=_PauseRow(definition_count=1, any_version_enabled=True)))

    response = await scheduler_module.trigger_job(_request(lane_id=_FAKE_LANE))
    payload = json.loads(response.body)

    assert response.status == _HTTP_SERVER_ERROR
    assert payload["error"] == "the run could not be opened"


async def test_the_trigger_route_names_only_the_class_of_a_driver_fault_never_its_bound_parameters(
    monkeypatch: pytest.MonkeyPatch,
    route_registry: LaneDispatchRegistry,
) -> None:
    """A SQLAlchemyError message carries the statement and its parameters -- which is how a DSN leaks."""

    async def _exploding_trigger(session: object, *, requested_by: str) -> JobSliceSummary:
        del session, requested_by
        raise OperationalError("SELECT ... FROM agri.job_run", {"dsn": "postgresql://user:secret@host"}, Exception())

    register_dispatchable_lane(
        lane_id=_FAKE_LANE,
        handler_token=_FAKE_TOKEN,
        trigger=_exploding_trigger,
        description="a test lane",
        registry=route_registry,
    )
    _bind_session(monkeypatch, _Session(row=_PauseRow(definition_count=1, any_version_enabled=True)))

    response = await scheduler_module.trigger_job(_request(lane_id=_FAKE_LANE))
    payload = json.loads(response.body)

    assert response.status == _HTTP_SERVER_ERROR
    assert payload["error"] == f"lane {_FAKE_LANE!r} failed against the ledger (OperationalError)"
    assert "secret" not in payload["error"]


async def test_the_trigger_route_500s_a_lane_whose_handler_module_was_never_imported(
    monkeypatch: pytest.MonkeyPatch,
    route_registry: LaneDispatchRegistry,
) -> None:
    register_dispatchable_lane(
        lane_id=_FAKE_LANE,
        handler_token="tests.token_registered_by_nothing",
        trigger=_RecordingTrigger(summary=_summary()),
        description="a test lane",
        registry=route_registry,
    )
    _bind_session(monkeypatch, _Session(row=_PauseRow(definition_count=1, any_version_enabled=True)))

    response = await scheduler_module.trigger_job(_request(lane_id=_FAKE_LANE))
    payload = json.loads(response.body)

    assert response.status == _HTTP_SERVER_ERROR
    assert "tests.token_registered_by_nothing" in payload["error"]


async def test_the_trigger_route_409s_a_paused_lane_rather_than_reporting_a_run_that_never_happened(
    monkeypatch: pytest.MonkeyPatch,
    route_registry: LaneDispatchRegistry,
) -> None:
    trigger = _RecordingTrigger(summary=_summary())
    register_dispatchable_lane(
        lane_id=_FAKE_LANE,
        handler_token=_FAKE_TOKEN,
        trigger=trigger,
        description="a test lane",
        registry=route_registry,
    )
    _bind_session(monkeypatch, _Session(row=_PauseRow(definition_count=1, any_version_enabled=False)))

    response = await scheduler_module.trigger_job(_request(lane_id=_FAKE_LANE))
    payload = json.loads(response.body)

    assert response.status == _HTTP_CONFLICT
    assert payload["state"] == "paused"
    assert payload["result"] is None
    assert trigger.calls == []


async def test_the_trigger_route_refuses_a_body_with_no_lane_id() -> None:
    response = await scheduler_module.trigger_job(_request())
    assert response.status == _HTTP_BAD_REQUEST

    blank = await scheduler_module.trigger_job(_request(lane_id="   "))
    assert blank.status == _HTTP_BAD_REQUEST


async def test_the_lanes_route_lists_what_the_admin_panel_may_trigger(
    route_registry: LaneDispatchRegistry,
) -> None:
    register_dispatchable_lane(
        lane_id=_FAKE_LANE,
        handler_token=_FAKE_TOKEN,
        trigger=_RecordingTrigger(summary=_summary()),
        description="a test lane",
        registry=route_registry,
    )

    response = await scheduler_module.list_lanes(_request())
    payload = json.loads(response.body)

    assert payload["lanes"] == [{"lane_id": _FAKE_LANE, "handler": _FAKE_TOKEN, "description": "a test lane"}]


# --- What the real service actually exposes ---------------------------------------------------


def test_the_real_strategy_lane_is_dispatchable_and_its_handler_resolves() -> None:
    assert STRATEGY_MV_REFRESH_DEFINITION_NAME in LANE_DISPATCH
    lane = LANE_DISPATCH.lane_for(STRATEGY_MV_REFRESH_DEFINITION_NAME)
    assert lane.handler_token == STRATEGY_MV_REFRESH_HANDLER_TOKEN
    assert lane.handler_token in JOB_HANDLERS


def test_the_trigger_route_is_mounted_at_the_path_the_admin_panel_posts_to() -> None:
    """Regression: an absolute blueprint prefix inside the /api/v1 group produced /api/v1/api/v1/jobs."""
    app = create_app()
    paths = {"/".join(name) if isinstance(name, tuple) else str(name) for name in app.router.routes_all}
    assert "api/v1/jobs/trigger" in paths
