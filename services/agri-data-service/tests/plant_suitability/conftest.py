"""Session fixtures: the generated inputs and the batch output under each preset."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agri_data_service.warehouse.plant_suitability.config import V0_FROZEN
from tests.plant_suitability.support import PRODUCTION_ON_FIXTURE, load_fixture

if TYPE_CHECKING:
    import polars as pl

    from tests.plant_suitability.support import SuitabilityFixture


@pytest.fixture(scope="session")
def suitability() -> SuitabilityFixture:
    """The fixture tables and role manifest."""
    return load_fixture()


@pytest.fixture(scope="session")
def v0_cells(suitability: SuitabilityFixture) -> pl.DataFrame:
    """The batch output for every fixture cell under the frozen v0 rules."""
    return suitability.evaluate(V0_FROZEN)


@pytest.fixture(scope="session")
def production_cells(suitability: SuitabilityFixture) -> pl.DataFrame:
    """The batch output for every fixture cell under the production rules."""
    return suitability.evaluate(PRODUCTION_ON_FIXTURE)
