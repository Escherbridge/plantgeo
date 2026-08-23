"""Parquet schema for the `soil-survey` lane: USDA SSURGO map-unit delineations, WKB geometry.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
STATIC layer, `horizon: none` (docs/lanes/soil-survey.md section 7): no forecaster exists or is
planned for this lane, so only `kind=observed` is ever written -- there is no `kind=forecast`
sibling to keep in shape-parity with it.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

SOIL_SURVEY_STREAM: Final = "soil-survey"

# The lane's own declared grain (docs/lanes/soil-survey.md section 4): "One row = one persisted
# SSURGO delineation, keyed on mupolygonkey" -- EXPLICITLY NOT mukey, which one Boise viewport
# measured collapsing 683 delineations onto only 98 distinct values
# (src/lib/server/AGENTS.md:367-369, restated at usda-soil.ts:595-598). Each row is already
# unique on mupolygonkey alone -- only the current, published vintage is ever exported (see
# pipeline/lanes/soil_survey.py and sql/pipeline/soil_survey_day_export.sql for why) -- so no
# second column is needed to make this a real key.
SOIL_SURVEY_GRAIN: Final[tuple[str, ...]] = ("mupolygonkey",)

# Columns mirror exactly what `persistCell` writes into geo.geometry + geo.features
# (src/lib/server/services/usda-soil.ts:719-732, 794, 825-920) plus two columns this export adds
# (release_day) or renders from the geometry dimension rather than the properties JSONB
# (geometry_id, last_confirmed_at, geometry_wkb). Not carried: geo.features.id / geo.layers.id --
# warehouse-internal surrogate keys with no meaning outside this Postgres instance, and the
# `geometry` GeoJSON copy embedded in properties (usda-soil.ts:794) -- redundant with
# `geometry_wkb`, which is taken from the authoritative PostGIS column, not the JSONB mirror.
SOIL_SURVEY_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=SOIL_SURVEY_STREAM,
        arrow_schema=pa.schema(
            [
                # geo.geometry.natural_key: the namespaced identity `usda-sda:<mupolygonkey>`
                # (drizzle/0008_geometry_dimension.sql:34).
                pa.field("natural_key", pa.string(), nullable=False),
                # SSURGO's own per-delineation primary key -- the grain. NOT mukey; see above.
                pa.field("mupolygonkey", pa.string(), nullable=False),
                # SSURGO map-unit key. One delineation's mukey, informational only -- never
                # unique per delineation, never the join key or the sort key.
                pa.field("mukey", pa.string(), nullable=False),
                pa.field("map_unit_name", pa.string(), nullable=True),
                pa.field("soil_series", pa.string(), nullable=True),
                pa.field("drainage_class", pa.string(), nullable=True),
                # Tri-state: SSURGO rates a component Yes or No, or leaves it unranked
                # (usda-soil.ts:76-77) -- never coerce the "unranked" case to false.
                pa.field("hydric_rating", pa.bool_(), nullable=True),
                pa.field("land_capability_class", pa.string(), nullable=True),
                # legend.areasymbol, e.g. "ID001" (usda-soil.ts:79-80).
                pa.field("survey_area_symbol", pa.string(), nullable=True),
                # geo.geometry.version_valid_from: this delineation's CURRENT vintage instant,
                # sourced from `sacatalog.saverest`. The upstream payload carries no timezone
                # for this field, so the value is always UTC midnight, never a fabricated
                # clock time (docs/lanes/soil-survey.md section 5, point 5). Only the current
                # vintage is exportable -- a closed geo.geometry version has no surviving
                # attributes to export at all; see sql/pipeline/soil_survey_day_export.sql for
                # why, and why that is not a simplification of the lane's Type-2 design.
                pa.field("survey_area_vintage", pa.timestamp("us", tz="UTC"), nullable=False),
                # geo.geometry.geometry_id: this version's stable identity, for lineage to a
                # `superseded_by` chain once a survey area republishes.
                pa.field("geometry_id", pa.string(), nullable=False),
                # geo.geometry.last_confirmed_at: the last ingest run that saw this version
                # unchanged upstream -- a staleness signal, never a validity bound
                # (drizzle/0008_geometry_dimension.sql:53-55).
                pa.field("last_confirmed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                # The day THIS export represents -- a caller-supplied constant broadcast onto
                # every row, not derived from any per-row column (see
                # sql/pipeline/soil_survey_day_export.sql). Distinct from `survey_area_vintage`:
                # a static layer's one release day is not the same fact as any one
                # delineation's own vintage -- the same distinction watersheds.py's
                # `release_day` draws for HUC12 boundaries.
                pa.field("release_day", pa.date32(), nullable=False),
                # Well-known binary, not GeoJSON, from geo.geometry.geom
                # (`geometry(GEOMETRY,4326)`, drizzle/0008_geometry_dimension.sql:38). WKB
                # carries no SRID of its own; a reader must assume SRID 4326 out of band, same
                # as every other geometry column in this warehouse.
                pa.field("geometry_wkb", pa.binary(), nullable=False),
                # geo.geometry.producer: constant "usda-sda" today, kept as a real column
                # rather than a hardcoded reader assumption, so a future second SSURGO producer
                # namespace does not silently blend into this one.
                pa.field("producer", pa.string(), nullable=False),
            ]
        ),
        sort_columns=SOIL_SURVEY_GRAIN,
    )
)
