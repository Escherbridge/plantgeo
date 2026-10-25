"""Bare GeoJSON-to-WKB conversion for the config-lane strategy (`nhdplus.py`), DuckDB spatial.

A SEPARATE, DUPLICATE IMPLEMENTATION OF `pipeline/direct/watersheds/support.py`, ON PURPOSE. A
strategy module may import `foundation`, `warehouse`, `ingest`, `pipeline/runner/contract.py`,
`pipeline/parquet` and its OWN `pipeline/lanes/<layer>/` package -- never `pipeline/direct/**`,
which the Phase-7 cleanup lane deletes (`pipeline/lanes/AGENTS.md`). `support.py`'s conversion is
therefore not reusable here; this module mirrors its SQL and settings exactly (same
`_load_spatial` pattern as `warehouse/parquet/tiers.py` and `pipeline/direct/drought/support.py`)
so a HUC12 polygon converts to the identical WKB bytes either writer produces -- required for the
G6 `--compare` parity check between this strategy and the legacy `watersheds-direct-forward`
writer it is dark beside until that gate flips `executor = "config"`.

DELIBERATELY NO REPAIR CHAIN -- no `ST_MakeValid`, no `ST_CollectionExtract`, no `ST_Multi`
normalisation, matching `support.py`'s own documented choice: the Postgres write path this lane
replaces applied none either (bare `ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)`), so mirroring that
bare conversion is what keeps a direct-fetched basin identical to its Postgres twin rather than
silently "fixing" a polygon the row it must reproduce was never fixed either.
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

#: Same sizing as `pipeline/direct/watersheds/support.py`: one measured row is 21,572 B of WKB
#: (`conductor/RUNBOOK.md:1783-1785`), so the whole ~9,396-basin extent is roughly 200 MB in memory
#: at once. Sized above `pipeline/direct/drought/support.py`'s 1600 MB / 3-thread session, which
#: that module's own comment scopes to "at most five rows" -- this session converts the WHOLE lane
#: in one round trip.
_CONVERSION_MEMORY_LIMIT: Final = "3200MB"
_CONVERSION_THREAD_COUNT: Final = 4
_CONVERSION_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class WatershedsGeometryError(RuntimeError):
    """Raised when a HUC12 polygon converts to nothing, or the spatial session cannot load."""


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load DuckDB's spatial extension from the image's directory. NEVER opened any other way.

    Copies `warehouse/parquet/tiers.py::_load_spatial` exactly: without the extension directory
    set first, `LOAD`/`INSTALL` die on "Can't find the home directory at '/nonexistent'"
    (`foundation/parquet/duckdb_extensions.py`).
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
    one. Not shared across turns -- the strategy's `rows()` calls this at most once per settled day,
    and a static_lookup lane settles at most one day a turn.
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

    Refuses the WHOLE population the moment any one polygon converts to empty: a `MULTIPOLYGON
    EMPTY` row is a fabricated "this basin exists and covers nothing" claim, and a boundary layer
    must never write one -- there is no honest partial answer for a national basin index.
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
