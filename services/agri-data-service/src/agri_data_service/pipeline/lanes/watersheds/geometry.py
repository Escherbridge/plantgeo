"""Bare GeoJSON-to-WKB conversion for the config-lane strategy (`nhdplus.py`), DuckDB spatial.

Runs `_shared/geometry.py`'s `"bare"` chain mode: the same SQL expression and session settings as
`pipeline/direct/watersheds/support.py`, so a HUC12 polygon converts to the identical WKB bytes
either writer produces -- required for the G6 `--compare` parity check between this strategy and
the legacy `watersheds-direct-forward` writer it is dark beside until that gate flips
`executor = "config"`.

DELIBERATELY NO REPAIR CHAIN -- no `ST_MakeValid`, no `ST_CollectionExtract`, no `ST_Multi`
normalisation, matching `support.py`'s own documented choice: the Postgres write path this lane
replaces applied none either (bare `ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)`), so mirroring that
bare conversion is what keeps a direct-fetched basin identical to its Postgres twin rather than
silently "fixing" a polygon the row it must reproduce was never fixed either. See
`pipeline/lanes/_shared/AGENTS.md`, "Geometry".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.lanes._shared.geometry import convert_geojson_to_wkb, spatial_session

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from contextlib import AbstractContextManager

    from duckdb import DuckDBPyConnection

#: Same sizing as `pipeline/direct/watersheds/support.py`: one measured row is 21,572 B of WKB
#: (`conductor/RUNBOOK.md:1783-1785`), so the whole ~9,396-basin extent is roughly 200 MB in memory
#: at once. Sized above `pipeline/direct/drought/support.py`'s 1600 MB / 3-thread session, which
#: that module's own comment scopes to "at most five rows" -- this session converts the WHOLE lane
#: in one round trip.
_CONVERSION_MEMORY_LIMIT: Final = "3200MB"
_CONVERSION_THREAD_COUNT: Final = 4
_CONVERSION_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class WatershedsGeometryError(RuntimeError):
    """Raised when a HUC12 polygon converts to nothing, or the spatial session cannot load."""


def watersheds_geometry_session() -> AbstractContextManager[DuckDBPyConnection]:
    """An in-memory, single-use spatial session for one whole-lane conversion (one per settled day)."""
    return spatial_session(
        memory_limit=_CONVERSION_MEMORY_LIMIT,
        threads=_CONVERSION_THREAD_COUNT,
        max_temp_directory_size=_CONVERSION_TEMP_DIRECTORY_SIZE,
    )


def convert_watershed_geometries_to_wkb(
    session: DuckDBPyConnection,
    geometries: Sequence[tuple[str, Mapping[str, object]]],
) -> dict[str, bytes]:
    """Convert every (huc12, GeoJSON geometry) pair to WKB in one DuckDB round trip, keyed by huc12.

    Refuses the WHOLE population the moment any one polygon converts to empty: a `MULTIPOLYGON
    EMPTY` row is a fabricated "this basin exists and covers nothing" claim, and a boundary layer
    must never write one -- there is no honest partial answer for a national basin index.
    """
    converted: dict[str, bytes] = {}
    shapes = convert_geojson_to_wkb(session, [geometry for _, geometry in geometries], mode="bare")
    for (huc12, _), shape in zip(geometries, shapes, strict=True):
        if shape.is_empty:
            raise WatershedsGeometryError(
                f"HUC12 {huc12} converted to an empty geometry; refusing the whole population rather than "
                "storing a fabricated MULTIPOLYGON EMPTY coverage claim"
            )
        converted[str(huc12)] = shape.wkb
    return converted


__all__ = [
    "WatershedsGeometryError",
    "convert_watershed_geometries_to_wkb",
    "watersheds_geometry_session",
]
