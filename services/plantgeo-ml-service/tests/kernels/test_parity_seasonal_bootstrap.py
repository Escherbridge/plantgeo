"""The seasonal bootstrap reproduces its golden quantiles, in both implementations.

Bit-identical on purpose: the draw stream crosses the boundary as data, so nothing random happens
inside either implementation and the only thing that could move an output is arithmetic.
"""

from __future__ import annotations

import numpy
import pytest
from kernel_cases import (
    BOOTSTRAP_HORIZON,
    QUANTILE_PROBABILITIES,
    decode_floats,
    empirical_resample_case,
    seasonal_bootstrap_case,
)
from kernel_harness import backends, build_backend, load_fixture

from plantgeo_ml_service.method.kernels import KernelBackend, SeasonalBootstrapRequest, kernels
from plantgeo_ml_service.method.kernels.bundles import KernelArgumentError


def _golden(fixture_key: str) -> numpy.ndarray:
    """Return one golden quantile matrix, shaped `(quantiles, horizon)`."""
    fixture = load_fixture("seasonal_bootstrap")
    return numpy.asarray([decode_floats(row) for row in fixture[fixture_key]], dtype=numpy.float64)


@pytest.mark.parametrize("backend_type", backends())
def test_the_scaled_and_clipped_ensemble_reproduces_its_quantiles(backend_type: type[KernelBackend]) -> None:
    """The additive-anomaly shape: offset, scaled and clipped to the series' physical bounds."""
    produced = build_backend(backend_type).seasonal_bootstrap(seasonal_bootstrap_case())

    assert numpy.array_equal(produced, _golden("scaled_and_clipped"))


@pytest.mark.parametrize("backend_type", backends())
def test_the_empirical_resample_reproduces_its_quantiles(backend_type: type[KernelBackend]) -> None:
    """The precipitation shape: nothing added, nothing scaled, nothing clipped."""
    produced = build_backend(backend_type).seasonal_bootstrap(empirical_resample_case())

    assert numpy.array_equal(produced, _golden("empirical_resample"))


@pytest.mark.parametrize("backend_type", backends())
def test_an_empirical_resample_only_ever_returns_values_the_record_produced(
    backend_type: type[KernelBackend],
) -> None:
    """A resampled quantile lies inside the pool's own range; a synthesised draw would escape it."""
    request = empirical_resample_case()

    produced = build_backend(backend_type).seasonal_bootstrap(request)

    assert float(produced.min()) >= float(request.innovation_pools.min())
    assert float(produced.max()) <= float(request.innovation_pools.max())


@pytest.mark.parametrize("backend_type", backends())
def test_a_single_member_ensemble_answers_that_member(backend_type: type[KernelBackend]) -> None:
    """One simulation is the degenerate case numpy handles by index wrap; the kernel must agree."""
    request = SeasonalBootstrapRequest(
        draw_indices=numpy.zeros((1, BOOTSTRAP_HORIZON), dtype=numpy.int64),
        innovation_pools=numpy.arange(BOOTSTRAP_HORIZON * 3, dtype=numpy.float64).reshape(BOOTSTRAP_HORIZON, 3),
        path_offsets=numpy.zeros(BOOTSTRAP_HORIZON),
        innovation_scales=numpy.ones(BOOTSTRAP_HORIZON),
        lower_bound=-numpy.inf,
        upper_bound=numpy.inf,
        quantile_probabilities=QUANTILE_PROBABILITIES,
    )

    produced = build_backend(backend_type).seasonal_bootstrap(request)

    expected = request.innovation_pools[:, 0]
    for quantile_row in produced:
        assert numpy.array_equal(quantile_row, expected)


def test_the_dispatch_under_python_mode_matches_the_fixture() -> None:
    """The default backend is the reference, so the shipped default is what the fixture pins."""
    produced = kernels({"PLANTGEO_ML_KERNELS": "python"}).seasonal_bootstrap(seasonal_bootstrap_case())

    assert numpy.array_equal(produced, _golden("scaled_and_clipped"))


def test_a_draw_index_outside_the_pool_is_refused_before_any_kernel_runs() -> None:
    """An out-of-range draw would read past the pool in a native kernel; the bundle refuses first."""
    scaled = seasonal_bootstrap_case()

    with pytest.raises(KernelArgumentError, match="pool width"):
        SeasonalBootstrapRequest(
            draw_indices=numpy.full_like(scaled.draw_indices, scaled.innovation_pools.shape[1]),
            innovation_pools=scaled.innovation_pools,
            path_offsets=scaled.path_offsets,
            innovation_scales=scaled.innovation_scales,
            lower_bound=scaled.lower_bound,
            upper_bound=scaled.upper_bound,
            quantile_probabilities=scaled.quantile_probabilities,
        )


def _bootstrap_with(
    *,
    innovation_pools: numpy.ndarray | None = None,
    path_offsets: numpy.ndarray | None = None,
    innovation_scales: numpy.ndarray | None = None,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
) -> SeasonalBootstrapRequest:
    """Return the scaled case with one field replaced, so a guard test names only what it poisons."""
    scaled = seasonal_bootstrap_case()
    return SeasonalBootstrapRequest(
        draw_indices=scaled.draw_indices,
        innovation_pools=scaled.innovation_pools if innovation_pools is None else innovation_pools,
        path_offsets=scaled.path_offsets if path_offsets is None else path_offsets,
        innovation_scales=scaled.innovation_scales if innovation_scales is None else innovation_scales,
        lower_bound=scaled.lower_bound if lower_bound is None else lower_bound,
        upper_bound=scaled.upper_bound if upper_bound is None else upper_bound,
        quantile_probabilities=scaled.quantile_probabilities,
    )


def test_a_non_finite_innovation_pool_is_refused_before_any_kernel_runs() -> None:
    """A NaN innovation propagates into every path that draws it and then into every quantile."""
    poisoned = seasonal_bootstrap_case().innovation_pools.copy()
    poisoned[0, 0] = numpy.nan

    with pytest.raises(KernelArgumentError, match=r"innovation_pools\[0\] must be a finite float"):
        _bootstrap_with(innovation_pools=poisoned)


def test_an_infinite_path_offset_is_refused() -> None:
    """An infinite offset survives clipping only when the bounds are infinite, and then poisons."""
    poisoned = seasonal_bootstrap_case().path_offsets.copy()
    poisoned[1] = numpy.inf

    with pytest.raises(KernelArgumentError, match=r"path_offsets\[1\]"):
        _bootstrap_with(path_offsets=poisoned)


def test_a_non_finite_innovation_scale_is_refused() -> None:
    """The scale multiplies a drawn innovation, so one NaN empties a whole horizon day."""
    poisoned = seasonal_bootstrap_case().innovation_scales.copy()
    poisoned[-1] = numpy.nan

    with pytest.raises(KernelArgumentError, match="innovation_scales"):
        _bootstrap_with(innovation_scales=poisoned)


def test_infinite_bounds_remain_legal_because_they_mean_do_not_clip() -> None:
    """The finite guard covers ARRAYS only: `-inf`/`+inf` bounds are the empirical-resample shape."""
    request = _bootstrap_with(lower_bound=-numpy.inf, upper_bound=numpy.inf)

    assert request.lower_bound == -numpy.inf
    assert request.upper_bound == numpy.inf
