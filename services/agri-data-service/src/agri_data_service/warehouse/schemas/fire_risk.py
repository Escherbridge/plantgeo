"""Parquet schema for the `fire-risk` lane: daily per-cell ignition risk at the fire-detections grain.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.

AGRI READS THIS STREAM FOR SERVING; THE ML SERVICE WRITES IT. Owner decision D2, 2026-09-18
(`conductor/tracks/plantgeo_ml_service_20260918/spec.md` FR-5 and FR-5a): every `kind=forecast`
partition moved to `services/plantgeo-ml-service`, which owns the forecasters and the model
artifacts. agri-data-service is observed-only and registers this schema so its Parquet readers, its
lane registry and its slider catalogue can resolve the slug the other service publishes under.

THE SCHEMA IS EXACTLY THE ML SERVICE'S `FIRE_RISK_SCHEMA`
(`services/plantgeo-ml-service/src/plantgeo_ml_service/warehouse/streams.py`): same field names,
types, nullability, order and sort columns. The two are copies held apart on purpose -- the services
deploy independently and neither imports the other -- so a change on either side is a change on
both.

GRAIN: `(cell_longitude, cell_latitude, valid_day)` at the 0.005-degree cell, which is
`fire-detections`' own grid (`warehouse/schemas/fire_detections.py`,
`FIRE_DETECTIONS_CELL_SIZE_DEGREES`). A risk row and the detections it was fit on therefore join
without a dimension table, and the coarse rungs re-floor through the same arithmetic.

WHY THE SIX FORECAST PROVENANCE COLUMNS SIT ON THE REGISTERED SCHEMA, unlike every other lane here:
this lane has NO observed side. It is a scored product, so `kind=forecast` is the only kind it ever
writes and there is no observed row for `forecast_stream_schema()` to append provenance to. That
makes it the one registered stream whose schema already carries those six names, which is why
`warehouse/parquet/schema.py` lists it in `FORECAST_ORIGINATED_STREAMS`.

WHY `probability` AND `risk_score` ARE NULLABLE AND `refused_reason` CARRIES THE WHY: an
out-of-stratum cell is refused, never scored. A fabricated zero reads as "no risk here", which is
the exact claim FR-5's publication gate exists to prevent. Every row still names the artifact it
came from, so a scored cell and a refused cell are both attributable.

ONE DIVERGENCE FROM THE ML COPY, AND IT IS A HOUSING DIFFERENCE, NOT A CONTRACT ONE: that service
carries `base_non_null_columns=("stratum", "model_artifact_sha256")` on the schema object because it
has no tier-derivation engine to hang them on. Here those two fields are already `nullable=False`,
and `validate_derivation_against_schema` refuses a base-non-null declaration for a field that is
never relaxed -- the declaration would be redundant. The guard the list exists to restore is
already in force on both sides.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import (
    FORECAST_PROVENANCE_FIELDS,
    FORECAST_PROVENANCE_GRAIN,
    ParquetStreamSchema,
    register_stream_schema,
)
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    register_tier_derivation,
)

# The layer slug, this stream's `layer=<slug>/` object prefix, and this module's own name.
FIRE_RISK_STREAM: Final = "fire-risk"

# The grid cell size in degrees, inherited from `fire-detections` so the two lanes share cells.
FIRE_RISK_CELL_SIZE_DEGREES: Final = 0.005

FIRE_RISK_GRAIN: Final[tuple[str, ...]] = (
    "cell_longitude",
    "cell_latitude",
    "valid_day",
)

FIRE_RISK_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=FIRE_RISK_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("cell_longitude", pa.float64(), nullable=False),
                pa.field("cell_latitude", pa.float64(), nullable=False),
                pa.field("valid_day", pa.date32(), nullable=False),
                # Null on a refused cell; `refused_reason` carries the why.
                pa.field("probability", pa.float64(), nullable=True),
                pa.field("risk_score", pa.float64(), nullable=True),
                pa.field("stratum", pa.string(), nullable=False),
                pa.field("refused_reason", pa.string(), nullable=True),
                pa.field("model_artifact_sha256", pa.string(), nullable=False),
                *FORECAST_PROVENANCE_FIELDS,
            ]
        ),
        sort_columns=FIRE_RISK_GRAIN + FORECAST_PROVENANCE_GRAIN,
    )
)

# Tier derivation: re-floor onto the coarser grid and re-aggregate, exactly as `fire-detections`
# does, because the two lanes share a grid and a coarse fire-risk cell must contain the same base
# cells a coarse fire-detections cell does.
#
# STRATUM IS A KEY COLUMN RATHER THAN AN AGGREGATE, and that decision carries the rest of the table.
# A stratum is the model's own partition of the landscape; averaging a closed-forest probability
# with a rangeland one produces a number no model ever fit. Keeping it in the grain means a coarse
# cell reports one row PER STRATUM present in it, which is both honest and still renderable. It also
# makes `model_artifact_sha256`, `forecast_run_id`, `random_seed` and `ensemble_size` genuinely
# constant within a group -- one run, one stratum, one model -- so `first` carries a real value
# rather than picking one of several.
#
# `refused_reason` is nulled: a coarse cell mixing refused and scored base cells can honestly name
# no single reason, and the field is nullable for exactly that.
FIRE_RISK_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=FIRE_RISK_STREAM,
        strategy=GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            key_columns=("valid_day", "stratum", *FORECAST_PROVENANCE_GRAIN),
            aggregations=(
                ColumnAggregation("probability", "mean"),  # intensive: a probability, never a total
                ColumnAggregation("risk_score", "mean"),  # intensive on the same scale as probability
                ColumnAggregation("refused_reason", "null"),  # no single reason survives a merge
                ColumnAggregation("model_artifact_sha256", "first"),  # constant per run and stratum
                ColumnAggregation("forecast_run_id", "first"),  # constant within a partition
                ColumnAggregation("random_seed", "first"),  # constant within a run
                ColumnAggregation("ensemble_size", "first"),  # constant within a run
            ),
        ),
    )
)
