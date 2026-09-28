"""The command (`python -m agri_data_service.pipeline.runner`): S4 exits, the S5 report as the last stdout line, always.

`main` runs over a built `lanes/` tree (real provider files, the real region manifest) and the real
precedence strategy; only the bucket is bound to a fake through `main(bind=...)`.
"""

from __future__ import annotations

import contextlib
import json
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.lane_config.loader import LANES_DIRECTORY_ENV_VAR
from agri_data_service.pipeline.runner.__main__ import main
from agri_data_service.pipeline.runner.exits import (
    EXIT_COMPLETED,
    EXIT_CONFIGURATION_ERROR,
    EXIT_INTERNAL_ERROR,
)
from tests.lane_config.builders import settled_soil_lane, transform_lane, write_lane_tree
from tests.runner.fakes import TODAY, ManualClock, MemoryLaneStore, grid_table, ports_for
from tests.runner.fixtures.grid_refuse import CELLS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from agri_data_service.pipeline.runner.clock import TurnClock
    from agri_data_service.pipeline.runner.contract import IngestStrategy, TransformStrategy
    from agri_data_service.pipeline.runner.turn import TurnPorts, TurnSpec

TRANSFORM = "fixture-precedence"
SETTLED, PROVISIONAL, DERIVED = "fixture-era5-value", "fixture-ifs-value", "fixture-value"


def _streams(slug: str) -> list[dict[str, object]]:
    return [{"slug": slug, "floor_basis": "command fixture"}]


@pytest.fixture
def lanes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A lanes tree with a precedence transform over a settled and a provisional input, and a broken strategy key."""
    settled = settled_soil_lane("fixture-era5-settled", strategy="fixtures.grid_refuse", streams=_streams(SETTLED))
    provisional = settled_soil_lane(
        "fixture-ifs-provisional",
        strategy="fixtures.grid_refuse",
        streams=_streams(PROVISIONAL),
        days={"publication_lag_days": 0, "absence_recheck_days": 3, "partial_day": "write_and_recheck"},
    )
    transform = transform_lane(
        TRANSFORM,
        inputs=["fixture-era5-settled", "fixture-ifs-provisional"],
        executor="config",
        streams=_streams(DERIVED),
        days={"publication_lag_days": 1, "absence_recheck_days": 5},
    )
    unresolvable = transform_lane(
        "fixture-unresolvable",
        inputs=["fixture-era5-settled"],
        executor="config",
        strategy="transforms.no_such_module",
        streams=_streams("fixture-nothing"),
        days={"publication_lag_days": 1, "absence_recheck_days": 5},
    )
    directory = write_lane_tree(tmp_path, [settled, provisional, transform, unresolvable])
    monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(directory))
    monkeypatch.delenv("PLANTGEO_REGION", raising=False)
    monkeypatch.setenv("PLANTGEO_TURN_ID", "turn-command-test")
    return directory


def _memory_binder(store: MemoryLaneStore):  # noqa: ANN202 - a PortBinder
    @contextlib.asynccontextmanager
    async def bind(
        spec: TurnSpec, strategy: IngestStrategy | TransformStrategy, *, clock: TurnClock
    ) -> AsyncIterator[TurnPorts]:
        del spec, clock
        yield ports_for(strategy, store, clock=ManualClock())

    return bind


def _raising_binder():  # noqa: ANN202 - a PortBinder
    @contextlib.asynccontextmanager
    async def bind(
        spec: TurnSpec, strategy: IngestStrategy | TransformStrategy, *, clock: TurnClock
    ) -> AsyncIterator[TurnPorts]:
        del spec, strategy, clock
        raise RuntimeError("object store unreachable")
        yield  # pragma: no cover - never reached

    return bind


def _last_stdout_json(captured: str) -> dict[str, object]:
    lines = [line for line in captured.splitlines() if line.strip()]
    return json.loads(lines[-1])


def _seeded_store() -> MemoryLaneStore:
    store = MemoryLaneStore()
    for offset in range(1, 6):
        day = TODAY - timedelta(days=offset)
        store.publish(PROVISIONAL, day, grid_table(day, dict.fromkeys(CELLS, 2.0)))
    return store


@pytest.mark.usefixtures("lanes")
def test_a_completed_turn_prints_the_report_last_on_stdout_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    """The executor's parser takes the last stdout line: the S5 report, with no `level`, naming the turn."""
    store = _seeded_store()

    exit_code = main(["--lane", TRANSFORM, "--mode", "transform"], bind=_memory_binder(store))

    captured = capsys.readouterr()
    report = _last_stdout_json(captured.out)
    assert exit_code == EXIT_COMPLETED
    assert report["event"] == "plantgeo_lane_turn_report"
    assert "level" not in report
    assert (report["lane"], report["mode"], report["exit_code"]) == (TRANSFORM, "transform", EXIT_COMPLETED)
    assert report["turn_id"] == "turn-command-test"
    assert report["unwritten_known"] is True
    assert report["strategy"] == "transforms.precedence"
    assert report["days_written"] == len(range(1, 6))
    log_lines = [json.loads(line) for line in captured.out.splitlines()[:-1] if line.startswith("{")]
    assert log_lines
    assert all(line.get("level") in {"debug", "info", "warn"} for line in log_lines)


@pytest.mark.usefixtures("lanes")
@pytest.mark.parametrize(
    "argv",
    [
        ["--lane", "no-such-lane", "--mode", "transform"],
        ["--lane", TRANSFORM],
        ["--lane", TRANSFORM, "--mode", "sideways"],
        ["--lane", TRANSFORM, "--mode", "forward"],
        ["--lane", "fixture-unresolvable", "--mode", "transform"],
    ],
    ids=["unknown-lane", "missing-mode", "unknown-mode", "mode-its-kind-forbids", "unresolvable-strategy"],
)
def test_a_configuration_fault_exits_78_with_a_report_and_an_error_line(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """S4 78: nothing is written, the report still prints (its `unwritten` list unknown), and stderr says why."""
    store = _seeded_store()

    exit_code = main(argv, bind=_memory_binder(store))

    captured = capsys.readouterr()
    report = _last_stdout_json(captured.out)
    assert exit_code == EXIT_CONFIGURATION_ERROR
    assert report["exit_code"] == EXIT_CONFIGURATION_ERROR
    assert report["outcome"] == "config_error"
    assert report["unwritten_known"] is False
    assert store.writes() == []
    assert any(json.loads(line).get("level") == "error" for line in captured.err.splitlines() if line.startswith("{"))


@pytest.mark.usefixtures("lanes")
def test_an_internal_fault_exits_70_and_the_report_still_prints_from_finally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S4 70: a binding that raises is a code-class failure; the report line is written anyway."""
    exit_code = main(["--lane", TRANSFORM, "--mode", "transform"], bind=_raising_binder())

    report = _last_stdout_json(capsys.readouterr().out)
    assert exit_code == EXIT_INTERNAL_ERROR
    assert report["error_type"] == "RuntimeError"
    assert report["unwritten_known"] is False
    assert "level" not in report
