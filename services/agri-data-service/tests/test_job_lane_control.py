"""Operator pause/resume preserves run/work state and records only committed controls.

CA2 (config-driven ingestion spec §4.8.9): the brake resolves through the lane catalogue, so a config lane
and its `<lane>:gap-fill` definition are braked like a legacy lane, and the next tick dispatches neither.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import click
import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from agri_data_service.execution import job_executor_service, lane_catalogue
from agri_data_service.execution import job_lane_control as control
from agri_data_service.execution.job_executor_service import EXECUTOR_DEFINITION_PREFIX
from agri_data_service.execution.lane_ids import SOIL_DIRECT_LANE_ID
from agri_data_service.foundation.lane_config import LANES_DIRECTORY_ENV_VAR
from agri_data_service.interface.cli.ops import ops
from tests.execution.soft_failure_fakes import COMPLETE_REPORT_SCRIPT, HOUR, NOW, FakeWorld, states
from tests.lane_config.builders import settled_soil_lane, write_lane_tree

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from pathlib import Path

NAME = "plantgeo.executor.sensors-direct-forward"
OTHER = "plantgeo.executor.parquet-signal"
INCIDENT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def definition(version: str, enabled: bool, name: str = NAME) -> dict[str, object]:
    return {"id": uuid.uuid4(), "name": name, "version": version, "enabled": enabled}


class Result:
    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self.rows = list(rows)

    def mappings(self) -> Result:
        return self

    def all(self) -> list[Mapping[str, object]]:
        return self.rows

    def first(self) -> Mapping[str, object] | None:
        return self.rows[0] if self.rows else None


class Session:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[object] = []
        self.audits: list[dict[str, Any]] = []
        self.committed = False
        self.rolled_back = False
        self.fail_audit = False
        self.fail_commit = False
        self.corrupt_readback = False
        self.updated = False
        self.held_run = {"status": "failed", "superseded": False}
        self.work = {"status": "running", "lease_expires_at": "future", "fencing_token": 17}

    async def execute(self, statement: object, parameters: Mapping[str, Any] | None = None) -> Result:
        self.calls.append(statement)
        values = parameters or {}
        if statement is control._SELECT or statement is control._LOCK:
            rows = [dict(row) for row in self.rows if row["name"] == values["name"]]
            if self.corrupt_readback and self.updated:
                rows[0]["enabled"] = not rows[0]["enabled"]
            return Result(rows[: values["limit"]])
        if statement is control._UPDATE:
            changed = []
            for row in self.rows:
                if row["name"] == values["name"] and row["enabled"] != values["enabled"]:
                    row["enabled"] = values["enabled"]
                    changed.append({"id": row["id"]})
            self.updated = True
            return Result(changed)
        if statement is control._AUDIT:
            if self.fail_audit:
                raise OperationalError("audit", {}, RuntimeError("failed"))
            self.audits.append(dict(values))
            return Result([{"id": INCIDENT_ID}])
        return Result([])

    async def commit(self) -> None:
        if self.fail_commit:
            raise OperationalError("commit", {}, RuntimeError("unknown"))
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> Session:
    fake = Session([definition("1", True), definition("2", True), definition("2", True, OTHER)])

    @asynccontextmanager
    async def opening() -> AsyncIterator[Session]:
        original = copy.deepcopy(fake.rows)
        try:
            yield fake
        finally:
            if not fake.committed:
                fake.rows[:] = original
                fake.audits.clear()

    monkeypatch.setattr(control, "ingest_session", opening)
    monkeypatch.setattr(
        type(control.settings),
        "require_local_source_loader_database_url",
        lambda _self: "postgresql+asyncpg://operator:secret@ledger/db",
    )
    return fake


def invoke(*extra: str) -> Any:
    return CliRunner().invoke(
        ops,
        [
            "jobs-set-lane-enabled",
            "--definition",
            NAME,
            "--operator",
            "repair-operator",
            "--reason",
            "preserve source repair window",
            *extra,
        ],
    )


def test_dry_run_does_not_lock_write_or_release(session: Session) -> None:
    result = invoke("--disabled")
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["outcome"] == "dry_run"
    assert receipt["after"] is None
    assert receipt["quiescence_proven"] is False
    assert session.calls.count(control._SELECT) == 1
    assert control._LOCK not in session.calls
    assert control._UPDATE not in session.calls
    assert not session.audits
    assert session.rolled_back
    assert not session.committed
    assert "secret" not in result.output


def test_pause_all_versions_exact_name_with_audit_and_unchanged_work(session: Session) -> None:
    before_work = dict(session.work)
    before_run = dict(session.held_run)
    result = invoke("--disabled", "--apply")
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["outcome"] == "changed"
    assert receipt["incident_id"] == str(INCIDENT_ID)
    assert all(row["enabled"] is False for row in session.rows if row["name"] == NAME)
    assert next(row for row in session.rows if row["name"] == OTHER)["enabled"] is True
    assert session.work == before_work
    assert session.held_run == before_run
    assert session.committed
    audit = session.audits[0]
    assert audit["owner"] == "repair-operator"
    assert audit["summary"] == "preserve source repair window"
    assert audit["fingerprint"].startswith("executor-lane-control:")
    assert json.loads(audit["detail"])["held_runs_released"] is False
    assert "job_run" not in str(control._UPDATE)
    assert "job_work_item" not in str(control._UPDATE).split("UPDATE", 1)[1]
    assert "'executor_lane_control'" in str(control._AUDIT)


def test_repeat_state_is_idempotent_but_audited_and_resume_updates_all(session: Session) -> None:
    for row in session.rows:
        if row["name"] == NAME:
            row["enabled"] = False
    repeat = invoke("--disabled", "--apply")
    assert repeat.exit_code == 0, repeat.output
    assert json.loads(repeat.output)["outcome"] == "already_set"
    assert json.loads(repeat.output)["versions_changed"] == 0
    resumed = invoke("--enabled", "--apply")
    assert resumed.exit_code == 0, resumed.output
    assert all(row["enabled"] is True for row in session.rows)
    assert len(session.audits) == len((repeat, resumed))
    assert session.held_run == {"status": "failed", "superseded": False}


@pytest.mark.parametrize("name", ["sensors-direct-forward", "plantgeo.executor.postgres-sensors", NAME + " ", "other"])
def test_unknown_exact_identity_is_refused(name: str) -> None:
    with pytest.raises(control.LaneControlRefusal, match="exactly name"):
        control.resolve_definition(name)


@pytest.mark.parametrize("shape", ["missing", "duplicate_version", "missing_current", "overflow"])
def test_ambiguous_or_missing_inventory_refuses_before_update(session: Session, shape: str) -> None:
    if shape == "missing":
        session.rows = []
    elif shape == "duplicate_version":
        session.rows = [definition("2", True), definition("2", False)]
    elif shape == "missing_current":
        session.rows = [definition("1", True)]
    else:
        session.rows = [definition(str(i), True) for i in range(control.MAX_VERSIONS + 1)]
    result = invoke("--disabled", "--apply")
    assert result.exit_code != 0
    assert control._UPDATE not in session.calls
    assert not session.committed


@pytest.mark.parametrize("failure", ["audit", "readback", "commit"])
def test_failed_control_never_claims_committed_success(session: Session, failure: str) -> None:
    session.fail_audit = failure == "audit"
    session.corrupt_readback = failure == "readback"
    session.fail_commit = failure == "commit"
    result = invoke("--disabled", "--apply")
    assert result.exit_code != 0
    assert not session.committed
    assert all(row["enabled"] is True for row in session.rows)
    assert not session.audits
    if failure != "readback":
        receipt = json.loads(result.output.splitlines()[0])
        assert receipt["outcome"] == "write_unconfirmed"
        assert receipt["held_runs_released"] is False


def test_required_desired_state_and_operator_reason(session: Session) -> None:
    result = invoke()
    assert result.exit_code != 0
    assert "choose --enabled or --disabled" in result.output
    for label, limit in (("operator", control.MAX_OPERATOR), ("reason", control.MAX_REASON)):
        for value in (" ", "x" * (limit + 1)):
            with pytest.raises(click.BadParameter, match="characters"):
                control._bounded(value, label, limit)
    assert not session.calls


# --- CA2: a config lane and its gap-fill are braked through the lane catalogue ----------------------------

CONFIG_LANE = SOIL_DIRECT_LANE_ID
CONFIG_GAP_FILL = f"{CONFIG_LANE}:gap-fill"


def _brake(name: str) -> None:
    result = CliRunner().invoke(
        ops,
        [
            "jobs-set-lane-enabled",
            "--definition",
            name,
            "--disabled",
            "--operator",
            "repair-operator",
            "--reason",
            "config-lane rollback drill",
            "--apply",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["outcome"] == "changed"


def test_a_braked_config_lane_and_its_gap_fill_are_not_dispatched_on_the_next_tick(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The brake is a ledger write the planner reads; neither the lane's spec nor an allow-list token is needed."""
    lane = settled_soil_lane(
        CONFIG_LANE,
        executor="config",
        schedule={
            "forward_cron": "0 * * * *",
            "gap_fill_cron": "0 * * * *",
            "gap_fill_enabled": True,
            "gap_fill_enabled_at_gate": "G6",
        },
    )
    monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(write_lane_tree(tmp_path, [lane])))
    monkeypatch.setattr(lane_catalogue, "RUNNER_COMMAND", (sys.executable, "-c", COMPLETE_REPORT_SCRIPT))
    monkeypatch.setattr(job_executor_service, "_NEVER_RUN_FIRST_SEEN", {})
    world = FakeWorld({}, active=frozenset()).install(monkeypatch)
    current_fire = NOW.replace(minute=0, second=0, microsecond=0)
    for lane_id in (CONFIG_LANE, CONFIG_GAP_FILL):
        world.seed_run(lane_id, current_fire - HOUR, "succeeded", exit_class="ok")

    unbraked = asyncio.run(world.tick(soft=None))
    assert states(unbraked) == {CONFIG_LANE: "ran", CONFIG_GAP_FILL: "ran"}, "control: both dispatch unbraked"

    names = [f"{EXECUTOR_DEFINITION_PREFIX}{lane_id}" for lane_id in (CONFIG_LANE, CONFIG_GAP_FILL)]
    session.rows = [definition("2", True, name) for name in names]
    for name in names:
        _brake(name)
    world.disabled = {
        str(row["name"]).removeprefix(EXECUTOR_DEFINITION_PREFIX) for row in session.rows if row["enabled"] is False
    }
    braked = asyncio.run(world.tick(now=NOW + HOUR, soft=None))

    assert world.disabled == {CONFIG_LANE, CONFIG_GAP_FILL}
    assert states(braked) == {CONFIG_LANE: "paused", CONFIG_GAP_FILL: "paused"}
    assert [len(world.runs_of(lane_id)) for lane_id in (CONFIG_LANE, CONFIG_GAP_FILL)] == [2, 2], "no run opened"


def test_a_gap_fill_definition_the_catalogue_does_not_hold_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a definition some path dispatches may be braked; a legacy lane has no `:gap-fill`."""
    monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(write_lane_tree(tmp_path, [])))
    with pytest.raises(control.LaneControlRefusal, match="exactly name"):
        control.resolve_definition(f"{EXECUTOR_DEFINITION_PREFIX}{CONFIG_GAP_FILL}")
