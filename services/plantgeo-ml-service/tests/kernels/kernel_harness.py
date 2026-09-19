"""What every kernel parity test needs: the fixtures, the two backends, and the named skip.

The Mojo backend is SKIPPED, never silently passed over, and the reason names the build command,
so a green run on Windows and a green run in WSL2 are told apart by reading the report rather than
by trusting it.

A skip is only honest when nobody ASKED for Mojo. `PLANTGEO_ML_KERNELS=mojo` is an operator saying
"the native kernels are the ones I am running", so an unloadable build under that selection is a
FAILURE, not a skip: a suite that skipped there would report green for a run in which the kernels
under test never executed, which is exactly the unfalsifiable benchmark the dispatch refuses to be.
The three-way decision lives in `native_backend_decision`, which is pure so both branches are
testable without a Mojo build.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, cast

import pytest

from plantgeo_ml_service.method.kernels import (
    MOJO_BACKEND,
    KernelBackend,
    MojoKernels,
    PythonKernels,
    selected_backend_name,
)
from plantgeo_ml_service.method.kernels.native import (
    NativeKernels,
    load_native_kernels,
    native_load_failure,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

FIXTURE_DIRECTORY: Final = Path(__file__).resolve().parents[1] / "fixtures" / "kernels"

REGENERATE_COMMAND: Final = "uv run --no-sync python scripts/regenerate_kernel_fixtures.py"

#: What to do with the Mojo parameter: run it, skip it by name, or fail it by name.
NativeDecision = Literal["run", "skip", "fail"]


def load_fixture(name: str) -> dict[str, Any]:
    """Return one checked-in golden output, or refuse by naming how it is made."""
    path = FIXTURE_DIRECTORY / f"{name}.json"
    if not path.is_file():
        raise AssertionError(f"{path} is missing; regenerate it with `{REGENERATE_COMMAND}`")
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def native_skip_reason() -> str | None:
    """Return why the Mojo backend cannot run here, or None when it can."""
    failure = native_load_failure()
    return None if failure is None else f"Mojo kernels unavailable: {failure}"


def native_backend_decision(
    *,
    environment: Mapping[str, str] | None = None,
    failure: str | None,
) -> tuple[NativeDecision, str]:
    """Return what to do with the Mojo parameter and the reason to print, given a load failure.

    `failure` is `native_load_failure()`'s answer, injected so a test can exercise the failing
    branch on a box that has a perfectly good Mojo build (and the passing branch on one that has
    none). `environment` defaults to the real process environment.
    """
    source = dict(os.environ) if environment is None else dict(environment)
    if failure is None:
        return "run", "the Mojo kernels are built"
    reason = f"Mojo kernels unavailable: {failure}"
    if selected_backend_name(source) == MOJO_BACKEND:
        failure_reason = (
            f"PLANTGEO_ML_KERNELS=mojo was selected but {reason}. "
            f"A skip here would report green for a run that never executed the kernels under test."
        )
        return "fail", failure_reason
    return "skip", reason


def failing_backend(reason: str) -> type[KernelBackend]:
    """Return a backend type whose construction fails the test by name, never skips it."""

    class UnavailableMojoKernels:
        """Refuses at construction, exactly where `MojoKernels` would have refused."""

        name = MOJO_BACKEND

        def __init__(self) -> None:
            pytest.fail(reason)

    return cast("type[KernelBackend]", UnavailableMojoKernels)


def backends() -> tuple[Any, ...]:
    """Return every kernel backend: Mojo runs, skips by NAME, or FAILS by name when it was asked for."""
    decision, reason = native_backend_decision(failure=native_load_failure())
    mojo_parameter = (
        pytest.param(failing_backend(reason), id="mojo")
        if decision == "fail"
        else pytest.param(
            MojoKernels,
            id="mojo",
            marks=pytest.mark.skipif(decision == "skip", reason=reason),
        )
    )
    return (pytest.param(PythonKernels, id="python"), mojo_parameter)


def build_backend(backend_type: type[KernelBackend]) -> KernelBackend:
    """Return one backend instance, constructed the way the dispatch would construct it."""
    return backend_type()


def require_native_kernels() -> NativeKernels:
    """Return the loaded Mojo kernels for a Mojo-only test, skipping or FAILING exactly as `backends()` does."""
    decision, reason = native_backend_decision(failure=native_load_failure())
    if decision == "fail":
        pytest.fail(reason)
    if decision == "skip":
        pytest.skip(reason)
    return load_native_kernels()


__all__ = [
    "FIXTURE_DIRECTORY",
    "REGENERATE_COMMAND",
    "NativeDecision",
    "backends",
    "build_backend",
    "failing_backend",
    "load_fixture",
    "native_backend_decision",
    "native_skip_reason",
    "require_native_kernels",
]
