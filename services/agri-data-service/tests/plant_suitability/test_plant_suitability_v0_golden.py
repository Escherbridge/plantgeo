"""Flow: the batch build under V0_FROZEN serves exactly what the frozen v0 layer served for the fixture cells."""

from __future__ import annotations

from typing import TYPE_CHECKING

from polars.testing import assert_frame_equal

if TYPE_CHECKING:
    import polars as pl

    from tests.plant_suitability.support import SuitabilityFixture


def test_v0_frozen_rules_reproduce_every_frozen_guild_column_for_every_fixture_cell(
    suitability: SuitabilityFixture, v0_cells: pl.DataFrame
) -> None:
    expected = suitability.expected_v0.sort("cell_id")
    served = v0_cells.select(expected.columns).sort("cell_id")

    assert served["cell_id"].to_list() == expected["cell_id"].to_list()
    assert_frame_equal(served, expected)
