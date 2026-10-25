"""Shim: moved to `pipeline/lanes/evacuation_zones/source.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.evacuation_zones.source import (
    EvacuationZonesSource,
    EvacuationZonesSourceError,
    EvacuationZonesTruncatedError,
    fetch_evacuation_zones_snapshot,
)

__all__ = [
    "EvacuationZonesSource",
    "EvacuationZonesSourceError",
    "EvacuationZonesTruncatedError",
    "fetch_evacuation_zones_snapshot",
]
