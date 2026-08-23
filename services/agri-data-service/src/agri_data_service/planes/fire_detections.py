"""The DuckDB/Polars serving read for the fire-detections cell-day aggregate.

Layer L3 (planes): may import foundation, method, warehouse, pipeline; may NOT import interface.
See `conductor/code_styleguides/layer-lanes.md` section 2: `kind` is a partition, never a column
branch. Every function here reads exactly one kind per call; blending observed and forecast rows
into one answer, or falling through from one to the other, is done nowhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.paths import stream_prefix
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_SCHEMA, FIRE_DETECTIONS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind

_GLOB_SUFFIX: Final = "**/*.parquet"
KIND_COLUMN: Final = "kind"
_SORT_COLUMNS: Final[tuple[str, ...]] = ("observed_day", "cell_longitude", "cell_latitude")


class FireDetectionsServingError(ValueError):
    """Raised when a serving request cannot be answered honestly (e.g. a window that runs backwards)."""


def _stream_glob(base_uri: str, kind: PartitionKind) -> str:
    """Return the glob pattern for one fire-detections partition kind, reusing the frozen key layout."""
    return f"{base_uri.rstrip('/')}/{stream_prefix(FIRE_DETECTIONS_STREAM, kind)}{_GLOB_SUFFIX}"


def _typed_empty_schema() -> pl.Schema:
    """Mirror the registered Arrow contract as a Polars schema, verified empirically as the escape hatch.

    Without a `schema=` hint, `pl.scan_parquet` on a glob matching zero files raises `ComputeError`
    ("expanded paths were empty") rather than returning an empty frame -- indistinguishable from a real
    failure unless the caller keys off a specific exception message. Every column stays correctly typed
    with or without this hint; passing it just makes "the kind=forecast prefix does not exist yet" and
    "it exists but this window has no rows" collapse into the same, correctly-typed empty result. Gap
    detection (a real absence vs a missing prefix) is `foundation/parquet/paths.py`'s job, done by
    listing keys, never by scanning content from here.
    """
    empty_frame = pl.from_arrow(pa.table(FIRE_DETECTIONS_SCHEMA.arrow_schema.empty_table()))
    if not isinstance(empty_frame, pl.DataFrame):
        raise TypeError("pl.from_arrow of a pyarrow Table unexpectedly returned a Series, not a DataFrame")
    return empty_frame.schema


def scan_fire_detections_kind(
    base_uri: str,
    *,
    kind: PartitionKind,
    storage_options: Mapping[str, str] | None = None,
) -> pl.LazyFrame:
    """Lazily scan ONE kind of the fire-detections stream, never both -- the partition IS the boundary.

    Every row carries an explicit `kind` literal rather than relying on Polars' Hive-partition column
    injection, which is present when files exist and absent from a zero-file scan even with
    `schema=` supplied -- an inconsistency this stamp erases so a caller never special-cases an empty
    scan's shape, and so a row read out of a concatenated multi-kind frame is always traceable to the
    kind it actually came from.
    """
    frame = pl.scan_parquet(
        _stream_glob(base_uri, kind),
        hive_partitioning=True,
        schema=_typed_empty_schema(),
        storage_options=dict(storage_options) if storage_options else {},
    )
    return frame.select(list(FIRE_DETECTIONS_SCHEMA.column_names)).with_columns(pl.lit(kind).alias(KIND_COLUMN))


def read_fire_detections_kind_window(
    base_uri: str,
    *,
    kind: PartitionKind,
    first_day: date,
    last_day: date,
    storage_options: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Materialize one kind's cell-day rows within `[first_day, last_day]`, never touching the other kind."""
    if last_day < first_day:
        raise FireDetectionsServingError(f"window {first_day}..{last_day} runs backwards")
    frame = scan_fire_detections_kind(base_uri, kind=kind, storage_options=storage_options)
    return (
        frame.filter(pl.col("observed_day").is_between(first_day, last_day, closed="both"))
        .sort(list(_SORT_COLUMNS))
        .collect()
    )


@dataclass(frozen=True, slots=True)
class FireDetectionsWindowRequest:
    """A bounded, explicit request: which days, and which side of `as_of_day` reads from which kind."""

    first_day: date
    last_day: date
    as_of_day: date

    def __post_init__(self) -> None:
        if self.last_day < self.first_day:
            raise FireDetectionsServingError(f"window {self.first_day}..{self.last_day} runs backwards")


def read_fire_detections_window(
    base_uri: str,
    request: FireDetectionsWindowRequest,
    *,
    storage_options: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Serve one bounded window: settled days from `kind=observed`, future days from `kind=forecast`.

    The split is `<= as_of_day` (observed) vs `> as_of_day` (forecast), decided ONLY by each day's
    position relative to `as_of_day` -- never by which stream happens to answer, and never a fallback
    from one kind to the other. `request.first_day <= request.as_of_day` and
    `request.last_day > request.as_of_day` are not mutually exclusive but together always cover the
    validated (non-backwards) window, so at least one branch below always contributes a frame.
    """
    frames: list[pl.DataFrame] = []
    if request.first_day <= request.as_of_day:
        frames.append(
            read_fire_detections_kind_window(
                base_uri,
                kind="observed",
                first_day=request.first_day,
                last_day=min(request.last_day, request.as_of_day),
                storage_options=storage_options,
            )
        )
    if request.last_day > request.as_of_day:
        frames.append(
            read_fire_detections_kind_window(
                base_uri,
                kind="forecast",
                first_day=max(request.first_day, request.as_of_day + timedelta(days=1)),
                last_day=request.last_day,
                storage_options=storage_options,
            )
        )
    return pl.concat(frames, how="vertical").sort(list(_SORT_COLUMNS))
