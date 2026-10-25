"""DuckDB spatial session and the GeoJSON-to-WKB repair every Oregon OEM polygon must pass through.

This is a SEPARATE session from `warehouse/parquet/tiers.py::derivation_session`: that one simplifies
an already-repaired base rung down to the three coarse rungs, this one repairs the base rung's
geometry BEFORE it is ever written. The derivation session belongs to the shared tier-derivation
pipeline and is never opened directly here -- the same split `drought/support.py` documents.

THE REPAIR CHAIN IS A TRANSCRIPTION OF `geo.sync_feature_geom_from_properties`
(`drizzle/0004_repair_ingested_geometries.sql:14-56`), the BEFORE INSERT OR UPDATE trigger that
produced every byte of `geo.features.geom` this lane's export SQL read, run here as
`_shared/geometry.py`'s `"pass_through_flagged"` chain mode (this lane reads only its validity and
emptiness verdicts, not the repair flags). Clause for clause:

  ST_GeomFromGeoJSON(geojson)                      -> `parsed := ST_GeomFromGeoJSON(...)`
  CASE WHEN ST_IsValid(parsed) THEN parsed         -> `IF NOT ST_IsValid(parsed) THEN ...` -- an
                                                      already-valid ring is passed through UNTOUCHED,
                                                      never round-tripped through MakeValid "just in
                                                      case", because MakeValid may renumber or re-order
                                                      rings and every one of those bytes shows up as a
                                                      content change.
  ELSE ST_CollectionExtract(ST_MakeValid(parsed),3) -> `ST_MakeValid`, then `ST_CollectionExtract(repaired,
                                                      ST_Dimension(parsed) + 1)`. Hardcoded to 3
                                                      (polygonal) because `ingest/arcgis.py::
                                                      require_polygon_geometry` has already refused
                                                      anything not a Polygon or MultiPolygon.
                                                      CollectionExtract on a non-collection is a no-op
                                                      in both engines.

It is deliberately NOT `drought/support.py`'s chain: that one wraps the result in `ST_Multi` because
`sql/ingest/store_drought_area.sql` does, and adding `ST_Multi` here would publish MULTIPOLYGON where
the map has always drawn POLYGON.

NO `ST_SetSRID`. The trigger's `IF ST_SRID(parsed) = 0 THEN ST_SetSRID(parsed, 4326)` exists because
PostGIS stores an SRID inside the column type; the published column here is plain WKB with no SRID
header, and every reader applies `EVACUATION_ZONES_GEOMETRY_SRID` (4326) itself
(`warehouse/schemas/evacuation_zones.py:39-43`). Setting one would be written into the geometry and
then silently dropped by `ST_AsWKB` -- the exact "assume the SRID survives" trap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.lanes._shared.geometry import convert_geojson_to_wkb, spatial_session

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from contextlib import AbstractContextManager

    from duckdb import DuckDBPyConnection

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


def evacuation_zones_geometry_session() -> AbstractContextManager[DuckDBPyConnection]:
    """An in-memory, single-use spatial session for one snapshot: `LOAD spatial` once per version, not per zone."""
    return spatial_session(
        memory_limit=_REPAIR_MEMORY_LIMIT,
        threads=_REPAIR_THREAD_COUNT,
        max_temp_directory_size=_REPAIR_TEMP_DIRECTORY_SIZE,
    )


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
    shapes = convert_geojson_to_wkb(session, [geometry for _, geometry in pairs], mode="pass_through_flagged")
    repaired: dict[str, bytes] = {}
    for (natural_key, _), shape in zip(pairs, shapes, strict=True):
        if shape.is_empty or not shape.is_valid:
            raise EvacuationZonesGeometryError(
                f"evacuation area {natural_key} repaired to an "
                f"{'empty' if shape.is_empty else 'invalid'} geometry; refusing the whole snapshot rather "
                "than publishing a statewide evacuation picture with one advisory area missing "
                "(geo.sync_feature_geom_from_properties raises on exactly this case)"
            )
        repaired[str(natural_key)] = shape.wkb
    return repaired


__all__ = [
    "EvacuationZonesGeometryError",
    "evacuation_zones_geometry_session",
    "repair_zone_geometries_to_wkb",
]
