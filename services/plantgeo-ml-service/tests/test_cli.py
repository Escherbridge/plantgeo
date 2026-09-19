"""`plantgeo-ml predict-daily`: the exit codes a scheduler branches on, and the receipt it prints."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest
from click.testing import CliRunner
from test_forecast_lane_bootstrap import build_harness
from test_monte_carlo_daily import ISSUED_ON, write_observed_fire_history

from plantgeo_ml_service.interface import cli as cli_module
from plantgeo_ml_service.interface.cli import INFRASTRUCTURE_EXIT_CODE, PredictRuntime, cli

if TYPE_CHECKING:
    from pathlib import Path

    from test_forecast_lane_bootstrap import ForecastHarness

BOUNDED_TURN_EXIT_CODE: Final = 0
USAGE_EXIT_CODE: Final = 2


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ForecastHarness:
    """Point the CLI at an in-process bucket, so no verb opens an S3 client."""
    harness = build_harness(tmp_path)
    write_observed_fire_history(harness)
    monkeypatch.setattr(
        cli_module,
        "open_runtime",
        lambda: PredictRuntime(
            store=harness.store,
            pointers=harness.pointers,
            reader=harness.reader,
            session=harness.reader.session,
        ),
    )
    return harness


def test_a_bounded_turn_exits_zero_and_prints_its_receipt(runtime: ForecastHarness) -> None:
    """Owner rule 2026-09-04: a turn where every lane refused is a completed turn, not a failure."""
    assert runtime.objects

    result = CliRunner().invoke(cli, ["predict-daily", "--issued-on", ISSUED_ON.isoformat(), "--lane", "vegetation"])

    assert result.exit_code == BOUNDED_TURN_EXIT_CODE
    document = json.loads(result.stdout)
    assert [lane["status"] for lane in document["lanes"]] == ["refused"]
    assert document["sha256"]


def test_a_dry_run_reports_the_scratch_prefix_it_wrote_under(runtime: ForecastHarness) -> None:
    assert runtime.objects

    result = CliRunner().invoke(
        cli,
        [
            "predict-daily",
            "--issued-on",
            ISSUED_ON.isoformat(),
            "--lane",
            "vegetation",
            "--dry-run-prefix",
            "ml/scratch/2026-09-19/",
        ],
    )

    assert result.exit_code == BOUNDED_TURN_EXIT_CODE
    assert json.loads(result.stdout)["scratch_run"] is True


def test_an_unknown_lane_is_a_usage_error_and_not_a_refused_turn(runtime: ForecastHarness) -> None:
    assert runtime.objects

    result = CliRunner().invoke(cli, ["predict-daily", "--lane", "not-a-lane"])

    assert result.exit_code == USAGE_EXIT_CODE


def test_an_unconfigured_bucket_exits_on_its_own_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one failure a schedule should page on, and the one a bounded turn must not look like."""

    def refuse() -> PredictRuntime:
        raise ValueError("object storage is not configured; set OBJECT_STORE_BUCKET")

    monkeypatch.setattr(cli_module, "open_runtime", refuse)

    result = CliRunner().invoke(cli, ["predict-daily"])

    assert result.exit_code == INFRASTRUCTURE_EXIT_CODE


def test_the_existing_verbs_are_still_mounted() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == BOUNDED_TURN_EXIT_CODE
    for verb in ("predict-daily", "serve", "strategy-train", "strategy-label-map-preflight"):
        assert verb in result.stdout
