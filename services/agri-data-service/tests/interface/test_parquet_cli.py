"""The CLI maps shared Parquet-core failures without inventing transport semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

import agri_data_service.interface.cli.parquet as parquet_cli
from agri_data_service.interface.cli import cli
from agri_data_service.parquet_ops.faults import ServingRefusalError

if TYPE_CHECKING:
    import pytest


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
