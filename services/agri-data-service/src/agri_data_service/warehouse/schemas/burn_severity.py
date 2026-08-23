"""Parquet schema for the `burn-severity` lane: MTBS burned-area boundaries by release day.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
See `docs/lanes/burn-severity.md` for source, cadence, and the release-not-daily grain, and
`warehouse/schemas/AGENTS.md` for the registration convention.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

BURN_SEVERITY_STREAM: Final = "burn-severity"

# Grain (docs/lanes/burn-severity.md section 4): one row = one MTBS-mapped burned-area boundary
# polygon for one fire, in one ignition-year cohort. `fire_id` (MTBS's native Fire_ID) is unique
# within a release day -- ingest/mtbs.py fetch_release_features asserts this across pages
# (mtbs.py:797-805, MtbsDuplicateFeatureError) -- so (observed_day, fire_id) is the true key.
# `observed_day` is kept even though every row of one exported partition shares its value,
# matching the signal-plane precedent (warehouse/parquet/schema.py SIGNAL_PLANE_GRAIN) of a
# self-describing schema whose grain does not depend on which partition a reader opened it from.
BURN_SEVERITY_GRAIN: Final[tuple[str, ...]] = ("observed_day", "fire_id")

BURN_SEVERITY_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=BURN_SEVERITY_STREAM,
        arrow_schema=pa.schema(
            [
                # Identity and lineage back to geo.features / geo.geometry.
                pa.field("feature_id", pa.string(), nullable=False),
                pa.field("fire_id", pa.string(), nullable=False),
                pa.field("natural_key", pa.string(), nullable=False),
                pa.field("release_identifier", pa.string(), nullable=False),
                pa.field("mapping_revision", pa.string(), nullable=False),
                # Time axis. `observed_day`/`data_available_at` are the release's PUBLICATION
                # date (never `ignition_date`, which would leak the ~18-month mapping lag as
                # lookahead -- docs/lanes/burn-severity.md section 4).
                pa.field("fire_year", pa.int32(), nullable=True),
                pa.field("ignition_date", pa.date32(), nullable=False),
                pa.field("observed_day", pa.date32(), nullable=False),
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=False),
                # Descriptive attributes MTBS actually publishes for this polygon layer.
                pa.field("fire_name", pa.string(), nullable=True),
                pa.field("fire_type", pa.string(), nullable=True),
                pa.field("assessment_type", pa.string(), nullable=True),
                pa.field("acres", pa.float64(), nullable=True),
                # Null on every published row today, by design, not a bug (MTBS distributes
                # severity as a separate thematic raster, never a polygon attribute). Carried
                # forward anyway per docs/lanes/burn-severity.md section 5.
                pa.field("severity_class", pa.string(), nullable=True),
                # Per-fire dNBR mapping-calibration thresholds. NOT an outcome measure and never
                # a stand-in for severity_class (docs/lanes/burn-severity.md section 5).
                pa.field("dnbr_offset", pa.int32(), nullable=True),
                pa.field("dnbr_standard_deviation", pa.int32(), nullable=True),
                pa.field("nodata_threshold", pa.int32(), nullable=True),
                pa.field("greenness_threshold", pa.int32(), nullable=True),
                pa.field("low_threshold", pa.int32(), nullable=True),
                pa.field("moderate_threshold", pa.int32(), nullable=True),
                pa.field("high_threshold", pa.int32(), nullable=True),
                # Governed license restriction, carried forward as a constant (see the export
                # SQL header): nothing MTBS-derived may reach a public CDN without a fresh
                # licensing review (ingest/mtbs.py:171).
                pa.field("allowed_client_exposure", pa.bool_(), nullable=False),
                # Native EPSG:4326 polygon/multipolygon as WKB; SRID is a lane-wide constant and
                # is not carried per row.
                pa.field("geom", pa.binary(), nullable=False),
            ]
        ),
        sort_columns=BURN_SEVERITY_GRAIN,
    )
)
