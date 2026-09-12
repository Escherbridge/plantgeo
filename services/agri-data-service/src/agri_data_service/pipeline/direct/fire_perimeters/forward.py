"""Publish at most ONE WFIGS version per turn, directly, when and only when the source has changed.

This module is the source-direct owner of the fire-perimeters Parquet lane. The generic registration
is a refusal, and this writer derives both the captured version and its watermark from WFIGS without
consulting PostgreSQL. The former `geo.features` exporter and `postgres-fire-perimeters` schedule are
retired; any remaining parity tooling is historical evidence only.

THERE IS NO DAY LOOP HERE, AND THAT IS THE POINT. `drought/forward.py` walks a 60-week backlog
newest-first because a `release_series` owes one partition per release Tuesday. This lane is a
`static_lookup`: `resolve_static_lane` answers `current` / `stale` / `source_empty` for the WHOLE
lane in one shot, and `stale` names exactly one version day. A turn therefore does one of two things
-- publish one version, or publish nothing -- and a tick the cron skipped costs nothing, because no
day ever carried an obligation.

THE TURN'S SHAPE, and why the order is this order:

    fetch  ->  conform  ->  read the ladder  ->  read the watermark  ->  resolve  ->  publish?

The fetch comes FIRST because this lane's version day is derived from the fetched population, not
from the calendar -- there is no day to lock on until the source has answered. That inverts
`drought/forward.py`, which locks the day and then fetches under the lock. The consequence is that
the population is captured OUTSIDE the lane-day lock, and it is deliberately NOT refetched inside
it: a refetch would move the content out from under the version day already derived from it. Two
concurrent turns are still safe, because the lock is taken before anything is written and the loser
finds the winner's version already published on its next tick.
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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.lane_contract import resolve_static_lane
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.ingest.mtbs import inline_bbox_value
from agri_data_service.pipeline.direct import (
    LANE_DAY_OUTCOMES,
    REFUSE_UNCONFIGURED_BBOX,
    REFUSE_WHOLE_RELEASE,
    SKIP_AND_COUNT,
    TIME_BUDGET_EXHAUSTED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.fire_perimeters.adapter import (
    DirectFirePerimetersAdapter,
    DirectFirePerimetersError,
)
from agri_data_service.pipeline.direct.fire_perimeters.products import (
    FIRE_PERIMETERS_DIRECT_ALL_TIERS,
    FIRE_PERIMETERS_DIRECT_KIND,
    fire_perimeters_lane_registration,
)
from agri_data_service.pipeline.direct.fire_perimeters.rows import fire_perimeter_population
from agri_data_service.pipeline.direct.fire_perimeters.source import (
    FirePerimetersTruncatedError,
    fetch_fire_perimeters_source,
)
from agri_data_service.pipeline.direct.fire_perimeters.watermark import (
    read_direct_watermark,
    read_published_ladder,
)
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.direct.fire_perimeters.watermark import DirectWatermarkReading, PublishedLadder
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

FIRE_PERIMETERS_FORWARD_RUN_ID_PREFIX: Final = "fire-perimeters-forward:"

#: A `static_lookup` lane owes AT MOST ONE VERSION at any instant -- `resolve_static_lane` returns a
#: single `version_day` or none -- so this is the only legal value. The knob exists so the operator
#: command is spelled identically to every sibling direct writer's
#: (`python -m ... --max-days 1`), and it is validated rather than ignored, because a `--max-days 5`
#: that silently published one version would be a lie about what the turn did.
FIRE_PERIMETERS_MAX_DAYS: Final = 1
FIRE_PERIMETERS_DEFAULT_MAX_DAYS: Final = 1
#: Sized for one ~11 MB WFIGS walk, one ~23 MB read-back, a DuckDB conversion of ~23 MB of polygon
#: and a four-rung ladder write. Generous against drought's 300 s because this lane moves roughly
#: twenty times drought's bytes per version.
FIRE_PERIMETERS_DEFAULT_TIME_BUDGET_SECONDS: Final = 900.0
FIRE_PERIMETERS_MAX_TIME_BUDGET_SECONDS: Final = 3_600.0
FIRE_PERIMETERS_DEFAULT_RETRY_ATTEMPTS: Final = 5
FIRE_PERIMETERS_MAX_RETRY_ATTEMPTS: Final = 10
FIRE_PERIMETERS_DEFAULT_RETRY_BASE_SECONDS: Final = 5.0
FIRE_PERIMETERS_MAX_RETRY_BASE_SECONDS: Final = 60.0
FIRE_PERIMETERS_DEFAULT_RETRY_MAX_SECONDS: Final = 60.0
FIRE_PERIMETERS_MAX_RETRY_MAX_SECONDS: Final = 300.0
FIRE_PERIMETERS_DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 300.0
FIRE_PERIMETERS_MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
FIRE_PERIMETERS_STATEMENT_TIMEOUT_SECONDS: Final = 120
FIRE_PERIMETERS_MIN_DELAY_SECONDS: Final = 0.1
FIRE_PERIMETERS_TIME_BUDGET_OUTCOME: Final = TIME_BUDGET_EXHAUSTED

#: What this writer promises about its own failure policy, CLI surface and reported words; see
#: `pipeline/direct/__init__.py` for the axes and `tests/direct/test_direct_writer_contract.py` for
#: the table all eleven are read as.
#:
#: THE TWO CELLS BELOW ARE NOT THE SAME ANSWER TO THE SAME QUESTION, and the whole "fire-perimeters
#: refuses while watersheds publishes" comparison turns on that. A WFIGS record whose identity will
#: not build is COUNTED as `rejected` and the snapshot publishes (`rows.py:242`) -- byte-identical to
#: what `watersheds` does with `rejected_basins`. A perimeter whose geometry is invalid or empty
#: refuses the WHOLE snapshot (`support.py:75`) -- and so does watersheds (`watersheds/support.py:130`).
#: The two writers' declared contracts are IDENTICAL on both axes. What differs is which defect their
#: live data happens to contain, which is why one lane is blocked in production and the other is not.
#:
#: THE GEOMETRY REFUSAL IS DELIBERATE AND IS NOT THIS PASS'S TO CHANGE. Its basis is not taste:
#: `geo_features_sync_geom` raises SQLSTATE 22023 for an invalid shape and aborts the INSERT that
#: carried it, so PostgreSQL never held such a perimeter either. Dropping it here would publish a
#: version the PostgreSQL population disagrees with; keeping it would publish a shape PostGIS refuses.
#: If an owner decides a named, counted loss is preferable to a blocked lane, the change is to
#: `support.py`'s refusal plus this declaration, together, in one diff.
WRITER_CONTRACT: Final = DirectWriterContract(
    slug="fire-perimeters",
    identity_defect=SKIP_AND_COUNT,
    geometry_defect=REFUSE_WHOLE_RELEASE,
    unconfigured_bbox=REFUSE_UNCONFIGURED_BBOX,
    turn_outcomes=LANE_DAY_OUTCOMES | {TIME_BUDGET_EXHAUSTED},
    flags_absent_on_purpose={
        "--product": "one stream; see products.py. WFIGS _Current is a single current-incident "
        "population, so a product selector here could only ever take one value.",
        "--max-records": "the ceiling is `INGEST_MAX_SOURCE_RECORDS`, shared with every other ArcGIS "
        "walk rather than owned here, and a walk WFIGS itself says was clipped is refused as "
        "`FirePerimetersTruncatedError` (source.py:49) rather than accepted at the cap. A per-writer "
        "spelling would imply this lane may publish a truncated incident population. It may not.",
        "--max-records-per-day": "same reason as `--max-records`; this is a version-stamped lane whose "
        "unit is a snapshot, not a day whose size an operator could bound.",
    },
    policy_basis="An unset INGEST_BBOX REFUSES here where `evacuation_zones` skips, because this "
    "lane's coverage has only ONE bound: skipping would leave the previously published version serving "
    "under a coverage claim this turn never re-proved, and publishing over an unstated extent would "
    "stamp a version whose coverage nobody can cite (source.py:86). Evacuation-zones is bounded twice "
    "and can therefore skip safely; see that writer's own contract for the other half of the split.",
)


class FirePerimetersForwardConfigError(ValueError):
    """Raised when a turn is asked for an unbounded or self-contradictory shape."""


@dataclass(frozen=True, slots=True)
class FirePerimetersForwardConfig:
    """Bound every source request, retry series and contention wait of one turn."""

    max_days: int
    time_budget_seconds: float
    retry_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    contention_timeout_seconds: float
    run_id: str | None = None
    bbox: str | None = None
    today: date | None = None


@dataclass(slots=True)
class MemoizedDirectWatermark:
    """The substituted `LaneWatermarkResolver`: it answers from the turn's ONE capture, never refetches.

    `gap_fill._fill_static_day` reads the watermark immediately BEFORE the export and again AFTER it,
    and re-exports when the two instants differ. That bracket exists because a PostgreSQL export
    infers a snapshot's vintage from its part files' upload time, so a source change committed
    between the SELECT and the PUT would read as captured when it was not.

    THAT RACE CANNOT ARISE HERE, and answering from the memo is what says so honestly rather than
    defeating the check. A direct version's vintage is not inferred: it IS `source.fetched_at`,
    recorded on the capture and written into every row's `updated_at`. WFIGS may well change during
    the upload -- but the thing being written is the immutable payload fetched at that instant, so
    the export's vintage is known exactly and there is nothing for the bracket to prove. A genuinely
    newer population is a NEW version at a NEW day, which the next turn publishes; refetching inside
    the bracket would instead swap the content under a version day already derived from the old
    content, which is the defect the bracket is meant to prevent, introduced by the fix for it.
    """

    watermark: SourceWatermark
    #: How many times the shared driver asked. Two per successful export attempt; purely diagnostic.
    reads: int = field(default=0, init=False)

    async def __call__(
        self,
        session: AsyncSession,  # noqa: ARG002 - uniform resolver shape; this watermark asks no database
        store: ObjectStore,  # noqa: ARG002 - uniform resolver shape; the reading is already in hand
        *,
        today: date,  # noqa: ARG002 - the source's own change time, never this run's date
    ) -> SourceWatermark:
        """Return the turn's single watermark reading; never opens a socket or a session."""
        self.reads += 1
        return self.watermark


def emit(payload: Mapping[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


async def run_fire_perimeters_forward(config: FirePerimetersForwardConfig) -> dict[str, object]:
    """Fetch WFIGS once, decide what version is owed, and publish it if one is."""
    _validate_config(config)
    run_id = config.run_id or f"{FIRE_PERIMETERS_FORWARD_RUN_ID_PREFIX}{uuid.uuid4()}"
    deadline = time.monotonic() + config.time_budget_seconds
    lane = fire_perimeters_lane_registration()

    source = await fetch_fire_perimeters_source(
        bbox=config.bbox,
        retry_attempts=config.retry_attempts,
        retry_base_seconds=config.retry_base_seconds,
        retry_max_seconds=config.retry_max_seconds,
    )
    # DERIVED FROM THE FETCH, NOT FROM THE WALL CLOCK AT TURN START. `resolve_static_lane` refuses a
    # watermark day later than `today` outright ("writing an observed partition dated in the future
    # is never right"), and a turn that started at 23:59:58 UTC and fetched at 00:00:01 would trip
    # exactly that on a lane whose watermark day IS the fetch day. Taking `today` from the fetch
    # instant removes the race rather than widening a tolerance around it.
    today = config.today or source.fetched_at.date()
    population = await asyncio.to_thread(fire_perimeter_population, source)
    emit(
        {
            "event": "fire_perimeters_forward_fetched",
            "run_id": run_id,
            "layer": FIRE_PERIMETERS_STREAM,
            "bbox": source.bbox,
            "fetched_at": source.fetched_at.isoformat(),
            "bytes_read": source.bytes_read,
            "perimeters_seen": len(source.perimeters),
            "rows_conformed": len(population.rows),
            "rejected": population.rejected,
            "collapsed_duplicate_identifiers": population.collapsed,
        }
    )

    store = ObjectStore.from_settings()
    ladder = await _retry_async(
        "fire-perimeters base-rung listing",
        lambda: asyncio.to_thread(read_published_ladder, store),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    reading = await _retry_async(
        "fire-perimeters watermark reading",
        lambda: asyncio.to_thread(read_direct_watermark, store, population, ladder),
        attempts=config.retry_attempts,
        base_seconds=config.retry_base_seconds,
        max_seconds=config.retry_max_seconds,
    )
    verdict = resolve_static_lane(
        watermark=reading.watermark,
        newest_data_day=ladder.newest_data_day,
        newest_data_instant=ladder.newest_data_instant,
        newest_marker_day=ladder.newest_marker_day,
        today=today,
    )
    emit(
        {
            "event": "fire_perimeters_forward_resolved",
            "run_id": run_id,
            "state": verdict.state,
            "version_day": None if verdict.version_day is None else verdict.version_day.isoformat(),
            "detail": verdict.detail,
        }
    )

    if verdict.version_day is None:
        # `current`, `source_empty` or `watermark_unread`: no version is owed, so nothing is written
        # and the turn is a success. This is the ordinary outcome of most ticks.
        return _report(
            run_id,
            lane=lane,
            today=today,
            source_fetched_at=source.fetched_at,
            population_rows=len(population.rows),
            reading=reading,
            ladder=ladder,
            verdict_state=verdict.state,
            verdict_detail=verdict.detail,
            availability=AvailabilityExtensionTally(),
            result=None,
        )

    availability = AvailabilityExtensionTally()
    availability_storage = BotoAvailabilityStorage.from_settings()
    adapter = DirectFirePerimetersAdapter(population=population)
    # BOTH are substituted. Routing only the adapter would leave `_fill_static_day` bracketing the
    # export with the REGISTERED watermark, which reads `geo.features` -- the table this writer
    # exists to stop depending on, and one that is empty or absent by the time this lane matters.
    direct_lane = replace(lane, adapter=adapter, watermark=MemoizedDirectWatermark(watermark=reading.watermark))
    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        result = await _publish_version_with_retries(
            session,
            store,
            direct_lane,
            verdict.version_day,
            today=today,
            run_id=run_id,
            config=config,
            deadline=deadline,
            availability_storage=availability_storage,
            availability=availability,
        )
    emit({"event": "fire_perimeters_forward_version_complete", "run_id": run_id, **result})
    return _report(
        run_id,
        lane=lane,
        today=today,
        source_fetched_at=source.fetched_at,
        population_rows=len(population.rows),
        reading=reading,
        ladder=ladder,
        verdict_state=verdict.state,
        verdict_detail=verdict.detail,
        availability=availability,
        result=result,
    )


async def _publish_version_with_retries(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    day: date,
    *,
    today: date,
    run_id: str,
    config: FirePerimetersForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Acquire the lane-day lock once, then republish under it for every bounded attempt."""
    contention_deadline = time.monotonic() + config.contention_timeout_seconds
    while True:
        async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if granted:
                return await _publish_locked_version_with_retries(
                    session,
                    store,
                    lane,
                    day,
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
            raise DirectFirePerimetersError(
                f"lane-day contention for version {day.isoformat()} exceeded {config.contention_timeout_seconds:g}s"
            )
        delay = min(
            remaining, _retry_delay(1, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds)
        )
        emit(
            {
                "event": "fire_perimeters_forward_contention",
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
    today: date,
    run_id: str,
    config: FirePerimetersForwardConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Rewrite the same version until the whole four-rung ladder verifies, one advisory lock held.

    The POPULATION is never refetched between attempts -- see `adapter.py`'s docstring. What a retry
    buys is another attempt at the OBJECT STORE, which is the only thing that failed if the first
    attempt did.
    """
    for write_attempt in range(1, config.retry_attempts + 1):
        if time.monotonic() >= deadline:
            return _version_result(day, outcome=FIRE_PERIMETERS_TIME_BUDGET_OUTCOME)
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                lane,
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=today,
                # The lock is already held by `_publish_version_with_retries`; the shared driver must
                # not try to take the same key again on the same session.
                lane_day_lock=unlocked_lane_day,
                statement_timeout_seconds=FIRE_PERIMETERS_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except Exception as error:  # reported through the ladder verification below, never swallowed
            with suppress(Exception):
                await session.rollback()
            outcome = "raised"
            parts = rows = written_bytes = 0
            detail = f"{day.isoformat()}: {type(error).__name__}: {error}"

        if outcome == "blocked":
            raise DirectFirePerimetersError(detail or f"fire-perimeters version {day.isoformat()} is blocked")
        if outcome == "absent":
            # `_export_one_day` only governs a day absent after `EmptyPartitionError`, which
            # `adapter.py` refuses to reach. Arriving here means the driver governed a version stamp
            # as absent, which `resolve_static_lane` treats as a FAILED READ rather than coverage --
            # so it is surfaced, not accepted.
            raise DirectFirePerimetersError(
                f"fire-perimeters version {day.isoformat()} was governed ABSENT, but a static lane's day is a "
                f"version stamp and the watermark asserts this version has rows: {detail}"
            )

        verified = False
        verification_error: Exception | None = None
        try:
            statuses = await asyncio.to_thread(_tier_status_for_version, store, day)
            verified = all(status == "data" for status in statuses.values())
        except Exception as error:  # reported below, never silently swallowed
            verification_error = error
        if outcome == "written" and verified:
            return _version_result(
                day, outcome=outcome, parts=parts, rows=rows, written_bytes=written_bytes, detail=detail
            )
        if write_attempt >= config.retry_attempts:
            suffix = (
                f"; verification failed: {type(verification_error).__name__}: {verification_error}"
                if verification_error is not None
                else ""
            )
            raise DirectFirePerimetersError(
                f"fire-perimeters version {day.isoformat()} did not publish a complete four-tier ladder after "
                f"{write_attempt} attempt(s): outcome={outcome}, detail={detail}{suffix}"
            )
        delay = _retry_delay(
            write_attempt, base_seconds=config.retry_base_seconds, max_seconds=config.retry_max_seconds
        )
        emit(
            {
                "event": "fire_perimeters_forward_r2_retry",
                "run_id": run_id,
                "day": day.isoformat(),
                "attempt": write_attempt,
                "outcome": outcome,
                "detail": detail,
                "retry_in_seconds": round(delay, 3),
            }
        )
        await asyncio.sleep(delay)
    raise AssertionError("bounded fire-perimeters publish attempts exhausted")


def _version_result(  # noqa: PLR0913 - one coordinate of the published version per arg
    day: date,
    *,
    outcome: str,
    parts: int = 0,
    rows: int = 0,
    written_bytes: int = 0,
    detail: str | None = None,
) -> dict[str, object]:
    """Render one version's result, in one shape whether it was written, skipped or refused."""
    return {
        "day": day.isoformat(),
        "outcome": outcome,
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
    }


def _tier_status_for_version(store: ObjectStore, day: date) -> dict[ZoomTier, PartitionDayStatus]:
    """Read every rung's completion status for exactly one version day."""
    return {
        tier: partition_day_statuses(
            layer=FIRE_PERIMETERS_STREAM,
            kind=FIRE_PERIMETERS_DIRECT_KIND,
            zoom=tier,
            first_day=day,
            last_day=day,
            keys=store.list_partition_keys(
                FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, tier, year=day.year, month=day.month
            ),
        )[day]
        for tier in FIRE_PERIMETERS_DIRECT_ALL_TIERS
    }


def _report(  # noqa: PLR0913 - one coordinate of the finished turn per arg
    run_id: str,
    *,
    lane: LaneRegistration,
    today: date,
    source_fetched_at: datetime,
    population_rows: int,
    reading: DirectWatermarkReading,
    ladder: PublishedLadder,
    verdict_state: str,
    verdict_detail: str,
    availability: AvailabilityExtensionTally,
    result: dict[str, object] | None,
) -> dict[str, object]:
    """Build the one terminal report a caller parses off stdout.

    `stranded_versions` is reported and NEVER acted on. A static lane's day is a version stamp, so
    re-exporting a half-uploaded past version today would date the CURRENT population as that
    version and manufacture one that never existed -- `gap_fill._static_lane_census` refuses the same
    repair for the same reason, and retracting the residue is an admin action.
    """
    newest_day = ladder.newest_data_day
    return {
        "status": "completed",
        "run_id": run_id,
        "layer": FIRE_PERIMETERS_STREAM,
        "namespace": f"layer={FIRE_PERIMETERS_STREAM}/kind={FIRE_PERIMETERS_DIRECT_KIND}/",
        "nature": lane.nature,
        "today": today.isoformat(),
        "fetched_at": source_fetched_at.isoformat(),
        "perimeters_conformed": population_rows,
        "content_digest": reading.fresh_digest,
        "published_version_read_back": reading.published is not None,
        "row_count_short_circuit": reading.short_circuited,
        "watermark_day": None if reading.watermark.day is None else reading.watermark.day.isoformat(),
        "watermark_instant": (None if reading.watermark.instant is None else reading.watermark.instant.isoformat()),
        "watermark_basis": reading.watermark.basis,
        "static_state": verdict_state,
        "static_detail": verdict_detail,
        "newest_published_version": None if newest_day is None else newest_day.isoformat(),
        "versions_held": ladder.version_count,
        "stranded_versions": [day.isoformat() for day in ladder.stranded_days],
        "versions_published": 0 if result is None else 1,
        **availability.to_summary(),
        "results": [] if result is None else [result],
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
        except Exception as error:  # every object-store read failure is retried the same way
            last_error = error
            if attempt >= attempts:
                break
            delay = _retry_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds)
            emit(
                {
                    "event": "fire_perimeters_forward_retry",
                    "operation": label,
                    "attempt": attempt,
                    "error_type": type(error).__name__,
                    "retry_in_seconds": round(delay, 3),
                }
            )
            await asyncio.sleep(delay)
    assert last_error is not None  # attempts >= 1 is enforced by `_validate_config`
    raise DirectFirePerimetersError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


def _validate_config(config: FirePerimetersForwardConfig) -> None:
    """Fail closed on every process-bound knob before a socket or a session is opened."""
    if config.max_days != FIRE_PERIMETERS_MAX_DAYS:
        raise FirePerimetersForwardConfigError(
            f"--max-days must be {FIRE_PERIMETERS_MAX_DAYS}: fire-perimeters is a static_lookup lane and owes at "
            "most one version at any instant, so a larger number could not be honoured and a turn that quietly "
            "published one version anyway would misreport what it did"
        )
    if not 1 <= config.retry_attempts <= FIRE_PERIMETERS_MAX_RETRY_ATTEMPTS:
        raise FirePerimetersForwardConfigError(
            f"--retry-attempts must be between 1 and {FIRE_PERIMETERS_MAX_RETRY_ATTEMPTS}"
        )
    bounds = {
        "--time-budget-seconds": (config.time_budget_seconds, FIRE_PERIMETERS_MAX_TIME_BUDGET_SECONDS),
        "--retry-base-seconds": (config.retry_base_seconds, FIRE_PERIMETERS_MAX_RETRY_BASE_SECONDS),
        "--retry-max-seconds": (config.retry_max_seconds, FIRE_PERIMETERS_MAX_RETRY_MAX_SECONDS),
        "--contention-timeout-seconds": (
            config.contention_timeout_seconds,
            FIRE_PERIMETERS_MAX_CONTENTION_TIMEOUT_SECONDS,
        ),
    }
    for name, (value, maximum) in bounds.items():
        if not math.isfinite(value) or not FIRE_PERIMETERS_MIN_DELAY_SECONDS <= value <= maximum:
            raise FirePerimetersForwardConfigError(
                f"{name} must be finite and between {FIRE_PERIMETERS_MIN_DELAY_SECONDS:g} and {maximum:g}, "
                f"got {value!r}"
            )
    if config.retry_max_seconds < config.retry_base_seconds:
        raise FirePerimetersForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only fire-perimeters operator. No `--product`: this lane has one."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--max-days", type=int, default=FIRE_PERIMETERS_DEFAULT_MAX_DAYS)
    built.add_argument("--time-budget-seconds", type=float, default=FIRE_PERIMETERS_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument(
        "--bbox",
        default=None,
        help="override INGEST_BBOX for this turn; the extent the published version claims to cover",
    )
    built.add_argument("--retry-attempts", type=int, default=FIRE_PERIMETERS_DEFAULT_RETRY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=FIRE_PERIMETERS_DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=FIRE_PERIMETERS_DEFAULT_RETRY_MAX_SECONDS)
    built.add_argument(
        "--contention-timeout-seconds",
        type=float,
        default=FIRE_PERIMETERS_DEFAULT_CONTENTION_TIMEOUT_SECONDS,
    )
    return built


def parse_args(argv: Sequence[str] | None = None) -> FirePerimetersForwardConfig:
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
    config = FirePerimetersForwardConfig(
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
    except FirePerimetersForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_fire_perimeters_forward(config)
    except FirePerimetersTruncatedError as error:
        # Named separately because retrying cannot clear it: the ceiling, the byte budget and an
        # undeliverable record are properties of the request and the feed, not of one attempt.
        print(
            json.dumps(
                {"status": "failed", "reason": "source_truncated", "error": f"{type(error).__name__}: {error}"},
                sort_keys=True,
            )
        )
        return 1
    except Exception as error:  # the one terminal failure report a caller parses
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "FIRE_PERIMETERS_MAX_DAYS",
    "FIRE_PERIMETERS_TIME_BUDGET_OUTCOME",
    "FirePerimetersForwardConfig",
    "FirePerimetersForwardConfigError",
    "MemoizedDirectWatermark",
    "main",
    "parse_args",
    "parser",
    "run_fire_perimeters_forward",
]
