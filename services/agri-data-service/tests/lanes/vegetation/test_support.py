"""`support.py`: the committed `agri.spatial_cell` snapshot, checked against the region's own lattice.

`support_cells.json` is committed (`pipeline/lanes/vegetation/AGENTS.md` "Support"; 1,568 rows). This
is the regression that would catch a silent drift between the pinned snapshot and the region
manifest it must agree with -- the same check `plan_requests` runs on every real turn.
"""

from __future__ import annotations

from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.lanes.vegetation.support import (
    NDVI_SUPPORT_CELL_COUNT,
    load_pinned_support,
    require_lattice_agreement,
)


def test_the_committed_support_agrees_with_the_region_s_analysis_0p25_lattice() -> None:
    support = load_pinned_support()

    assert len(support.cells) == NDVI_SUPPORT_CELL_COUNT
    require_lattice_agreement(support, load_region("pnw").analysis_lattices["analysis-0p25"])
