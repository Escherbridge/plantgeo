"""The CLI maps shared Parquet-core failures without inventing transport semantics."""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

import agri_data_service.interface.cli.parquet as parquet_cli
from agri_data_service.interface.cli import cli
from agri_data_service.parquet_ops.faults import ServingRefusalError
from tests.parquet_ops.fakes import FakeListing, FakeRowReader

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize("statistic", ["mean", "min", "max"])
def test_temperature_history_day_and_window_use_the_live_warehouse(
    monkeypatch: pytest.MonkeyPatch,
    statistic: str,
) -> None:
    listing, reader = FakeListing(), FakeRowReader()
    layer = f"climate-field-air-temperature-{statistic}"
    day = date(2022, 4, 30)
    part = listing.write_day(layer, "observed", 13, day)
    reader.rows_by_key[part] = ({"normalized_value": 21.4},)

    async def read(
        work: Callable[[FakeListing, FakeRowReader], dict[str, object]],
        _operation: str,
    ) -> dict[str, object]:
        return work(listing, reader)

    monkeypatch.setattr(parquet_cli, "_row_read", read)
    scope = ["--layer", layer, "--zoom", "13"]
    runner = CliRunner()
    single = runner.invoke(cli, ["data", "parquet", "day", *scope, "--day", day.isoformat()])
    window = runner.invoke(
        cli, ["data", "parquet", "window", *scope, "--first-day", day.isoformat(), "--last-day", day.isoformat()]
    )
    assert single.exit_code == window.exit_code == 0
    envelope = json.loads(single.output)
    assert envelope == {
        "state": "published",
        "requested_day": day.isoformat(),
        "served_day": day.isoformat(),
        "rows": [{"normalized_value": 21.4}],
        "truncated": False,
    }
    assert json.loads(window.output) == {"days": [envelope]}


def test_unexpected_row_fault_is_rendered_as_a_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("raw detail must not become the public failure contract")

    monkeypatch.setattr(parquet_cli, "_row_read", fail)

    result = CliRunner().invoke(
        cli,
        ["data", "parquet", "day", "--layer", "signal", "--zoom", "13", "--day", "2026-08-01"],
    )

    assert result.exit_code != 0
    assert "serving_fault" in result.output
    assert "RuntimeError" in result.output
    assert "raw detail" not in result.output


def test_coverage_configuration_fault_is_rendered_inside_the_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_settings: object) -> None:
        raise ValueError("OBJECT_STORE_SECRET_ACCESS_KEY")

    monkeypatch.setattr(type(parquet_cli.settings), "require_object_store", fail)

    result = CliRunner().invoke(cli, ["data", "parquet", "coverage"])

    assert result.exit_code != 0
    assert "serving_fault" in result.output
    assert "ValueError" in result.output
    assert "OBJECT_STORE_SECRET_ACCESS_KEY" not in result.output


def test_core_refusal_code_and_message_survive_the_cli_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ServingRefusalError("serving_at_capacity", "all bounded slots are busy")

    monkeypatch.setattr(parquet_cli, "_row_read", refuse)

    result = CliRunner().invoke(
        cli,
        ["data", "parquet", "day", "--layer", "signal", "--zoom", "13", "--day", "2026-08-01"],
    )

    assert result.exit_code != 0
    assert "serving_at_capacity: all bounded slots are busy" in result.output
