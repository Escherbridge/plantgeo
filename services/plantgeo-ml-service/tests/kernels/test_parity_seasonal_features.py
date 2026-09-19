"""The cyclical and photoperiod kernel reproduces its golden output, in both implementations.

The only kernel asserted within a tolerance rather than bit for bit: `sin`, `cos`, `tan` and
`acos` are libm on the Python side and Mojo stdlib here, and the two are free to disagree in the
last bit. Sine and cosine are bounded by one so an absolute tolerance is meaningful for them; the
photoperiod is tens of thousands of seconds, where one ulp is already 7.3e-12, so it carries a
RELATIVE tolerance instead. Measured drift on 2026-09-19: sine and cosine bit-identical,
photoperiod 1.5e-11 absolute and 2.4e-16 relative.

This file also proves the kernel against `pipeline/fire_risk_plane`'s two scalar helpers, which is
what keeps the kernel's own copy of the FAO-56 constants from drifting: `method/kernels` may import
`foundation` only, so the two copies cannot be one shared import.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy
import pytest
from kernel_cases import decode_floats, seasonal_feature_case
from kernel_harness import backends, build_backend, load_fixture

from plantgeo_ml_service.method.kernels import KernelBackend, SeasonalFeatureRequest, kernels
from plantgeo_ml_service.method.kernels.bundles import KernelArgumentError
from plantgeo_ml_service.pipeline.fire_risk_plane import cyclical_day_of_year, photoperiod_seconds

#: Sine and cosine live in [-1, 1], so an absolute tolerance says what it sounds like.
CYCLICAL_TOLERANCE = 1e-12

#: One ulp of a 43,200 second day is 7.3e-12, so the photoperiod is pinned relatively instead.
PHOTOPERIOD_RELATIVE_TOLERANCE = 1e-12


@pytest.mark.parametrize("backend_type", backends())
def test_the_cyclical_pair_reproduces_its_golden_output(backend_type: type[KernelBackend]) -> None:
    """The day-of-year sine and cosine match the fixture to 1e-12 absolute."""
    fixture = load_fixture("seasonal_features")

    produced = build_backend(backend_type).seasonal_features(seasonal_feature_case())

    assert numpy.allclose(
        produced.day_of_year_sine, decode_floats(fixture["day_of_year_sine"]), rtol=0.0, atol=CYCLICAL_TOLERANCE
    )
    assert numpy.allclose(
        produced.day_of_year_cosine, decode_floats(fixture["day_of_year_cosine"]), rtol=0.0, atol=CYCLICAL_TOLERANCE
    )


@pytest.mark.parametrize("backend_type", backends())
def test_the_photoperiod_reproduces_its_golden_output(backend_type: type[KernelBackend]) -> None:
    """The photoperiod matches the fixture to 1e-12 relative, which is tighter than one ulp."""
    fixture = load_fixture("seasonal_features")

    produced = build_backend(backend_type).seasonal_features(seasonal_feature_case())

    assert numpy.allclose(
        produced.photoperiod_seconds,
        decode_floats(fixture["photoperiod_seconds"]),
        rtol=PHOTOPERIOD_RELATIVE_TOLERANCE,
        atol=0.0,
    )


@pytest.mark.parametrize("backend_type", backends())
def test_the_kernel_agrees_with_the_published_scalar_helpers(backend_type: type[KernelBackend]) -> None:
    """The batch kernel and the scalar definition are two copies; this is what keeps them one."""
    days = tuple(date(2024, 1, 1) + timedelta(days=step * 37) for step in range(10))
    latitudes = numpy.linspace(-60.0, 60.0, len(days))
    request = SeasonalFeatureRequest(
        ordinal_days=numpy.asarray([day.timetuple().tm_yday for day in days], dtype=numpy.int64),
        year_lengths=numpy.full(len(days), 366.0),
        latitudes=latitudes,
    )

    produced = build_backend(backend_type).seasonal_features(request)

    for index, day in enumerate(days):
        expected_sine, expected_cosine = cyclical_day_of_year(day)
        assert produced.day_of_year_sine[index] == pytest.approx(expected_sine, abs=CYCLICAL_TOLERANCE)
        assert produced.day_of_year_cosine[index] == pytest.approx(expected_cosine, abs=CYCLICAL_TOLERANCE)
        assert produced.photoperiod_seconds[index] == pytest.approx(
            photoperiod_seconds(float(latitudes[index]), day), rel=PHOTOPERIOD_RELATIVE_TOLERANCE
        )


@pytest.mark.parametrize("backend_type", backends())
def test_a_polar_day_answers_a_full_day_rather_than_a_domain_error(backend_type: type[KernelBackend]) -> None:
    """The cosine argument is clipped, so the Arctic midsummer is 24 hours, not a NaN."""
    request = SeasonalFeatureRequest(
        ordinal_days=numpy.asarray([172], dtype=numpy.int64),
        year_lengths=numpy.asarray([366.0]),
        latitudes=numpy.asarray([89.0]),
    )

    produced = build_backend(backend_type).seasonal_features(request)

    assert float(produced.photoperiod_seconds[0]) == pytest.approx(86_400.0, abs=1e-6)


def test_a_non_finite_latitude_is_refused_before_any_kernel_runs() -> None:
    """`tan(NaN)` is NaN and never raises, so a NaN latitude answers a plausible NaN photoperiod."""
    with pytest.raises(KernelArgumentError, match=r"latitudes\[1\] must be a finite float"):
        SeasonalFeatureRequest(
            ordinal_days=numpy.asarray([1, 2], dtype=numpy.int64),
            year_lengths=numpy.asarray([365.0, 365.0]),
            latitudes=numpy.asarray([45.0, numpy.nan]),
        )


def test_an_infinite_year_length_is_refused() -> None:
    """An infinite year length divides the cycle angle to zero rather than failing the positivity check."""
    with pytest.raises(KernelArgumentError, match="year_lengths"):
        SeasonalFeatureRequest(
            ordinal_days=numpy.asarray([1], dtype=numpy.int64),
            year_lengths=numpy.asarray([numpy.inf]),
            latitudes=numpy.asarray([45.0]),
        )


def test_the_dispatch_under_python_mode_matches_the_fixture() -> None:
    """The default backend is the reference, so the shipped default is what the fixture pins."""
    fixture = load_fixture("seasonal_features")

    produced = kernels({"PLANTGEO_ML_KERNELS": "python"}).seasonal_features(seasonal_feature_case())

    assert numpy.allclose(
        produced.photoperiod_seconds,
        decode_floats(fixture["photoperiod_seconds"]),
        rtol=PHOTOPERIOD_RELATIVE_TOLERANCE,
        atol=0.0,
    )
