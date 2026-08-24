"""Parquet schema for the `fire-perimeters` lane: the WFIGS current-incident snapshot, geometry as WKB.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
Columns are derived from `geo.features`/`geo.layers` exactly as read by
`sql/pipeline/fire_perimeters_day_export.sql` -- see `docs/lanes/fire-perimeters.md` for the
source system, cadence, and grain evidence, and `AGENTS.md` in this package for the registration
convention this module follows.
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

FIRE_PERIMETERS_STREAM: Final = "fire-perimeters"

# `docs/lanes/fire-perimeters.md` #4: `geo.features` holds one row per WFIGS incident, refreshed
# in place -- NOT one row per (incident, day). `observed_day` here is
# `geo.feature_observation_day(properties)`, THE SAME function the map's date slider and
# `sql/ingest/observed_days.sql`'s completeness census already read, so a row lands on the single
# day its own publisher-dated timestamp names, never on every day the export job happens to run --
# the exact trap the lane doc's #4/#5 name. `horizon: none` per the lane doc #7: only 6 of
# thousands of Type-2 dimension entries across every producer ever reached a second WFIGS version,
# so there is no per-incident growth trajectory to calibrate a forecast against. No
# `method/monte_carlo/fire-perimeters.py` exists, by design.
FIRE_PERIMETERS_GRAIN: Final[tuple[str, ...]] = ("observed_day", "unique_fire_identifier")

FIRE_PERIMETERS_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=FIRE_PERIMETERS_STREAM,
        arrow_schema=pa.schema(
            [
                # geo.features.id (uuid) -- the warehouse row identity, kept for traceback to source.
                pa.field("feature_id", pa.string(), nullable=False),
                # properties->>'uniqueFireIdentifier' -- the WFIGS native key (ingest/wfigs.py:196-198),
                # required and never blank (identity.py's build_fire_perimeter_identity).
                pa.field("unique_fire_identifier", pa.string(), nullable=False),
                pa.field("observed_day", pa.date32(), nullable=False),
                pa.field("incident_name", pa.string(), nullable=True),
                pa.field("irwin_id", pa.string(), nullable=True),
                # properties->>'fireDiscoveryDateTime' / 'polygonDateTime' -- both can be null on the
                # wire (ingest/AGENTS.md:395: 13 of 112 production perimeters had a null
                # polygonDateTime, all with a parseable discovery-time fallback).
                pa.field("fire_discovery_at", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("polygon_at", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("gis_acres", pa.float64(), nullable=True),
                pa.field("fire_cause", pa.string(), nullable=True),
                pa.field("incident_type_category", pa.string(), nullable=True),
                pa.field("poo_state", pa.string(), nullable=True),
                pa.field("percent_contained", pa.float64(), nullable=True),
                # perimeter_severity() (wfigs.py:142-156) returns None rather than a fabricated
                # bucket when WFIGS reports no containment -- never coerce this to a string default.
                pa.field("severity", pa.string(), nullable=True),
                # geo.features.status -- always 'published' by construction of the export's own
                # WHERE clause; carried as evidence of the governance gate applied, not invented.
                pa.field("status", pa.string(), nullable=False),
                # The ML leakage-boundary column (geo.features.data_available_at). 100% NULL in
                # production today (conductor/RUNBOOK.md measured this across all layers) -- kept
                # nullable rather than backfilled with a guess; partial stays partial.
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("updated_at", pa.timestamp("us", tz="UTC"), nullable=False),
                # WKB, not GeoJSON. conductor/RUNBOOK.md's decisions table: "geo.features.geom is
                # authoritative and the dimension is the stale copy" -- geo.geometry's Type-2
                # version chain exists for this producer but is thin and has a known silent-freeze
                # failure mode (docs/lanes/fire-perimeters.md #4), so this lane's WKB comes from
                # geo.features.geom, never geo.geometry.geom. ST_AsBinary emits standard WKB with
                # no SRID header; every row is `geometry(GEOMETRY,4326)`
                # (src/lib/server/db/schema.ts:33), so a reader must assume SRID 4326 rather than
                # read it off the bytes.
                pa.field("geometry_wkb", pa.binary(), nullable=False),
            ]
        ),
        sort_columns=FIRE_PERIMETERS_GRAIN,
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
FIRE_PERIMETERS_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=FIRE_PERIMETERS_STREAM,
        strategy=GeometrySimplification(
            geometry_column="geometry_wkb",
            min_area_tier_squares=None,
        ),
    )
)
