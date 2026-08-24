"""Parquet schema for the water-gauges lane: USGS NWIS discharge readings, one row per instant.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
See `docs/lanes/water-gauges.md` for the source contract this schema is derived from and
`warehouse/schemas/AGENTS.md` for the registration convention.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    register_tier_derivation,
)

WATER_GAUGES_STREAM: Final = "water-gauges"

# The identity contract mints a NEW geo.features row per (site, reading instant) rather than
# overwriting one row per gauge -- ingest/identity.py, build_streamflow_gauge_identity, keyed on
# "{siteNo}:{updatedAt}" (docs/lanes/water-gauges.md section 1, the "headline finding"). This is a
# genuine append-only reading log, so the grain sorts on the site and the instant rather than
# collapsing same-day readings the way signal_plane_day_export.sql collapses release republication
# -- doing that here would delete real sub-daily measurements, not a duplicate.
WATER_GAUGES_GRAIN: Final[tuple[str, ...]] = ("site_number", "observed_at")

WATER_GAUGES_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=WATER_GAUGES_STREAM,
        arrow_schema=pa.schema(
            [
                # Grain half 1/2. Never null: a gauge with no site number is dropped before it can
                # ever be written (ingest/identity.py, build_streamflow_gauge_identity raises
                # MissingNativeKeyError, caught in ingest/usgs_nwis.py's build_gauge_write).
                # NULLABLE because the coarse rungs null it:
                # a coarse cell merges several gauges and can honestly name none of them
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("site_number", pa.string(), nullable=True),
                # Grain half 2/2. The true UTC instant of the reading, parsed from properties'
                # `updatedAt`. May fall on a different UTC calendar day than `observed_day` below --
                # see that field's comment. Never null for the same reason as site_number: a row
                # whose `updatedAt` failed to parse is never written.
                pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                # The PUBLISHER-NAMED day (drizzle/0015_tile_observation_day.sql,
                # geo.feature_observation_day): the first ten characters of the timestamp text,
                # before any UTC offset is applied. This is the axis the map's time slider and tile
                # layers actually read -- NEVER derive this by truncating `observed_at`, which moved
                # 6,279 of 16,743 production rows onto the day after the one they name
                # (drizzle/0015_tile_observation_day.sql:32-33). Always equal to the exported day's
                # partition for every row this exporter can produce; carried explicitly so a reader
                # combining multiple days never has to re-derive it.
                pa.field("observed_day", pa.date32(), nullable=False),
                # Always a string (possibly empty) -- ingest/usgs_nwis.py's parse_gauge and
                # parse_daily_value_series both coalesce a missing siteName to "".
                # NULLABLE because the coarse rungs null it:
                # a coarse cell merges several gauges and can honestly name none of them
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("site_name", pa.string(), nullable=True),
                # From properties' `geogLocation`. The parser (ingest/usgs_nwis.py:229-234) requires
                # the geoLocation/geogLocation objects to exist but not that latitude/longitude keys
                # are populated inside them, so nullable here is honest about what the source
                # actually guarantees rather than an assumption this schema would enforce by force.
                pa.field("latitude", pa.float64(), nullable=True),
                pa.field("longitude", pa.float64(), nullable=True),
                # Discharge, cubic feet per second, NWIS parameter 00060. Null when a forward-path
                # tick found the site silently reporting nothing -- the row is still written, with a
                # wall-clock-stamped identity, rather than dropped or given a fabricated value
                # (ingest/usgs_nwis.py, parse_gauge). NOT filtered by sign: genuine reverse flow
                # reaches -172,000 cfs at these gauges. The NWIS missing-value sentinel (-999999) is
                # guarded upstream of geo.features (is_missing_value_sentinel) and never reaches
                # this table, so no further filtering happens here.
                pa.field("flow_cfs", pa.float64(), nullable=True),
                # Always null from this producer today: classify_condition is called with a literal
                # None at both ingest call sites (ingest/usgs_nwis.py:168-180). Kept as a real column
                # because NWIS's own schema defines a percentile and a future pipeline change may
                # populate it -- dropping it now would have to be re-added later as a schema change.
                pa.field("percentile", pa.float64(), nullable=True),
                # classify_condition(None) always returns a string ("unknown" today); never JSON
                # null in practice, but modelled nullable since nothing in the ingest contract
                # guarantees the key is always populated for every future producer of this lane.
                pa.field("condition", pa.string(), nullable=True),
                # infer_trend always returns "stable" or "declining"; same nullable reasoning as
                # condition above.
                pa.field("trend", pa.string(), nullable=True),
                # Literal "USGS NWIS", stamped by build_gauge_write on every row this lane writes.
                pa.field("source", pa.string(), nullable=False),
                # Whether this row is linked into the Type-2 geometry dimension. Deliberately kept
                # rather than used as a filter: docs/lanes/water-gauges.md section 4 measured 37% of
                # one day's rows unlinked (2026-08-04), and latitude/longitude/flow_cfs all live in
                # `properties` independent of that link, so filtering here would silently discard
                # over a third of real discharge measurements.
                pa.field("geometry_linked", pa.bool_(), nullable=False),
                # geo.features' ML leakage-boundary column (src/lib/server/db/schema.ts,
                # `dataAvailableAt`) -- "when this platform could have known the feature", distinct
                # from `observed_at`. No water-gauges producer is wired to supply it yet
                # (build_streamflow_gauge_identity never sets it, and
                # FeatureIdentity.data_available_at defaults to None -- ingest/identity.py). Exported
                # honestly null rather than backfilled with a guess.
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=True),
                # features.created_at: always populated via the table's DB-side default. Exported as
                # a conservative, always-available upper bound on the leakage boundary above -- this
                # platform could not have known a row before it was written to the warehouse, even
                # though `data_available_at` may one day prove it knew earlier.
                pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
            ]
        ),
        sort_columns=WATER_GAUGES_GRAIN,
    )
)

# Both latitude and longitude are NULLABLE (water_gauges.py:56-57); derive_tier drops null-coordinate
# rows from derived tiers automatically (_derive_grid_tier drops them with .drop_nulls(coordinates)),
# so coarse rungs carry only the subset of gauges that published a location. The base rung keeps them
# all, matching this schema's deliberate "kept rather than filtered" decision (water_gauges.py:80-85).
WATER_GAUGES_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=WATER_GAUGES_STREAM,
        strategy=GridAggregation(
            longitude_column="longitude",
            latitude_column="latitude",
            # NEITHER `site_number` NOR `observed_at` MAY BE A KEY. Both are unique per base row --
            # the gauge's identity and its reading instant -- so keying on them makes every group a
            # singleton and z9/z5/z0 become three verbatim copies of z13 with snapped coordinates:
            # four times the storage, zero coarsening. The DAY is the only time grain a coarse rung
            # can honestly hold.
            key_columns=("observed_day",),
            aggregations=(
                ColumnAggregation("site_number", "null"),  # one gauge's identity; a merged cell has none
                ColumnAggregation("site_name", "null"),  # likewise -- a cell of several gauges has no one name
                ColumnAggregation("observed_at", "max"),  # newest reading instant among the merged gauges
                ColumnAggregation("flow_cfs", "mean"),  # intensive measurement; does not add across gauges
                ColumnAggregation("percentile", "mean"),  # intensive measurement; does not add across gauges
                # NULLED, NOT `first`. These are HAZARD fields, and `first` would hand a merged cell
                # one arbitrary gauge's verdict -- reporting "normal" for a cell in which another
                # gauge is at flood stage. There is no aggregate in the vocabulary that means "the
                # most severe of these" (the values are free-text, not an ordered enum), so the
                # honest coarse answer is no answer. A reader that needs a gauge's condition reads
                # the base rung, where it is per-gauge and true.
                ColumnAggregation("condition", "null"),
                ColumnAggregation("trend", "null"),
                ColumnAggregation("source", "first"),  # constant across the lane; always "USGS NWIS"
                ColumnAggregation("geometry_linked", "all"),  # gate; coarse cell is linked only if every row in it was
                ColumnAggregation("data_available_at", "max"),  # most recent ML leakage boundary among merged cells
                ColumnAggregation("ingested_at", "max"),  # most recent warehouse persistence instant among merged cells
            ),
        ),
        # Relaxed to nullable ONLY so the coarse rungs above may null them. Named here so a
        # NULL at the base rung still fails the write loudly, as it did before the zoom axis.
        base_non_null_columns=("site_name", "site_number"),
    )
)
