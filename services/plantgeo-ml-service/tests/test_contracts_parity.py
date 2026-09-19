"""`foundation/contracts.py` copies two helpers from agri-data-service's `execution/contracts.py`.

Same two-assertion shape as `test_canonical_parity.py`: the fixture assertion runs in the image, the
live assertion runs in a checkout and is what notices the sibling moving.
"""

from __future__ import annotations

import json

import pytest
from parity_cases import (
    FIXTURE_DIRECTORY,
    SIBLING_PACKAGE_ROOT,
    evaluate_contracts,
    load_module_by_path,
)

from plantgeo_ml_service.foundation import contracts

FIXTURE_PATH = FIXTURE_DIRECTORY / "contracts.json"
SIBLING_MODULE_PATH = SIBLING_PACKAGE_ROOT / "execution" / "contracts.py"


def test_this_service_reproduces_the_golden_contract_outputs() -> None:
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert evaluate_contracts(contracts) == expected


@pytest.mark.skipif(not SIBLING_MODULE_PATH.is_file(), reason="agri-data-service source is not on disk")
def test_the_sibling_still_produces_the_golden_contract_outputs() -> None:
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sibling = evaluate_contracts(load_module_by_path("sibling_contracts", SIBLING_MODULE_PATH))

    assert sibling == expected, (
        "agri-data-service's execution/contracts.py no longer matches the parity fixture, so the two "
        "services would now serialize or refuse differently. Decide which side is right, port the "
        "change, then regenerate with "
        "`uv run --no-sync python scripts/regenerate_parity_fixtures.py`."
    )
