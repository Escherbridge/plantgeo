"""The fixed inputs every kernel parity fixture and every parity test are built from.

Shared by `tests/kernels/test_parity_*.py` and `scripts/regenerate_kernel_fixtures.py`, so the
golden outputs and the assertions can never describe different inputs. The generators are seeded
`numpy.random.default_rng` streams, whose values numpy guarantees across releases.

Floats cross into JSON as `float.hex()` strings, because a bit-identical assertion needs a
round trip that loses nothing and `repr` of a float64 is only shortest-round-trip by convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import numpy

from plantgeo_ml_service.method.kernels import (
    NeighborSearchRequest,
    SeasonalBootstrapRequest,
    SeasonalFeatureRequest,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from numpy.typing import NDArray

#: One day index the whole neighbour-search case is anchored on: 2020-01-01 as a proleptic ordinal.
ANCHOR_DAY_INDEX: Final = 737_425

NEIGHBOR_ROWS: Final = 120
NEIGHBOR_FEATURES: Final = 6
NEIGHBOR_COUNT: Final = 8
NEIGHBOR_EXCLUSION_DAYS: Final = 30
NEIGHBOR_HORIZON_DAYS: Final = 14

#: The exact-tie case: `NEIGHBOR_TIE_DISTINCT` distinct rows, each repeated `NEIGHBOR_TIE_BLOCK`
#: times, so whole blocks of candidates carry bit-identical distances. `NEIGHBOR_TIE_COUNT` is
#: chosen to land INSIDE a block (5 of 12, blocks of 3), so the kernel must split a tie and the
#: tie-break rule -- lower candidate index first -- is what the fixture pins.
NEIGHBOR_TIE_DISTINCT: Final = 4
NEIGHBOR_TIE_BLOCK: Final = 3
NEIGHBOR_TIE_ROWS: Final = NEIGHBOR_TIE_DISTINCT * NEIGHBOR_TIE_BLOCK
NEIGHBOR_TIE_COUNT: Final = 5

#: The oversubscribed case: k is EVERY row while the two leakage windows leave only five
#: candidates eligible, so the kernel must truncate to the eligible count rather than pad with an
#: ineligible row, an uninitialised distance or its own "unreachable" sentinel.
NEIGHBOR_OVERSUBSCRIBED_ROWS: Final = 40
NEIGHBOR_OVERSUBSCRIBED_ELIGIBLE: Final = 5

BOOTSTRAP_SIMULATIONS: Final = 64
BOOTSTRAP_HORIZON: Final = 5
BOOTSTRAP_POOL_WIDTH: Final = 9

FEATURE_ROWS: Final = 24

#: The three published quantiles as probabilities, derived the way `numpy.percentile` derives them.
QUANTILE_PROBABILITIES: Final[NDArray[numpy.float64]] = numpy.true_divide(
    numpy.asarray([10.0, 50.0, 90.0], dtype=numpy.float64), 100.0
)


def encode_floats(values: Iterable[float]) -> list[str]:
    """Return float64 values as exact hexadecimal strings, for a lossless fixture round trip."""
    return [float(value).hex() for value in values]


def decode_floats(encoded: Iterable[str]) -> NDArray[numpy.float64]:
    """Return hexadecimal float strings as the float64 values they name, exactly."""
    return numpy.asarray([float.fromhex(value) for value in encoded], dtype=numpy.float64)


def neighbor_search_case() -> NeighborSearchRequest:
    """Return the fixed neighbour-search case: a dense archive with both leakage windows engaged."""
    generator = numpy.random.default_rng(20260919)
    candidates = generator.normal(size=(NEIGHBOR_ROWS, NEIGHBOR_FEATURES))
    query_day = ANCHOR_DAY_INDEX + NEIGHBOR_ROWS
    return NeighborSearchRequest(
        query_vector=generator.normal(size=NEIGHBOR_FEATURES),
        candidate_matrix=candidates,
        candidate_time_index=numpy.arange(NEIGHBOR_ROWS, dtype=numpy.int64) + ANCHOR_DAY_INDEX,
        feature_weights=generator.uniform(0.25, 2.0, size=NEIGHBOR_FEATURES),
        query_time_index=query_day,
        horizon_boundary_index=query_day - NEIGHBOR_HORIZON_DAYS,
        exclusion_days=NEIGHBOR_EXCLUSION_DAYS,
        neighbor_count=NEIGHBOR_COUNT,
    )


def neighbor_tie_case() -> NeighborSearchRequest:
    """Return a case whose candidates tie EXACTLY, so only the tie-break rule decides the order.

    Duplicate rows, not nearly-equal ones: the distance of row `i` and row `i + 1` is the same
    float64 down to the last bit, because the two rows ARE the same float64 values and both
    implementations accumulate them in the same feature order. A selection that preferred the
    later index, or that used `<=` where the reference's stable argsort uses `<`, shows up here
    and nowhere else.
    """
    generator = numpy.random.default_rng(20260921)
    distinct = generator.normal(size=(NEIGHBOR_TIE_DISTINCT, NEIGHBOR_FEATURES))
    query_day = ANCHOR_DAY_INDEX + 400
    return NeighborSearchRequest(
        query_vector=generator.normal(size=NEIGHBOR_FEATURES),
        candidate_matrix=numpy.repeat(distinct, NEIGHBOR_TIE_BLOCK, axis=0),
        candidate_time_index=numpy.arange(NEIGHBOR_TIE_ROWS, dtype=numpy.int64) + ANCHOR_DAY_INDEX,
        feature_weights=generator.uniform(0.25, 2.0, size=NEIGHBOR_FEATURES),
        query_time_index=query_day,
        # Both windows wide open: every row is eligible, so nothing but the tie-break is measured.
        horizon_boundary_index=query_day,
        exclusion_days=0,
        neighbor_count=NEIGHBOR_TIE_COUNT,
    )


def neighbor_oversubscribed_case() -> NeighborSearchRequest:
    """Return a case asking for every row while the leakage windows leave only five eligible.

    The horizon boundary admits day offsets 0..4 and the 30-day exclusion window admits 0..8, so
    their intersection is exactly `NEIGHBOR_OVERSUBSCRIBED_ELIGIBLE` rows while `neighbor_count`
    is all 40. The kernel must answer five neighbours, not forty.
    """
    generator = numpy.random.default_rng(20260922)
    query_day = ANCHOR_DAY_INDEX + NEIGHBOR_OVERSUBSCRIBED_ROWS - 1
    return NeighborSearchRequest(
        query_vector=generator.normal(size=NEIGHBOR_FEATURES),
        candidate_matrix=generator.normal(size=(NEIGHBOR_OVERSUBSCRIBED_ROWS, NEIGHBOR_FEATURES)),
        candidate_time_index=numpy.arange(NEIGHBOR_OVERSUBSCRIBED_ROWS, dtype=numpy.int64) + ANCHOR_DAY_INDEX,
        feature_weights=generator.uniform(0.25, 2.0, size=NEIGHBOR_FEATURES),
        query_time_index=query_day,
        horizon_boundary_index=ANCHOR_DAY_INDEX + NEIGHBOR_OVERSUBSCRIBED_ELIGIBLE,
        exclusion_days=NEIGHBOR_EXCLUSION_DAYS,
        neighbor_count=NEIGHBOR_OVERSUBSCRIBED_ROWS,
    )


def seasonal_bootstrap_case() -> SeasonalBootstrapRequest:
    """Return the fixed bootstrap case: scaled, offset and clipped, the additive-anomaly shape."""
    generator = numpy.random.default_rng(20260920)
    return SeasonalBootstrapRequest(
        draw_indices=generator.integers(
            0, BOOTSTRAP_POOL_WIDTH, size=(BOOTSTRAP_SIMULATIONS, BOOTSTRAP_HORIZON)
        ).astype(numpy.int64),
        innovation_pools=generator.normal(size=(BOOTSTRAP_HORIZON, BOOTSTRAP_POOL_WIDTH)),
        path_offsets=generator.normal(size=BOOTSTRAP_HORIZON),
        innovation_scales=generator.uniform(0.1, 1.0, size=BOOTSTRAP_HORIZON),
        lower_bound=-1.5,
        upper_bound=1.5,
        quantile_probabilities=QUANTILE_PROBABILITIES,
    )


def empirical_resample_case() -> SeasonalBootstrapRequest:
    """Return the precipitation shape: nothing added, nothing scaled, nothing clipped."""
    scaled = seasonal_bootstrap_case()
    return SeasonalBootstrapRequest(
        draw_indices=scaled.draw_indices,
        innovation_pools=numpy.abs(scaled.innovation_pools),
        path_offsets=numpy.zeros(BOOTSTRAP_HORIZON, dtype=numpy.float64),
        innovation_scales=numpy.ones(BOOTSTRAP_HORIZON, dtype=numpy.float64),
        lower_bound=-numpy.inf,
        upper_bound=numpy.inf,
        quantile_probabilities=QUANTILE_PROBABILITIES,
    )


def seasonal_feature_case() -> SeasonalFeatureRequest:
    """Return the fixed feature case, spanning a leap year, a common year and both polar caps."""
    ordinal_days = numpy.linspace(1, 366, FEATURE_ROWS).astype(numpy.int64)
    leap = numpy.arange(FEATURE_ROWS) % 2 == 0
    return SeasonalFeatureRequest(
        ordinal_days=ordinal_days,
        year_lengths=numpy.where(leap, 366.0, 365.0),
        latitudes=numpy.linspace(-89.5, 89.5, FEATURE_ROWS),
    )


__all__ = [
    "ANCHOR_DAY_INDEX",
    "BOOTSTRAP_HORIZON",
    "BOOTSTRAP_POOL_WIDTH",
    "BOOTSTRAP_SIMULATIONS",
    "FEATURE_ROWS",
    "NEIGHBOR_COUNT",
    "NEIGHBOR_FEATURES",
    "NEIGHBOR_OVERSUBSCRIBED_ELIGIBLE",
    "NEIGHBOR_OVERSUBSCRIBED_ROWS",
    "NEIGHBOR_ROWS",
    "NEIGHBOR_TIE_BLOCK",
    "NEIGHBOR_TIE_COUNT",
    "NEIGHBOR_TIE_DISTINCT",
    "NEIGHBOR_TIE_ROWS",
    "QUANTILE_PROBABILITIES",
    "decode_floats",
    "empirical_resample_case",
    "encode_floats",
    "neighbor_oversubscribed_case",
    "neighbor_search_case",
    "neighbor_tie_case",
    "seasonal_bootstrap_case",
    "seasonal_feature_case",
]
