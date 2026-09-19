"""The pure-Python reference for every kernel: the default implementation and the rollback.

Layer L1 (method/kernels). This module is the DEFINITION of what each kernel computes; the Mojo
translation in `kernels/*.mojo` is judged against it, never the other way round. Every operation
order here is load-bearing, because the parity harness asserts bit-identical output for the
neighbour search and the bootstrap. Rationale lives in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

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

#: FAO-56 (Allen et al. 1998) equations 24 and 25, and the seconds a radian of hour angle is worth.
#: Held here as well as in `pipeline/fire_risk_plane.py` on purpose: a kernel may import `foundation`
#: only, so the two copies are kept honest by `tests/kernels/test_parity_seasonal_features.py`
#: asserting this kernel against that module's scalar helpers rather than by a shared import.
SECONDS_PER_HOUR: Final = 3_600.0
HOURS_PER_DAY: Final = 24.0
DAYS_PER_COMMON_YEAR: Final = 365.0
SOLAR_DECLINATION_AMPLITUDE: Final = 0.409
SOLAR_DECLINATION_PHASE: Final = 1.39

BACKEND_NAME: Final = "python"


def neighbor_search(request: NeighborSearchRequest) -> NeighborSearchResult:
    """Return the nearest eligible analogs by weighted L2 distance, nearest first.

    The distance is the euclidean norm of the difference between the PRE-WEIGHTED query and the
    PRE-WEIGHTED candidate (`candidate * weight - query * weight`), which is the association
    `method/ml/analog_ensemble.find_analogs` has always used; folding the weight into the
    difference instead would move the last bit. The squares are then accumulated feature by feature
    in increasing feature order, because a pairwise or blocked sum would give the Mojo kernel a
    different rounding to reproduce.

    Ties are broken by the lower candidate index: a stable argsort, matching a first-wins linear
    scan in the native kernel.
    """
    weighted_query = request.query_vector * request.feature_weights
    weighted_candidates = request.candidate_matrix * request.feature_weights
    differences = weighted_candidates - weighted_query
    squares = differences * differences
    totals = numpy.zeros(request.candidate_matrix.shape[0], dtype=numpy.float64)
    for feature in range(request.candidate_matrix.shape[1]):
        totals += squares[:, feature]
    distances = numpy.sqrt(totals)

    eligible = request.eligible_mask
    ranked = numpy.where(eligible, distances, numpy.inf)
    selected_count = min(request.neighbor_count, int(eligible.sum()))
    order = numpy.argsort(ranked, kind="stable")[:selected_count].astype(numpy.int64)
    return NeighborSearchResult(distances=distances[order], candidate_indices=order)


def seasonal_bootstrap(request: SeasonalBootstrapRequest) -> NDArray[numpy.float64]:
    """Return the requested quantiles of the bootstrap ensemble, shaped `(quantiles, horizon)`.

    One path is `offset + scale * pool[horizon, draw]`, clipped to the series' physical bounds. An
    empirical resample is the same arithmetic with offset 0, scale 1 and infinite bounds, so both
    of `method/monte_carlo/signal.py`'s two estimators and the NDVI forecaster share this kernel.
    """
    innovations = numpy.take_along_axis(request.innovation_pools, request.draw_indices.T, axis=1).T
    paths = request.path_offsets + request.innovation_scales * innovations
    bounded = numpy.clip(paths, request.lower_bound, request.upper_bound)
    quantiles = numpy.quantile(bounded, request.quantile_probabilities, axis=0, method="linear")
    return numpy.ascontiguousarray(quantiles, dtype=numpy.float64)


def seasonal_features(request: SeasonalFeatureRequest) -> SeasonalFeatureResult:
    """Return the cycle-closed day-of-year pair and the FAO-56 photoperiod, one entry per row.

    The cycle is closed on the row's OWN year length, so 31 December and 1 January are adjacent in
    both a common and a leap year. The photoperiod cosine is clipped, so a polar day answers a full
    day rather than a domain error.
    """
    ordinal_days = request.ordinal_days.astype(numpy.float64)
    angle = 2.0 * math.pi * (ordinal_days - 1.0) / request.year_lengths
    declination = SOLAR_DECLINATION_AMPLITUDE * numpy.sin(
        2.0 * math.pi * ordinal_days / DAYS_PER_COMMON_YEAR - SOLAR_DECLINATION_PHASE
    )
    cosine_argument = -numpy.tan(request.latitudes * (math.pi / 180.0)) * numpy.tan(declination)
    sunset_hour_angle = numpy.arccos(numpy.clip(cosine_argument, -1.0, 1.0))
    return SeasonalFeatureResult(
        day_of_year_sine=numpy.sin(angle),
        day_of_year_cosine=numpy.cos(angle),
        photoperiod_seconds=HOURS_PER_DAY / math.pi * sunset_hour_angle * SECONDS_PER_HOUR,
    )


__all__ = [
    "BACKEND_NAME",
    "neighbor_search",
    "seasonal_bootstrap",
    "seasonal_features",
]
