"""Cycle-closed day-of-year pair and FAO-56 photoperiod, one entry per row.

Translation of `method/kernels/reference.py::seasonal_features`. Unlike the other two kernels this
one is asserted within a tolerance rather than bit-for-bit: `sin`, `cos`, `tan` and `acos` are libm
calls on the Python side and Mojo stdlib calls here, and the two are free to disagree in the last
bit. The parity harness pins sine and cosine to atol 1e-12 and the photoperiod to rtol 1e-12,
because one ulp of a ~43,200 second day is already 7.3e-12 seconds.

Five arguments, inside the six-argument cap on a Mojo function imported from Python:
    ordinal days, year lengths, latitudes, shape (row count), and one output buffer holding the
    sine block, the cosine block and the photoperiod block back to back.
"""

from std.math import acos, cos, sin, tan
from std.os import abort
from std.python import PythonObject
from std.python.bindings import PythonModuleBuilder

from buffers import float64_buffer, int64_buffer

comptime PI = 3.141592653589793
comptime TWO_PI = 2.0 * PI
comptime DEGREES_TO_RADIANS = PI / 180.0
comptime DAYS_PER_COMMON_YEAR = 365.0
comptime SOLAR_DECLINATION_AMPLITUDE = 0.409
comptime SOLAR_DECLINATION_PHASE = 1.39
comptime HOURS_PER_DAY = 24.0
comptime SECONDS_PER_HOUR = 3600.0


@export
def PyInit_plantgeo_seasonal_features() abi("C") -> PythonObject:
    try:
        var builder = PythonModuleBuilder("plantgeo_seasonal_features")
        builder.def_function[seasonal_features](
            "seasonal_features",
            docstring="Cyclical day-of-year sine and cosine, and FAO-56 photoperiod seconds.",
        )
        return builder.finalize()
    except error:
        abort(String("plantgeo_seasonal_features init failed: ", error))


def seasonal_features(
    ordinal_days: PythonObject,
    year_lengths: PythonObject,
    latitudes: PythonObject,
    shape: PythonObject,
    out_features: PythonObject,
) raises -> PythonObject:
    """Write the sine, cosine and photoperiod blocks into the output buffer, in row order."""
    var days = int64_buffer(ordinal_days)
    var lengths = float64_buffer(year_lengths)
    var latitude_values = float64_buffer(latitudes)
    var shape_values = int64_buffer(shape)
    var output = float64_buffer(out_features)

    var row_count = Int(shape_values[unsafe_offset=0])

    for row in range(row_count):
        var ordinal_day = Float64(Int(days[unsafe_offset=row]))
        var angle = TWO_PI * (ordinal_day - 1.0) / lengths[unsafe_offset=row]
        var declination = SOLAR_DECLINATION_AMPLITUDE * sin(
            TWO_PI * ordinal_day / DAYS_PER_COMMON_YEAR - SOLAR_DECLINATION_PHASE
        )
        var cosine_argument = -tan(latitude_values[unsafe_offset=row] * DEGREES_TO_RADIANS) * tan(
            declination
        )
        if cosine_argument < -1.0:
            cosine_argument = -1.0
        if cosine_argument > 1.0:
            cosine_argument = 1.0
        var sunset_hour_angle = acos(cosine_argument)
        output[unsafe_offset=row] = sin(angle)
        output[unsafe_offset=row_count + row] = cos(angle)
        output[unsafe_offset=2 * row_count + row] = (
            HOURS_PER_DAY / PI * sunset_hour_angle * SECONDS_PER_HOUR
        )

    return PythonObject(None)
