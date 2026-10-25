"""Base-rung geometry repair for the drought layer: the exact DuckDB spatial chain the legacy direct
writer runs, DUPLICATED here rather than imported.

NOT imported from `pipeline/direct/drought/support.py`: this package may never import
`pipeline/direct/**` (`pipeline/lanes/AGENTS.md` -- "Config-lane strategies"), and the repair chain's
own precedent already restates rather than imports across a layer boundary it cannot cross
(`support.py::_load_spatial` is itself a verbatim copy of `warehouse/parquet/tiers.py::_load_spatial`,
"since `tiers.py` is a read-only L1 module this L3 package may not import into" --
`pipeline/direct/AGENTS.md`, "Drought"). `extension_directory_setting()` IS reused as a plain function
call -- it lives in `foundation`, inside this package's own allowed import set -- but the repair SQL,
the DuckDB session and its memory/thread/temp-dir guards are restated byte-for-byte so this strategy's
output stays identical to the legacy writer's at the G6 compare gate (`--mode forward --compare`;
spec Phase 5 "Abort G6 if any row shows an unexplained delta").

See `pipeline/direct/AGENTS.md`, "Drought" -- "Two DuckDB spatial sessions, never merged into one".
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.duckdb_extensions import extension_directory_setting

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from duckdb import DuckDBPyConnection

    from agri_data_service.pipeline.lanes.drought.source_protocol import DroughtAreaPayload

#: The exact chain `sql/ingest/store_drought_area.sql` runs in PostGIS and
#: `pipeline/direct/drought/support.py` restates for DuckDB spatial: MakeValid repairs a
#: self-intersecting ring (publishers emit these routinely at national resolution),
#: CollectionExtract(3) keeps only the polygonal parts a repair may have produced, and Multi
#: normalises a single-part release class onto the same MULTIPOLYGON shape as a multi-part one.
_REPAIR_SQL: Final = (
    "SELECT dm_category, ST_AsWKB(repaired) AS wkb, ST_IsEmpty(repaired) AS is_empty FROM ("
    "SELECT dm_category, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(geojson)), 3)) "
    "AS repaired FROM raw_drought_areas) ORDER BY dm_category"
)

#: Mirrors `pipeline/direct/drought/support.py` byte-for-byte: the same runtime image, the same guard.
#: Restated as literals rather than imported, matching that module's own choice not to import
#: `warehouse/parquet/tiers.py::DERIVATION_MEMORY_LIMIT` et al. -- a separate DuckDB instance earns no
#: exemption from the identical spilling guard merely for living in a different package.
_REPAIR_MEMORY_LIMIT: Final = "1600MB"
_REPAIR_THREAD_COUNT: Final = 3
_REPAIR_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class DroughtGeometryError(RuntimeError):
    """Raised when a USDM class polygon repairs to nothing, or the spatial session cannot load."""


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load DuckDB's spatial extension from the image's directory. NEVER opened any other way.

    The runtime user's home is `/nonexistent` in both images, so LOAD/INSTALL die on "Can't find the
    home directory" without the extension directory set first (`foundation/parquet/duckdb_extensions.py`).
    """
    setting = extension_directory_setting()
    if setting is not None:
        session.execute(setting)
    try:
        session.execute("LOAD spatial")
    except duckdb.Error:
        session.execute("INSTALL spatial")
        session.execute("LOAD spatial")


@contextmanager
def drought_geometry_session() -> Iterator[DuckDBPyConnection]:
    """Open an in-memory, single-use DuckDB session with `spatial` loaded, for repairing one release.

    `:memory:` and no database file, so a repair can never leave one behind or reopen a stale one. Not
    shared across releases -- one release is at most five rows, far below the volume that would make
    reusing a session across calls worth the complexity.
    """
    session = duckdb.connect(database=":memory:")
    try:
        session.execute(f"SET memory_limit = '{_REPAIR_MEMORY_LIMIT}'")
        session.execute(f"SET threads = {_REPAIR_THREAD_COUNT}")
        session.execute(f"SET max_temp_directory_size = '{_REPAIR_TEMP_DIRECTORY_SIZE}'")
        _load_spatial(session)
        yield session
    finally:
        session.close()


def repair_drought_areas_to_wkb(
    session: DuckDBPyConnection,
    areas: Sequence[DroughtAreaPayload],
) -> dict[int, bytes]:
    """Repair every class's GeoJSON to valid WKB in one DuckDB round trip, keyed by drought class.

    Refuses (`DroughtGeometryError`) the WHOLE release the moment any one class repairs to empty,
    matching the Postgres write path's own refusal rather than silently dropping a class or writing a
    `MULTIPOLYGON EMPTY` row -- a fabricated "this class exists and covers nothing" claim.
    """
    if not areas:
        return {}
    frame = pa.table(
        {
            "dm_category": [area.drought_intensity_class for area in areas],
            "geojson": [json.dumps(area.geometry, allow_nan=False, separators=(",", ":")) for area in areas],
        }
    )
    session.register("raw_drought_areas", frame)
    try:
        rows = session.execute(_REPAIR_SQL).fetchall()
    finally:
        session.unregister("raw_drought_areas")
    repaired: dict[int, bytes] = {}
    for dm_category, wkb, is_empty in rows:
        if is_empty:
            raise DroughtGeometryError(
                f"drought intensity class D{dm_category} repaired to an empty geometry; refusing the "
                "whole release rather than storing a fabricated MULTIPOLYGON EMPTY coverage claim"
            )
        # Keying into a dict deliberately COLLAPSES a duplicate `dm_category` within one release,
        # matching `sql/ingest/store_drought_area.sql`'s `ON CONFLICT (valid_date, dm_category) DO
        # UPDATE` last-write-wins behaviour rather than diverging from it.
        repaired[int(dm_category)] = bytes(wkb)
    if repaired.keys() != {area.drought_intensity_class for area in areas}:
        raise DroughtGeometryError("the DuckDB repair round trip returned a different set of classes than it was given")
    return repaired


__all__ = ["DroughtGeometryError", "drought_geometry_session", "repair_drought_areas_to_wkb"]
