"""Numeric kernel sub-package (L1): one dispatch, two interchangeable implementations.

`PLANTGEO_ML_KERNELS` selects: `python` (the default, and the rollback) runs the pure references in
`reference.py`; `mojo` loads the compiled extensions and refuses to start when they are absent.
The environment variable is read here rather than through `config.Settings` because `method` may not
import the package root, and it carries the same name the settings field exposes. Rationale, and why
Mojo is an optimisation and never a dependency, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final, Protocol

from plantgeo_ml_service.method.kernels import reference
from plantgeo_ml_service.method.kernels.bundles import (
    KernelArgumentError,
    NeighborSearchRequest,
    NeighborSearchResult,
    SeasonalBootstrapRequest,
    SeasonalFeatureRequest,
    SeasonalFeatureResult,
)
from plantgeo_ml_service.method.kernels.native import (
    NativeKernelsUnavailableError,
    load_native_kernels,
)

if TYPE_CHECKING:
    import numpy
    from numpy.typing import NDArray

#: The one environment variable that decides which implementation runs.
KERNEL_SELECTION_VARIABLE: Final = "PLANTGEO_ML_KERNELS"
PYTHON_BACKEND: Final = "python"
MOJO_BACKEND: Final = "mojo"
KERNEL_BACKEND_NAMES: Final[tuple[str, ...]] = (PYTHON_BACKEND, MOJO_BACKEND)
DEFAULT_BACKEND: Final = PYTHON_BACKEND


class UnknownKernelBackendError(ValueError):
    """`PLANTGEO_ML_KERNELS` names something that is not an implementation."""


class KernelBackend(Protocol):
    """What every kernel implementation answers, whichever language computed it."""

    name: str

    def neighbor_search(self, request: NeighborSearchRequest) -> NeighborSearchResult:
        """Return the nearest eligible analogs by weighted L2 distance, nearest first."""
        ...

    def seasonal_bootstrap(self, request: SeasonalBootstrapRequest) -> NDArray[numpy.float64]:
        """Return the bootstrap ensemble's quantiles, shaped `(quantiles, horizon)`."""
        ...

    def seasonal_features(self, request: SeasonalFeatureRequest) -> SeasonalFeatureResult:
        """Return the cycle-closed day-of-year pair and the FAO-56 photoperiod, one entry per row."""
        ...


class PythonKernels:
    """The pure-Python reference implementation, wrapped as a backend."""

    name = PYTHON_BACKEND

    def neighbor_search(self, request: NeighborSearchRequest) -> NeighborSearchResult:
        """Return the nearest eligible analogs by weighted L2 distance, nearest first."""
        return reference.neighbor_search(request)

    def seasonal_bootstrap(self, request: SeasonalBootstrapRequest) -> NDArray[numpy.float64]:
        """Return the bootstrap ensemble's quantiles, shaped `(quantiles, horizon)`."""
        return reference.seasonal_bootstrap(request)

    def seasonal_features(self, request: SeasonalFeatureRequest) -> SeasonalFeatureResult:
        """Return the cycle-closed day-of-year pair and the FAO-56 photoperiod, one entry per row."""
        return reference.seasonal_features(request)


class MojoKernels:
    """The Mojo implementation, loaded on construction so an absent build fails loudly and early."""

    name = MOJO_BACKEND

    def __init__(self) -> None:
        self._native = load_native_kernels()

    def neighbor_search(self, request: NeighborSearchRequest) -> NeighborSearchResult:
        """Return the nearest eligible analogs by weighted L2 distance, nearest first."""
        return self._native.neighbor_search(request)

    def seasonal_bootstrap(self, request: SeasonalBootstrapRequest) -> NDArray[numpy.float64]:
        """Return the bootstrap ensemble's quantiles, shaped `(quantiles, horizon)`."""
        return self._native.seasonal_bootstrap(request)

    def seasonal_features(self, request: SeasonalFeatureRequest) -> SeasonalFeatureResult:
        """Return the cycle-closed day-of-year pair and the FAO-56 photoperiod, one entry per row."""
        return self._native.seasonal_features(request)


_BACKEND_CACHE: dict[str, KernelBackend] = {}


def selected_backend_name(environment: dict[str, str] | None = None) -> str:
    """Return which implementation the environment asks for, refusing an unknown name."""
    source = environment if environment is not None else dict(os.environ)
    requested = source.get(KERNEL_SELECTION_VARIABLE, DEFAULT_BACKEND).strip().lower() or DEFAULT_BACKEND
    if requested not in KERNEL_BACKEND_NAMES:
        raise UnknownKernelBackendError(
            f"{KERNEL_SELECTION_VARIABLE}={requested!r} is not a kernel implementation; "
            f"the choices are {KERNEL_BACKEND_NAMES}"
        )
    return requested


def kernels(environment: dict[str, str] | None = None) -> KernelBackend:
    """Return the selected kernel implementation, built once per process and per selection."""
    name = selected_backend_name(environment)
    cached = _BACKEND_CACHE.get(name)
    if cached is not None:
        return cached
    backend: KernelBackend = MojoKernels() if name == MOJO_BACKEND else PythonKernels()
    _BACKEND_CACHE[name] = backend
    return backend


def reset_kernel_cache() -> None:
    """Forget every built backend, so a test can switch implementations inside one process."""
    _BACKEND_CACHE.clear()


__all__ = [
    "DEFAULT_BACKEND",
    "KERNEL_BACKEND_NAMES",
    "KERNEL_SELECTION_VARIABLE",
    "MOJO_BACKEND",
    "PYTHON_BACKEND",
    "KernelArgumentError",
    "KernelBackend",
    "MojoKernels",
    "NativeKernelsUnavailableError",
    "NeighborSearchRequest",
    "NeighborSearchResult",
    "PythonKernels",
    "SeasonalBootstrapRequest",
    "SeasonalFeatureRequest",
    "SeasonalFeatureResult",
    "UnknownKernelBackendError",
    "kernels",
    "reset_kernel_cache",
    "selected_backend_name",
]
