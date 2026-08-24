"""Parquet schema for the watersheds lane: USGS WBD HUC12 base-layer boundaries.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
STATIC layer, `horizon: none` (docs/lanes/watersheds.md section 7): no forecaster exists or is
planned for this lane, so only `kind=observed` is ever written -- there is no `kind=forecast`
sibling to keep in shape-parity with it.

TIER DERIVATION: watersheds is the ONE lane with a genuine hierarchy. A HUC12's first ten digits
ARE its HUC10 parent; its first eight ARE its HUC8 (RUNBOOK section 0.32.2 decision 3). The
uniform ladder maps HUC12/10/8/6 onto tiers z13/z9/z5/z0, so at z5 the `huc12` column holds an
eight-digit HUC8 code. The column keeps its name deliberately — renaming per rung would be the
schema branch the uniform ladder exists to avoid, and a HUC's level IS its length.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GeometrySimplification,
    HierarchicalDissolve,
    TierDerivation,
    register_tier_derivation,
)

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
                # the grain, since huc12 alone already identifies the basin uniquely. Nullable
                # because a dissolved basin that is the union of nine HUC12s has no single
                # geo.features row behind it, so the aggregate is honestly NULL at coarse rungs.
                pa.field("feature_id", pa.string(), nullable=True),
                # Well-known binary, not GeoJSON: roughly half the size of the GeoJSON copy this
                # same row also carries in geo.features.properties->'geometry' (measured on one
                # row, conductor/RUNBOOK.md:1783-1785).
                pa.field("geom", pa.binary(), nullable=False),
            ]
        ),
        sort_columns=WATERSHEDS_GRAIN,
    )
)

# The tier derivation for watersheds: the ONE lane with a real hierarchical dissolve.
# A HUC12's first ten digits ARE its HUC10 parent, and its first eight ARE its HUC8, so the parent
# code is a substring rather than a lookup. RUNBOOK section 0.32.2 decision 3 maps the native
# HUC12/10/8/6/4 levels onto the uniform ladder's four rungs (z13/z9/z5/z0), taking 12/10/8/6.
WATERSHEDS_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=WATERSHEDS_STREAM,
        strategy=GeometrySimplification(
            geometry_column="geom",
            dissolve=HierarchicalDissolve(
                code_column="huc12",
                # z9 serves HUC10 (10 digits), z5 serves HUC8 (8 digits), z0 serves HUC6 (6 digits).
                code_length_by_tier={9: 10, 5: 8, 0: 6},
            ),
            aggregations=(
                # A dissolved HUC10 has no single HUC12's name; NULL rather than inventing one.
                ColumnAggregation(column="name", how="null"),
                # Areas are additive: a HUC10's area is the sum of its child HUC12s' areas.
                ColumnAggregation(column="areasqkm", how="sum"),
                # `tohuc` points at a downstream HUC12 code that no longer exists at coarser rungs;
                # a HUC10's downstream is a HUC10, not any one child's HUC12 `tohuc` value.
                ColumnAggregation(column="tohuc", how="null"),
                # A dissolved basin may span multiple states; the base lane's comma-separated list
                # cannot honestly represent the union, so NULL rather than fabricating.
                ColumnAggregation(column="states", how="null"),
                # Different child HUC12s may have different `hutype` values; no single value
                # honestly describes the merged basin.
                ColumnAggregation(column="hutype", how="null"),
                # The source is constant across the entire lane ("USGS NHDPlus HR WBDHU12"), so
                # `first` picks the shared value and the column stays NOT NULL at coarse rungs.
                ColumnAggregation(column="source", how="first"),
                # The newest loaddate vintage among the child basins: when USGS last touched ANY
                # of the HUC12s now merged into this HUC10.
                ColumnAggregation(column="observed_at", how="max"),
                # The newest ML leakage boundary among merged basins.
                ColumnAggregation(column="data_available_at", how="max"),
                # The release day is a caller-supplied constant broadcast onto every row of the
                # export, so every child shares it and `first` is correct.
                ColumnAggregation(column="release_day", how="first"),
                # No single geo.features row corresponds to a dissolved basin that is the union of
                # nine child HUC12s, so the provenance link becomes NULL at coarse rungs.
                ColumnAggregation(column="feature_id", how="null"),
            ),
        ),
        # Relaxed to nullable ONLY so the coarse rungs above may null them. Named here so a
        # NULL at the base rung still fails the write loudly, as it did before the zoom axis.
        base_non_null_columns=("feature_id",),
    ),
)
