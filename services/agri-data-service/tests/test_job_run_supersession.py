"""`jobs-supersede-run`: lane and checkpoint refusals, the clock refusal, the dry run, the recording, idempotency.

No database: `ingest_session` yields a recorded fake session, `read_lane_checkpoint` and the pause state
answer from the test, the activation allow-list and the ledger target are pinned, matching
`test_jobs_pulse_command.py`'s convention. Real-PostgreSQL proof that the incident insert, the fingerprint
probe and the failure-streak window in `select_latest_run.sql` and the planner agree is
`test_job_run_supersession_agri_db.py`.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from agri_data_service.config import settings
from agri_data_service.execution import job_run_supersession
from agri_data_service.execution.job_executor_service import (
    RUN_SUPERSESSION_FINGERPRINT_PREFIX,
    RUN_SUPERSESSION_INCIDENT_TYPE,
    ActivationConfig,
    LatestRun,
)
from agri_data_service.execution.job_run_supersession import (
    EVIDENCE_MAX_LENGTH,
    SupersessionRefusal,
    jobs_supersede_run,
    ledger_target,
    resolve_executor_lane,
)
from agri_data_service.interface.cli.ops import ops
from agri_data_service.jobs.dispatch import LanePauseState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from click.testing import Result

_RUN_ID = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
_ITEM_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
_INCIDENT_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
_FAILED_BUCKET = datetime(2026, 9, 2, 18, tzinfo=UTC)
_NOW = datetime(2026, 9, 3, 20, 30, tzinfo=UTC)
_CURRENT_BUCKET_ISO = "2026-09-03T20:00:00+00:00"
_REPLAY_LANE = "parquet-drought"
_COALESCE_LANE = "postgres-fire-perimeters"
_INACTIVE_LANE = "jobs-strategy-mv-refresh"
_ACTIVE = ActivationConfig(frozenset({_REPLAY_LANE, _COALESCE_LANE, "vegetation-catch-up", "jobs-matview-refresh"}))
_EVIDENCE = "old executor code (e4490c3) exited 1 on every attempt; gap-fill repaired in 2b4cfef, deployed 4f2502a0"
_STORED_EVIDENCE = "first recording: DuckDB spatial extension directory fixed"
_LEDGER = "ledger.example.test:5432/plantgeo"


def _work_item_row() -> dict[str, object]:
    return {
        "work_item_id": _ITEM_ID,
        "shard_key": _FAILED_BUCKET.isoformat(),
        "status": "dead_letter",
        "attempt_count": 5,
        "max_attempts": 5,
        "completed_at": datetime(2026, 9, 2, 20, 7, tzinfo=UTC),
        "attempt_number": 5,
        "failure_class": "CalledProcessError",
        "error_summary": "lane 'parquet-drought' command exited with status 1",
        "finished_at": datetime(2026, 9, 2, 20, 7, tzinfo=UTC),
    }


class _FakeResult:
    """Both shapes `jobs.lease` drives: `.mappings().first()` and `.mappings().all()`."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> Mapping[str, object] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Mapping[str, object]]:
        return list(self._rows)


class _FakeSession:
    """Records every statement; answers the reads and the incident insert from the test's state."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.insert_returns_row = True
        self.stored_incident: dict[str, object] | None = None
        self.close_error: Exception | None = None
        self.commit_error: Exception | None = None

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> _FakeResult:
        sql = str(statement)
        self.statements.append((sql, dict(parameters or {})))
        if "FROM agri.job_work_item AS item" in sql:
            return _FakeResult([_work_item_row()])
        if "INSERT INTO agri.job_incident" in sql:
            rows = [{"id": _INCIDENT_ID, "resolved_at": datetime.now(UTC)}] if self.insert_returns_row else []
            return _FakeResult(rows)
        if "FROM agri.job_incident" in sql and "SELECT id, summary, owner, resolved_at" in sql:
            return _FakeResult([] if self.stored_incident is None else [self.stored_incident])
        return _FakeResult([])

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def inserts(self) -> list[dict[str, object]]:
        return [params for sql, params in self.statements if "INSERT INTO agri.job_incident" in sql]


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture(autouse=True)
def _pin_process_seams(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    @asynccontextmanager
    async def _fake_ingest_session() -> AsyncIterator[_FakeSession]:
        yield session
        if session.close_error is not None:
            raise session.close_error

    async def _not_paused(_session: object, _name: str) -> LanePauseState:
        return LanePauseState(registered=True, paused=False)

    monkeypatch.setattr(job_run_supersession, "ingest_session", _fake_ingest_session)
    monkeypatch.setattr(job_run_supersession, "read_lane_pause_state", _not_paused)
    monkeypatch.setattr(job_run_supersession, "parse_activation", lambda: _ACTIVE)
    monkeypatch.setattr(job_run_supersession, "_utc_now", lambda: _NOW)
    # Patched on the settings CLASS: a pydantic settings instance refuses attributes that are not fields.
    monkeypatch.setattr(
        type(settings),
        "require_local_source_loader_database_url",
        lambda _self: "postgresql+asyncpg://user:secret@ledger.example.test:5432/plantgeo",
    )


def _checkpoint(monkeypatch: pytest.MonkeyPatch, latest: LatestRun | None) -> None:
    async def _read(_session: object, _spec: object) -> LatestRun | None:
        return latest

    monkeypatch.setattr(job_run_supersession, "read_lane_checkpoint", _read)


def _failed_checkpoint(
    *,
    run_id: uuid.UUID = _RUN_ID,
    status: str = "failed",
    superseded: bool = False,
    streak: int = 1,
) -> LatestRun:
    return LatestRun(
        run_id=run_id,
        scheduled_for=_FAILED_BUCKET,
        status=status,
        work_claimable=False,
        superseded_by_operator=superseded,
        consecutive_failures=streak,
    )


def _invoke(*extra: str, lane: str = _REPLAY_LANE, run_id: uuid.UUID = _RUN_ID, evidence: str = _EVIDENCE) -> Result:
    return CliRunner().invoke(
        jobs_supersede_run,
        ["--lane", lane, "--run-id", str(run_id), "--evidence", evidence, "--operator", "jade", *extra],
    )


def test_ops_family_exposes_the_verb_once() -> None:
    assert ops.commands["jobs-supersede-run"] is jobs_supersede_run


def test_ledger_target_names_the_database_and_never_its_credentials() -> None:
    assert ledger_target("postgresql+asyncpg://postgres:hunter2@switchback.proxy.rlwy.net:37967/plantgeo") == (
        "switchback.proxy.rlwy.net:37967/plantgeo"
    )
    assert "hunter2" not in ledger_target("postgresql://u:hunter2@h/d")


def test_unknown_non_executable_and_inactive_lanes_are_refused_before_any_ledger_read(session: _FakeSession) -> None:
    unknown = _invoke(lane="no-such-lane")
    snapshot_only = _invoke(lane="soil-moisture-parquet-backfill")
    inactive = _invoke(lane=_INACTIVE_LANE)

    assert unknown.exit_code == 1
    assert "unknown lane" in unknown.output
    assert snapshot_only.exit_code == 1
    assert "never opens executor buckets" in snapshot_only.output
    assert inactive.exit_code == 1
    assert "not in the executor's active allow-list" in inactive.output
    assert session.statements == []


def test_resolve_executor_lane_admits_active_executable_lanes_only() -> None:
    assert resolve_executor_lane("vegetation-catch-up", _ACTIVE).catch_up_policy == "replay_oldest"
    assert resolve_executor_lane("jobs-matview-refresh", _ACTIVE).catch_up_policy == "coalesce_latest"
    with pytest.raises(SupersessionRefusal):
        resolve_executor_lane("soil-moisture-parquet-backfill", _ACTIVE)
    with pytest.raises(SupersessionRefusal):
        resolve_executor_lane(_INACTIVE_LANE, _ACTIVE)


def test_a_paused_lane_is_refused_before_its_checkpoint_is_read(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    async def _paused(_session: object, _name: str) -> LanePauseState:
        return LanePauseState(registered=True, paused=True)

    monkeypatch.setattr(job_run_supersession, "read_lane_pause_state", _paused)
    _checkpoint(monkeypatch, _failed_checkpoint())

    result = _invoke("--apply")

    assert result.exit_code == 1
    assert "is paused in the ledger" in result.output
    assert session.inserts() == []
    assert session.commits == 0


def test_a_lane_the_clock_releases_is_refused_without_naming_a_command(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint(streak=2))

    result = _invoke("--apply", lane=_COALESCE_LANE)

    assert result.exit_code == 1
    assert "the clock releases lane 'postgres-fire-perimeters' by itself" in result.output
    assert "2 consecutive failure(s), below this coalesce_latest lane's limit of 3" in result.output
    assert "ingest-fire-perimeters" not in result.output
    assert session.inserts() == []
    assert session.commits == 0


def test_a_coalesce_lane_held_by_the_breaker_is_accepted(
    monkeypatch: pytest.MonkeyPatch, session: _FakeSession
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint(streak=3))

    result = _invoke("--apply", lane=_COALESCE_LANE)

    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["outcome"] == "recorded"
    assert receipt["consecutive_failures"] == 3
    assert receipt["opens_no_earlier_than"] == _CURRENT_BUCKET_ISO
    assert len(session.inserts()) == 1
    assert session.commits == 1


@pytest.mark.parametrize(
    ("latest", "expected"),
    [
        (None, "has no checkpoint run"),
        (_failed_checkpoint(run_id=uuid.UUID("11111111-1111-4111-8111-111111111111")), "is not the checkpoint"),
        (_failed_checkpoint(status="running"), "is still running"),
        (_failed_checkpoint(status="succeeded"), "is not held"),
    ],
)
def test_only_the_settled_without_success_checkpoint_can_be_superseded(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
    latest: LatestRun | None,
    expected: str,
) -> None:
    _checkpoint(monkeypatch, latest)

    result = _invoke("--apply")

    assert result.exit_code == 1
    assert expected in result.output
    assert session.inserts() == []
    assert session.commits == 0


def test_the_dry_run_prints_one_receipt_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, session: _FakeSession
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint())

    result = _invoke()

    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["outcome"] == "dry_run"
    assert receipt["incident_id"] is None
    assert receipt["ledger"] == _LEDGER
    assert receipt["run_id"] == str(_RUN_ID)
    assert receipt["run_status"] == "failed"
    assert receipt["consecutive_failures"] == 1
    # The lane resumes at the current bucket, exactly as the planner will open it.
    assert receipt["opens_no_earlier_than"] == _CURRENT_BUCKET_ISO
    assert receipt["fingerprint"] == f"{RUN_SUPERSESSION_FINGERPRINT_PREFIX}{_RUN_ID}"
    assert receipt["work_items"][0]["error_summary"] == "lane 'parquet-drought' command exited with status 1"
    assert session.inserts() == []
    assert session.commits == 0
    assert session.rollbacks == 1


def test_apply_records_one_resolved_incident_and_leaves_the_run_alone(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint())

    result = _invoke("--apply")

    assert result.exit_code == 0, result.output
    # One JSON document and nothing else on stdout: the receipt must stay machine-readable.
    receipt = json.loads(result.output)
    assert receipt["outcome"] == "recorded"
    assert receipt["incident_id"] == str(_INCIDENT_ID)
    assert receipt["evidence"] == _EVIDENCE
    inserts = session.inserts()
    assert len(inserts) == 1
    assert inserts[0]["fingerprint"] == f"{RUN_SUPERSESSION_FINGERPRINT_PREFIX}{_RUN_ID}"
    assert inserts[0]["incident_type"] == RUN_SUPERSESSION_INCIDENT_TYPE
    assert inserts[0]["job_run_id"] == _RUN_ID
    assert inserts[0]["job_work_item_id"] == _ITEM_ID
    assert inserts[0]["summary"] == _EVIDENCE
    assert inserts[0]["owner"] == "jade"
    assert inserts[0]["acknowledged_by"] == "jade"
    detail = json.loads(str(inserts[0]["detail"]))
    assert detail["lane_id"] == _REPLAY_LANE
    assert detail["opens_no_earlier_than"] == receipt["opens_no_earlier_than"]
    assert detail["work_items"] == receipt["work_items"]
    assert detail["recorded_by"] == "agri-service ops jobs-supersede-run"
    assert session.commits == 1
    assert session.rollbacks == 0
    # Only the incident insert writes; no UPDATE or DELETE ever reaches the ledger.
    assert not any(sql.lstrip().upper().startswith(("UPDATE", "DELETE")) for sql, _ in session.statements)


def test_an_already_superseded_run_reports_the_stored_evidence_not_the_supplied_one(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint(superseded=True))
    session.stored_incident = {"id": _INCIDENT_ID, "summary": _STORED_EVIDENCE, "owner": "first-operator"}

    result = _invoke("--apply")

    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["outcome"] == "already_superseded"
    assert receipt["incident_id"] == str(_INCIDENT_ID)
    assert receipt["evidence"] == _STORED_EVIDENCE
    assert receipt["operator"] == "first-operator"
    assert session.inserts() == []
    assert session.commits == 0
    assert session.rollbacks == 1


def test_a_conflicting_recording_is_reported_with_the_winning_evidence(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint())
    session.insert_returns_row = False
    session.stored_incident = {"id": _INCIDENT_ID, "summary": _STORED_EVIDENCE, "owner": "first-operator"}

    result = _invoke("--apply")

    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["outcome"] == "already_superseded"
    assert receipt["evidence"] == _STORED_EVIDENCE
    assert len(session.inserts()) == 1
    assert session.commits == 0
    assert session.rollbacks == 1


def test_a_recording_that_cannot_be_read_back_is_a_refusal_not_an_outcome(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint(superseded=True))
    session.stored_incident = None

    result = _invoke("--apply")

    assert result.exit_code == 1
    assert "could not be read back" in result.output
    assert session.inserts() == []
    assert session.commits == 0


def test_a_commit_that_fails_prints_write_failed_and_never_claims_recorded(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint())
    session.commit_error = OperationalError("COMMIT", {}, Exception("connection reset"))

    result = _invoke("--apply")

    assert result.exit_code == 1
    receipt_line, *rest = result.output.splitlines()
    receipt = json.loads(receipt_line)
    assert receipt["outcome"] == "write_failed"
    assert receipt["incident_id"] is None
    assert any("NOT durable" in line for line in rest)
    assert session.commits == 0


def test_a_connection_lost_after_the_recording_still_prints_the_receipt(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint())
    session.close_error = OperationalError("SELECT 1", {}, Exception("connection reset"))

    result = _invoke("--apply")

    assert result.exit_code == 1
    receipt_line, *rest = result.output.splitlines()
    receipt = json.loads(receipt_line)
    assert receipt["outcome"] == "recorded"
    assert any("the receipt above was reached" in line for line in rest)
    assert session.commits == 1


def test_a_connection_lost_before_any_receipt_says_nothing_was_recorded(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    async def _read(_session: object, _spec: object) -> LatestRun | None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(job_run_supersession, "read_lane_checkpoint", _read)

    result = _invoke("--apply")

    assert result.exit_code == 1
    assert "nothing was recorded" in result.output
    assert session.inserts() == []


def test_a_missing_or_unparseable_dsn_is_one_refusal_line_without_the_dsn(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> None:
    _checkpoint(monkeypatch, _failed_checkpoint())
    monkeypatch.setattr(type(settings), "require_local_source_loader_database_url", lambda _self: "not a dsn:secret")

    result = _invoke()

    assert result.exit_code == 1
    assert "could not be parsed" in result.output
    assert "secret" not in result.output
    assert session.statements == []


@pytest.mark.parametrize("evidence", ["", "   ", "x" * (EVIDENCE_MAX_LENGTH + 1)])
def test_evidence_must_be_present_and_bounded(session: _FakeSession, evidence: str) -> None:
    result = _invoke(evidence=evidence)

    assert result.exit_code == 2
    assert "--evidence" in result.output
    assert session.statements == []
