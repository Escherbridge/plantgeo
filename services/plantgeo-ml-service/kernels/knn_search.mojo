"""Weighted-L2 neighbour search with a two-sided exclusion mask and a stable top-k selection.

Bit-for-bit translation of `method/kernels/reference.py::neighbor_search`. The reference is the
definition; this file is judged against it by `tests/kernels/test_parity_knn_search.py`.

Six arguments, which is the cap on a Mojo function imported from Python (spec FR-9), so every
integer scalar and the per-candidate day index share ONE int64 bundle:

    bundle[0] rows, [1] features, [2] neighbour count, [3] query day index,
    [4] horizon boundary day index, [5] exclusion days, then one day index per candidate row.
"""

from std.math import sqrt
from std.os import abort
from std.python import PythonObject
from std.python.bindings import PythonModuleBuilder

from buffers import float64_buffer, int64_buffer

comptime HEADER_SLOTS = 6


@export
def PyInit_plantgeo_knn_search() abi("C") -> PythonObject:
    try:
        var builder = PythonModuleBuilder("plantgeo_knn_search")
        builder.def_function[search_neighbors](
            "search_neighbors",
            docstring="Weighted-L2 neighbour search; returns how many neighbours were written.",
        )
        builder.def_function[describe_bundle](
            "describe_bundle",
            docstring="Echo this kernel's view of the bundle header; returns HEADER_SLOTS.",
        )
        return builder.finalize()
    except error:
        abort(String("plantgeo_knn_search init failed: ", error))


def describe_bundle(bundle: PythonObject, out_header: PythonObject) raises -> PythonObject:
    """Echo the header this kernel reads, so Python can prove the two sides agree on the layout.

    `HEADER_SLOTS` is declared twice -- here and as `NEIGHBOR_SEARCH_HEADER_SLOTS` in
    `method/kernels/native.py` -- because an int cannot be shared across the Python boundary at
    compile time. If the two ever drift, the search does not crash: it reads a day index as a
    scalar, silently excludes the wrong candidates, and answers a plausible neighbour set. So the
    agreement is asserted directly instead: this writes back the six scalars AND the first
    per-candidate day index, read at the offset THIS file believes the header ends at, and returns
    its own slot count. `tests/kernels/test_kernel_dispatch.py` compares all eight against what the
    Python packer put there.
    """
    var bundle_values = int64_buffer(bundle)
    var header_output = int64_buffer(out_header)
    for slot in range(HEADER_SLOTS + 1):
        header_output[unsafe_offset=slot] = bundle_values[unsafe_offset=slot]
    return PythonObject(HEADER_SLOTS)


def search_neighbors(
    query: PythonObject,
    candidates: PythonObject,
    weights: PythonObject,
    bundle: PythonObject,
    out_distances: PythonObject,
    out_indices: PythonObject,
) raises -> PythonObject:
    """Write the nearest eligible analogs into the output arrays, nearest first."""
    var query_values = float64_buffer(query)
    var candidate_values = float64_buffer(candidates)
    var weight_values = float64_buffer(weights)
    var bundle_values = int64_buffer(bundle)
    var distance_output = float64_buffer(out_distances)
    var index_output = int64_buffer(out_indices)

    var rows = Int(bundle_values[unsafe_offset=0])
    var features = Int(bundle_values[unsafe_offset=1])
    var neighbor_count = Int(bundle_values[unsafe_offset=2])
    var query_day = bundle_values[unsafe_offset=3]
    var horizon_boundary_day = bundle_values[unsafe_offset=4]
    var exclusion_days = bundle_values[unsafe_offset=5]

    # `Float64.MAX` stands in for the reference's `numpy.inf`: it is the sentinel for "ineligible"
    # and for "already selected", and it is only ever compared, never returned.
    var unreachable = Float64.MAX
    var distances = List[Float64](capacity=rows)

    for row in range(rows):
        var candidate_day = bundle_values[unsafe_offset=HEADER_SLOTS + row]
        var separation = candidate_day - query_day
        if separation < 0:
            separation = -separation
        if candidate_day >= horizon_boundary_day or separation <= exclusion_days:
            distances.append(unreachable)
            continue
        var total = Float64(0.0)
        var base = row * features
        for feature in range(features):
            var weight = weight_values[unsafe_offset=feature]
            var difference = (
                candidate_values[unsafe_offset=base + feature] * weight
                - query_values[unsafe_offset=feature] * weight
            )
            total += difference * difference
        distances.append(sqrt(total))

    # A strictly-less comparison over increasing rows makes the first row win a tie, which is the
    # same order the reference's stable argsort produces.
    var selected = 0
    while selected < neighbor_count:
        var best_row = -1
        var best_distance = unreachable
        for row in range(rows):
            if distances[row] < best_distance:
                best_distance = distances[row]
                best_row = row
        if best_row < 0:
            break
        distance_output[unsafe_offset=selected] = best_distance
        index_output[unsafe_offset=selected] = Int64(best_row)
        distances[best_row] = unreachable
        selected += 1

    return PythonObject(selected)
