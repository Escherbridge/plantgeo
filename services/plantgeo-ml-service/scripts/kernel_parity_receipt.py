"""Write the phase 3 parity receipt: what the two kernel implementations actually agreed on.

Runs in WSL2 or a linux-64 container, where both backends exist, and writes
`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase3-parity.json`. The Docker
`mojo-kernels-enabled` stage runs it with `--probe-only`, so the image refuses to ship a build
that disagrees with the reference.

    pixi run parity-receipt
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

SERVICE_ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))
sys.path.insert(0, str(SERVICE_ROOT / "tests" / "kernels"))

import numpy  # noqa: E402 - the paths above are what make these importable
from kernel_cases import (  # noqa: E402 - same reason
    empirical_resample_case,
    neighbor_oversubscribed_case,
    neighbor_search_case,
    neighbor_tie_case,
    seasonal_bootstrap_case,
    seasonal_feature_case,
)

from plantgeo_ml_service.method.kernels import MojoKernels, PythonKernels  # noqa: E402 - same reason

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from plantgeo_ml_service.method.kernels import NeighborSearchRequest

EVIDENCE_PATH: Final = (
    SERVICE_ROOT.parents[1]
    / "conductor"
    / "tracks"
    / "plantgeo_ml_service_20260918"
    / "evidence"
    / "phase3-parity.json"
)

#: Sine and cosine are bounded by one, so an absolute tolerance is meaningful for them. The
#: photoperiod is tens of thousands of seconds, where one ulp is 7.3e-12, so it is pinned
#: relatively. Matches `tests/kernels/test_parity_seasonal_features.py`.
CYCLICAL_TOLERANCE: Final = 1e-12
PHOTOPERIOD_RELATIVE_TOLERANCE: Final = 1e-12


def _deviation(left: NDArray[numpy.float64], right: NDArray[numpy.float64]) -> dict[str, Any]:
    """Return how far apart two float64 arrays are, absolutely and relatively."""
    difference = numpy.abs(left - right)
    scale = numpy.maximum(numpy.abs(left), numpy.finfo(numpy.float64).tiny)
    return {
        "bit_identical": bool(numpy.array_equal(left, right)),
        "max_absolute_deviation": float(difference.max()),
        "max_relative_deviation": float((difference / scale).max()),
    }


def _compare_search(
    python_backend: PythonKernels,
    mojo_backend: MojoKernels,
    case: NeighborSearchRequest,
) -> dict[str, Any]:
    """Return one neighbour-search comparison: the distances, the order, and how many were selected."""
    from_python = python_backend.neighbor_search(case)
    from_mojo = mojo_backend.neighbor_search(case)
    return {
        "requirement": "bit_identical",
        "distances": _deviation(from_python.distances, from_mojo.distances),
        "indices_identical": bool(numpy.array_equal(from_python.candidate_indices, from_mojo.candidate_indices)),
        "selected_count": int(from_python.candidate_indices.size),
        "selected_count_identical": bool(from_python.candidate_indices.size == from_mojo.candidate_indices.size),
    }


def compare() -> dict[str, Any]:
    """Return one comparison entry per kernel, between the Python reference and the Mojo build."""
    python_backend = PythonKernels()
    mojo_backend = MojoKernels()

    search_case = neighbor_search_case()
    python_search = python_backend.neighbor_search(search_case)
    mojo_search = mojo_backend.neighbor_search(search_case)

    feature_case = seasonal_feature_case()
    python_features = python_backend.seasonal_features(feature_case)
    mojo_features = mojo_backend.seasonal_features(feature_case)

    return {
        "knn_search": {
            "requirement": "bit_identical",
            "distances": _deviation(python_search.distances, mojo_search.distances),
            "indices_identical": bool(
                numpy.array_equal(python_search.candidate_indices, mojo_search.candidate_indices)
            ),
            "selected_count": int(python_search.candidate_indices.size),
        },
        # The two edge shapes a bit-identical claim is worth nothing without: exact ties, where
        # only the tie-break rule decides the ORDER, and k larger than the eligible count, where
        # the kernel must truncate rather than pad with its own "unreachable" sentinel.
        "knn_search_exact_ties": _compare_search(python_backend, mojo_backend, neighbor_tie_case()),
        "knn_search_oversubscribed": _compare_search(python_backend, mojo_backend, neighbor_oversubscribed_case()),
        "seasonal_bootstrap": {
            "requirement": "bit_identical",
            "scaled_and_clipped": _deviation(
                python_backend.seasonal_bootstrap(seasonal_bootstrap_case()),
                mojo_backend.seasonal_bootstrap(seasonal_bootstrap_case()),
            ),
            "empirical_resample": _deviation(
                python_backend.seasonal_bootstrap(empirical_resample_case()),
                mojo_backend.seasonal_bootstrap(empirical_resample_case()),
            ),
        },
        "seasonal_features": {
            "requirement": (
                f"day_of_year_sine and day_of_year_cosine within {CYCLICAL_TOLERANCE} absolute; "
                f"photoperiod_seconds within {PHOTOPERIOD_RELATIVE_TOLERANCE} relative"
            ),
            "day_of_year_sine": _deviation(python_features.day_of_year_sine, mojo_features.day_of_year_sine),
            "day_of_year_cosine": _deviation(python_features.day_of_year_cosine, mojo_features.day_of_year_cosine),
            "photoperiod_seconds": _deviation(python_features.photoperiod_seconds, mojo_features.photoperiod_seconds),
        },
    }


def verdicts(comparison: dict[str, Any]) -> list[str]:
    """Return every way the Mojo build failed its declared requirement, empty when it passed."""
    search = comparison["knn_search"]
    bootstrap = comparison["seasonal_bootstrap"]
    features = comparison["seasonal_features"]
    edge_cases = {name: comparison[name] for name in ("knn_search_exact_ties", "knn_search_oversubscribed")}
    return [
        *([] if search["distances"]["bit_identical"] else ["knn_search distances are not bit-identical"]),
        *([] if search["indices_identical"] else ["knn_search selected different candidates"]),
        *[
            f"{name} {complaint}"
            for name, entry in edge_cases.items()
            for complaint, held in (
                ("distances are not bit-identical", entry["distances"]["bit_identical"]),
                ("selected different candidates", entry["indices_identical"]),
                ("selected a different number of candidates", entry["selected_count_identical"]),
            )
            if not held
        ],
        *[
            f"seasonal_bootstrap {case} is not bit-identical"
            for case in ("scaled_and_clipped", "empirical_resample")
            if not bootstrap[case]["bit_identical"]
        ],
        *[
            f"seasonal_features {cyclical} exceeded {CYCLICAL_TOLERANCE} absolute"
            for cyclical in ("day_of_year_sine", "day_of_year_cosine")
            if features[cyclical]["max_absolute_deviation"] > CYCLICAL_TOLERANCE
        ],
        *(
            []
            if features["photoperiod_seconds"]["max_relative_deviation"] <= PHOTOPERIOD_RELATIVE_TOLERANCE
            else [f"seasonal_features photoperiod_seconds exceeded {PHOTOPERIOD_RELATIVE_TOLERANCE} relative"]
        ),
    ]


def mojo_version() -> str:
    """Return the compiler version string, or why it could not be read."""
    try:
        completed = subprocess.run(["mojo", "--version"], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return f"unavailable: {error}"
    return completed.stdout.strip() or f"unavailable: exit {completed.returncode}"


def main() -> int:
    """Compare the two implementations, print the verdict, and write the receipt unless probing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="check parity and exit without writing the evidence file (the Docker build gate)",
    )
    arguments = parser.parse_args()

    comparison = compare()
    failures = verdicts(comparison)
    receipt = {
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "numpy_version": numpy.__version__,
        "mojo_version": mojo_version(),
        "verdict": "PASS" if not failures else "FAIL",
        "failures": failures,
        "kernels": comparison,
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    if not arguments.probe_only:
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"written {EVIDENCE_PATH}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
