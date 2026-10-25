"""USDA CDL identities, pinned release dates and estimation support."""

from typing import Final

from agri_data_service.foundation.region import load_region
from agri_data_service.warehouse.crop_cover_releases import CDL_RELEASE_DATES as RELEASE_DAYS

__all__ = ["RELEASE_DAYS"]

CDL_SERVICE: Final = "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer"
METADATA_BASE: Final = "https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/"
NATIONAL_METADATA_FIRST_YEAR: Final = 2024
NATIVE_FINE_RESOLUTION_M: Final = 10
NATIVE_COARSE_RESOLUTION_M: Final = 30
GRID_METRES: Final = {0: 96000, 5: 48000, 9: 12000, 13: 3000}
BASE_CELL_METRES: Final = GRID_METRES[13]
TILE_METRES: Final = 120000
NO_DATA_CODES: Final = frozenset({0, 81, 255})
CROP_CODES: Final = frozenset((*range(1, 62), *range(66, 78), *range(204, 255)))


def capture_envelope() -> tuple[float, float, float, float]:
    """Read the admitted crop support from the currently selected region."""
    envelope = load_region().sub_envelopes["crop_cover"]
    return envelope.west, envelope.south, envelope.east, envelope.north


def native_resolution(year: int) -> int:
    """Read the annual product's original classified pixel resolution."""
    return NATIVE_FINE_RESOLUTION_M if year >= NATIONAL_METADATA_FIRST_YEAR else NATIVE_COARSE_RESOLUTION_M


def metadata_url(year: int) -> str:
    """Resolve the official annual metadata, including the pre-national state catalogue."""
    filename = (
        f"metadata_wa{year % 100:02d}.htm"
        if year < NATIONAL_METADATA_FIRST_YEAR
        else f"metadata_CDL{year % 100:02d}_FGDC-STD-001-1998.htm"
    )
    return METADATA_BASE + filename
