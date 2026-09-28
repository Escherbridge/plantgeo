"""An in-memory executor ledger for the GL-5 soft-failure tests (o5b; spec Sec 4.9.3).

The tests drive the public seams: `run_executor_tick` plans and executes, and the REAL
`run_scheduled_command` handler spawns a REAL child process for every turn. Two things are faked at the
process edge, both with behaviour rather than call expectations:

- **The ledger rows.** `FakeWorld` owns the job runs, the definitions and the `agri.job_incident` rows.
  `FakeSession.execute` answers `lane_incidents.py`'s four SQL statements with the same semantics as the
  SQL files: upsert by fingerprint, `last_seen_at = GREATEST(...)`, a resolve that renames the
  fingerprint, and a select of the open rows plus `lane_hold` rows resolved in the last 7 days. The real
  helpers still open a real `begin_nested()` savepoint, which the fake session counts.
- **The worker.** `run_job_slice` is replaced by a worker that drives the real handler through its two
  calls (bootstrap `progressed`, then the terminal call), settles the run, and records the final attempt
  that `select_run_final_attempt.sql` returns.

The planner's own ledger reads (`_load_or_register_definition`, `read_lane_checkpoint`,
`_definition_state`, `_open_scheduled_run`) are pinned to the world, as `test_lane_cadence.py` and
`test_self_healing.py` pin them.
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from sqlalchemy.exc import OperationalError

from agri_data_service.execution import job_executor_service, lane_incidents
from agri_data_service.execution.gap_repair_contract import (
    EXECUTOR_REPAIR_WORK_ITEM_KIND,
    REPAIR_BINDINGS,
    REPAIR_LANE_SUFFIX,
    RepairRequest,
)
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_DEFINITION_PREFIX,
    EXECUTOR_WORK_ITEM_KIND,
    LANE_SPECS,
    ActivationConfig,
    ExecutorTickSummary,
    LaneExecutionSpec,
    LatestRun,
    RepairAuthoringClock,
    SoftFailureState,
)
from agri_data_service.jobs import JobDefinitionRecord, JobHandlerOutcome, JobInvocation
from agri_data_service.jobs.worker import JobSliceSummary

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import pytest

    from agri_data_service.execution.lane_scheduling import DueLane

#: A settled bucket on the hour; every test lane is hourly at phase 0, so `NOW` sits in bucket 06:00.
NOW: Final = datetime(2026, 9, 28, 6, 5, tzinfo=UTC)
HOUR: Final = timedelta(hours=1)

#: A writer's clean terminal report: every selected day written, nothing owed.
COMPLETE_REPORT_SCRIPT: Final = 'import json\nprint(json.dumps({"outcome": "complete", "days_unwritten": 0}))\n'
#: A writer whose command raises a bug-shaped exception and prints no report.
KEY_ERROR_SCRIPT: Final = "raise KeyError('cell_id')\n"


def exit_script(code: int) -> str:
    return f"import sys\nsys.exit({code})\n"


def report_script(report: Mapping[str, object], *, exit_code: int = 0) -> str:
    """A child that prints one terminal JSON report on stdout and exits with `exit_code`."""
    return f"import json, sys\nprint(json.dumps(json.loads({json.dumps(dict(report))!r})))\nsys.exit({exit_code})\n"


def usage_then_report_script(*, last_send_outcome: str, report: Mapping[str, object], exit_code: int) -> str:
    """A child whose usage line (the meter's wire contract: `plantgeo_turn_usage`, its pid, the latest
    send's outcome) precedes its terminal report -- the evidence R3 pairs with a wrapper message."""
    usage = {"event": "plantgeo_turn_usage", "level": "info", "hosts": {}, "last_send_outcome": last_send_outcome}
    return (
        "import json, os, sys, time\n"
        f"usage = json.loads({json.dumps(usage)!r})\n"
        "usage['pid'] = os.getpid()\n"
        "usage['last_send_at'] = time.time()\n"
        "print(json.dumps(usage))\n"
        f"print(json.dumps(json.loads({json.dumps(dict(report))!r})))\n"
        f"sys.exit({exit_code})\n"
    )


def build_lane_spec(lane_id: str, script: str, *, timeout_seconds: int = 60) -> LaneExecutionSpec:
    """An hourly (phase 0) lane running `python -c script`; a registered lane id keeps its own contract,
    so its evidence rules (R3's wrapper registry) and repair binding still apply."""
    command = (sys.executable, "-c", script)
    base = LANE_SPECS.get(lane_id)
    if base is None:
        return LaneExecutionSpec(
            lane_id=lane_id,
            conflicts_with=(),
            work_class="incremental",
            migration_disposition="source-specific",
            cadence_seconds=3600,
            phase_offset_seconds=0,
            schedule="0 * * * *",
            publication_lag_days=None,
            publication_cadence_days=None,
            publication_lag_source="test",
            selection_policy="test",
            catch_up_policy="coalesce_latest",
            command=command,
            command_timeout_seconds=timeout_seconds,
            description="soft-failure test lane",
        )
    return replace(
        base,
        command=command,
        command_timeout_seconds=timeout_seconds,
        cadence_seconds=3600,
        phase_offset_seconds=0,
        schedule="0 * * * *",
        catch_up_policy="coalesce_latest",
        work_class="incremental",
    )


# --- The incident rows: `lane_incidents.py`'s four statements, answered with their SQL semantics --------


def injected_fault(statement: str) -> OperationalError:
    return OperationalError(statement, {}, Exception("injected ledger fault"))


def _text(value: object) -> str | None:
    """What `detail ->> 'key'` returns: text, or NULL."""
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


@dataclass(slots=True)
class IncidentLedger:
    rows: dict[uuid.UUID, dict[str, object]] = field(default_factory=dict)
    #: When set, the per-tick incident read raises (and optionally marks the connection dead).
    fail_select: bool = False
    #: Fingerprints whose upsert raises.
    fail_upserts: Callable[[str], bool] = lambda _fingerprint: False

    def upsert(self, params: Mapping[str, object]) -> dict[str, object]:
        fingerprint = str(params["fingerprint"])
        if self.fail_upserts(fingerprint):
            raise injected_fault("upsert_lane_incident")
        now = params["now"]
        assert isinstance(now, datetime)
        detail = json.loads(str(params["detail"]))
        row = self.by_fingerprint(fingerprint)
        if row is None:
            row = {
                "id": uuid.uuid4(),
                "fingerprint": fingerprint,
                "incident_type": params["incident_type"],
                "status": "open",
                "occurrence_count": 1,
                "first_seen_at": now,
                "last_seen_at": now,
                "cooldown_until": None,
                "resolved_at": None,
            }
            self.rows[row["id"]] = row  # type: ignore[index]
        else:
            row["occurrence_count"] = int(row["occurrence_count"]) + 1  # type: ignore[call-overload]
            row["last_seen_at"] = max(row["last_seen_at"], now)  # type: ignore[type-var]
        row.update(
            severity=params["severity"],
            job_run_id=params["job_run_id"],
            job_work_item_id=params["job_work_item_id"],
            summary=params["summary"],
            detail=detail,
        )
        returned = ("id", "fingerprint", "status", "occurrence_count", "first_seen_at", "last_seen_at")
        return {key: row[key] for key in returned}

    def resolve(self, params: Mapping[str, object]) -> dict[str, object] | None:
        row = self.by_fingerprint(str(params["fingerprint"]))
        if row is None:
            return None
        row["status"] = "resolved"
        row["resolved_at"] = params["now"]
        row["fingerprint"] = f"{row['fingerprint']}:resolved:{row['id']}"
        row["detail"] = {**row["detail"], **json.loads(str(params["detail_patch"]))}  # type: ignore[dict-item]
        return {"id": row["id"], "fingerprint": row["fingerprint"], "resolved_at": row["resolved_at"]}

    def select(self, params: Mapping[str, object]) -> list[dict[str, object]]:
        if self.fail_select:
            raise injected_fault("select_lane_incidents")
        now = params["now"]
        assert isinstance(now, datetime)
        selected = [
            row
            for row in self.rows.values()
            if row["status"] != "resolved"
            or (row["incident_type"] == "lane_hold" and row["resolved_at"] >= now - timedelta(days=7))  # type: ignore[operator]
        ]
        return [
            {
                **{key: row[key] for key in row if key != "detail"},
                "state": _text(row["detail"].get("state")),  # type: ignore[attr-defined]
                "rung": _text(row["detail"].get("rung")),  # type: ignore[attr-defined]
                "chain_first_seen_at": _text(row["detail"].get("chain_first_seen_at")),  # type: ignore[attr-defined]
                "episodes_7d": _text(row["detail"].get("episodes_7d")),  # type: ignore[attr-defined]
            }
            for row in sorted(selected, key=lambda row: row["first_seen_at"])  # type: ignore[arg-type,return-value]
        ]

    def by_fingerprint(self, fingerprint: str) -> dict[str, object] | None:
        """The OPEN row under a fingerprint (a resolved row has been renamed away)."""
        return next(
            (row for row in self.rows.values() if row["fingerprint"] == fingerprint and row["status"] != "resolved"),
            None,
        )

    def resolved(self, fingerprint: str) -> list[dict[str, object]]:
        """Every resolved episode that opened under `fingerprint`."""
        prefix = f"{fingerprint}:resolved:"
        return [row for row in self.rows.values() if str(row["fingerprint"]).startswith(prefix)]

    def open_kinds(self, subject: str) -> set[str]:
        """Which incident kinds are open for one lane (fingerprint `<kind>:<subject>`)."""
        return {
            str(row["incident_type"])
            for row in self.rows.values()
            if row["status"] != "resolved" and str(row["fingerprint"]).endswith(f":{subject}")
        }

    def seed(self, **row: object) -> dict[str, object]:
        """Insert one row as an earlier process left it (for restart and orphan cases)."""
        seeded: dict[str, object] = {
            "id": uuid.uuid4(),
            "status": "open",
            "severity": "warning",
            "job_run_id": None,
            "job_work_item_id": None,
            "summary": "seeded",
            "occurrence_count": 1,
            "cooldown_until": None,
            "resolved_at": None,
            "detail": {},
            **row,
        }
        seeded.setdefault("last_seen_at", seeded["first_seen_at"])
        self.rows[seeded["id"]] = seeded  # type: ignore[index]
        return seeded


# --- The session: savepoints, commits, rollbacks and a pinned connection that can die ----------------


@dataclass(slots=True)
class _Bind:
    invalidated: bool = False


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, object]]:
        return list(self._rows)


class _Savepoint:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _Savepoint:
        self._session.savepoints += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if exc_type is not None:
            self._session.savepoints_rolled_back += 1
        return False


class FakeSession:
    """Just enough `AsyncSession`: `execute`, `begin_nested`, `commit`, `rollback` and a `bind`."""

    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.bind = _Bind()
        self.savepoints = 0
        self.savepoints_rolled_back = 0
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> _Result:
        try:
            return _Result(self.world.answer(statement, dict(parameters or {})))
        except OperationalError:
            if self.world.fault_kills_connection:
                self.bind.invalidated = True
            raise

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


# --- The world: runs, definitions, the worker, and one tick ------------------------------------------


@dataclass(slots=True)
class FakeRun:
    run_id: uuid.UUID
    lane_id: str
    scheduled_for: datetime
    status: str
    kind: str
    payload: Mapping[str, object]
    superseded_by_operator: bool = False
    has_work_items: bool = True
    final_attempt: dict[str, object] | None = None


def _definition(spec: LaneExecutionSpec) -> JobDefinitionRecord:
    return JobDefinitionRecord(
        id=uuid.uuid4(),
        name=f"{EXECUTOR_DEFINITION_PREFIX}{spec.lane_id}",
        version="2",
        handler="plantgeo.executor.command.v1",
        queue_name="default",
        concurrency_key=None,
        max_attempts=1,
        lease_seconds=spec.command_timeout_seconds + 120,
        time_budget_seconds=spec.command_timeout_seconds + 30,
        retry_policy={},  # type: ignore[arg-type]
        parameters={},
    )


def repair_payload(lane_id: str, *, max_days: int = 1) -> dict[str, object]:
    """A stored repair work item payload for `lane_id`'s first bound layer (`RepairRequest.to_payload`)."""
    layer = next(layer for layer, binding in REPAIR_BINDINGS.items() if binding.lane_id == lane_id)
    if max_days > REPAIR_BINDINGS[layer].max_days_cap:
        # An out-of-bounds request as a hand-edited or stale row would carry it: `from_payload` refuses it.
        valid = repair_payload(lane_id)
        return {**valid, "max_days": max_days}
    return RepairRequest(
        lane_id=lane_id,
        layer=layer,
        max_days=max_days,
        gap_first_day=date(2026, 9, 20),
        gap_last_day=date(2026, 9, 20),
        gap_day_count=1,
        source_ceiling_day=None,
        expected_horizon_day=None,
        authored_at=NOW,
    ).to_payload()


class FakeWorld:
    """The ledger, the lanes and the worker for one test; `tick()` runs one real `run_executor_tick`."""

    def __init__(self, specs: Mapping[str, LaneExecutionSpec], *, active: frozenset[str] | None = None) -> None:
        self.specs: dict[str, LaneExecutionSpec] = dict(specs)
        self.activation = ActivationConfig(frozenset(specs) if active is None else active)
        self.incidents = IncidentLedger()
        self.runs: list[FakeRun] = []
        self.disabled: set[str] = set()
        self.repair_definitions: set[str] = set()
        #: Lanes (definition ids) whose fenced lease is lost at their first heartbeat.
        self.lease_lost_lanes: set[str] = set()
        self.fault_kills_connection = False
        #: Lane (definition) id -> exception its checkpoint read raises; HEAD's own planning statement.
        self.checkpoint_faults: dict[str, BaseException] = {}
        self.repair_state_faults: dict[str, BaseException] = {}
        #: Lane id -> exception opening its next bucket's run raises (HEAD's own `open_job_run`).
        self.open_run_faults: dict[str, BaseException] = {}
        #: Every terminal handler outcome, per definition lane id, oldest first.
        self.outcomes: dict[str, list[JobHandlerOutcome]] = {}
        self.session = FakeSession(self)

    # -- installation --------------------------------------------------------------------------------

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
        monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType(self.specs))
        monkeypatch.setattr(job_executor_service, "parse_activation", lambda: self.activation)
        monkeypatch.setattr(job_executor_service, "_load_or_register_definition", self._load_definition)
        monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", self._checkpoint)
        monkeypatch.setattr(job_executor_service, "_definition_state", self._definition_state)
        monkeypatch.setattr(job_executor_service, "_open_scheduled_run", self._open_scheduled_run)
        monkeypatch.setattr(job_executor_service, "run_job_slice", self._run_slice)
        monkeypatch.setattr(job_executor_service, "_LANE_TURN_REPORTS", {})
        monkeypatch.setattr(job_executor_service, "_LANE_EXIT_CLASSES", {})
        monkeypatch.setattr(job_executor_service, "_LANE_TURN_VERDICTS", {})
        return self

    def set_script(self, lane_id: str, script: str, *, timeout_seconds: int = 60) -> None:
        self.specs[lane_id] = build_lane_spec(lane_id, script, timeout_seconds=timeout_seconds)

    def set_active(self, active: frozenset[str], *, quarantined: frozenset[str] = frozenset()) -> None:
        self.activation = ActivationConfig(active, quarantined)

    # -- the ledger's planning reads -----------------------------------------------------------------

    def _owning_lane(self, definition_lane_id: str) -> str:
        return definition_lane_id.removesuffix(REPAIR_LANE_SUFFIX)

    async def _load_definition(self, _session: object, spec: LaneExecutionSpec) -> JobDefinitionRecord | None:
        return None if spec.lane_id in self.disabled else _definition(spec)

    async def _definition_state(self, _session: object, spec: LaneExecutionSpec) -> tuple[uuid.UUID, bool] | None:
        fault = self.repair_state_faults.get(spec.lane_id)
        if fault is not None:
            raise fault
        return (uuid.uuid4(), True) if spec.lane_id in self.repair_definitions else None

    def runs_of(self, lane_id: str) -> list[FakeRun]:
        return sorted((run for run in self.runs if run.lane_id == lane_id), key=lambda run: run.scheduled_for)

    def latest(self, lane_id: str) -> FakeRun | None:
        runs = self.runs_of(lane_id)
        return runs[-1] if runs else None

    async def _checkpoint(self, _session: object, spec: LaneExecutionSpec) -> LatestRun | None:
        fault = self.checkpoint_faults.get(spec.lane_id)
        if fault is not None:
            raise fault
        runs = self.runs_of(spec.lane_id)
        if not runs:
            return None
        latest = runs[-1]
        streak = 0
        for run in reversed(runs):
            if run.status not in {"failed", "partial"}:
                break
            streak += 1
        return LatestRun(
            run_id=latest.run_id,
            scheduled_for=latest.scheduled_for,
            status=latest.status,
            work_claimable=latest.status in {"queued", "running"} and latest.has_work_items,
            has_work_items=latest.has_work_items,
            superseded_by_operator=latest.superseded_by_operator,
            consecutive_failures=min(streak, 3),
            definition_enabled=spec.lane_id not in self.disabled,
        )

    # -- seeding -------------------------------------------------------------------------------------

    def seed_run(  # noqa: PLR0913 - one run fact per keyword
        self,
        lane_id: str,
        scheduled_for: datetime,
        status: str,
        *,
        exit_class: str | None = None,
        attempt_status: str | None = None,
        kind: str = EXECUTOR_WORK_ITEM_KIND,
        payload: Mapping[str, object] | None = None,
        has_work_items: bool = True,
    ) -> FakeRun:
        final_attempt = None
        if status in {"failed", "succeeded", "partial"}:
            final_attempt = {
                "status": attempt_status or ("succeeded" if status == "succeeded" else "failed"),
                "failure_class": None if status == "succeeded" else "scheduled_command_exit",
                "exit_class": exit_class,
                "error_summary": None,
                "finished_at": scheduled_for + timedelta(minutes=10),
            }
        run = FakeRun(
            run_id=uuid.uuid4(),
            lane_id=lane_id,
            scheduled_for=scheduled_for,
            status=status,
            kind=kind,
            payload=dict(
                payload or {"lane_id": self._owning_lane(lane_id), "scheduled_for": scheduled_for.isoformat()}
            ),
            has_work_items=has_work_items,
            final_attempt=final_attempt,
        )
        self.runs.append(run)
        return run

    def seed_hold(self, lane_id: str, *, now: datetime = NOW, exit_class: str = "code") -> FakeRun:
        """Three failed buckets in a row before `now`'s bucket: the clock no longer releases the lane."""
        bucket = now.replace(minute=0, second=0, microsecond=0)
        runs = [self.seed_run(lane_id, bucket - HOUR * back, "failed", exit_class=exit_class) for back in (3, 2, 1)]
        return runs[-1]

    def seed_settled(self, lane_id: str, *, now: datetime = NOW) -> FakeRun:
        """The lane's CURRENT bucket already succeeded: it is not due this tick."""
        return self.seed_run(lane_id, now.replace(minute=0, second=0, microsecond=0), "succeeded", exit_class="ok")

    def open_repair_run(self, lane_id: str, *, now: datetime = NOW, max_days: int = 1) -> FakeRun:
        self.repair_definitions.add(f"{lane_id}{REPAIR_LANE_SUFFIX}")
        return self.seed_run(
            f"{lane_id}{REPAIR_LANE_SUFFIX}",
            now - timedelta(minutes=1),
            "queued",
            kind=EXECUTOR_REPAIR_WORK_ITEM_KIND,
            payload=repair_payload(lane_id, max_days=max_days),
        )

    def supersede(self, run: FakeRun) -> None:
        """What `jobs-supersede-run` records: the held run now reads `superseded_by_operator`."""
        run.superseded_by_operator = True

    # -- the worker ----------------------------------------------------------------------------------

    async def _open_scheduled_run(self, _session: object, candidate: DueLane) -> uuid.UUID:
        fault = self.open_run_faults.get(candidate.spec.lane_id)
        if fault is not None:
            raise fault
        run = self.seed_run(candidate.spec.lane_id, candidate.scheduled_for, "queued")
        return run.run_id

    def _invocation(self, run: FakeRun, *, cursor: Mapping[str, object] | None, budget: float) -> JobInvocation:
        async def heartbeat() -> bool:
            return run.lane_id not in self.lease_lost_lanes

        return JobInvocation(
            shard_key=run.scheduled_for.isoformat(),
            kind=run.kind,
            payload=run.payload,
            cursor=cursor,
            parameters={},
            attempt_number=1,
            max_attempts=1,
            progress_fraction=0.0,
            seconds_remaining=budget,
            heartbeat=heartbeat,
        )

    async def _run_slice(  # noqa: PLR0913 - mirrors `run_job_slice`'s keywords
        self,
        _session: object,
        *,
        definition_name: str,
        version: str,
        job_run_id: uuid.UUID,
        worker_id: str,
        budget_seconds: float,
        stop: object = None,
    ) -> JobSliceSummary:
        del version, stop
        run = next(run for run in self.runs if run.run_id == job_run_id)
        handler = job_executor_service.run_scheduled_command
        outcome = await handler(self._invocation(run, cursor=None, budget=budget_seconds))
        if outcome.kind == "progressed":
            outcome = await handler(self._invocation(run, cursor=outcome.cursor, budget=budget_seconds))
        self.outcomes.setdefault(run.lane_id, []).append(outcome)
        succeeded = outcome.kind == "completed"
        run.status = "succeeded" if succeeded else "failed" if outcome.kind == "failed" else "queued"
        run.final_attempt = {
            "status": "succeeded" if succeeded else "failed",
            "failure_class": outcome.failure_class,
            "exit_class": outcome.metrics.get("exit_class"),
            "error_summary": outcome.reason,
            "finished_at": datetime.now(UTC),
        }
        return JobSliceSummary(
            definition_name=definition_name,
            worker_id=worker_id,
            job_run_id=job_run_id,
            stop_reason="no_claimable_work",
            claimed=1,
            succeeded=int(succeeded),
            dead_lettered=int(outcome.kind == "failed"),
            run_status=run.status,
        )

    def last_outcome(self, lane_id: str) -> JobHandlerOutcome:
        return self.outcomes[lane_id][-1]

    # -- SQL answers ---------------------------------------------------------------------------------

    def answer(  # noqa: PLR0911 - one return per statement the fake ledger answers
        self, statement: object, params: dict[str, object]
    ) -> list[dict[str, object]]:
        if statement is lane_incidents._UPSERT_LANE_INCIDENT:
            return [self.incidents.upsert(params)]
        if statement is lane_incidents._RESOLVE_LANE_INCIDENT:
            resolved = self.incidents.resolve(params)
            return [] if resolved is None else [resolved]
        if statement is lane_incidents._SELECT_LANE_INCIDENTS:
            return self.incidents.select(params)
        if statement is lane_incidents._SELECT_RUN_FINAL_ATTEMPT:
            run = next((run for run in self.runs if run.run_id == params["job_run_id"]), None)
            return [] if run is None or run.final_attempt is None else [dict(run.final_attempt)]
        if statement is job_executor_service._TRY_LEADER_LOCK:
            return [{"acquired": True}]
        if statement is job_executor_service._RELEASE_LEADER_LOCK:
            return [{"released": True}]
        return []  # the transaction-local statement timeout

    # -- one tick --------------------------------------------------------------------------------------

    async def tick(
        self,
        *,
        now: datetime = NOW,
        soft: SoftFailureState | None,
        repair_clock: RepairAuthoringClock | None = None,
        max_lanes: int = 6,
    ) -> ExecutorTickSummary:
        return await job_executor_service.run_executor_tick(
            self.session,  # type: ignore[arg-type]
            activation=self.activation,
            now=now,
            max_lanes_per_tick=max_lanes,
            repair_clock=repair_clock,
            soft_failure=soft,
        )


def states(summary: ExecutorTickSummary) -> dict[str, str]:
    """Lane id -> the state this tick reported for it."""
    return {lane.lane_id: lane.state for lane in summary.lanes}


def events_named(logs: list[dict[str, object]], event: str) -> list[dict[str, object]]:
    return [entry for entry in logs if entry.get("event") == event]
