"""Parquet schema for the `fire-perimeters` lane: the WFIGS current-incident snapshot, geometry as WKB.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
Columns are derived from `geo.features`/`geo.layers` exactly as read by
`sql/pipeline/fire_perimeters_day_export.sql` -- see `docs/lanes/fire-perimeters.md` for the
source system, cadence, and grain evidence, and `AGENTS.md` in this package for the registration
convention this module follows.

THIS LANE IS A `static_lookup` SNAPSHOT, RE-REGISTERED 2026-09-04, and the two dates below are the
whole reason the change was needed. It was registered `daily_series` keyed on `observed_day`, which
made a partition hold only the incidents whose OWN publisher timestamp named that day -- 177
perimeters scattered across 45 partition days, so a single-day read returned near-empty and
reconstructing what `geo.fire_risk_tiles` draws needed the union of a 404-day window. `geo.features`
was never a daily series to slice: it holds one row per WFIGS incident refreshed in place
(`docs/lanes/fire-perimeters.md` #4), which is the same current-state shape `evacuation-zones`
already publishes as a watermark-driven snapshot. One partition now holds the whole standing set.
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

# ONE ROW PER WFIGS INCIDENT PER SNAPSHOT -- never one row per (incident, day), and this tuple is
# where that is enforced rather than described. `docs/lanes/fire-perimeters.md` #4: `geo.features`
# holds one row per incident, refreshed in place, so a snapshot's population is keyed by
# `unique_fire_identifier` alone (`identity.py`'s `build_fire_perimeter_identity`, which carries no
# date component) and `snapshot_day` is the constant version stamp every row in one partition
# shares. Leading on `snapshot_day` also keeps the sort key NON-NULL: `observed_day` is nullable
# below and a nullable leading sort column orders undated rows by database convention rather than
# by contract. `horizon: none` per the lane doc #7: only 6 of thousands of Type-2 dimension entries
# across every producer ever reached a second WFIGS version, so there is no per-incident growth
# trajectory to calibrate a forecast against -- and a `static_lookup` may not declare one at all
# (`foundation/parquet/lane_contract.py`'s `nature_permits_forecast`). No
# `method/monte_carlo/fire-perimeters.py` exists, by design.
FIRE_PERIMETERS_GRAIN: Final[tuple[str, ...]] = ("snapshot_day", "unique_fire_identifier")

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
                # The VERSION STAMP: the day this whole population was captured, stamped on by the
                # export and identical to the `year=/month=/day=` the partition is written to.
                # `foundation/parquet/lane_contract.py`: a static lane's partition day is a version,
                # not an observation, and re-exporting day D IS how that version is corrected.
                pa.field("snapshot_day", pa.date32(), nullable=False),
                # The INCIDENT's own date: `geo.feature_observation_day(properties)`, the same
                # function `geo.fire_risk_tiles` emits as an MVT attribute and the map's date slider
                # filters on. NULLABLE, and that is a contract, not slack. The function returns NULL
                # for a row it cannot date (`drizzle/0018_fire_discovery_observation_day.sql:39-40`)
                # and the client keeps such a row at EVERY slider date via
                # `src/lib/map/tile-layer-date-filter.ts`'s `["!", ["has", "observed_day"]]`. The old
                # `daily_series` export dropped those rows outright -- its `= :observed_day` filter
                # can never match NULL -- so it served strictly fewer perimeters than Martin drew.
                pa.field("observed_day", pa.date32(), nullable=True),
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
