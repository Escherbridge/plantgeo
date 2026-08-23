"""Parquet schema for the drought lane: USDM drought-class polygons, one release per USDM Tuesday.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
`horizon: none` (conductor/RUNBOOK.md section 0.24.1, stream S2) -- a drought class is a
published USDM assessment, not a physical process this repo projects forward, so only
`kind=observed` is ever written; there is no `kind=forecast` sibling to keep in shape-parity
with it (conductor/code_styleguides/layer-lanes.md section 2).
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

DROUGHT_STREAM: Final = "drought"

# One row = one USDM drought-monitor class (D0-D4) of one weekly release, keyed exactly as
# `geo.drought_areas` itself is: the table's own conflict target is the unique index on
# (valid_date, dm_category) (drizzle/0007_governed_environmental_ingestion.sql:20), so this
# export reuses that pair as its grain rather than inventing a second one.
DROUGHT_GRAIN: Final[tuple[str, ...]] = ("valid_date", "dm_category")

# Columns mirror exactly what `sql/pipeline/drought_release_export.sql` selects from
# `geo.drought_areas`, whose declaration is `drizzle/0007_governed_environmental_ingestion.sql:9-18`.
# Not carried: nothing is dropped -- unlike watersheds' `geo.features`, this source table has no
# surrogate-key noise or duplicate-encoding columns to leave behind.
DROUGHT_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=DROUGHT_STREAM,
        arrow_schema=pa.schema(
            [
                # geo.drought_areas.id, kept for provenance back to the warehouse row; not part
                # of the grain, since (valid_date, dm_category) already identifies the class
                # uniquely (see DROUGHT_GRAIN above).
                pa.field("area_id", pa.string(), nullable=False),
                # The USDM release Tuesday this class belongs to. Stored upstream as
                # `varchar(10)` under `CHECK (valid_date ~ '^\d{4}-\d{2}-\d{2}$')`
                # (drizzle/0007_governed_environmental_ingestion.sql:11,17) -- confirmed ISO
                # YYYY-MM-DD by that constraint, never a free-form string -- so the export parses
                # it with Postgres's own `to_date`, deliberately unguarded so a value that failed
                # to parse would abort the export rather than land here as a silently coerced or
                # NULL date (sql/pipeline/drought_release_export.sql). Carrying the varchar
                # through unparsed would push the same ambiguity onto every future reader.
                pa.field("valid_date", pa.date32(), nullable=False),
                # The USDM drought class, 0 (D0, abnormally dry) through 4 (D4, exceptional
                # drought), CHECK-constrained at the source
                # (drizzle/0007_governed_environmental_ingestion.sql:16) and again in
                # `ingest/usdm.py` (`MIN_DROUGHT_CLASS`, `MAX_DROUGHT_CLASS`). `int32` is wide
                # enough for a five-value enum with headroom to spare.
                pa.field("dm_category", pa.int32(), nullable=False),
                # Provenance: the exact dated USDM archive file this class was fetched from
                # (`ingest/usdm.py:132-134`, `usdm_source_url`).
                pa.field("source_url", pa.string(), nullable=False),
                # When this repo's ingest wrote or last re-wrote this row -- `DEFAULT now()`
                # (drizzle/0007_governed_environmental_ingestion.sql:15). Distinct from
                # `valid_date`: ingest can run any day after a Tuesday publishes, and a
                # `--replace` re-run advances this without changing the release the row is valid
                # for (`ingest/usdm.py`, the `replace_predicate` in `store_drought_area.sql`).
                pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
                # Well-known binary, not GeoJSON -- this lane's chosen wire format for geometry
                # (matches `warehouse/schemas/watersheds.py`). WKB carries no coordinate-system
                # header; every row in this stream is WGS 84 (EPSG 4326) because the source
                # column's own declared type is `geometry(MULTIPOLYGON,4326)`
                # (drizzle/0007_governed_environmental_ingestion.sql:13), confirmed again by the
                # write path's own `ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)` call
                # (`sql/ingest/store_drought_area.sql:103`). A reader must apply that SRID
                # itself; nothing in the Parquet file records it.
                pa.field("geom", pa.binary(), nullable=False),
            ]
        ),
        sort_columns=DROUGHT_GRAIN,
    )
)
