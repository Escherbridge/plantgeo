"""Vegetation admin commands fail closed on unsettled or incomplete work."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast

from click.testing import CliRunner

from agri_data_service import cli as cli_module
from agri_data_service.pipeline.parquet.vegetation_absence import VegetationAbsenceLadderReport
from agri_data_service.pipeline.parquet.vegetation_forward import (
    VegetationForwardDayResult,
    VegetationForwardScope,
    VegetationForwardSummary,
    VegetationPublicationDrainSummary,
)

if TYPE_CHECKING:
    import pytest

    from agri_data_service.execution.vegetation_ndvi_plane import RegistrationSummary

DAY = date(2026, 8, 19)
FORWARD_MAX_DAYS = 64


def test_forward_command_uses_the_pinned_change_window(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    scope = VegetationForwardScope(
        cell_keys=("45.1250:-122.6250",),
        cutoff_day=date(2026, 8, 25),
        observed_days=(date(2026, 8, 25),),
        cell_days=(("45.1250:-122.6250", date(2026, 8, 25)),),
    )

    async def complete(**kwargs: object) -> VegetationForwardSummary:
        seen.update(kwargs)
        return VegetationForwardSummary(
            scope=scope,
            registration=cast("RegistrationSummary", object()),
            source_revision=185_231,
            affected_day_count=1,
            examined_day_count=1,
            stop_reason="complete",
            days=(
                VegetationForwardDayResult(
                    day=date(2026, 8, 25),
                    outcome="written",
                    attempt_count=1,
                ),
            ),
        )

    monkeypatch.setattr(cli_module, "_parquet_forward_changed_vegetation", complete)
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "parquet-forward-vegetation",
            "--since",
            "2026-08-26T00:00:00Z",
            "--through-day",
            "2026-08-25",
            "--max-days",
            str(FORWARD_MAX_DAYS),
        ],
    )

    assert result.exit_code == 0
    assert seen["since"] == datetime(2026, 8, 26, tzinfo=UTC)
    assert seen["through_day"] == date(2026, 8, 25)
    assert seen["max_days"] == FORWARD_MAX_DAYS
    assert '"forward_complete": 1' in result.output
    assert '"selected_cell_days": 1' in result.output


def test_catch_up_command_needs_no_since_window_and_fails_closed_on_remaining_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def incomplete(**kwargs: object) -> VegetationPublicationDrainSummary:
        seen.update(kwargs)
        return VegetationPublicationDrainSummary(
            through_day=cast("date", kwargs["through_day"]),
            defensive_day_count=46,
            pending_day_count=45,
            remaining_day_count=20,
            source_revision=185_244,
            stop_reason="day_limit",
            days=(),
        )

    monkeypatch.setattr(cli_module, "_parquet_catch_up_vegetation", incomplete)
    result = CliRunner().invoke(
        cli_module.cli,
        ["parquet-catch-up-vegetation", "--through-day", "2026-08-27", "--max-days", "25"],
    )

    assert result.exit_code == 1
    assert seen["through_day"] == date(2026, 8, 27)
    assert "since" not in seen
    assert '"defensive_days": 46' in result.output
    assert '"remaining_days": 20' in result.output


def test_absence_command_refuses_unsettled_last_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "_vegetation_settled_cutoff", lambda: DAY)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "parquet-vegetation-absence-ladders",
            "--first-day",
            DAY.isoformat(),
            "--last-day",
            date(2026, 8, 20).isoformat(),
        ],
    )

    assert result.exit_code == 1
    assert "is not settled" in result.output


def test_absence_retraction_requires_the_current_settled_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "_vegetation_settled_cutoff", lambda: DAY)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "parquet-retract-vegetation-absences",
            "--day",
            date(2026, 8, 21).isoformat(),
            "--coverage-last-day",
            date(2026, 8, 18).isoformat(),
        ],
    )

    assert result.exit_code == 1
    assert "coverage-last-day must be" in result.output


def test_exact_command_requires_the_full_settled_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "_vegetation_settled_cutoff", lambda: DAY)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "parquet-reconcile-vegetation-exact",
            "--first-day",
            date(2026, 8, 1).isoformat(),
            "--last-day",
            date(2026, 8, 25).isoformat(),
            "--coverage-last-day",
            date(2026, 8, 18).isoformat(),
        ],
    )

    assert result.exit_code == 1
    assert "coverage-last-day must be" in result.output


def test_absence_command_exits_nonzero_when_a_bounded_backlog_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "_vegetation_settled_cutoff", lambda: DAY)

    async def incomplete_pass(**_kwargs: object) -> VegetationAbsenceLadderReport:
        return VegetationAbsenceLadderReport(
            dry_run=False,
            eligible_days=2,
            remaining_days=1,
            completed_days=1,
            contended_days=0,
            already_written_markers=0,
            would_write_markers=0,
            written_markers=3,
            failures=(),
        )

    monkeypatch.setattr(cli_module, "_parquet_vegetation_absence_ladders", incomplete_pass)
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "parquet-vegetation-absence-ladders",
            "--first-day",
            DAY.isoformat(),
            "--last-day",
            DAY.isoformat(),
            "--max-days",
            "1",
            "--apply",
            "--no-progress",
        ],
    )

    assert result.exit_code == 1
    assert '"remaining_days": 1' in result.output
