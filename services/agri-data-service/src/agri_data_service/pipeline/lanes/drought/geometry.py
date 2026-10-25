"""Base-rung geometry repair for the drought layer: the legacy direct writer's DuckDB spatial chain.

Runs `_shared/geometry.py`'s `"always_multi"` chain mode -- the exact chain
`sql/ingest/store_drought_area.sql` runs in PostGIS and `pipeline/direct/drought/support.py` restates
for DuckDB spatial: MakeValid repairs a self-intersecting ring (publishers emit these routinely at
national resolution), CollectionExtract(3) keeps only the polygonal parts a repair may have
produced, and Multi normalises a single-part release class onto the same MULTIPOLYGON shape as a
multi-part one. Still NOT imported from `pipeline/direct/**` (`pipeline/lanes/AGENTS.md`); the
session caps below are restated literals so this strategy's output stays identical to the legacy
writer's at the G6 compare gate (`--mode forward --compare`). See `pipeline/lanes/_shared/AGENTS.md`,
"Geometry", and `pipeline/direct/AGENTS.md`, "Drought" -- "Two DuckDB spatial sessions, never merged
into one".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.lanes._shared.geometry import convert_geojson_to_wkb, spatial_session

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractContextManager

    from duckdb import DuckDBPyConnection

    from agri_data_service.pipeline.lanes.drought.source_protocol import DroughtAreaPayload

#: Mirrors `pipeline/direct/drought/support.py` byte-for-byte: the same runtime image, the same guard.
#: Restated as literals rather than imported, matching that module's own choice not to import
#: `warehouse/parquet/tiers.py::DERIVATION_MEMORY_LIMIT` et al. -- a separate DuckDB instance earns no
#: exemption from the identical spilling guard merely for living in a different package.
_REPAIR_MEMORY_LIMIT: Final = "1600MB"
_REPAIR_THREAD_COUNT: Final = 3
_REPAIR_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class DroughtGeometryError(RuntimeError):
    """Raised when a USDM class polygon repairs to nothing, or the spatial session cannot load."""


def drought_geometry_session() -> AbstractContextManager[DuckDBPyConnection]:
    """An in-memory, single-use spatial session for repairing one release (at most five rows)."""
    return spatial_session(
        memory_limit=_REPAIR_MEMORY_LIMIT,
        threads=_REPAIR_THREAD_COUNT,
        max_temp_directory_size=_REPAIR_TEMP_DIRECTORY_SIZE,
    )


def repair_drought_areas_to_wkb(
    session: DuckDBPyConnection,
    areas: Sequence[DroughtAreaPayload],
) -> dict[int, bytes]:
    """Repair every class's GeoJSON to valid WKB in one DuckDB round trip, keyed by drought class.

    Refuses (`DroughtGeometryError`) the WHOLE release the moment any one class repairs to empty,
    matching the Postgres write path's own refusal rather than silently dropping a class or writing a
    `MULTIPOLYGON EMPTY` row -- a fabricated "this class exists and covers nothing" claim.
    """
    shapes = convert_geojson_to_wkb(session, [area.geometry for area in areas], mode="always_multi")
    repaired: dict[int, bytes] = {}
    for area, shape in zip(areas, shapes, strict=True):
        if shape.is_empty:
            raise DroughtGeometryError(
                f"drought intensity class D{area.drought_intensity_class} repaired to an empty geometry; refusing "
                "the whole release rather than storing a fabricated MULTIPOLYGON EMPTY coverage claim"
            )
        # Keying into a dict deliberately COLLAPSES a duplicate `dm_category` within one release, last
        # position winning -- `sql/ingest/store_drought_area.sql`'s `ON CONFLICT (valid_date, dm_category)
        # DO UPDATE` last-write-wins behaviour.
        repaired[int(area.drought_intensity_class)] = shape.wkb
    return repaired


__all__ = ["DroughtGeometryError", "drought_geometry_session", "repair_drought_areas_to_wkb"]
