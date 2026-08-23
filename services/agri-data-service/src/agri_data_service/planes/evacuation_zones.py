"""Serving read for the evacuation-zones lane: resolve one "as of" snapshot day, honestly.

Layer L3 planes: may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import
`interface`. See `docs/lanes/evacuation-zones.md` for source-system facts and
`conductor/code_styleguides/layer-lanes.md` section 2 for why `kind` is a partition, never a
column branch, and is never blended.

This lane is a current-state SNAPSHOT, not a time series (`docs/lanes/evacuation-zones.md` section
3): Postgres holds no record of what was published on a past day, so "as of date D" cannot mean
"the true state on D" -- it can only mean "the newest snapshot this lane ever captured at or before
D". This module always says which snapshot actually answered rather than presenting it as same-day
truth, per the life-safety warning in `layer-lanes.md` section 2.

Geometry is WKB with no SRID header; every row's SRID is the fixed schema-level constant
`EVACUATION_ZONES_GEOMETRY_SRID` (4326, `warehouse/schemas/evacuation_zones.py`), out of band and
never re-derived here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import polars as pl

from agri_data_service.foundation.parquet.paths import (
    partition_day_statuses,
    stream_prefix,
    validate_partition_kind,
)
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_SCHEMA, EVACUATION_ZONES_STREAM

if TYPE_CHECKING:
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# The only jurisdiction this lane covers (docs/lanes/evacuation-zones.md section 5). Washington,
# Idaho and western Montana have no equivalent feed, and that absence is structural: there is no
# per-row state flag on `geo.features` to filter on, so a caller states which state its query
# location falls in rather than this module guessing from geometry it does not have.
_COVERED_STATE: Final = "oregon"

EvacuationZonesCoverageStatus = Literal["covered", "no_coverage"]

# The four-way outcome a caller must be able to tell apart. "no_coverage" and "observed" with
# `zone_count == 0` mean OPPOSITE things to someone deciding whether to evacuate -- see the module
# docstring and layer-lanes.md section 2's "wrong-but-plausible is worse than an honest gap."
EvacuationZonesAnswerStatus = Literal["no_coverage", "not_yet_observed", "observed", "conflicted"]

# Pinned so an empty `kind=observed` glob (before this lane's first snapshot) or the permanently
# empty `kind=forecast` subtree (`horizon: none`, docs/lanes/evacuation-zones.md section 7) returns
# a correctly-typed zero-row frame instead of `polars.exceptions.ComputeError` on an empty glob.
# `pl.from_arrow` is typed as returning `DataFrame | Series`; a `pa.Table` input always yields a
# `DataFrame`, and the assert narrows that for mypy rather than asserting it away with a cast.
_evacuation_zones_empty_frame = pl.from_arrow(EVACUATION_ZONES_SCHEMA.arrow_schema.empty_table())
assert isinstance(_evacuation_zones_empty_frame, pl.DataFrame)
_EVACUATION_ZONES_POLARS_SCHEMA: Final = _evacuation_zones_empty_frame.schema


class EvacuationZonesServingError(ValueError):
    """Raised when a serving-read request cannot be answered honestly."""


@dataclass(frozen=True, slots=True)
class EvacuationZonesAsOfAnswer:
    """One answer to "what were Oregon's evacuation zones as of date D" -- typed so a caller cannot
    confuse "no coverage here" with "covered, and quiet."
    """

    requested_as_of: date
    status: EvacuationZonesAnswerStatus
    answered_by_snapshot_day: date | None
    zones: pa.Table
    note: str

    @property
    def zone_count(self) -> int:
        """Row count of `zones`; 0 is a valid, honest answer for `status == "observed"`."""
        return int(self.zones.num_rows)


def classify_evacuation_zones_coverage(state: str) -> EvacuationZonesCoverageStatus:
    """Return whether `state` falls inside this lane's Oregon-only coverage."""
    return "covered" if state.strip().casefold() == _COVERED_STATE else "no_coverage"


def evacuation_zones_scan_pattern(*, root: str, kind: PartitionKind) -> str:
    """Return the glob rooted at `root` for one partition kind's subtree of the frozen layout."""
    normalized_root = root if root.endswith("/") else f"{root}/"
    return f"{normalized_root}{stream_prefix(EVACUATION_ZONES_STREAM, validate_partition_kind(kind))}**/*.parquet"


def bucket_object_root(*, credentials: ObjectStoreCredentials, store: ObjectStore) -> str:
    """Return the production scan root: the bucket URI plus the store's own key prefix, if any."""
    return f"s3://{credentials.bucket}/{store.prefix}" if store.prefix else f"s3://{credentials.bucket}/"


def read_evacuation_zones_forecast(*, root: str, storage_options: dict[str, str] | None = None) -> pa.Table:
    """Scan `kind=forecast` and return whatever is there -- which is always nothing.

    This lane ships no `method/monte_carlo/evacuation_zones.py` (`horizon: none`: an evacuation
    level is a policy decision by an emergency manager, not a physical process to project --
    `docs/lanes/evacuation-zones.md` section 7). The subtree therefore never receives files, and
    this function's honesty is structural, not a hand-rolled shortcut: it genuinely scans the same
    way `kind=observed` is scanned and gets zero rows back because nothing was ever written there.
    """
    frame = pl.scan_parquet(
        evacuation_zones_scan_pattern(root=root, kind="forecast"),
        hive_partitioning=False,
        schema=_EVACUATION_ZONES_POLARS_SCHEMA,
        storage_options=storage_options,
    ).collect()
    return conform_to_stream_schema(frame.to_arrow(), EVACUATION_ZONES_SCHEMA)


def _newest_answerable_day(
    store: ObjectStore, *, history_floor: date, as_of: date
) -> tuple[date, PartitionDayStatus] | None:
    """Return the newest day at or before `as_of` carrying data or a governed absence, by listing alone.

    Never opens a file (`layer-lanes.md` section 4: "gap detection that opens files has misused the
    layout"). A day the cron simply did not run is skipped over silently -- this lane has no
    backfill path (`pipeline/parquet/AGENTS.md`), so "newest at or before D" is the only honest
    reading of a snapshot-only source.
    """
    keys = store.list_partition_keys(EVACUATION_ZONES_STREAM, "observed")
    statuses = partition_day_statuses(
        layer=EVACUATION_ZONES_STREAM, kind="observed", first_day=history_floor, last_day=as_of, keys=keys
    )
    candidates = [(day, status) for day, status in statuses.items() if status != "missing"]
    return max(candidates, key=lambda pair: pair[0]) if candidates else None


def _read_observed_day(*, root: str, day: date, storage_options: dict[str, str] | None) -> pa.Table:
    """Read one day's `kind=observed` partition as one table, however many part files it spans."""
    frame = (
        pl.scan_parquet(
            evacuation_zones_scan_pattern(root=root, kind="observed"),
            hive_partitioning=False,
            schema=_EVACUATION_ZONES_POLARS_SCHEMA,
            storage_options=storage_options,
        )
        .filter(pl.col("snapshot_day") == day)
        .select(list(EVACUATION_ZONES_SCHEMA.column_names))
        .sort(list(EVACUATION_ZONES_SCHEMA.sort_columns))
        .collect()
    )
    return conform_to_stream_schema(frame.to_arrow(), EVACUATION_ZONES_SCHEMA)


def resolve_evacuation_zones_as_of(  # noqa: PLR0913
    store: ObjectStore,
    *,
    root: str,
    as_of: date,
    state: str,
    history_floor: date,
    storage_options: dict[str, str] | None = None,
) -> EvacuationZonesAsOfAnswer:
    """Resolve "as of date D" to the newest snapshot at or before D, or an honest reason there is none.

    Never interpolates between snapshots and never presents the newest snapshot as same-day truth
    for an earlier `as_of`: `answered_by_snapshot_day` always names which day actually answered, and
    `note` spells out the gap between requested and answering day when they differ.
    `history_floor` bounds the backward search; this module does not invent one -- the caller
    supplies the lane's real ingestion-start floor.
    """
    if history_floor > as_of:
        raise EvacuationZonesServingError(f"history_floor {history_floor} must not be after as_of {as_of}")

    empty = EVACUATION_ZONES_SCHEMA.arrow_schema.empty_table()

    if classify_evacuation_zones_coverage(state) == "no_coverage":
        return EvacuationZonesAsOfAnswer(
            requested_as_of=as_of,
            status="no_coverage",
            answered_by_snapshot_day=None,
            zones=empty,
            note=(
                f"{state!r} is outside evacuation-zones' Oregon-only coverage "
                "(docs/lanes/evacuation-zones.md section 5): no equivalent feed exists for "
                "Washington, Idaho or western Montana. This is a structural absence of coverage, "
                "never to be confused with an in-coverage query that finds zero active zones."
            ),
        )

    resolved = _newest_answerable_day(store, history_floor=history_floor, as_of=as_of)
    if resolved is None:
        return EvacuationZonesAsOfAnswer(
            requested_as_of=as_of,
            status="not_yet_observed",
            answered_by_snapshot_day=None,
            zones=empty,
            note=(
                f"no evacuation-zones snapshot exists between {history_floor} and {as_of}; this "
                "lane has no backfill path (pipeline/parquet/AGENTS.md), so a day the cron missed "
                "is lost, not interpolated."
            ),
        )
    answering_day, day_status = resolved

    if day_status == "conflict":
        return EvacuationZonesAsOfAnswer(
            requested_as_of=as_of,
            status="conflicted",
            answered_by_snapshot_day=answering_day,
            zones=empty,
            note=(
                f"{answering_day} carries both a data partition and a governed-absence marker -- "
                "an admin-only anomaly (layer-lanes.md section 4). Refusing to silently pick a "
                "side for a life-safety layer."
            ),
        )

    if day_status == "absent":
        stale_note = "" if answering_day == as_of else f" Newest snapshot at or before {as_of}, not a same-day reading."
        return EvacuationZonesAsOfAnswer(
            requested_as_of=as_of,
            status="observed",
            answered_by_snapshot_day=answering_day,
            zones=empty,
            note=(
                f"{answering_day} is a governed absence: zero currently-published Oregon OEM zones "
                "-- a normal quiet-season state, not a defect." + stale_note
            ),
        )

    zones = _read_observed_day(root=root, day=answering_day, storage_options=storage_options)
    trailer = "" if answering_day == as_of else f", the newest one at or before {as_of}"
    return EvacuationZonesAsOfAnswer(
        requested_as_of=as_of,
        status="observed",
        answered_by_snapshot_day=answering_day,
        zones=zones,
        note=f"answered by the {answering_day} snapshot{trailer} ({zones.num_rows} zone(s)).",
    )
