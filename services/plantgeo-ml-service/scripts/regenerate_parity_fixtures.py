"""Rewrite the cross-service parity fixtures from agri-data-service's own modules.

Run from the service root after a deliberate change to a shared helper on either side:
`uv run --no-sync python scripts/regenerate_parity_fixtures.py`. It needs the sibling's source on
disk, so it runs in a repository checkout and never inside the image. See `tests/AGENTS.md`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "tests"))

from parity_cases import (  # noqa: E402 - the tests directory has to be on the path first
    FIXTURE_DIRECTORY,
    SIBLING_PACKAGE_ROOT,
    evaluate_canonical,
    evaluate_contracts,
    load_module_by_path,
)

#: Fixture name -> (sibling module path, the function that evaluates the fixed case set).
FIXTURES = {
    "canonical": (SIBLING_PACKAGE_ROOT / "foundation" / "canonical.py", evaluate_canonical),
    "contracts": (SIBLING_PACKAGE_ROOT / "execution" / "contracts.py", evaluate_contracts),
}


def main() -> int:
    """Regenerate every parity fixture, or explain which sibling module could not be read."""
    FIXTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, (module_path, evaluate) in FIXTURES.items():
        if not module_path.is_file():
            print(f"sibling module absent, cannot regenerate {name}: {module_path}", file=sys.stderr)
            return 1
        outcomes = evaluate(load_module_by_path(f"sibling_{name}", module_path))
        destination = FIXTURE_DIRECTORY / f"{name}.json"
        destination.write_text(json.dumps(outcomes, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {destination.relative_to(SERVICE_ROOT)} from {module_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
