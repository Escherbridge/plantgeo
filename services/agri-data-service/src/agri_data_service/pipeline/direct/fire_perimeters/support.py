"""DuckDB spatial session and the GeoJSON-to-WKB repair every WFIGS perimeter must pass through.

This is a SEPARATE session from `warehouse/parquet/tiers.py::derivation_session`: that one
simplifies an already-written base rung down to the three coarse rungs, this one produces the base
rung's geometry BEFORE it is ever written. This package (L3, `pipeline/direct`) may import
`warehouse` for schemas, but the derivation session belongs to the shared tier-derivation pipeline
and is never opened directly here -- the same split `drought/support.py` documents.

THE REPAIR CHAIN IS `geo.sync_feature_geom_from_properties`'s (`drizzle/0000_baseline.sql:106-159`),
the BEFORE INSERT OR UPDATE trigger that produced every byte of `geo.features.geom` this layer ever
served: a valid shape passes untouched; an invalid one is `ST_MakeValid`-ed, then
`ST_CollectionExtract(..., 3)`-ed when the repair produced a GEOMETRYCOLLECTION; only what is STILL
invalid or empty is refused (SQLSTATE 22023); and a repaired row is flagged. It is the chain
`evacuation_zones/support.py` already restates for the sibling `static_lookup` polygon lane. An
earlier revision of this module refused every invalid shape outright, citing a migration file that
no longer exists -- with 41 of 99 live perimeters invalid on 2026-09-15, that failed every tick.
History, measurement and the reasoning: this directory's `AGENTS.md`, "Geometry repair".

SRID IS CARRIED BY CONVENTION, NOT BY BYTES, ON BOTH SIDES. `ST_AsBinary` emits standard WKB with
no SRID header and so does DuckDB's `ST_AsWKB`; every row was `geometry(GEOMETRY,4326)`
(`src/lib/server/db/schema.ts:33`), so a reader assumes 4326 rather than reading it off the bytes.
DuckDB spatial has no SRID concept at all, which makes the trigger's `ST_SetSRID` step a no-op here
rather than a divergence: WFIGS' ArcGIS endpoint is queried with `outSR=4326` (`ingest/arcgis.py`'s
envelope query), so the coordinates arriving are already in 4326 and PostGIS's own branch is the
`ST_SRID(parsed) = 0` one -- GeoJSON carries no SRID either.

NO GEODESIC FUNCTION IS CALLED HERE, and none may be added. `ST_Distance_Sphere` in DuckDB takes
(latitude, longitude); called in the ordinary (lon, lat) order it returns a plausible number that is
about 23% wrong, so it is banned project-wide in favour of `ST_Distance_Spheroid` with latitude
first. The one area ratio below is planar and unit-free by construction, so it needs neither.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.duckdb_extensions import extension_directory_setting

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from duckdb import DuckDBPyConnection

#: The trigger's chain, restated for DuckDB and keyed by position:
#:
#:   ST_GeomFromGeoJSON(geojson)                        -> `parsed := ST_GeomFromGeoJSON(...)`; raises on an
#:                                                         unreadable document as the trigger re-raises 22023
#:   CASE WHEN was_valid THEN parsed                     -> a valid ring passes through UNTOUCHED. MakeValid may
#:                                                         renumber rings, and every such byte would read as a
#:                                                         content change to `watermark.py`'s digest
#:   ELSE ST_CollectionExtract(ST_MakeValid(parsed), 3)  -> `ST_MakeValid`, then `ST_CollectionExtract(repaired,
#:                                                         ST_Dimension(parsed) + 1)`. 3 is hardcoded because
#:                                                         `require_polygon_geometry` admits only Polygon /
#:                                                         MultiPolygon, and CollectionExtract on a non-collection
#:                                                         is a no-op in both engines (a collection extracts to a
#:                                                         MULTIPOLYGON in both, one polygonal part or many)
#:   is_valid / is_empty                                 -> come back as columns so the refusal can NAME the
#:                                                         perimeter, matching the trigger's final RAISE
#:   was_repaired                                        -> the trigger's `geometry_repaired: true` stamp
#:   area_change                                         -> (repaired - original) / original planar area; NULL
#:                                                         when untouched or when the original's SIGNED area is
#:                                                         not positive (zero for a bowtie; negative when DuckDB's
#:                                                         shell-minus-holes goes below zero). Negative usually
#:                                                         means overlapping parts were unioned, not lost ground
#:                                                         (`AGENTS.md`, "The area-change number"). A ratio of
#:                                                         two areas in the same degrees is unit-free, so no
#:                                                         geodesic call is needed to make it honest
_REPAIR_SQL: Final = (
    "SELECT position, ST_AsWKB(repaired) AS wkb, ST_IsValid(repaired) AS is_valid, "
    "ST_IsEmpty(repaired) AS is_empty, NOT was_valid AS was_repaired, "
    "CASE WHEN NOT was_valid AND ST_Area(parsed) > 0 "
    "THEN (ST_Area(repaired) - ST_Area(parsed)) / ST_Area(parsed) END AS area_change FROM ("
    "SELECT position, parsed, was_valid, "
    "CASE WHEN was_valid THEN parsed ELSE ST_CollectionExtract(ST_MakeValid(parsed), 3) END AS repaired FROM ("
    "SELECT position, parsed, ST_IsValid(parsed) AS was_valid FROM ("
    "SELECT position, ST_GeomFromGeoJSON(geojson) AS parsed FROM raw_fire_perimeters"
    "))) ORDER BY position"
)

#: Mirrors `warehouse/parquet/tiers.py::DERIVATION_MEMORY_LIMIT` / `DERIVATION_THREAD_COUNT` /
#: `DERIVATION_TEMP_DIRECTORY_SIZE` byte-for-byte -- restated as literals rather than imported,
#: because this conversion session is deliberately NOT that module's `derivation_session` (see the
#: module docstring). A local DuckDB cross-join has already consumed a host on this project, and
#: `tiers.py` disables spilling by guard rather than by tuning; a session repairing ~23 MB of
#: polygon in one round trip gets no exemption from that guard for being a separate instance.
_CONVERT_MEMORY_LIMIT: Final = "1600MB"
_CONVERT_THREAD_COUNT: Final = 3
_CONVERT_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class FirePerimeterGeometryError(RuntimeError):
    """Raised when a perimeter is STILL invalid or empty after repair, or the spatial session cannot load.

    THE WHOLE SNAPSHOT IS REFUSED, NOT THE ROW -- the `refuse_whole_release` every repairing sibling
    declares. The trigger's final `RAISE ... 22023` aborted the INSERT that carried such a shape, so
    no honest version of this incident has ever existed; dropping it would publish a snapshot with a
    burning incident missing, which reads on the map as "no fire here". Failing loudly and naming the
    perimeter leaves the previous version serving and tells an operator which incident WFIGS must fix.
    """


@dataclass(frozen=True, slots=True)
class RepairedPerimeterGeometry:
    """One perimeter's WKB, whether the repair chain changed it, and by how much (planar area ratio)."""

    wkb: bytes
    repaired: bool
    #: `(repaired - original) / original`, planar, in the feed's own degrees so the unit cancels. `None`
    #: for an untouched shape, or a repaired one whose original's signed area was zero or negative --
    #: no honest denominator. Negative usually means overlapping parts were unioned, not lost ground.
    area_change: float | None


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load DuckDB's spatial extension from the image's directory. NEVER opened any other way.

    Copies `warehouse/parquet/tiers.py::_load_spatial` exactly -- that module is read-only to this
    package. The runtime user's home is `/nonexistent` in both images, so LOAD/INSTALL die on
    "Can't find the home directory" without the extension directory set first, which is exactly how
    every geometry lane's z9 derivation failed in production on 2026-09-02
    (`foundation/parquet/duckdb_extensions.py`, `warehouse/parquet/AGENTS.md` "The derivation
    session and the extension directory"). This base-rung repair is not that derivation, but it is
    bound by the identical constraint: a DuckDB session on the same image with the same
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
def fire_perimeter_geometry_session() -> Iterator[DuckDBPyConnection]:
    """Open an in-memory, single-use DuckDB session with `spatial` loaded, for repairing one snapshot.

    `:memory:` and no database file, so a repair can never leave one behind or reopen a stale one.
    Not shared across versions -- unlike `warehouse/parquet/tiers.py::derivation_session`'s
    documented reuse across many lane-days -- because this lane publishes at most one version per
    turn, so the `LOAD spatial` that reuse saves is paid once either way.
    """
    session = duckdb.connect(database=":memory:")
    try:
        session.execute(f"SET memory_limit = '{_CONVERT_MEMORY_LIMIT}'")
        session.execute(f"SET threads = {_CONVERT_THREAD_COUNT}")
        session.execute(f"SET max_temp_directory_size = '{_CONVERT_TEMP_DIRECTORY_SIZE}'")
        _load_spatial(session)
        yield session
    finally:
        session.close()


def repair_perimeter_geometries_to_wkb(
    session: DuckDBPyConnection,
    geometries: Sequence[object],
    identities: Sequence[str],
) -> tuple[RepairedPerimeterGeometry, ...]:
    """Repair every perimeter's GeoJSON to valid WKB in ONE DuckDB round trip, in the order given.

    `identities` is parallel to `geometries` and exists only so a refusal can name the incident;
    nothing is keyed by it, because two WFIGS records may legitimately share a fire identifier
    within one walk and `rows.py` -- not this module -- is where that collapse belongs.

    Returns a tuple positionally aligned with the input. `position` is carried through the query and
    ordered on, rather than trusting DuckDB to preserve registration order, because the alignment of
    a shape to its attributes is the one thing a silent reorder would corrupt beyond detection: every
    perimeter would still be present, valid and correctly counted, and each would be drawn on another
    fire's ground.
    """
    if not geometries:
        return ()
    if len(geometries) != len(identities):
        raise FirePerimeterGeometryError(
            f"{len(geometries)} geometries were given beside {len(identities)} identities; a refusal that "
            "cannot name its perimeter is not a refusal an operator can act on"
        )
    frame = pa.table(
        {
            "position": list(range(len(geometries))),
            # `allow_nan=False` refuses a non-finite ordinate here rather than letting DuckDB read
            # `NaN` as a coordinate; PostgreSQL's `(NEW.properties -> 'geometry')::text` round trip
            # can never carry one either, because jsonb has no NaN.
            "geojson": [json.dumps(geometry, allow_nan=False, separators=(",", ":")) for geometry in geometries],
        }
    )
    session.register("raw_fire_perimeters", frame)
    try:
        rows = session.execute(_REPAIR_SQL).fetchall()
    except duckdb.Error as error:
        raise FirePerimeterGeometryError(
            f"DuckDB could not read one of {len(geometries)} WFIGS perimeter geometries as GeoJSON, which is "
            f"the same document PostGIS refuses with SQLSTATE 22023: {type(error).__name__}: {error}"
        ) from error
    finally:
        session.unregister("raw_fire_perimeters")
    if len(rows) != len(geometries):
        raise FirePerimeterGeometryError(
            f"the DuckDB repair returned {len(rows)} shapes for {len(geometries)} perimeters"
        )
    converted: list[RepairedPerimeterGeometry] = []
    for expected_position, (position, wkb, is_valid, is_empty, was_repaired, area_change) in enumerate(rows):
        if int(position) != expected_position:
            raise FirePerimeterGeometryError(
                f"the DuckDB repair returned position {position} where {expected_position} was ordered; "
                "an out-of-order answer would draw each perimeter on another fire's ground"
            )
        if not is_valid or is_empty:
            fault = "empty" if is_empty else "invalid"
            raise FirePerimeterGeometryError(
                f"WFIGS perimeter {identities[expected_position]!r} is still {fault} after ST_MakeValid and "
                "ST_CollectionExtract, the case geo.sync_feature_geom_from_properties refuses with SQLSTATE 22023 "
                "-- there is no honest shape to publish for this incident, so the whole snapshot is refused rather "
                "than published with it missing"
            )
        converted.append(
            RepairedPerimeterGeometry(
                wkb=bytes(wkb),
                repaired=bool(was_repaired),
                area_change=None if area_change is None else float(area_change),
            )
        )
    return tuple(converted)


__all__ = [
    "FirePerimeterGeometryError",
    "RepairedPerimeterGeometry",
    "fire_perimeter_geometry_session",
    "repair_perimeter_geometries_to_wkb",
]
