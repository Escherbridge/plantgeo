"""The fire-perimeters lane's identity, partition kind and rung ladder, read from the registry.

Holds the constants `rows.py`, `watermark.py`, `adapter.py`, `forward.py` and `parity.py` all need,
in one place a test can import without pulling the whole driver -- the same reason
`drought/products.py`, `climate/products.py` and `soil/products.py` exist.

There is no cadence helper and no release-week walk here, unlike `drought/products.py`. A
`static_lookup` lane has no calendar to step over: its day comes from a source watermark, which is
`watermark.py`'s job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_STREAM

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`. There is
#: no `kind=forecast` sibling for this lane and there may not be one: a `static_lookup` may not name
#: a forecaster at all (`foundation/parquet/lane_contract.py`'s `nature_permits_forecast`), which is
#: why `warehouse/schemas/fire_perimeters.py` records `horizon: none` as structural rather than
#: declared.
FIRE_PERIMETERS_DIRECT_KIND: Final = "observed"

#: Every rung one version must hold before it counts as published. The base rung is written by
#: `adapter.py`; z9/z5/z0 are derived from it by the shared finalizer inside `fill_one_lane_day`.
FIRE_PERIMETERS_DIRECT_ALL_TIERS: Final[tuple[ZoomTier, ...]] = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)


def fire_perimeters_lane_registration() -> LaneRegistration:
    """Read the registered fire-perimeters lane fresh on every call -- never cached at import.

    `LANE_REGISTRY[FIRE_PERIMETERS_STREAM]` is the single place `nature` (`static_lookup`),
    `history_floor` (2025-07-28, and INERT for this nature) and `publication_lag_days` (0) are
    declared and cited -- `pipeline/parquet/lane_registry.py`, the `FIRE_PERIMETERS_STREAM`
    registration's `floor_basis`. This module may not edit that file, and re-declaring its numbers
    here would be a second copy free to drift from the one the generic gap-fill driver still reads.

    `forward.py` substitutes BOTH this registration's `adapter` and its `watermark` before handing it
    to `fill_one_lane_day`: the registered pair reads PostgreSQL, and the whole point of this package
    is that it does not.
    """
    return LANE_REGISTRY[FIRE_PERIMETERS_STREAM]


__all__ = [
    "FIRE_PERIMETERS_DIRECT_ALL_TIERS",
    "FIRE_PERIMETERS_DIRECT_KIND",
    "fire_perimeters_lane_registration",
]
