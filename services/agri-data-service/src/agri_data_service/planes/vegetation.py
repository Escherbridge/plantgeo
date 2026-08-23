"""Serving read for the vegetation (Sentinel-2 NDVI) lane: DuckDB/Polars over the object-store Parquet plane.

Layer L5 planes: may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import
`interface`. See `docs/lanes/vegetation.md` for source system, grain and known-gap evidence, and
`conductor/code_styleguides/layer-lanes.md` section 2 for why `kind` is a partition, never a column
branch, and is never blended.

`zoom` is a partition on the same terms, and the danger it carries is specific to this lane: NDVI is
an INDEX, so four tiers of one cell-day are four different averages of the same reflectance, all in
`[-1, 1]` and all individually plausible. A scan spanning the ladder produces several rows per
(cell_id, observed_day) that disagree by a little, and nothing downstream -- not the schema, not the
sort, not a range check -- can tell that apart from genuine sub-cell variation. Every read therefore
resolves one `requested_zoom` through `serving_zoom_tier` and globs inside that rung alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from agri_data_service.foundation.parquet.paths import (
    MAX_GAP_WINDOW_DAYS,
    validate_partition_kind,
    zoom_prefix,
)
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.warehouse.parquet.schema import get_stream_schema
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA, VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


def _typed_empty_schema(kind: PartitionKind) -> pl.Schema:
    """Return this lane's registered Arrow contract FOR THIS KIND as a Polars schema for `scan_parquet`.

    Keyed on `kind` because the two contracts stopped being one object: a `kind=forecast` file
    carries the six provenance columns on top of the observed grain (`warehouse/parquet/schema.py`).
    Pinning the observed schema over a forecast file makes Polars refuse the read outright with
    "extra column in file outside of expected schema", so the hint widens exactly as the file does.

    The hint is also what lets a `kind` subtree with zero written files (e.g. `kind="forecast"`,
    which this lane has never written -- `docs/lanes/vegetation.md` section 5.2) return a correctly
    typed zero-row frame instead of raising `polars.exceptions.ComputeError` on an empty glob.
    """
    converted = pl.from_arrow(get_stream_schema(VEGETATION_PLANE_STREAM, kind).arrow_schema.empty_table())
    if not isinstance(converted, pl.DataFrame):
        raise TypeError("pl.from_arrow of a pyarrow Table must return a polars DataFrame")
    return converted.schema


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
    zoom: ZoomTier
    observed: pl.DataFrame
    forecast: pl.DataFrame


def vegetation_scan_pattern(*, root: str, kind: PartitionKind, zoom: ZoomTier) -> str:
    """Return the glob `read_vegetation_partition` scans for one kind at one tier, rooted at `root`.

    `root` is everything before the frozen `layer=.../kind=.../zoom=.../` layout:
    `s3://<bucket>/<prefix>` in production (see `bucket_object_root`), or a local directory in tests.
    The returned pattern starts INSIDE `zoom=NN/`, so neither the other kind's bytes nor any other
    rung's are even listed, let alone opened.
    """
    normalized_root = root if root.endswith("/") else f"{root}/"
    stream_at_tier = zoom_prefix(VEGETATION_PLANE_STREAM, validate_partition_kind(kind), zoom)
    return f"{normalized_root}{stream_at_tier}**/*.parquet"


def bucket_object_root(*, credentials: ObjectStoreCredentials, store: ObjectStore) -> str:
    """Return the production scan root: the bucket URI plus the store's own key prefix, if any."""
    return f"s3://{credentials.bucket}/{store.prefix}" if store.prefix else f"s3://{credentials.bucket}/"


def read_vegetation_partition(  # noqa: PLR0913 - one argument per read-scoping dimension, none foldable
    *,
    root: str,
    kind: PartitionKind,
    requested_zoom: int,
    first_day: date,
    last_day: date,
    storage_options: dict[str, str] | None = None,
) -> pl.DataFrame:
    """Read one kind's vegetation rows at one tier in `[first_day, last_day]`; nothing else is opened.

    Empty is a valid, honest answer, not a defect to paper over -- see `_typed_empty_schema`.
    Rows are returned in the schema's own column order, sorted to its registered grain
    (`cell_id`, `observed_day`), matching exactly what `pipeline/lanes/vegetation.py` wrote.
    """
    validated_kind = validate_partition_kind(kind)
    if last_day < first_day:
        raise VegetationServingError(f"window {first_day}..{last_day} runs backwards")
    span_days = (last_day - first_day).days + 1
    if span_days > MAX_GAP_WINDOW_DAYS:
        raise VegetationServingError(f"serving window of {span_days} days exceeds the {MAX_GAP_WINDOW_DAYS}-day budget")
    pattern = vegetation_scan_pattern(root=root, kind=validated_kind, zoom=serving_zoom_tier(requested_zoom))
    frame = pl.scan_parquet(
        pattern,
        hive_partitioning=False,
        schema=_typed_empty_schema(validated_kind),
        storage_options=storage_options,
    )
    # Projected back down to the OBSERVED column names so both kinds hand callers one uniform
    # shape; forecast provenance is written but not yet served (`VegetationWindow` keeps the two
    # frames apart by field name, so a wider forecast side would buy a caller nothing here).
    return (
        frame.filter(pl.col("observed_day").is_between(first_day, last_day))
        .select(list(VEGETATION_PLANE_SCHEMA.column_names))
        .sort(list(VEGETATION_PLANE_SCHEMA.sort_columns))
        .collect()
    )


def read_vegetation_window(
    *,
    root: str,
    requested_zoom: int,
    first_day: date,
    last_day: date,
    storage_options: dict[str, str] | None = None,
) -> VegetationWindow:
    """Read both partition kinds at ONE tier for one window, each kept in its own named field, never blended.

    One tier answers both halves, and `VegetationWindow.zoom` records which. Reading the settled half
    at one rung and the projected half at another would hand a caller two frames whose `cell_id`
    values name grids of different sizes -- and since the field names promise only that the two are
    not blended, nothing would stop a caller from joining them on `cell_id` anyway.
    """
    zoom = serving_zoom_tier(requested_zoom)
    return VegetationWindow(
        first_day=first_day,
        last_day=last_day,
        zoom=zoom,
        observed=read_vegetation_partition(
            root=root,
            kind="observed",
            requested_zoom=zoom,
            first_day=first_day,
            last_day=last_day,
            storage_options=storage_options,
        ),
        forecast=read_vegetation_partition(
            root=root,
            kind="forecast",
            requested_zoom=zoom,
            first_day=first_day,
            last_day=last_day,
            storage_options=storage_options,
        ),
    )
