"""Parquet schema for the `fire-detections` lane: NASA FIRMS active-fire hotspots, cell-day grain.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.

SOURCE CONFIRMATION: the sole producer is `ingest/firms.py`, both access paths (`nasa-firms` current
window and `nasa-firms-archive` past windows -- `ingest/AGENTS.md` "firms.py", `docs/lanes/
fire-detections.md` section 1) writing one shared identity contract into `geo.features` under layer
`fire-detections` (`FIRMS_LAYER.default`, `ingest/firms.py:63-68`) through `ingest/writer.py::
ingest_features` -- never a dedicated `geo.fire_detections` table, which held zero rows and zero
producer writes and was dropped (`drizzle/0021_drop_fire_detections.sql`).

GRAIN DECISION -- why this is a cell-day aggregate, not the raw per-detection row:

`docs/lanes/fire-detections.md` section 4 fixes the raw natural key as
`satellite:acqDate:acqTime:lat(4dp):lon(4dp)` -- one row per discrete, high-cardinality,
event-driven hotspot at a specific instant. `layer-lanes.md` section 2 requires a `kind=observed`
partition and its future `kind=forecast` sibling to share identical grain, units and columns.
Taken literally against the raw grain that requirement is unsatisfiable here: there is no
statistically defensible way to project the exact latitude/longitude of a future fire detection,
because ignition is not a sample on an existing continuous field the way a weather or NDVI reading
is (`docs/lanes/fire-detections.md` section 7). The lane's own contract resolves this by naming an
aggregate grain a Monte Carlo ensemble CAN project -- detection count and/or summed fire-radiative
power per grid cell per day -- and cites production precedent for exactly that shape: the tile-serving
rollups `geo.tile_fire_detections_z9/z6/z0` already reduce this same layer to grid-cell x day
(`conductor/RUNBOOK.md:5130-5132`, `docs/lanes/fire-detections.md` section 7). This schema adopts
that recommendation directly: `(cell_longitude, cell_latitude, observed_day)` is the grain, so a
`kind=forecast` partition -- once a `method/monte_carlo/fire-detections.py` module exists -- can be
shaped identically without inventing a second convention.

WHY FLOAT CELL COORDINATES, NOT AN OPAQUE `cell_id` STRING (unlike `SIGNAL_PLANE_SCHEMA.cell_id`,
`warehouse/parquet/schema.py:120`): the signal plane's `cell_id` resolves through an existing
`agri.spatial_cell` dimension a reader can join against. No such dimension exists for this ad hoc
fire-detections grid, so an opaque key here would be unresolvable by any reader that has only this
Parquet stream -- the two floats are self-describing and need no side table.

WHY 0.005 DEGREES: the finest of the three resolutions the existing production rollups already use
(`conductor/RUNBOOK.md:5130`, `z9`). Coarser aggregates are always derivable from a finer one by
re-flooring; the reverse is not true, so the finest already-validated size is the safer canonical
grain to persist. The exact snapping arithmetic lives in `sql/pipeline/fire_detections_day_export.sql`.

WHY NO `forecast_run_id`/`random_seed`/etc. PROVENANCE COLUMNS YET, DESPITE `horizon: 30d`
(`docs/lanes/fire-detections.md` frontmatter) AND `warehouse/schemas/AGENTS.md`'s general guidance
that they belong in the same schema, nullable on the observed side: this wave ships the observed
side only (`method/monte_carlo/fire-detections.py` does not exist yet), and the sibling
`weather-observations` lane -- landed under this same contract -- made the same call for the same
reason it states explicitly for its own omitted `data_available_at`
(`warehouse/schemas/weather_observations.py:71-75`): "a column that is unconditionally NULL is not
provenance, it is a placeholder." Six permanently-NULL columns describing a forecaster that does not
exist yet would be exactly that placeholder. Adding them is a schema-evolution commit for whoever
builds the forecaster, not a reservation made speculatively here.

COLUMN-BY-COLUMN PROVENANCE:
  cell_longitude / cell_latitude -- floor-snapped to the 0.005-degree grid from `ST_X`/`ST_Y` of
                                     `geo.features.geom` (populated by `geo.sync_feature_geom_from_
                                     properties`, `drizzle/0001_handy_riptide.sql:151-161`, from the
                                     GeoJSON Point `ingest/firms.py:307`).
  observed_day                   -- `geo.feature_observation_day(properties)`
                                     (`drizzle/0015_tile_observation_day.sql:21-57`), the same
                                     function every tile function and the map's date slider use, read
                                     from `properties->>'observedAt'`
                                     (`format_javascript_timestamp(identity.observed_at)`,
                                     `ingest/firms.py:503`) -- never re-derived from `acqDate`/
                                     `acqTime` here, so this export can never bucket a detection onto
                                     a different day than the one already shown on the map.
  detection_count                 -- count of published, geometry-linked detections landing in this
                                     cell-day; the Poisson-like count a Monte Carlo ensemble can
                                     project (`docs/lanes/fire-detections.md` section 7).
  frp_sum                         -- MW, summed only over detections in the group that published
                                     `properties.frp` (`ingest/firms.py:301-303`); NULL, never 0,
                                     when none did -- the same never-zero-fill discipline the
                                     producer itself applies to `brightness`
                                     (`ingest/firms.py:201-215`: "a brightness temperature of 0 K is
                                     physically impossible", the same reasoning applies to a
                                     fabricated 0 MW of fire-radiative power).
  frp_observation_count           -- how many of `detection_count` detections actually contributed
                                     to `frp_sum`, so a reader can tell full FRP coverage from
                                     partial without opening a raw detail stream this lane does not
                                     export. KNOWN LIMITATION, inherited rather than solved here:
                                     `frp_sum` mixes VIIRS (375 m pixel) and MODIS (1000 m pixel)
                                     footprints, whose FRP reads roughly an order of magnitude apart
                                     for the same physical fire (`ingest/firms.py:143-147`, median
                                     33.10 MW vs 4.27 MW measured on production) -- this column does
                                     NOT split by instrument, matching the lane contract's literal
                                     "summed FRP per cell-day" recommendation
                                     (`docs/lanes/fire-detections.md` section 7) rather than adding an
                                     unrequested grain dimension.
  high_confidence_detection_count -- count of detections whose FIRMS-normalized confidence band
                                     (`properties.confidenceNormalized`, `ingest/firms.py:157-171,
                                     232-243`) is `"high"`, so a quality signal survives the
                                     aggregation without re-deriving VIIRS's categorical vs MODIS's
                                     percentage confidence scale from a raw row this stream does not
                                     carry (`docs/lanes/fire-detections.md` section 5.2).
  newest_observed_at              -- latest `properties->>'observedAt'` among the detections rolled
                                     into this cell-day; the provenance-recency column, mirroring
                                     `SIGNAL_PLANE_SCHEMA.newest_observed_at`
                                     (`warehouse/parquet/schema.py:124`).
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

# The layer slug verbatim from `geo.layers.name` -- `FIRMS_LAYER.default` (`ingest/firms.py:64`).
# Also this stream's `layer=<slug>/` object prefix.
FIRE_DETECTIONS_STREAM: Final = "fire-detections"

# The grid cell size, degrees. Kept here as documented context only -- the authoritative value is
# the literal baked into `sql/pipeline/fire_detections_day_export.sql`, since Postgres does the
# snapping; this constant is not interpolated into that file (see the SQL header for why: it is a
# fixed, non-user-supplied literal, the same category `sql.md` allows to be hardcoded rather than
# templated).
FIRE_DETECTIONS_CELL_SIZE_DEGREES: Final = 0.005

FIRE_DETECTIONS_GRAIN: Final[tuple[str, ...]] = (
    "cell_longitude",
    "cell_latitude",
    "observed_day",
)

FIRE_DETECTIONS_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=FIRE_DETECTIONS_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("cell_longitude", pa.float64(), nullable=False),
                pa.field("cell_latitude", pa.float64(), nullable=False),
                pa.field("observed_day", pa.date32(), nullable=False),
                pa.field("detection_count", pa.int64(), nullable=False),
                pa.field("frp_sum", pa.float64(), nullable=True),
                pa.field("frp_observation_count", pa.int64(), nullable=False),
                pa.field("high_confidence_detection_count", pa.int64(), nullable=False),
                pa.field("newest_observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
            ]
        ),
        sort_columns=FIRE_DETECTIONS_GRAIN,
    )
)

FIRE_DETECTIONS_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=FIRE_DETECTIONS_STREAM,
        strategy=GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            key_columns=("observed_day",),
            aggregations=(
                ColumnAggregation("detection_count", "sum"),  # additive count of hotspots per cell-day
                ColumnAggregation("frp_sum", "sum"),  # additive fire-radiative power total across detections
                ColumnAggregation("frp_observation_count", "sum"),  # additive count of FRP-carrying detections
                ColumnAggregation("high_confidence_detection_count", "sum"),  # additive count
                ColumnAggregation("newest_observed_at", "max"),  # most recent observation instant among merged cells
            ),
        ),
    )
)
