"""Pure logic: the infraspecific merge fills the kept row's null traits from members in one fixed donor order."""

from __future__ import annotations

import itertools

import polars as pl
import pytest

from agri_data_service.warehouse.plant_suitability.pools import collapse_infraspecific

KEPT_PLANT_ID, FIRST_DONOR_MIN_TEMP_C, FIRST_NON_NULL_PH_MAX = 30, -30.0, 8.0
RANGE_COLUMNS = ("precip_min_mm", "precip_max_mm", "precip_min_in", "precip_max_in", "ph_min", "ph_max")
# The kept row (named by a source, the species row) lacks both traits; two unnamed, in-state infraspecific members
# tie on every kept-row key, so plant_id alone orders them as donors (review p16: no fixture binomial exercises this).
MEMBERS = (
    {"plant_id": 30, "display_name": "Poa secunda", "named_by_source": True, "min_temp_c": None, "ph_max": None},
    {"plant_id": 20, "display_name": "Poa secunda var. elongata", "named_by_source": False, "min_temp_c": -20.0,
     "ph_max": 8.0},
    {"plant_id": 10, "display_name": "Poa secunda ssp. juncifolia", "named_by_source": False, "min_temp_c": -30.0,
     "ph_max": None},
)  # fmt: skip


def pool(members: tuple[dict[str, object], ...]) -> pl.DataFrame:
    """A one-binomial pool of the members, in the given row order."""
    rows = [
        {
            **member,
            "plant_binomial": "poa secunda",
            "present_in_state": True,
            "applicability_rows": [f"row {member['plant_id']}"],
            "fire_resistant_values": [],
            **{column: member.get(column) for column in RANGE_COLUMNS},
        }
        for member in members
    ]
    schema = {
        "plant_id": pl.Int64, "display_name": pl.String, "named_by_source": pl.Boolean, "min_temp_c": pl.Float64,
        "plant_binomial": pl.String, "present_in_state": pl.Boolean, "applicability_rows": pl.List(pl.String),
        "fire_resistant_values": pl.List(pl.String), **dict.fromkeys(RANGE_COLUMNS, pl.Float64),
    }  # fmt: skip
    return pl.DataFrame(rows, schema=schema)


ROW_ORDERS = list(itertools.permutations(MEMBERS))


@pytest.mark.parametrize(
    "order", ROW_ORDERS, ids=["-".join(str(member["plant_id"]) for member in order) for order in ROW_ORDERS]
)
def test_the_kept_row_takes_each_null_trait_from_the_first_donor_by_plant_id_whatever_the_row_order(
    order: tuple[dict[str, object], ...],
) -> None:
    merged = collapse_infraspecific(pool(order)).row(0, named=True)

    assert merged["plant_id"] == KEPT_PLANT_ID
    assert merged["min_temp_c"] == FIRST_DONOR_MIN_TEMP_C
    assert merged["ph_max"] == FIRST_NON_NULL_PH_MAX
    assert merged["applicability_rows"] == ["row 30", "row 10", "row 20"]
