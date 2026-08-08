"""The backfill console reports the ledger honestly and never raises on a bad read."""

# These tests stub AsyncSession, so they prove shape, derivation and rendering only --
# never that the SQL runs. Real-database coverage of the three ops queries belongs in the
# agri-sweep-db suite, alongside the other job-ledger protocol tests.

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from agri_data_service.routes import ops as ops_route

_HTTP_OK = 200
_RUN_ID = UUID("11111111-2222-3333-4444-555555555555")
# 40 of 100 windows done; 24 succeeded in a 24-hour window is 1/hour, so 60 outstanding is 60h.
_EXPECTED_COMPLETION_PERCENT = 40.0
_EXPECTED_ETA_HOURS = 60.0
_EXPECTED_TREND_CUMULATIVE = 3


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self.rows)


class _Session:
    """Answer each ops query by matching a fragment unique to its statement."""

    def __init__(
        self,
        *,
        lanes: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        trend: list[dict[str, Any]],
    ) -> None:
        self.lanes = lanes
        self.failures = failures
        self.trend = trend
        self.parameters: list[dict[str, Any]] = []

    async def execute(self, statement: object, parameters: dict[str, Any]) -> _Result:
        self.parameters.append(parameters)
        rendered = str(statement)
        if "WITH item_rollup AS" in rendered:
            return _Result(self.lanes)
        if "LEFT JOIN LATERAL" in rendered:
            return _Result(self.failures)
        if "WITH daily_dead_letters AS" in rendered:
            return _Result(self.trend)
        raise AssertionError(f"unexpected statement: {rendered[:120]}")


class _FailingSession:
    async def execute(self, _statement: object, _parameters: dict[str, Any]) -> _Result:
        raise PermissionError("permission denied for table job_work_item")


def _session_factory(session: object) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[object]:
        yield session

    return factory


def _lane(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "definition_name": "firms-archive",
        "definition_version": "1",
        "definition_enabled": True,
        "definition_schedule": "5,35 * * * *",
        "job_run_id": _RUN_ID,
        "logical_run_key": "firms-archive:2000-11-01",
        "run_status": "running",
        "scheduled_for": datetime(2026, 8, 1, tzinfo=UTC),
        "run_started_at": datetime(2026, 8, 1, tzinfo=UTC),
        "run_completed_at": None,
        "total_work_items": 100,
        "succeeded_work_items": 40,
        "failed_work_items": 2,
        "total_items": 100,
        "queued_items": 55,
        "leased_items": 1,
        "running_items": 0,
        "retry_wait_items": 2,
        "deferred_items": 0,
        "succeeded_items": 40,
        "dead_letter_items": 2,
        "cancelled_items": 0,
        "outstanding_items": 60,
        "stalled_lease_items": 1,
        "succeeded_in_window": 24,
        "dead_lettered_in_window": 1,
        "open_attempts": 1,
        "recent_worker_count": 1,
        "oldest_outstanding_shard_key": "2000-11-02",
        "outstanding_progress_fraction": 0.25,
        "next_attempt_at": datetime(2026, 8, 7, 12, 30, tzinfo=UTC),
        "last_item_completed_at": datetime(2026, 8, 7, 11, 30, tzinfo=UTC),
        "last_attempt_started_at": datetime(2026, 8, 7, 11, 45, tzinfo=UTC),
        "last_checkpoint_at": datetime(2026, 8, 7, 11, 40, tzinfo=UTC),
        "last_recorded_activity_at": datetime(2026, 8, 7, 11, 45, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def _failure() -> dict[str, Any]:
    return {
        "definition_name": "firms-archive",
        "logical_run_key": "firms-archive:2000-11-01",
        "shard_key": "2000-11-03",
        "item_status": "dead_letter",
        "attempt_count": 8,
        "max_attempts": 8,
        "last_error_class": "ConnectError",
        "last_error_summary": "getaddrinfo failed <script>",
        "completed_at": datetime(2026, 8, 7, 10, 0, tzinfo=UTC),
        "next_attempt_at": None,
        "attempt_number": 8,
        "attempt_worker_id": "cron-archive-firms",
        "attempt_status": "failed",
        "attempt_finished_at": datetime(2026, 8, 7, 10, 0, tzinfo=UTC),
        "attempt_failure_class": "ConnectError",
        "attempt_error_summary": "getaddrinfo failed <script>alert(1)</script>",
    }


def _trend() -> dict[str, Any]:
    return {
        "definition_name": "firms-archive",
        "day": datetime(2026, 8, 6, tzinfo=UTC),
        "dead_lettered": 3,
        "cumulative_dead_lettered": 3,
    }


def _request(**query: str) -> Any:
    return SimpleNamespace(args=query)


@pytest.mark.asyncio
async def test_snapshot_json_derives_rate_and_eta_and_stays_json_serializable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stalled_lane = _lane(
        definition_name="streamflow-archive",
        logical_run_key="streamflow-archive:2000-11-01",
        succeeded_in_window=0,
        outstanding_items=5,
    )
    session = _Session(lanes=[_lane(), stalled_lane], failures=[_failure()], trend=[_trend()])
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    response = await ops_route.backfill_snapshot_json(_request())
    payload = json.loads(response.body)

    assert response.status == _HTTP_OK
    assert payload["error"] is None
    assert payload["throughput_window_hours"] == ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS
    moving, stalled = payload["lanes"]
    assert moving["throughput_per_hour"] == 1.0
    assert moving["completion_percent"] == _EXPECTED_COMPLETION_PERCENT
    assert moving["eta_hours"] == _EXPECTED_ETA_HOURS
    assert "T" in moving["eta_ready_at"]
    # Nothing succeeded in the window, so the ETA is unknowable and must not be faked.
    assert stalled["throughput_per_hour"] == 0.0
    assert stalled["eta_hours"] is None
    assert stalled["eta_ready_at"] is None
    assert moving["job_run_id"] == str(_RUN_ID)
    assert moving["last_recorded_activity_at"] == "2026-08-07T11:45:00+00:00"
    assert payload["failures"][0]["shard_key"] == "2000-11-03"
    assert payload["dead_letter_trend"][0]["cumulative_dead_lettered"] == _EXPECTED_TREND_CUMULATIVE
    assert "not the last cron tick" in payload["activity_note"]
    assert session.parameters[0] == {"throughput_window_hours": ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS}


@pytest.mark.asyncio
async def test_snapshot_reports_a_failed_ledger_read_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(_FailingSession()))

    response = await ops_route.backfill_snapshot_json(_request())
    payload = json.loads(response.body)

    assert response.status == _HTTP_OK
    assert payload["lanes"] == []
    assert "PermissionError" in payload["error"]


@pytest.mark.asyncio
async def test_backfill_page_serves_html_with_the_stream_wired_and_error_text_escaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(lanes=[_lane()], failures=[_failure()], trend=[_trend()])
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    response = await ops_route.backfill_dashboard(_request(interval="99"))
    body = response.body.decode()

    assert response.status == _HTTP_OK
    assert "text/html" in response.content_type
    assert '<div id="ops-stream"' in body
    # 99 clamps to the 30-second ceiling, and the stream URL carries the clamped value.
    assert "/ops/backfill/stream?interval=30" in body
    assert ops_route._DATASTAR_URL in body
    assert all(region in body for region in ('id="ops-meta"', 'id="ops-lanes"', 'id="ops-failures"'))
    assert "firms-archive" in body
    # Ledger error text is escaped, so a hostile error summary cannot inject markup.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>" not in body.split("</head>")[1]


def test_stream_frames_each_region_as_a_datastar_element_patch() -> None:
    event = ops_route._patch_elements_event("#ops-lanes", '<section id="ops-lanes">x</section>')

    assert event.startswith("event: datastar-patch-elements\n")
    assert "data: selector #ops-lanes\n" in event
    assert "data: mode outer\n" in event
    assert 'data: elements <section id="ops-lanes">x</section>' in event
    assert event.endswith("\n\n")


def test_datastar_bundle_is_vendored_next_to_the_service() -> None:
    assert ops_route._DATASTAR_PATH.is_file()
    assert "Datastar" in ops_route._DATASTAR_PATH.read_text(encoding="utf-8")[:64]
