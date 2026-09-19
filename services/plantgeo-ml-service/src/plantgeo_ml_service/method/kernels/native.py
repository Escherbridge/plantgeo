"""The Mojo-backed kernels: loading the compiled extensions, and flattening bundles for them.

Layer L1 (method/kernels). Every function a Mojo module exports takes at most six arguments, so the
bundles above are flattened into contiguous numpy buffers here and the native side writes its answer
into caller-allocated output arrays. Nothing allocates across the boundary.

This module never silently falls back: when the extensions are absent, `load_native_kernels` raises
`NativeKernelsUnavailableError` naming the directory it looked in and the rollback environment
variable. `PLANTGEO_ML_KERNELS=mojo` is a promise the operator made, and a quiet downgrade to the
Python reference would make a benchmark unfalsifiable. Rationale lives in `AGENTS.md`.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import numpy

from plantgeo_ml_service.method.kernels.bundles import (
    NeighborSearchRequest,
    NeighborSearchResult,
    SeasonalBootstrapRequest,
    SeasonalFeatureRequest,
    SeasonalFeatureResult,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

BACKEND_NAME: Final = "mojo"

#: Where `pixi run build-kernels` drops the compiled extensions, and where the Docker `mojo-build`
#: stage copies them to. Excluded from the quality receipt's tree digest (`scripts/quality_receipt.py`)
#: because it holds build output, not reviewed source.
NATIVE_DIRECTORY: Final = Path(__file__).resolve().parent / "_native"

#: One Mojo source file becomes one extension module; the stem is also its `PyInit_` symbol.
NEIGHBOR_SEARCH_MODULE: Final = "plantgeo_knn_search"
SEASONAL_BOOTSTRAP_MODULE: Final = "plantgeo_seasonal_bootstrap"
SEASONAL_FEATURES_MODULE: Final = "plantgeo_seasonal_features"
NATIVE_MODULE_NAMES: Final[tuple[str, ...]] = (
    NEIGHBOR_SEARCH_MODULE,
    SEASONAL_BOOTSTRAP_MODULE,
    SEASONAL_FEATURES_MODULE,
)

#: How many int64 header slots precede the per-candidate day index inside the search bundle.
#: Declared again as `HEADER_SLOTS` in `kernels/knn_search.mojo`, because an integer cannot be
#: shared across the Python boundary at compile time. Drift between the two does not crash: the
#: kernel would read a day index as a scalar, exclude the wrong candidates, and answer a plausible
#: neighbour set. `NativeKernels.describe_neighbor_bundle` round-trips a header through both sides
#: so `tests/kernels/test_kernel_dispatch.py` can assert the agreement instead of assuming it.
NEIGHBOR_SEARCH_HEADER_SLOTS: Final = 6

BUILD_COMMAND: Final = "pixi run build-kernels"
ROLLBACK_HINT: Final = "set PLANTGEO_ML_KERNELS=python to run the pure-Python reference instead"


class NativeKernelsUnavailableError(RuntimeError):
    """`PLANTGEO_ML_KERNELS=mojo` was requested and the compiled extensions are not on disk."""


def native_module_path(module_name: str, *, directory: Path = NATIVE_DIRECTORY) -> Path:
    """Return where one compiled kernel extension is expected to sit."""
    return directory / f"{module_name}.so"


def missing_native_modules(*, directory: Path = NATIVE_DIRECTORY) -> tuple[str, ...]:
    """Return the kernel modules that have not been built, in declaration order."""
    return tuple(name for name in NATIVE_MODULE_NAMES if not native_module_path(name, directory=directory).is_file())


def _load_extension(module_name: str, *, directory: Path) -> Any:
    """Import one compiled Mojo extension by file path, refusing with the build command if absent."""
    path = native_module_path(module_name, directory=directory)
    loader = importlib.machinery.ExtensionFileLoader(module_name, str(path))
    specification = importlib.util.spec_from_loader(module_name, loader)
    if specification is None:  # pragma: no cover - spec_from_loader only returns None without a loader
        raise NativeKernelsUnavailableError(f"{path} could not be turned into an import specification")
    module = importlib.util.module_from_spec(specification)
    loader.exec_module(module)
    return module


def pack_neighbor_bundle(request: NeighborSearchRequest) -> NDArray[numpy.int64]:
    """Return the one int64 bundle the search kernel reads: six scalars, then one day per candidate.

    The six-argument cap on a Mojo function imported from Python (spec FR-9) is why the scalars
    travel inside an array at all. The slot ORDER here is the contract `kernels/knn_search.mojo`
    reads by offset.
    """
    rows, features = request.candidate_matrix.shape
    bundle = numpy.empty(NEIGHBOR_SEARCH_HEADER_SLOTS + rows, dtype=numpy.int64)
    bundle[0] = rows
    bundle[1] = features
    bundle[2] = request.neighbor_count
    bundle[3] = request.query_time_index
    bundle[4] = request.horizon_boundary_index
    bundle[5] = request.exclusion_days
    bundle[NEIGHBOR_SEARCH_HEADER_SLOTS:] = request.candidate_time_index
    return bundle


class NativeKernels:
    """The three Mojo kernels, bound to their compiled extensions."""

    def __init__(self, *, directory: Path = NATIVE_DIRECTORY) -> None:
        self._neighbor_search = _load_extension(NEIGHBOR_SEARCH_MODULE, directory=directory)
        self._seasonal_bootstrap = _load_extension(SEASONAL_BOOTSTRAP_MODULE, directory=directory)
        self._seasonal_features = _load_extension(SEASONAL_FEATURES_MODULE, directory=directory)

    def neighbor_search(self, request: NeighborSearchRequest) -> NeighborSearchResult:
        """Run the weighted-L2 search natively, returning the same shape the reference does."""
        bundle = pack_neighbor_bundle(request)
        distances = numpy.zeros(request.neighbor_count, dtype=numpy.float64)
        indices = numpy.full(request.neighbor_count, -1, dtype=numpy.int64)
        selected = int(
            self._neighbor_search.search_neighbors(
                request.query_vector,
                numpy.ascontiguousarray(request.candidate_matrix).reshape(-1),
                request.feature_weights,
                bundle,
                distances,
                indices,
            )
        )
        return NeighborSearchResult(distances=distances[:selected], candidate_indices=indices[:selected])

    def describe_neighbor_bundle(self, request: NeighborSearchRequest) -> tuple[int, NDArray[numpy.int64]]:
        """Return the kernel's own `HEADER_SLOTS` and the header it read back, for a layout assertion.

        Diagnostic, not a run path: nothing in the service calls this. It exists so the two
        independent declarations of the header size can be compared rather than trusted; see the
        note at `NEIGHBOR_SEARCH_HEADER_SLOTS`.
        """
        bundle = pack_neighbor_bundle(request)
        # One slot past the header, so the echo also proves where the per-candidate days begin.
        echoed = numpy.zeros(NEIGHBOR_SEARCH_HEADER_SLOTS + 1, dtype=numpy.int64)
        slots = int(self._neighbor_search.describe_bundle(bundle, echoed))
        return slots, echoed

    def seasonal_bootstrap(self, request: SeasonalBootstrapRequest) -> NDArray[numpy.float64]:
        """Run the bootstrap ensemble and its quantiles natively, shaped `(quantiles, horizon)`."""
        horizon = request.horizon_days
        quantile_count = int(request.quantile_probabilities.size)
        path_terms = numpy.empty(2 * horizon, dtype=numpy.float64)
        path_terms[:horizon] = request.path_offsets
        path_terms[horizon:] = request.innovation_scales
        shape = numpy.array(
            [request.simulation_count, horizon, request.innovation_pools.shape[1], quantile_count],
            dtype=numpy.int64,
        )
        limits = numpy.empty(2 + quantile_count, dtype=numpy.float64)
        limits[0] = request.lower_bound
        limits[1] = request.upper_bound
        limits[2:] = request.quantile_probabilities
        output = numpy.zeros(quantile_count * horizon, dtype=numpy.float64)
        self._seasonal_bootstrap.bootstrap_quantiles(
            request.draw_indices.reshape(-1),
            numpy.ascontiguousarray(request.innovation_pools).reshape(-1),
            path_terms,
            shape,
            limits,
            output,
        )
        return output.reshape(quantile_count, horizon)

    def seasonal_features(self, request: SeasonalFeatureRequest) -> SeasonalFeatureResult:
        """Run the cyclical and photoperiod feature kernel natively, one entry per row."""
        rows = request.row_count
        output = numpy.zeros(3 * rows, dtype=numpy.float64)
        self._seasonal_features.seasonal_features(
            request.ordinal_days,
            request.year_lengths,
            request.latitudes,
            numpy.array([rows], dtype=numpy.int64),
            output,
        )
        return SeasonalFeatureResult(
            day_of_year_sine=output[:rows].copy(),
            day_of_year_cosine=output[rows : 2 * rows].copy(),
            photoperiod_seconds=output[2 * rows :].copy(),
        )


def native_load_failure(*, directory: Path = NATIVE_DIRECTORY) -> str | None:
    """Return why the native kernels cannot run here, or None when they can.

    Presence on disk is NOT enough: a linux-64 `.so` built in WSL2 sits in the same worktree a
    Windows interpreter reads, and only an attempted load tells the two apart. A parity harness
    that skipped on absence alone would try to load it and fail instead of skipping.
    """
    missing = missing_native_modules(directory=directory)
    if missing:
        return (
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} absent from {directory}. "
            f"Build with `{BUILD_COMMAND}` in WSL2 or a linux-64 container, or {ROLLBACK_HINT}."
        )
    try:
        NativeKernels(directory=directory)
    except (ImportError, OSError) as error:
        return (
            f"the compiled kernels in {directory} did not load on {sys.platform}: {error}. "
            f"Mojo builds linux-64 only, so a Windows interpreter never loads them; {ROLLBACK_HINT}."
        )
    return None


def load_native_kernels(*, directory: Path = NATIVE_DIRECTORY) -> NativeKernels:
    """Return the native kernels, or refuse by naming what is wrong and how to fix it."""
    failure = native_load_failure(directory=directory)
    if failure is not None:
        raise NativeKernelsUnavailableError(f"PLANTGEO_ML_KERNELS=mojo cannot start: {failure}")
    return NativeKernels(directory=directory)


__all__ = [
    "BACKEND_NAME",
    "BUILD_COMMAND",
    "NATIVE_DIRECTORY",
    "NATIVE_MODULE_NAMES",
    "NEIGHBOR_SEARCH_HEADER_SLOTS",
    "NativeKernels",
    "NativeKernelsUnavailableError",
    "load_native_kernels",
    "missing_native_modules",
    "native_load_failure",
    "native_module_path",
    "pack_neighbor_bundle",
]
