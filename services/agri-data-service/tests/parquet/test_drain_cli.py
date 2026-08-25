"""The operator surface of the drain: `--selection`, the two dry runs, and the legacy sweep verb.

THIS FILE EXISTS BECAUSE A CORRECT REPAIR NOBODY CAN START IS NOT A REPAIR. Before it, the ladder
selection, the ladder census and the pre-zoom sweep had no caller anywhere outside `drain.py`:
`parquet-drain` declared no `--selection`, `_parquet_drain` passed none, and `--dry-run` printed the
BASE census whatever was asked. An operator reading "1,040 lane-days it could not see" would run
`parquet-drain --dry-run`, be shown no ladder work, run `parquet-drain`, and leave ~1,037 days
permanently empty below z13 -- on a green tick and exit code 0.

The assertions below are therefore about REACHABILITY, not about what a census contains
(`test_drain.py` owns that): the ladder dry run prints a LADDER report, a plain run threads
`selection="ladder"` into `run_drain`, and the sweep is a verb of its own that deletes nothing
unless asked twice.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING, Any, Final

import click
from click.testing import CliRunner

from agri_data_service.cli import cli
from agri_data_service.config import Settings
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.parquet.drain import DrainLaneProgress, DrainSummary
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]
    import pytest

STREAM: Final = "fire-detections"
DAY: Final = dt.date(2026, 8, 1)
RUN_ID: Final = "test-drain-cli"
FROZEN_NOW: Final = dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)


def _base_table(day: date) -> pa.Table:
    import pyarrow as pa  # noqa: PLC0415 - a type-only import at module scope, materialised here

    return pa.Table.from_pylist(
        [
            {
                "cell_longitude": -116.0,
                "cell_latitude": 43.0,
                "observed_day": day,
                "detection_count": 1,
                "frp_sum": 1.0,
                "frp_observation_count": 1,
                "high_confidence_detection_count": 1,
                "newest_observed_at": FROZEN_NOW,
            }
        ],
        schema=observed_stream_schema(STREAM).arrow_schema,
    )


def _store_with_one_base_complete_day() -> tuple[ObjectStore, RecordingBackend]:
    """One published day with NO coarse rungs: exactly the shape the ladder selection exists for."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(_base_table(DAY), layer=STREAM, kind="observed", zoom=BASE_ZOOM_TIER, day=DAY)
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=1, completed_at=FROZEN_NOW, run_id=RUN_ID),
        layer=STREAM,
        kind="observed",
        zoom=BASE_ZOOM_TIER,
        day=DAY,
    )
    return store, backend


def _pin_store(monkeypatch: pytest.MonkeyPatch, store: ObjectStore) -> None:
    def _stub_from_settings(_cls: type[ObjectStore], _source: object = None) -> ObjectStore:
        return store

    monkeypatch.setattr(ObjectStore, "from_settings", classmethod(_stub_from_settings))


def test_a_ladder_dry_run_reports_the_ladder_census_not_the_base_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """DO NOT DELETE. The base census CANNOT see this day: it is base-complete, so it has no gap.

    Auditing a ladder repair through the missing census is how ~1,037 days would be reported as
    "nothing to do" while every zoom under 13 stayed empty for them forever.
    """
    store, backend = _store_with_one_base_complete_day()
    published = dict(backend.objects)
    _pin_store(monkeypatch, store)

    ladder = CliRunner().invoke(cli, ["parquet-drain", "--layer", STREAM, "--selection", "ladder", "--dry-run"])
    missing = CliRunner().invoke(cli, ["parquet-drain", "--layer", STREAM, "--dry-run"])

    assert ladder.exit_code == 0, ladder.output
    ladder_report = json.loads(ladder.output)
    assert ladder_report["incomplete_ladder_days"] == 1
    assert ladder_report["lanes_with_incomplete_ladders"] == [STREAM]

    assert missing.exit_code == 0, missing.output
    missing_report = json.loads(missing.output)
    assert "incomplete_ladder_days" not in missing_report, "the default dry run is still the BASE census"
    assert DAY.isoformat() not in json.dumps(missing_report), "the base census cannot see a base-complete day"

    assert backend.objects == published, "--dry-run must not write a single object"


def test_the_ladder_selection_reaches_run_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole of FINDING 1: `_parquet_drain` passed no `selection`, so this path was unreachable."""
    store, _ = _store_with_one_base_complete_day()
    _pin_store(monkeypatch, store)
    seen: dict[str, Any] = {}

    async def _record(*_args: object, **kwargs: object) -> DrainSummary:
        seen.update(kwargs)
        return DrainSummary(run_id=RUN_ID, lanes=(DrainLaneProgress(slug=STREAM, considered=0, pending=[]),), seconds=0)

    class _NoSession:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    monkeypatch.setattr("agri_data_service.cli.run_drain", _record)
    monkeypatch.setattr("agri_data_service.cli.local_source_loader_session", lambda _url: _NoSession())
    monkeypatch.setattr(
        Settings,
        "require_local_source_loader_database_url",
        lambda _self: "postgresql+asyncpg://unused/never-opened",
    )

    result = CliRunner().invoke(cli, ["parquet-drain", "--layer", STREAM, "--selection", "ladder", "--no-progress"])

    assert result.exit_code == 0, result.output
    assert seen["selection"] == "ladder", "the drain ran the selection the operator asked for"


def test_the_selection_option_offers_both_walks_and_defaults_to_the_export_drain() -> None:
    """A default that changed would silently re-point every existing runbook invocation."""
    option = next(param for param in cli.commands["parquet-drain"].params if param.name == "selection")

    assert isinstance(option.type, click.Choice)
    assert set(option.type.choices) == {"missing", "ladder"}
    assert option.default == "missing"

    help_text = CliRunner().invoke(cli, ["parquet-drain", "--help"])
    assert help_text.exit_code == 0, help_text.output
    assert "--selection" in help_text.output


def test_an_unknown_selection_is_refused_before_anything_is_listed() -> None:
    result = CliRunner().invoke(cli, ["parquet-drain", "--layer", STREAM, "--selection", "everything", "--dry-run"])

    assert result.exit_code != 0
    assert "everything" in result.output


# --- The legacy sweep verb -----------------------------------------------------------------------


def _legacy_key(store: ObjectStore, day: date) -> str:
    return store.key_for(
        f"layer={STREAM}/kind=observed/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}/part-0.parquet"
    )


def _pin_store_and_backend(monkeypatch: pytest.MonkeyPatch, store: ObjectStore, backend: RecordingBackend) -> None:
    monkeypatch.setattr("agri_data_service.cli._parquet_store_and_backend", lambda: (store, backend))


def test_the_legacy_sweep_is_a_verb_and_deletes_nothing_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting from the record of truth must not be reachable by forgetting an argument."""
    store, backend = _store_with_one_base_complete_day()
    key = _legacy_key(store, DAY)
    backend.put(key, b"pre-zoom bytes", content_type="application/octet-stream")
    _pin_store_and_backend(monkeypatch, store, backend)

    result = CliRunner().invoke(cli, ["parquet-retire-legacy-layout", "--layer", STREAM])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["superseded"] == 1
    assert report["removed"] == 0
    assert key in backend.objects, "a report-only sweep deleted an object"


def test_the_legacy_sweep_removes_superseded_objects_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    store, backend = _store_with_one_base_complete_day()
    key = _legacy_key(store, DAY)
    backend.put(key, b"pre-zoom bytes", content_type="application/octet-stream")
    _pin_store_and_backend(monkeypatch, store, backend)

    result = CliRunner().invoke(cli, ["parquet-retire-legacy-layout", "--layer", STREAM, "--delete"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["removed"] == 1
    assert key not in backend.objects


def test_the_legacy_sweep_keeps_an_orphan_even_with_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """An orphan is the only usable copy of its day; `--delete` alone must not decide to re-export it."""
    store, backend = _store_with_one_base_complete_day()
    orphan = _legacy_key(store, dt.date(2026, 7, 30))
    backend.put(orphan, b"pre-zoom bytes", content_type="application/octet-stream")
    _pin_store_and_backend(monkeypatch, store, backend)

    result = CliRunner().invoke(cli, ["parquet-retire-legacy-layout", "--layer", STREAM, "--delete"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["orphaned"] == 1
    assert report["removed"] == 0
    assert orphan in backend.objects
