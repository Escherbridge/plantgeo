"""Rewrite the cross-service parity fixtures from agri-data-service's own modules.

Run from the service root after a deliberate change to a shared helper on either side:
`uv run --no-sync python scripts/regenerate_parity_fixtures.py`. It needs the sibling's source on
disk, so it runs in a repository checkout and never inside the image. See `tests/AGENTS.md`.

Regenerating FIRST turns a caught drift into an accepted one. Decide which side is right, port the
change, and only then run this.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "tests"))

from parity_cases import (  # noqa: E402 - the tests directory has to be on the path first
    FIXTURE_DIRECTORY,
    SIBLING_PACKAGE_ROOT,
    evaluate_canonical,
    evaluate_contracts,
    load_module_by_path,
)
from parity_parquet_adapters import (  # noqa: E402 - same reason
    ml_availability_adapter,
    sibling_availability_adapter,
    sibling_lanes_adapter,
    sibling_markers_adapter,
    sibling_paths_adapter,
    sibling_streams_adapter,
)
from parity_parquet_cases import (  # noqa: E402 - same reason
    SIBLING_SOURCE_ROOT,
    evaluate_availability,
    evaluate_availability_metadata,
    evaluate_lanes,
    evaluate_parquet_markers,
    evaluate_parquet_paths,
    evaluate_streams,
    sibling_source_is_present,
)

#: Phase-1 fixtures: one sibling FILE each, loadable by path because those helpers import nothing.
FILE_FIXTURES: dict[str, tuple[Path, Any]] = {
    "canonical": (SIBLING_PACKAGE_ROOT / "foundation" / "canonical.py", evaluate_canonical),
    "contracts": (SIBLING_PACKAGE_ROOT / "execution" / "contracts.py", evaluate_contracts),
}

#: Phase-2A fixtures: the sibling's real PACKAGE, because these modules import their own siblings.
ADAPTER_FIXTURES: dict[str, tuple[Any, Any]] = {
    "parquet_paths": (sibling_paths_adapter, evaluate_parquet_paths),
    "parquet_markers": (sibling_markers_adapter, evaluate_parquet_markers),
    "streams": (sibling_streams_adapter, evaluate_streams),
    "lanes": (sibling_lanes_adapter, evaluate_lanes),
    "availability": (sibling_availability_adapter, evaluate_availability),
}


def write_fixture(name: str, outcomes: Any) -> None:
    """Write one fixture with a pinned rendering, so a re-run on an unchanged tree changes nothing."""
    destination = FIXTURE_DIRECTORY / f"{name}.json"
    destination.write_text(json.dumps(outcomes, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {destination.relative_to(SERVICE_ROOT)}")


def main() -> int:
    """Regenerate every parity fixture, or explain which sibling module could not be read."""
    FIXTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, (module_path, evaluate) in FILE_FIXTURES.items():
        if not module_path.is_file():
            print(f"sibling module absent, cannot regenerate {name}: {module_path}", file=sys.stderr)
            return 1
        write_fixture(name, evaluate(load_module_by_path(f"sibling_{name}", module_path)))
    if not sibling_source_is_present():
        print(f"sibling package absent, cannot regenerate the Parquet fixtures: {SIBLING_SOURCE_ROOT}", file=sys.stderr)
        return 1
    for name, (build_adapter, evaluate) in ADAPTER_FIXTURES.items():
        write_fixture(name, evaluate(build_adapter()))
    # The one fixture with no sibling side. Its key set is parity-checked inside `availability.json`;
    # only the VALUE rendering is pinned here, because the sibling's formatter needs SQLAlchemy.
    write_fixture("availability_metadata", evaluate_availability_metadata(ml_availability_adapter()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
