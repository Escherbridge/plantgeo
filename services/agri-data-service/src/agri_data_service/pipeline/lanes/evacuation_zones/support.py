"""DuckDB spatial session and the GeoJSON-to-WKB repair every Oregon OEM polygon must pass through.

This is a SEPARATE session from `warehouse/parquet/tiers.py::derivation_session`: that one simplifies
an already-repaired base rung down to the three coarse rungs, this one repairs the base rung's
geometry BEFORE it is ever written. This package (L3, `pipeline/direct`) may import `warehouse` for
schemas, but the derivation session belongs to the shared tier-derivation pipeline and is never
opened directly here -- the same split `drought/support.py` documents.

THE REPAIR CHAIN IS A TRANSCRIPTION OF `geo.sync_feature_geom_from_properties`
(`drizzle/0004_repair_ingested_geometries.sql:14-56`), the BEFORE INSERT OR UPDATE trigger that
produced every byte of `geo.features.geom` this lane's export SQL read. Restated for DuckDB spatial
because the direct fetch never passes through PostGIS at all. It is deliberately NOT
`drought/support.py`'s chain: that one wraps the result in `ST_Multi` because
`sql/ingest/store_drought_area.sql` does, and adding `ST_Multi` here would publish MULTIPOLYGON where
the map has always drawn POLYGON.
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

#: The trigger's chain, clause for clause:
#:
#:   ST_GeomFromGeoJSON(geojson)                      -> `parsed := ST_GeomFromGeoJSON(...)`
#:   CASE WHEN ST_IsValid(parsed) THEN parsed         -> `IF NOT ST_IsValid(parsed) THEN ...` -- an
#:                                                       already-valid ring is passed through
#:                                                       UNTOUCHED, never round-tripped through
#:                                                       MakeValid "just in case", because MakeValid
#:                                                       may renumber or re-order rings and every one
#:                                                       of those bytes shows up as a content change.
#:   ELSE ST_CollectionExtract(ST_MakeValid(parsed),3) -> `ST_MakeValid` then, when the repair
#:                                                       produced a GEOMETRYCOLLECTION,
#:                                                       `ST_CollectionExtract(repaired,
#:                                                       ST_Dimension(parsed) + 1)`. The dimension is
#:                                                       hardcoded to 3 (polygonal) because
#:                                                       `ingest/arcgis.py::require_polygon_geometry`
#:                                                       has already refused anything that is not a
#:                                                       Polygon or MultiPolygon, so `ST_Dimension`
#:                                                       is 2 for every row that reaches here.
#:                                                       CollectionExtract on a non-collection is a
#:                                                       no-op in both engines, which is why the SQL
#:                                                       needs no branch where the trigger has one.
#:
#: NO `ST_SetSRID`. The trigger's `IF ST_SRID(parsed) = 0 THEN ST_SetSRID(parsed, 4326)` exists
#: because PostGIS stores an SRID inside the column type; the published column here is plain WKB with
#: no SRID header, and every reader applies `EVACUATION_ZONES_GEOMETRY_SRID` (4326) itself
#: (`warehouse/schemas/evacuation_zones.py:39-43`). Setting one would be written into the geometry
#: and then silently dropped by `ST_AsWKB` -- the exact "assume the SRID survives" trap.
_REPAIR_SQL: Final = (
    "SELECT natural_key, ST_AsWKB(repaired) AS wkb, ST_IsEmpty(repaired) AS is_empty, "
    "ST_IsValid(repaired) AS is_valid FROM ("
    "SELECT natural_key, CASE WHEN ST_IsValid(parsed) THEN parsed "
    "ELSE ST_CollectionExtract(ST_MakeValid(parsed), 3) END AS repaired FROM ("
    "SELECT natural_key, ST_GeomFromGeoJSON(geojson) AS parsed FROM raw_evacuation_zones"
    ")) ORDER BY natural_key"
)

#: Mirrors `warehouse/parquet/tiers.py::DERIVATION_MEMORY_LIMIT` / `DERIVATION_THREAD_COUNT` /
#: `DERIVATION_TEMP_DIRECTORY_SIZE` byte-for-byte -- restated as literals rather than imported,
#: exactly as `drought/support.py` restates them, because this repair session is deliberately not
#: that module's `derivation_session`. A local DuckDB job has already consumed a host on this
#: project; a separate instance is not a reason to skip the guard, and spilling stays disabled.
_REPAIR_MEMORY_LIMIT: Final = "1600MB"
_REPAIR_THREAD_COUNT: Final = 3
_REPAIR_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class EvacuationZonesGeometryError(RuntimeError):
    """Raised when an evacuation-area polygon cannot be repaired, or the spatial session cannot load."""


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load DuckDB's spatial extension from the image's directory. NEVER opened any other way.

    Copies `warehouse/parquet/tiers.py::_load_spatial` exactly -- that module is read-only to this
    package. The runtime user's home is `/nonexistent` in both images, so LOAD/INSTALL die on "Can't
    find the home directory" without the extension directory set first, which is exactly how every
    geometry lane's z9 derivation failed in production on 2026-09-02
    (`foundation/parquet/duckdb_extensions.py`). This base-rung repair session is not that
    derivation, but it is bound by the identical constraint: a DuckDB session on the same image with
    the same `/nonexistent` home.
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
def evacuation_zones_geometry_session() -> Iterator[DuckDBPyConnection]:
    """Open an in-memory, single-use DuckDB session with `spatial` loaded, for repairing one snapshot.

    `:memory:` and no database file, so a repair can never leave one behind or reopen a stale one.
    One session per snapshot rather than one per row: a statewide snapshot is a few hundred polygons
    and they are repaired in a single round trip, so `LOAD spatial` is paid once per published
    version rather than once per zone.
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


def repair_zone_geometries_to_wkb(
    session: DuckDBPyConnection,
    geojson_by_natural_key: Mapping[str, Mapping[str, object]] | Sequence[tuple[str, Mapping[str, object]]],
) -> dict[str, bytes]:
    """Repair every zone's GeoJSON to WKB in one DuckDB round trip, keyed by natural key.

    Refuses (`EvacuationZonesGeometryError`) the WHOLE snapshot the moment one polygon repairs to
    empty or stays invalid, matching the trigger's own `RAISE EXCEPTION ... ERRCODE = '22023'`
    (`drizzle/0004_repair_ingested_geometries.sql:40-43`), which aborts the entire statement and so
    the entire ingestion batch. Dropping the offending zone instead would publish a statewide
    evacuation picture with one advisory area silently missing from it -- the failure mode this
    layer's life-safety warning (`conductor/code_styleguides/layer-lanes.md` section 2) exists for.
    """
    pairs = (
        tuple(geojson_by_natural_key.items())
        if hasattr(geojson_by_natural_key, "items")
        else tuple(geojson_by_natural_key)
    )
    if not pairs:
        return {}
    frame = pa.table(
        {
            "natural_key": [natural_key for natural_key, _ in pairs],
            # `allow_nan=False` refuses a non-finite ordinate here rather than emitting `NaN`, which
            # is not JSON and which `ST_GeomFromGeoJSON` would reject with a far less specific error.
            "geojson": [json.dumps(geometry, allow_nan=False, separators=(",", ":")) for _, geometry in pairs],
        }
    )
    session.register("raw_evacuation_zones", frame)
    try:
        rows = session.execute(_REPAIR_SQL).fetchall()
    finally:
        session.unregister("raw_evacuation_zones")
    repaired: dict[str, bytes] = {}
    for natural_key, wkb, is_empty, is_valid in rows:
        if is_empty or not is_valid:
            raise EvacuationZonesGeometryError(
                f"evacuation area {natural_key} repaired to an "
                f"{'empty' if is_empty else 'invalid'} geometry; refusing the whole snapshot rather "
                "than publishing a statewide evacuation picture with one advisory area missing "
                "(geo.sync_feature_geom_from_properties raises on exactly this case)"
            )
        repaired[str(natural_key)] = bytes(wkb)
    if repaired.keys() != {natural_key for natural_key, _ in pairs}:
        raise EvacuationZonesGeometryError(
            "the DuckDB repair round trip returned a different set of zones than it was given"
        )
    return repaired


__all__ = [
    "EvacuationZonesGeometryError",
    "evacuation_zones_geometry_session",
    "repair_zone_geometries_to_wkb",
]
