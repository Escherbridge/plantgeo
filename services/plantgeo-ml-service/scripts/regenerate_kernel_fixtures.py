"""Regenerate the checked-in golden outputs for the Mojo kernel parity harness.

The PYTHON REFERENCE is the definition of every kernel, so this script runs the reference and
nothing else: regenerating from a Mojo build would make the parity assertion circular. Run it only
when a kernel's published arithmetic changes on purpose, and say so in the review.

    uv run --no-sync python scripts/regenerate_kernel_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

SERVICE_ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))
sys.path.insert(0, str(SERVICE_ROOT / "tests" / "kernels"))

from kernel_cases import (  # noqa: E402 - the path above is what makes this importable
    empirical_resample_case,
    encode_floats,
    neighbor_oversubscribed_case,
    neighbor_search_case,
    neighbor_tie_case,
    seasonal_bootstrap_case,
    seasonal_feature_case,
)

from plantgeo_ml_service.method.kernels import reference  # noqa: E402 - same reason

if TYPE_CHECKING:
    from plantgeo_ml_service.method.kernels.bundles import NeighborSearchResult

FIXTURE_DIRECTORY: Final = SERVICE_ROOT / "tests" / "fixtures" / "kernels"


def _encode_search(result: NeighborSearchResult) -> dict[str, object]:
    """Return one neighbour-search answer as the fixture stores it: exact floats, plain indices."""
    return {
        "distances": encode_floats(result.distances),
        "candidate_indices": [int(index) for index in result.candidate_indices],
    }


def build_fixtures() -> dict[str, dict[str, object]]:
    """Return every golden output, keyed by the fixture file it belongs in."""
    search = reference.neighbor_search(neighbor_search_case())
    ties = reference.neighbor_search(neighbor_tie_case())
    oversubscribed = reference.neighbor_search(neighbor_oversubscribed_case())
    scaled = reference.seasonal_bootstrap(seasonal_bootstrap_case())
    resampled = reference.seasonal_bootstrap(empirical_resample_case())
    features = reference.seasonal_features(seasonal_feature_case())
    return {
        "knn_search": {
            **_encode_search(search),
            "exact_ties": _encode_search(ties),
            "oversubscribed": _encode_search(oversubscribed),
        },
        "seasonal_bootstrap": {
            "scaled_and_clipped": [encode_floats(row) for row in scaled],
            "empirical_resample": [encode_floats(row) for row in resampled],
        },
        "seasonal_features": {
            "day_of_year_sine": encode_floats(features.day_of_year_sine),
            "day_of_year_cosine": encode_floats(features.day_of_year_cosine),
            "photoperiod_seconds": encode_floats(features.photoperiod_seconds),
        },
    }


def main() -> int:
    """Write one fixture file per kernel and report what changed."""
    FIXTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, payload in build_fixtures().items():
        path = FIXTURE_DIRECTORY / f"{name}.json"
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        previous = path.read_text(encoding="utf-8") if path.is_file() else None
        path.write_text(rendered, encoding="utf-8")
        print(f"{'unchanged' if previous == rendered else 'written  '} {path.relative_to(SERVICE_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
