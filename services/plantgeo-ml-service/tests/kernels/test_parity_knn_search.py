"""The weighted-L2 neighbour search reproduces its golden output, in both implementations.

Bit-identical, not approximate: the search is pure arithmetic over float64 with a fixed
accumulation order, so any difference between Mojo and numpy is a defect rather than rounding.
`scripts/build_kernels.sh` turns floating-point contraction OFF for exactly this reason.
"""

from __future__ import annotations

import numpy
import pytest
from kernel_cases import (
    NEIGHBOR_OVERSUBSCRIBED_ELIGIBLE,
    NEIGHBOR_TIE_COUNT,
    decode_floats,
    neighbor_oversubscribed_case,
    neighbor_search_case,
    neighbor_tie_case,
)
from kernel_harness import backends, build_backend, load_fixture

from plantgeo_ml_service.method.kernels import KernelBackend, NeighborSearchRequest, kernels
from plantgeo_ml_service.method.kernels.bundles import KernelArgumentError


@pytest.mark.parametrize("backend_type", backends())
def test_the_search_reproduces_the_golden_distances_bit_for_bit(backend_type: type[KernelBackend]) -> None:
    """Every implementation returns the fixture's distances with no tolerance at all."""
    fixture = load_fixture("knn_search")

    found = build_backend(backend_type).neighbor_search(neighbor_search_case())

    assert numpy.array_equal(found.distances, decode_floats(fixture["distances"]))


@pytest.mark.parametrize("backend_type", backends())
def test_the_search_selects_the_same_candidates_in_the_same_order(backend_type: type[KernelBackend]) -> None:
    """Ties break toward the lower candidate index, so the ORDER is part of the contract."""
    fixture = load_fixture("knn_search")

    found = build_backend(backend_type).neighbor_search(neighbor_search_case())

    assert found.candidate_indices.tolist() == fixture["candidate_indices"]


@pytest.mark.parametrize("backend_type", backends())
def test_the_search_never_returns_a_candidate_the_leakage_windows_excluded(
    backend_type: type[KernelBackend],
) -> None:
    """The exclusion mask is the kernel's job, not the caller's; an excluded row must not appear."""
    request = neighbor_search_case()

    found = build_backend(backend_type).neighbor_search(request)

    assert bool(request.eligible_mask[found.candidate_indices].all())


@pytest.mark.parametrize("backend_type", backends())
def test_the_search_returns_distances_in_ascending_order(backend_type: type[KernelBackend]) -> None:
    """Nearest first is what every caller reads; a selection sort that drifted would show here."""
    found = build_backend(backend_type).neighbor_search(neighbor_search_case())

    assert numpy.all(numpy.diff(found.distances) >= 0.0)


def test_the_dispatch_under_python_mode_matches_the_reference() -> None:
    """The default backend is the reference, so the shipped default is what the fixture pins."""
    fixture = load_fixture("knn_search")

    found = kernels({"PLANTGEO_ML_KERNELS": "python"}).neighbor_search(neighbor_search_case())

    assert numpy.array_equal(found.distances, decode_floats(fixture["distances"]))


@pytest.mark.parametrize("backend_type", backends())
def test_exactly_tied_candidates_break_toward_the_lower_index(backend_type: type[KernelBackend]) -> None:
    """Duplicate rows tie to the last bit, so the ORDER is decided by the tie-break rule alone."""
    golden = load_fixture("knn_search")["exact_ties"]

    found = build_backend(backend_type).neighbor_search(neighbor_tie_case())

    assert numpy.array_equal(found.distances, decode_floats(golden["distances"]))
    assert found.candidate_indices.tolist() == golden["candidate_indices"]


@pytest.mark.parametrize("backend_type", backends())
def test_a_tie_that_is_split_takes_the_earlier_block_member(backend_type: type[KernelBackend]) -> None:
    """k lands inside a block of equal candidates, so a `<=` comparison would show up right here."""
    found = build_backend(backend_type).neighbor_search(neighbor_tie_case())

    assert found.candidate_indices.size == NEIGHBOR_TIE_COUNT
    for position in range(1, found.candidate_indices.size):
        tied = found.distances[position] == found.distances[position - 1]
        assert not tied or found.candidate_indices[position] > found.candidate_indices[position - 1]


@pytest.mark.parametrize("backend_type", backends())
def test_more_neighbours_than_eligible_candidates_truncates(backend_type: type[KernelBackend]) -> None:
    """k is every row and only five survive the leakage windows; padding would leak an excluded row."""
    golden = load_fixture("knn_search")["oversubscribed"]
    request = neighbor_oversubscribed_case()

    found = build_backend(backend_type).neighbor_search(request)

    assert found.candidate_indices.size == NEIGHBOR_OVERSUBSCRIBED_ELIGIBLE
    assert found.distances.size == NEIGHBOR_OVERSUBSCRIBED_ELIGIBLE
    assert numpy.array_equal(found.distances, decode_floats(golden["distances"]))
    assert found.candidate_indices.tolist() == golden["candidate_indices"]
    assert bool(request.eligible_mask[found.candidate_indices].all())
    assert int(request.eligible_mask.sum()) == NEIGHBOR_OVERSUBSCRIBED_ELIGIBLE


def test_a_non_finite_query_vector_is_refused_before_any_kernel_runs() -> None:
    """A NaN is never less than the running best, so the Mojo search would silently skip it."""
    case = neighbor_search_case()
    poisoned = case.query_vector.copy()
    poisoned[0] = numpy.nan

    with pytest.raises(KernelArgumentError, match=r"query_vector\[0\] must be a finite float"):
        NeighborSearchRequest(
            query_vector=poisoned,
            candidate_matrix=case.candidate_matrix,
            candidate_time_index=case.candidate_time_index,
            feature_weights=case.feature_weights,
            query_time_index=case.query_time_index,
            horizon_boundary_index=case.horizon_boundary_index,
            exclusion_days=case.exclusion_days,
            neighbor_count=case.neighbor_count,
        )


def test_an_infinite_candidate_is_refused_by_row_and_column() -> None:
    """An infinity is indistinguishable from the kernel's own "ineligible" sentinel."""
    case = neighbor_search_case()
    poisoned = case.candidate_matrix.copy()
    poisoned[2, 1] = numpy.inf

    with pytest.raises(KernelArgumentError, match=r"candidate_matrix\[13\] must be a finite float"):
        NeighborSearchRequest(
            query_vector=case.query_vector,
            candidate_matrix=poisoned,
            candidate_time_index=case.candidate_time_index,
            feature_weights=case.feature_weights,
            query_time_index=case.query_time_index,
            horizon_boundary_index=case.horizon_boundary_index,
            exclusion_days=case.exclusion_days,
            neighbor_count=case.neighbor_count,
        )


def test_non_finite_feature_weights_are_refused() -> None:
    """A weight is multiplied into every feature, so one infinity poisons every distance."""
    case = neighbor_search_case()
    poisoned = case.feature_weights.copy()
    poisoned[-1] = -numpy.inf

    with pytest.raises(KernelArgumentError, match="feature_weights"):
        NeighborSearchRequest(
            query_vector=case.query_vector,
            candidate_matrix=case.candidate_matrix,
            candidate_time_index=case.candidate_time_index,
            feature_weights=poisoned,
            query_time_index=case.query_time_index,
            horizon_boundary_index=case.horizon_boundary_index,
            exclusion_days=case.exclusion_days,
            neighbor_count=case.neighbor_count,
        )
