"""Publish one version of Oregon OEM's evacuation areas directly, when and only when it has changed.

Bypasses PostgreSQL entirely: `pipeline/lanes/evacuation_zones.py::export_evacuation_zones_day` (the
registered `_fill_evacuation_zones` adapter) reads `geo.features` and LEFT JOINs `geo.geometry`, and
`ingest/evacuation_zones.py::run_evacuation_zones_ingestion_job` is what filled them. This module
replaces both. PostgreSQL is still opened for ONE thing -- the shared session-scoped lane-day
advisory lock -- which is coordination, not a data sink (`pipeline/direct/AGENTS.md`, header).

HOW A VERSION STAMP IS DECIDED WITHOUT A POSTGRES WATERMARK, which is this lane's whole problem.
`sql/pipeline/lane_watermark_evacuation_zones.sql` answered "when did the published set last change"
by reading `GREATEST(max(features.updated_at), max(features.created_at),
max(geometry.version_valid_from))`. Every one of those three is a column this track deletes, and two
of them are PlantGeo's own change-detection clock rather than anything Oregon publishes. The upstream
offers no replacement: `created_date` never moves when a level is raised, and `last_edited_date` is
re-stamped on unchanged areas every few minutes, so a watermark built on it would re-snapshot the
whole layer every tick -- "exactly the behaviour being removed", in that file's own words.

So the change test moves INTO the writer, where a direct fetch makes it cheaper than it ever was in
SQL: capture the statewide set, digest its source-determined content (`rows.content_digest`), and
compare that against the newest snapshot this lane has already published. Equal means nothing is
owed. Different means THIS capture is the first to see the new state, so the version day is the UTC
date of the capture and the whole population is re-exported under it.

Three consequences worth stating, because they are improvements rather than compromises:

- The instant race `gap_fill.py::_fill_static_day` brackets against cannot arise here. That bracket
  exists because a source change landing between an export's SELECT and its PUT makes the part
  file's `LastModified` read as "captured" forever after. Currency here is decided by CONTENT, not
  by comparing two instants, so a change that lands mid-export is simply seen by the next tick's
  digest and published then.
- A shape-only revision is seen. The Postgres pair provably could not see one -- see
  `rows.py`'s module docstring for the two independent reasons -- and `content_digest` includes
  `geometry_wkb`.
- A version is never stamped from the cron's calendar. A tick that finds nothing changed writes
  nothing at all, so a tick the scheduler skipped still costs nothing, exactly as the watermark
  model promised.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.foundation.parquet.paths import (
    completed_partition_days,
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.ingest.mtbs import inline_bbox_value
from agri_data_service.pipeline.direct.evacuation_zones.adapter import (
    DirectEvacuationZonesAdapter,
    DirectEvacuationZonesError,
)
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    COVERED_STATE,
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
    bbox_unconfigured_reason,
    evacuation_zones_lane_registration,
    refuse_uncovered_state,
    resolve_coverage_bbox,
)
from agri_data_service.pipeline.direct.evacuation_zones.rows import (
    apply_carried_forward_updated_at,
    content_digest,
    evacuation_zones_table,
    row_content_digests,
    updated_at_by_natural_key,
)
from agri_data_service.pipeline.direct.evacuation_zones.source import fetch_evacuation_zones_snapshot
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration, LaneWatermarkResolver

EVACUATION_ZONES_DIRECT_ALL_TIERS: Final[tuple[ZoomTier, ...]] = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)
EVACUATION_ZONES_FORWARD_RUN_ID_PREFIX: Final = "evacuation-zones-forward:"
#: A `static_lookup` lane owes AT MOST ONE version per turn, structurally: there is exactly one
#: current state and the only version it can be stamped with is the day this capture saw it. A
#: backlog is not merely absent, it is unrepresentable -- Oregon publishes no archive, so a day the
#: scheduler missed has no state left to publish (`docs/lanes/evacuation-zones.md` section 3). The
#: flag is accepted, and bounded to 1, so the operator contract matches every other direct writer's
#: rather than this lane quietly having a different one.
EVACUATION_ZONES_DEFAULT_MAX_DAYS: Final = 1
EVACUATION_ZONES_MAX_DAYS: Final = 1
EVACUATION_ZONES_DEFAULT_TIME_BUDGET_SECONDS: Final = 300.0
EVACUATION_ZONES_MAX_TIME_BUDGET_SECONDS: Final = 1_800.0
EVACUATION_ZONES_DEFAULT_RETRY_ATTEMPTS: Final = 5
EVACUATION_ZONES_MAX_RETRY_ATTEMPTS: Final = 10
EVACUATION_ZONES_DEFAULT_RETRY_BASE_SECONDS: Final = 5.0
EVACUATION_ZONES_MAX_RETRY_BASE_SECONDS: Final = 60.0
EVACUATION_ZONES_DEFAULT_RETRY_MAX_SECONDS: Final = 60.0
EVACUATION_ZONES_MAX_RETRY_MAX_SECONDS: Final = 300.0
EVACUATION_ZONES_DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 300.0
EVACUATION_ZONES_MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
EVACUATION_ZONES_STATEMENT_TIMEOUT_SECONDS: Final = 120
EVACUATION_ZONES_MIN_DELAY_SECONDS: Final = 0.1

EVACUATION_ZONES_UNCHANGED_OUTCOME: Final = "unchanged"
EVACUATION_ZONES_BBOX_UNCONFIGURED_OUTCOME: Final = "bbox_unconfigured"
EVACUATION_ZONES_TIME_BUDGET_OUTCOME: Final = "time_budget_exhausted"


class EvacuationZonesForwardConfigError(ValueError):
    """Raised when a turn is asked for an unbounded or self-contradictory shape."""


@dataclass(frozen=True, slots=True)
class EvacuationZonesForwardConfig:
    """Bound every upstream request, retry series and contention wait of one turn."""

    max_days: int
    time_budget_seconds: float
    retry_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    contention_timeout_seconds: float
    run_id: str | None = None
    today: date | None = None
    bbox: str | None = None
    state: str = COVERED_STATE


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


def emit(payload: dict[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


async def run_evacuation_zones_forward(config: EvacuationZonesForwardConfig) -> dict[str, object]:
    """Capture Oregon's current statewide set and publish it only if it differs from the newest version."""
    _validate_config(config)
    refuse_uncovered_state(config.state)
    run_id = config.run_id or f"{EVACUATION_ZONES_FORWARD_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    lane = evacuation_zones_lane_registration()
    deadline = time.monotonic() + config.time_budget_seconds
    availability = AvailabilityExtensionTally()

    bbox = resolve_coverage_bbox(config.bbox)
    if bbox is None:
        report = _skipped_report(
            run_id,
            lane=lane,
            availability=availability,
            outcome=EVACUATION_ZONES_BBOX_UNCONFIGURED_OUTCOME,
            detail=(
                f"{bbox_unconfigured_reason()}; this lane's coverage is bounded twice -- by Oregon's own "
                "statewide feed and by INGEST_BBOX -- so an unset bbox SKIPS the turn rather than "
                "widening the query, exactly as run_evacuation_zones_ingestion_job did"
            ),
        )
        emit({"event": "evacuation_zones_forward_skipped", **report})
        return report

    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    published = await _retry_async(
        "published evacuation-zones version census",
        lambda: asyncio.to_thread(read_published_snapshot, store),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    source = await fetch_evacuation_zones_snapshot(
        bbox,
        retry_attempts=config.retry_attempts,
        retry_base_seconds=config.retry_base_seconds,
        retry_max_seconds=config.retry_max_seconds,
    )
    version_day = source.fetched_at.astimezone(UTC).date()
    if published is not None and published.day > version_day:
        # A version stamped AHEAD of this capture. Publishing behind it would write a version
        # `read_published_snapshot` never looks at again, so every later tick would find the same
        # difference and rewrite the same stranded day forever. Refused rather than latched.
        raise DirectEvacuationZonesError(
            f"the newest published version {published.day.isoformat()} is later than this capture's "
            f"own day {version_day.isoformat()}; a version behind it would be invisible to every "
            "later turn and this one would rewrite it on every tick. Check the host clock, or "
            "retract the future version"
        )
    # Built ONCE, with every row provisionally stamped `feature_updated_at = fetched_at`. The digest
    # deliberately does not look at that column, so the carry-forward patch below cannot change it.
    table = evacuation_zones_table(source, snapshot_day=version_day)
    captured_digest = content_digest(table)

    emit(
        {
            "event": "evacuation_zones_forward_started",
            "run_id": run_id,
            "layer": EVACUATION_ZONES_STREAM,
            "namespace": f"layer={EVACUATION_ZONES_STREAM}/kind={EVACUATION_ZONES_DIRECT_KIND}/",
            "bbox": bbox,
            "zones_captured": source.zone_count,
            "captured_digest": captured_digest,
            "published_version": None if published is None else published.day.isoformat(),
            "published_digest": None if published is None else published.digest,
            "candidate_version": version_day.isoformat(),
        }
    )

    if published is not None and published.digest == captured_digest:
        report = _unchanged_report(
            run_id,
            lane=lane,
            availability=availability,
            published=published,
            source=source,
            digest=captured_digest,
        )
        emit({"event": "evacuation_zones_forward_unchanged", **report})
        return report

    if time.monotonic() >= deadline:
        report = _skipped_report(
            run_id,
            lane=lane,
            availability=availability,
            outcome=EVACUATION_ZONES_TIME_BUDGET_OUTCOME,
            detail=f"the turn's time budget expired before {version_day.isoformat()} could be published",
        )
        emit({"event": "evacuation_zones_forward_skipped", **report})
        return report

    table = apply_carried_forward_updated_at(table, _unchanged_row_stamps(table, published))

    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        result = await _publish_version_with_retries(
            session,
            store,
            lane,
            version_day,
            source=source,
            table=table,
            today=today,
            run_id=run_id,
            config=config,
            deadline=deadline,
            availability_storage=availability_storage,
            availability=availability,
        )
    emit({"event": "evacuation_zones_forward_version_complete", "run_id": run_id, **result})

    final = await _retry_async(
        "final evacuation-zones ladder census",
        lambda: asyncio.to_thread(_tier_status_day, store, version_day),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": EVACUATION_ZONES_STREAM,
        "namespace": f"layer={EVACUATION_ZONES_STREAM}/kind={EVACUATION_ZONES_DIRECT_KIND}/",
        "history_floor": lane.history_floor.isoformat(),
        "bbox": bbox,
        "coverage_state": COVERED_STATE,
        "versions_published": 1,
        **availability.to_summary(),
        "results": [result],
        "tier_status": {f"z{tier}": status for tier, status in final.items()},
    }


def _unchanged_row_stamps(table: pa.Table, published: PublishedSnapshot | None) -> dict[str, datetime]:
    """Return `feature_updated_at` for exactly the rows whose source content did NOT move.

    Reproduces `sql/ingest/refresh_features.sql`'s row-scoped UPDATE: it moved `updated_at = now()`
    only for the external ids whose stored properties genuinely differed, leaving every unchanged row
    alone. A whole-snapshot re-stamp would turn that column into a poll clock and destroy the one
    honest answer it carries -- when this row last changed. A key present in the published snapshot
    but with a different digest, and a key that is new this capture, both correctly fall through to
    the provisional `fetched_at` the builder already stamped.
    """
    if published is None:
        return {}
    current = row_content_digests(table)
    return {
        natural_key: stamp
        for natural_key, stamp in published.updated_at.items()
        if published.row_digests.get(natural_key) == current.get(natural_key)
    }


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
        raise DirectEvacuationZonesError(
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


async def _publish_version_with_retries(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    source: EvacuationZonesSource,
    table: pa.Table,
    today: date,
    run_id: str,
    config: EvacuationZonesForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Acquire the lane-day lock once, then republish the SAME capture under it for every attempt."""
    contention_deadline = time.monotonic() + config.contention_timeout_seconds
    while True:
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if granted:
                return await _publish_locked_version_with_retries(
                    session,
                    store,
                    lane,
                    day,
                    source=source,
                    table=table,
                    today=today,
                    run_id=run_id,
                    config=config,
                    deadline=deadline,
                    availability_storage=availability_storage,
                    availability=availability,
                )
        await session.rollback()
        remaining = min(contention_deadline, deadline) - time.monotonic()
        if remaining <= 0:
            raise DirectEvacuationZonesError(
                f"lane-day contention for {day} exceeded {config.contention_timeout_seconds:g}s"
            )
        delay = min(
            remaining, _retry_delay(1, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds)
        )
        emit(
            {
                "event": "evacuation_zones_forward_contention",
                "run_id": run_id,
                "day": day.isoformat(),
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)


async def _publish_locked_version_with_retries(  # noqa: PLR0913
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    source: EvacuationZonesSource,
    table: pa.Table,
    today: date,
    run_id: str,
    config: EvacuationZonesForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Write and verify the four-rung ladder for one version while one advisory lock remains held.

    THE CAPTURE IS NOT REFETCHED BETWEEN ATTEMPTS. A version day must carry one coherent statewide
    state; a retry that refetched would publish a mixture of two, under a version stamp chosen from
    the first. See `adapter.py`'s module docstring.
    """
    for write_attempt in range(1, config.retry_attempts + 1):
        if time.monotonic() >= deadline:
            return _version_result(
                source, day, outcome=EVACUATION_ZONES_TIME_BUDGET_OUTCOME, parts=0, rows=0, written_bytes=0, detail=None
            )
        adapter = DirectEvacuationZonesAdapter(table=table, source=source)
        direct_lane = replace(lane, adapter=adapter, watermark=_captured_watermark(source, day))
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                direct_lane,
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=today,
                lane_day_lock=unlocked_lane_day,
                statement_timeout_seconds=EVACUATION_ZONES_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except Exception as error:  # reported, never silently swallowed
            with suppress(Exception):
                await session.rollback()
            outcome = "raised"
            parts = rows = written_bytes = 0
            detail = f"{day.isoformat()}: {type(error).__name__}: {error}"

        if outcome == "blocked":
            raise DirectEvacuationZonesError(detail or f"evacuation-zones {day} is blocked")

        verified = False
        verification_error: Exception | None = None
        try:
            statuses = await asyncio.to_thread(_tier_status_day, store, day)
            expected = "absent" if source.zone_count == 0 else "data"
            verified = all(status == expected for status in statuses.values())
        except Exception as error:  # reported below, never silently swallowed
            verification_error = error
        if outcome in {"written", "absent"} and verified:
            return _version_result(
                source, day, outcome=outcome, parts=parts, rows=rows, written_bytes=written_bytes, detail=detail
            )
        if write_attempt >= config.retry_attempts:
            suffix = (
                f"; verification failed: {type(verification_error).__name__}: {verification_error}"
                if verification_error is not None
                else ""
            )
            raise DirectEvacuationZonesError(
                f"evacuation-zones {day} did not publish a complete four-tier ladder after "
                f"{write_attempt} attempt(s): outcome={outcome}, detail={detail}{suffix}"
            )
        delay = _retry_delay(
            write_attempt, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds
        )
        emit(
            {
                "event": "evacuation_zones_forward_r2_retry",
                "run_id": run_id,
                "day": day.isoformat(),
                "attempt": write_attempt,
                "outcome": outcome,
                "detail": detail,
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)
    raise AssertionError("bounded evacuation-zones publish attempts exhausted")


def _captured_watermark(source: EvacuationZonesSource, day: date) -> LaneWatermarkResolver:
    """Build the watermark resolver `fill_one_lane_day` requires of a `static_lookup` registration.

    `LaneRegistration.__post_init__` REFUSES a `static_lookup` lane with `watermark=None`, so the
    direct lane must carry one; `_fill_static_day` then calls it once before the export and once
    after, and treats two EQUAL instants as proof the source held still across the export window.

    This resolver answers with the capture that is being published -- the same `fetched_at` both
    times, because no second fetch happened. The bracket is therefore inert, and that is deliberate
    rather than overlooked: the race it closes (a source change landing between SELECT and PUT
    latching the day as `current` forever) is not reachable by this writer, whose next turn compares
    CONTENT rather than instants and republishes the moment the two differ. Doing a real second fetch
    here would either cost a full statewide walk per publication, or -- if probed cheaply through
    `last_edited_date` -- report a change on essentially every export, because Oregon re-stamps that
    clock on unchanged areas every few minutes. Neither buys protection this writer needs.

    Reads no database: the `session` and `store` arguments exist because the resolver protocol is
    uniform across lanes whose clock IS in Postgres.
    """
    watermark = SourceWatermark(
        day=day,
        instant=source.fetched_at,
        basis=(
            f"evacuation-zones: source-direct capture of {source.zone_count} Oregon OEM area(s) at "
            f"{source.fetched_at.isoformat()} within bbox {source.bbox}; this lane's Postgres watermark "
            "(sql/pipeline/lane_watermark_evacuation_zones.sql) is retired and currency is decided by "
            "content comparison in pipeline/direct/evacuation_zones/forward.py"
        ),
    )

    async def resolve(
        session: AsyncSession,  # noqa: ARG001 - uniform resolver shape; this lane reads no database
        store: ObjectStore,  # noqa: ARG001 - uniform resolver shape; the capture is already in hand
        *,
        today: date,  # noqa: ARG001 - the capture's own instant, never this run's date
    ) -> SourceWatermark:
        return watermark

    return resolve


def _tier_status_day(store: ObjectStore, day: date) -> dict[ZoomTier, PartitionDayStatus]:
    """Read every rung's status for exactly one version day, by listing alone."""
    return {
        tier: partition_day_statuses(
            layer=EVACUATION_ZONES_STREAM,
            kind=EVACUATION_ZONES_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(
                EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, tier, year=day.year, month=day.month
            ),
        )[day]
        for tier in EVACUATION_ZONES_DIRECT_ALL_TIERS
    }


def _version_result(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    source: EvacuationZonesSource,
    day: date,
    *,
    outcome: str,
    parts: int,
    rows: int,
    written_bytes: int,
    detail: str | None,
) -> dict[str, object]:
    """Render the capture evidence behind one version's publication."""
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "published": outcome in {"written", "absent"},
        "zones": source.zone_count,
        "fetched_at": source.fetched_at.isoformat(),
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
    }


def _unchanged_report(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    run_id: str,
    *,
    lane: LaneRegistration,
    availability: AvailabilityExtensionTally,
    published: PublishedSnapshot,
    source: EvacuationZonesSource,
    digest: str,
) -> dict[str, object]:
    """Report a turn that found the reference set unchanged and correctly wrote nothing."""
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": EVACUATION_ZONES_STREAM,
        "namespace": f"layer={EVACUATION_ZONES_STREAM}/kind={EVACUATION_ZONES_DIRECT_KIND}/",
        "history_floor": lane.history_floor.isoformat(),
        "bbox": source.bbox,
        "coverage_state": COVERED_STATE,
        "versions_published": 0,
        **availability.to_summary(),
        "results": [
            {
                "day": published.day.isoformat(),
                "outcome": EVACUATION_ZONES_UNCHANGED_OUTCOME,
                "published": False,
                "zones": source.zone_count,
                "fetched_at": source.fetched_at.isoformat(),
                "parts": 0,
                "rows_across_write": 0,
                "written_bytes": 0,
                "detail": (
                    f"version {published.day.isoformat()} already holds this exact content "
                    f"(digest {digest}), so no new version is owed. A static lookup's day is a "
                    "version stamp, not a calendar position"
                ),
            }
        ],
        "tier_status": {},
    }


def _skipped_report(
    run_id: str,
    *,
    lane: LaneRegistration,
    availability: AvailabilityExtensionTally,
    outcome: str,
    detail: str,
) -> dict[str, object]:
    """Report a turn that never reached a capture, in the same shape a published turn reports."""
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": EVACUATION_ZONES_STREAM,
        "namespace": f"layer={EVACUATION_ZONES_STREAM}/kind={EVACUATION_ZONES_DIRECT_KIND}/",
        "history_floor": lane.history_floor.isoformat(),
        "bbox": None,
        "coverage_state": COVERED_STATE,
        "versions_published": 0,
        **availability.to_summary(),
        "results": [
            {
                "day": None,
                "outcome": outcome,
                "published": False,
                "zones": 0,
                "fetched_at": None,
                "parts": 0,
                "rows_across_write": 0,
                "written_bytes": 0,
                "detail": detail,
            }
        ],
        "tier_status": {},
    }


async def _retry_async[T](
    label: str,
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_seconds: float,
    max_seconds: float,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except DirectEvacuationZonesError:
            # A data/absence conflict is a refusal to guess, not a transient listing failure; retrying
            # asks the same question of the same objects and gets the same contradiction.
            raise
        except Exception as error:  # every R2 census failure is retried the same way
            last_error = error
            if attempt >= attempts:
                break
            delay = _retry_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds)
            emit(
                {
                    "event": "evacuation_zones_forward_retry",
                    "operation": label,
                    "attempt": attempt,
                    "error_type": type(error).__name__,
                    "retry_in_seconds": round(delay, 3),
                }
            )
            await asyncio.sleep(delay)
    assert last_error is not None  # attempts >= 1 is enforced by `_validate_config`
    raise DirectEvacuationZonesError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


def _validate_config(config: EvacuationZonesForwardConfig) -> None:
    """Fail closed on every process-bound knob before a socket or a session is opened."""
    if not 1 <= config.max_days <= EVACUATION_ZONES_MAX_DAYS:
        raise EvacuationZonesForwardConfigError(
            f"--max-days must be between 1 and {EVACUATION_ZONES_MAX_DAYS}: this lane is a static_lookup "
            "with exactly one current state, and Oregon publishes no archive a second version could come "
            "from (docs/lanes/evacuation-zones.md section 3)"
        )
    if not 1 <= config.retry_attempts <= EVACUATION_ZONES_MAX_RETRY_ATTEMPTS:
        raise EvacuationZonesForwardConfigError(
            f"--retry-attempts must be between 1 and {EVACUATION_ZONES_MAX_RETRY_ATTEMPTS}"
        )
    bounds = {
        "--time-budget-seconds": (config.time_budget_seconds, EVACUATION_ZONES_MAX_TIME_BUDGET_SECONDS),
        "--retry-base-seconds": (config.retry_base_seconds, EVACUATION_ZONES_MAX_RETRY_BASE_SECONDS),
        "--retry-max-seconds": (config.retry_max_seconds, EVACUATION_ZONES_MAX_RETRY_MAX_SECONDS),
        "--contention-timeout-seconds": (
            config.contention_timeout_seconds,
            EVACUATION_ZONES_MAX_CONTENTION_TIMEOUT_SECONDS,
        ),
    }
    for name, (value, maximum) in bounds.items():
        if not math.isfinite(value) or not EVACUATION_ZONES_MIN_DELAY_SECONDS <= value <= maximum:
            raise EvacuationZonesForwardConfigError(
                f"{name} must be finite and between {EVACUATION_ZONES_MIN_DELAY_SECONDS:g} and {maximum:g}, "
                f"got {value!r}"
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise EvacuationZonesForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only evacuation-zones operator. No `--product`: this lane has one."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--max-days", type=int, default=EVACUATION_ZONES_DEFAULT_MAX_DAYS)
    built.add_argument("--time-budget-seconds", type=float, default=EVACUATION_ZONES_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument("--retry-attempts", type=int, default=EVACUATION_ZONES_DEFAULT_RETRY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=EVACUATION_ZONES_DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=EVACUATION_ZONES_DEFAULT_RETRY_MAX_SECONDS)
    built.add_argument(
        "--contention-timeout-seconds", type=float, default=EVACUATION_ZONES_DEFAULT_CONTENTION_TIMEOUT_SECONDS
    )
    built.add_argument(
        "--bbox",
        default=None,
        help=(
            "override INGEST_BBOX for this turn; still policy-checked by ingest/policy.py. Absent AND "
            "unset skips the turn rather than widening the query"
        ),
    )
    return built


def parse_args(argv: Sequence[str] | None = None) -> EvacuationZonesForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded turn.

    `inline_bbox_value` rewrites `--bbox -125,42,...` to `--bbox=-125,42,...` before argparse ever
    sees it, matching `ingest/mtbs.py::main` and `burn_severity/forward.py`. Without it argparse
    reads the leading `-125` as a second flag rather than this option's value and the documented
    operator command dies with "argument --bbox: expected one argument". The helper lives in
    `ingest/mtbs.py` because that is where the trap was first paid for; every `--bbox` writer in
    `pipeline/direct` imports the one implementation rather than restating the rewrite.
    """
    raw = list(argv) if argv is not None else sys.argv[1:]
    built = parser()
    arguments = built.parse_args(inline_bbox_value(raw))
    config = EvacuationZonesForwardConfig(
        max_days=arguments.max_days,
        time_budget_seconds=arguments.time_budget_seconds,
        retry_attempts=arguments.retry_attempts,
        retry_base_seconds=arguments.retry_base_seconds,
        retry_max_seconds=arguments.retry_max_seconds,
        contention_timeout_seconds=arguments.contention_timeout_seconds,
        run_id=arguments.run_id,
        bbox=arguments.bbox,
    )
    try:
        _validate_config(config)
    except EvacuationZonesForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_evacuation_zones_forward(config)
    except Exception as error:  # the one terminal failure report a caller parses
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "EVACUATION_ZONES_BBOX_UNCONFIGURED_OUTCOME",
    "EVACUATION_ZONES_DIRECT_ALL_TIERS",
    "EVACUATION_ZONES_MAX_DAYS",
    "EVACUATION_ZONES_TIME_BUDGET_OUTCOME",
    "EVACUATION_ZONES_UNCHANGED_OUTCOME",
    "EvacuationZonesForwardConfig",
    "EvacuationZonesForwardConfigError",
    "PublishedSnapshot",
    "main",
    "parse_args",
    "parser",
    "read_published_snapshot",
    "run_evacuation_zones_forward",
]
