"""Per-stream source pull and Parquet write (L3 `pipeline`).

One module per stream, named by its slug. A lane never imports another lane; shared needs move
down the lattice. See `conductor/code_styleguides/layer-lanes.md` §1.

EVERY LANE WRITES EXACTLY ONE RUNG OF THE ZOOM LADDER, AND `LANE_BASE_ZOOM_TIER` IS IT. A day
export reads the ungeneralized population out of Postgres, which is the most detailed rung there
is; the coarser rungs are DERIVED from those Parquet objects in Polars/DuckDB (RUNBOOK §0.32.2
decision 2), never from a second day-scoped query. So the tier is a module constant rather than an
argument of any `export_*`, for exactly the reason `kind="observed"` is one: a caller free to name
the tier could ask a lane to publish a generalization nobody computed, and no reader downstream
could tell that object apart from one that was really generalized.

It is DERIVED from the ladder's own top rather than written as the literal 13 -- the base is "the
tier nothing generalized", not a number -- so a rung added above z13 moves it. `gap_fill.py`'s
`GAP_FILL_ZOOM_TIER` derives the same value from the same tuple and deliberately does not import
this one: `gap_fill` imports `lane_registry`, which imports every lane, so the reverse edge would
close a cycle. The shared derivation is what keeps the two from drifting.

It sits in the package `__init__` because that is the only module here every lane may reach.
`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other` (correctly) refuses a
lane that imports a sibling MODULE, and `foundation/parquet/zoom.py` -- the layer it would
otherwise move down to -- owns the ladder itself rather than the write side's choice of rung.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.zoom import ZoomTier

LANE_BASE_ZOOM_TIER: Final[ZoomTier] = ZOOM_TIERS[-1]

__all__ = ["LANE_BASE_ZOOM_TIER"]
