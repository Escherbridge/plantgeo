"""Every `ingest-*` CLI verb: option plumbing, session lifecycle, and each verb's own exit-code contract.

`test_ingest_runner.py` already covers `ingest-all`'s exit-code contract and the
`run_all_ingestion_jobs` orchestration it delegates to. This file covers the other verbs' own
option wiring (`--bbox`, `--valid-date`, `--replace`, `--since`/`--until`) and confirms every verb
opens exactly one `ingest_session()` and never touches a real database or Redis connection to do it.

The `ingest_session` fixture below is a monkeypatch, so nothing in this file exercises the DSN the
real one resolves. `test_config.py` covers that separately, through the real
`db.engine.ingest_session`: it is the seam that made every scheduled run abort with
`source-ingest requires LOCAL_SOURCE_LOADER_DATABASE_URL` while both operator docs said the
container needed only `DATABASE_URL`.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import click
import pytest
from click.testing import CliRunner

from agri_data_service.ingest import commands as commands_module
from agri_data_service.ingest.commands import register_ingest_commands
from agri_data_service.ingest.results import IngestionJobResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SENTINEL_SESSION = object()


@asynccontextmanager
async def _fake_ingest_session() -> AsyncIterator[object]:
    """Stand in for the real DB session so a CLI-level test never opens a connection."""
    yield _SENTINEL_SESSION


@pytest.fixture(autouse=True)
def _patch_ingest_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # RealtimePublisher() is left real: its constructor only parses REDIS_URL and its __aenter__
    # opens no socket, so it is already offline-safe without mocking (ingest/AGENTS.md "realtime.py").
    monkeypatch.setattr(commands_module, "ingest_session", _fake_ingest_session)


def _group() -> click.Group:
    group = click.Group("agri-service")
    register_ingest_commands(group)
    return group


def _ingested(source: str, seen: int = 3, written: int = 2) -> IngestionJobResult:
    return IngestionJobResult(source=source, status="ingested", records_seen=seen, records_written=written)


def _failed(source: str, reason: str) -> IngestionJobResult:
    return IngestionJobResult(source=source, status="failed", records_seen=0, records_written=0, reason=reason)


# --- Every feature-writing verb shares one shape: a single bbox-scoped job run through the shared
#     feature writer. Table-driven over all seven of them. Only ingest-drought differs (it is a
#     national release, not a bbox query) and only ingest-all runs more than one source. ---

_BBOX_SCOPED_VERBS = [
    ("ingest-firms", "run_fire_ingestion_job", "FIRMS_SOURCE"),
    ("ingest-streamflow", "run_water_ingestion_job", "USGS_STREAMFLOW_SOURCE"),
    ("ingest-weather", "run_weather_ingestion_job", "OPEN_METEO_SOURCE"),
    ("ingest-fire-perimeters", "run_fire_perimeters_ingestion_job", "WFIGS_SOURCE"),
    ("ingest-ndvi", "run_vegetation_ingestion_job", "NDVI_SOURCE"),
    ("ingest-sensors", "run_sensor_ingestion_job", "NWS_SENSOR_SOURCE"),
    ("ingest-evacuation-zones", "run_evacuation_zones_ingestion_job", "EVACUATION_ZONES_SOURCE"),
]


@pytest.mark.parametrize(("verb", "job_attribute", "source_attribute"), _BBOX_SCOPED_VERBS)
def test_bbox_scoped_verb_passes_the_bbox_option_through_to_its_job(
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    job_attribute: str,
    source_attribute: str,
) -> None:
    source = getattr(commands_module, source_attribute)
    captured: dict[str, object] = {}

    async def fake_job(
        write_features: object,
        *,
        bbox: str | None = None,
        on_persisted: object | None = None,
    ) -> IngestionJobResult:
        captured["bbox"] = bbox
        captured["write_features"] = write_features
        captured["on_persisted"] = on_persisted
        return _ingested(source)

    monkeypatch.setattr(commands_module, job_attribute, fake_job)
    invocation = CliRunner().invoke(_group(), [verb, "--bbox", "-125,42,-111,49"])

    assert invocation.exit_code == 0, invocation.output
    assert captured["bbox"] == "-125,42,-111,49"
    assert captured["write_features"] is not None
    assert (captured["on_persisted"] is not None) is (verb == "ingest-ndvi")
    assert json.loads(invocation.output.strip()) == {
        "source": source,
        "status": "ingested",
        "records_seen": 3,
        "records_written": 2,
    }


@pytest.mark.parametrize(("verb", "job_attribute", "source_attribute"), _BBOX_SCOPED_VERBS)
def test_bbox_scoped_verb_omits_bbox_when_the_option_is_not_given(
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    job_attribute: str,
    source_attribute: str,
) -> None:
    source = getattr(commands_module, source_attribute)
    captured: dict[str, object] = {}

    async def fake_job(
        _write_features: object,
        *,
        bbox: str | None = None,
        on_persisted: object | None = None,  # noqa: ARG001
    ) -> IngestionJobResult:
        captured["bbox"] = bbox
        return _ingested(source)

    monkeypatch.setattr(commands_module, job_attribute, fake_job)
    invocation = CliRunner().invoke(_group(), [verb])

    assert invocation.exit_code == 0, invocation.output
    assert captured["bbox"] is None


@pytest.mark.parametrize(("verb", "job_attribute", "source_attribute"), _BBOX_SCOPED_VERBS)
def test_bbox_scoped_verb_exits_non_zero_when_its_job_reports_failed(
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    job_attribute: str,
    source_attribute: str,
) -> None:
    source = getattr(commands_module, source_attribute)

    # Both parameters keep the real job's names so the keyword call in commands.py still binds.
    async def fake_job(
        _write_features: object,
        *,
        bbox: str | None = None,  # noqa: ARG001
        on_persisted: object | None = None,  # noqa: ARG001
    ) -> IngestionJobResult:
        return _failed(source, "upstream request failed with status 500")

    monkeypatch.setattr(commands_module, job_attribute, fake_job)
    invocation = CliRunner().invoke(_group(), [verb])

    assert invocation.exit_code == 1
    # The summary is printed to stdout before the process exits non-zero (cli.py:169-175's ClickException
    # pattern is not used here on purpose: `finish` must print every result before turning the run red).
    summary = json.loads(invocation.output.strip())
    assert summary["source"] == source
    assert summary["status"] == "failed"


# --- ingest-drought: its own two options, and the store is bound to the session ingest_session yields ---


def test_ingest_drought_passes_valid_date_and_replace_through_to_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_job(store: object, *, valid_date: str | None = None, replace: bool = False) -> IngestionJobResult:
        # PostgresDroughtStore exposes no public accessor; reach in to confirm the session it was bound to.
        captured["session"] = store._session
        captured["valid_date"] = valid_date
        captured["replace"] = replace
        return _ingested(commands_module.USDM_SOURCE)

    monkeypatch.setattr(commands_module, "run_drought_ingestion_job", fake_job)
    invocation = CliRunner().invoke(_group(), ["ingest-drought", "--valid-date", "2026-07-28", "--replace"])

    assert invocation.exit_code == 0, invocation.output
    assert captured["session"] is _SENTINEL_SESSION
    assert captured["valid_date"] == "2026-07-28"
    assert captured["replace"] is True


def test_ingest_drought_defaults_to_no_explicit_date_and_no_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_job(_store: object, *, valid_date: str | None = None, replace: bool = False) -> IngestionJobResult:
        captured["valid_date"] = valid_date
        captured["replace"] = replace
        return _ingested(commands_module.USDM_SOURCE)

    monkeypatch.setattr(commands_module, "run_drought_ingestion_job", fake_job)
    invocation = CliRunner().invoke(_group(), ["ingest-drought"])

    assert invocation.exit_code == 0, invocation.output
    assert captured["valid_date"] is None
    assert captured["replace"] is False


def test_ingest_drought_exits_non_zero_when_the_job_reports_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    # The keyword parameters keep the real job's names so the call in commands.py still binds.
    async def fake_job(
        _store: object,
        *,
        valid_date: str | None = None,  # noqa: ARG001
        replace: bool = False,  # noqa: ARG001
    ) -> IngestionJobResult:
        return _failed(commands_module.USDM_SOURCE, "USDM release fetch failed")

    monkeypatch.setattr(commands_module, "run_drought_ingestion_job", fake_job)
    invocation = CliRunner().invoke(_group(), ["ingest-drought"])

    assert invocation.exit_code == 1


# --- ingest-all: confirm commands.py wires ingest_session's session and a real publisher into
#     runner.run_all_ingestion_jobs, rather than duplicating its own copy of the orchestration ---


def test_ingest_all_hands_ingest_sessions_session_and_the_bbox_to_run_all_ingestion_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_all_ingestion_jobs(
        session: object,
        publisher: object | None = None,
        bbox: str | None = None,
    ) -> list[IngestionJobResult]:
        captured["session"] = session
        captured["publisher"] = publisher
        captured["bbox"] = bbox
        return [_ingested("nasa-firms"), _ingested("ndvi")]

    monkeypatch.setattr(commands_module, "run_all_ingestion_jobs", fake_run_all_ingestion_jobs)
    invocation = CliRunner().invoke(_group(), ["ingest-all", "--bbox", "-125,42,-111,49"])

    assert invocation.exit_code == 0, invocation.output
    assert captured["session"] is _SENTINEL_SESSION
    assert captured["publisher"] is not None
    assert captured["bbox"] == "-125,42,-111,49"
    assert len(invocation.output.strip().splitlines()) == 2


# --- The verbs that were unreachable before 2026-08-04: the geometry repair, the date-ranged
#     backfill driver, and the USDM archive walk all shipped with no CLI entry point at all. ---


def test_every_registered_verb_is_reachable_from_the_group() -> None:
    # A module-level function with no command wrapper is dead code in the shipped image. Assert the
    # registry itself, so adding a runner without a verb fails here rather than in production.
    assert {command.name for command in commands_module.INGEST_COMMANDS} == set(_group().commands)
    for required in ("ingest-geometry-repair", "ingest-backfill", "ingest-drought-history"):
        assert required in _group().commands


def test_ingest_geometry_repair_runs_the_repair_against_the_ingest_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_repair(
        session: object,
        run_clock: object | None = None,  # noqa: ARG001
        batch_size: int = 200,
        max_features: int | None = None,
    ) -> IngestionJobResult:
        captured["session"] = session
        captured["batch_size"] = batch_size
        captured["max_features"] = max_features
        return _ingested(commands_module.GEOMETRY_REPAIR_SOURCE, seen=5, written=5)

    monkeypatch.setattr(commands_module, "run_geometry_repair", fake_repair)
    invocation = CliRunner().invoke(_group(), ["ingest-geometry-repair", "--batch-size", "50", "--max-features", "10"])

    assert invocation.exit_code == 0, invocation.output
    assert captured == {"session": _SENTINEL_SESSION, "batch_size": 50, "max_features": 10}
    assert json.loads(invocation.output.strip())["source"] == commands_module.GEOMETRY_REPAIR_SOURCE


def test_ingest_geometry_repair_exits_non_zero_when_the_repair_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_repair(_session: object, **_options: object) -> IngestionJobResult:
        return _failed(commands_module.GEOMETRY_REPAIR_SOURCE, "repair write failed")

    monkeypatch.setattr(commands_module, "run_geometry_repair", fake_repair)

    assert CliRunner().invoke(_group(), ["ingest-geometry-repair"]).exit_code == 1


def test_ingest_backfill_rejects_an_unknown_source_rather_than_walking_nothing() -> None:
    invocation = CliRunner().invoke(_group(), ["ingest-backfill", "--source", "not-a-source"])

    assert invocation.exit_code != 0
    assert "--source must be one of" in invocation.output


def test_ingest_backfill_builds_the_window_from_since_and_until_in_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_backfill(
        source: object,
        _write_features: object,
        plan: object,
    ) -> list[IngestionJobResult]:
        captured["source_name"] = source.source_name  # type: ignore[attr-defined]
        captured["window"] = plan.window  # type: ignore[attr-defined]
        captured["chunk"] = plan.chunk  # type: ignore[attr-defined]
        captured["bbox"] = plan.bbox  # type: ignore[attr-defined]
        return [_ingested("nws-sensors")]

    monkeypatch.setattr(commands_module, "run_source_backfill", fake_backfill)
    invocation = CliRunner().invoke(
        _group(),
        [
            "ingest-backfill",
            "--source",
            "nws-sensors",
            "--since",
            "2026-07-01",
            "--until",
            "2026-07-08",
            "--chunk-days",
            "2",
            "--bbox",
            "-125,42,-111,49",
        ],
    )

    assert invocation.exit_code == 0, invocation.output
    window = captured["window"]
    assert window.start == datetime(2026, 7, 1, tzinfo=UTC)  # type: ignore[union-attr]
    assert window.end == datetime(2026, 7, 8, tzinfo=UTC)  # type: ignore[union-attr]
    assert captured["chunk"] == timedelta(days=2)
    assert captured["bbox"] == "-125,42,-111,49"
    # The chunk results are emitted, then the fold: an operator resumes --since from the last chunk.
    assert len(invocation.output.strip().splitlines()) == 2


def test_ingest_backfill_refuses_an_inverted_window() -> None:
    invocation = CliRunner().invoke(
        _group(),
        ["ingest-backfill", "--source", "nws-sensors", "--since", "2026-07-08", "--until", "2026-07-01"],
    )

    assert invocation.exit_code != 0
    assert "--since must precede --until" in invocation.output


def test_ingest_drought_history_folds_the_per_week_ledger_into_one_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_walk(_store: object, plan: object, _stored: object) -> list[object]:
        captured["weeks"] = len(plan.weeks)  # type: ignore[attr-defined]
        captured["replace"] = plan.replace  # type: ignore[attr-defined]
        return []

    monkeypatch.setattr(commands_module, "run_usdm_history_backfill", fake_walk)
    invocation = CliRunner().invoke(_group(), ["ingest-drought-history", "--years", "1", "--replace"])

    assert invocation.exit_code == 0, invocation.output
    assert captured["replace"] is True
    weeks = captured["weeks"]
    assert isinstance(weeks, int)
    assert weeks > 0
    # An empty ledger is a skip, never a silent success claim.
    assert json.loads(invocation.output.strip())["status"] == "skipped"
