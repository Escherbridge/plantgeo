"""One governed release day's records as the base-rung `BURN_SEVERITY_SCHEMA` table, polygons repaired to WKB in DuckDB.

The config-lane successor of the legacy `rows.py` + `support.py` pair under `pipeline/direct/burn_severity/`,
restated rather than imported because no `pipeline/lanes/**` module may import `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`). Same columns, same repair chain, same caps; why each holds:
`pipeline/lanes/burn_severity/AGENTS.md`, "Release rows".
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.duckdb_extensions import extension_directory_setting
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from datetime import date

    from duckdb import DuckDBPyConnection

    from agri_data_service.pipeline.lanes.burn_severity.source_protocol import BurnSeverityRecordPayload

#: Namespaces `feature_id`: a direct row has no `geo.features.id` to cite, and a bare UUID never holds a colon.
DIRECT_FEATURE_ID_PREFIX: Final = "direct"

#: MakeValid, keep the polygonal parts, normalise to MULTIPOLYGON: the PostGIS trigger's chain, in DuckDB spatial.
_REPAIR_SQL: Final = (
    "SELECT fire_id, ST_AsWKB(repaired) AS wkb, ST_IsEmpty(repaired) AS is_empty FROM ("
    "SELECT fire_id, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(geojson)), 3)) "
    "AS repaired FROM raw_burn_severity_features) ORDER BY fire_id"
)

#: The derivation session's resource guard, spilling disabled (`warehouse/parquet/tiers.py`).
_REPAIR_MEMORY_LIMIT: Final = "1600MB"
_REPAIR_THREAD_COUNT: Final = 3
_REPAIR_TEMP_DIRECTORY_SIZE: Final = "0GiB"


class BurnSeverityGeometryError(RuntimeError):
    """A polygon repaired to nothing, a Fire_ID repeated across cohorts, or the spatial session would not load."""


def direct_feature_id(fire_id: str) -> str:
    """The deterministic `direct:`-namespaced id a direct row carries in place of a Postgres id."""
    return f"{DIRECT_FEATURE_ID_PREFIX}:{fire_id}"


def _load_spatial(session: DuckDBPyConnection) -> None:
    """Load `spatial` from the image's extension directory first: the runtime user has no home directory."""
    setting = extension_directory_setting()
    if setting is not None:
        session.execute(setting)
    try:
        session.execute("LOAD spatial")
    except duckdb.Error:
        session.execute("INSTALL spatial")
        session.execute("LOAD spatial")


@contextmanager
def geometry_session() -> Iterator[DuckDBPyConnection]:
    """An in-memory, single-use, resource-guarded DuckDB session with `spatial` loaded."""
    session = duckdb.connect(database=":memory:")
    try:
        session.execute(f"SET memory_limit = '{_REPAIR_MEMORY_LIMIT}'")
        session.execute(f"SET threads = {_REPAIR_THREAD_COUNT}")
        session.execute(f"SET max_temp_directory_size = '{_REPAIR_TEMP_DIRECTORY_SIZE}'")
        _load_spatial(session)
        yield session
    finally:
        session.close()


def repair_geometries_to_wkb(
    session: DuckDBPyConnection, features: Sequence[tuple[str, Mapping[str, object]]]
) -> dict[str, bytes]:
    """Repair every fire's GeoJSON polygon to WKB in one round trip; refuse the whole day on an empty repair."""
    if not features:
        return {}
    fire_ids = [fire_id for fire_id, _geometry in features]
    if len(set(fire_ids)) != len(fire_ids):
        raise BurnSeverityGeometryError(
            "a burn-severity release day repeats a Fire_ID across its ignition-year cohorts; refusing rather "
            "than collapsing one fire's geometry into another's"
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
        answered = session.execute(_REPAIR_SQL).fetchall()
    finally:
        session.unregister("raw_burn_severity_features")
    repaired: dict[str, bytes] = {}
    for fire_id, wkb, is_empty in answered:
        if is_empty:
            raise BurnSeverityGeometryError(
                f"MTBS Fire_ID {fire_id!r} repaired to an empty geometry; refusing the release day rather than "
                "storing a MULTIPOLYGON EMPTY burned-area claim"
            )
        repaired[str(fire_id)] = bytes(wkb)
    if repaired.keys() != set(fire_ids):
        raise BurnSeverityGeometryError("the DuckDB repair returned a different set of Fire_IDs than it was given")
    return repaired


def release_day_table(records: Sequence[BurnSeverityRecordPayload], *, observed_day: date) -> pa.Table:
    """The base-rung table for one release day; `observed_day` is the governed release day, never re-derived."""
    polygons = [(record.producer_local_id, record.geometry) for record in records]
    with geometry_session() as session:
        repaired = repair_geometries_to_wkb(session, polygons)
    rows = [
        {
            "feature_id": direct_feature_id(record.producer_local_id),
            "fire_id": record.producer_local_id,
            "natural_key": record.natural_key,
            "release_identifier": record.release_identifier,
            "mapping_revision": record.mapping_revision,
            "fire_year": record.ignition_year,
            "ignition_date": record.ignition_date,
            "observed_day": observed_day,
            "data_available_at": record.data_available_at,
            "fire_name": record.fire_name,
            "fire_type": record.fire_type,
            "assessment_type": record.assessment_type,
            "acres": record.acres,
            "severity_class": record.severity_class,
            "dnbr_offset": record.severity_thresholds.dnbr_offset,
            "dnbr_standard_deviation": record.severity_thresholds.dnbr_standard_deviation,
            "nodata_threshold": record.severity_thresholds.nodata_threshold,
            "greenness_threshold": record.severity_thresholds.greenness_threshold,
            "low_threshold": record.severity_thresholds.low_threshold,
            "moderate_threshold": record.severity_thresholds.moderate_threshold,
            "high_threshold": record.severity_thresholds.high_threshold,
            # Ingest policy, never a column read: nothing MTBS-derived reaches a public CDN without a licensing review.
            "allowed_client_exposure": False,
            "geom": repaired[record.producer_local_id],
        }
        for record in records
    ]
    return pa.Table.from_pylist(rows, schema=BURN_SEVERITY_SCHEMA.arrow_schema)


__all__ = [
    "DIRECT_FEATURE_ID_PREFIX",
    "BurnSeverityGeometryError",
    "direct_feature_id",
    "geometry_session",
    "release_day_table",
    "repair_geometries_to_wkb",
]
