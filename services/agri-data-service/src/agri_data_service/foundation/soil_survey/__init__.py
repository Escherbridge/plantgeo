"""SSURGO capture and candidate receipts: pure pydantic models, no I/O. See `AGENTS.md`.

Layer L0 (`foundation`), a ruled exception like `foundation/region/` -- see `receipts.py`'s
docstring and `foundation/AGENTS.md`'s "Ruled exception: `foundation/soil_survey/`". `release.py`
(the `Part`/`Candidate`/`ShardRef`/`Release` side) is added in a later slice and re-exported here
alongside `receipts.py` once it lands.
"""

from __future__ import annotations

from agri_data_service.foundation.soil_survey.receipts import (
    MAX_AREA_DELINEATIONS,
    MAX_CAPTURE_PAGES,
    MAX_CAPTURE_SECONDS,
    MAX_NATIVE_KEY_DIGITS,
    MAX_OBJECT_BYTES,
    MAX_PAGE_ROWS,
    ROOT,
    SHA256,
    WGS84_MAX_LATITUDE,
    WGS84_MAX_LONGITUDE,
    AreaCapture,
    AreaCensusEntry,
    AreaInventory,
    AreaSummary,
    AreaSymbol,
    Blob,
    CapturePage,
    FrozenModel,
    SoilSurveyError,
    digest,
    encoded,
    require_area,
    verify_blob,
)

__all__ = [
    "MAX_AREA_DELINEATIONS",
    "MAX_CAPTURE_PAGES",
    "MAX_CAPTURE_SECONDS",
    "MAX_NATIVE_KEY_DIGITS",
    "MAX_OBJECT_BYTES",
    "MAX_PAGE_ROWS",
    "ROOT",
    "SHA256",
    "WGS84_MAX_LATITUDE",
    "WGS84_MAX_LONGITUDE",
    "AreaCapture",
    "AreaCensusEntry",
    "AreaInventory",
    "AreaSummary",
    "AreaSymbol",
    "Blob",
    "CapturePage",
    "FrozenModel",
    "SoilSurveyError",
    "digest",
    "encoded",
    "require_area",
    "verify_blob",
]
