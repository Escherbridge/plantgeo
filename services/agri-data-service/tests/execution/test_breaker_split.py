"""The breaker split (spec §4.4 "Breaker split", S4, S16; FR-8): native runner exits, and the legacy switch.

A config lane's runner speaks the S4 exits itself, so its output is never read for legacy evidence, and a
runner that exits 0 without its report is a bug; a legacy writer keeps the R1-R4 evidence rules and its
report-less exit 0. `BREAKER_MODE=legacy` is GL-5's operator-only hold, byte for byte. Every flow runs the
real tick and handler with real child processes (`test_lane_catalogue.py::_ConfigWorld` for the config path,
`test_hold_probes.py::ProbeWorld` for the ladder).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.execution.job_executor_service import (
    BREAKER_MODE_VARIABLE,
    ExecutorSettings,
    SoftFailureState,
)
from agri_data_service.execution.lane_ids import DROUGHT_DIRECT_LANE_ID
from tests.execution.soft_failure_fakes import HOUR, NOW, build_lane_spec, exit_script, states
from tests.execution.test_hold_probes import ProbeWorld
from tests.execution.test_lane_catalogue import (
    CONFIG_GAP_FILL,
    CONFIG_LANE,
    _config_lane,
    _ConfigWorld,
    _install_lanes,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

#: A child that dies on what looks exactly like an upstream 503, with no report on stdout.
UPSTREAM_LOOKING_CRASH: Final = (
    "import sys\nsys.stderr.write('UpstreamHttpError: upstream request failed with status 503\\n')\nsys.exit(1)\n"
)
#: A child that exits 0 and prints nothing at all.
SILENT_SUCCESS: Final = exit_script(0)


def _both_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str) -> _ConfigWorld:
    """The config lane and a legacy lane, both running `script`."""
    _install_lanes(monkeypatch, tmp_path, _config_lane())
    ledger = _ConfigWorld(
        monkeypatch,
        {DROUGHT_DIRECT_LANE_ID: build_lane_spec(DROUGHT_DIRECT_LANE_ID, script)},
        active=frozenset({DROUGHT_DIRECT_LANE_ID}),
        runner_script=script,
    )
    ledger.ran_before(CONFIG_LANE)
    ledger.ran_before(CONFIG_GAP_FILL)
    return ledger


async def test_a_config_lane_s_exit_is_native_and_a_legacy_lane_s_is_read_for_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ledger = _both_paths(monkeypatch, tmp_path, UPSTREAM_LOOKING_CRASH)

    await ledger.world.tick(soft=None)

    config_class = ledger.world.last_outcome(CONFIG_LANE).metrics["exit_class"]
    legacy_class = ledger.world.last_outcome(DROUGHT_DIRECT_LANE_ID).metrics["exit_class"]
    assert config_class == "code", "the runner exits 75 for upstream; its exit 1 is a bug, whatever stderr says"
    assert legacy_class == "upstream", "a legacy writer's exit 1 is still read for R1 evidence"


async def test_a_config_turn_that_exits_zero_without_its_report_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ledger = _both_paths(monkeypatch, tmp_path, SILENT_SUCCESS)

    summary = await ledger.world.tick(soft=None)

    config = ledger.world.last_outcome(CONFIG_LANE)
    legacy = ledger.world.last_outcome(DROUGHT_DIRECT_LANE_ID)
    assert (config.kind, config.failure_class, config.metrics["exit_class"]) == (
        "failed",
        "report_missing",
        "report_missing",
    ), "the runner prints its report from `finally`, so a missing one is a runner bug on the code ladder"
    assert (legacy.kind, legacy.metrics["exit_class"]) == ("completed", "report_missing"), "legacy: unchanged"
    assert states(summary)[CONFIG_LANE] == "failed"


@pytest.mark.parametrize(
    ("environment", "probes"),
    [
        pytest.param({}, 1, id="split-probes-the-upstream-hold-after-an-hour"),
        pytest.param({BREAKER_MODE_VARIABLE: "legacy"}, 0, id="legacy-is-operator-only"),
    ],
)
async def test_legacy_breaker_mode_restores_operator_only_holds(
    monkeypatch: pytest.MonkeyPatch, environment: Mapping[str, str], probes: int
) -> None:
    lane = "held-upstream-lane"
    world = ProbeWorld({lane: build_lane_spec(lane, exit_script(75))}).install(monkeypatch)
    world.seed_hold(lane, exit_class="upstream")
    state = SoftFailureState.for_process(world.activation, ExecutorSettings.from_environment(environment))

    await world.tick(now=NOW, soft=state)
    await world.tick(now=NOW + HOUR, soft=state)
    later = await world.tick(now=NOW + HOUR + HOUR // 2, soft=state)

    assert len(world.probe_runs(lane)) == probes
    assert states(later)[lane] == "failed", "either way the lane is held between probes"
    hold = world.incidents.by_fingerprint(f"lane_hold:{lane}")
    assert hold is not None, "legacy still records the hold (GL-5); it only never probes it"
