"""Parquet schema registry: one Arrow schema, grain sort key, and codec per object stream.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
See `AGENTS.md` in this directory for the registration convention and the signal-plane evidence.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Final, Literal

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.paths import validate_layer_slug

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


def get_stream_schema(name: str) -> ParquetStreamSchema:
    """Return the registered contract for `name`, autoloading the lane's schema module if needed."""
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
                pa.field("cell_id", pa.string(), nullable=False),
                pa.field("observed_day", pa.date32(), nullable=False),
                pa.field("normalized_value", pa.float64(), nullable=False),
                pa.field("observation_count", pa.int64(), nullable=False),
                pa.field("newest_observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("coverage_fraction", pa.float64(), nullable=True),
                pa.field("allowed_client_exposure", pa.bool_(), nullable=True),
            ]
        ),
        sort_columns=SIGNAL_PLANE_GRAIN,
    )
)
