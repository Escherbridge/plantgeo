"""Seasonal bootstrap ensemble and its linear-interpolated quantiles, over a caller-supplied draws.

Bit-for-bit translation of `method/kernels/reference.py::seasonal_bootstrap`. NO random number
generator is ported: `draw_indices` is whatever numpy's seeded PCG64 already emitted, so the
recorded `random_seed` stays the one reproduction handle (spec FR-9).

The quantile arithmetic reproduces `numpy.quantile(..., method="linear")` step for step, including
its virtual index `(n - 1) * q`, its out-of-range index clamp, and its two-branch
interpolation that switches association at `gamma >= 0.5`. A simpler `a + (b - a) * gamma` would
agree to about one ulp and fail the bit-identical parity assertion.

Six arguments, the cap on a Mojo function imported from Python:
    draw_indices (simulations x horizon), pools (horizon x pool width), path_terms (offsets then
    scales, each horizon long), shape (simulations, horizon, pool width, quantile count),
    limits (lower bound, upper bound, then one probability per quantile), and the output buffer
    (quantile count x horizon).
"""

from std.math import floor
from std.os import abort
from std.python import PythonObject
from std.python.bindings import PythonModuleBuilder

from buffers import float64_buffer, int64_buffer


@export
def PyInit_plantgeo_seasonal_bootstrap() abi("C") -> PythonObject:
    try:
        var builder = PythonModuleBuilder("plantgeo_seasonal_bootstrap")
        builder.def_function[bootstrap_quantiles](
            "bootstrap_quantiles",
            docstring="Bootstrap ensemble quantiles from a caller-supplied draw stream.",
        )
        return builder.finalize()
    except error:
        abort(String("plantgeo_seasonal_bootstrap init failed: ", error))


def bootstrap_quantiles(
    draw_indices: PythonObject,
    pools: PythonObject,
    path_terms: PythonObject,
    shape: PythonObject,
    limits: PythonObject,
    out_quantiles: PythonObject,
) raises -> PythonObject:
    """Write one quantile per requested probability per horizon day into the output buffer."""
    var draws = int64_buffer(draw_indices)
    var pool_values = float64_buffer(pools)
    var terms = float64_buffer(path_terms)
    var shape_values = int64_buffer(shape)
    var limit_values = float64_buffer(limits)
    var output = float64_buffer(out_quantiles)

    var simulation_count = Int(shape_values[unsafe_offset=0])
    var horizon_days = Int(shape_values[unsafe_offset=1])
    var pool_width = Int(shape_values[unsafe_offset=2])
    var quantile_count = Int(shape_values[unsafe_offset=3])
    var lower_bound = limit_values[unsafe_offset=0]
    var upper_bound = limit_values[unsafe_offset=1]

    var column = List[Float64](capacity=simulation_count)

    for horizon in range(horizon_days):
        var offset = terms[unsafe_offset=horizon]
        var scale = terms[unsafe_offset=horizon_days + horizon]
        var pool_base = horizon * pool_width
        column.clear()
        for simulation in range(simulation_count):
            var draw = Int(draws[unsafe_offset=simulation * horizon_days + horizon])
            var value = offset + scale * pool_values[unsafe_offset=pool_base + draw]
            if value < lower_bound:
                value = lower_bound
            if value > upper_bound:
                value = upper_bound
            column.append(value)
        sort_ascending(column)
        for quantile in range(quantile_count):
            output[unsafe_offset=quantile * horizon_days + horizon] = interpolated_quantile(
                column, limit_values[unsafe_offset=2 + quantile], simulation_count
            )

    return PythonObject(None)


def sift_down(mut values: List[Float64], start: Int, end: Int):
    """Restore the max-heap property at `start` over `values[0:end]`."""
    var root = start
    while True:
        var child = 2 * root + 1
        if child >= end:
            return
        if child + 1 < end and values[child] < values[child + 1]:
            child += 1
        if not values[root] < values[child]:
            return
        var held = values[root]
        values[root] = values[child]
        values[child] = held
        root = child


def sort_ascending(mut values: List[Float64]):
    """Sort one column of draws in place, ascending.

    Heapsort rather than the stdlib: Mojo 1.0's stdlib exposes no `sort` this kernel could import,
    and the choice of algorithm is invisible to the caller because equal float64 values are
    indistinguishable, so any correct sort yields the byte-identical array numpy's partition does.
    """
    var count = len(values)
    var parent = count // 2 - 1
    while parent >= 0:
        sift_down(values, parent, count)
        parent -= 1
    var end = count - 1
    while end > 0:
        var held = values[0]
        values[0] = values[end]
        values[end] = held
        sift_down(values, 0, end)
        end -= 1


def interpolated_quantile(
    sorted_values: List[Float64], probability: Float64, value_count: Int
) -> Float64:
    """Return one linear-interpolated quantile of an ascending column, numpy's way."""
    if value_count == 1:
        return sorted_values[0]
    var count = Float64(value_count)
    # `(n - 1) * q`, NOT the Hyndman and Fan `n*q + (alpha + q*(1 - alpha - beta)) - 1` that
    # numpy's own `_compute_virtual_index` spells out: numpy's `linear` entry deliberately takes
    # the first form "to avoid some rounding issues", and the two disagree in the last bit. Taking
    # the textbook form here cost 4.4e-16 on the p10 row, measured 2026-09-19.
    var virtual_index = (count - 1.0) * probability
    var previous_index = 0
    var next_index = 1
    if virtual_index >= count - 1.0:
        previous_index = value_count - 2
        next_index = value_count - 1
    elif virtual_index >= 0.0:
        previous_index = Int(floor(virtual_index))
        next_index = previous_index + 1
    var gamma = virtual_index - Float64(previous_index)
    var low_value = sorted_values[previous_index]
    var high_value = sorted_values[next_index]
    var span = high_value - low_value
    if gamma >= 0.5:
        return high_value - span * (1.0 - gamma)
    return low_value + span * gamma
