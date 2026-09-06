"""DuckDB spatial session and the GeoJSON-to-WKB conversion every perimeter must pass through.

This is a SEPARATE session from `warehouse/parquet/tiers.py::derivation_session`: that one
simplifies an already-written base rung down to the three coarse rungs, this one produces the base
rung's geometry BEFORE it is ever written. This package (L3, `pipeline/direct`) may import
`warehouse` for schemas, but the derivation session belongs to the shared tier-derivation pipeline
and is never opened directly here -- the same split `drought/support.py` documents.

WHAT THIS REPRODUCES, CLAUSE BY CLAUSE. `geo.features.geom` is not written by any query: it is
written by the BEFORE INSERT OR UPDATE trigger `geo_features_sync_geom`
(`drizzle/0001_handy_riptide.sql:151-186`), which does exactly three things --

    parsed := ST_GeomFromGeoJSON((NEW.properties -> 'geometry')::text);
    IF ST_SRID(parsed) = 0 THEN parsed := ST_SetSRID(parsed, 4326); END IF;
    IF ST_SRID(parsed) <> 4326 OR NOT ST_IsValid(parsed) THEN RAISE ... 22023;

-- parse, stamp SRID 4326, and REFUSE an invalid shape. There is NO `ST_MakeValid` in that chain,
which is why this module has none either. `drought/support.py` runs a full MakeValid /
CollectionExtract / Multi repair because `sql/ingest/store_drought_area.sql` runs one; copying that
repair here would produce WKB PostGIS never held, silently ADDING perimeters to this lane that the
PostgreSQL population does not contain and that `parity.py` would then report as an overcount.

SRID IS CARRIED BY CONVENTION, NOT BY BYTES, ON BOTH SIDES. `ST_AsBinary` emits standard WKB with
no SRID header and so does DuckDB's `ST_AsWKB`; every row is `geometry(GEOMETRY,4326)`
(`src/lib/server/db/schema.ts:33`), so a reader assumes 4326 rather than reading it off the bytes.
DuckDB spatial has no SRID concept at all, which makes the `ST_SetSRID` step a no-op here rather
than a divergence: WFIGS' ArcGIS endpoint is queried with `outSR=4326` (`ingest/arcgis.py`'s
envelope query), so the coordinates arriving are already in 4326 and PostGIS's own branch is the
`ST_SRID(parsed) = 0` one -- GeoJSON carries no SRID either.

NO GEODESIC FUNCTION IS CALLED HERE, and none may be added. `ST_Distance_Sphere` in DuckDB takes
(latitude, longitude); called in the ordinary (lon, lat) order it returns a plausible number that is
about 23% wrong, so it is banned project-wide in favour of `ST_Distance_Spheroid` with latitude
first. This module measures no distances and needs neither.
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

#: The trigger's chain, restated for DuckDB. `ST_GeomFromGeoJSON` raises on a document it cannot
#: read, exactly as the trigger's inner `BEGIN ... EXCEPTION` re-raises 22023; the validity and
#: emptiness answers come back as columns so the refusal below can NAME the perimeter rather than
#: failing the whole round trip anonymously.
_CONVERT_SQL: Final = (
    "SELECT position, ST_AsWKB(parsed) AS wkb, ST_IsValid(parsed) AS is_valid, "
    "ST_IsEmpty(parsed) AS is_empty FROM ("
    "SELECT position, ST_GeomFromGeoJSON(geojson) AS parsed FROM raw_fire_perimeters"
    ") ORDER BY position"
)

#: Mirrors `warehouse/parquet/tiers.py::DERIVATION_MEMORY_LIMIT` / `DERIVATION_THREAD_COUNT` /
#: `DERIVATION_TEMP_DIRECTORY_SIZE` byte-for-byte -- restated as literals rather than imported,
#: because this conversion session is deliberately NOT that module's `derivation_session` (see the
#: module docstring). A local DuckDB cross-join has already consumed a host on this project, and
#: `tiers.py` disables spilling by guard rather than by tuning; a session converting ~23 MB of
#: polygon in one round trip gets no exemption from that guard for being a separate instance.
_CONVERT_MEMORY_LIMIT: Final = "1600MB"
_CONVERT_THREAD_COUNT: Final = 3
_CONVERT_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class FirePerimeterGeometryError(RuntimeError):
    """Raised when a WFIGS polygon is invalid or empty, or the spatial session cannot load.

    THE WHOLE SNAPSHOT IS REFUSED, NOT THE ROW. `geo_features_sync_geom` raises SQLSTATE 22023 for
    an invalid shape, which aborts the INSERT that carried it, so PostgreSQL never held such a
    perimeter either -- dropping it here would publish a version the PostgreSQL population disagrees
    with, and keeping it would publish a shape PostGIS refuses. Failing loudly and naming the
    perimeter is the only answer that leaves the previous version serving and tells an operator
    which incident to look at.
    """


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load DuckDB's spatial extension from the image's directory. NEVER opened any other way.

    Copies `warehouse/parquet/tiers.py::_load_spatial` exactly -- that module is read-only to this
    package. The runtime user's home is `/nonexistent` in both images, so LOAD/INSTALL die on
    "Can't find the home directory" without the extension directory set first, which is exactly how
    every geometry lane's z9 derivation failed in production on 2026-09-02
    (`foundation/parquet/duckdb_extensions.py`, `warehouse/parquet/AGENTS.md` "The derivation
    session and the extension directory"). This base-rung conversion is not that derivation, but it
    is bound by the identical constraint: a DuckDB session on the same image with the same
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
    """Open an in-memory, single-use DuckDB session with `spatial` loaded, for converting one snapshot.

    `:memory:` and no database file, so a conversion can never leave one behind or reopen a stale
    one. Not shared across versions -- unlike `warehouse/parquet/tiers.py::derivation_session`'s
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


def perimeter_geometries_to_wkb(
    session: DuckDBPyConnection,
    geometries: Sequence[object],
    identities: Sequence[str],
) -> tuple[bytes, ...]:
    """Convert every perimeter's GeoJSON to WKB in ONE DuckDB round trip, in the order given.

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
        rows = session.execute(_CONVERT_SQL).fetchall()
    except duckdb.Error as error:
        raise FirePerimeterGeometryError(
            f"DuckDB could not read one of {len(geometries)} WFIGS perimeter geometries as GeoJSON, which is "
            f"the same document PostGIS refuses with SQLSTATE 22023: {type(error).__name__}: {error}"
        ) from error
    finally:
        session.unregister("raw_fire_perimeters")
    if len(rows) != len(geometries):
        raise FirePerimeterGeometryError(
            f"the DuckDB conversion returned {len(rows)} shapes for {len(geometries)} perimeters"
        )
    converted: list[bytes] = []
    for expected_position, (position, wkb, is_valid, is_empty) in enumerate(rows):
        if int(position) != expected_position:
            raise FirePerimeterGeometryError(
                f"the DuckDB conversion returned position {position} where {expected_position} was ordered; "
                "an out-of-order answer would draw each perimeter on another fire's ground"
            )
        if not is_valid or is_empty:
            fault = "an empty geometry" if is_empty else "an invalid geometry"
            raise FirePerimeterGeometryError(
                f"WFIGS perimeter {identities[expected_position]!r} carries {fault}, which "
                "geo_features_sync_geom refuses with SQLSTATE 22023 -- so PostgreSQL never held this "
                "perimeter either. The whole snapshot is refused rather than published without it"
            )
        converted.append(bytes(wkb))
    return tuple(converted)


__all__ = [
    "FirePerimeterGeometryError",
    "fire_perimeter_geometry_session",
    "perimeter_geometries_to_wkb",
]
