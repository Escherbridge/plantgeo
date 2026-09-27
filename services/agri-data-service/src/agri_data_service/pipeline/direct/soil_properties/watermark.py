"""The soil-properties version clock: the latest pinned ISRIC Last-Modified instant. Pure; reads nothing.

`pipeline/parquet/lane_registry.py` imports this module, so it reaches only `foundation` and this
package's `products.py`. See `pipeline/direct/soil_properties/AGENTS.md`, "Watermark and drift".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.pipeline.direct.soil_properties.products import (
    RELEASE_DAY,
    RELEASE_ID,
    SOURCE_FILE_PINS,
    latest_pin,
)

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


class SoilPropertiesWatermarkError(ValueError):
    """Raised when the pinned release contradicts itself or the day it is asked on."""


async def read_soil_properties_source_watermark(
    session: AsyncSession,
    store: ObjectStore,
    *,
    today: date,
) -> SourceWatermark:
    """Return MAX Last-Modified over the thirty pinned VRTs; drift is `maintain`'s job, not this clock's."""
    del session, store
    newest = latest_pin()
    instant = newest.last_modified
    if instant.date() != RELEASE_DAY:
        raise SoilPropertiesWatermarkError(
            f"the latest pinned Last-Modified day {instant.date().isoformat()} is not the release day "
            f"{RELEASE_DAY.isoformat()} that names {RELEASE_ID}; re-pin products.py as one release"
        )
    if instant.date() > today:
        raise SoilPropertiesWatermarkError(f"{RELEASE_ID} cannot be available before {instant.date().isoformat()}")
    return SourceWatermark(
        day=instant.date(),
        instant=instant,
        basis=(
            f"MAX Last-Modified over the {len(SOURCE_FILE_PINS)} pinned ISRIC SoilGrids v2.0 mean VRTs "
            f"({newest.file_name}); per-file Last-Modified and ETag pins in pipeline/direct/soil_properties/products.py"
        ),
    )


__all__ = ["SoilPropertiesWatermarkError", "read_soil_properties_source_watermark"]
