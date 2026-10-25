"""Shim: moved to `pipeline/lanes/crop_cover/products.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.crop_cover.products import (
    BASE_CELL_METRES,
    CDL_SERVICE,
    CROP_CODES,
    GRID_METRES,
    METADATA_BASE,
    NATIONAL_METADATA_FIRST_YEAR,
    NATIVE_COARSE_RESOLUTION_M,
    NATIVE_FINE_RESOLUTION_M,
    NO_DATA_CODES,
    RELEASE_DAYS,
    TILE_METRES,
    capture_envelope,
    metadata_url,
    native_resolution,
)

__all__ = [
    "BASE_CELL_METRES",
    "CDL_SERVICE",
    "CROP_CODES",
    "GRID_METRES",
    "METADATA_BASE",
    "NATIONAL_METADATA_FIRST_YEAR",
    "NATIVE_COARSE_RESOLUTION_M",
    "NATIVE_FINE_RESOLUTION_M",
    "NO_DATA_CODES",
    "RELEASE_DAYS",
    "TILE_METRES",
    "capture_envelope",
    "metadata_url",
    "native_resolution",
]
