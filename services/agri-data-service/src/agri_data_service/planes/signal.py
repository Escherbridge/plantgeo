"""DuckDB/Polars serving read for the governed signal plane: observed and forecast Parquet streams.

Layer L3 (planes): may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import
`interface`. This module replaces reading `agri.signal_observation` over tRPC -- it scans the
Hive-partitioned `layer=signal/kind=<observed|forecast>/zoom=NN/year=/month=/day=/part-*.parquet`
layout straight out of object storage via Polars, using `polars_storage_options` as the credential
seam (`pipeline.parquet.objectstore`).

`kind` is a partition, never a column a caller filters after the fact: every public function here
takes `kind` as a required, explicit keyword, scopes its glob to exactly that partition, and stamps
the requested kind back onto every returned row. There is no code path that unions `observed` and
`forecast` frames -- a caller wanting both calls this module twice and keeps the two results apart,
by construction. See `conductor/code_styleguides/layer-lanes.md` section 2.

`zoom` is a partition on the same terms, and the scan targets already name it: `month_prefix` and
`day_prefix` both sit under `zoom=NN/`, so a target list is tier-scoped by construction rather than
by a filter applied afterwards. Every public function takes a `requested_zoom` and resolves it once
through `serving_zoom_tier`; there is no signature here that accepts more than one zoom.

Unlike `kind`, the resolved tier is NOT stamped as a column. The `kind` stamp exists because Polars'
Hive injection is inconsistent across empty and non-empty scans and because callers concatenate two
kinds' frames; no caller ever builds a frame spanning two tiers, so a per-row tier stamp would
disambiguate a situation that cannot arise, at the cost of a column the registered schema does not
have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.config import Settings, settings
from agri_data_service.foundation.parquet.paths import day_prefix, month_prefix
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.pipeline.parquet.objectstore import polars_storage_options
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_STREAM, get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

# A serving read has no legitimate reason to scan more than about a year at once; a caller wanting
# a deeper history should page by year rather than materialise one unbounded frame in memory.
MAX_TIME_WINDOW_DAYS: Final = 366


class SignalPlaneReadError(RuntimeError):
    """Raised when the signal plane cannot be read as the frozen kind-partitioned layout demands."""


@dataclass(frozen=True, slots=True)
class SignalPlaneSource:
    """Where the governed signal plane's Parquet objects are scanned from, and how to authenticate.

    Deliberately independent of the writer's `ObjectStore`: this is a read-only scan surface, and
    Polars performs its own object-store I/O rather than going through `ObjectStoreBackend`. A
    `root_uri` is a fully-formed scheme (`s3://bucket/prefix/` in production, a local directory
    path in a test), always ending in `/`, so every relative partition prefix concatenates onto it
    without a caller needing to know which scheme is in play.
    """

    root_uri: str
    storage_options: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.root_uri.endswith("/"):
            object.__setattr__(self, "root_uri", f"{self.root_uri}/")

    @classmethod
    def from_credentials(cls, credentials: ObjectStoreCredentials, *, prefix: str = "") -> SignalPlaneSource:
        """Build a bucket-backed source; `prefix` matches `Settings.object_store_prefix` when set."""
        normalized = prefix.strip("/")
        root = f"s3://{credentials.bucket}/{normalized}" if normalized else f"s3://{credentials.bucket}"
        return cls(root_uri=root, storage_options=polars_storage_options(credentials))

    @classmethod
    def from_settings(cls, source: Settings | None = None) -> SignalPlaneSource:
        """Build a bucket-backed source from `Settings`, raising when object storage is not configured."""
        resolved = settings if source is None else source
        credentials = resolved.require_object_store()
        return cls.from_credentials(credentials, prefix=resolved.object_store_prefix)


def _polars_schema(kind: PartitionKind) -> pl.Schema:
    """The signal plane's registered Arrow schema FOR THIS KIND, in Polars dtypes for `scan_parquet`.

    Passed explicitly to `scan_parquet` so a month with zero partitions returns an empty, correctly
    typed frame instead of raising -- Polars' own documented behaviour for an otherwise-unmatched
    glob is to fail closed, which is wrong here: a day with no data (not yet landed, or a governed
    absence) is a normal, expected serving answer, not an error.

    Keyed on `kind` because the two contracts stopped being one object: a `kind=forecast` file
    carries the six provenance columns on top of the observed grain (`warehouse/parquet/schema.py`).
    Pinning the observed schema over a forecast file makes Polars refuse the read outright with
    "extra column in file outside of expected schema", so the hint widens exactly as the file does.
    """
    empty = pl.from_arrow(get_stream_schema(SIGNAL_PLANE_STREAM, kind).arrow_schema.empty_table())
    if not isinstance(empty, pl.DataFrame):
        raise SignalPlaneReadError("the signal plane's empty Arrow table did not convert to a Polars DataFrame")
    return empty.schema


def _month_scan_target(source: SignalPlaneSource, kind: PartitionKind, *, zoom: ZoomTier, year: int, month: int) -> str:
    """Return the Hive-partitioned glob for one calendar month of one kind's stream at one tier."""
    return f"{source.root_uri}{month_prefix(SIGNAL_PLANE_STREAM, kind, zoom, year, month)}**/*.parquet"


def _day_scan_target(source: SignalPlaneSource, kind: PartitionKind, *, zoom: ZoomTier, day: date) -> str:
    """Return the Hive-partitioned glob for one exact day of one kind's stream at one tier, no month scan needed."""
    return f"{source.root_uri}{day_prefix(SIGNAL_PLANE_STREAM, kind, zoom, day)}*.parquet"


def _year_months_spanned(first_day: date, last_day: date) -> tuple[tuple[int, int], ...]:
    """Every (year, month) pair `[first_day, last_day]` touches, in order."""
    if last_day < first_day:
        raise SignalPlaneReadError(f"time window {first_day.isoformat()}..{last_day.isoformat()} runs backwards")
    months: list[tuple[int, int]] = []
    cursor_year, cursor_month = first_day.year, first_day.month
    while (cursor_year, cursor_month) <= (last_day.year, last_day.month):
        months.append((cursor_year, cursor_month))
        cursor_month += 1
        if cursor_month > 12:  # noqa: PLR2004 - the calendar's own month count, not a tunable
            cursor_month = 1
            cursor_year += 1
    return tuple(months)


def _scan(  # noqa: PLR0913 - one argument per read-scoping dimension, all keyword-only past `kind`
    source: SignalPlaneSource,
    kind: PartitionKind,
    *,
    targets: Sequence[str],
    first_day: date,
    last_day: date,
    cell_ids: Sequence[str] | None,
    signal_names: Sequence[str] | None,
) -> pl.LazyFrame:
    # Projected back down to the OBSERVED column names so both kinds hand callers one uniform
    # shape; forecast provenance is written but not yet served.
    observed = get_stream_schema(SIGNAL_PLANE_STREAM, "observed")
    frame = pl.scan_parquet(
        list(targets),
        hive_partitioning=True,
        schema=_polars_schema(kind),
        storage_options=dict(source.storage_options),
    ).select(observed.column_names)
    frame = frame.filter(pl.col("observed_day").is_between(first_day, last_day))
    if cell_ids is not None:
        frame = frame.filter(pl.col("cell_id").is_in(list(cell_ids)))
    if signal_names is not None:
        frame = frame.filter(pl.col("signal_name").is_in(list(signal_names)))
    return frame.with_columns(pl.lit(kind).alias("kind"))


def read_signal_value_on_day(  # noqa: PLR0913 - one argument per read-scoping dimension, all keyword-only
    source: SignalPlaneSource,
    *,
    kind: PartitionKind,
    requested_zoom: int,
    day: date,
    cell_ids: Sequence[str] | None = None,
    signal_names: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Return one day's rows of ONE kind at ONE tier, narrowed to the given cells/signals if given.

    `kind="observed"` can only ever return settled measurements; `kind="forecast"` can only ever
    return projected quantiles. A day with nothing written -- not yet landed, or a governed
    absence -- comes back as a zero-row, correctly typed frame, never an error and never a value
    borrowed from the other kind, and never a value borrowed from another rung of the ladder.
    """
    target = _day_scan_target(source, kind, zoom=serving_zoom_tier(requested_zoom), day=day)
    frame = _scan(
        source, kind, targets=(target,), first_day=day, last_day=day, cell_ids=cell_ids, signal_names=signal_names
    )
    return frame.collect()


def read_signal_time_window(  # noqa: PLR0913 - one argument per read-scoping dimension, all keyword-only
    source: SignalPlaneSource,
    *,
    kind: PartitionKind,
    requested_zoom: int,
    first_day: date,
    last_day: date,
    cell_ids: Sequence[str] | None = None,
    signal_names: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Return every row of ONE kind at ONE tier in `[first_day, last_day]`, narrowed to the given cells/signals.

    Bounded to `MAX_TIME_WINDOW_DAYS`: a serving read has no legitimate use scanning more than
    that in one call. Months with no partitions at all are silently absent from the result rather
    than raising -- the same "empty is a normal answer" rule `read_signal_value_on_day` follows.

    Every month in the window resolves to the SAME tier, computed once before the loop. Resolving
    per month would let a ladder change part way through a long window hand back a frame whose cell
    grid changes mid-series, which a `[first_day, last_day]` label would not reveal.
    """
    span_days = (last_day - first_day).days + 1
    if span_days > MAX_TIME_WINDOW_DAYS:
        raise SignalPlaneReadError(
            f"time window {first_day.isoformat()}..{last_day.isoformat()} spans {span_days} days, "
            f"over the {MAX_TIME_WINDOW_DAYS}-day serving-read budget; page by year instead"
        )
    zoom = serving_zoom_tier(requested_zoom)
    targets = tuple(
        _month_scan_target(source, kind, zoom=zoom, year=year, month=month)
        for year, month in _year_months_spanned(first_day, last_day)
    )
    frame = _scan(
        source,
        kind,
        targets=targets,
        first_day=first_day,
        last_day=last_day,
        cell_ids=cell_ids,
        signal_names=signal_names,
    )
    return frame.collect()
