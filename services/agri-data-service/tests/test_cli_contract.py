"""CLI contracts keep the frozen run plan explicit and file-backed."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import agri_data_service.cli as cli_module
from agri_data_service.cli import _load_run_plan, cli

FORECAST_HORIZON_DAYS = 30


def _write_plan(path: Path, **extra: object) -> None:
    plan = {
        "partitions": ["colorado-west"],
        "expected_shards": ["colorado-west"],
        "expected_outputs": [
            {
                "output_key": "danger-forecast-colorado-west",
                "kind": "danger_forecast",
                "covered_shards": ["colorado-west"],
                "covered_partitions": ["colorado-west"],
            }
        ],
        **extra,
    }
    path.write_text(json.dumps(plan), encoding="utf-8")


def test_run_plan_parser_is_strict_and_typed(tmp_path: Path) -> None:
    path = tmp_path / "run-plan.json"
    _write_plan(path)

    partitions, shards, outputs = _load_run_plan(path)

    assert partitions == ["colorado-west"]
    assert shards == ["colorado-west"]
    assert outputs[0].output_key == "danger-forecast-colorado-west"

    _write_plan(path, credential="must-not-be-accepted")
    with pytest.raises(ValueError, match="must contain only"):
        _load_run_plan(path)

    path.write_text(
        json.dumps(
            {
                "partitions": ["z", "a"],
                "expected_shards": ["z", "a"],
                "expected_outputs": [
                    {
                        "output_key": "combined",
                        "kind": "danger_forecast",
                        "covered_shards": ["a", "z"],
                        "covered_partitions": ["a", "z"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sorted, unique"):
        _load_run_plan(path)


def test_local_cli_exposes_plan_finalize_and_server_owned_actor() -> None:
    runner = CliRunner()

    init_help = runner.invoke(cli, ["local", "init", "--help"])
    assert init_help.exit_code == 0
    assert "--run-plan" in init_help.output
    assert "--release-set-manifest-checksum" in init_help.output
    assert "--partition" not in init_help.output

    finalize_help = runner.invoke(cli, ["local", "finalize", "--help"])
    assert finalize_help.exit_code == 0
    assert "--run-validation-report" in finalize_help.output

    publish_help = runner.invoke(cli, ["local", "publish", "--help"])
    assert publish_help.exit_code == 0
    assert "--published-by" not in publish_help.output


def test_cli_exposes_explicit_unscheduled_forecast_commands() -> None:
    runner = CliRunner()

    help_result = runner.invoke(cli, ["forecast-refresh-ml-daily", "--help"])

    assert help_result.exit_code == 0
    assert "refresh" in help_result.output.lower()
    assert "schedule" not in help_result.output.lower()

    iteration_help = runner.invoke(cli, ["forecast-run-iteration", "--help"])
    assert iteration_help.exit_code == 0
    assert "--horizon-days" in iteration_help.output
    assert "--simulation-count" in iteration_help.output
    assert "evaluation-only" in iteration_help.output
    assert "publish" not in iteration_help.output.lower()

    reconcile_help = runner.invoke(cli, ["forecast-reconcile-actuals", "--help"])
    assert reconcile_help.exit_code == 0
    assert "--iteration-id" in reconcile_help.output
    assert "--actual-release-set-id" in reconcile_help.output
    assert "--as-of-time" in reconcile_help.output


def test_cli_exposes_local_evaluation_only_strategy_training() -> None:
    result = CliRunner().invoke(cli, ["strategy-train", "--help"])

    assert result.exit_code == 0
    assert "--label-bundle" in result.output
    assert "--output-artifact" in result.output
    assert "evaluation-only" in result.output
    assert "publish" not in result.output.lower()


def test_strategy_train_writes_canonical_artifact_and_reports_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    label_bundle = tmp_path / "labels.json"
    label_bundle.write_text("{}", encoding="utf-8")
    output_artifact = tmp_path / "nested" / "strategy-model.json"
    bundle = object()

    class Artifact:
        checksum = "a" * 64
        decision_state = "ranked"
        label_bundle_checksum = "b" * 64
        label_checksum = "c" * 64
        selected_strategy_id = "cover-crop"

        @staticmethod
        def to_json() -> str:
            return '{"decision_state":"ranked","schema_version":"strategy_training_artifact_v1"}'

    def load(path: Path) -> object:
        assert path == label_bundle
        return bundle

    def train(value: object) -> Artifact:
        assert value is bundle
        return Artifact()

    monkeypatch.setattr(cli_module, "load_strategy_label_bundle", load)
    monkeypatch.setattr(cli_module, "train_strategy_models", train)

    result = CliRunner().invoke(
        cli,
        [
            "strategy-train",
            "--label-bundle",
            str(label_bundle),
            "--output-artifact",
            str(output_artifact),
        ],
    )

    assert result.exit_code == 0
    assert output_artifact.read_text(encoding="utf-8") == Artifact.to_json()
    assert not list(output_artifact.parent.glob("*.tmp"))
    assert json.loads(result.output) == {
        "artifact_checksum": "a" * 64,
        "decision_state": "ranked",
        "label_bundle_checksum": "b" * 64,
        "output_artifact": str(output_artifact),
        "selected_strategy_id": "cover-crop",
        "strategy_label_checksum": "c" * 64,
    }


def test_forecast_mv_refresh_command_reports_materialized_row_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh() -> int:
        return 7

    monkeypatch.setattr(cli_module, "_forecast_refresh_ml_daily", refresh)

    result = CliRunner().invoke(cli, ["forecast-refresh-ml-daily"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"row_count": 7, "state": "refreshed"}


def test_forecast_iteration_command_requires_aware_times_and_reports_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_iteration(**_kwargs: object) -> dict[str, object]:
        return {
            "iteration_id": "dd1bff85-a93d-4f72-935b-399e1d53b452",
            "state": "finalized",
            "value_count": FORECAST_HORIZON_DAYS,
        }

    monkeypatch.setattr(cli_module, "_forecast_run_iteration", run_iteration)
    base_arguments = [
        "forecast-run-iteration",
        "--iteration-key",
        "fixture-v1",
        "--series-id",
        "28ca007c-55c2-4dc7-842a-b7972d0d154b",
        "--release-set-id",
        "3449df84-b8d4-4a68-a668-852acc0246c0",
        "--as-of-time",
        "2026-07-23T00:00:00Z",
        "--cutoff-time",
        "2026-03-31T00:00:00Z",
    ]

    result = CliRunner().invoke(cli, base_arguments)

    assert result.exit_code == 0
    assert json.loads(result.output)["value_count"] == FORECAST_HORIZON_DAYS

    invalid = base_arguments.copy()
    invalid[invalid.index("2026-07-23T00:00:00Z")] = "2026-07-23T00:00:00"
    invalid_result = CliRunner().invoke(cli, invalid)
    assert invalid_result.exit_code != 0
    assert "UTC offset" in invalid_result.output


def test_forecast_actual_reconciliation_command_reports_inserted_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reconcile(**_kwargs: object) -> dict[str, object]:
        return {
            "actual_count": FORECAST_HORIZON_DAYS,
            "forecast_value_count": FORECAST_HORIZON_DAYS,
            "inserted_count": FORECAST_HORIZON_DAYS,
            "iteration_id": "dd1bff85-a93d-4f72-935b-399e1d53b452",
        }

    monkeypatch.setattr(cli_module, "_forecast_reconcile_actuals", reconcile)
    result = CliRunner().invoke(
        cli,
        [
            "forecast-reconcile-actuals",
            "--iteration-id",
            "dd1bff85-a93d-4f72-935b-399e1d53b452",
            "--actual-release-set-id",
            "3449df84-b8d4-4a68-a668-852acc0246c0",
            "--as-of-time",
            "2026-07-23T00:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["inserted_count"] == FORECAST_HORIZON_DAYS
