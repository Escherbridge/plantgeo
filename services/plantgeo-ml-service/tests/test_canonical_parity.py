"""`foundation/canonical.py` is a copy of agri-data-service's; this proves it has not drifted.

Two assertions, deliberately: the fixture one runs everywhere including the Docker image, where the
sibling's source is absent, and the live one runs only in a repository checkout and is what catches
a change made on the sibling's side. A fixture that only ever compared against itself would pass
forever while the two services diverged.
"""

from __future__ import annotations

import json

import pytest
from parity_cases import (
    FIXTURE_DIRECTORY,
    SIBLING_PACKAGE_ROOT,
    evaluate_canonical,
    load_module_by_path,
)

from plantgeo_ml_service.foundation import canonical

FIXTURE_PATH = FIXTURE_DIRECTORY / "canonical.json"
SIBLING_MODULE_PATH = SIBLING_PACKAGE_ROOT / "foundation" / "canonical.py"


def test_this_service_reproduces_the_golden_canonical_outputs() -> None:
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert evaluate_canonical(canonical) == expected


@pytest.mark.skipif(not SIBLING_MODULE_PATH.is_file(), reason="agri-data-service source is not on disk")
def test_the_sibling_still_produces_the_golden_canonical_outputs() -> None:
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sibling = evaluate_canonical(load_module_by_path("sibling_canonical", SIBLING_MODULE_PATH))

    assert sibling == expected, (
        "agri-data-service's foundation/canonical.py no longer matches the parity fixture, so the "
        "two services would now write different checksums for the same document. Decide which side "
        "is right, port the change, then regenerate with "
        "`uv run --no-sync python scripts/regenerate_parity_fixtures.py`."
    )
