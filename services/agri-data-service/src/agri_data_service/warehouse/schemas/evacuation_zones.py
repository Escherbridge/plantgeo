"""Parquet schema for the evacuation-zones lane: Oregon OEM's current-state feed, snapshotted daily.

Layer L1: may import `foundation` and sibling `warehouse` modules; may NOT import method,
pipeline, planes, or interface. See `AGENTS.md` in this directory for the registration
convention and `docs/lanes/evacuation-zones.md` for the source-system facts this schema encodes.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    GeometrySimplification,
    TierDerivation,
    register_tier_derivation,
)

EVACUATION_ZONES_STREAM: Final = "evacuation-zones"

# Single fixed producer for this lane (docs/lanes/evacuation-zones.md §1); the identity builder
# that mints this token lives at ingest/evacuation_zones.py:65 (EVACUATION_ZONES_PRODUCER). Kept
# here as its own constant, rather than parsed out of `natural_key`, per the geometry dimension's
# own instruction: "Read the producer column instead of splitting the key at all"
# (drizzle/0008_geometry_dimension.sql:19-20).
EVACUATION_ZONES_PRODUCER: Final = "or-oem-evacuation-areas"

# The true grain: one Oregon OEM evacuation area (`natural_key`), captured in one daily snapshot.
# This is a current-state-only source refreshed in place (ingest/writer.py `_REFRESH_FEATURES`;
# docs/lanes/evacuation-zones.md §3-4): there is no per-day delta to key on, so `snapshot_day`
# names the capture day rather than an event day. It sorts first even though it is constant within
# one partition file, matching the convention `SIGNAL_PLANE_GRAIN` set in
# `warehouse/parquet/schema.py`; `natural_key` sorts second, which -- since `producer` above is
# the only value this lane ever writes -- collapses to a lexical sort on Oregon's own GlobalID.
EVACUATION_ZONES_GRAIN: Final[tuple[str, ...]] = ("snapshot_day", "natural_key")

# geo.geometry.geom and geo.features.geom (the column this lane's `geometry_wkb` is read from) are
# both fixed at SRID 4326 by their own column type (drizzle/0008_geometry_dimension.sql:38,
# `public.geometry(GEOMETRY,4326)`). The SRID is therefore a schema-level constant, never a
# per-row fact, which is why geometry is exported as plain WKB (no SRID envelope) rather than EWKB.
EVACUATION_ZONES_GEOMETRY_SRID: Final = 4326

EVACUATION_ZONES_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=EVACUATION_ZONES_STREAM,
        arrow_schema=pa.schema(
            [
                # Identity: Oregon's own key, and PlantGeo's producer-namespaced key built from it
                # (ingest/evacuation_zones.py:283-305 `build_evacuation_zone_identity`). Both are
                # guaranteed non-blank before a write is ever attempted (`MissingNativeKeyError`
                # otherwise), so neither is nullable here.
                pa.field("global_id", pa.string(), nullable=False),
                pa.field("natural_key", pa.string(), nullable=False),
                pa.field("producer", pa.string(), nullable=False),
                pa.field("snapshot_day", pa.date32(), nullable=False),
                # Current-state attributes, one column per stored property
                # (ingest/evacuation_zones.py:308-351 `build_evacuation_zone_write`). All nullable:
                # Oregon's feed leaves any of them unset for a given area.
                pa.field("evacuation_area_name", pa.string(), nullable=True),
                pa.field("fire_name", pa.string(), nullable=True),
                pa.field("county", pa.string(), nullable=True),
                pa.field("hazard_type", pa.string(), nullable=True),
                pa.field("evacuation_level", pa.int32(), nullable=True),
                pa.field("evacuation_level_label", pa.string(), nullable=True),
                pa.field("severity", pa.string(), nullable=True),
                pa.field("structures_within", pa.float64(), nullable=True),
                pa.field("addresses_within", pa.float64(), nullable=True),
                pa.field("population_within", pa.float64(), nullable=True),
                pa.field("editor_name", pa.string(), nullable=True),
                # Dated from Oregon's own `created_date` ONLY, never `last_edited_date`
                # (ingest/evacuation_zones.py:284-294) -- nullable because an area Oregon never
                # dated is stored undated, never guessed as "now".
                pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=True),
                # Constant literal every write sets (EVACUATION_ZONES_PROPERTY_SOURCE,
                # ingest/evacuation_zones.py:52,347); not nullable because the write path never
                # omits it.
                pa.field("source", pa.string(), nullable=False),
                # Polygon geometry (`require_polygon_geometry`, ingest/evacuation_zones.py:238-240),
                # WKB-encoded; SRID is the fixed EVACUATION_ZONES_GEOMETRY_SRID constant above.
                pa.field("geometry_wkb", pa.binary(), nullable=False),
                # Provenance from the Type-2 geometry dimension (geo.geometry), which versions the
                # SHAPE only, never these attributes (drizzle/0008_geometry_dimension.sql). Nullable
                # because the join is a LEFT JOIN: a feature row not yet linked to a geometry
                # version must still export rather than silently vanish from the snapshot.
                pa.field("geometry_version_id", pa.string(), nullable=True),
                pa.field("geometry_version_valid_from", pa.timestamp("us", tz="UTC"), nullable=True),
                # The freshness signal named explicitly in the write path's own comment as "the
                # freshness signal a consumer needs to age out a vanished area"
                # (ingest/evacuation_zones.py:346; docs/lanes/evacuation-zones.md §5).
                pa.field("geometry_last_confirmed_at", pa.timestamp("us", tz="UTC"), nullable=True),
                # The ML-leakage boundary column; 100% NULL on every published row today
                # (docs/lanes/evacuation-zones.md §5, RUNBOOK §0.25's census) -- carried forward
                # unpopulated rather than assumed.
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=True),
                # geo.features.updated_at: when PlantGeo's own row was last refreshed in place,
                # distinct from `observed_at` (Oregon's dating) and `geometry_last_confirmed_at`
                # (the shape's own staleness signal).
                pa.field("feature_updated_at", pa.timestamp("us", tz="UTC"), nullable=True),
            ]
        ),
        sort_columns=EVACUATION_ZONES_GRAIN,
    )
)

# Tier derivation: SIMPLIFY ONLY, no hierarchy (no dissolve, no aggregations). With dissolve=None,
# every row is carried through unchanged and only the geometry is generalised via topology-
# preserving simplification, so no per-column aggregates are needed and no nullability changes.
# THE AREA FLOOR IS DELIBERATELY UNSET, and the measurement is why. `min_area_tier_squares=1.0`
# was the first choice and it EMPTIES this lane at z0: one z0 grid square is 5.0 x 5.0 = 25 square
# degrees, while the whole PNW universe this warehouse holds is roughly 10 x 10 degrees, so every
# feature in it is far below one square and all of them drop. The z0 tier answers requests z0-z4
# (`zoom_tier_span`), so that is a blank map at continent zoom, not a cheaper one.
# Simplification alone already collapses these polygons to a handful of vertices, which is where
# the byte saving actually comes from. The knob stays available for a future lane whose features
# are genuinely global in extent; for this data, dropping is the wrong half of the trade.
EVACUATION_ZONES_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=EVACUATION_ZONES_STREAM,
        strategy=GeometrySimplification(
            geometry_column="geometry_wkb",
            min_area_tier_squares=None,
        ),
    )
)
