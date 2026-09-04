"""DuckDB spatial session and the GeoJSON-to-WKB repair every USDM class polygon must pass through.

This is a SEPARATE session from `warehouse/parquet/tiers.py::derivation_session`: that one
simplifies an already-repaired base rung down to the three coarse rungs, this one repairs the base
rung's geometry BEFORE it is ever written. This package (L3, `pipeline/direct`) may import
`warehouse` for schemas, but the derivation session itself belongs to the shared tier-derivation
pipeline and is never opened directly here -- see `pipeline/direct/AGENTS.md`, "Drought" -- "Two
DuckDB spatial sessions, never one merged into the other".
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

    from agri_data_service.ingest.usdm import DroughtArea

#: The exact PostGIS repair chain `sql/ingest/store_drought_area.sql` runs, restated for DuckDB
#: spatial: MakeValid repairs a self-intersecting ring (publishers emit these routinely at national
#: resolution), CollectionExtract(3) keeps only the polygonal parts a repair may have produced, and
#: Multi normalises a single-part release class onto the same MULTIPOLYGON shape as a multi-part one.
_REPAIR_SQL: Final = (
    "SELECT dm_category, ST_AsWKB(repaired) AS wkb, ST_IsEmpty(repaired) AS is_empty FROM ("
    "SELECT dm_category, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(geojson)), 3)) "
    "AS repaired FROM raw_drought_areas) ORDER BY dm_category"
)


#: Mirrors `warehouse/parquet/tiers.py::DERIVATION_MEMORY_LIMIT` / `DERIVATION_THREAD_COUNT` /
#: `DERIVATION_TEMP_DIRECTORY_SIZE` byte-for-byte -- restated as literals rather than imported,
#: because this repair session is deliberately NOT that module's `derivation_session` (see the
#: module docstring's "never opened directly here"). A local DuckDB cross-join has already consumed
#: a host on this project, and `tiers.py` disables spilling by guard rather than by tuning; this
#: single-release repair session gets no exemption from that guard merely for being a separate
#: instance.
_REPAIR_MEMORY_LIMIT: Final = "1600MB"
_REPAIR_THREAD_COUNT: Final = 3
_REPAIR_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class DroughtGeometryError(RuntimeError):
    """Raised when a USDM class polygon repairs to nothing, or the spatial session cannot load."""


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load DuckDB's spatial extension from the image's directory. NEVER opened any other way.

    Copies `warehouse/parquet/tiers.py::_load_spatial` exactly -- that module is read-only to this
    package. The runtime user's home is `/nonexistent` in both images, so LOAD/INSTALL die on
    "Can't find the home directory" without the extension directory set first, which is exactly how
    every geometry lane's z9 derivation failed in production on 2026-09-02
    (`foundation/parquet/duckdb_extensions.py`, `warehouse/parquet/AGENTS.md` "The derivation
    session and the extension directory"). This base-rung repair session is not that derivation, but
    it is bound by the identical constraint: it is a DuckDB session on the same image with the same
    `/nonexistent` home.
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

    `:memory:` and no database file, so a repair can never leave one behind or reopen a stale one.
    Not shared across releases -- unlike `warehouse/parquet/tiers.py::derivation_session`'s
    documented reuse across many lane-days -- because one release is at most five rows and the reuse
    saving `LOAD spatial` buys elsewhere does not apply at this volume.

    STILL RESOURCE-GUARDED like that session, though: `_REPAIR_MEMORY_LIMIT` / `_REPAIR_THREAD_COUNT`
    / `_REPAIR_TEMP_DIRECTORY_SIZE` pin the identical caps `derivation_session` pins, spilling
    disabled included -- a separate DuckDB instance is not a reason to skip the guard.
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
    areas: Sequence[DroughtArea],
) -> dict[int, bytes]:
    """Repair every class's GeoJSON to valid WKB in one DuckDB round trip, keyed by drought class.

    Refuses (`DroughtGeometryError`) the WHOLE release the moment any one class repairs to empty,
    matching the Postgres write path's own refusal (`ingest/usdm.py::PostgresDroughtStore.store_release`,
    `sql/ingest/store_drought_area.sql`'s header) rather than silently dropping a class or writing a
    `MULTIPOLYGON EMPTY` row -- a fabricated "this class exists and covers nothing" claim.
    """
    if not areas:
        return {}
    frame = pa.table(
        {
            "dm_category": [area.drought_monitor_category for area in areas],
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
                f"USDM drought class D{dm_category} repaired to an empty geometry; refusing the whole "
                "release rather than storing a fabricated MULTIPOLYGON EMPTY coverage claim"
            )
        # Keying into a dict deliberately COLLAPSES a duplicate `dm_category` within one release --
        # the last row for that class silently wins. This matches Postgres exactly rather than
        # diverging from it: `sql/ingest/store_drought_area.sql`'s `ON CONFLICT (valid_date,
        # dm_category) DO UPDATE` performs the identical last-write-wins collapse on the same key.
        # Equivalent behaviour, not a bug -- do not "fix" this into keeping every duplicate.
        repaired[int(dm_category)] = bytes(wkb)
    if repaired.keys() != {area.drought_monitor_category for area in areas}:
        raise DroughtGeometryError("the DuckDB repair round trip returned a different set of classes than it was given")
    return repaired


__all__ = [
    "DroughtGeometryError",
    "drought_geometry_session",
    "repair_drought_areas_to_wkb",
]
