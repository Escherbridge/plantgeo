"""Tests for the operator scripts under scripts/, loaded by file path (scripts/ is not a package)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIRECTORY = Path(__file__).resolve().parents[2] / "scripts"


def load_scripts_module(file_name: str, module_name: str) -> Any:
    """Load one module from scripts/ by file path, reusing an instance already loaded here.

    Reuse is what makes the gate testable as one system: `check.py` and `verify_quality_receipt.py`
    both do `from quality_receipt import ...`, so registering that module under its real name hands
    every caller the same instance -- and the same `ReceiptError` class for `pytest.raises`.
    """
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPTS_DIRECTORY / file_name)
    if spec is None or spec.loader is None:
        message = f"cannot load {file_name} from {_SCRIPTS_DIRECTORY}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
