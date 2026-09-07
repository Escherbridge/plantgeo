"""The registered evacuation-zones lane, read fresh from LANE_REGISTRY. The package's ONE edge to it.

SPLIT OUT OF `products.py` ON 2026-09-06, AND THE SPLIT IS THE POINT. `pipeline/parquet/lane_registry.py`
now imports this package -- `watermark.py` is the body of the registered `_evacuation_zones_watermark`
resolver -- and `watermark.py` needs `rows.py`, which needs `products.py`. While `products.py` held
this function, that chain closed a cycle straight back into a half-initialised `lane_registry` module
and `LANE_REGISTRY` would not yet exist to import. Keeping the one lane-registry-facing function in
its own module leaves `products.py` free of any edge to the registry, so every other module in this
package can be imported FROM the registry.

Nothing here is cached at import for the same reason the function is not: the registry is the single
place this lane's `history_floor` (2025-04-14) and `nature` (`static_lookup`) are declared and cited,
and a second copy in this package would be free to drift from the one the driver reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_STREAM

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration


def evacuation_zones_lane_registration() -> LaneRegistration:
    """Read the registered evacuation-zones lane fresh on every call -- never cached at import.

    `forward.py` replaces only this registration's `adapter` and `watermark` fields before its own
    call into `fill_one_lane_day`, so it writes through the SAME floor, nature, lag and cited
    `floor_basis` the census reads. Since the 2026-09-06 swap both registered fields are already
    source-direct -- a refusal naming this package, and `watermark.py` -- so the substitution now
    exists only to reuse the capture this turn already holds instead of taking a second one.
    """
    return LANE_REGISTRY[EVACUATION_ZONES_STREAM]


__all__ = ["evacuation_zones_lane_registration"]
