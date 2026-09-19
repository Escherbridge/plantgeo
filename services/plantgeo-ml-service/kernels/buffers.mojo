"""Reading a numpy array's raw memory, once, for every kernel in this directory.

Mojo 1.0 has no first-party numpy view, so a buffer crosses the boundary as the integer address
`numpy.ndarray.ctypes.data` reports. Every caller in `method/kernels/native.py` passes
`numpy.ascontiguousarray` output of the declared dtype, so the address is the first element of a
C-contiguous block and the kernel may index it linearly. Rationale lives in
`src/plantgeo_ml_service/method/kernels/AGENTS.md`.
"""

from std.memory import Pointer
from std.python import PythonObject

comptime Float64Buffer = Pointer[Float64, MutUnsafeAnyOrigin]
comptime Int64Buffer = Pointer[Int64, MutUnsafeAnyOrigin]


def float64_buffer(array: PythonObject) raises -> Float64Buffer:
    """Return one contiguous float64 numpy array as a raw buffer."""
    return Float64Buffer(unsafe_from_address=Int(py=array.ctypes.data))


def int64_buffer(array: PythonObject) raises -> Int64Buffer:
    """Return one contiguous int64 numpy array as a raw buffer."""
    return Int64Buffer(unsafe_from_address=Int(py=array.ctypes.data))
