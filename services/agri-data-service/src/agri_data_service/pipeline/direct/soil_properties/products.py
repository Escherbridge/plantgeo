"""ISRIC SoilGrids v2.0 release identity, the thirty pinned source files, the lattice and the unit divisors.

Imports only `warehouse` and nothing that reads `LANE_REGISTRY`: `watermark.py` imports this module and
the registry imports `watermark.py`. See `pipeline/direct/soil_properties/AGENTS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from agri_data_service.warehouse.schemas.soil_properties import (
    SOIL_DEPTH_INTERVALS,
    SOIL_PROPERTY_CODES,
    soil_value_column,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

SOURCE_RELEASE: Final = "soilgrids-v2.0"
SOURCE_LICENCE: Final = "CC-BY 4.0"
SOURCE_CITATION: Final = "ISRIC SoilGrids v2.0 (Poggio et al. 2021, SOIL 7, 217-240), CC-BY 4.0"
SOURCE_BASE_URL: Final = "https://files.isric.org/soilgrids/latest/data"
SOURCE_RESOLUTION_M: Final = 250

#: The partition day and version stamp: the latest Last-Modified day across the thirty pins below.
RELEASE_DAY: Final = date(2020, 6, 2)
RELEASE_ID: Final = f"{SOURCE_RELEASE}/{RELEASE_DAY.isoformat()}"

#: Column depth suffix -> ISRIC's own depth spelling in file names and labels.
ISRIC_DEPTH_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {"0_5": "0-5cm", "5_15": "5-15cm", "15_30": "15-30cm"},
)

# --- The 0.005-degree origin lattice (CONTRACT C1) ----------------------------------------------
LATTICE_DEGREES: Final = 0.005
CELL_CENTRE_OFFSET_DEGREES: Final = LATTICE_DEGREES / 2
LATTICE_WEST: Final = -125.0
LATTICE_SOUTH: Final = 42.0
LATTICE_EAST: Final = -111.0
LATTICE_NORTH: Final = 49.0
LATTICE_COLUMNS: Final = 2_800
LATTICE_ROWS: Final = 1_400
LATTICE_CELL_COUNT: Final = LATTICE_COLUMNS * LATTICE_ROWS


@dataclass(frozen=True, slots=True)
class PropertyUnit:
    """How one ISRIC mapped integer converts to a physical value (`physical = mapped / divisor`)."""

    property_code: str
    mapped_unit: str
    divisor: int
    physical_unit: str
    output_key: str
    decimals: int


#: CONTRACT C1 unit table; checked against ISRIC's conversion-factor page in P0.2.
PROPERTY_UNITS: Final[Mapping[str, PropertyUnit]] = MappingProxyType(
    {
        unit.property_code: unit
        for unit in (
            PropertyUnit("phh2o", "pH x 10", 10, "pH (water)", "ph", 1),
            PropertyUnit("soc", "dg/kg", 10, "g/kg", "soc_g_kg", 1),
            PropertyUnit("nitrogen", "cg/kg", 100, "g/kg", "nitrogen_g_kg", 2),
            PropertyUnit("bdod", "cg/cm3", 100, "g/cm3", "bdod_g_cm3", 2),
            PropertyUnit("cec", "mmol(c)/kg", 10, "cmol(c)/kg", "cec_cmolc_kg", 1),
            PropertyUnit("ocd", "hg/m3", 10, "kg/m3", "ocd_kg_m3", 1),
            PropertyUnit("clay", "g/kg", 10, "% (mass)", "clay_pct", 1),
            PropertyUnit("sand", "g/kg", 10, "% (mass)", "sand_pct", 1),
            PropertyUnit("silt", "g/kg", 10, "% (mass)", "silt_pct", 1),
            PropertyUnit("cfvo", "cm3/dm3", 10, "vol %", "cfvo_vol_pct", 1),
        )
    },
)


@dataclass(frozen=True, slots=True)
class SourceFilePin:
    """One ISRIC mean VRT as a live HEAD answered it; any difference at capture time is drift."""

    property_code: str
    depth_interval: str
    last_modified: datetime
    etag: str
    content_length: int

    def __post_init__(self) -> None:
        """Refuse a pin for a column the lane does not carry, or a zone-less instant."""
        soil_value_column(self.property_code, self.depth_interval)
        if self.last_modified.tzinfo is None:
            raise ValueError(f"{self.file_name}: a Last-Modified pin must be timezone-aware")

    @property
    def file_name(self) -> str:
        """ISRIC's file name, e.g. `clay_15-30cm_mean.vrt`."""
        return f"{self.property_code}_{ISRIC_DEPTH_LABELS[self.depth_interval]}_mean.vrt"

    @property
    def vrt_url(self) -> str:
        """The public VRT this pin describes."""
        return f"{SOURCE_BASE_URL}/{self.property_code}/{self.file_name}"

    @property
    def value_column(self) -> str:
        """The lane column this file's values land in."""
        return soil_value_column(self.property_code, self.depth_interval)


def _pin(property_code: str, depth_interval: str, last_modified: str, etag: str, content_length: int) -> SourceFilePin:
    """Build one pin from the HEAD response's RFC 3339 UTC instant."""
    instant = datetime.strptime(last_modified, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return SourceFilePin(property_code, depth_interval, instant, etag, content_length)


#: Live HEAD of all thirty VRTs on 2026-09-27 (P0.1). 27 files are dated 2020-06-02; the three `ocd`
#: files are dated 2020-05-26. ETags are ISRIC's quoted strings, verbatim.
SOURCE_FILE_PINS: Final[tuple[SourceFilePin, ...]] = (
    _pin("phh2o", "0_5", "2020-06-02T15:38:07Z", '"10771316263-1591112287-3524139"', 3_524_139),
    _pin("phh2o", "5_15", "2020-06-02T15:37:33Z", '"10770185003-1591112253-3531831"', 3_531_831),
    _pin("phh2o", "15_30", "2020-06-02T15:34:09Z", '"10771316271-1591112049-3539523"', 3_539_523),
    _pin("soc", "0_5", "2020-06-02T15:49:25Z", '"10770287658-1591112965-5907288"', 5_907_288),
    _pin("soc", "5_15", "2020-06-02T15:50:45Z", '"10771502793-1591113045-5920094"', 5_920_094),
    _pin("soc", "15_30", "2020-06-02T15:49:32Z", '"10770287664-1591112972-5933045"', 5_933_045),
    _pin("nitrogen", "0_5", "2020-06-02T14:59:08Z", '"10770602304-1591109948-5978280"', 5_978_280),
    _pin("nitrogen", "5_15", "2020-06-02T14:56:36Z", '"10770287561-1591109796-5991143"', 5_991_143),
    _pin("nitrogen", "15_30", "2020-06-02T14:55:06Z", '"10771435865-1591109706-6004108"', 6_004_108),
    _pin("bdod", "0_5", "2020-06-02T15:21:08Z", '"10770602262-1591111268-3511543"', 3_511_543),
    _pin("bdod", "5_15", "2020-06-02T15:25:06Z", '"10770761103-1591111506-3519224"', 3_519_224),
    _pin("bdod", "15_30", "2020-06-02T15:26:00Z", '"10771075874-1591111560-3526905"', 3_526_905),
    _pin("cec", "0_5", "2020-06-02T15:18:53Z", '"10770896723-1591111133-5915517"', 5_915_517),
    _pin("cec", "5_15", "2020-06-02T15:17:05Z", '"10770602277-1591111025-5928487"', 5_928_487),
    _pin("cec", "15_30", "2020-06-02T15:12:20Z", '"10771502682-1591110740-5941457"', 5_941_457),
    _pin("ocd", "0_5", "2020-05-26T07:57:04Z", '"10771316212-1590479824-5901109"', 5_901_109),
    _pin("ocd", "5_15", "2020-05-26T07:57:25Z", '"10771435881-1590479845-5913942"', 5_913_942),
    _pin("ocd", "15_30", "2020-05-26T07:57:28Z", '"10770602326-1590479848-5926880"', 5_926_880),
    _pin("clay", "0_5", "2020-06-02T15:55:55Z", '"10770761153-1591113355-5929079"', 5_929_079),
    _pin("clay", "5_15", "2020-06-02T15:55:56Z", '"10771435853-1591113356-5941912"', 5_941_912),
    _pin("clay", "15_30", "2020-06-02T15:55:12Z", '"10770761158-1591113312-5954882"', 5_954_882),
    _pin("sand", "0_5", "2020-06-02T16:06:42Z", '"10770602359-1591114002-5928942"', 5_928_942),
    _pin("sand", "5_15", "2020-06-02T16:10:17Z", '"10752541679-1591114217-5941912"', 5_941_912),
    _pin("sand", "15_30", "2020-06-02T16:14:20Z", '"10771502758-1591114460-5954882"', 5_954_882),
    _pin("silt", "0_5", "2020-06-02T15:48:11Z", '"10770287630-1591112891-5928942"', 5_928_942),
    _pin("silt", "5_15", "2020-06-02T15:45:41Z", '"10771316333-1591112741-5941912"', 5_941_912),
    _pin("silt", "15_30", "2020-06-02T15:45:41Z", '"10770287638-1591112741-5954882"', 5_954_882),
    _pin("cfvo", "0_5", "2020-06-02T14:34:36Z", '"10771316120-1591108476-5927837"', 5_927_837),
    _pin("cfvo", "5_15", "2020-06-02T14:42:08Z", '"10770287527-1591108928-5939086"', 5_939_086),
    _pin("cfvo", "15_30", "2020-06-02T14:51:29Z", '"10770602285-1591109489-5952051"', 5_952_051),
)


def latest_pin() -> SourceFilePin:
    """Return the pin with the latest Last-Modified instant: the release's watermark."""
    return max(SOURCE_FILE_PINS, key=lambda pin: pin.last_modified)


def expected_pin_keys() -> frozenset[tuple[str, str]]:
    """Every (property, depth) pair the lane carries, so a missing or extra pin is a visible defect."""
    return frozenset((code, depth) for code in SOIL_PROPERTY_CODES for depth in SOIL_DEPTH_INTERVALS)


__all__ = [
    "CELL_CENTRE_OFFSET_DEGREES",
    "ISRIC_DEPTH_LABELS",
    "LATTICE_CELL_COUNT",
    "LATTICE_COLUMNS",
    "LATTICE_DEGREES",
    "LATTICE_EAST",
    "LATTICE_NORTH",
    "LATTICE_ROWS",
    "LATTICE_SOUTH",
    "LATTICE_WEST",
    "PROPERTY_UNITS",
    "RELEASE_DAY",
    "RELEASE_ID",
    "SOURCE_BASE_URL",
    "SOURCE_CITATION",
    "SOURCE_FILE_PINS",
    "SOURCE_LICENCE",
    "SOURCE_RELEASE",
    "SOURCE_RESOLUTION_M",
    "PropertyUnit",
    "SourceFilePin",
    "expected_pin_keys",
    "latest_pin",
]
