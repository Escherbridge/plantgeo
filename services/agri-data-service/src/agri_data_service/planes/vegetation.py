"""Serving read for the vegetation (Sentinel-2 NDVI) lane: DuckDB/Polars over the object-store Parquet plane.

Layer L5 planes: may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import
`interface`. See `docs/lanes/vegetation.md` for source system, grain and known-gap evidence, and
`conductor/code_styleguides/layer-lanes.md` section 2 for why `kind` is a partition, never a column
branch, and is never blended.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.paths import (
    MAX_GAP_WINDOW_DAYS,
    stream_prefix,
    validate_partition_kind,
)
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA, VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


def _polars_schema_of(arrow_schema: pa.Schema) -> pl.Schema:
    """Return the Polars schema equivalent of an Arrow schema, narrowed from `pl.from_arrow`'s union return."""
    converted = pl.from_arrow(arrow_schema.empty_table())
    if not isinstance(converted, pl.DataFrame):
        raise TypeError("pl.from_arrow of a pyarrow Table must return a polars DataFrame")
    return converted.schema


# Reused directly from the schema's own registered Arrow contract so an empty scan is typed
# identically to a populated one -- `pl.scan_parquet(..., schema=...)` is what lets a `kind`
# subtree with zero written files (e.g. `kind="forecast"`, which this lane has never written --
# `docs/lanes/vegetation.md` section 5.2) return zero rows instead of raising
# `polars.exceptions.ComputeError` on an empty glob.
_VEGETATION_POLARS_SCHEMA: Final = _polars_schema_of(VEGETATION_PLANE_SCHEMA.arrow_schema)


class VegetationServingError(ValueError):
    """Raised when a serving-read request cannot be answered honestly."""


@dataclass(frozen=True, slots=True)
class VegetationWindow:
    """Settled and forecast vegetation rows for one window, kept as two separate, labeled frames.

    Never merge `.observed` and `.forecast`: `layer-lanes.md` section 2 -- future dates are served
    from `kind=forecast`, settled dates from `kind=observed`, and a reader that blends them loses
    which kind produced which row. Each field name IS the traceability: there is no code path in
    this module that can produce a row whose kind is ambiguous.
    """

    first_day: date
    last_day: date
    observed: pl.DataFrame
    forecast: pl.DataFrame


def vegetation_scan_pattern(*, root: str, kind: PartitionKind) -> str:
    """Return the glob `read_vegetation_partition` scans for one kind, rooted at `root`.

    `root` is everything before the frozen `layer=.../kind=.../` layout: `s3://<bucket>/<prefix>`
    in production (see `bucket_object_root`), or a local directory in tests. The returned pattern
    starts INSIDE `kind=<kind>/`, so the other kind's bytes are never even listed, let alone opened.
    """
    normalized_root = root if root.endswith("/") else f"{root}/"
    return f"{normalized_root}{stream_prefix(VEGETATION_PLANE_STREAM, validate_partition_kind(kind))}**/*.parquet"


def bucket_object_root(*, credentials: ObjectStoreCredentials, store: ObjectStore) -> str:
    """Return the production scan root: the bucket URI plus the store's own key prefix, if any."""
    return f"s3://{credentials.bucket}/{store.prefix}" if store.prefix else f"s3://{credentials.bucket}/"


def read_vegetation_partition(
    *,
    root: str,
    kind: PartitionKind,
    first_day: date,
    last_day: date,
    storage_options: dict[str, str] | None = None,
) -> pl.DataFrame:
    """Read one partition kind's vegetation rows in `[first_day, last_day]`; the other kind is never opened.

    Empty is a valid, honest answer, not a defect to paper over -- see `_VEGETATION_POLARS_SCHEMA`.
    Rows are returned in the schema's own column order, sorted to its registered grain
    (`cell_id`, `observed_day`), matching exactly what `pipeline/lanes/vegetation.py` wrote.
    """
    validated_kind = validate_partition_kind(kind)
    if last_day < first_day:
        raise VegetationServingError(f"window {first_day}..{last_day} runs backwards")
    span_days = (last_day - first_day).days + 1
    if span_days > MAX_GAP_WINDOW_DAYS:
        raise VegetationServingError(f"serving window of {span_days} days exceeds the {MAX_GAP_WINDOW_DAYS}-day budget")
    pattern = vegetation_scan_pattern(root=root, kind=validated_kind)
    frame = pl.scan_parquet(
        pattern,
        hive_partitioning=False,
        schema=_VEGETATION_POLARS_SCHEMA,
        storage_options=storage_options,
    )
    return (
        frame.filter(pl.col("observed_day").is_between(first_day, last_day))
        .select(list(VEGETATION_PLANE_SCHEMA.column_names))
        .sort(list(VEGETATION_PLANE_SCHEMA.sort_columns))
        .collect()
    )


def read_vegetation_window(
    *,
    root: str,
    first_day: date,
    last_day: date,
    storage_options: dict[str, str] | None = None,
) -> VegetationWindow:
    """Read both partition kinds for one window, each kept in its own named field, never blended."""
    return VegetationWindow(
        first_day=first_day,
        last_day=last_day,
        observed=read_vegetation_partition(
            root=root, kind="observed", first_day=first_day, last_day=last_day, storage_options=storage_options
        ),
        forecast=read_vegetation_partition(
            root=root, kind="forecast", first_day=first_day, last_day=last_day, storage_options=storage_options
        ),
    )
