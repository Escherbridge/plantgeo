"""DuckDB spatial session and the GeoJSON-to-WKB repair every WFIGS perimeter must pass through.

RESTATED from `pipeline/direct/fire_perimeters/support.py` rather than imported, and deliberately:
that package predates the `ProviderClient` rule and is deleted whole by the Phase-7 supersession
cleanup lane (`lanes/AGENTS.md` "Extracted source leaves are NOT strategies"), and a strategy may
import only `foundation`, `warehouse`, `ingest`, `pipeline/runner/contract.py`, `pipeline/parquet`
and its OWN `pipeline/lanes/<layer>/` package -- never `pipeline/direct/**`. `p4-extract` did not
move this module here because fire-perimeters' modules were not shared with any non-lane caller
(`metadata.json` slice `s-fire-perimeters`), unlike `pipeline/lanes/evacuation_zones/support.py`,
which IS the moved original of the byte-identical chain below.

THE CHAIN ITSELF IS NOT A NEW DESIGN DECISION, BUT IT IS NOT ONE UNIFORM CHAIN EITHER (corrected,
re-read after ring review: an earlier revision of this docstring overstated the identity across all
four restatements). All four trace to `geo.sync_feature_geom_from_properties`'s
(`drizzle/0000_baseline.sql:106-159`) BEFORE INSERT OR UPDATE trigger, but the trigger's two call
sites diverged, and the DuckDB restatements each follow their OWN call site -- there are TWO
variants in this codebase, not one:

- **Pass-through-and-flag** (THIS module, `pipeline/direct/fire_perimeters/support.py`,
  `pipeline/direct/evacuation_zones/support.py`, `pipeline/lanes/evacuation_zones/support.py`): a
  shape that is already valid passes UNTOUCHED -- `CASE WHEN was_valid THEN parsed ELSE
  ST_CollectionExtract(ST_MakeValid(parsed), 3) END` below -- and a repaired row is flagged
  (`was_repaired`/`area_change`, surfaced per row by `RepairedPerimeterGeometry`). No `ST_Multi`
  normalisation: `require_polygon_geometry` (`ingest/arcgis.py`) already admits only Polygon /
  MultiPolygon, so there is nothing left for `ST_Multi` to normalise onto a MultiPolygon shape that
  `ST_CollectionExtract` has not already produced.
- **Always-repair, no flag** (`pipeline/direct/burn_severity/support.py`): EVERY shape, valid or not,
  runs the full `ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(geojson)), 3))` chain
  unconditionally -- no `was_valid` branch, no pass-through, and no repaired/area-change tracking at
  all (that module's own `_REPAIR_SQL` returns only `fire_id, wkb, is_empty`). MTBS's national-scale
  class polygons self-intersect routinely enough at that resolution that burn-severity's own author
  judged the pass-through not worth keeping; this module's author (WFIGS incident perimeters, a
  different source at a different scale) judged the opposite, for the reason named just below.

Only what is STILL invalid or empty after either chain is refused (the trigger's SQLSTATE 22023).
THIS module's choice is a pass-through because `_encode`/`content_digest` in
`pipeline/direct/fire_perimeters/forward.py` (and this module's own strategy's `content_digest`)
digest the geometry bytes: an unconditional `ST_MakeValid` may renumber a valid ring's vertices
byte-for-byte identically in practice, but nothing guarantees it, and a repair that touched every
VALID shape too would read as "changed" on a byte comparison even when nothing upstream moved. See
`pipeline/direct/fire_perimeters/AGENTS.md`, "Geometry repair", for the history and measurement (41
of 99 live perimeters invalid on 2026-09-15) that made refusing every invalid shape untenable and a
repair chain necessary in the first place.

**The SQL now lives once, in `pipeline/lanes/_shared/geometry.py`, as NAMED chain modes** -- this
module runs `"pass_through_flagged"`, burn-severity and drought run `"always_multi"`, watersheds runs
`"bare"`. The extraction moved code, not decisions: every variant is preserved byte-for-byte as its
own mode, and whether MTBS's flagging gap is itself a bug is STILL a decision to be made with the
legacy packages' full history in view (7S, plan.md "c7-scaffold"), never by switching a mode here.
See `pipeline/lanes/_shared/AGENTS.md`, "Geometry".

SRID IS CARRIED BY CONVENTION, NOT BY BYTES, ON BOTH SIDES -- same reasoning as the restated module:
`ST_AsWKB` emits standard WKB with no SRID header, every row is `geometry(GEOMETRY,4326)`
(`src/lib/server/db/schema.ts:33`), and WFIGS is queried with `outSR=4326`
(`ingest/wfigs.py::WFIGS_PAGE_QUERY`), so a reader must assume SRID 4326 rather than read it off the
bytes.

NO GEODESIC FUNCTION IS CALLED HERE, and none may be added -- the one area ratio below is planar and
unit-free by construction, so it needs neither `ST_Distance_Sphere` nor `ST_Distance_Spheroid`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import duckdb

from agri_data_service.pipeline.lanes._shared.geometry import (
    SpatialConversionError,
    convert_geojson_to_wkb,
    spatial_session,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractContextManager

    from duckdb import DuckDBPyConnection

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


def fire_perimeter_geometry_session() -> AbstractContextManager[DuckDBPyConnection]:
    """An in-memory, single-use spatial session for one snapshot (`static_lookup`: at most one version a turn)."""
    return spatial_session(
        memory_limit=_CONVERT_MEMORY_LIMIT,
        threads=_CONVERT_THREAD_COUNT,
        max_temp_directory_size=_CONVERT_TEMP_DIRECTORY_SIZE,
    )


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
    try:
        shapes = convert_geojson_to_wkb(session, geometries, mode="pass_through_flagged")
    except duckdb.Error as error:
        raise FirePerimeterGeometryError(
            f"DuckDB could not read one of {len(geometries)} WFIGS perimeter geometries as GeoJSON, which is "
            f"the same document PostGIS refuses with SQLSTATE 22023: {type(error).__name__}: {error}"
        ) from error
    except SpatialConversionError as error:
        # A count/order/NULL fault would draw each perimeter on another fire's ground.
        raise FirePerimeterGeometryError(f"the DuckDB repair answer cannot be aligned: {error}") from error
    converted: list[RepairedPerimeterGeometry] = []
    for identity, shape in zip(identities, shapes, strict=True):
        if not shape.is_valid or shape.is_empty:
            fault = "empty" if shape.is_empty else "invalid"
            raise FirePerimeterGeometryError(
                f"WFIGS perimeter {identity!r} is still {fault} after ST_MakeValid and "
                "ST_CollectionExtract -- there is no honest shape to publish for this incident, so the whole "
                "snapshot is refused rather than published with it missing"
            )
        converted.append(
            RepairedPerimeterGeometry(wkb=shape.wkb, repaired=bool(shape.was_repaired), area_change=shape.area_change)
        )
    return tuple(converted)


__all__ = [
    "FirePerimeterGeometryError",
    "RepairedPerimeterGeometry",
    "fire_perimeter_geometry_session",
    "repair_perimeter_geometries_to_wkb",
]
