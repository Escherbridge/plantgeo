"""DuckDB spatial session and GeoJSON-to-WKB under a named repair chain (`_shared/AGENTS.md` §Geometry)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.duckdb_extensions import extension_directory_setting

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from duckdb import DuckDBPyConnection

#: Which repair chain a lane's legacy writer ran; the three are preserved, never unified (AGENTS.md §Geometry).
GeometryChainMode = Literal["bare", "always_multi", "pass_through_flagged"]

_REGISTERED_TABLE: Final = "raw_geojson_geometries"

#: The exact geometry expression each mode writes; parity tests find it inside the legacy SQL text.
CHAIN_EXPRESSIONS: Final[dict[str, str]] = {
    "bare": "ST_GeomFromGeoJSON(geojson)",
    "always_multi": "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(geojson)), 3))",
    "pass_through_flagged": "CASE WHEN was_valid THEN parsed ELSE ST_CollectionExtract(ST_MakeValid(parsed), 3) END",
}

#: Every mode answers the same six columns so one reader serves all three; NULL where a mode computes nothing.
_CHAIN_SQL: Final[dict[str, str]] = {
    # Bare parse, no repair: the legacy `ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)` write path.
    "bare": (
        "SELECT position, ST_AsWKB(geom) AS wkb, ST_IsEmpty(geom) AS is_empty, "
        "CAST(NULL AS BOOLEAN) AS is_valid, CAST(NULL AS BOOLEAN) AS was_repaired, "
        "CAST(NULL AS DOUBLE) AS area_change FROM ("
        f"SELECT position, {CHAIN_EXPRESSIONS['bare']} AS geom FROM {_REGISTERED_TABLE}) ORDER BY position"
    ),
    # Unconditional MakeValid -> CollectionExtract(3) -> Multi: `store_drought_area.sql` and the MTBS chain.
    "always_multi": (
        "SELECT position, ST_AsWKB(repaired) AS wkb, ST_IsEmpty(repaired) AS is_empty, "
        "CAST(NULL AS BOOLEAN) AS is_valid, CAST(NULL AS BOOLEAN) AS was_repaired, "
        "CAST(NULL AS DOUBLE) AS area_change FROM ("
        f"SELECT position, {CHAIN_EXPRESSIONS['always_multi']} AS repaired FROM {_REGISTERED_TABLE}) "
        "ORDER BY position"
    ),
    # `geo.sync_feature_geom_from_properties`: a valid shape passes untouched, an invalid one is repaired and flagged.
    "pass_through_flagged": (
        "SELECT position, ST_AsWKB(repaired) AS wkb, ST_IsEmpty(repaired) AS is_empty, "
        "ST_IsValid(repaired) AS is_valid, NOT was_valid AS was_repaired, "
        "CASE WHEN NOT was_valid AND ST_Area(parsed) > 0 "
        "THEN (ST_Area(repaired) - ST_Area(parsed)) / ST_Area(parsed) END AS area_change FROM ("
        f"SELECT position, parsed, was_valid, {CHAIN_EXPRESSIONS['pass_through_flagged']} AS repaired FROM ("
        "SELECT position, parsed, ST_IsValid(parsed) AS was_valid FROM ("
        f"SELECT position, ST_GeomFromGeoJSON(geojson) AS parsed FROM {_REGISTERED_TABLE}"
        "))) ORDER BY position"
    ),
}


class SpatialConversionError(RuntimeError):
    """DuckDB answered a row count, order or NULL the round trip cannot attribute to its input."""


@dataclass(frozen=True, slots=True)
class ConvertedGeometry:
    """One input geometry's WKB and the chain's verdict on it; refusal policy stays with the calling lane."""

    wkb: bytes
    is_empty: bool
    #: Post-chain validity; computed only by `pass_through_flagged`, else None.
    is_valid: bool | None
    #: Whether the chain changed the shape; computed only by `pass_through_flagged`, else None.
    was_repaired: bool | None
    #: Planar `(repaired - original) / original`; None unless repaired with a positive original area.
    area_change: float | None


def load_spatial(session: DuckDBPyConnection) -> None:
    """Load `spatial` with the image's extension directory set first: the runtime user's home is `/nonexistent`."""
    setting = extension_directory_setting()
    if setting is not None:
        session.execute(setting)
    try:
        session.execute("LOAD spatial")
    except duckdb.Error:
        session.execute("INSTALL spatial")
        session.execute("LOAD spatial")


@contextmanager
def spatial_session(*, memory_limit: str, threads: int, max_temp_directory_size: str) -> Iterator[DuckDBPyConnection]:
    """An in-memory, single-use, resource-guarded DuckDB session with `spatial` loaded; caps are the caller's."""
    session = duckdb.connect(database=":memory:")
    try:
        session.execute(f"SET memory_limit = '{memory_limit}'")
        session.execute(f"SET threads = {threads}")
        session.execute(f"SET max_temp_directory_size = '{max_temp_directory_size}'")
        load_spatial(session)
        yield session
    finally:
        session.close()


def convert_geojson_to_wkb(
    session: DuckDBPyConnection, geometries: Sequence[object], *, mode: GeometryChainMode
) -> tuple[ConvertedGeometry, ...]:
    """Convert every geometry in ONE round trip, aligned with the input by position; `duckdb.Error` propagates."""
    if not geometries:
        return ()
    frame = pa.table(
        {
            "position": list(range(len(geometries))),
            # `allow_nan=False` refuses a non-finite ordinate here rather than letting DuckDB read `NaN`.
            "geojson": [json.dumps(geometry, allow_nan=False, separators=(",", ":")) for geometry in geometries],
        }
    )
    session.register(_REGISTERED_TABLE, frame)
    try:
        rows = session.execute(_CHAIN_SQL[mode]).fetchall()
    finally:
        session.unregister(_REGISTERED_TABLE)
    if len(rows) != len(geometries):
        raise SpatialConversionError(f"the DuckDB conversion returned {len(rows)} shapes for {len(geometries)} inputs")
    converted: list[ConvertedGeometry] = []
    for expected_position, (position, wkb, is_empty, is_valid, was_repaired, area_change) in enumerate(rows):
        # Ordered on a carried position, never registration order: a silent reorder would misattribute every shape.
        if int(position) != expected_position:
            raise SpatialConversionError(
                f"the DuckDB conversion returned position {position} where {expected_position} was ordered"
            )
        if wkb is None:
            raise SpatialConversionError(f"input {expected_position} converted to NULL rather than a geometry")
        converted.append(
            ConvertedGeometry(
                wkb=bytes(wkb),
                is_empty=bool(is_empty),
                is_valid=None if is_valid is None else bool(is_valid),
                was_repaired=None if was_repaired is None else bool(was_repaired),
                area_change=None if area_change is None else float(area_change),
            )
        )
    return tuple(converted)


__all__ = [
    "CHAIN_EXPRESSIONS",
    "ConvertedGeometry",
    "GeometryChainMode",
    "SpatialConversionError",
    "convert_geojson_to_wkb",
    "load_spatial",
    "spatial_session",
]
