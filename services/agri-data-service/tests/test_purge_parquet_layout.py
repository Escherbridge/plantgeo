"""Fail-closed retirement contract for the historical Parquet purge tool."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def _tool() -> Any:
    path = Path(__file__).parents[1] / "scripts" / "purge_parquet_layout.py"
    name = "plantgeo_purge_parquet_layout_tool"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TOOL = _tool()


def test_confirm_refuses_before_object_store_construction(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    constructed = False

    def fail_if_constructed(_cls: object, _settings: object = None) -> object:
        nonlocal constructed
        constructed = True
        raise AssertionError("retired purge must refuse before object-store construction")

    monkeypatch.setattr(TOOL.ObjectStore, "from_settings", classmethod(fail_if_constructed))
    monkeypatch.setattr(TOOL.sys, "argv", ["purge_parquet_layout.py", "--confirm"])

    with pytest.raises(SystemExit) as error:
        TOOL.main()

    assert error.value.code != 0
    assert constructed is False
    assert "mutation mode is retired" in capsys.readouterr().err
