"""The watersheds lane's identity, read from the registry rather than restated.

Only one product publishes here -- `WATERSHEDS_STREAM` -- so this module exists for the same reason
`pipeline/direct/drought/products.py` does: to hold the one constant `source.py`, `adapter.py`,
`forward.py` and `parity.py` all need, in a place a test can import without pulling in the network,
DuckDB or object-store machinery those modules carry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_STREAM

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration


def watersheds_lane_registration() -> LaneRegistration:
    """Read the registered watersheds lane fresh on every call -- never cached at import.

    `LANE_REGISTRY[WATERSHEDS_STREAM]` is the single place `history_floor` (2026-08-07) and
    `nature` (`static_lookup`) are declared and measured -- `pipeline/parquet/lane_registry.py`'s
    `WATERSHEDS_STREAM` registration `floor_basis` -- and re-declaring its numbers here would be a
    second copy free to drift from the one the census reads. `forward.py` replaces only this
    registration's `adapter` and `watermark` fields for its own call into `fill_one_lane_day`;
    every other field -- `history_floor`, `nature`, `publication_lag_days`, `floor_basis` -- passes
    through unchanged.

    SAFE TO IMPORT `LANE_REGISTRY` HERE, unlike in the evacuation-zones package: the registry
    imports `watersheds/watermark.py`, which reaches only `source.py` and `ingest/`, never this
    module. Nothing this file imports can therefore be reached from a half-initialised registry.
    """
    return LANE_REGISTRY[WATERSHEDS_STREAM]


__all__ = ["watersheds_lane_registration"]
