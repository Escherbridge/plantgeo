"""Pinned Arrow schemas for the streams this service reads and writes, plus the forecast derivation.

Layer L2: may import `foundation` and `method`; may NOT import `pipeline`, `planes` or `interface`.
Every observed schema here is a COPY of agri-data-service's, held honest by
`tests/test_streams_parity.py`. Rationale and the fire-risk column evidence live in `AGENTS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.foundation.parquet_paths import PartitionKind, validate_layer_slug, validate_partition_kind

ParquetCompression = Literal["zstd", "snappy"]

#: Measured on July 2026 of the signal plane by the sibling service: 695,338 B zstd vs 874,945 B
#: snappy. A different codec here would write bytes its readers cannot compare against theirs.
DEFAULT_PARQUET_COMPRESSION: Final[ParquetCompression] = "zstd"


class StreamSchemaError(LookupError):
    """Raised when a stream has no pinned schema in this service."""


class StreamSchemaConflictError(ValueError):
    """Raised when a declared contract cannot hold: a duplicate name, or a provenance collision."""


@dataclass(frozen=True, slots=True)
class ParquetStreamSchema:
    """The storage contract for one object stream: Arrow schema, grain sort key, codec, base rule."""

    name: str
    arrow_schema: pa.Schema
    sort_columns: tuple[str, ...]
    compression: ParquetCompression = DEFAULT_PARQUET_COMPRESSION
    #: Columns the base rung requires non-null even though the Arrow field is nullable, because the
    #: coarse rungs null them. The sibling keeps these on its `TierDerivation`; this service has no
    #: derivation engine, so they ride on the schema. See `AGENTS.md`.
    base_non_null_columns: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        validate_layer_slug(self.name)
        if not self.sort_columns:
            raise StreamSchemaConflictError(f"stream {self.name!r} must declare the grain it is sorted to")
        unknown = tuple(column for column in self.sort_columns if column not in self.arrow_schema.names)
        if unknown:
            raise StreamSchemaConflictError(f"stream {self.name!r} sorts on columns absent from its schema: {unknown}")
        stray = tuple(column for column in self.base_non_null_columns if column not in self.arrow_schema.names)
        if stray:
            raise StreamSchemaConflictError(f"stream {self.name!r} names base-non-null columns it lacks: {stray}")

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return the schema's columns in write order."""
        return tuple(self.arrow_schema.names)


# --- Forecast provenance ---------------------------------------------------------------------------
# The six columns `layer-lanes.md` section 3 requires on EVERY `kind=forecast` row. They live only on
# the forecast side: a column that is unconditionally NULL is not provenance, it is a placeholder.

FORECAST_PROVENANCE_FIELDS: Final[tuple[pa.Field, ...]] = (
    pa.field("forecast_run_id", pa.string(), nullable=False),
    pa.field("random_seed", pa.int64(), nullable=False),
    pa.field("ensemble_size", pa.int32(), nullable=False),
    pa.field("horizon_days", pa.int16(), nullable=False),
    pa.field("issued_on", pa.date32(), nullable=False),
    pa.field("quantile", pa.float64(), nullable=False),
)

FORECAST_PROVENANCE_COLUMNS: Final[tuple[str, ...]] = tuple(field_.name for field_ in FORECAST_PROVENANCE_FIELDS)

#: The observed grain is a cell-day; a forecast partition holds many rows per cell-day, one per
#: reported quantile and issue day. These three finish the key so the sort before every write is
#: total, because an ordering that leaves ties is not reproducible evidence.
FORECAST_PROVENANCE_GRAIN: Final[tuple[str, ...]] = ("issued_on", "horizon_days", "quantile")


def forecast_schema_for(observed: ParquetStreamSchema) -> ParquetStreamSchema:
    """Derive a lane's forecast contract: its observed columns, in order, plus the six provenance columns."""
    collisions = tuple(name for name in FORECAST_PROVENANCE_COLUMNS if name in observed.arrow_schema.names)
    if collisions:
        raise StreamSchemaConflictError(
            f"stream {observed.name!r} declares forecast provenance column(s) {collisions} on its observed "
            "side; provenance belongs to kind=forecast alone, so the two would disagree on the same name"
        )
    return ParquetStreamSchema(
        name=observed.name,
        arrow_schema=pa.schema([*observed.arrow_schema, *FORECAST_PROVENANCE_FIELDS]),
        sort_columns=observed.sort_columns + FORECAST_PROVENANCE_GRAIN,
        compression=observed.compression,
        base_non_null_columns=observed.base_non_null_columns,
    )


# --- The observed streams this service consumes ------------------------------------------------------

SIGNAL_STREAM: Final = "signal"
FIRE_DETECTIONS_STREAM: Final = "fire-detections"
VEGETATION_STREAM: Final = "vegetation"
DROUGHT_STREAM: Final = "drought"
BURN_SEVERITY_STREAM: Final = "burn-severity"
WEATHER_OBSERVATIONS_STREAM: Final = "weather-observations"
FIRE_RISK_STREAM: Final = "fire-risk"

SIGNAL_SCHEMA: Final = ParquetStreamSchema(
    name=SIGNAL_STREAM,
    arrow_schema=pa.schema(
        [
            pa.field("support_key", pa.string(), nullable=False),
            pa.field("signal_name", pa.string(), nullable=False),
            pa.field("normalized_unit", pa.string(), nullable=False),
            # Nullable because the coarse rungs null it; the base rung always carries it.
            pa.field("cell_id", pa.string(), nullable=True),
            pa.field("observed_day", pa.date32(), nullable=False),
            pa.field("normalized_value", pa.float64(), nullable=False),
            pa.field("observation_count", pa.int64(), nullable=False),
            pa.field("newest_observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("coverage_fraction", pa.float64(), nullable=True),
            pa.field("allowed_client_exposure", pa.bool_(), nullable=True),
            pa.field("cell_longitude", pa.float64(), nullable=False),
            pa.field("cell_latitude", pa.float64(), nullable=False),
        ]
    ),
    sort_columns=("support_key", "signal_name", "normalized_unit", "cell_id", "observed_day"),
    base_non_null_columns=("cell_id",),
)

FIRE_DETECTIONS_SCHEMA: Final = ParquetStreamSchema(
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
    sort_columns=("cell_longitude", "cell_latitude", "observed_day"),
)

VEGETATION_SCHEMA: Final = ParquetStreamSchema(
    name=VEGETATION_STREAM,
    arrow_schema=pa.schema(
        [
            pa.field("cell_id", pa.string(), nullable=True),
            pa.field("grid_name", pa.string(), nullable=False),
            pa.field("metric_name", pa.string(), nullable=False),
            pa.field("metric_unit", pa.string(), nullable=False),
            pa.field("observed_day", pa.date32(), nullable=False),
            pa.field("metric_value", pa.float64(), nullable=False),
            pa.field("observation_checksum", pa.string(), nullable=True),
            pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("release_count", pa.int64(), nullable=False),
            pa.field("allowed_client_exposure", pa.bool_(), nullable=False),
            pa.field("cell_longitude", pa.float64(), nullable=False),
            pa.field("cell_latitude", pa.float64(), nullable=False),
        ]
    ),
    sort_columns=("cell_id", "observed_day"),
    base_non_null_columns=("cell_id", "observation_checksum"),
)

DROUGHT_SCHEMA: Final = ParquetStreamSchema(
    name=DROUGHT_STREAM,
    arrow_schema=pa.schema(
        [
            pa.field("area_id", pa.string(), nullable=False),
            pa.field("valid_date", pa.date32(), nullable=False),
            pa.field("dm_category", pa.int32(), nullable=False),
            pa.field("source_url", pa.string(), nullable=False),
            pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
            # Well-known binary carrying no coordinate-system header; every row is WGS 84.
            pa.field("geom", pa.binary(), nullable=False),
        ]
    ),
    sort_columns=("valid_date", "dm_category"),
)

BURN_SEVERITY_SCHEMA: Final = ParquetStreamSchema(
    name=BURN_SEVERITY_STREAM,
    arrow_schema=pa.schema(
        [
            pa.field("feature_id", pa.string(), nullable=False),
            pa.field("fire_id", pa.string(), nullable=False),
            pa.field("natural_key", pa.string(), nullable=False),
            pa.field("release_identifier", pa.string(), nullable=False),
            pa.field("mapping_revision", pa.string(), nullable=False),
            pa.field("fire_year", pa.int32(), nullable=True),
            pa.field("ignition_date", pa.date32(), nullable=False),
            pa.field("observed_day", pa.date32(), nullable=False),
            pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("fire_name", pa.string(), nullable=True),
            pa.field("fire_type", pa.string(), nullable=True),
            pa.field("assessment_type", pa.string(), nullable=True),
            pa.field("acres", pa.float64(), nullable=True),
            pa.field("severity_class", pa.string(), nullable=True),
            pa.field("dnbr_offset", pa.int32(), nullable=True),
            pa.field("dnbr_standard_deviation", pa.int32(), nullable=True),
            pa.field("nodata_threshold", pa.int32(), nullable=True),
            pa.field("greenness_threshold", pa.int32(), nullable=True),
            pa.field("low_threshold", pa.int32(), nullable=True),
            pa.field("moderate_threshold", pa.int32(), nullable=True),
            pa.field("high_threshold", pa.int32(), nullable=True),
            pa.field("allowed_client_exposure", pa.bool_(), nullable=False),
            pa.field("geom", pa.binary(), nullable=False),
        ]
    ),
    sort_columns=("observed_day", "fire_id"),
)

WEATHER_OBSERVATIONS_SCHEMA: Final = ParquetStreamSchema(
    name=WEATHER_OBSERVATIONS_STREAM,
    arrow_schema=pa.schema(
        [
            pa.field("latitude", pa.float64(), nullable=False),
            pa.field("longitude", pa.float64(), nullable=False),
            pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("observed_day", pa.date32(), nullable=False),
            pa.field("external_id", pa.string(), nullable=True),
            pa.field("temperature_c", pa.float64(), nullable=False),
            pa.field("relative_humidity_pct", pa.float64(), nullable=False),
            pa.field("wind_speed_ms", pa.float64(), nullable=False),
            pa.field("wind_direction_deg", pa.float64(), nullable=True),
            pa.field("precipitation_mm", pa.float64(), nullable=False),
            pa.field("source", pa.string(), nullable=False),
            pa.field("feature_id", pa.string(), nullable=True),
            pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        ]
    ),
    sort_columns=("latitude", "longitude", "observed_at"),
    base_non_null_columns=("external_id", "feature_id", "wind_direction_deg"),
)

# --- The fire-risk stream this service ORIGINATES ------------------------------------------------
# Spec FR-5. Not a copy: no sibling schema exists yet, and `p2d-fire-risk-registration` lands the
# matching `warehouse/schemas/fire_risk.py` on the agri side later. The grain is the fire-detections
# 0.005-degree cell, so a risk row and the detections it was fit on join without a dimension table.
#
# `probability` and `risk_score` are NULLABLE and `refused_reason` carries the why, because an
# out-of-stratum cell is refused, never scored: a fabricated zero would read as "no risk here" and is
# exactly the claim the FR-5 publication gate exists to prevent. Every row still names the artifact
# it came from, so a scored cell and a refused cell are both attributable.

FIRE_RISK_SCHEMA: Final = ParquetStreamSchema(
    name=FIRE_RISK_STREAM,
    arrow_schema=pa.schema(
        [
            pa.field("cell_longitude", pa.float64(), nullable=False),
            pa.field("cell_latitude", pa.float64(), nullable=False),
            pa.field("valid_day", pa.date32(), nullable=False),
            pa.field("probability", pa.float64(), nullable=True),
            pa.field("risk_score", pa.float64(), nullable=True),
            pa.field("stratum", pa.string(), nullable=False),
            pa.field("refused_reason", pa.string(), nullable=True),
            pa.field("model_artifact_sha256", pa.string(), nullable=False),
            *FORECAST_PROVENANCE_FIELDS,
        ]
    ),
    sort_columns=(
        "cell_longitude",
        "cell_latitude",
        "valid_day",
        *FORECAST_PROVENANCE_GRAIN,
    ),
    base_non_null_columns=("stratum", "model_artifact_sha256"),
)

#: Every observed stream this service reads, by slug. `fire-risk` is deliberately absent: it has no
#: observed side, so `observed_stream_schema("fire-risk")` must refuse rather than hand back a
#: forecast contract under an observed name.
OBSERVED_STREAM_SCHEMAS: Final[dict[str, ParquetStreamSchema]] = {
    schema.name: schema
    for schema in (
        SIGNAL_SCHEMA,
        FIRE_DETECTIONS_SCHEMA,
        VEGETATION_SCHEMA,
        DROUGHT_SCHEMA,
        BURN_SEVERITY_SCHEMA,
        WEATHER_OBSERVATIONS_SCHEMA,
    )
}

#: Streams this service ORIGINATES: already forecast-shaped, so `forecast_schema_for` is not applied.
ORIGINATED_STREAM_SCHEMAS: Final[dict[str, ParquetStreamSchema]] = {FIRE_RISK_SCHEMA.name: FIRE_RISK_SCHEMA}


def observed_stream_schema(name: str) -> ParquetStreamSchema:
    """Return the pinned observed contract for `name`, or refuse by naming what is registered."""
    schema = OBSERVED_STREAM_SCHEMAS.get(name)
    if schema is None:
        raise StreamSchemaError(
            f"no observed Parquet schema is pinned for stream {name!r}; "
            f"this service consumes {tuple(sorted(OBSERVED_STREAM_SCHEMAS))}"
        )
    return schema


def stream_schema(name: str, kind: PartitionKind = "observed") -> ParquetStreamSchema:
    """Return one stream-kind's contract: the observed schema, or observed plus provenance for `forecast`."""
    originated = ORIGINATED_STREAM_SCHEMAS.get(name)
    if originated is not None:
        if validate_partition_kind(kind) == "observed":
            raise StreamSchemaError(f"stream {name!r} is forecast-only; it has no observed side to read")
        return originated
    observed = observed_stream_schema(name)
    if validate_partition_kind(kind) == "observed":
        return observed
    return forecast_schema_for(observed)


def registered_stream_names() -> tuple[str, ...]:
    """Return every stream this service pins a schema for, sorted."""
    return tuple(sorted({*OBSERVED_STREAM_SCHEMAS, *ORIGINATED_STREAM_SCHEMAS}))


# The `weather-forecast` lane (FR-12) keeps its schema in its own module and registers itself on
# import; this line is what makes importing THIS module enough to see it.
from plantgeo_ml_service.warehouse import weather_forecast  # noqa: E402, F401
