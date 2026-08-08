"""The reliability contract: per-job isolation, a failed job becoming a failed cron run,
and `runner.run_all_ingestion_jobs`'s own sequential order / bbox plumbing (see the bottom section)."""

# ruff: noqa: PLR2004

from __future__ import annotations

from typing import TYPE_CHECKING

import click
import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from agri_data_service.ingest import commands as commands_module
from agri_data_service.ingest import runner
from agri_data_service.ingest.commands import INGEST_COMMANDS, register_ingest_commands
from agri_data_service.ingest.results import (
    IngestionJobResult,
    any_job_failed,
    failure_reason,
    run_isolated_job,
    skipped_result,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

EXPECTED_VERBS = (
    "ingest-firms",
    "ingest-streamflow",
    "ingest-watersheds",
    "ingest-weather",
    "ingest-fire-perimeters",
    "ingest-drought",
    "ingest-ndvi",
    "ingest-sensors",
    "ingest-evacuation-zones",
    "ingest-mtbs",
    "ingest-backfill",
    "ingest-geometry-repair",
    "ingest-drought-history",
    "ingest-all",
    # The durable archive lanes. They ride the same registration function but not the same exit rule:
    # `emit`/`finish` fail a run when any source failed, which is wrong for a slice that legitimately
    # ends with work remaining. Their own contract is pinned in tests/test_ingest_commands_jobs.py.
    "jobs-plan-lane",
    "jobs-run",
    "jobs-status",
    "jobs-reconcile-lane",
    "validate-streams",
)


def _result(source: str, status: str) -> IngestionJobResult:
    return IngestionJobResult(source=source, status=status, records_seen=0, records_written=0)  # type: ignore[arg-type]


def test_a_skipped_result_carries_no_truncation_or_details() -> None:
    assert skipped_result("nasa-firms", "INGEST_BBOX is not configured").to_summary() == {
        "source": "nasa-firms",
        "status": "skipped",
        "records_seen": 0,
        "records_written": 0,
        "reason": "INGEST_BBOX is not configured",
    }


def test_a_summary_includes_only_the_optional_fields_that_are_set() -> None:
    result = IngestionJobResult(
        source="usgs-streamflow",
        status="ingested",
        records_seen=5,
        records_written=4,
        truncated=False,
        details={"wall_clock_identities": 2},
    )
    assert result.to_summary() == {
        "source": "usgs-streamflow",
        "status": "ingested",
        "records_seen": 5,
        "records_written": 4,
        "truncated": False,
        "details": {"wall_clock_identities": 2},
    }


async def test_a_raised_job_becomes_a_failed_result_rather_than_aborting_the_run() -> None:
    async def explode() -> IngestionJobResult:
        raise RuntimeError("upstream request failed with status 500")

    result = await run_isolated_job("nasa-firms", explode)
    assert result.status == "failed"
    assert result.reason == "upstream request failed with status 500"
    assert result.records_written == 0


def test_a_database_failure_reason_names_the_error_class_not_the_statement() -> None:
    # A SQLAlchemy message would echo the whole statement and its bound payload into the cron log.
    reason = failure_reason(OperationalError("INSERT INTO geo.features ...", {}, Exception("boom")))
    assert reason == "ingest write failed (OperationalError)"


def test_a_reasonless_failure_still_reports_something() -> None:
    assert failure_reason(RuntimeError()) == "unknown ingestion failure"


def test_any_job_failed_is_what_reddens_the_cron_run() -> None:
    assert not any_job_failed([_result("ndvi", "skipped"), _result("nasa-firms", "ingested")])
    assert any_job_failed([_result("ndvi", "skipped"), _result("nasa-firms", "failed")])
    assert not any_job_failed([])


def test_every_ingest_verb_is_registered_on_the_cli_group() -> None:
    group = click.Group("agri-cli")
    register_ingest_commands(group)
    assert sorted(group.commands) == sorted(EXPECTED_VERBS)
    assert len(INGEST_COMMANDS) == len(EXPECTED_VERBS)


def test_ingest_all_exits_non_zero_when_any_job_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    # This is the entire reliability motivation for the lane: the deleted route returned 202 before
    # any job had run, so a thrown job went to console.error and nothing anywhere went red.
    async def failing_run(_bbox: str | None) -> Sequence[IngestionJobResult]:
        return [
            _result("nasa-firms", "failed"),
            _result("usgs-streamflow", "ingested"),
            _result("ndvi", "skipped"),
        ]

    monkeypatch.setattr(commands_module, "_run_all", failing_run)
    group = click.Group("agri-cli")
    register_ingest_commands(group)
    invocation = CliRunner().invoke(group, ["ingest-all"])

    assert invocation.exit_code == 1
    # Every job is still reported: one failure must not erase the other five's progress.
    assert len(invocation.output.strip().splitlines()) == 3


def test_ingest_all_exits_zero_when_every_job_merely_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skipping_run(_bbox: str | None) -> Sequence[IngestionJobResult]:
        return [_result("nasa-firms", "skipped"), _result("ndvi", "skipped")]

    monkeypatch.setattr(commands_module, "_run_all", skipping_run)
    group = click.Group("agri-cli")
    register_ingest_commands(group)
    invocation = CliRunner().invoke(group, ["ingest-all"])
    assert invocation.exit_code == 0


# --- runner.run_all_ingestion_jobs: the orchestration itself, not the CLI layer above it ---
#
# The tests above exercise commands.py by replacing `_run_all` wholesale, which proves the CLI's
# exit-code contract but never calls `runner.run_all_ingestion_jobs`. These tests replace the eight
# job callables instead, so the sequential order, the bbox plumbing and the per-job isolation that
# `run_all_ingestion_jobs` itself is responsible for are pinned directly.

EXPECTED_SOURCE_ORDER = (
    runner.FIRMS_SOURCE,
    runner.USGS_STREAMFLOW_SOURCE,
    runner.OPEN_METEO_SOURCE,
    runner.WFIGS_SOURCE,
    runner.USDM_SOURCE,
    runner.NDVI_SOURCE,
    runner.NWS_SENSOR_SOURCE,
    runner.EVACUATION_ZONES_SOURCE,
    # The geometry repair is the last job of every tick, not a hand-run script. `geo.features`
    # accumulates rows with a NULL geometry_id -- the `/api/ingest/*` push routes never set one --
    # and every such row is excluded from the slider's observation window and from getMetricAtDate.
    # Before it was wired in here it had no CLI verb at all, so the only thing standing between the
    # time axis and years of silent depth loss was an operator remembering to run it by hand.
    runner.GEOMETRY_REPAIR_SOURCE,
)

EXPECTED_JOB_ORDER = [
    "firms",
    "usgs",
    "weather",
    "wfigs",
    "usdm",
    "ndvi",
    "sensors",
    "evacuation-zones",
    "geometry-repair",
]

_TEST_BBOX = "-125,42,-111,49"

_SENTINEL_SESSION = object()
_SENTINEL_PUBLISHER = object()
_SENTINEL_WRITE_FEATURES = object()


class _FakeDroughtStore:
    """Stands in for PostgresDroughtStore so the test never touches SQLAlchemy or a real session."""

    def __init__(self, session: object) -> None:
        self.session = session


@pytest.fixture
def job_call_log(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    """Replace bind_feature_writer, PostgresDroughtStore and all eight jobs with recording fakes.

    `log["bind"]` records the (session, publisher) pair bind_feature_writer was given; `log["order"]`
    records each job's name and the argument it was called with, in call order.
    """
    log: dict[str, list[object]] = {"bind": [], "order": []}

    def fake_bind_feature_writer(session: object, publisher: object | None = None) -> object:
        log["bind"].append((session, publisher))
        return _SENTINEL_WRITE_FEATURES

    async def fake_firms(write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        assert write_features is _SENTINEL_WRITE_FEATURES
        log["order"].append(("firms", bbox))
        return _result(runner.FIRMS_SOURCE, "ingested")

    async def fake_usgs(write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        assert write_features is _SENTINEL_WRITE_FEATURES
        log["order"].append(("usgs", bbox))
        return _result(runner.USGS_STREAMFLOW_SOURCE, "ingested")

    async def fake_weather(write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        assert write_features is _SENTINEL_WRITE_FEATURES
        log["order"].append(("weather", bbox))
        return _result(runner.OPEN_METEO_SOURCE, "ingested")

    async def fake_wfigs(write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        assert write_features is _SENTINEL_WRITE_FEATURES
        log["order"].append(("wfigs", bbox))
        return _result(runner.WFIGS_SOURCE, "ingested")

    async def fake_usdm(store: _FakeDroughtStore) -> IngestionJobResult:
        # Deliberately no bbox parameter: USDM is not bbox-scoped (T6). If the runner ever tried to
        # thread bbox through here it would raise TypeError instead of silently passing it along.
        assert isinstance(store, _FakeDroughtStore)
        log["order"].append(("usdm", store.session))
        return _result(runner.USDM_SOURCE, "ingested")

    async def fake_ndvi(write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        assert write_features is _SENTINEL_WRITE_FEATURES
        log["order"].append(("ndvi", bbox))
        return _result(runner.NDVI_SOURCE, "ingested")

    async def fake_sensors(write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        assert write_features is _SENTINEL_WRITE_FEATURES
        log["order"].append(("sensors", bbox))
        return _result(runner.NWS_SENSOR_SOURCE, "ingested")

    async def fake_evacuation_zones(write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        assert write_features is _SENTINEL_WRITE_FEATURES
        log["order"].append(("evacuation-zones", bbox))
        return _result(runner.EVACUATION_ZONES_SOURCE, "ingested")

    async def fake_geometry_repair(session: object) -> IngestionJobResult:
        # Takes the session, not the writer: the repair rewrites existing rows through the geometry
        # dimension rather than writing new features.
        log["order"].append(("geometry-repair", session))
        return _result(runner.GEOMETRY_REPAIR_SOURCE, "ingested")

    monkeypatch.setattr(runner, "bind_feature_writer", fake_bind_feature_writer)
    monkeypatch.setattr(runner, "PostgresDroughtStore", _FakeDroughtStore)
    monkeypatch.setattr(runner, "run_fire_ingestion_job", fake_firms)
    monkeypatch.setattr(runner, "run_water_ingestion_job", fake_usgs)
    monkeypatch.setattr(runner, "run_weather_ingestion_job", fake_weather)
    monkeypatch.setattr(runner, "run_fire_perimeters_ingestion_job", fake_wfigs)
    monkeypatch.setattr(runner, "run_drought_ingestion_job", fake_usdm)
    monkeypatch.setattr(runner, "run_vegetation_ingestion_job", fake_ndvi)
    monkeypatch.setattr(runner, "run_sensor_ingestion_job", fake_sensors)
    monkeypatch.setattr(runner, "run_evacuation_zones_ingestion_job", fake_evacuation_zones)
    monkeypatch.setattr(runner, "run_geometry_repair", fake_geometry_repair)
    return log


async def test_runner_runs_the_eight_sources_then_the_geometry_repair_in_a_fixed_order(
    job_call_log: dict[str, list[object]],
) -> None:
    results = await runner.run_all_ingestion_jobs(_SENTINEL_SESSION, _SENTINEL_PUBLISHER, bbox=_TEST_BBOX)
    assert [result.source for result in results] == list(EXPECTED_SOURCE_ORDER)
    assert [name for name, _ in job_call_log["order"]] == EXPECTED_JOB_ORDER


async def test_runner_builds_exactly_one_feature_writer_shared_by_the_bbox_scoped_jobs(
    job_call_log: dict[str, list[object]],
) -> None:
    await runner.run_all_ingestion_jobs(_SENTINEL_SESSION, _SENTINEL_PUBLISHER, bbox=_TEST_BBOX)
    # bind_feature_writer is called exactly once per run, not once per job: the same writer closure
    # backs every bbox-scoped job, matching ingest.ts's single shared write path.
    assert job_call_log["bind"] == [(_SENTINEL_SESSION, _SENTINEL_PUBLISHER)]


async def test_runner_threads_the_bbox_into_every_bbox_scoped_job(
    job_call_log: dict[str, list[object]],
) -> None:
    await runner.run_all_ingestion_jobs(_SENTINEL_SESSION, _SENTINEL_PUBLISHER, bbox=_TEST_BBOX)
    bbox_by_name = dict(job_call_log["order"])
    # Every feature-writing source is bbox-scoped; only USDM (a national release) is not.
    for name in ("firms", "usgs", "weather", "wfigs", "ndvi", "sensors", "evacuation-zones"):
        assert bbox_by_name[name] == _TEST_BBOX


async def test_runner_binds_the_drought_store_to_the_same_session_it_was_given(
    job_call_log: dict[str, list[object]],
) -> None:
    await runner.run_all_ingestion_jobs(_SENTINEL_SESSION, _SENTINEL_PUBLISHER)
    bbox_by_name = dict(job_call_log["order"])
    assert bbox_by_name["usdm"] is _SENTINEL_SESSION
    # The repair reaches the warehouse through the very same session, so one `ingest-all` run is
    # one connection for the sources and their clean-up alike.
    assert bbox_by_name["geometry-repair"] is _SENTINEL_SESSION


async def test_runner_publisher_defaults_to_none_when_the_caller_omits_it(
    job_call_log: dict[str, list[object]],
) -> None:
    await runner.run_all_ingestion_jobs(_SENTINEL_SESSION)
    assert job_call_log["bind"] == [(_SENTINEL_SESSION, None)]


async def test_runner_a_single_job_failure_does_not_erase_the_other_results(
    monkeypatch: pytest.MonkeyPatch,
    job_call_log: dict[str, list[object]],
) -> None:
    """Per-job isolation is the entire reliability motivation for this lane: one exception, seven survivors."""

    async def fake_weather_that_explodes(_write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        job_call_log["order"].append(("weather", bbox))
        raise RuntimeError("open-meteo upstream exploded")

    monkeypatch.setattr(runner, "run_weather_ingestion_job", fake_weather_that_explodes)

    results = await runner.run_all_ingestion_jobs(_SENTINEL_SESSION, _SENTINEL_PUBLISHER, bbox=_TEST_BBOX)

    status_by_source = {result.source: result.status for result in results}
    assert status_by_source[runner.OPEN_METEO_SOURCE] == "failed"
    assert status_by_source[runner.FIRMS_SOURCE] == "ingested"
    assert status_by_source[runner.USGS_STREAMFLOW_SOURCE] == "ingested"
    assert status_by_source[runner.WFIGS_SOURCE] == "ingested"
    assert status_by_source[runner.USDM_SOURCE] == "ingested"
    assert status_by_source[runner.NDVI_SOURCE] == "ingested"
    assert status_by_source[runner.NWS_SENSOR_SOURCE] == "ingested"
    assert status_by_source[runner.EVACUATION_ZONES_SOURCE] == "ingested"
    # Every source still ran, in order, despite the failure landing in the middle of the sequence.
    assert [name for name, _ in job_call_log["order"]] == EXPECTED_JOB_ORDER
    assert [result.source for result in results] == list(EXPECTED_SOURCE_ORDER)


async def test_runner_a_failure_in_the_first_job_still_lets_the_remaining_jobs_run(
    monkeypatch: pytest.MonkeyPatch,
    job_call_log: dict[str, list[object]],
) -> None:
    async def fake_firms_that_explodes(_write_features: object, *, bbox: str | None = None) -> IngestionJobResult:
        job_call_log["order"].append(("firms", bbox))
        raise RuntimeError("FIRMS upstream exploded")

    monkeypatch.setattr(runner, "run_fire_ingestion_job", fake_firms_that_explodes)

    results = await runner.run_all_ingestion_jobs(_SENTINEL_SESSION, _SENTINEL_PUBLISHER, bbox=_TEST_BBOX)

    status_by_source = {result.source: result.status for result in results}
    assert status_by_source[runner.FIRMS_SOURCE] == "failed"
    assert all(
        status_by_source[source] == "ingested" for source in EXPECTED_SOURCE_ORDER if source != runner.FIRMS_SOURCE
    )
    assert [name for name, _ in job_call_log["order"]] == EXPECTED_JOB_ORDER
