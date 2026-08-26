"""Vegetation admin commands fail closed on unsettled or incomplete work."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from click.testing import CliRunner

from agri_data_service import cli as cli_module
from agri_data_service.pipeline.parquet.vegetation_absence import VegetationAbsenceLadderReport

if TYPE_CHECKING:
    import pytest

DAY = date(2026, 8, 19)


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
