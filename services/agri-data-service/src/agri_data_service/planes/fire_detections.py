"""The DuckDB/Polars serving read for the fire-detections cell-day aggregate.

Layer L3 (planes): may import foundation, method, warehouse, pipeline; may NOT import interface.
See `conductor/code_styleguides/layer-lanes.md` section 2: `kind` is a partition, never a column
branch. Every function here reads exactly one kind per call; blending observed and forecast rows
into one answer, or falling through from one to the other, is done nowhere in this module.

`zoom` is a partition on the same terms. Every public function takes a `requested_zoom` -- the map
zoom a viewport is actually at -- and resolves it ONCE through `serving_zoom_tier` into the single
published rung at or below it, then globs inside that rung's prefix alone. The glob deliberately
starts inside `zoom=NN/` rather than inside `kind=<kind>/`: the wider prefix matches all four tiers,
so one scan would stack four resolutions of the same cell-day into one frame, quadruple the
detection counts, and look entirely well-formed while doing it. See `planes/AGENTS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.paths import zoom_prefix
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.warehouse.parquet.schema import get_stream_schema
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_SCHEMA, FIRE_DETECTIONS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

_GLOB_SUFFIX: Final = "**/*.parquet"
KIND_COLUMN: Final = "kind"
_SORT_COLUMNS: Final[tuple[str, ...]] = ("observed_day", "cell_longitude", "cell_latitude")


class FireDetectionsServingError(ValueError):
    """Raised when a serving request cannot be answered honestly (e.g. a window that runs backwards)."""


def _zoom_tier_glob(base_uri: str, kind: PartitionKind, zoom: ZoomTier) -> str:
    """Return the glob covering exactly ONE tier of one partition kind, never the whole ladder."""
    return f"{base_uri.rstrip('/')}/{zoom_prefix(FIRE_DETECTIONS_STREAM, kind, zoom)}{_GLOB_SUFFIX}"


def _typed_empty_schema(kind: PartitionKind) -> pl.Schema:
    """Mirror the registered Arrow contract FOR THIS KIND as a Polars schema, the empirical escape hatch.

    Keyed on `kind` because the two contracts stopped being one object: a `kind=forecast` file carries
    the six provenance columns on top of the observed grain (`warehouse/parquet/schema.py`). Pinning
    the observed schema over a forecast file makes Polars refuse the read outright with "extra column
    in file outside of expected schema", so the hint has to widen exactly as the file does.

    Without a `schema=` hint, `pl.scan_parquet` on a glob matching zero files raises `ComputeError`
    ("expanded paths were empty") rather than returning an empty frame -- indistinguishable from a real
    failure unless the caller keys off a specific exception message. Every column stays correctly typed
    with or without this hint; passing it just makes "the kind=forecast prefix does not exist yet" and
    "it exists but this window has no rows" collapse into the same, correctly-typed empty result. Gap
    detection (a real absence vs a missing prefix) is `foundation/parquet/paths.py`'s job, done by
    listing keys, never by scanning content from here.
    """
    empty_frame = pl.from_arrow(pa.table(get_stream_schema(FIRE_DETECTIONS_STREAM, kind).arrow_schema.empty_table()))
    if not isinstance(empty_frame, pl.DataFrame):
        raise TypeError("pl.from_arrow of a pyarrow Table unexpectedly returned a Series, not a DataFrame")
    return empty_frame.schema


def scan_fire_detections_kind(
    base_uri: str,
    *,
    kind: PartitionKind,
    requested_zoom: int,
    storage_options: Mapping[str, str] | None = None,
) -> pl.LazyFrame:
    """Lazily scan ONE kind at ONE tier, never both kinds and never two rungs -- the partition IS the boundary.

    Every row carries an explicit `kind` literal rather than relying on Polars' Hive-partition column
    injection, which is present when files exist and absent from a zero-file scan even with
    `schema=` supplied -- an inconsistency this stamp erases so a caller never special-cases an empty
    scan's shape, and so a row read out of a concatenated multi-kind frame is always traceable to the
    kind it actually came from.

    No matching `zoom` column is stamped, and that asymmetry is deliberate: `kind` frames get
    concatenated (`read_fire_detections_window` stacks a settled and a projected half), so a row
    needs to say which kind produced it, while a frame spanning two tiers is never built anywhere,
    so a per-row tier stamp would disambiguate nothing that can occur.
    """
    frame = pl.scan_parquet(
        _zoom_tier_glob(base_uri, kind, serving_zoom_tier(requested_zoom)),
        hive_partitioning=True,
        schema=_typed_empty_schema(kind),
        storage_options=dict(storage_options) if storage_options else {},
    )
    # Projected back down to the OBSERVED columns so both kinds hand callers one uniform shape --
    # `read_fire_detections_as_of_window` concatenates an observed and a forecast frame and a wider
    # forecast side would refuse to stack. Forecast provenance is therefore written but not yet
    # served: surfacing `quantile` means deciding what a multi-quantile cell-day looks like to a
    # reader, which is a serving decision this lookup deliberately does not make.
    return frame.select(list(FIRE_DETECTIONS_SCHEMA.column_names)).with_columns(pl.lit(kind).alias(KIND_COLUMN))


def read_fire_detections_kind_window(  # noqa: PLR0913 - one argument per read-scoping dimension, none foldable
    base_uri: str,
    *,
    kind: PartitionKind,
    requested_zoom: int,
    first_day: date,
    last_day: date,
    storage_options: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Materialize one kind's cell-day rows within `[first_day, last_day]`, never touching the other kind."""
    if last_day < first_day:
        raise FireDetectionsServingError(f"window {first_day}..{last_day} runs backwards")
    frame = scan_fire_detections_kind(
        base_uri, kind=kind, requested_zoom=requested_zoom, storage_options=storage_options
    )
    return (
        frame.filter(pl.col("observed_day").is_between(first_day, last_day, closed="both"))
        .sort(list(_SORT_COLUMNS))
        .collect()
    )


@dataclass(frozen=True, slots=True)
class FireDetectionsWindowRequest:
    """A bounded, explicit request: which days, which zoom, and which side of `as_of_day` reads from which kind."""

    first_day: date
    last_day: date
    as_of_day: date
    requested_zoom: int

    def __post_init__(self) -> None:
        if self.last_day < self.first_day:
            raise FireDetectionsServingError(f"window {self.first_day}..{self.last_day} runs backwards")
        # Resolved eagerly so an off-scale zoom is refused at construction, next to the backwards
        # window -- not four frames later, half way through a scan that has already opened files.
        serving_zoom_tier(self.requested_zoom)

    @property
    def zoom_tier(self) -> ZoomTier:
        """The single published rung answering `requested_zoom`; both halves of the split read it."""
        return serving_zoom_tier(self.requested_zoom)


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

    Both halves read `request.zoom_tier`, the one rung the request resolved to. Stacking a settled
    half read at one resolution onto a projected half read at another would produce a frame whose
    cell grid changes part way along the time axis, which no `kind` stamp would reveal.
    """
    frames: list[pl.DataFrame] = []
    if request.first_day <= request.as_of_day:
        frames.append(
            read_fire_detections_kind_window(
                base_uri,
                kind="observed",
                requested_zoom=request.requested_zoom,
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
                requested_zoom=request.requested_zoom,
                first_day=max(request.first_day, request.as_of_day + timedelta(days=1)),
                last_day=request.last_day,
                storage_options=storage_options,
            )
        )
    return pl.concat(frames, how="vertical").sort(list(_SORT_COLUMNS))
