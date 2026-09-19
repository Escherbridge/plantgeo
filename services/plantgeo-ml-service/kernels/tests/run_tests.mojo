"""Mojo-side unit tests for the pieces of the kernels that carry their own arithmetic.

Mojo 1.0 removed the `mojo test` subcommand, so this is a plain executable: `pixi run test-kernels`
builds and runs it, and a failed expectation raises, which exits non-zero. The cross-language parity
assertions live in `tests/kernels/test_parity_*.py`; these cover the sort and the quantile
interpolation on inputs whose answers are known by hand, so a failure here names the broken piece
instead of reporting "the ensemble moved".
"""

from seasonal_bootstrap import interpolated_quantile, sort_ascending

comptime TOLERANCE = 1e-12


def expect_equal(observed: Float64, expected: Float64, description: String) raises:
    """Raise unless one observed value matches its expectation within the test tolerance."""
    if abs(observed - expected) > TOLERANCE:
        raise Error(String(description, ": expected ", expected, ", observed ", observed))
    print("ok -", description)


def test_sort_ascending_orders_a_shuffled_column() raises:
    var values: List[Float64] = [3.5, -1.0, 0.0, 2.25, -7.5]
    sort_ascending(values)
    expect_equal(values[0], -7.5, "sorted column starts at its minimum")
    expect_equal(values[2], 0.0, "sorted column keeps its middle value")
    expect_equal(values[4], 3.5, "sorted column ends at its maximum")


def test_sort_ascending_keeps_duplicates() raises:
    var values: List[Float64] = [2.0, 1.0, 2.0, 1.0]
    sort_ascending(values)
    expect_equal(values[0], 1.0, "duplicates survive the sort, low pair")
    expect_equal(values[1], 1.0, "duplicates survive the sort, low pair again")
    expect_equal(values[3], 2.0, "duplicates survive the sort, high pair")


def test_a_single_member_ensemble_is_its_own_quantile() raises:
    var values: List[Float64] = [4.25]
    expect_equal(interpolated_quantile(values, 0.1, 1), 4.25, "one member answers p10")
    expect_equal(interpolated_quantile(values, 0.9, 1), 4.25, "one member answers p90")


def test_the_extreme_probabilities_take_the_end_values() raises:
    var values: List[Float64] = [0.0, 1.0, 2.0, 3.0, 4.0]
    expect_equal(interpolated_quantile(values, 0.0, 5), 0.0, "p0 is the minimum")
    expect_equal(interpolated_quantile(values, 1.0, 5), 4.0, "p100 is the maximum")


def test_the_median_of_an_even_column_interpolates() raises:
    var values: List[Float64] = [0.0, 1.0, 2.0, 3.0]
    expect_equal(interpolated_quantile(values, 0.5, 4), 1.5, "median of an even column")


def test_a_quantile_below_the_midpoint_uses_the_low_association() raises:
    # gamma is 0.2 here, so the `gamma < 0.5` branch runs: low + span * gamma = 10 + 10 * 0.2.
    var values: List[Float64] = [10.0, 20.0, 30.0]
    expect_equal(interpolated_quantile(values, 0.1, 3), 12.0, "p10 of a three-member column")


def main() raises:
    test_sort_ascending_orders_a_shuffled_column()
    test_sort_ascending_keeps_duplicates()
    test_a_single_member_ensemble_is_its_own_quantile()
    test_the_extreme_probabilities_take_the_end_values()
    test_the_median_of_an_even_column_interpolates()
    test_a_quantile_below_the_midpoint_uses_the_low_association()
    print("all kernel unit tests passed")
