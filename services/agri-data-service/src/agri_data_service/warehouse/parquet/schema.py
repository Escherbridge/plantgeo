"""Parquet schema registry: one Arrow schema, grain sort key, and codec per object stream.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
See `AGENTS.md` in this directory for the registration convention and the signal-plane evidence.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Final, Literal

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.paths import (
    PartitionKind,
    validate_layer_slug,
    validate_partition_kind,
)
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    register_tier_derivation,
)

ParquetCompression = Literal["zstd", "snappy"]

# Measured on July 2026 of the signal plane: 695,338 B zstd vs 874,945 B snappy. RUNBOOK section 0.22.6.
DEFAULT_PARQUET_COMPRESSION: Final[ParquetCompression] = "zstd"

# A lane registers its schema by defining it in this package under its own slug; see AGENTS.md.
LANE_SCHEMA_PACKAGE: Final = "agri_data_service.warehouse.schemas"


class StreamSchemaError(LookupError):
    """Raised when a stream has no registered Parquet schema and none can be autoloaded."""


class StreamSchemaConflictError(ValueError):
    """Raised when a stream name is registered twice with different storage contracts."""


@dataclass(frozen=True, slots=True)
class ParquetStreamSchema:
    """The storage contract for one object stream: Arrow schema, grain sort key, and codec."""

    name: str
    arrow_schema: pa.Schema
    sort_columns: tuple[str, ...]
    compression: ParquetCompression = DEFAULT_PARQUET_COMPRESSION

    def __post_init__(self) -> None:
        validate_layer_slug(self.name)
        if not self.sort_columns:
            raise ValueError(f"stream {self.name!r} must declare the grain it is sorted to before writing")
        unknown = tuple(column for column in self.sort_columns if column not in self.arrow_schema.names)
        if unknown:
            raise ValueError(f"stream {self.name!r} sorts on columns absent from its schema: {unknown}")

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return the schema's columns in write order."""
        return tuple(self.arrow_schema.names)


_REGISTRY: Final[dict[str, ParquetStreamSchema]] = {}

# Derived `kind=forecast` contracts, cached per stream. Never registered: a lane declares its
# OBSERVED columns and nothing else, and the forecast side is computed from them.
_FORECAST_REGISTRY: Final[dict[str, ParquetStreamSchema]] = {}


# --- Forecast provenance ------------------------------------------------------------------------
# The six columns `conductor/code_styleguides/layer-lanes.md` section 3 requires on EVERY
# `kind=forecast` row. They live only on the forecast side, per RUNBOOK section 0.28.1 decision 1:
# carrying them nullable on the observed side would make six unconditionally-NULL columns, and
# "a column that is unconditionally NULL is not provenance, it is a placeholder"
# (`warehouse/schemas/weather_observations.py:71-75`). AGENTS.md carries the type evidence.

FORECAST_PROVENANCE_FIELDS: Final[tuple[pa.Field, ...]] = (
    pa.field("forecast_run_id", pa.string(), nullable=False),
    pa.field("random_seed", pa.int64(), nullable=False),
    pa.field("ensemble_size", pa.int32(), nullable=False),
    pa.field("horizon_days", pa.int16(), nullable=False),
    pa.field("issued_on", pa.date32(), nullable=False),
    pa.field("quantile", pa.float64(), nullable=False),
)

FORECAST_PROVENANCE_COLUMNS: Final[tuple[str, ...]] = tuple(field.name for field in FORECAST_PROVENANCE_FIELDS)

# The observed grain is a cell-DAY; a forecast partition holds many rows per cell-day -- one per
# reported quantile, and potentially per issue day. These three finish the key so the sort before
# every write is total, because an ordering that leaves ties is not reproducible evidence.
FORECAST_PROVENANCE_GRAIN: Final[tuple[str, ...]] = ("issued_on", "horizon_days", "quantile")


def forecast_stream_schema(observed: ParquetStreamSchema) -> ParquetStreamSchema:
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
    )


def stream_schema_module(name: str) -> str:
    """Return the module a lane's schema is autoloaded from: slug `fire-detections` maps to `fire_detections`."""
    return f"{LANE_SCHEMA_PACKAGE}.{validate_layer_slug(name).replace('-', '_')}"


def register_stream_schema(spec: ParquetStreamSchema) -> ParquetStreamSchema:
    """Register `spec` under its own name; re-registering an identical contract is a no-op."""
    existing = _REGISTRY.get(spec.name)
    if existing is not None and existing != spec:
        raise StreamSchemaConflictError(f"stream {spec.name!r} is already registered with a different storage contract")
    _REGISTRY[spec.name] = spec
    return spec


def observed_stream_schema(name: str) -> ParquetStreamSchema:
    """Return the registered observed contract for `name`, autoloading the lane's schema module if needed."""
    registered = _REGISTRY.get(name)
    if registered is not None:
        return registered
    module_name = stream_schema_module(name)
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise StreamSchemaError(
            f"no Parquet schema registered for stream {name!r}; define one in {module_name}"
        ) from exc
    autoloaded = _REGISTRY.get(name)
    if autoloaded is None:
        raise StreamSchemaError(f"{module_name} imported but registered no schema named {name!r}")
    return autoloaded


def get_stream_schema(name: str, kind: PartitionKind = "observed") -> ParquetStreamSchema:
    """Return one stream-kind's contract: the observed schema, or observed plus provenance for `forecast`."""
    observed = observed_stream_schema(name)
    if validate_partition_kind(kind) == "observed":
        return observed
    cached = _FORECAST_REGISTRY.get(name)
    if cached is None:
        cached = _FORECAST_REGISTRY.setdefault(name, forecast_stream_schema(observed))
    return cached


def registered_stream_names() -> tuple[str, ...]:
    """Return every stream registered so far, sorted; lanes not yet imported do not appear."""
    return tuple(sorted(_REGISTRY))


# --- The signal plane -------------------------------------------------------------------------
# Ten columns, measured and decided in RUNBOOK section 0.22.4. `min_value`/`max_value`/`avg_value`
# are deliberately absent: they equalled `normalized_value` on 100% of 701,257 measured rows and
# cost 3.81x in file size. Do not re-add them. AGENTS.md carries the nullability evidence.

SIGNAL_PLANE_STREAM: Final = "signal"

SIGNAL_PLANE_GRAIN: Final[tuple[str, ...]] = (
    "support_key",
    "signal_name",
    "normalized_unit",
    "cell_id",
    "observed_day",
)

SIGNAL_PLANE_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=SIGNAL_PLANE_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("support_key", pa.string(), nullable=False),
                pa.field("signal_name", pa.string(), nullable=False),
                pa.field("normalized_unit", pa.string(), nullable=False),
                # NULLABLE because the coarse rungs null it: a coarse cell spans many source
                # cells and can honestly name none of them. The base z13 rung always carries it.
                pa.field("cell_id", pa.string(), nullable=True),
                pa.field("observed_day", pa.date32(), nullable=False),
                pa.field("normalized_value", pa.float64(), nullable=False),
                pa.field("observation_count", pa.int64(), nullable=False),
                pa.field("newest_observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("coverage_fraction", pa.float64(), nullable=True),
                pa.field("allowed_client_exposure", pa.bool_(), nullable=True),
                # Cell position from `agri.spatial_cell.centroid`, the representative point of the
                # grid this cell belongs to. Holds the cell ORIGIN's centroid, not any single
                # observation's location. The 2026-08-23 "1,965 of 1,965 production rows" figure this
                # comment used to cite was a pre-deploy reference measurement, not a claim about
                # already-published Parquet objects, and got mis-read as one -- CORRECTED 2026-09-04:
                # every `layer=signal/kind=observed` base-rung object written before commit `8ce71fd`
                # (2026-08-24, the commit that added these two fields to both this schema and
                # `sql/pipeline/signal_plane_day_export.sql` in the same change) structurally lacks
                # both columns and needs re-export. See `AGENTS.md`, "The signal plane", for the full
                # mechanism, the owed re-export, and the `agri.spatial_cell` dependency this join adds.
                pa.field("cell_longitude", pa.float64(), nullable=False),
                pa.field("cell_latitude", pa.float64(), nullable=False),
            ]
        ),
        sort_columns=SIGNAL_PLANE_GRAIN,
    )
)

SIGNAL_PLANE_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=SIGNAL_PLANE_STREAM,
        strategy=GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            # Coarse grain: one row per support/signal/unit/day per coarsened grid cell. cell_id is
            # unique to one base cell (replaced by the coordinates as the spatial key) so no merged
            # row can honestly name one.
            key_columns=("support_key", "signal_name", "normalized_unit", "observed_day"),
            aggregations=(
                ColumnAggregation("cell_id", "null"),  # unique to one base cell, no honest merge
                ColumnAggregation("normalized_value", "mean"),  # intensive measurement: signal average
                ColumnAggregation("observation_count", "sum"),  # additive: total observations in merged cells
                ColumnAggregation("newest_observed_at", "max"),  # newest reading among merged cells
                ColumnAggregation("coverage_fraction", "mean"),  # intensive: average coverage across cells
                ColumnAggregation("allowed_client_exposure", "all"),  # gate: coarse cell exposed only if all were
            ),
        ),
        # Relaxed to nullable ONLY so the coarse rungs above may null it. Named here so a NULL at
        # the base rung still fails the write loudly, as it did before the zoom axis.
        base_non_null_columns=("cell_id",),
    )
)
