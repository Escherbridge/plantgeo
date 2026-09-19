"""The typed argument bundles every kernel speaks, and the results it answers with.

Layer L1 (method/kernels). One bundle per kernel exists because a Mojo function imported from
Python takes at most six arguments (spec FR-9); a bundle keeps the Python call site readable while
the native call site stays inside that cap. Rationale lives in `AGENTS.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy

from plantgeo_ml_service.foundation.canonical import validate_finite

if TYPE_CHECKING:
    from numpy.typing import NDArray

#: How many float64 lanes one neighbour search may carry. A weighted-L2 search is quadratic in
#: rows, so an unbounded candidate matrix is a denial of service rather than a slow query.
MAX_CANDIDATE_ROWS: Final = 2_000_000
MAX_FEATURE_COUNT: Final = 512
MAX_SIMULATION_COUNT: Final = 1_000_000
MAX_HORIZON_DAYS: Final = 400

#: Named so the shape checks read as intent rather than as bare numbers.
VECTOR_DIMENSIONS: Final = 1
MATRIX_DIMENSIONS: Final = 2


class KernelArgumentError(ValueError):
    """A bundle does not describe a shape any kernel can execute."""


def _require_finite(name: str, array: NDArray[numpy.float64]) -> NDArray[numpy.float64]:
    """Return one float64 array, refusing any NaN or infinity by naming the first offending entry.

    Every float64 array a kernel reads passes through here. A native kernel has no exception to
    raise across the Python boundary and no `numpy.nan`-aware comparison: a NaN distance is never
    less than the running best, so a NaN candidate is silently never selected and an infinite one
    is indistinguishable from the `Float64.MAX` sentinel the Mojo search uses for "ineligible".
    Either would answer a plausible-looking neighbour set computed from garbage, so the refusal
    happens at the bundle instead.

    The scalar bounds `lower_bound` and `upper_bound` are deliberately NOT checked: the empirical
    resample shape passes `-inf`/`+inf` to mean "do not clip", which is a legitimate value there.
    """
    finite = numpy.isfinite(array)
    if not bool(finite.all()):
        flattened = numpy.ascontiguousarray(array).reshape(-1)
        position = int(numpy.argmin(finite.reshape(-1)))
        try:
            validate_finite(float(flattened[position]), f"{name}[{position}]")
        except ValueError as error:
            raise KernelArgumentError(str(error)) from error
    return array


def _require_float_vector(name: str, values: NDArray[numpy.float64]) -> NDArray[numpy.float64]:
    """Return one contiguous finite float64 vector, refusing anything a native kernel could not read."""
    array = numpy.ascontiguousarray(values, dtype=numpy.float64)
    if array.ndim != VECTOR_DIMENSIONS:
        raise KernelArgumentError(f"{name} must be one-dimensional, got shape {array.shape}")
    return _require_finite(name, array)


def _require_float_matrix(name: str, values: NDArray[numpy.float64]) -> NDArray[numpy.float64]:
    """Return one contiguous finite row-major float64 matrix, refusing anything a native kernel could not read."""
    array = numpy.ascontiguousarray(values, dtype=numpy.float64)
    if array.ndim != MATRIX_DIMENSIONS:
        raise KernelArgumentError(f"{name} must be two-dimensional, got shape {array.shape}")
    return _require_finite(name, array)


def _require_int_vector(name: str, values: NDArray[numpy.int64]) -> NDArray[numpy.int64]:
    """Return one contiguous int64 vector, refusing anything a native kernel could not read."""
    array = numpy.ascontiguousarray(values, dtype=numpy.int64)
    if array.ndim != VECTOR_DIMENSIONS:
        raise KernelArgumentError(f"{name} must be one-dimensional, got shape {array.shape}")
    return array


@dataclass(frozen=True, slots=True)
class NeighborSearchRequest:
    """One weighted-L2 neighbour search: a query, a candidate archive, and the two leakage windows.

    `candidate_time_index` carries whatever monotone integer day index the caller uses (a
    `date.toordinal()` in this service). A candidate is eligible when it lies strictly before
    `horizon_boundary_index` AND further than `exclusion_days` from `query_time_index` on either
    side, which is exactly `method/ml/analog_ensemble.find_analogs`' published rule.
    """

    query_vector: NDArray[numpy.float64]
    candidate_matrix: NDArray[numpy.float64]
    candidate_time_index: NDArray[numpy.int64]
    feature_weights: NDArray[numpy.float64]
    query_time_index: int
    horizon_boundary_index: int
    exclusion_days: int
    neighbor_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "query_vector", _require_float_vector("query_vector", self.query_vector))
        object.__setattr__(self, "candidate_matrix", _require_float_matrix("candidate_matrix", self.candidate_matrix))
        object.__setattr__(
            self, "candidate_time_index", _require_int_vector("candidate_time_index", self.candidate_time_index)
        )
        object.__setattr__(self, "feature_weights", _require_float_vector("feature_weights", self.feature_weights))
        rows, features = self.candidate_matrix.shape
        if rows == 0:
            raise KernelArgumentError("candidate_matrix has no rows; there is nothing to search")
        if rows > MAX_CANDIDATE_ROWS:
            raise KernelArgumentError(f"{rows} candidate rows exceeds the {MAX_CANDIDATE_ROWS} row ceiling")
        if not 0 < features <= MAX_FEATURE_COUNT:
            raise KernelArgumentError(f"{features} features is outside 1..{MAX_FEATURE_COUNT}")
        if self.query_vector.size != features:
            raise KernelArgumentError(f"query_vector has {self.query_vector.size} entries, expected {features}")
        if self.feature_weights.size != features:
            raise KernelArgumentError(f"feature_weights has {self.feature_weights.size} entries, expected {features}")
        if self.candidate_time_index.size != rows:
            raise KernelArgumentError(
                f"candidate_time_index has {self.candidate_time_index.size} entries, expected {rows}"
            )
        if self.neighbor_count < 1:
            raise KernelArgumentError(f"neighbor_count must be positive, got {self.neighbor_count}")
        if self.exclusion_days < 0:
            raise KernelArgumentError(f"exclusion_days must not be negative, got {self.exclusion_days}")

    @property
    def eligible_mask(self) -> NDArray[numpy.bool_]:
        """Return which candidates survive both leakage windows, in candidate order."""
        horizon_safe = self.candidate_time_index < self.horizon_boundary_index
        outside_window = numpy.abs(self.candidate_time_index - self.query_time_index) > self.exclusion_days
        return horizon_safe & outside_window


@dataclass(frozen=True, slots=True)
class NeighborSearchResult:
    """The selected analogs: their weighted distances and their rows in the candidate matrix."""

    distances: NDArray[numpy.float64]
    candidate_indices: NDArray[numpy.int64]


@dataclass(frozen=True, slots=True)
class SeasonalBootstrapRequest:
    """One seasonal bootstrap ensemble, over a draw stream the CALLER already produced.

    No RNG crosses this boundary. `draw_indices` is whatever numpy's seeded PCG64 emitted, so the
    recorded `random_seed` stays the one authoritative reproduction handle (spec FR-9) and the
    native kernel can never disagree with the Python reference about a random number.

    `quantile_probabilities` are probabilities in `[0, 1]`, NOT percentages, and must be the exact
    float64 values `numpy.percentile` would have derived, because the linear interpolation below is
    sensitive to the last bit of the probability: `0.1 * 100.0 / 100.0` is not `0.1`.
    """

    draw_indices: NDArray[numpy.int64]
    innovation_pools: NDArray[numpy.float64]
    path_offsets: NDArray[numpy.float64]
    innovation_scales: NDArray[numpy.float64]
    lower_bound: float
    upper_bound: float
    quantile_probabilities: NDArray[numpy.float64]

    def __post_init__(self) -> None:
        draws = numpy.ascontiguousarray(self.draw_indices, dtype=numpy.int64)
        if draws.ndim != MATRIX_DIMENSIONS:
            raise KernelArgumentError(f"draw_indices must be two-dimensional, got shape {draws.shape}")
        object.__setattr__(self, "draw_indices", draws)
        object.__setattr__(self, "innovation_pools", _require_float_matrix("innovation_pools", self.innovation_pools))
        object.__setattr__(self, "path_offsets", _require_float_vector("path_offsets", self.path_offsets))
        object.__setattr__(
            self, "innovation_scales", _require_float_vector("innovation_scales", self.innovation_scales)
        )
        object.__setattr__(
            self, "quantile_probabilities", _require_float_vector("quantile_probabilities", self.quantile_probabilities)
        )
        simulations, horizon = draws.shape
        if not 0 < simulations <= MAX_SIMULATION_COUNT:
            raise KernelArgumentError(f"{simulations} simulations is outside 1..{MAX_SIMULATION_COUNT}")
        if not 0 < horizon <= MAX_HORIZON_DAYS:
            raise KernelArgumentError(f"{horizon} horizon days is outside 1..{MAX_HORIZON_DAYS}")
        if self.innovation_pools.shape[0] != horizon:
            raise KernelArgumentError(f"innovation_pools has {self.innovation_pools.shape[0]} rows, expected {horizon}")
        if self.path_offsets.size != horizon:
            raise KernelArgumentError(f"path_offsets has {self.path_offsets.size} entries, expected {horizon}")
        if self.innovation_scales.size != horizon:
            raise KernelArgumentError(
                f"innovation_scales has {self.innovation_scales.size} entries, expected {horizon}"
            )
        if self.quantile_probabilities.size < 1:
            raise KernelArgumentError("at least one quantile probability is required")
        if bool((self.quantile_probabilities < 0.0).any() or (self.quantile_probabilities > 1.0).any()):
            raise KernelArgumentError("quantile probabilities are fractions in [0, 1], not percentages")
        if self.lower_bound > self.upper_bound:
            raise KernelArgumentError(f"lower bound {self.lower_bound} exceeds upper bound {self.upper_bound}")
        pool_width = self.innovation_pools.shape[1]
        if bool((draws < 0).any() or (draws >= pool_width).any()):
            raise KernelArgumentError(f"a draw index falls outside the 0..{pool_width - 1} pool width")

    @property
    def simulation_count(self) -> int:
        """Return how many ensemble members one horizon day carries."""
        return int(self.draw_indices.shape[0])

    @property
    def horizon_days(self) -> int:
        """Return how many horizon days the ensemble spans."""
        return int(self.draw_indices.shape[1])


@dataclass(frozen=True, slots=True)
class SeasonalFeatureRequest:
    """One batch of cyclical day-of-year and FAO-56 photoperiod features, one entry per row."""

    ordinal_days: NDArray[numpy.int64]
    year_lengths: NDArray[numpy.float64]
    latitudes: NDArray[numpy.float64]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ordinal_days", _require_int_vector("ordinal_days", self.ordinal_days))
        object.__setattr__(self, "year_lengths", _require_float_vector("year_lengths", self.year_lengths))
        object.__setattr__(self, "latitudes", _require_float_vector("latitudes", self.latitudes))
        rows = self.ordinal_days.size
        if self.year_lengths.size != rows:
            raise KernelArgumentError(f"year_lengths has {self.year_lengths.size} entries, expected {rows}")
        if self.latitudes.size != rows:
            raise KernelArgumentError(f"latitudes has {self.latitudes.size} entries, expected {rows}")
        if bool((self.year_lengths <= 0.0).any()):
            raise KernelArgumentError("a year length must be positive")

    @property
    def row_count(self) -> int:
        """Return how many rows the batch carries."""
        return int(self.ordinal_days.size)


@dataclass(frozen=True, slots=True)
class SeasonalFeatureResult:
    """The three mandatory seasonal features, in row order."""

    day_of_year_sine: NDArray[numpy.float64]
    day_of_year_cosine: NDArray[numpy.float64]
    photoperiod_seconds: NDArray[numpy.float64]


__all__ = [
    "MATRIX_DIMENSIONS",
    "MAX_CANDIDATE_ROWS",
    "MAX_FEATURE_COUNT",
    "MAX_HORIZON_DAYS",
    "MAX_SIMULATION_COUNT",
    "VECTOR_DIMENSIONS",
    "KernelArgumentError",
    "NeighborSearchRequest",
    "NeighborSearchResult",
    "SeasonalBootstrapRequest",
    "SeasonalFeatureRequest",
    "SeasonalFeatureResult",
]
