"""Benchmark the Mojo neighbour search against scikit-learn brute force on the 1,568-cell pilot.

Run in WSL2 or a linux-64 container: `pixi run bench-kernels`. It writes
`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase3-benchmark.md` and prints the same
numbers, so a reviewer reads the receipt rather than a claim.

TWO comparisons are reported because only one of them is the production question:

  * Per query, which is how `method/ml/analog_ensemble.find_analogs` is actually called: one query
    vector against the whole archive, once per cell. The scikit-learn column includes its `fit`,
    because the old code constructed and fitted a `NearestNeighbors` on every call.
  * Batched, where scikit-learn fits once and answers every query in a single BLAS-backed call.
    The kernel has no batch entry point, so this column is scikit-learn's best case against the
    kernel's ordinary one. It is reported because leaving it out would flatter the kernel.
"""

from __future__ import annotations

import os
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

SERVICE_ROOT: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

import numpy  # noqa: E402 - the path above is what makes the service package importable
from sklearn.neighbors import NearestNeighbors  # noqa: E402 - same reason

from plantgeo_ml_service.method.kernels import (  # noqa: E402 - same reason
    NeighborSearchRequest,
    kernels,
    reset_kernel_cache,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy.typing import NDArray

#: The fire-detections pilot: 1,568 cells, the covariates-v2 vector width, the AnEn neighbour count.
PILOT_CELLS: Final = 1_568
PILOT_FEATURES: Final = 20
PILOT_NEIGHBORS: Final = 20
PILOT_EXCLUSION_DAYS: Final = 30
PILOT_HORIZON_DAYS: Final = 30

#: How many timed repetitions each measurement takes, and how many queries one repetition runs.
REPETITIONS: Final = 5
QUERY_SAMPLE: Final = 100

EVIDENCE_PATH: Final = (
    SERVICE_ROOT.parents[1]
    / "conductor"
    / "tracks"
    / "plantgeo_ml_service_20260918"
    / "evidence"
    / "phase3-benchmark.md"
)


def build_archive() -> tuple[NDArray[numpy.float64], NDArray[numpy.float64], NDArray[numpy.int64]]:
    """Return the synthetic pilot archive: candidates, feature weights and day indices."""
    generator = numpy.random.default_rng(20260919)
    candidates = generator.normal(size=(PILOT_CELLS, PILOT_FEATURES))
    weights = generator.uniform(0.25, 2.0, size=PILOT_FEATURES)
    days = numpy.arange(PILOT_CELLS, dtype=numpy.int64) + 737_425
    return candidates, weights, days


def timed(operation: Callable[[], None]) -> float:
    """Return the best of `REPETITIONS` wall-clock seconds, which is the least noisy estimate."""
    measurements = []
    for _ in range(REPETITIONS):
        started = time.perf_counter()
        operation()
        measurements.append(time.perf_counter() - started)
    return min(measurements)


def main() -> int:
    """Measure both engines on both call patterns and write the evidence receipt."""
    candidates, weights, days = build_archive()
    generator = numpy.random.default_rng(4242)
    queries = generator.normal(size=(QUERY_SAMPLE, PILOT_FEATURES))
    query_day = int(days[-1]) + 1
    horizon_boundary = query_day - PILOT_HORIZON_DAYS
    eligible = (days < horizon_boundary) & (numpy.abs(days - query_day) > PILOT_EXCLUSION_DAYS)
    weighted_archive = candidates[eligible] * weights

    def request_for(index: int) -> NeighborSearchRequest:
        return NeighborSearchRequest(
            query_vector=queries[index],
            candidate_matrix=candidates,
            candidate_time_index=days,
            feature_weights=weights,
            query_time_index=query_day,
            horizon_boundary_index=horizon_boundary,
            exclusion_days=PILOT_EXCLUSION_DAYS,
            neighbor_count=PILOT_NEIGHBORS,
        )

    def sklearn_per_query() -> None:
        for index in range(QUERY_SAMPLE):
            neighbors = NearestNeighbors(n_neighbors=PILOT_NEIGHBORS, algorithm="brute", metric="euclidean")
            neighbors.fit(weighted_archive)
            neighbors.kneighbors((queries[index] * weights).reshape(1, -1))

    def sklearn_batched() -> None:
        neighbors = NearestNeighbors(n_neighbors=PILOT_NEIGHBORS, algorithm="brute", metric="euclidean")
        neighbors.fit(weighted_archive)
        neighbors.kneighbors(queries * weights)

    def backend_per_query(name: str) -> Callable[[], None]:
        reset_kernel_cache()
        backend = kernels({"PLANTGEO_ML_KERNELS": name})

        def run() -> None:
            for index in range(QUERY_SAMPLE):
                backend.neighbor_search(request_for(index))

        return run

    results = {
        "scikit-learn brute force, fitted per query": timed(sklearn_per_query),
        "scikit-learn brute force, fitted once and batched": timed(sklearn_batched),
        "kernels(python), per query": timed(backend_per_query("python")),
        "kernels(mojo), per query": timed(backend_per_query("mojo")),
    }

    lines = [
        "# Phase 3 benchmark: Mojo neighbour search against scikit-learn brute force",
        "",
        f"Measured {datetime.now(UTC).date().isoformat()} on {platform.platform()}, "
        f"{os.cpu_count()} logical processors, CPython {platform.python_version()}, "
        f"numpy {numpy.__version__}.",
        "",
        f"Synthetic pilot: {PILOT_CELLS} candidate rows x {PILOT_FEATURES} features, "
        f"k={PILOT_NEIGHBORS}, a {PILOT_EXCLUSION_DAYS}-day exclusion window and a "
        f"{PILOT_HORIZON_DAYS}-day horizon guard, {QUERY_SAMPLE} queries per repetition, "
        f"best of {REPETITIONS} repetitions.",
        "",
        "| engine | seconds per repetition | microseconds per query | versus fitted-per-query sklearn |",
        "|---|---:|---:|---:|",
    ]
    baseline = results["scikit-learn brute force, fitted per query"]
    for label, seconds in results.items():
        lines.append(
            f"| {label} | {seconds:.4f} | {seconds / QUERY_SAMPLE * 1e6:.1f} | {baseline / seconds:.2f}x |"
        )
    lines.extend(
        [
            "",
            "`kernels(mojo)` and `kernels(python)` return bit-identical distances and indices; the",
            "parity receipt is `phase3-parity.json`. The batched scikit-learn row is that library's",
            "best case and has no kernel equivalent, because `find_analogs` is called one query at a",
            "time; it is here so the comparison cannot be accused of choosing a weak baseline.",
            "",
            "Regenerate with `pixi run bench-kernels` from `services/plantgeo-ml-service` in WSL2.",
        ]
    )
    rendered = "\n".join(lines) + "\n"
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(rendered, encoding="utf-8")
    print(rendered)
    print(f"written {EVIDENCE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
