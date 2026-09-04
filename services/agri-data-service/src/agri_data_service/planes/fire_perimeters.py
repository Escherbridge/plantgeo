"""The `fire-perimeters` lane's Polars serving read: resolve one "as of" snapshot day, honestly, then
read however many `part-N` files that day wrote.

Layer L3: may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import `interface`.
`geometry_wkb` is standard WKB with no SRID header -- every row is SRID 4326 out of band
(`warehouse/schemas/fire_perimeters.py`), never read off the bytes. This lane declares
`horizon: none` (`docs/lanes/fire-perimeters.md` #7) and writes only `kind=observed`, so this
module never accepts a `kind` argument -- there is no `kind=forecast` partition to point one at.

RE-REGISTERED 2026-09-04 from `daily_series` to `static_lookup` (`warehouse/schemas/fire_perimeters.py`).
This lane is a current-state SNAPSHOT, not a time series: `geo.features` holds no record of what was
published on a past day, so "as of date D" cannot mean "the true state on D" -- it can only mean "the
newest snapshot this lane ever captured at or before D", the identical resolution rule
`planes/evacuation_zones.py` already proves for the same current-state shape read off the same table.
`resolve_fire_perimeters_as_of` is the entry point that performs it; `read_fire_perimeters_day` stays
beneath it as the exact-day reader for one already-resolved snapshot day.

`observed_day` -- the INCIDENT's own date, distinct from `snapshot_day` the VERSION stamp -- survives
per row and is nullable. `resolve_fire_perimeters_as_of` filters the resolved snapshot in-frame on it
with `observed_day IS NULL OR observed_day <= as_of`, exactly the rule
`src/lib/map/tile-layer-date-filter.ts` applies client-side against Martin's tiles today: an undated
incident is kept at every date rather than hidden, and a dated one stays visible from its own day
forward rather than only on the one day it was reported.

`zoom` is the opposite case and IS required: four rungs are published, so a request that did not name
one would have to guess, and the guess would show up as a perimeter drawn at a resolution nobody
asked for rather than as an error. A day listed across the whole ladder would also return one
incident once per rung, and `FIRE_PERIMETERS_GRAIN` sorting would file the copies next to each other
where they read as an incident that was re-reported, not as one that was re-generalised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import polars as pl

from agri_data_service.foundation.parquet.paths import (
    UNFILLED_PARTITION_STATUSES,
    completed_partition_days,
    partition_day_statuses,
    try_parse_partition_path,
)
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.warehouse.schemas.fire_perimeters import (
    FIRE_PERIMETERS_GRAIN,
    FIRE_PERIMETERS_SCHEMA,
    FIRE_PERIMETERS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# The only stream this lane ever writes (docs/lanes/fire-perimeters.md #7). Kept as a private
# constant rather than a `kind` parameter so a caller cannot even ask for a partition that will
# never exist.
_OBSERVED_KIND: Final = "observed"

# The three-way outcome `resolve_fire_perimeters_as_of` can return. No `no_coverage` branch: unlike
# evacuation-zones' Oregon-only feed, WFIGS `_Current` has no jurisdictional restriction this lane
# encodes, so every request is either answerable, not yet answerable, or an admin-only conflict.
FirePerimetersAnswerStatus = Literal["not_yet_observed", "observed", "conflicted"]


class FirePerimetersServingError(ValueError):
    """Raised when a serving-read request cannot be answered honestly."""


@dataclass(frozen=True, slots=True)
class FirePerimetersAsOfAnswer:
    """One answer to "which WFIGS incidents were current as of date D" -- typed so a caller cannot
    confuse "no snapshot exists yet" with "observed, and empty."
    """

    requested_as_of: date
    status: FirePerimetersAnswerStatus
    answered_by_snapshot_day: date | None
    answered_by_zoom_tier: ZoomTier
    perimeters: pl.DataFrame
    note: str

    @property
    def perimeter_count(self) -> int:
        """Row count of `perimeters`; 0 is a valid, honest answer for `status == "observed"`."""
        return int(self.perimeters.height)


def fire_perimeters_base_uri(credentials: ObjectStoreCredentials, store: ObjectStore) -> str:
    """Return the `s3://bucket/prefix` root a production caller passes as `read_fire_perimeters_day`'s `base_uri`.

    Built from the same credentials and the same `ObjectStore` prefix the writer used, so serving
    reads the writer's own bucket rather than a second, hand-typed copy of the endpoint/bucket/prefix.
    """
    return f"s3://{credentials.bucket}/{store.prefix}"


def read_fire_perimeters_day(
    store: ObjectStore,
    *,
    day: date,
    requested_zoom: int,
    base_uri: str,
    storage_options: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Read every WFIGS incident this lane wrote for one UTC day at one tier, sorted to the registered grain.

    `store.list_partition_keys` -- the same listing gap detection already uses -- discovers every
    `part-N.parquet` file for `day`, however many the exporter's geometry-byte spillover produced
    (`pipeline/lanes/fire_perimeters.py`); they are read in one `pl.scan_parquet` call and returned
    as one table, so a caller never has to know a day was ever split. A day with no listed part
    files -- a genuinely quiet day, a governed absence, or any date not yet reached, since this
    lane's horizon is `none` -- answers with zero rows of the canonical schema. There is no
    "nearest available day" fallback anywhere in this function: `kind=observed` is the only stream
    this lane writes, and a request for a day nothing was written for gets that day's honest empty
    answer, never a silent read of whatever the newest observed day happens to hold.

    `storage_options` is the dict `pipeline.parquet.objectstore.polars_storage_options(credentials)`
    returns for a real bucket; pass `None` (or omit it) when `base_uri` is a local path, e.g. in a
    test. `base_uri` for a production caller is `fire_perimeters_base_uri(credentials, store)`.
    """
    part_keys = _observed_day_part_keys(store, serving_zoom_tier(requested_zoom), day)
    if not part_keys:
        return _empty_fire_perimeters_frame()
    root = base_uri.rstrip("/")
    uris = [f"{root}/{key}" for key in part_keys]
    options = dict(storage_options) if storage_options else None
    frame = pl.scan_parquet(uris, storage_options=options).collect()
    return frame.sort(list(FIRE_PERIMETERS_GRAIN))


def _observed_day_part_keys(store: ObjectStore, zoom: ZoomTier, day: date) -> tuple[str, ...]:
    """Return every `part-N.parquet` relative key written for exactly this UTC day at one tier, ascending.

    `list_partition_keys` also returns any `absent.json` governed-absence marker for the month;
    `try_parse_partition_path` returns `None` for one of those, which is what filters it out here
    -- an absence marker is not a source of rows, and its presence or absence changes nothing about
    what this function answers.

    A day whose parts exist without a completion marker is an upload killed part-way through and
    answers as an empty tuple -- its parts are a prefix of the day, not the day. The caller already
    treats an empty tuple as zero rows, so no new branch is needed.
    """
    candidates = list(
        store.list_partition_keys(FIRE_PERIMETERS_STREAM, _OBSERVED_KIND, zoom, year=day.year, month=day.month)
    )
    completed = completed_partition_days(candidates, layer=FIRE_PERIMETERS_STREAM, kind=_OBSERVED_KIND, zoom=zoom)
    if day not in completed:
        return ()
    return tuple(
        sorted(key for key in candidates if (parsed := try_parse_partition_path(key)) is not None and parsed.day == day)
    )


def _empty_fire_perimeters_frame() -> pl.DataFrame:
    """Zero rows, correctly typed: the honest answer for a day with no part files, future dates included."""
    empty = pl.from_arrow(FIRE_PERIMETERS_SCHEMA.arrow_schema.empty_table())
    if not isinstance(empty, pl.DataFrame):  # pragma: no cover - `from_arrow` on a `pa.Table` always returns one.
        raise TypeError("polars.from_arrow of an empty pyarrow Table did not return a DataFrame")
    return empty


def _newest_answerable_snapshot_day(
    store: ObjectStore, *, zoom: ZoomTier, history_floor: date, as_of: date
) -> tuple[date, PartitionDayStatus] | None:
    """Return the newest day at or before `as_of` carrying data or a governed absence AT ONE TIER, by listing alone.

    Mirrors `planes/evacuation_zones.py::_newest_answerable_day` exactly -- same primitive
    (`partition_day_statuses`), same exclusion rule, same reasoning: this lane has no backfill path,
    so a day the cron simply did not run is skipped over silently rather than invented, and "newest at
    or before D" is the only honest reading of a snapshot-only source. A "conflict" day stays a
    candidate deliberately -- `resolve_fire_perimeters_as_of` answers it with an explicit `conflicted`
    status rather than silently falling back to an older, clean day.
    """
    keys = store.list_partition_keys(FIRE_PERIMETERS_STREAM, _OBSERVED_KIND, zoom)
    statuses = partition_day_statuses(
        layer=FIRE_PERIMETERS_STREAM,
        kind=_OBSERVED_KIND,
        zoom=zoom,
        first_day=history_floor,
        last_day=as_of,
        keys=keys,
    )
    candidates = [(day, status) for day, status in statuses.items() if status not in UNFILLED_PARTITION_STATUSES]
    return max(candidates, key=lambda pair: pair[0]) if candidates else None


def resolve_fire_perimeters_as_of(  # noqa: PLR0913 - each argument is one independent coordinate of the as-of read
    store: ObjectStore,
    *,
    base_uri: str,
    requested_zoom: int,
    as_of: date,
    history_floor: date,
    storage_options: Mapping[str, str] | None = None,
) -> FirePerimetersAsOfAnswer:
    """Resolve "as of date D at map zoom Z" to the newest snapshot at or before D, at the one tier serving Z.

    Never interpolates between snapshots and never presents the newest snapshot as same-day truth for
    an earlier `as_of`: `answered_by_snapshot_day` always names which day actually answered, and `note`
    spells out the gap between requested and answering day when they differ. `history_floor` bounds the
    backward search; this module does not invent one -- the caller supplies the lane's real
    ingestion-start floor (`pipeline/parquet/lane_registry.py`'s `FIRE_PERIMETERS_STREAM` registration).

    The resolved snapshot is then filtered IN-FRAME on `observed_day` against `as_of` -- not against
    `answered_by_snapshot_day` -- because `as_of` is the slider date a caller actually asked for, the
    same value `src/lib/map/tile-layer-date-filter.ts` would have compared client-side. The resolved
    snapshot stands in for "the current `geo.features` state as best captured", and the in-frame filter
    reproduces the client's own "at or before, plus every undated row" rule on top of it -- never a
    `== as_of` equality, which the lane's own re-registration retired precisely because it silently
    dropped every row `observed_day` could not date.
    """
    if history_floor > as_of:
        raise FirePerimetersServingError(f"history_floor {history_floor} must not be after as_of {as_of}")

    zoom = serving_zoom_tier(requested_zoom)
    resolved = _newest_answerable_snapshot_day(store, zoom=zoom, history_floor=history_floor, as_of=as_of)
    if resolved is None:
        return FirePerimetersAsOfAnswer(
            requested_as_of=as_of,
            status="not_yet_observed",
            answered_by_snapshot_day=None,
            answered_by_zoom_tier=zoom,
            perimeters=_empty_fire_perimeters_frame(),
            note=(
                f"no fire-perimeters snapshot exists between {history_floor} and {as_of} at zoom tier "
                f"{zoom}; this lane has no backfill path (pipeline/parquet/AGENTS.md), so a day the "
                "cron missed is lost, not interpolated."
            ),
        )
    answering_day, day_status = resolved

    if day_status == "conflict":
        return FirePerimetersAsOfAnswer(
            requested_as_of=as_of,
            status="conflicted",
            answered_by_snapshot_day=answering_day,
            answered_by_zoom_tier=zoom,
            perimeters=_empty_fire_perimeters_frame(),
            note=(
                f"{answering_day} carries both a data partition and a governed-absence marker -- an "
                "admin-only anomaly (layer-lanes.md section 4). Refusing to silently pick a side."
            ),
        )

    if day_status == "absent":
        stale_note = "" if answering_day == as_of else f" Newest snapshot at or before {as_of}, not a same-day reading."
        return FirePerimetersAsOfAnswer(
            requested_as_of=as_of,
            status="observed",
            answered_by_snapshot_day=answering_day,
            answered_by_zoom_tier=zoom,
            perimeters=_empty_fire_perimeters_frame(),
            note=(
                f"{answering_day} is a governed absence: zero currently-published WFIGS incidents -- a "
                "normal quiet-fire-season state, not a defect." + stale_note
            ),
        )

    snapshot = read_fire_perimeters_day(
        store, day=answering_day, requested_zoom=requested_zoom, base_uri=base_uri, storage_options=storage_options
    )
    in_frame = snapshot.filter(pl.col("observed_day").is_null() | (pl.col("observed_day") <= as_of))
    trailer = "" if answering_day == as_of else f", the newest one at or before {as_of}"
    return FirePerimetersAsOfAnswer(
        requested_as_of=as_of,
        status="observed",
        answered_by_snapshot_day=answering_day,
        answered_by_zoom_tier=zoom,
        perimeters=in_frame,
        note=(
            f"answered by the {answering_day} snapshot{trailer} at zoom tier {zoom}, in-frame at "
            f"{as_of} ({in_frame.height} of {snapshot.height} incident(s); an incident with no "
            "observed_day is kept at every date)."
        ),
    )
