"""`PLANTGEO_ML_KERNELS` selects an implementation, and `mojo` refuses rather than downgrades.

The refusal is the point: a quiet fallback to the Python reference would make a Mojo benchmark
unfalsifiable and would let a Railway service claim an optimisation it is not running.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _pytest.outcomes import Failed
from kernel_cases import neighbor_search_case
from kernel_harness import failing_backend, native_backend_decision, require_native_kernels

from plantgeo_ml_service.method.kernels import (
    DEFAULT_BACKEND,
    MOJO_BACKEND,
    PYTHON_BACKEND,
    PythonKernels,
    UnknownKernelBackendError,
    kernels,
    reset_kernel_cache,
    selected_backend_name,
)
from plantgeo_ml_service.method.kernels.native import (
    BUILD_COMMAND,
    NATIVE_MODULE_NAMES,
    NEIGHBOR_SEARCH_HEADER_SLOTS,
    NativeKernelsUnavailableError,
    load_native_kernels,
    missing_native_modules,
    pack_neighbor_bundle,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_the_default_implementation_is_the_python_reference() -> None:
    """Python is the default and the rollback; Mojo is never reached by an unset environment."""
    assert DEFAULT_BACKEND == PYTHON_BACKEND
    assert selected_backend_name({}) == PYTHON_BACKEND
    assert isinstance(kernels({}), PythonKernels)


def test_an_unknown_implementation_name_is_refused_by_name() -> None:
    """A typo must not silently mean `python`, or a mis-set service would look correct."""
    with pytest.raises(UnknownKernelBackendError, match="rust"):
        selected_backend_name({"PLANTGEO_ML_KERNELS": "rust"})


def test_the_selection_ignores_case_and_surrounding_space() -> None:
    """Railway variables arrive as typed strings; `Mojo ` must not become an unknown backend."""
    assert selected_backend_name({"PLANTGEO_ML_KERNELS": " Mojo "}) == MOJO_BACKEND


def test_an_empty_selection_falls_back_to_the_default() -> None:
    """An empty variable is how a platform spells "unset"; it must not be an unknown backend."""
    assert selected_backend_name({"PLANTGEO_ML_KERNELS": ""}) == PYTHON_BACKEND


def test_mojo_mode_refuses_when_the_extensions_are_absent(tmp_path: Path) -> None:
    """The refusal names every missing module, the build command and the rollback variable."""
    with pytest.raises(NativeKernelsUnavailableError) as refusal:
        load_native_kernels(directory=tmp_path)

    message = str(refusal.value)
    assert BUILD_COMMAND in message
    assert "PLANTGEO_ML_KERNELS=python" in message
    for module_name in NATIVE_MODULE_NAMES:
        assert module_name in message


def test_every_declared_kernel_module_is_looked_for(tmp_path: Path) -> None:
    """Three kernels ship; a fourth added without a loader entry would never be loaded at all."""
    assert missing_native_modules(directory=tmp_path) == NATIVE_MODULE_NAMES


def test_the_backend_is_built_once_per_selection() -> None:
    """Loading a C extension per call would cost more than the kernel saves."""
    reset_kernel_cache()

    first = kernels({"PLANTGEO_ML_KERNELS": "python"})
    second = kernels({"PLANTGEO_ML_KERNELS": "python"})

    assert first is second


# --- the harness' three-way decision: run, skip by name, or FAIL by name (both branches) ---


def test_an_unbuilt_mojo_kernel_only_skips_when_python_mode_was_selected() -> None:
    """Nobody asked for Mojo, so an absent build is a fact about the box, not a defect."""
    decision, reason = native_backend_decision(environment={}, failure="no .so on disk")

    assert decision == "skip"
    assert "no .so on disk" in reason


def test_an_unbuilt_mojo_kernel_fails_when_mojo_mode_was_selected() -> None:
    """The operator said the native kernels are what runs; skipping would report a green non-run."""
    decision, reason = native_backend_decision(
        environment={"PLANTGEO_ML_KERNELS": "mojo"},
        failure="no .so on disk",
    )

    assert decision == "fail"
    assert "PLANTGEO_ML_KERNELS=mojo" in reason
    assert "no .so on disk" in reason


def test_a_loadable_mojo_kernel_runs_under_either_selection() -> None:
    """No failure means no excuse: the parameter runs whichever implementation was selected."""
    assert native_backend_decision(environment={}, failure=None)[0] == "run"
    assert native_backend_decision(environment={"PLANTGEO_ML_KERNELS": "mojo"}, failure=None)[0] == "run"


def test_the_failing_backend_fails_the_test_rather_than_skipping_it() -> None:
    """`backends()` hands this type to the Mojo parameter; constructing it must be a FAILURE."""
    backend_type = failing_backend("the reason a reader needs")

    with pytest.raises(Failed, match="the reason a reader needs"):
        backend_type()


# --- the two independent declarations of the search bundle's header size ---


def test_both_sides_agree_on_the_bundle_header_layout() -> None:
    """`NEIGHBOR_SEARCH_HEADER_SLOTS` and the kernel's `HEADER_SLOTS` are two declarations, not one.

    Drift is silent: the kernel would read a day index as a scalar and answer a plausible wrong
    neighbour set. So the header is packed by Python, read back by Mojo, and compared here.
    """
    native = require_native_kernels()
    request = neighbor_search_case()

    slots, echoed = native.describe_neighbor_bundle(request)

    assert slots == NEIGHBOR_SEARCH_HEADER_SLOTS
    assert echoed[:NEIGHBOR_SEARCH_HEADER_SLOTS].tolist() == [
        request.candidate_matrix.shape[0],
        request.candidate_matrix.shape[1],
        request.neighbor_count,
        request.query_time_index,
        request.horizon_boundary_index,
        request.exclusion_days,
    ]
    assert int(echoed[NEIGHBOR_SEARCH_HEADER_SLOTS]) == int(request.candidate_time_index[0])


def test_the_python_packer_puts_the_first_candidate_day_where_the_kernel_looks() -> None:
    """The packer is asserted on its own too, so a python-mode run still guards the slot order."""
    request = neighbor_search_case()

    bundle = pack_neighbor_bundle(request)

    assert bundle.size == NEIGHBOR_SEARCH_HEADER_SLOTS + request.candidate_matrix.shape[0]
    assert bundle[NEIGHBOR_SEARCH_HEADER_SLOTS:].tolist() == request.candidate_time_index.tolist()
