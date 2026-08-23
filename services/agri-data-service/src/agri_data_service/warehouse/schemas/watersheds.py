"""Parquet schema for the watersheds lane: USGS WBD HUC12 base-layer boundaries.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
STATIC layer, `horizon: none` (docs/lanes/watersheds.md section 7): no forecaster exists or is
planned for this lane, so only `kind=observed` is ever written -- there is no `kind=forecast`
sibling to keep in shape-parity with it.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

WATERSHEDS_STREAM: Final = "watersheds"

# One row = one nationally-keyed HUC12 basin polygon, identified by its 12-digit USGS code
# (docs/lanes/watersheds.md section 4). Unlike the signal plane's (support, name, unit, cell,
# day) grain, there is no day component: a re-run refreshes the same huc12 row in place rather
# than minting a new one (ingest/watersheds.py:10-14), so huc12 alone is the whole grain.
WATERSHEDS_GRAIN: Final[tuple[str, ...]] = ("huc12",)

# Columns mirror exactly what `build_watershed_write` puts into geo.features
# (services/agri-data-service/src/agri_data_service/ingest/watersheds.py:151-168) plus the two
# columns native to the geo.features row itself (data_available_at, geom) and one column this
# export adds (release_day). Not carried: geo.features.id / geo.layers.id -- warehouse-internal
# surrogate keys with no meaning outside this Postgres instance, and huc12 already serves as the
# lane's own durable identity.
WATERSHEDS_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=WATERSHEDS_STREAM,
        arrow_schema=pa.schema(
            [
                # The USGS national key; both the entity id and the version key for this
                # snapshot-not-a-series layer (ingest/watersheds.py:114-129).
                pa.field("huc12", pa.string(), nullable=False),
                pa.field("name", pa.string(), nullable=True),
                pa.field("areasqkm", pa.float64(), nullable=True),
                # The downstream basin's HUC code -- absent for a terminal (outlet) basin.
                pa.field("tohuc", pa.string(), nullable=True),
                pa.field("states", pa.string(), nullable=True),
                pa.field("hutype", pa.string(), nullable=True),
                # Constant per row today ("USGS NHDPlus HR WBDHU12"), always written by the one
                # producer this lane has (ingest/watersheds.py:42,164) -- a NULL here would mean
                # the write path regressed, not that the field is legitimately unset.
                pa.field("source", pa.string(), nullable=False),
                # The WBD's own per-basin loaddate vintage -- when USGS loaded or last touched
                # THIS basin's boundary, not when this repo captured it (docs/lanes/
                # watersheds.md section 3). NULL for a basin whose loaddate did not parse
                # (ingest/watersheds.py:143-149) -- a real, expected partial, never fabricated.
                pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=True),
                # The ML leakage-boundary column (drizzle/0025_feature_data_available_at.sql).
                # Measured 100% NULL across all 9,396 production rows today (docs/lanes/
                # watersheds.md section 5, trap 5) -- carried through rather than dropped, so a
                # future producer wiring it does not need a schema migration to appear.
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=True),
                # The day THIS export represents -- a caller-supplied constant broadcast onto
                # every row, not derived from any per-row column (see
                # sql/pipeline/watersheds_day_export.sql). Distinct from `observed_at`: a static
                # layer's one release day is not the same fact as any one basin's own vintage.
                pa.field("release_day", pa.date32(), nullable=False),
                # geo.features.id, kept for provenance back to the warehouse row; not part of
                # the grain, since huc12 alone already identifies the basin uniquely.
                pa.field("feature_id", pa.string(), nullable=False),
                # Well-known binary, not GeoJSON: roughly half the size of the GeoJSON copy this
                # same row also carries in geo.features.properties->'geometry' (measured on one
                # row, conductor/RUNBOOK.md:1783-1785).
                pa.field("geom", pa.binary(), nullable=False),
            ]
        ),
        sort_columns=WATERSHEDS_GRAIN,
    )
)
