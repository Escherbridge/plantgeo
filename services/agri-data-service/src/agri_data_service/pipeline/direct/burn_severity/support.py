"""DuckDB spatial session and the GeoJSON-to-WKB repair every MTBS burned-area polygon must pass through.

This is a SEPARATE session from `warehouse/parquet/tiers.py::derivation_session`: that one
simplifies an already-repaired base rung down to the three coarse rungs, this one repairs the base
rung's geometry BEFORE it is ever written. This package (L3, `pipeline/direct`) may import
`warehouse` for schemas, but the derivation session itself belongs to the shared tier-derivation
pipeline and is never opened directly here -- matching `pipeline/direct/drought/support.py`'s own
"Two DuckDB spatial sessions, never one merged into the other" (`pipeline/direct/AGENTS.md`,
"Drought").
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

#: The exact PostGIS repair chain `geo.sync_feature_geom_from_properties` runs on every
#: `geo.features` insert/update (`drizzle/0004_repair_ingested_geometries.sql:33-38`), restated for
#: DuckDB spatial: MakeValid repairs a self-intersecting ring (MTBS's national-resolution boundary
#: service emits these), CollectionExtract(3) keeps only the polygonal parts a repair may have
#: produced (dimension 2 -> `ST_Dimension + 1 == 3`, the same value the Postgres trigger computes
#: for a Polygon/MultiPolygon input), and Multi normalises a single-part fire onto the same
#: MULTIPOLYGON shape as a multi-part one.
_REPAIR_SQL: Final = (
    "SELECT fire_id, ST_AsWKB(repaired) AS wkb, ST_IsEmpty(repaired) AS is_empty FROM ("
    "SELECT fire_id, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(geojson)), 3)) "
    "AS repaired FROM raw_burn_severity_features) ORDER BY fire_id"
)

#: Mirrors `warehouse/parquet/tiers.py::DERIVATION_MEMORY_LIMIT` / `DERIVATION_THREAD_COUNT` /
#: `DERIVATION_TEMP_DIRECTORY_SIZE` byte-for-byte -- restated as literals rather than imported,
#: because this repair session is deliberately NOT that module's `derivation_session` (see the
#: module docstring's "never opened directly here"). A local DuckDB cross-join has already consumed
#: a host on this project, and `tiers.py` disables spilling by guard rather than by tuning; this
#: single-release-day repair session gets no exemption from that guard merely for being small.
_REPAIR_MEMORY_LIMIT: Final = "1600MB"
_REPAIR_THREAD_COUNT: Final = 3
_REPAIR_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class BurnSeverityGeometryError(RuntimeError):
    """Raised when a burned-area polygon repairs to nothing, or the spatial session cannot load."""


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
def burn_severity_geometry_session() -> Iterator[DuckDBPyConnection]:
    """Open an in-memory, single-use DuckDB session with `spatial` loaded, for repairing one release day.

    `:memory:` and no database file, so a repair can never leave one behind or reopen a stale one.
    Not shared across release days -- a release day tops out at a few hundred fires (measured whole-
    layer total: 541 rows -- `docs/lanes/burn-severity.md` section 5) -- so the reuse saving
    `LOAD spatial` buys elsewhere does not apply at this volume.

    STILL RESOURCE-GUARDED though: `_REPAIR_MEMORY_LIMIT` / `_REPAIR_THREAD_COUNT` /
    `_REPAIR_TEMP_DIRECTORY_SIZE` pin the identical caps `derivation_session` pins, spilling
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


def repair_burn_severity_geometries_to_wkb(
    session: DuckDBPyConnection,
    features: Sequence[tuple[str, dict[str, object]]],
) -> dict[str, bytes]:
    """Repair every fire's GeoJSON polygon to valid WKB in one DuckDB round trip, keyed by Fire_ID.

    `features` is `(fire_id, geometry)` pairs rather than a richer record type, so this module never
    has to import `ingest.mtbs.MtbsBurnSeverityRecord` just to read two of its fields.

    Refuses (`BurnSeverityGeometryError`) the WHOLE release day the moment any one fire's polygon
    repairs to empty, matching Postgres's own trigger refusal
    (`geo.sync_feature_geom_from_properties`, `drizzle/0004_repair_ingested_geometries.sql`) rather
    than silently dropping a fire or writing a `MULTIPOLYGON EMPTY` row -- a fabricated "this fire
    burned nothing" claim.

    A duplicate Fire_ID across the day's unioned ignition-year cohorts is refused outright rather
    than collapsed: `ingest/mtbs.py::MtbsDuplicateFeatureError` already guarantees `fire_id` is
    unique WITHIN one cohort's paged fetch, so a repeat across cohorts here would mean two different
    ignition years reused the same MTBS Fire_ID -- a data-shape error worth surfacing loudly, unlike
    `drought`'s five KNOWN drought classes, which legitimately collapse on a duplicate key
    (`pipeline/direct/drought/support.py::repair_drought_areas_to_wkb`).
    """
    if not features:
        return {}
    fire_ids = [fire_id for fire_id, _geometry in features]
    if len(set(fire_ids)) != len(fire_ids):
        raise BurnSeverityGeometryError(
            "a burn-severity release day carries a duplicate Fire_ID across its unioned ignition-year "
            "cohorts; refusing rather than silently collapsing one fire's geometry into another's"
        )
    frame = pa.table(
        {
            "fire_id": fire_ids,
            "geojson": [
                json.dumps(geometry, allow_nan=False, separators=(",", ":")) for _fire_id, geometry in features
            ],
        }
    )
    session.register("raw_burn_severity_features", frame)
    try:
        rows = session.execute(_REPAIR_SQL).fetchall()
    finally:
        session.unregister("raw_burn_severity_features")
    repaired: dict[str, bytes] = {}
    for fire_id, wkb, is_empty in rows:
        if is_empty:
            raise BurnSeverityGeometryError(
                f"MTBS Fire_ID {fire_id!r} repaired to an empty geometry; refusing the whole release "
                "day rather than storing a fabricated MULTIPOLYGON EMPTY burn-area claim"
            )
        repaired[str(fire_id)] = bytes(wkb)
    if repaired.keys() != set(fire_ids):
        raise BurnSeverityGeometryError(
            "the DuckDB repair round trip returned a different set of Fire_IDs than it was given"
        )
    return repaired


__all__ = [
    "BurnSeverityGeometryError",
    "burn_severity_geometry_session",
    "repair_burn_severity_geometries_to_wkb",
]
