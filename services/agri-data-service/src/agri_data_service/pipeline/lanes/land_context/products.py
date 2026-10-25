"""Pinned public endpoints and bounded PNW snapshot ownership."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

HISTORY_FLOOR: Final = date(2026, 9, 20)
ARCHIVE_ROOT: Final = "layer=land-context-boundaries/kind=observed/availability/blm-source"
STATES: Final = frozenset({"OR", "WA", "ID"})
SHA256_HEX_LENGTH: Final = 64


@dataclass(frozen=True, slots=True)
class ArcgisProduct:
    """A reviewed endpoint, exact filter, and source identity field."""

    slug: str
    endpoint: str
    where: str
    key_field: str
    envelope: str | None = None


SURFACE_ORWA = ArcgisProduct(
    "blm-orwa-ownership",
    "https://gis.blm.gov/orarcgis/rest/services/Land_Status/BLM_OR_Ownership/MapServer/0",
    "PROPERTY_STATUS = 'BLM'",
    "GLOBALID",
)
SURFACE_IDAHO = ArcgisProduct(
    "blm-national-sma-idaho",
    "https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer/1",
    "ADMIN_AGENCY_CODE = 'BLM'",
    "OBJECTID",
    "-117.25,41.98,-111.04,49.01",
)
FIELD_OFFICES = ArcgisProduct(
    "blm-field-offices",
    "https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer/3",
    "ADMIN_ST IN ('OR', 'WA', 'ID') AND BLM_ORG_TYPE = 'Field'",
    "OBJECTID",
)
CENSUS_STATES = ArcgisProduct(
    "census-state-context",
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0",
    "STUSAB IN ('OR', 'WA', 'ID')",
    "STUSAB",
)
PRODUCTS: Final = (SURFACE_ORWA, SURFACE_IDAHO, FIELD_OFFICES, CENSUS_STATES)
