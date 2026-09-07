"""Decide evacuation-zones' version stamp from CONTENT, because Oregon publishes no clock that could.

This module is the registered resolver's whole body:
`pipeline/parquet/lane_registry.py::_evacuation_zones_watermark` calls
`read_evacuation_zones_source_watermark` and nothing else. Adapter and watermark were swapped
together on 2026-09-06 -- the adapter to a source-direct refusal naming
`pipeline/direct/evacuation_zones`, the watermark to this.

## Why the replacement was a design decision and not a transcription

`sql/pipeline/lane_watermark_evacuation_zones.sql` (deleted in that same edit) answered "when did the
published set last change" with `GREATEST(max(features.updated_at), max(features.created_at),
max(geometry.version_valid_from))`. All three are PlantGeo's own warehouse clocks over two tables
this track drops, and Oregon OEM offers no replacement for any of them:

  `created_date`      never moves when an evacuation level is raised -- the one event that matters
                      most on this layer would be invisible.
  `last_edited_date`  is re-stamped on unchanged areas every few minutes by Oregon's own sync
                      (`ingest/evacuation_zones.py:410-415`), so a watermark built on it reports
                      "changed" on essentially every poll and re-snapshots the whole layer forever.

Neither is a version clock, so this module does not look for one. It asks the question the direct
writer already asks -- HAS THE PUBLISHED POPULATION CHANGED -- against the same content digest
`forward.py` publishes on (`rows.content_digest`, whose `CONTENT_DIGEST_COLUMNS` is deliberately
every column Oregon determines and no column this repo's clock determines; `last_edited_date` is not
among them and is not even stored). The census and the writer therefore cannot disagree: they run one
comparison, spelled once.

## The four answers, in the order they are reached

1. THE CAPTURE IS EMPTY -> `SourceWatermark(day=None)`, which `resolve_static_lane` reports as
   `source_empty`: "the source holds no rows to version". That is the honest reading of a statewide
   stand-down, and it is deliberately NOT "current at the governed-absence marker" -- for a static
   lookup, `resolve_static_lane` treats a marker at the watermark day as a FAILED READ to retry, so
   answering with a day here would report a quiet Oregon as permanently stale.
2. NOTHING IS PUBLISHED YET -> this capture is the first, and its own instant is the change instant.
3. THE DIGESTS AGREE -> STICKY at the published version's own day, exactly as `max(updated_at)` was
   sticky across a poll that found nothing changed. Nothing is owed.
4. THE DIGESTS DIFFER -> this capture is the first to see the new state, so the version day is the
   UTC date of the capture, matching `forward.py`'s own rule for the day it publishes under.

THE STICKY ANSWER CARRIES NO `instant`, ON PURPOSE. `_resolve_watermark_day` then calls it a
DAY-RESOLUTION `current` and says so in its detail. Handing it the published version's capture
instant would buy nothing -- the comparison that settled this was byte equality of the whole
population, which is strictly stronger than any instant comparison -- and would open the spin loop
`fire_perimeters/watermark.py::_unchanged_watermark` has to guard against by hand: a container clock
running ahead of the object store makes the capture look later than the export that wrote it, which
reads as `stale` and re-publishes an identical version every tick, forever, on a green report.

## The cost, named

One statewide walk (~116 areas, ~480 KB, `source.py`'s bounded page walk) plus one listing and one
read-back of the newest published version. Nothing calls this on a schedule: the generic
`parquet-evacuation-zones` lane is retired in favour of `evacuation-zones-direct-forward`, which
passes its own already-captured resolver into `fill_one_lane_day` (`forward.py::_captured_watermark`)
rather than capturing a second time. What remains is `resolve_lane_watermarks` for an operator who
explicitly asked (`--read-watermarks`), and the census of a run that names this lane by hand.

## The first turn after cutover always publishes, and that is correct

A version written by the Postgres lane carries `geo.geometry` uuids in `geometry_version_id`, while
this package writes `direct:<natural_key>:<sha256(wkb)[:16]>` (`rows.py`), so the digests cannot
match however identical the zones are. The first direct turn writes one version; after it, every
comparison is direct-against-direct. It is also the turn that retires the accumulation the Postgres
chain could not: 116 areas live against 718 published, because a closed evacuation area simply stops
being returned by the feed and nothing in the Postgres path could ever retract it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.foundation.parquet.paths import (
    completed_partition_days,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
    bbox_unconfigured_reason,
    resolve_coverage_bbox,
)
from agri_data_service.pipeline.direct.evacuation_zones.rows import (
    content_digest,
    evacuation_zones_table,
    row_content_digests,
    updated_at_by_natural_key,
)
from agri_data_service.pipeline.direct.evacuation_zones.source import fetch_evacuation_zones_snapshot
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_SCHEMA

if TYPE_CHECKING:
    from datetime import date, datetime

    from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: How much of a digest a basis string quotes. The full 64 hex characters make an operator-facing
#: sentence unreadable and prove nothing more: the comparison is always done on the whole value.
_DIGEST_EXCERPT: Final = 12

#: This module's OWN retry series, deliberately shorter than `forward.py`'s. A watermark read is a
#: diagnostic inside somebody's census tick, not a publication turn: the writer may spend up to five
#: attempts over ~60-second waits to get one version onto a life-safety layer, and a census that
#: hung for that long to print one row would be the wrong trade. Same jittered backoff, fewer and
#: tighter attempts.
WATERMARK_RETRY_ATTEMPTS: Final = 3
WATERMARK_RETRY_BASE_SECONDS: Final = 2.0
WATERMARK_RETRY_MAX_SECONDS: Final = 10.0


class EvacuationZonesWatermarkError(RuntimeError):
    """Raised when this run cannot decide what version is owed, which is never an empty source."""


@dataclass(frozen=True, slots=True)
class PublishedSnapshot:
    """What this lane has already published: which version, and what that version actually holds.

    `absent` is True for a version published as a GOVERNED ABSENCE -- a real, readable answer
    ("Oregon published nothing") whose content is the empty set, which is why `digest` is still
    populated for it and compares equal to the next quiet capture rather than reading as "unknown".
    """

    day: date
    digest: str
    row_digests: dict[str, str]
    updated_at: dict[str, datetime]
    absent: bool


def read_published_snapshot(store: ObjectStore) -> PublishedSnapshot | None:
    """Read the newest version this lane has published at its base rung, or None if it has none.

    LISTING FIRST, then at most one partition read. `layer-lanes.md` section 4's "gap detection that
    opens files has misused the layout" still holds for GAP DETECTION; this is not gap detection but
    a content comparison, and the only object that can answer it is the published snapshot itself.
    One `read_partition` of a few hundred polygons is the entire cost, paid once per turn.

    A day carrying BOTH data and an absence marker is a conflict, and it is refused rather than
    resolved: for a version-stamped life-safety layer, silently preferring one of two contradictory
    claims is precisely what `planes/evacuation_zones.py` returns `conflicted` rather than guess.
    """
    listed = store.list_partition_objects(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER)
    part_days = {
        parsed.day
        for entry in listed
        if (parsed := try_parse_partition_path(entry.relative_path)) is not None
        and parsed.layer == EVACUATION_ZONES_STREAM
        and parsed.kind == EVACUATION_ZONES_DIRECT_KIND
        and parsed.zoom == LANE_BASE_ZOOM_TIER
    }
    marker_days = {
        marker.day
        for entry in listed
        if (marker := try_parse_absence_marker_path(entry.relative_path)) is not None
        and marker.layer == EVACUATION_ZONES_STREAM
        and marker.kind == EVACUATION_ZONES_DIRECT_KIND
        and marker.zoom == LANE_BASE_ZOOM_TIER
    }
    conflicts = part_days & marker_days
    if conflicts:
        raise EvacuationZonesWatermarkError(
            f"evacuation-zones version(s) {sorted(day.isoformat() for day in conflicts)} carry both a data "
            "partition and a governed-absence marker; refusing to pick a side for a life-safety layer"
        )
    complete_days = part_days & completed_partition_days(
        (entry.relative_path for entry in listed),
        layer=EVACUATION_ZONES_STREAM,
        kind=EVACUATION_ZONES_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
    )
    newest = max(complete_days | marker_days, default=None)
    if newest is None:
        return None
    if newest in marker_days:
        # Published content is the EMPTY SET, and that is a real answer rather than a missing one:
        # the marker says Oregon had nothing statewide. The next quiet capture digests identically
        # and correctly publishes nothing.
        return PublishedSnapshot(
            day=newest,
            digest=content_digest(EVACUATION_ZONES_SCHEMA.arrow_schema.empty_table()),
            row_digests={},
            updated_at={},
            absent=True,
        )
    table = store.read_partition(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER, newest)
    return PublishedSnapshot(
        day=newest,
        digest=content_digest(table),
        row_digests=row_content_digests(table),
        updated_at=updated_at_by_natural_key(table),
        absent=False,
    )


def watermark_for_capture(
    source: EvacuationZonesSource,
    *,
    captured_digest: str,
    published: PublishedSnapshot | None,
) -> SourceWatermark:
    """Turn one capture and one published version into this lane's version clock. No I/O, no clock read.

    Pure so the four answers in the module docstring can be tested as the decisions they are, rather
    than through a fetch. The only instant this function can ever report is `source.fetched_at`: the
    capture is the observation, and no field Oregon publishes dates a change to it.
    """
    if source.zone_count == 0:
        return SourceWatermark(
            day=None,
            basis=(
                f"evacuation-zones: Oregon OEM returned no evacuation area within bbox {source.bbox} at "
                f"{source.fetched_at.isoformat()}, so there is no population to version"
            ),
        )
    excerpt = captured_digest[:_DIGEST_EXCERPT]
    if published is None:
        return _changed_watermark(source, excerpt, comparison="no version is published yet")
    if published.digest == captured_digest:
        return SourceWatermark(
            day=published.day,
            instant=None,
            basis=(
                f"evacuation-zones: {source.zone_count} area(s) captured at {source.fetched_at.isoformat()} "
                f"digest to {excerpt}, identical to published version {published.day.isoformat()}"
                f"{' (a governed absence)' if published.absent else ''}, so the reference set has NOT changed "
                "since that version. Sticky at that day exactly as max(updated_at) was sticky across an "
                "unchanged poll, and instant-free on purpose: byte equality of the whole population already "
                "settled this more strongly than any instant comparison could"
            ),
        )
    return _changed_watermark(
        source,
        excerpt,
        comparison=(
            f"published version {published.day.isoformat()} digests to "
            f"{published.digest[:_DIGEST_EXCERPT]}{' (a governed absence)' if published.absent else ''}"
        ),
    )


def _changed_watermark(source: EvacuationZonesSource, excerpt: str, *, comparison: str) -> SourceWatermark:
    """The population differs from what is published, so THIS capture is the change instant.

    The same KIND of clock the retired SQL watermark read: `geo.features.updated_at` was likewise the
    instant a poller recorded a change, never an instant Oregon declared. The gate is what made it a
    change clock rather than a poll clock (`sql/ingest/refresh_features.sql`'s
    `IS DISTINCT FROM` on the properties), and the digest comparison IS that gate, applied to a
    population instead of a row -- and with geometry inside it, which that gate stripped out.
    """
    return SourceWatermark(
        day=source.fetched_at.date(),
        instant=source.fetched_at,
        basis=(
            f"evacuation-zones: {source.zone_count} area(s) captured from Oregon OEM at "
            f"{source.fetched_at.isoformat()} within bbox {source.bbox} digest to {excerpt}; {comparison}. "
            "The set CHANGED, so this capture is the change instant -- Oregon publishes no field that "
            "dates one (created_date never moves when a level is raised, last_edited_date is re-stamped "
            "on unchanged areas every few minutes)"
        ),
    )


async def read_evacuation_zones_source_watermark(store: ObjectStore, *, bbox: str | None = None) -> SourceWatermark:
    """Capture Oregon's current statewide set, digest it, and compare it against what is published.

    Reads NO database. The `session` a `LaneWatermarkResolver` is handed goes unused one level up, in
    the registry's own wrapper, which is where that argument's `noqa` and its reason live.

    AN UNCONFIGURED BBOX RAISES RATHER THAN ANSWERING `day=None`. Those are different claims and
    `resolve_static_lane` treats them differently: `day=None` is `source_empty` ("Oregon has no
    active evacuation areas"), which on a life-safety layer would be a fabrication, while a raised
    read is reported as `watermark_unread` ("this run could not find out"). `forward.py` skips the
    same turn for the same reason before it opens a socket.
    """
    resolved = resolve_coverage_bbox(bbox)
    if resolved is None:
        raise EvacuationZonesWatermarkError(
            f"{bbox_unconfigured_reason()}; this lane's version clock is a content comparison over the "
            "areas inside the configured extent, so with no extent this run cannot read it. That is an "
            "unread watermark, never an empty source"
        )
    published = await asyncio.to_thread(read_published_snapshot, store)
    source = await fetch_evacuation_zones_snapshot(
        resolved,
        retry_attempts=WATERMARK_RETRY_ATTEMPTS,
        retry_base_seconds=WATERMARK_RETRY_BASE_SECONDS,
        retry_max_seconds=WATERMARK_RETRY_MAX_SECONDS,
    )
    captured = await asyncio.to_thread(evacuation_zones_table, source, snapshot_day=source.fetched_at.date())
    return watermark_for_capture(source, captured_digest=content_digest(captured), published=published)


__all__ = [
    "WATERMARK_RETRY_ATTEMPTS",
    "WATERMARK_RETRY_BASE_SECONDS",
    "WATERMARK_RETRY_MAX_SECONDS",
    "EvacuationZonesWatermarkError",
    "PublishedSnapshot",
    "read_evacuation_zones_source_watermark",
    "read_published_snapshot",
    "watermark_for_capture",
]
