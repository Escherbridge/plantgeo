"""Read a window of governed OBSERVED partitions, refusing any day the producer had not published.

Layer L3. The leakage rule this module exists to enforce, and why an unresolved day refuses rather
than shrinking the window, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Literal

import polars as pl

from plantgeo_ml_service.foundation.parquet_paths import (
    COVERED_PARTITION_STATUSES,
    PartitionKind,
    ZoomTier,
    classify_partition_day,
    month_prefix,
    tier_day_objects,
    try_parse_partition_path,
    validate_zoom_tier,
)
from plantgeo_ml_service.pipeline.duckdb_session import read_parquet_keys
from plantgeo_ml_service.warehouse.lanes import lane_contract
from plantgeo_ml_service.warehouse.streams import stream_schema

if TYPE_CHECKING:
    from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
    from plantgeo_ml_service.pipeline.object_store import ObjectStore

#: A feature window is a training or scoring span, not an archive walk. Roughly eleven years of
#: daily rows, which covers every history floor this service reads except fire-detections' full
#: 9,400-day depth; that one is walked in chunks by its caller rather than in one read.
MAX_WINDOW_DAYS: Final = 4_000

#: Why one read was refused. A named reason, never a bare False: the caller writes it into the
#: `refused_reason` column of whatever it was building, and a caller cannot act on a bool.
ObservedRefusalReason = Literal[
    "window_inverted",
    "window_too_wide",
    "below_history_floor",
    "beyond_publication_lag",
    "day_not_governed",
]


class ObservedReadRefusalError(RuntimeError):
    """A typed refusal to read: the window, the reason, and the days that caused it."""

    def __init__(
        self,
        reason: ObservedRefusalReason,
        message: str,
        *,
        layer: str,
        days: tuple[date, ...] = (),
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.layer = layer
        self.days = days


@dataclass(frozen=True, slots=True)
class ObservedReader:
    """Reads one lane's observed rows out of the bucket, bounded by that lane's own clock."""

    store: ObjectStore
    session: DuckDbSession

    def read_lane_window(
        self,
        layer: str,
        zoom: ZoomTier,
        first_day: date,
        last_day: date,
        *,
        as_of: date,
    ) -> pl.DataFrame:
        """Return every observed row of `layer` in `[first_day, last_day]`, or refuse with a reason.

        `as_of` is the issue day the caller is building for. A day the lane's producer could not
        have published by then is refused rather than skipped: silently narrowing the window turns
        a leakage guard into a data-quality surprise the caller never sees.
        """
        tier = validate_zoom_tier(zoom)
        days = self._validated_window(layer, first_day, last_day, as_of=as_of)
        keys = self._listed_keys(layer, tier, first_day, last_day)
        objects = tier_day_objects(keys, layer=layer, kind=_OBSERVED, zoom=tier)
        statuses = {day: classify_partition_day(day, objects, zoom=tier) for day in days}
        ungoverned = tuple(day for day, status in statuses.items() if status not in COVERED_PARTITION_STATUSES)
        if ungoverned:
            raise ObservedReadRefusalError(
                "day_not_governed",
                f"lane {layer!r} at rung {tier} holds neither a completed partition nor a governed absence "
                f"for {len(ungoverned)} day(s) of the requested window, first {ungoverned[0].isoformat()}",
                layer=layer,
                days=ungoverned,
            )
        data_days = frozenset(day for day, status in statuses.items() if status == "data") & objects.parts
        part_keys = tuple(key for key in keys if _names_a_part_of(key, layer, tier, data_days))
        if not part_keys:
            return _empty_frame(layer)
        table = read_parquet_keys(self.session, part_keys)
        return pl.from_arrow(table)  # type: ignore[return-value]  # a Table always yields a DataFrame

    def _validated_window(self, layer: str, first_day: date, last_day: date, *, as_of: date) -> tuple[date, ...]:
        """Bound the window against the lane's floor, its publication lag and the day budget."""
        contract = lane_contract(layer)
        if last_day < first_day:
            raise ObservedReadRefusalError(
                "window_inverted",
                f"window {first_day.isoformat()}..{last_day.isoformat()} runs backwards",
                layer=layer,
            )
        span = (last_day - first_day).days + 1
        if span > MAX_WINDOW_DAYS:
            raise ObservedReadRefusalError(
                "window_too_wide",
                f"a window of {span} days exceeds the {MAX_WINDOW_DAYS}-day read budget",
                layer=layer,
            )
        if first_day < contract.history_floor:
            raise ObservedReadRefusalError(
                "below_history_floor",
                f"lane {layer!r} begins at {contract.history_floor.isoformat()}; "
                f"{first_day.isoformat()} predates every day it can hold",
                layer=layer,
                days=(first_day,),
            )
        ceiling = contract.settled_through(as_of)
        if last_day > ceiling:
            raise ObservedReadRefusalError(
                "beyond_publication_lag",
                f"lane {layer!r} publishes with a {contract.publication_lag_days}-day lag, so a feature "
                f"issued on {as_of.isoformat()} may read through {ceiling.isoformat()} and no later; "
                f"{last_day.isoformat()} was requested",
                layer=layer,
                days=(last_day,),
            )
        return tuple(first_day + timedelta(days=offset) for offset in range(span))

    def _listed_keys(self, layer: str, zoom: ZoomTier, first_day: date, last_day: date) -> tuple[str, ...]:
        """List only the month prefixes the window touches, so the listing is bounded by the window."""
        keys: list[str] = []
        for year, month in _months_between(first_day, last_day):
            prefix = month_prefix(layer, _OBSERVED, zoom, year, month)
            keys.extend(self.store.list_relative_paths(prefix))
        return tuple(keys)


def _names_a_part_of(key: str, layer: str, zoom: ZoomTier, days: frozenset[date]) -> bool:
    """Return whether one listed key is a part file of this tier on one of the answerable days."""
    parsed = try_parse_partition_path(key)
    if parsed is None:
        return False
    return (parsed.layer, parsed.kind, parsed.zoom) == (layer, _OBSERVED, zoom) and parsed.day in days


def _months_between(first_day: date, last_day: date) -> tuple[tuple[int, int], ...]:
    """Return every (year, month) the inclusive window touches, in order."""
    months: list[tuple[int, int]] = []
    year, month = first_day.year, first_day.month
    while (year, month) <= (last_day.year, last_day.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == _DECEMBER else (year, month + 1)
    return tuple(months)


def _empty_frame(layer: str) -> pl.DataFrame:
    """Return a typed, zero-row frame so a governed-absent window has the same columns as a full one."""
    schema = stream_schema(layer, _OBSERVED)
    return pl.from_arrow(schema.arrow_schema.empty_table())  # type: ignore[return-value]


_OBSERVED: Final[PartitionKind] = "observed"
_DECEMBER: Final = 12
