"""Settle a lane's planned windows against the days its layer actually holds, so a replan re-walks nothing landed.

Planning a lane creates one work item for EVERY window from its floor to today -- roughly 1,900 for FIRMS
at 5-day windows from 2000-11-01. A few hundred of those already landed, walked by the bash driver this
package replaces. Re-walking a landed window is not a correctness problem (the feature writer's diff
rejects an unchanged payload, so it writes zero rows by construction) but at a measured 11.5 minutes for
one peak-season FIRMS day it is many hours of fetch spent proving something already true.

**The coverage is derived from the DATA, never from the bash cursor files, and that is the whole point.**
`.firms-archive-cursor` recorded where the walk had *reached*; it did not record what had *landed*. On the
first full pass those two numbers differed by 169 of 298 windows -- the driver hit `ConnectError`, advanced
its cursor past every failure, wrote a completion sentinel and reported success, and 2.5 years of fire
history went missing while every file on disk said the walk was done. Importing those files would import
exactly that lie and mark 169 empty windows succeeded. Reading `geo.feature_observation_day` back out of
`geo.features` cannot: a day is covered because rows for it exist, or it is not covered.

That is also why the cursor and failure files need no migration at all when the bash drivers are retired.
They can be abandoned where they lie; this module derives the truth they were a bad proxy for.

Three outcomes per window, and only one of them settles anything:

- **covered** -- every UTC day of the window is present in the layer. Marked `succeeded`.
- **partial** -- some days present, some absent. STAYS QUEUED. Partial is not landed.
- **absent** -- no day of the window is present. Stays queued.

Dry run is the default. An `--apply` that mis-marked windows would recreate the silent hole from the other
direction, so the report names the span it would settle and samples the windows themselves.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.archive_walk import (
    ArchiveWalkPayloadError,
    ArchiveWindowRequest,
    archive_lane_definition_name,
    archive_lane_run_key,
    archive_source,
)
from agri_data_service.ingest.writer import resolve_layer_id
from agri_data_service.jobs.lease import apply_statement_timeout, canonical_json, fetch_rows, required_column
from agri_data_service.jobs.worker import refresh_job_run_rollup

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.lanes import BackfillLane
    from agri_data_service.ingest.source import HistoryWindow

CoverageVerdict = Literal["covered", "partial", "absent"]

# Mirrors `validation._FEATURE_OBSERVED_DAYS` exactly, and deliberately so: a window is "landed" here only
# if the completeness report can also see it. The two filters that make the difference are
# `status = 'published'` and `geometry_id IS NOT NULL` -- readObservationWindows requires both, so a row
# missing either is drawn on the map but invisible to the time axis. Counting such a row as coverage would
# mark a window succeeded whose days the slider still cannot reach.
PUBLISHED_FEATURE_STATUS: Final = "published"

# The error direction is not symmetric and this filter is chosen for that reason. Under-counting coverage
# costs one re-walk of a window that had in fact landed -- minutes, and the writer rejects the unchanged
# payload anyway. Over-counting marks a window succeeded that never landed, which is the exact failure this
# package exists to make impossible. When in doubt, do not mark.
_COVERAGE_BIAS_NOTE: Final = "a window is marked landed only when every one of its days is already served"

# One row per observed day of one layer. FIRMS' floor is 2000-11-01, so the widest lane owes about 9,400
# days; 50,000 is five times that and still trivial to hold. Hitting it is REFUSED rather than truncated --
# a truncated day set invents absent days, and an absent day at the tail is what turns a covered window
# into a partial one and leaves it queued forever.
MAX_OBSERVED_DAY_ROWS: Final = 50_000

# One row per planned window of one run. FIRMS plans ~1,900; 20,000 is an order of magnitude of headroom.
# Refused rather than truncated for the same reason: an unread work item is a window this reconciliation
# would silently decline to consider.
MAX_LANE_WINDOW_ROWS: Final = 20_000

# Per category, in the printed report. Everything is counted; only the listing is bounded, and every
# category carries the number it omitted alongside the sample.
MAX_REPORTED_WINDOWS: Final = 50

# The work-item states a reconciliation may settle. All three are claimable and unowned, so writing a
# terminal status races nothing.
#
# `leased` and `running` are deliberately absent: a live worker holds the fence on those, and writing a
# terminal status behind a live lease is precisely the corruption the fencing token exists to prevent --
# the worker would keep walking and then find its own completion refused.
#
# `dead_letter` is absent for a different and stronger reason: it is EVIDENCE. A dead letter is the durable
# record that eight attempts failed on that window, and jobs/AGENTS.md is explicit that a shard which
# quietly reports success after exhausting its retries is indistinguishable from one that worked. Flipping
# one to `succeeded` because its days happen to be present now would be that same erasure, run backwards.
# A dead-lettered window whose days ARE present is reported (`dead_lettered_covered`) so an operator can
# see it and requeue it deliberately, which is a decision a person makes, not one this verb makes for them.
RECONCILABLE_WORK_ITEM_STATES: Final[frozenset[str]] = frozenset({"queued", "retry_wait", "deferred"})

DEAD_LETTER_WORK_ITEM_STATE: Final = "dead_letter"

# The states that mean another process owns the row right now.
HELD_WORK_ITEM_STATES: Final[frozenset[str]] = frozenset({"leased", "running"})

# Where a reconciliation records itself: the work item's own `payload`, and NOT a `job_attempt` row.
# Fabricating an attempt would write a worker id, a fencing token and a wall-clock duration into the ledger
# for work this process never performed, and every later query that counts attempts would read that lie.
# `ArchiveWindowRequest.from_payload` reads only the keys it names and ignores the rest, so the marker is
# inert to the handler while staying queryable as `payload -> 'reconciled_from_observed_coverage'`.
RECONCILIATION_MARKER_KEY: Final = "reconciled_from_observed_coverage"

_ONE_MICROSECOND: Final = timedelta(microseconds=1)


class ReconciliationError(RuntimeError):
    """Raised when a lane cannot be reconciled from the data, so nothing is settled on a guess."""


class ReconciliationScanTooLargeError(ReconciliationError):
    """Raised when a scan hits its row cap; truncating it would invent absent days or unread windows."""


@dataclass(frozen=True, slots=True)
class WindowCoverage:
    """One planned window measured against the days its layer holds, and what that measurement decided."""

    shard_key: str
    status: str
    first_day: date
    last_day: date
    expected_days: int
    observed_days: int
    verdict: CoverageVerdict
    missing_sample: tuple[date, ...]
    omitted_missing_days: int

    def to_summary(self) -> dict[str, object]:
        """Render one window row, naming the days it is short of rather than only how many."""
        summary: dict[str, object] = {
            "shard_key": self.shard_key,
            "status": self.status,
            "first_day": self.first_day.isoformat(),
            "last_day": self.last_day.isoformat(),
            "expected_days": self.expected_days,
            "observed_days": self.observed_days,
            "verdict": self.verdict,
        }
        if self.missing_sample:
            summary["missing_days"] = [day.isoformat() for day in self.missing_sample]
            summary["omitted_missing_days"] = self.omitted_missing_days
        return summary


@dataclass(frozen=True, slots=True)
class CoverageCategory:
    """One verdict's windows, bounded for printing but never for counting."""

    verdict: CoverageVerdict
    window_count: int
    windows: tuple[WindowCoverage, ...]
    omitted_window_count: int
    first_day: date | None
    last_day: date | None

    def to_summary(self) -> dict[str, object]:
        """Render the category: how many, over which span, and a bounded sample of the windows themselves."""
        return {
            "window_count": self.window_count,
            "first_day": None if self.first_day is None else self.first_day.isoformat(),
            "last_day": None if self.last_day is None else self.last_day.isoformat(),
            "windows": [window.to_summary() for window in self.windows],
            "omitted_window_count": self.omitted_window_count,
        }


@dataclass(frozen=True, slots=True)
class LaneReconciliation:
    """What one lane's planned windows measured against its layer, and what -- if anything -- was settled."""

    lane: str
    definition: str
    run_key: str
    job_run_id: uuid.UUID | None
    layer_reference: str
    applied: bool
    observed_day_count: int
    first_observed_day: date | None
    last_observed_day: date | None
    planned_window_count: int
    covered: CoverageCategory
    partial: CoverageCategory
    absent: CoverageCategory
    settled_window_count: int
    held_shard_keys: tuple[str, ...]
    dead_lettered_shard_keys: tuple[str, ...]
    dead_lettered_covered: tuple[str, ...]
    marked_succeeded_count: int

    @property
    def would_settle(self) -> int:
        """Windows this run would mark succeeded; equal to `marked_succeeded_count` once `--apply` has run."""
        return self.covered.window_count

    def to_summary(self) -> dict[str, object]:
        """Render the whole reconciliation as the one JSON line a cron log and an operator both read."""
        return {
            "lane": self.lane,
            "definition": self.definition,
            "run_key": self.run_key,
            "job_run_id": None if self.job_run_id is None else str(self.job_run_id),
            "layer": self.layer_reference,
            "applied": self.applied,
            "state": "applied" if self.applied else "dry_run",
            "observed_day_count": self.observed_day_count,
            "first_observed_day": None if self.first_observed_day is None else self.first_observed_day.isoformat(),
            "last_observed_day": None if self.last_observed_day is None else self.last_observed_day.isoformat(),
            "planned_window_count": self.planned_window_count,
            "settled_window_count": self.settled_window_count,
            "covered": self.covered.to_summary(),
            "partial": self.partial.to_summary(),
            "absent": self.absent.to_summary(),
            "held_shard_keys": list(self.held_shard_keys),
            "dead_lettered_shard_keys": list(self.dead_lettered_shard_keys),
            "dead_lettered_covered": list(self.dead_lettered_covered),
            "would_mark_succeeded": self.would_settle,
            "marked_succeeded": self.marked_succeeded_count,
            "coverage_rule": _COVERAGE_BIAS_NOTE,
        }


# ---------------------------------------------------------------------------------------------------------------
# Pure logic. Everything above the SQL is testable with no database, which is where this module's credibility
# lives: the SQL only lists days and windows, the rule that decides whether a window is landed is here.
# ---------------------------------------------------------------------------------------------------------------


def window_days(window: HistoryWindow) -> tuple[date, ...]:
    """Every UTC day a half-open window touches; a window ending at midnight does not own that day."""
    first = window.start.astimezone(UTC).date()
    # One microsecond before the exclusive end. A grid window is `[2000-11-01T00:00Z, 2000-11-06T00:00Z)`
    # and owns 11-01 through 11-05, not 11-06 -- counting the end day would demand a day the window never
    # asked upstream for, and every window on the grid would measure as partial forever.
    last = (window.end.astimezone(UTC) - _ONE_MICROSECOND).date()
    if last < first:
        raise ReconciliationError(f"window {window.start.isoformat()}..{window.end.isoformat()} touches no whole day")
    return tuple(first + timedelta(days=offset) for offset in range((last - first).days + 1))


def window_coverage(
    shard_key: str,
    status: str,
    window: HistoryWindow,
    observed: frozenset[date],
    *,
    max_reported_missing_days: int = MAX_REPORTED_WINDOWS,
) -> WindowCoverage:
    """Measure one window against the observed days, refusing to call anything but full coverage landed.

    `partial` is NOT landed, and that is the load-bearing decision in this module. A window missing some of
    its days is missing exactly the days the walk still owes, and nothing visible from here distinguishes a
    day upstream genuinely published nothing for from a day the fetch never reached. Settling it would
    write the same silent hole the bash driver wrote, from the other direction -- and unlike the bash there
    would be no failure file to contradict it. So a partial window stays queued and the walk re-fetches it;
    the days that already landed cost nothing to re-walk, because the writer's diff rejects them.
    """
    days = window_days(window)
    missing = tuple(day for day in days if day not in observed)
    observed_count = len(days) - len(missing)
    if not missing:
        verdict: CoverageVerdict = "covered"
    elif observed_count == 0:
        verdict = "absent"
    else:
        verdict = "partial"
    return WindowCoverage(
        shard_key=shard_key,
        status=status,
        first_day=days[0],
        last_day=days[-1],
        expected_days=len(days),
        observed_days=observed_count,
        verdict=verdict,
        missing_sample=missing[:max_reported_missing_days],
        omitted_missing_days=max(0, len(missing) - max_reported_missing_days),
    )


def build_coverage_category(
    verdict: CoverageVerdict,
    windows: Sequence[WindowCoverage],
    *,
    max_reported_windows: int = MAX_REPORTED_WINDOWS,
) -> CoverageCategory:
    """Fold one verdict's windows into a printable category: the count, the calendar span, a bounded sample."""
    ordered = sorted(windows, key=lambda window: (window.first_day, window.shard_key))
    return CoverageCategory(
        verdict=verdict,
        window_count=len(ordered),
        windows=tuple(ordered[:max_reported_windows]),
        omitted_window_count=max(0, len(ordered) - max_reported_windows),
        # The span is reported even when the sample is truncated: "this would settle 2000-11 through
        # 2022-07" is the single fact a human most needs to sanity-check before spending `--apply`.
        first_day=ordered[0].first_day if ordered else None,
        last_day=max(window.last_day for window in ordered) if ordered else None,
    )


def reconciliation_marker(coverage: WindowCoverage, *, layer_reference: str) -> dict[str, object]:
    """The record a settled window carries on its own payload, stating what was measured rather than asserted."""
    return {
        "layer": layer_reference,
        "expected_days": coverage.expected_days,
        "observed_days": coverage.observed_days,
        "first_day": coverage.first_day.isoformat(),
        "last_day": coverage.last_day.isoformat(),
        "previous_status": coverage.status,
        "tool": "jobs-reconcile-lane",
    }


# ---------------------------------------------------------------------------------------------------------------
# SQL. Schema-qualified (nothing in this service sets `search_path`), parameterised, and opening with a
# `-- <name>` marker the unit tests match on -- the same convention `jobs/lease.py` and `validation.py` use.
# Each statement lives in its own file under `sql/ingest/`, where its parameters, its rationale and a
# clause-by-clause walkthrough are documented. The file is named after the constant below while the marker keeps
# its `reconcile_` prefix, because `tests/test_ingest_reconcile.py` hard-codes the marker strings; each file's
# header records that divergence. The "no colon in a comment" rule now lives in sql/AGENTS.md and in every file.
# ---------------------------------------------------------------------------------------------------------------

_OBSERVED_LAYER_DAYS: Final = text(load_query_sql("ingest/observed_layer_days.sql"))

_LANE_RUN_WINDOWS: Final = text(load_query_sql("ingest/lane_run_windows.sql"))

_MARK_WINDOWS_RECONCILED: Final = text(load_query_sql("ingest/mark_windows_reconciled.sql"))


# ---------------------------------------------------------------------------------------------------------------
# The async entry points
# ---------------------------------------------------------------------------------------------------------------


async def observed_layer_days(session: AsyncSession, layer_reference: str) -> frozenset[date]:
    """Every UTC day the lane's target layer already serves, read through the same rule the report applies."""
    layer_id = await resolve_layer_id(session, layer_reference)
    rows = await fetch_rows(
        session,
        _OBSERVED_LAYER_DAYS,
        {
            "layer_id": layer_id,
            "published_status": PUBLISHED_FEATURE_STATUS,
            "row_limit": MAX_OBSERVED_DAY_ROWS,
        },
    )
    if len(rows) >= MAX_OBSERVED_DAY_ROWS:
        raise ReconciliationScanTooLargeError(
            f"layer {layer_reference!r} reports at least {MAX_OBSERVED_DAY_ROWS} observed days; raise "
            "MAX_OBSERVED_DAY_ROWS rather than reconciling against a truncated day set"
        )
    return frozenset(required_column(row, "observed_day", date) for row in rows)


async def _read_lane_windows(
    session: AsyncSession,
    logical_run_key: str,
) -> tuple[uuid.UUID | None, tuple[Mapping[str, object], ...]]:
    """Read every planned window of the lane's run, refusing a result that hit its cap rather than truncating."""
    rows = await fetch_rows(
        session,
        _LANE_RUN_WINDOWS,
        {"logical_run_key": logical_run_key, "row_limit": MAX_LANE_WINDOW_ROWS},
    )
    if len(rows) >= MAX_LANE_WINDOW_ROWS:
        raise ReconciliationScanTooLargeError(
            f"run {logical_run_key!r} holds at least {MAX_LANE_WINDOW_ROWS} windows; raise MAX_LANE_WINDOW_ROWS "
            "rather than reconciling against a truncated window set"
        )
    if not rows:
        return None, ()
    return required_column(rows[0], "job_run_id", uuid.UUID), tuple(rows)


def _window_request(row: Mapping[str, object]) -> ArchiveWindowRequest:
    """Read one stored window payload, naming the shard whose record no longer parses rather than skipping it."""
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise ReconciliationError(f"work item {row.get('shard_key')!r} stores a payload that is not a JSON object")
    try:
        return ArchiveWindowRequest.from_payload(payload)
    except ArchiveWalkPayloadError as error:
        raise ReconciliationError(f"work item {row.get('shard_key')!r} stores no walkable window: {error}") from error


async def reconcile_lane(
    session: AsyncSession,
    lane: BackfillLane,
    *,
    apply_changes: bool = False,
    layer_reference: str | None = None,
    max_reported_windows: int = MAX_REPORTED_WINDOWS,
) -> LaneReconciliation:
    """Measure a lane's planned windows against its layer's observed days and, with `apply_changes`, settle them.

    Read-only unless `apply_changes` is set. The caller commits, exactly as `plan_archive_lane` leaves it, so
    a dry run that is never committed writes nothing even if a future caller forgets which mode it asked for.
    """
    await apply_statement_timeout(session)
    resolved_layer = layer_reference if layer_reference is not None else archive_source(lane).layer_reference()
    observed = await observed_layer_days(session, resolved_layer)
    run_key = archive_lane_run_key(lane)
    job_run_id, rows = await _read_lane_windows(session, run_key)

    by_verdict: dict[CoverageVerdict, list[WindowCoverage]] = {"covered": [], "partial": [], "absent": []}
    held: list[str] = []
    dead_lettered: list[str] = []
    dead_lettered_covered: list[str] = []
    settled = 0
    for row in rows:
        shard_key = required_column(row, "shard_key", str)
        status = required_column(row, "status", str)
        coverage = window_coverage(
            shard_key,
            status,
            _window_request(row).window,
            observed,
            max_reported_missing_days=max_reported_windows,
        )
        if status in HELD_WORK_ITEM_STATES:
            held.append(shard_key)
            continue
        if status == DEAD_LETTER_WORK_ITEM_STATE:
            dead_lettered.append(shard_key)
            if coverage.verdict == "covered":
                dead_lettered_covered.append(shard_key)
            continue
        if status not in RECONCILABLE_WORK_ITEM_STATES:
            # `succeeded` and `cancelled`. Already settled, by a walk or by an operator, and counted so the
            # planned total and the three verdict counts still add up for whoever reads the line.
            settled += 1
            continue
        by_verdict[coverage.verdict].append(coverage)

    covered = build_coverage_category("covered", by_verdict["covered"], max_reported_windows=max_reported_windows)
    marked = 0
    if apply_changes and job_run_id is not None and by_verdict["covered"]:
        marked = await _mark_windows_reconciled(
            session,
            job_run_id=job_run_id,
            windows=by_verdict["covered"],
            layer_reference=resolved_layer,
        )
    observed_days = sorted(observed)
    return LaneReconciliation(
        lane=lane.name,
        definition=archive_lane_definition_name(lane),
        run_key=run_key,
        job_run_id=job_run_id,
        layer_reference=resolved_layer,
        applied=apply_changes,
        observed_day_count=len(observed_days),
        first_observed_day=observed_days[0] if observed_days else None,
        last_observed_day=observed_days[-1] if observed_days else None,
        planned_window_count=len(rows),
        covered=covered,
        partial=build_coverage_category("partial", by_verdict["partial"], max_reported_windows=max_reported_windows),
        absent=build_coverage_category("absent", by_verdict["absent"], max_reported_windows=max_reported_windows),
        settled_window_count=settled,
        held_shard_keys=tuple(held),
        dead_lettered_shard_keys=tuple(dead_lettered),
        dead_lettered_covered=tuple(dead_lettered_covered),
        marked_succeeded_count=marked,
    )


async def _mark_windows_reconciled(
    session: AsyncSession,
    *,
    job_run_id: uuid.UUID,
    windows: Sequence[WindowCoverage],
    layer_reference: str,
) -> int:
    """Settle every covered window in one statement, then let the rollup recompute the run's counters.

    `refresh_job_run_rollup` and not an incremental bump: `ck_job_run_work_item_counts_within_total` is
    IMMEDIATE, and a reconciliation settles hundreds of windows at once, so the one shape that cannot
    transiently violate the sum is the rollup's single statement of absolute recomputed values.
    """
    marked = [
        {
            "shard_key": window.shard_key,
            # Rendered to TEXT here and cast back to jsonb inside the statement, the same double hop
            # `open_job_run` puts a work item's payload through, because `jsonb_to_recordset` reads a
            # nested object as its own jsonb column only when the column list says so and a text column
            # round-trips unambiguously through every driver.
            "marker": canonical_json(reconciliation_marker(window, layer_reference=layer_reference)),
        }
        for window in windows
    ]
    rows = await fetch_rows(
        session,
        _MARK_WINDOWS_RECONCILED,
        {
            "job_run_id": job_run_id,
            "marker_key": RECONCILIATION_MARKER_KEY,
            "marked": json.dumps(marked, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        },
    )
    await refresh_job_run_rollup(session, job_run_id)
    return len(rows)


__all__ = [
    "DEAD_LETTER_WORK_ITEM_STATE",
    "HELD_WORK_ITEM_STATES",
    "MAX_LANE_WINDOW_ROWS",
    "MAX_OBSERVED_DAY_ROWS",
    "MAX_REPORTED_WINDOWS",
    "RECONCILABLE_WORK_ITEM_STATES",
    "RECONCILIATION_MARKER_KEY",
    "CoverageCategory",
    "CoverageVerdict",
    "LaneReconciliation",
    "ReconciliationError",
    "ReconciliationScanTooLargeError",
    "WindowCoverage",
    "build_coverage_category",
    "observed_layer_days",
    "reconcile_lane",
    "reconciliation_marker",
    "window_coverage",
    "window_days",
]
