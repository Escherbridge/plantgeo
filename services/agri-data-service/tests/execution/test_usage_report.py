"""GL-4: `agri-service ops jobs-usage-report` and its loaders (plan 0W.4, spec Sec 4.9.2).

Every DB-facing test here drives `execution/usage_report.py` against a fake `AsyncSession` that
dispatches on statement IDENTITY (`statement is usage_report._SELECT_...`), the same pattern
`tests/test_job_lane_control.py::Session` already established for this codebase's other CLI/session
code -- it proves the module issues the RIGHT statements, in the right transaction shape, without a
live PostgreSQL connection.
"""

from __future__ import annotations

import ast
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from agri_data_service.execution import usage_report
from agri_data_service.interface.cli.ops import ops

ATTEMPT_A = uuid.uuid4()
ATTEMPT_B = uuid.uuid4()


def usage_row(**overrides: object) -> dict[str, object]:
    """One row as `select_provider_usage.sql` would return it; a metered soil-on-paid-Open-Meteo turn."""
    base: dict[str, object] = {
        "attempt_id": ATTEMPT_A,
        "job_run_id": uuid.uuid4(),
        "job_work_item_id": uuid.uuid4(),
        "lane_id": "soil-era5-land-direct-forward",
        "definition_name": "plantgeo.executor.soil-era5-land-direct-forward",
        "scheduled_for": datetime(2026, 9, 1, tzinfo=UTC),
        "started_at": datetime(2026, 9, 1, 6, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 9, 1, 6, 5, tzinfo=UTC),
        "attempt_status": "succeeded",
        "started_on": date(2026, 9, 1),
        "exit_class": "ok",
        "turn_outcome": "completed",
        "probe_status": None,
        "spawned": True,
        "report_present": True,
        "usage_reported": True,
        "usage_complete": True,
        "unwritten_known": True,
        "elapsed_seconds": 300,
        "start_lag_seconds": 5,
        "rss_peak_kib": 40000,
        "cpu_seconds": 12,
        "meter_errors": 0,
        "requests": 24,
        "weighted_calls": 1500,
        "fetch_attempts": 24,
        "rows_written": 100,
        "bytes_written": 2000,
        "publication_debt": 0,
        "charged_basis": "metered",
        "charged": 1500,
        "suspect": 0,
        "host": "customer-abc.open-meteo.com",
        "provider": "open-meteo",
        "pool": "open-meteo-paid",
        "http_requests": 24,
        "http_2xx": 24,
        "http_3xx": 0,
        "http_4xx": 0,
        "http_429": 0,
        "http_5xx": 0,
        "transport_failures": 0,
        "bytes_in": 100000,
        "backoff_seconds": 0,
        "weighted_calls_metered": 1500,
        "last_send_outcome": "2xx",
    }
    base.update(overrides)
    return base


def month_to_date_row(pool: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "pool": pool,
        "epoch_at": datetime(2026, 9, 1, tzinfo=UTC),
        "metered_count": 1,
        "reported_count": 0,
        "suspect_basis_count": 0,
        "not_spawned_count": 0,
        "lost_count": 0,
        "charged": 1500,
        "suspect": 0,
    }
    base.update(overrides)
    return base


def incident_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "fingerprint": "lane_hold:soil-era5-land-direct-forward",
        "incident_type": "lane_hold",
        "severity": "warning",
        "status": "open",
        "summary": "held after 3 upstream failures",
        "occurrence_count": 3,
        "first_seen_at": datetime(2026, 9, 27, tzinfo=UTC),
        "last_seen_at": datetime(2026, 9, 28, tzinfo=UTC),
        "cooldown_until": None,
        "owner": None,
        "acknowledged_at": None,
        "acknowledged_by": None,
        "state": "held",
        "rung": "2",
        "exit_class": "upstream",
        "chain_first_seen_at": None,
    }
    base.update(overrides)
    return base


class Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = list(rows)

    def mappings(self) -> Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None


class FakeNestedTransaction:
    async def __aenter__(self) -> FakeNestedTransaction:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False  # never swallow -- matches begin_nested's real rollback-then-reraise contract


class FakeSession:
    def __init__(self) -> None:
        self.executed: list[tuple[object, dict[str, object]]] = []
        self.rolled_back = False
        self.usage_rows: list[dict[str, object]] = []
        self.month_to_date_rows: dict[str, dict[str, object]] = {}
        self.incident_rows: list[dict[str, object]] = []
        self.fail_usage = False
        self.fail_incidents = False

    async def execute(self, statement: object, parameters: dict[str, object] | None = None) -> Result:
        params = parameters or {}
        self.executed.append((statement, params))
        if statement is usage_report._SET_STATEMENT_TIMEOUT or statement is usage_report._SET_READ_ONLY:
            return Result([])
        if statement is usage_report._SELECT_PROVIDER_USAGE:
            if self.fail_usage:
                raise RuntimeError("select_provider_usage failed")
            return Result(self.usage_rows)
        if statement is usage_report._SELECT_MONTH_TO_DATE:
            row = self.month_to_date_rows.get(str(params["pool"]))
            return Result([row] if row else [])
        if statement is usage_report._SELECT_OPEN_INCIDENTS:
            if self.fail_incidents:
                raise RuntimeError("select_open_incidents failed")
            return Result(self.incident_rows)
        raise AssertionError(f"unexpected statement executed: {statement}")

    def begin_nested(self) -> FakeNestedTransaction:
        return FakeNestedTransaction()

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    fake = FakeSession()

    @asynccontextmanager
    async def fake_ingest_session() -> Any:
        yield fake

    monkeypatch.setattr(usage_report, "ingest_session", fake_ingest_session)
    return fake


def _all_pools_month_to_date() -> dict[str, dict[str, object]]:
    return {pool: month_to_date_row(pool) for pool in sorted(usage_report.POOL_LABELS)}


# --- The transaction shape --------------------------------------------------------------------


async def test_runs_in_a_read_only_transaction_with_statement_timeout(session: FakeSession) -> None:
    session.month_to_date_rows = _all_pools_month_to_date()
    await usage_report.build_report(days=1, since=None, until=None, lane_ids=None, pool=None, by="lane")
    statements = [statement for statement, _ in session.executed]
    assert usage_report._SET_STATEMENT_TIMEOUT in statements
    assert usage_report._SET_READ_ONLY in statements
    # Both bounds are pinned before the first real query, not after -- an unbounded or writable
    # statement must never slip in ahead of them.
    first_query_index = statements.index(usage_report._SELECT_PROVIDER_USAGE)
    assert statements.index(usage_report._SET_STATEMENT_TIMEOUT) < first_query_index
    assert statements.index(usage_report._SET_READ_ONLY) < first_query_index
    assert session.rolled_back is True, "a read-only report must never leave an open transaction"


async def test_sections_are_fault_isolated(session: FakeSession) -> None:
    session.fail_usage = True
    session.month_to_date_rows = {"open-meteo-paid": month_to_date_row("open-meteo-paid")}
    session.incident_rows = [incident_row()]
    report = await usage_report.build_report(
        days=1, since=None, until=None, lane_ids=None, pool="open-meteo-paid", by="lane"
    )
    usage_section = report["usage"]
    assert isinstance(usage_section, dict), "a failing section reports its own error, not a raise out of build_report"
    assert "error" in usage_section
    month_to_date_section = report["month_to_date"]
    assert isinstance(month_to_date_section, dict)
    assert month_to_date_section["open-meteo-paid"]["charged"] == 1500  # noqa: PLR2004 - month_to_date_row's own default
    open_incidents_section = report["open_incidents"]
    assert isinstance(open_incidents_section, list)
    assert len(open_incidents_section) == 1
    assert session.rolled_back is True


async def test_a_second_section_still_runs_after_the_first_ones_savepoint_rolls_back(session: FakeSession) -> None:
    session.fail_usage = True
    session.month_to_date_rows = _all_pools_month_to_date()
    session.incident_rows = [incident_row()]
    report = await usage_report.build_report(days=1, since=None, until=None, lane_ids=None, pool=None, by="lane")
    assert isinstance(report["usage"], dict)
    assert "error" in report["usage"]
    # The savepoint's rollback must leave the transaction usable for the section after it, not just
    # report an error and then silently return nothing for everything downstream.
    assert isinstance(report["open_incidents"], list)
    assert len(report["open_incidents"]) == 1
    assert report["open_incidents"][0]["incident_type"] == "lane_hold"


# --- Grouping (pure, no session) ------------------------------------------------------------------


def test_groups_by_day_pool_host_and_lane() -> None:
    rows = [
        usage_row(),
        usage_row(
            attempt_id=ATTEMPT_B,
            lane_id="fire-detections-direct-forward",
            definition_name="plantgeo.executor.fire-detections-direct-forward",
            host="firms.modaps.eosdis.nasa.gov",
            provider="firms",
            pool="firms",
            http_requests=10,
            http_429=1,
            weighted_calls_metered=0,
            charged_basis="not_spawned",
            charged=0,
            suspect=0,
        ),
    ]
    by_pool = usage_report.group_usage_rows(rows, by="pool")
    assert {bucket["pool"] for bucket in by_pool} == {"open-meteo-paid", "firms"}
    by_lane = usage_report.group_usage_rows(rows, by="lane")
    assert {bucket["lane_id"] for bucket in by_lane} == {
        "soil-era5-land-direct-forward",
        "fire-detections-direct-forward",
    }
    by_host = usage_report.group_usage_rows(rows, by="host")
    assert {bucket["host"] for bucket in by_host} == {"customer-abc.open-meteo.com", "firms.modaps.eosdis.nasa.gov"}
    by_day = usage_report.group_usage_rows(rows, by="day")
    assert len(by_day) == 1, "both fixture rows share the same started_on day"
    assert by_day[0]["attempts"] == 2  # noqa: PLR2004 - two fixture rows were grouped


def test_repair_definitions_roll_up_to_the_owning_lane() -> None:
    """`select_provider_usage.sql` strips `REPAIR_LANE_SUFFIX` before Python ever sees a row, so a
    forward attempt and its repair already carry the SAME `lane_id` even though their
    `definition_name` differs by the literal `:gap-repair` suffix -- grouping by lane must merge them."""
    forward = usage_row()
    repair = usage_row(
        attempt_id=ATTEMPT_B,
        definition_name="plantgeo.executor.soil-era5-land-direct-forward:gap-repair",
    )
    buckets = usage_report.group_usage_rows([forward, repair], by="lane")
    assert len(buckets) == 1
    assert buckets[0]["lane_id"] == "soil-era5-land-direct-forward"
    assert buckets[0]["attempts"] == 2  # noqa: PLR2004 - forward + repair, two fixture rows
    assert buckets[0]["charged"] == 3000  # noqa: PLR2004 - two usage_row()s at 1500 each


def test_a_hostless_attempt_still_contributes_outcome_counts_but_no_phantom_host_row() -> None:
    hostless = usage_row(
        attempt_id=ATTEMPT_B,
        host=None,
        provider=None,
        pool=None,
        http_requests=None,
        weighted_calls_metered=None,
        exit_class="config",
        turn_outcome="config_error",
        charged_basis="not_spawned",
        charged=0,
        suspect=0,
    )
    by_lane = usage_report.group_usage_rows([usage_row(), hostless], by="lane")
    assert len(by_lane) == 1
    bucket = by_lane[0]
    assert bucket["attempts"] == 2  # noqa: PLR2004 - two fixture rows, one hostless
    assert bucket["exit_class_counts"]["config"] == 1
    # http_requests only ever comes off a real host row -- the hostless placeholder contributes 0.
    assert bucket["http_requests"] == 24  # noqa: PLR2004 - usage_row()'s own fixture default


# --- month_to_date: the sole loader, and the basis split ------------------------------------------


async def test_suspect_basis_is_separate_from_charged(session: FakeSession) -> None:
    session.month_to_date_rows = {
        "open-meteo-paid": month_to_date_row(
            "open-meteo-paid", charged=900_000, suspect=1602, suspect_basis_count=1, lost_count=1
        )
    }
    section = await usage_report.month_to_date(session, pool="open-meteo-paid", now=datetime(2026, 9, 28, tzinfo=UTC))
    assert section["charged"] == 900_000  # noqa: PLR2004 - this test's own month_to_date_row override
    assert section["suspect"] == 1602  # noqa: PLR2004 - LANE_LOGICAL_CAPS["soil"], this test's own fixture override
    assert section["basis_split"]["suspect"] == 1
    assert section["basis_split"]["lost"] == 1
    assert section["budget"]["monthly_cap"] == usage_report.PAID_MONTHLY_BUDGET


async def test_a_non_weighted_pool_carries_no_budget_lines(session: FakeSession) -> None:
    session.month_to_date_rows = {"usgs-water-data": month_to_date_row("usgs-water-data", charged=0, suspect=0)}
    section = await usage_report.month_to_date(session, pool="usgs-water-data", now=datetime(2026, 9, 28, tzinfo=UTC))
    assert section["budget"] is None


def test_month_to_date_is_the_only_loader() -> None:
    """`execution/provider_budget.py` (G1) must import `month_to_date` rather than re-loading
    `select_provider_month_to_date.sql` itself -- an AST walk of every module under `src/` finds
    exactly one `load_query_sql("execution/select_provider_month_to_date.sql")` call, here."""
    src_root = Path(usage_report.__file__).resolve().parents[1]
    call_sites: list[Path] = []
    for module in src_root.rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "load_query_sql" or not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and first_arg.value == "execution/select_provider_month_to_date.sql":
                call_sites.append(module)
    assert call_sites == [Path(usage_report.__file__).resolve()]


# --- The CLI verb -----------------------------------------------------------------------------


def test_verb_is_registered_and_help_parses() -> None:
    assert "jobs-usage-report" in ops.commands
    result = CliRunner().invoke(ops, ["jobs-usage-report", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--days", "--since", "--until", "--lane", "--pool", "--by", "--format"):
        assert flag in result.output


def test_json_on_stdout_logs_on_stderr(session: FakeSession) -> None:
    session.month_to_date_rows = _all_pools_month_to_date()
    result = CliRunner().invoke(ops, ["jobs-usage-report", "--days", "1"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, "stdout must carry exactly the report's JSON line, nothing else interleaved"
    payload = json.loads(lines[0])
    assert payload["event"] == "plantgeo_usage_report"
    assert set(payload) >= {"month_to_date", "usage", "open_incidents", "window"}


def test_table_format_does_not_raise_and_names_every_section(session: FakeSession) -> None:
    session.month_to_date_rows = _all_pools_month_to_date()
    session.usage_rows = [usage_row()]
    session.incident_rows = [incident_row()]
    result = CliRunner().invoke(ops, ["jobs-usage-report", "--days", "1", "--format", "table"])
    assert result.exit_code == 0, result.output
    for heading in ("month_to_date", "usage", "open_incidents"):
        assert heading in result.output


def test_pool_and_by_flags_are_validated_choices() -> None:
    result = CliRunner().invoke(ops, ["jobs-usage-report", "--pool", "not-a-real-pool"])
    assert result.exit_code != 0
    result = CliRunner().invoke(ops, ["jobs-usage-report", "--by", "not-a-real-dimension"])
    assert result.exit_code != 0


def test_days_and_since_until_are_mutually_exclusive() -> None:
    now = datetime(2026, 9, 28, tzinfo=UTC)
    with pytest.raises(usage_report.click.BadParameter):
        usage_report.resolve_window(days=1, since="2026-09-01", until="2026-09-02", now=now)
    with pytest.raises(usage_report.click.BadParameter):
        usage_report.resolve_window(days=None, since="2026-09-01", until=None, now=now)


def test_since_until_window_is_half_open_and_inclusive_of_until_day() -> None:
    now = datetime(2026, 9, 28, tzinfo=UTC)
    window = usage_report.resolve_window(days=None, since="2026-09-01", until="2026-09-03", now=now)
    assert window.since == datetime(2026, 9, 1, tzinfo=UTC)
    assert window.until == datetime(2026, 9, 4, tzinfo=UTC)
