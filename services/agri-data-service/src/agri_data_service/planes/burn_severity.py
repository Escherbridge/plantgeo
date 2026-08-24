"""The `burn-severity` lane's Polars serving read: one MTBS release, resolved "as of" a calendar day.

Layer L3: may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import `interface`.
`geom` is standard WKB with no SRID header -- every row is SRID 4326 out of band
(`warehouse/schemas/burn_severity.py`), never read off the bytes. This lane declares `horizon: none`
(`docs/lanes/burn-severity.md` #7) and writes only `kind=observed`, so this module never accepts a
`kind` argument -- there is no `kind=forecast` partition to point one at.

MTBS publishes five discrete release days across all of history, not a daily series
(`docs/lanes/burn-severity.md` #4), so "the day for `day=X`" and "what had MTBS published as of day
X" are different questions. `read_burn_severity_release_day` answers the first: it reads the EXACT
release day the caller already knows (from an object listing, or from `resolve_burn_severity_as_of`
below). `resolve_burn_severity_as_of` answers the second: given an arbitrary calendar day -- a
time-slider position that will usually fall between two releases -- it finds the newest release
published at or before it and reads THAT release once, naming which one answered. Neither function
ever blends two releases into one answer (`layer-lanes.md` #2).

`zoom` IS a parameter, unlike `kind`, and is required: `kind` has one legal value on this lane while
the ladder has four live rungs, so there is a real choice and a default would only decide it quietly.
The listing that finds a release day and the scan that reads it name the SAME rung -- resolving one
release day at one resolution and reading it at another would answer "as of" with a release day that
is real and rows that are not the ones it names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.paths import (
    completed_partition_days,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.warehouse.schemas.burn_severity import (
    BURN_SEVERITY_GRAIN,
    BURN_SEVERITY_SCHEMA,
    BURN_SEVERITY_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# The only stream this lane ever writes (docs/lanes/burn-severity.md #7). Kept as a private constant
# rather than a `kind` parameter so a caller cannot even ask for a partition that will never exist.
_OBSERVED_KIND: Final = "observed"

# MTBS bypasses agri.source_release/agri.data_source entirely (sql/pipeline/burn_severity_day_export.sql
# header), so `allowed_client_exposure` is a hardcoded FALSE literal baked into every written row, not a
# governed database join -- and that FALSE directly CONTRADICTS geo.layers.is_public=true for this same
# layer (drizzle/0011_burn_severity_layer.sql:17). The two have never been reconciled. This module does
# not launder that by building an exposure gate here (there is no governed row behind the column to gate
# against); it surfaces the contradiction on every "as of" answer instead, so a caller decides with eyes
# open rather than getting an answer that has silently picked a side. `allowed_client_exposure` itself
# still travels verbatim on every row of `rows` -- this constant explains what that column means here.
BURN_SEVERITY_EXPOSURE_CONTRADICTION: Final = (
    "every burn-severity row carries allowed_client_exposure=false (ingest/mtbs.py:171 -- no MTBS-"
    "derived data may reach a public CDN without a fresh licensing review) while geo.layers.is_public"
    "=true for this same layer (drizzle/0011_burn_severity_layer.sql:17); the two have never been "
    "reconciled, and the column, not the layer flag, is the live restriction"
)


def burn_severity_base_uri(credentials: ObjectStoreCredentials, store: ObjectStore) -> str:
    """Return the `s3://bucket/prefix` root a production caller passes as `base_uri` below."""
    return f"s3://{credentials.bucket}/{store.prefix}"


@dataclass(frozen=True, slots=True)
class BurnSeverityAsOfAnswer:
    """Which MTBS release (if any) answers "as of" one calendar day, and its rows.

    `release_day` is the exact release-publication day MTBS's completeness machinery dated this
    cohort to (docs/lanes/burn-severity.md #4) -- always the newest one at or before `requested_day`,
    never a blend of two. `None` means no MTBS release existed yet at `requested_day`; that is an
    honest absence, not a synthesized answer. A `requested_day` past the newest release resolves to
    that newest release rather than projecting one: this lane ships no `kind=forecast` stream at all
    (docs/lanes/burn-severity.md #7), so there is nothing to fall through to for a future date either.
    """

    requested_day: date
    release_day: date | None
    zoom: ZoomTier
    is_governed_absence: bool
    rows: pl.DataFrame
    exposure_contradiction: str = BURN_SEVERITY_EXPOSURE_CONTRADICTION


def read_burn_severity_release_day(
    store: ObjectStore,
    *,
    day: date,
    requested_zoom: int,
    base_uri: str,
    storage_options: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Read every fire MTBS published in the release dated to exactly `day`, at one tier, sorted to the grain.

    `store.list_partition_keys` discovers every `part-N.parquet` written for `day`, however many the
    exporter's row-count spillover produced (`pipeline/lanes/burn_severity.py` `MAX_ROWS_PER_PART`);
    they are read in one `pl.scan_parquet` call and returned as one table, so a release that spilled
    across several parts reads as ONE day, never one part standing in for the whole release. A day
    with no listed part files -- not one of MTBS's five governed release days, a day recorded as a
    governed absence, or any date this lane has not been asked about -- answers with zero rows of the
    canonical schema; there is no "nearest available day" fallback in this function. A caller that
    wants that behaviour wants `resolve_burn_severity_as_of` instead.

    `storage_options` is the dict `pipeline.parquet.objectstore.polars_storage_options(credentials)`
    returns for a real bucket; pass `None` (or omit it) when `base_uri` is a local path, e.g. a test.
    `base_uri` for a production caller is `burn_severity_base_uri(credentials, store)`.
    """
    part_keys = _observed_day_part_keys(store, serving_zoom_tier(requested_zoom), day)
    if not part_keys:
        return _empty_burn_severity_frame()
    root = base_uri.rstrip("/")
    uris = [f"{root}/{key}" for key in part_keys]
    options = dict(storage_options) if storage_options else None
    frame = pl.scan_parquet(uris, storage_options=options).collect()
    return frame.sort(list(BURN_SEVERITY_GRAIN))


def resolve_burn_severity_as_of(
    store: ObjectStore,
    *,
    requested_day: date,
    requested_zoom: int,
    base_uri: str,
    storage_options: Mapping[str, str] | None = None,
) -> BurnSeverityAsOfAnswer:
    """Answer "what had MTBS published as of `requested_day`", naming exactly which release and tier answered.

    Only `kind="observed"` is ever consulted -- this lane writes no `kind=forecast` stream, so there
    is nothing to fall through to, silently or otherwise. The candidate release day is chosen from a
    real object LISTING (`store.list_partition_keys`, the same gap-detection primitive the writer
    side uses -- `layer-lanes.md` #4: a release or a gap is discoverable by listing, never by
    scanning), and only the confirmed release day is ever handed to Polars for the actual row read.
    A release recorded as a governed absence (a real MTBS release whose cohort held zero fires in
    this deployment's bounding box) answers with `is_governed_absence=True` and zero rows, never with
    the silence of "day not found."
    """
    zoom = serving_zoom_tier(requested_zoom)
    candidates = _known_release_days(store, zoom)
    eligible = {day: absent for day, absent in candidates.items() if day <= requested_day}
    if not eligible:
        return BurnSeverityAsOfAnswer(
            requested_day=requested_day,
            release_day=None,
            zoom=zoom,
            is_governed_absence=False,
            rows=_empty_burn_severity_frame(),
        )
    release_day = max(eligible)
    is_absence = eligible[release_day]
    rows = (
        _empty_burn_severity_frame()
        if is_absence
        else read_burn_severity_release_day(
            store, day=release_day, requested_zoom=zoom, base_uri=base_uri, storage_options=storage_options
        )
    )
    return BurnSeverityAsOfAnswer(
        requested_day=requested_day,
        release_day=release_day,
        zoom=zoom,
        is_governed_absence=is_absence,
        rows=rows,
    )


def _known_release_days(store: ObjectStore, zoom: ZoomTier) -> dict[date, bool]:
    """List every known observed release day AT ONE TIER, mapping day -> "is a governed absence".

    Unscoped by year/month on purpose: resolving "as of" requires seeing every release this lane has
    ever recorded, and that whole history is five releases today (docs/lanes/burn-severity.md #3,
    #5) -- nowhere near `ObjectStore`'s 500,000-key listing budget. Scoped by TIER on purpose too:
    a governed absence is recorded per rung, so a release absent at z13 and present at z09 would
    otherwise collapse into one `days[...] = True` whose value depends on listing order.

    A day holding part files WITHOUT a completion marker is an upload killed part-way through and
    is NOT counted as a published release -- its parts are a prefix of the day, not the day.
    """
    keys = list(store.list_partition_keys(BURN_SEVERITY_STREAM, _OBSERVED_KIND, zoom))
    completed = completed_partition_days(keys, layer=BURN_SEVERITY_STREAM, kind=_OBSERVED_KIND, zoom=zoom)
    days: dict[date, bool] = {}
    for key in keys:
        partition = try_parse_partition_path(key)
        if partition is not None and partition.day in completed:
            days.setdefault(partition.day, False)
            continue
        marker = try_parse_absence_marker_path(key)
        if marker is not None:
            days[marker.day] = True
    return days


def _observed_day_part_keys(store: ObjectStore, zoom: ZoomTier, day: date) -> tuple[str, ...]:
    """Return every `part-N.parquet` relative key written for exactly this release day at one tier, ascending.

    A day whose parts exist without a completion marker is an upload killed part-way through and
    answers as an empty tuple -- its parts are a prefix of the release, not the release.
    """
    candidates = list(
        store.list_partition_keys(BURN_SEVERITY_STREAM, _OBSERVED_KIND, zoom, year=day.year, month=day.month)
    )
    completed = completed_partition_days(candidates, layer=BURN_SEVERITY_STREAM, kind=_OBSERVED_KIND, zoom=zoom)
    if day not in completed:
        return ()
    return tuple(
        sorted(key for key in candidates if (parsed := try_parse_partition_path(key)) is not None and parsed.day == day)
    )


def _empty_burn_severity_frame() -> pl.DataFrame:
    """Zero rows, correctly typed: the honest answer for a release day with no part files."""
    empty = pl.from_arrow(BURN_SEVERITY_SCHEMA.arrow_schema.empty_table())
    if not isinstance(empty, pl.DataFrame):  # pragma: no cover - `from_arrow` on a `pa.Table` always returns one.
        raise TypeError("polars.from_arrow of an empty pyarrow Table did not return a DataFrame")
    return empty
