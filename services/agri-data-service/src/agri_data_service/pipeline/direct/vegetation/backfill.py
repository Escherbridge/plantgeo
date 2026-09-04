"""Reach Parquet parity with Postgres for every vegetation day the legacy owner already governed.

D2's bar (`conductor/tracks/environmental_postgres_retirement_20260904/spec.md`): "a layer's backfill
is complete when Parquet covers at least what PostgreSQL currently holds for it". This module walks
`[registered history_floor, VEGETATION_DIRECT_WRITER_START_DAY]` -- the range `forward.py` never
touches -- and republishes each day whose ladder is not yet `data` at all four rungs, THROUGH THE
EXISTING, UNCHANGED, ALREADY-REVIEWED POSTGRES-READING ADAPTER (`LANE_REGISTRY[VEGETATION_PLANE_STREAM]`,
`pipeline/lanes/vegetation.py::export_vegetation_day`). It never re-derives NDVI from Sentinel-2 for a
day Postgres already computed and checksummed; re-deriving it would risk a second, silently different
value under the same `(cell, observed_day)` grain.

EVERY TURN MUST LEAVE A DURABLE MARK ON EVERY DAY IT SPENDS. Most days in this window are correctly a
governed absence -- `pipeline/parquet/lane_registry.py:882` says so in the registration's own
`floor_basis`, and `docs/lanes/vegetation.md` section 5.3 measures why (cloud screening widens the
Sentinel-2 revisit to a median 7-day gap). An earlier revision pre-checked row existence with a
read-only `EXISTS` and reported a zero-row day as a `no_governed_rows` entry that wrote NOTHING: no
marker, no ledger row, no cursor. Since every turn re-censuses the same window and takes the oldest
incomplete days, such a day stayed `missing` and stayed first forever -- `--max-days 30` produced 30
census entries, zero writes and an unchanged backlog, on that turn and on every turn after it.

THE PRE-CHECK IS GONE, AND THE "GAP" IT WORKED AROUND WAS NOT ONE. `export_vegetation_day` on a
zero-row day calls `store.write_partition`, which raises `EmptyPartitionError` -- and that is exactly
the signal `gap_fill._export_one_day` catches to call `_govern_absent_day`, which marks the day absent
at ALL FOUR rungs (coarse first, base last, rolled back as a unit if any rung refuses) and then lets
`fill_one_lane_day` extend the availability index with `terminal_state="governed_absence"`. Routing a
Postgres-empty day through the SAME registered adapter as every other day therefore produces a durable,
indexed, four-rung governed absence with the exporter's own zero-row result as its proof, and
`_incomplete_days_ascending` skips it from then on. This module still never writes Postgres and never
edits `pipeline/lanes/vegetation.py`.

A DAY THAT RESOLVES TO NEITHER `written` NOR `absent` NO LONGER SPENDS THE TURN'S BUDGET. `raised`,
`blocked`, `contended` and `incomplete_after_write` are all states a day can hold indefinitely, so
counting them against `--max-days` would let one stuck day at the floor hide every day behind it.
They are attempted, reported and stepped over, bounded by `VEGETATION_BACKFILL_MAX_UNRESOLVED_DAYS`
so a systemic failure surfaces in one turn instead of burning the whole wall clock; `--from-day`
raises this turn's floor for the case where an operator must step past one deliberately.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.pipeline.direct.vegetation.forward import (
    VEGETATION_DIRECT_ALL_TIERS,
    _tier_status_day,
    _tier_status_window,
)
from agri_data_service.pipeline.direct.vegetation.products import VEGETATION_DIRECT_WRITER_START_DAY
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

VEGETATION_BACKFILL_RUN_ID_PREFIX: Final = "vegetation-sentinel2-ndvi-backfill:"
VEGETATION_BACKFILL_DEFAULT_MAX_DAYS: Final = 5
VEGETATION_BACKFILL_MAX_DAYS: Final = 30
VEGETATION_BACKFILL_DEFAULT_TIME_BUDGET_SECONDS: Final = 900.0
VEGETATION_BACKFILL_STATEMENT_TIMEOUT_SECONDS: Final = 120

#: How many days a turn may attempt and fail to make durable before it stops and reports. Generous
#: enough that a few transient lock contentions never stall the sweep, small enough that a systemic
#: failure -- a bucket that refuses writes, a warehouse missing `agri.spatial_cell` -- is reported in
#: one turn rather than after the whole wall clock has been spent retrying the same fault.
VEGETATION_BACKFILL_MAX_UNRESOLVED_DAYS: Final = 25

#: The two outcomes that leave a day in a state it is never selected for again: `written` (parts plus a
#: completion marker at all four rungs, read back before it is claimed) and `absent` (a governed
#: absence at all four rungs). Everything else -- `raised`, `blocked`, `contended`,
#: `incomplete_after_write` -- is a day this turn touched and did NOT settle, so it must not be
#: counted against `--max-days`; see this module's docstring.
VEGETATION_BACKFILL_DURABLE_OUTCOMES: Final[frozenset[str]] = frozenset({"written", "absent"})


@dataclass(frozen=True, slots=True)
class VegetationBackfillConfig:
    """Bound one backfill turn's day count and wall clock."""

    max_days: int
    time_budget_seconds: float
    run_id: str | None = None
    #: Raises THIS TURN's floor above the registered `history_floor`, so an operator can step past a
    #: day that keeps resolving to `raised`/`blocked` and needs an admin. It never lowers the floor
    #: and never touches the ceiling: the ownership boundary is not an operator knob.
    from_day: date | None = None


class VegetationBackfillConfigError(ValueError):
    """Raised when a backfill turn is asked for an unbounded shape."""


class VegetationBackfillError(RuntimeError):
    """Raised when a day cannot be resolved to either a governed publish or a reported gap."""


def emit(payload: dict[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def backfill_floor() -> date:
    """Return the first day this backfill may ever consider: the lane's own registered history floor."""
    return LANE_REGISTRY[VEGETATION_PLANE_STREAM].history_floor


def backfill_ceiling() -> date:
    """Return the last day this backfill owns -- the ownership boundary day ITSELF, not the day before.

    THE BOUNDARY DAY BELONGS TO EXACTLY ONE DRIVER, AND IT IS THIS ONE.
    `adapter.py::refuse_pre_ownership_day` refuses `day <= VEGETATION_DIRECT_WRITER_START_DAY` and
    `forward.py::history_floor` starts at `START + 1`, so ceilinging here at `START - 1` left `START`
    itself owned by NEITHER driver at any value of the constant. The two windows now abut exactly:
    `backfill_ceiling() + 1 day == forward.history_floor()`, asserted in
    `tests/direct/test_vegetation_adapter.py` rather than only described.
    """
    return VEGETATION_DIRECT_WRITER_START_DAY


async def run_vegetation_backfill(config: VegetationBackfillConfig) -> dict[str, object]:
    """Republish the oldest incomplete-ladder day at a time, through the unchanged registered adapter.

    THIS DEPENDS ON `LANE_REGISTRY[vegetation]` STILL BEING THE POSTGRES-READING `_fill_vegetation`
    (`tests/direct/test_direct_package_registration.py` records that it is, and that routing it to
    this package is owed at the join step). If a join agent re-routes the registration to
    `DirectVegetationAdapter`, every day here fails LOUDLY rather than silently re-deriving history
    from Sentinel-2: `adapter.py::refuse_pre_ownership_day` refuses the whole backfill window by
    construction, so the turn reports `raised` days and trips the unresolved-day budget.
    """
    _validate_config(config)
    run_id = config.run_id or f"{VEGETATION_BACKFILL_RUN_ID_PREFIX}{uuid.uuid4()}"
    store = ObjectStore.from_settings()
    availability_storage = BotoAvailabilityStorage.from_settings()
    availability = AvailabilityExtensionTally()
    deadline = time.monotonic() + config.time_budget_seconds

    lane = LANE_REGISTRY[VEGETATION_PLANE_STREAM]
    floor = max(backfill_floor(), config.from_day) if config.from_day is not None else backfill_floor()
    ceiling = backfill_ceiling()
    published: list[dict[str, object]] = []
    if ceiling < floor:
        return _report(run_id, floor=floor, ceiling=ceiling, backlog=0, published=published, availability=availability)

    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        statuses = await asyncio.to_thread(_tier_status_window, store, floor, ceiling)
        backlog = _incomplete_days_ascending(statuses)
        published = await _walk_backlog(
            session,
            store,
            lane=lane,
            backlog=backlog,
            run_id=run_id,
            config=config,
            deadline=deadline,
            availability_storage=availability_storage,
            availability=availability,
        )

    return _report(
        run_id,
        floor=floor,
        ceiling=ceiling,
        backlog=len(backlog),
        published=published,
        availability=availability,
    )


async def _walk_backlog(  # noqa: PLR0913 - the store, lane, budget and tallies are distinct coordinates
    session: AsyncSession,
    store: ObjectStore,
    *,
    lane: LaneRegistration,
    backlog: tuple[date, ...],
    run_id: str,
    config: VegetationBackfillConfig,
    deadline: float,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> list[dict[str, object]]:
    """Attempt the oldest days until `--max-days` of them are DURABLE, stepping over the ones that are not.

    `--max-days` bounds the days this turn SETTLES, not the days it touches: a day that comes back
    `raised` or `blocked` will come back the same way next turn, so spending the budget on it is how
    one stuck day at the floor hides the whole backlog behind it. Two other bounds still stop the
    walk -- the turn's wall clock and `VEGETATION_BACKFILL_MAX_UNRESOLVED_DAYS` -- so "step over it"
    can never become "walk 1,500 days retrying the same fault".
    """
    published: list[dict[str, object]] = []
    durable = 0
    unresolved = 0
    for day in backlog:
        if durable >= config.max_days:
            break
        if unresolved >= VEGETATION_BACKFILL_MAX_UNRESOLVED_DAYS:
            published.append(
                {
                    "day": day.isoformat(),
                    "outcome": "unresolved_day_budget_exhausted",
                    "detail": (
                        f"{unresolved} day(s) this turn resolved to neither a publication nor a governed "
                        "absence, which is a fault this walk cannot work around; the turn stops here rather "
                        "than spending its wall clock re-attempting it"
                    ),
                }
            )
            break
        if time.monotonic() >= deadline:
            published.append({"day": day.isoformat(), "outcome": "time_budget_exhausted"})
            break
        entry = await _publish_from_postgres(
            session,
            store,
            lane=lane,
            day=day,
            run_id=run_id,
            availability_storage=availability_storage,
            availability=availability,
        )
        published.append(entry)
        if entry["outcome"] in VEGETATION_BACKFILL_DURABLE_OUTCOMES:
            durable += 1
        else:
            unresolved += 1
    return published


async def _publish_from_postgres(  # noqa: PLR0913 - one lane-day coordinate per argument
    session: AsyncSession,
    store: ObjectStore,
    *,
    lane: LaneRegistration,
    day: date,
    run_id: str,
    availability_storage: AvailabilityStorage,
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Publish one day under the lane-day lock, through the registered Postgres-reading adapter unchanged.

    A DAY POSTGRES HOLDS NOTHING FOR COMES BACK `absent`, NOT AS A CENSUS NOTE. The exporter's
    `store.write_partition` raises `EmptyPartitionError` on the zero-row table, `_export_one_day`
    catches exactly that to call `_govern_absent_day`, and the four-rung marker plus its availability
    entry are what make the day durable. Nothing here needs to pre-empt it -- see this module's
    docstring, "THE PRE-CHECK IS GONE".

    NO `deadline` ARGUMENT. The turn's wall clock is enforced by `_walk_backlog` before this is ever
    called; carrying a second copy of it here read as a bound this function applied, and it applied
    none. The bound one lane-day genuinely carries is
    `VEGETATION_BACKFILL_STATEMENT_TIMEOUT_SECONDS`, which is passed below.
    """
    async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
        if not granted:
            return {"day": day.isoformat(), "outcome": "lock_contended"}
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                lane,
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=datetime.now(UTC).date(),
                lane_day_lock=unlocked_lane_day,
                statement_timeout_seconds=VEGETATION_BACKFILL_STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
            await session.rollback()
        except Exception as error:
            await session.rollback()
            return {"day": day.isoformat(), "outcome": "raised", "detail": f"{type(error).__name__}: {error}"}
    verification_detail = await asyncio.to_thread(_tier_status_day, store, day) if outcome == "written" else None
    complete = verification_detail is None or all(status == "data" for status in verification_detail.values())
    return {
        "day": day.isoformat(),
        "outcome": outcome if complete else "incomplete_after_write",
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "detail": detail,
        "tier_statuses": None if verification_detail is None else dict(verification_detail),
    }


def _incomplete_days_ascending(statuses: Mapping[ZoomTier, Mapping[date, PartitionDayStatus]]) -> tuple[date, ...]:
    """Every day in the censused window whose four rungs are not all `data`, oldest first.

    Oldest first, not newest-first like `forward.py`: backfill is closing a fixed historical debt with
    no settled edge to chase, so a stable systematic sweep is the more useful default. A day whose
    base rung is `absent` (a governed absence already recorded by some earlier run) is treated as
    already complete here -- it needs no republish, and this module makes no absence-recheck claim
    the way `forward.py`'s mirrored-past machinery does for a live, still-moving catalogue.
    """
    days = tuple(statuses[VEGETATION_DIRECT_ALL_TIERS[0]])
    pending: list[date] = []
    for day in sorted(days):
        rung = {tier: statuses[tier][day] for tier in VEGETATION_DIRECT_ALL_TIERS}
        if rung[LANE_BASE_ZOOM_TIER] == "absent":
            continue
        if any(status != "data" for status in rung.values()):
            pending.append(day)
    return tuple(pending)


def _report(  # noqa: PLR0913 - the window, the backlog and the two tallies are separate facts
    run_id: str,
    *,
    floor: date,
    ceiling: date,
    backlog: int,
    published: list[dict[str, object]],
    availability: AvailabilityExtensionTally,
) -> dict[str, object]:
    """Render the one terminal report a caller parses.

    `durable_days` AND `unresolved_days` ARE BOTH REPORTED, because a turn that touched 30 days and
    settled none of them is the exact failure the day budget now steps over -- and a report showing
    only how many days were attempted would read as progress while the backlog stood still.
    """
    durable = sum(1 for entry in published if entry.get("outcome") in VEGETATION_BACKFILL_DURABLE_OUTCOMES)
    report: dict[str, object] = {
        "status": "completed",
        "run_id": run_id,
        "layer": VEGETATION_PLANE_STREAM,
        "backfill_floor": floor.isoformat(),
        "backfill_ceiling": ceiling.isoformat(),
        "backlog_days": backlog,
        "durable_days": durable,
        "unresolved_days": len(published) - durable,
        **availability.to_summary(),
        "days": published,
    }
    emit({"event": "vegetation_backfill_complete", **report})
    return report


def _validate_config(config: VegetationBackfillConfig) -> None:
    """Fail closed on every process-bound knob before a socket or a session is opened."""
    if not 1 <= config.max_days <= VEGETATION_BACKFILL_MAX_DAYS:
        raise VegetationBackfillConfigError(f"--max-days must be between 1 and {VEGETATION_BACKFILL_MAX_DAYS}")
    if config.time_budget_seconds <= 0:
        raise VegetationBackfillConfigError("--time-budget-seconds must be positive")
    if config.from_day is not None and config.from_day > backfill_ceiling():
        raise VegetationBackfillConfigError(
            f"--from-day must be on or before this backfill's ceiling {backfill_ceiling().isoformat()}; "
            "everything after it belongs to the direct forward writer"
        )


def _operator_day(text_value: str) -> date:
    """Parse one `--from-day` argument as a strict ISO calendar day, refusing anything looser."""
    try:
        return date.fromisoformat(text_value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text_value!r} is not an ISO calendar day (YYYY-MM-DD)") from error


def parser() -> argparse.ArgumentParser:
    """Build the bounded, backward-only vegetation backfill operator."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--max-days", type=int, default=VEGETATION_BACKFILL_DEFAULT_MAX_DAYS)
    built.add_argument("--time-budget-seconds", type=float, default=VEGETATION_BACKFILL_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument(
        "--from-day",
        type=_operator_day,
        default=None,
        help=(
            "raise THIS TURN's floor to this ISO day, to step past a day that keeps resolving to "
            "raised/blocked and needs an admin; it never lowers the floor and never moves the ceiling"
        ),
    )
    return built


def parse_args(argv: Sequence[str] | None = None) -> VegetationBackfillConfig:
    """Validate every operator input at the boundary and hand back one bounded turn."""
    built = parser()
    arguments = built.parse_args(argv)
    config = VegetationBackfillConfig(
        max_days=arguments.max_days,
        time_budget_seconds=arguments.time_budget_seconds,
        run_id=arguments.run_id,
        from_day=arguments.from_day,
    )
    try:
        _validate_config(config)
    except VegetationBackfillConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded backfill turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_vegetation_backfill(config)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "VEGETATION_BACKFILL_DURABLE_OUTCOMES",
    "VEGETATION_BACKFILL_MAX_UNRESOLVED_DAYS",
    "VegetationBackfillConfig",
    "VegetationBackfillConfigError",
    "VegetationBackfillError",
    "backfill_ceiling",
    "backfill_floor",
    "main",
    "parse_args",
    "parser",
    "run_vegetation_backfill",
]
