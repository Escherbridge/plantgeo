"""DuckDB spatial session and the GeoJSON-to-WKB repair every WFIGS perimeter must pass through.

RESTATED from `pipeline/direct/fire_perimeters/support.py` rather than imported, and deliberately:
that package predates the `ProviderClient` rule and is deleted whole by the Phase-7 supersession
cleanup lane (`lanes/AGENTS.md` "Extracted source leaves are NOT strategies"), and a strategy may
import only `foundation`, `warehouse`, `ingest`, `pipeline/runner/contract.py`, `pipeline/parquet`
and its OWN `pipeline/lanes/<layer>/` package -- never `pipeline/direct/**`. `p4-extract` did not
move this module here because fire-perimeters' modules were not shared with any non-lane caller
(`metadata.json` slice `s-fire-perimeters`), unlike `pipeline/lanes/evacuation_zones/support.py`,
which IS the moved original of the byte-identical chain below.

THE CHAIN ITSELF IS NOT A NEW DESIGN DECISION. It is `geo.sync_feature_geom_from_properties`'s
(`drizzle/0000_baseline.sql:106-159`) BEFORE INSERT OR UPDATE trigger, restated for DuckDB four times
over in this codebase (`pipeline/direct/{fire_perimeters,evacuation_zones,burn_severity}/support.py`,
`pipeline/lanes/evacuation_zones/support.py`): a valid shape passes untouched; an invalid one is
`ST_MakeValid`-ed, then `ST_CollectionExtract(..., 3)`-ed when the repair produced a
GEOMETRYCOLLECTION; only what is STILL invalid or empty is refused (the trigger's SQLSTATE 22023);
a repaired row is flagged. See `pipeline/direct/fire_perimeters/AGENTS.md`, "Geometry repair", for
the history and measurement (41 of 99 live perimeters invalid on 2026-09-15) that made the refusal
of every invalid shape untenable and this chain necessary.

SRID IS CARRIED BY CONVENTION, NOT BY BYTES, ON BOTH SIDES -- same reasoning as the restated module:
`ST_AsWKB` emits standard WKB with no SRID header, every row is `geometry(GEOMETRY,4326)`
(`src/lib/server/db/schema.ts:33`), and WFIGS is queried with `outSR=4326`
(`ingest/wfigs.py::WFIGS_PAGE_QUERY`), so a reader must assume SRID 4326 rather than read it off the
bytes.

NO GEODESIC FUNCTION IS CALLED HERE, and none may be added -- the one area ratio below is planar and
unit-free by construction, so it needs neither `ST_Distance_Sphere` nor `ST_Distance_Spheroid`.
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

#: The trigger's chain, restated for DuckDB and keyed by position -- see the module docstring for
#: what each step reproduces. 3 is hardcoded because the strategy's own fetch admits only Polygon /
#: MultiPolygon geometry (`ingest/arcgis.py::require_polygon_geometry`), and `ST_CollectionExtract`
#: on a non-collection is a no-op in both engines.
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

#: Mirrors `pipeline/direct/fire_perimeters/support.py`'s own constants byte-for-byte -- restated as
#: literals rather than imported for the same reason the module docstring gives, and restated rather
#: than re-derived because a local DuckDB cross-join has already consumed a host on this project
#: (see that module's own comment).
_CONVERT_MEMORY_LIMIT: Final = "1600MB"
_CONVERT_THREAD_COUNT: Final = 3
_CONVERT_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class FirePerimeterGeometryError(RuntimeError):
    """Raised when a perimeter is STILL invalid or empty after repair, or the spatial session cannot load.

    THE WHOLE SNAPSHOT IS REFUSED, NOT THE ROW -- the same `refuse_whole_release` policy
    `pipeline/direct/fire_perimeters/forward.py::WRITER_CONTRACT` declares. The trigger's final
    `RAISE ... 22023` aborted the write that carried such a shape, so no honest version of this
    incident has ever existed; dropping it alone would publish a snapshot with a burning incident
    missing, which reads on the map as "no fire here".
    """


@dataclass(frozen=True, slots=True)
class RepairedPerimeterGeometry:
    """One perimeter's WKB, whether the repair chain changed it, and by how much (planar area ratio)."""

    wkb: bytes
    repaired: bool
    #: `(repaired - original) / original`, planar, in the feed's own degrees so the unit cancels.
    #: `None` for an untouched shape, or a repaired one whose original's signed area was zero or
    #: negative -- no honest denominator.
    area_change: float | None


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load DuckDB's spatial extension from the image's directory. NEVER opened any other way.

    Restated from `warehouse/parquet/tiers.py::_load_spatial` (read-only to a lane strategy): the
    runtime user's home is `/nonexistent` in both images, so LOAD/INSTALL die on "Can't find the
    home directory" without the extension directory set first.
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
    Not shared across turns, because this lane publishes at most one version per turn (`static_lookup`),
    so the `LOAD spatial` a shared session would save is paid once either way.
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

    `identities` is parallel to `geometries` and exists only so a refusal can name the incident.
    Returns a tuple positionally aligned with the input. `position` is carried through the query and
    ordered on, rather than trusting DuckDB to preserve registration order, because the alignment of
    a shape to its attributes is the one thing a silent reorder would corrupt beyond detection.
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
            # `NaN` as a coordinate.
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
        raise FirePerimeterGeometryError(f"the DuckDB repair returned {len(rows)} shapes for {len(geometries)} perimeters")
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
                "ST_CollectionExtract -- there is no honest shape to publish for this incident, so the whole "
                "snapshot is refused rather than published with it missing"
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
