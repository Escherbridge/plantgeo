"""DuckDB spatial session and the bare GeoJSON-to-WKB conversion every HUC12 polygon passes through.

DELIBERATELY NO REPAIR CHAIN -- no `ST_MakeValid`, no `ST_CollectionExtract`, no `ST_Multi`
normalisation. Unlike `pipeline/direct/drought/support.py`'s USDM repair (national-scale class
polygons that self-intersect routinely at that resolution), watersheds' own Postgres write path
applies none: `sql/ingest/insert_geometry_versions.sql:210` is bare
`ST_SetSRID(ST_GeomFromGeoJSON(request.geojson), 4326)`. Mirroring that bare conversion exactly is
what keeps a direct-fetched basin identical to its Postgres twin, rather than silently "fixing" a
polygon the row it must reproduce was never fixed either. A repair chain belongs here the day a
self-intersecting WBDHU12 polygon is actually measured, not copied in on the strength of a
different lane's different source and different coordinate scale.

This is a SEPARATE session from `warehouse/parquet/tiers.py::derivation_session`, for the identical
reason `pipeline/direct/drought/support.py`'s module docstring gives: L3 `pipeline/direct` code may
import `warehouse` for schemas, but the shared tier-derivation session belongs to that module alone
and is never opened directly here. This base-rung conversion is not that derivation, but it is bound
by the same `/nonexistent`-home constraint (`foundation/parquet/duckdb_extensions.py`).

NO DISTANCE CALCULATION OF ANY KIND HAPPENS IN THIS LANE'S BASE-RUNG EXPORT, so DuckDB's geodesic
argument-order hazard -- `ST_Distance_Sphere` takes `(lon, lat)` and is banned; `ST_Distance_Spheroid`
takes `(lat, lon)` and is the one to use -- never arises here. Noted only because it is a listed
hazard for every geometry lane in this track, not because this module calls either function.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.duckdb_extensions import extension_directory_setting

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from duckdb import DuckDBPyConnection

_CONVERT_SQL: Final = (
    "SELECT huc12, ST_AsWKB(geom) AS wkb, ST_IsEmpty(geom) AS is_empty FROM ("
    "SELECT huc12, ST_GeomFromGeoJSON(geojson) AS geom FROM raw_watershed_geometries) ORDER BY huc12"
)

# One row measured 21,572 B of WKB (sql/pipeline/watersheds_day_export.sql header, transcribed from
# conductor/RUNBOOK.md:1783-1785) -- roughly 200 MB for the whole ~9,396-basin national extent held
# in memory at once. Sized well above `pipeline/direct/drought/support.py`'s 1600 MB / 3-thread
# session, which that module's own comment scopes to "at most five rows": this session converts the
# WHOLE lane in one round trip, not one release's handful of class polygons.
_CONVERSION_MEMORY_LIMIT: Final = "3200MB"
_CONVERSION_THREAD_COUNT: Final = 4
_CONVERSION_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class WatershedsGeometryError(RuntimeError):
    """Raised when a HUC12 polygon converts to nothing, or the spatial session cannot load."""


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load DuckDB's spatial extension from the image's directory. NEVER opened any other way.

    Copies `warehouse/parquet/tiers.py::_load_spatial` / `pipeline/direct/drought/support.py::_load_spatial`
    exactly: without the extension directory set first, `LOAD`/`INSTALL` die on "Can't find the home
    directory at '/nonexistent'" -- exactly how every geometry lane's z9 derivation failed in
    production on 2026-09-02 (`foundation/parquet/duckdb_extensions.py`,
    `warehouse/parquet/AGENTS.md` "The derivation session and the extension directory").
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
def watersheds_geometry_session() -> Iterator[DuckDBPyConnection]:
    """Open an in-memory, single-use DuckDB session with `spatial` loaded, for one whole-lane conversion.

    `:memory:` and no database file, so a conversion can never leave one behind or reopen a stale
    one. Not shared across turns -- `forward.py` calls this at most once per run, since the whole
    lane is converted in a single round trip, unlike `drought/support.py`'s per-release reuse story.

    STILL RESOURCE-GUARDED: `_CONVERSION_MEMORY_LIMIT` / `_CONVERSION_THREAD_COUNT` /
    `_CONVERSION_TEMP_DIRECTORY_SIZE` pin the same discipline `derivation_session` and
    `drought_geometry_session` both pin -- spilling disabled included. A local DuckDB cross-join has
    already consumed a host on this project; a separate DuckDB instance is not a reason to skip that.
    """
    session = duckdb.connect(database=":memory:")
    try:
        session.execute(f"SET memory_limit = '{_CONVERSION_MEMORY_LIMIT}'")
        session.execute(f"SET threads = {_CONVERSION_THREAD_COUNT}")
        session.execute(f"SET max_temp_directory_size = '{_CONVERSION_TEMP_DIRECTORY_SIZE}'")
        _load_spatial(session)
        yield session
    finally:
        session.close()


def convert_watershed_geometries_to_wkb(
    session: DuckDBPyConnection,
    geometries: Sequence[tuple[str, Mapping[str, object]]],
) -> dict[str, bytes]:
    """Convert every (huc12, GeoJSON geometry) pair to WKB in one DuckDB round trip, keyed by huc12.

    Refuses the WHOLE population the moment any one polygon converts to empty, matching
    `repair_drought_areas_to_wkb`'s refusal for the identical reason: a `MULTIPOLYGON EMPTY` row is
    a fabricated "this basin exists and covers nothing" claim, and a boundary layer must never write
    one -- there is no honest partial answer for a national basin index.
    """
    if not geometries:
        return {}
    frame = pa.table(
        {
            "huc12": [huc12 for huc12, _ in geometries],
            "geojson": [json.dumps(geometry, allow_nan=False, separators=(",", ":")) for _, geometry in geometries],
        }
    )
    session.register("raw_watershed_geometries", frame)
    try:
        rows = session.execute(_CONVERT_SQL).fetchall()
    finally:
        session.unregister("raw_watershed_geometries")
    converted: dict[str, bytes] = {}
    for huc12, wkb, is_empty in rows:
        if is_empty:
            raise WatershedsGeometryError(
                f"HUC12 {huc12} converted to an empty geometry; refusing the whole population rather than "
                "storing a fabricated MULTIPOLYGON EMPTY coverage claim"
            )
        converted[str(huc12)] = bytes(wkb)
    if converted.keys() != {huc12 for huc12, _ in geometries}:
        raise WatershedsGeometryError(
            "the DuckDB conversion round trip returned a different huc12 set than it was given"
        )
    return converted


__all__ = [
    "WatershedsGeometryError",
    "convert_watershed_geometries_to_wkb",
    "watersheds_geometry_session",
]
