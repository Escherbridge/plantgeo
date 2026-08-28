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

The module also runs that measurement BACKWARDS, as `plan_lane_gaps` (`jobs-plan-gaps`). Settling was only
ever half a loop: `validate-streams` could see that 2024-03-11 is missing and `reconcile_lane` could only
ever REMOVE work, so a hole discovered inside an already-succeeded run had no verb that could reopen it.
Planning gaps maps each missing day onto the lane's OWN floor-anchored grid -- never a second grid -- and
opens the shard that owns it, reopening one that already succeeded over nothing. Both directions read the
same day census and the same cadence-aware gap rule, so they cannot disagree about what "missing" means.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.archive_walk import (
    ARCHIVE_WALK_WORK_ITEM_KIND,
    PAYLOAD_WALK_GENERATION,
    ArchiveWalkPayloadError,
    ArchiveWindowRequest,
    archive_lane_definition_name,
    archive_lane_definition_spec,
    archive_lane_run_key,
    archive_source,
    archive_window_payload,
    archive_window_shard_key,
    walk_generation,
)
from agri_data_service.ingest.lanes import lane_windows
from agri_data_service.ingest.validation.completeness import missing_publication_days
from agri_data_service.ingest.validation.constants import DAILY_PUBLICATION_CADENCE_DAYS
from agri_data_service.ingest.validation.models import lane_publication_cadence_days
from agri_data_service.ingest.validation.queries import OBSERVED_DAYS_FOR_LAYER
from agri_data_service.ingest.writer import resolve_layer_id
from agri_data_service.jobs import JobWorkItemSpec, ensure_job_definition, open_job_run
from agri_data_service.jobs.lease import apply_statement_timeout, canonical_json, fetch_rows, required_column
from agri_data_service.jobs.worker import refresh_job_run_rollup

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.lanes import BackfillLane, LaneWindow
    from agri_data_service.ingest.source import HistoryWindow

CoverageVerdict = Literal["covered", "partial", "absent"]

# What gap planning would do to one window of the lane's grid. Only the first two write anything; the rest
# are named outcomes rather than a silent skip, because "this gap day sits under a dead letter" and "this
# gap day sits under a window nobody has claimed" are different operator problems.
GapWindowAction = Literal[
    "open",
    "reopen",
    "reopen_exhausted",
    "already_queued",
    "held",
    "dead_lettered",
    "cancelled",
]

GAP_WINDOW_ACTIONS: Final[tuple[GapWindowAction, ...]] = (
    "open",
    "reopen",
    "reopen_exhausted",
    "already_queued",
    "held",
    "dead_lettered",
    "cancelled",
)

# How many times gap planning may reopen ONE window before it stops asking and starts reporting.
#
# Reopening assumes a missing day is a HOLE -- the walk succeeded over nothing and a re-walk will land the
# rows. For a lane whose upstream genuinely published nothing that day, that assumption is false and the
# loop never terminates: the window is reopened, re-walked, writes nothing because there is nothing to
# write, succeeds again, and is reopened by the next tick. Measured on prod 2026-08-14, firms-archive
# offers 454 such windows over 1,069 "missing" days whose month histogram is 68% December-February and
# 1.7% July-September -- the inverse of fire season, which is what a PNW bbox with no fires in it looks
# like, not what a fetch defect looks like. `ingest/archive_walk.py` already states the same rule from the
# walk's side: "a winter day genuinely holds no detections; failing those would turn most of the archive
# red".
#
# So a window that has been reopened this many times is reclassified `reopen_exhausted` rather than
# reopened again. That is not the gap being forgiven -- the window keeps every missing day it carries and
# is reported under its own action, which is what surfaces it as an operator data point instead of as
# silent hourly churn. The counter is the walk generation the reopen statement already bumps
# (`PAYLOAD_WALK_GENERATION`), so this needs no new column and no migration.
MAX_GAP_REOPEN_GENERATIONS: Final = 5

# The report and this module now execute ONE statement, `sql/ingest/observed_days.sql`, formatted for one
# layer and one day range instead of every layer and every day; `OBSERVED_DAYS_FOR_LAYER` is imported rather
# than re-loaded, and both scope slots are filled there, at import, from closed constants. A window is
# "landed" here only if the completeness report can also see it, and that is no longer a promise two files
# make to each other -- it is the same SQL. The filters that carry it are `status = 'published'` and
# `geometry_id IS NOT NULL`: readObservationWindows requires both, so a row missing either is drawn on the
# map but invisible to the time axis, and counting it as coverage would mark a window succeeded whose days
# the slider still cannot reach.
PUBLISHED_FEATURE_STATUS: Final = "published"

# The error direction is not symmetric and this filter is chosen for that reason. Under-counting coverage
# costs one re-walk of a window that had in fact landed -- minutes, and the writer rejects the unchanged
# payload anyway. Over-counting marks a window succeeded that never landed, which is the exact failure this
# package exists to make impossible. When in doubt, do not mark.
_COVERAGE_BIAS_NOTE: Final = "a window is marked landed only when every one of its days is already served"

# One row per observed day of one layer, for the ONE execution of `observed_days.sql` a census now costs.
# FIRMS' floor is 2000-11-01, so the widest lane owes about 9,400 days; 50,000 is five times that and still
# trivial to hold. Hitting it is REFUSED rather than truncated -- a truncated day set invents absent days,
# and an absent day at the tail is what turns a covered window into a partial one and leaves it queued
# forever.
MAX_OBSERVED_DAY_ROWS: Final = 50_000

# WHY THE CENSUS IS ONE STATEMENT AGAIN, AND WHY IT WAS BRIEFLY THIRTEEN. Between 2026-08-15 and 2026-08-16
# this function cut the lane's span into two-year shards and unioned the answers, because an unsharded
# census of the firms layer measured 81 to 101 seconds against the 120s transaction statement timeout
# `apply_statement_timeout` pins. That diagnosis was wrong in its cause: the slowness was a MISSING INDEX,
# not a missing partition. `geo.feature_observation_day` is IMMUTABLE and PARALLEL SAFE, so it can carry an
# expression index, and `ix_features_layer_observation_day` over
# `(layer_id, geo.feature_observation_day(properties))` now exists in production and is confirmed used by
# the planner as an Index Cond.
#
# Measured against production 2026-08-16 with that index in place: ONE statement runs in 68.7s, thirteen
# shards run in 95.3s, and `shared_blks_read` is a wash (331,152 against 332,076). Sharding never reduced
# the work -- it added roughly 27 seconds of pure per-statement overhead on top of it. 68.7s is 57% of the
# 120s timeout, which leaves 43% headroom, so the fan-out is removed and the census is one bounded,
# index-served statement.
#
# The day BOUNDS survive that revert and are the point of it: `{day_scope}` in `observed_days.sql` and
# `_DAY_RANGE_SCOPE` in `validation/queries.py` are exactly what the new index makes fast, and the map's
# date slider still wants a bounded range. What was removed is the SHARDED CENSUS, never bounded querying.

# The bounds a census carries when its caller named none. Real dates rather than NULLs on purpose: a
# nullable bound would force `observed_days.sql` into an "either the bound is null or the day matches it"
# predicate and a generic plan, which is the same degradation the layer scope slot exists to avoid. Python's
# whole calendar sits well inside PostgreSQL's `date` range, so binding these excludes nothing.
UNBOUNDED_FIRST_OBSERVED_DAY: Final = date.min
UNBOUNDED_LAST_OBSERVED_DAY: Final = date.max

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

# The same idea running the other way: what a REOPENED window carries, so the row itself says why it went
# back to `queued` and which days it was reopened over. Queryable as `payload -> 'reopened_from_observed_gaps'`.
GAP_PLAN_MARKER_KEY: Final = "reopened_from_observed_gaps"

# The one work-item state gap planning may convert. `succeeded` and nothing else: `queued`/`retry_wait`/
# `deferred` are already open, `leased`/`running` are held by a live fence, `dead_letter` is evidence, and
# `cancelled` is an operator's decision. All four are reported instead. The gate is repeated inside
# `sql/ingest/reopen_gap_windows.sql`, because a cron tick can claim a window between the read and the write.
REOPENABLE_WORK_ITEM_STATE: Final = "succeeded"

CANCELLED_WORK_ITEM_STATE: Final = "cancelled"

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


def window_coverage(  # noqa: PLR0913 - the shard's four identifying values plus two keyword-only bounds
    shard_key: str,
    status: str,
    window: HistoryWindow,
    observed: frozenset[date],
    *,
    publication_cadence_days: int = DAILY_PUBLICATION_CADENCE_DAYS,
    max_reported_missing_days: int = MAX_REPORTED_WINDOWS,
) -> WindowCoverage:
    """Measure one window against the observed days, refusing to call anything but full coverage landed.

    `partial` is NOT landed, and that is the load-bearing decision in this module. A window missing some of
    its days is missing exactly the days the walk still owes, and nothing visible from here distinguishes a
    day upstream genuinely published nothing for from a day the fetch never reached. Settling it would
    write the same silent hole the bash driver wrote, from the other direction -- and unlike the bash there
    would be no failure file to contradict it. So a partial window stays queued and the walk re-fetches it;
    the days that already landed cost nothing to re-walk, because the writer's diff rejects them.

    The day comparison itself is NOT implemented here. It is `validation.completeness.missing_publication_days`,
    the same cadence-aware walk `validate-streams` reports gaps with, so the report and the settler cannot
    drift into two different meanings of "missing". A second, cadence-blind implementation lived here until
    2026-08-10 and was a strictly weaker special case of that one: correct only while every lane's stream
    published daily, and silently wrong -- in the settling direction -- for the first weekly or five-day
    lane anyone registered. Both lanes registered today declare a daily cadence, so this substitution
    changes no current behaviour; it removes the trap rather than a bug. See ingest/AGENTS.md, "One gap rule".
    """
    days = window_days(window)
    missing = missing_publication_days(
        (day for day in days if day in observed),
        expected_first_day=days[0],
        through_day=days[-1],
        publication_cadence_days=publication_cadence_days,
    )
    observed_count = sum(1 for day in days if day in observed)
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

_LANE_RUN_WINDOWS: Final = text(load_query_sql("ingest/lane_run_windows.sql"))

_MARK_WINDOWS_RECONCILED: Final = text(load_query_sql("ingest/mark_windows_reconciled.sql"))

_REOPEN_GAP_WINDOWS: Final = text(load_query_sql("ingest/reopen_gap_windows.sql"))


# ---------------------------------------------------------------------------------------------------------------
# The async entry points
# ---------------------------------------------------------------------------------------------------------------


async def observed_layer_days(
    session: AsyncSession,
    layer_reference: str,
    *,
    first_day: date | None = None,
    through_day: date | None = None,
) -> frozenset[date]:
    """Every UTC day the lane's target layer already serves, read through the same rule the report applies.

    ONE execution of `observed_days.sql`, always. Naming both bounds narrows the day scope to the caller's
    own span, which is what `ix_features_layer_observation_day` turns into an Index Cond; naming neither
    binds the widest dates the calendar has, so there is one statement shape and one plan either way and no
    caller ever forces the nullable-bound generic plan the .sql header warns about. Both verbs in this
    module name a span, and the set returned is a superset of every day either consults -- both only ever
    ask about days inside the lane's own floor-to-now range.

    **THE ROW-CAP REFUSAL IS THE POINT OF READING IT HERE.** `observed_days.sql` orders by day and takes the
    earliest rows, so a truncated result drops its TAIL, and every dropped day comes back from this function
    as a day the layer does not serve. `plan_lane_gaps` would then reopen the window that owns it and the
    walk would re-fetch a window that was never missing, forever. A truncated census is not a smaller
    census, it is a wrong one, so it is refused rather than returned.
    """
    layer_id = await resolve_layer_id(session, layer_reference)
    from_day = UNBOUNDED_FIRST_OBSERVED_DAY if first_day is None else first_day
    to_day = UNBOUNDED_LAST_OBSERVED_DAY if through_day is None else through_day
    if to_day < from_day:
        raise ReconciliationError(f"day span {from_day.isoformat()}..{to_day.isoformat()} ends before it begins")
    rows = await fetch_rows(
        session,
        OBSERVED_DAYS_FOR_LAYER,
        {
            "layer_id": layer_id,
            "published_status": PUBLISHED_FEATURE_STATUS,
            "from_day": from_day,
            "to_day": to_day,
            "row_limit": MAX_OBSERVED_DAY_ROWS,
        },
    )
    if len(rows) >= MAX_OBSERVED_DAY_ROWS:
        raise ReconciliationScanTooLargeError(
            f"layer {layer_reference!r} reports at least {MAX_OBSERVED_DAY_ROWS} observed days between "
            f"{from_day.isoformat()} and {to_day.isoformat()}; raise MAX_OBSERVED_DAY_ROWS or narrow the "
            "day range rather than reconciling against a truncated day set"
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

    The day census is read over the lane's OWN span -- its declared floor through today -- rather than over
    the layer's whole life, in one bounded statement (see `observed_layer_days`). No verdict can move as a
    result: every window on the lane's grid is floor-anchored and no window reaches past now, so every day
    `window_coverage` consults is inside that span by construction. What the narrowing does change is the
    three `observed_*` fields of the report, which now describe the days this LANE's span holds rather than
    every day the layer holds -- which is the number a lane report was always trying to say.
    """
    await apply_statement_timeout(session)
    resolved_layer = layer_reference if layer_reference is not None else archive_source(lane).layer_reference()
    observed = await observed_layer_days(
        session,
        resolved_layer,
        first_day=lane.floor.date(),
        through_day=datetime.now(UTC).date(),
    )
    cadence = lane_publication_cadence_days(archive_lane_definition_name(lane))
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
            publication_cadence_days=cadence,
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


# ---------------------------------------------------------------------------------------------------------------
# Gap planning: the same measurement, run backwards. Everything above turns "the data is there" into
# `succeeded`; everything below turns "the data is NOT there" into a claimable window. Pure logic first,
# for the same reason: the SQL only lists days and windows.
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GapWindow:
    """One window of the lane's own grid that a missing day landed in, and what planning would do to it."""

    shard_key: str
    lane_window: LaneWindow
    action: GapWindowAction
    existing_status: str | None
    missing_days: tuple[date, ...]
    omitted_missing_days: int

    @property
    def first_day(self) -> date:
        """The window's inclusive first UTC day."""
        return window_days(self.lane_window.window)[0]

    @property
    def last_day(self) -> date:
        """The window's inclusive last UTC day; a window ending at midnight does not own that day."""
        return window_days(self.lane_window.window)[-1]

    def to_summary(self) -> dict[str, object]:
        """Render one gap window: which shard, what state it is in, and which of its days are missing."""
        summary: dict[str, object] = {
            "shard_key": self.shard_key,
            "grid_index": self.lane_window.grid_index,
            "first_day": self.first_day.isoformat(),
            "last_day": self.last_day.isoformat(),
            "action": self.action,
            "existing_status": self.existing_status,
            "missing_days": [day.isoformat() for day in self.missing_days],
        }
        if self.omitted_missing_days:
            summary["omitted_missing_days"] = self.omitted_missing_days
        return summary


@dataclass(frozen=True, slots=True)
class LaneGapPlan:
    """What one lane's layer is missing, which windows of its grid own those days, and what was opened."""

    lane: str
    definition: str
    run_key: str
    job_run_id: uuid.UUID | None
    layer_reference: str
    applied: bool
    publication_cadence_days: int
    floor_day: date
    through_day: date | None
    observed_day_count: int
    missing_days: tuple[date, ...]
    windows: tuple[GapWindow, ...]
    unplannable_days: tuple[date, ...]
    opened_count: int
    reopened_count: int

    def windows_for(self, action: GapWindowAction) -> tuple[GapWindow, ...]:
        """Every gap window this plan would take one particular action on, oldest first."""
        return tuple(window for window in self.windows if window.action == action)

    @property
    def would_open(self) -> int:
        """Shards this run would create; equal to `opened_count` once `--apply` has run and nothing raced."""
        return len(self.windows_for("open"))

    @property
    def would_reopen(self) -> int:
        """Shards this run would move back to `queued`; equal to `reopened_count` after a clean `--apply`."""
        return len(self.windows_for("reopen"))

    def to_summary(self, *, max_reported_windows: int = MAX_REPORTED_WINDOWS) -> dict[str, object]:
        """Render the whole plan as the one JSON line a cron log and an operator both read."""
        summary: dict[str, object] = {
            "lane": self.lane,
            "definition": self.definition,
            "run_key": self.run_key,
            "job_run_id": None if self.job_run_id is None else str(self.job_run_id),
            "layer": self.layer_reference,
            "applied": self.applied,
            "state": "applied" if self.applied else "dry_run",
            "publication_cadence_days": self.publication_cadence_days,
            "floor_day": self.floor_day.isoformat(),
            "through_day": None if self.through_day is None else self.through_day.isoformat(),
            "observed_day_count": self.observed_day_count,
            "missing_day_count": len(self.missing_days),
            # The calendar span is reported even when the sample is truncated: "this would plan 2012-01
            # through 2014-08" is the single fact a human most needs before spending `--apply`.
            "first_missing_day": self.missing_days[0].isoformat() if self.missing_days else None,
            "last_missing_day": self.missing_days[-1].isoformat() if self.missing_days else None,
            "missing_day_sample": [day.isoformat() for day in self.missing_days[:max_reported_windows]],
            "omitted_missing_days": max(0, len(self.missing_days) - max_reported_windows),
            "unplannable_day_count": len(self.unplannable_days),
            "unplannable_day_sample": [day.isoformat() for day in self.unplannable_days[:max_reported_windows]],
            "would_open": self.would_open,
            "would_reopen": self.would_reopen,
            "opened": self.opened_count,
            "reopened": self.reopened_count,
            "reopen_rule": _REOPEN_BIAS_NOTE,
        }
        for action in GAP_WINDOW_ACTIONS:
            windows = self.windows_for(action)
            summary[action] = {
                "window_count": len(windows),
                "first_day": windows[0].first_day.isoformat() if windows else None,
                "last_day": max(window.last_day for window in windows).isoformat() if windows else None,
                "windows": [window.to_summary() for window in windows[:max_reported_windows]],
                "omitted_window_count": max(0, len(windows) - max_reported_windows),
            }
        return summary


# The error direction is not symmetric here either, and it points the opposite way to reconciliation's.
# Over-planning costs one re-walk of a window that had in fact landed -- minutes, and the writer rejects the
# unchanged payload anyway. Under-planning leaves the hole exactly where `validate-streams` found it, which
# is the state this verb exists to end. When in doubt, plan.
_REOPEN_BIAS_NOTE: Final = (
    "only a succeeded window is reopened; leased, dead-lettered and cancelled windows are reported, never converted"
)


def map_days_to_grid(
    lane: BackfillLane,
    days: Sequence[date],
    grid: Sequence[LaneWindow],
) -> tuple[tuple[tuple[LaneWindow, tuple[date, ...]], ...], tuple[date, ...]]:
    """Place each missing day on the lane's own floor-anchored grid, naming the days no whole window owns."""
    # THE GRID IS THE PLANNER'S, NOT A SECOND ONE. `lane_windows` builds it; this only inverts the arithmetic
    # that built it (`floor + grid_index * window_days`) to find which window a day fell in, and then CHECKS
    # the answer by asking that window for its own days. A day that lands outside the window the arithmetic
    # chose means the two formulas have drifted, and failing that check loudly beats mis-keying a shard.
    #
    # A day with no whole window is normal rather than exceptional: everything above the newest whole window
    # belongs to the forward hourly cron (see `lane_windows` on why no trailing partial is ever planned), and
    # everything below the floor is outside what this lane declared it would fill. Both are REPORTED as
    # unplannable rather than dropped, because "we found a gap and can do nothing about it" is the one answer
    # an operator must not have to infer from silence.
    by_index = {window.grid_index: window for window in grid}
    owned: dict[int, list[date]] = {}
    unplannable: list[date] = []
    floor_day = lane.floor.date()
    for day in days:
        grid_index = (day - floor_day).days // lane.window_days
        candidate = by_index.get(grid_index) if grid_index >= 0 else None
        if candidate is None or day not in window_days(candidate.window):
            unplannable.append(day)
            continue
        owned.setdefault(grid_index, []).append(day)
    mapped = tuple((by_index[grid_index], tuple(sorted(found))) for grid_index, found in sorted(owned.items()))
    return mapped, tuple(unplannable)


def _row_payload(row: Mapping[str, object]) -> Mapping[str, object] | None:
    """Read one window row's JSONB payload, tolerating a driver that hands back something else.

    `walk_generation` already treats an absent or non-integer counter as zero; this only has to guarantee
    it is handed a mapping or None, so a payload stored as a JSON scalar cannot raise here. `dict` rather
    than `Mapping` because a JSONB column deserializes to exactly that, and testing against the builtin
    keeps `collections.abc` in the type-checking block where the rest of this module's imports live.
    """
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else None


def gap_window_action(existing_status: str | None, *, reopen_generation: int = 0) -> GapWindowAction:
    """Decide what planning may do to the shard a missing day landed on, from the state that shard is in.

    `reopen_generation` is the shard's walk generation -- how many times gap planning has already reopened
    it. Past `MAX_GAP_REOPEN_GENERATIONS` a still-missing day is evidence that the upstream has nothing to
    give rather than evidence of a hole, so the window is reported instead of reopened a sixth time.
    """
    if existing_status is None:
        return "open"
    if existing_status == REOPENABLE_WORK_ITEM_STATE:
        return "reopen_exhausted" if reopen_generation >= MAX_GAP_REOPEN_GENERATIONS else "reopen"
    if existing_status in HELD_WORK_ITEM_STATES:
        return "held"
    if existing_status == DEAD_LETTER_WORK_ITEM_STATE:
        return "dead_lettered"
    if existing_status == CANCELLED_WORK_ITEM_STATE:
        return "cancelled"
    # `queued`, `retry_wait`, `deferred`. Already claimable; planning them again would only reset progress.
    return "already_queued"


def gap_reopen_marker(
    window: GapWindow,
    *,
    layer_reference: str,
    publication_cadence_days: int,
) -> dict[str, object]:
    """The record a reopened window carries on its own payload, stating what was measured rather than asserted."""
    return {
        "layer": layer_reference,
        "publication_cadence_days": publication_cadence_days,
        "first_day": window.first_day.isoformat(),
        "last_day": window.last_day.isoformat(),
        "missing_days": [day.isoformat() for day in window.missing_days],
        "omitted_missing_days": window.omitted_missing_days,
        "previous_status": window.existing_status,
        "tool": "jobs-plan-gaps",
    }


async def plan_lane_gaps(  # noqa: PLR0913 - the lane plus five keyword-only knobs, as `reconcile_lane` has
    session: AsyncSession,
    lane: BackfillLane,
    *,
    apply_changes: bool = False,
    end: datetime | None = None,
    layer_reference: str | None = None,
    max_reported_missing_days: int = MAX_REPORTED_WINDOWS,
) -> LaneGapPlan:
    """Turn the days a lane's layer is missing into claimable windows of that lane's own grid. The caller commits.

    Read-only unless `apply_changes` is set, exactly like `reconcile_lane`, so a dry run that is never
    committed writes nothing even if a future caller forgets which mode it asked for.
    """
    await apply_statement_timeout(session)
    resolved_layer = layer_reference if layer_reference is not None else archive_source(lane).layer_reference()
    cadence = lane_publication_cadence_days(archive_lane_definition_name(lane))
    run_key = archive_lane_run_key(lane)
    floor_day = lane.floor.date()

    # The grid is built BEFORE the census, and only because the census wants the day bounds it names. It is
    # pure arithmetic over the lane and the clock, it issues no statement, and moving it above the first
    # `execute` therefore changes neither what is read nor the order the statements arrive in.
    #
    # `grid` is newest-first, so its head names the last day any whole window owns. Asking for gaps past that
    # day would report the forward hourly cron's own territory as this lane's debt -- and asking the census
    # for those days would be measuring a span this verb is not allowed to act on.
    grid = lane_windows(lane, end if end is not None else datetime.now(UTC))
    grid_through_day = window_days(grid[0].window)[-1] if grid else None
    observed = await observed_layer_days(
        session,
        resolved_layer,
        first_day=floor_day,
        through_day=grid_through_day,
    )
    job_run_id, rows = await _read_lane_windows(session, run_key)
    existing = {required_column(row, "shard_key", str): required_column(row, "status", str) for row in rows}
    # How many times each shard has already been reopened, read off the same rows: the reopen statement
    # bumps `PAYLOAD_WALK_GENERATION` on every pass, so the payload already counts this for free.
    reopen_generations = {required_column(row, "shard_key", str): walk_generation(_row_payload(row)) for row in rows}

    if not grid or grid_through_day is None:
        # Nothing whole below the boundary. A lane planned today at a floor of today owes no window yet, and
        # reporting an empty plan is the honest answer rather than inventing a partial one.
        return _empty_gap_plan(lane, run_key, job_run_id, resolved_layer, cadence, floor_day, len(observed))

    through_day = grid_through_day
    missing = missing_publication_days(
        observed,
        expected_first_day=floor_day,
        through_day=through_day,
        publication_cadence_days=cadence,
    )
    mapped, unplannable = map_days_to_grid(lane, missing, grid)
    windows = tuple(
        GapWindow(
            shard_key=(shard_key := archive_window_shard_key(lane, lane_window)),
            lane_window=lane_window,
            action=gap_window_action(
                existing.get(shard_key),
                reopen_generation=reopen_generations.get(shard_key, 0),
            ),
            existing_status=existing.get(shard_key),
            missing_days=found[:max_reported_missing_days],
            omitted_missing_days=max(0, len(found) - max_reported_missing_days),
        )
        for lane_window, found in mapped
    )

    opened_count = 0
    reopened_count = 0
    if apply_changes:
        job_run_id, opened_count, reopened_count = await _apply_gap_plan(
            session,
            lane,
            windows,
            job_run_id=job_run_id,
            layer_reference=resolved_layer,
            publication_cadence_days=cadence,
        )
    return LaneGapPlan(
        lane=lane.name,
        definition=archive_lane_definition_name(lane),
        run_key=run_key,
        job_run_id=job_run_id,
        layer_reference=resolved_layer,
        applied=apply_changes,
        publication_cadence_days=cadence,
        floor_day=floor_day,
        through_day=through_day,
        observed_day_count=len(observed),
        missing_days=missing,
        windows=windows,
        unplannable_days=unplannable,
        opened_count=opened_count,
        reopened_count=reopened_count,
    )


def _empty_gap_plan(  # noqa: PLR0913 - one parameter per field the empty plan still has to report honestly
    lane: BackfillLane,
    run_key: str,
    job_run_id: uuid.UUID | None,
    layer_reference: str,
    publication_cadence_days: int,
    floor_day: date,
    observed_day_count: int,
) -> LaneGapPlan:
    """The plan for a lane whose grid holds no whole window yet: nothing owed, nothing measured, nothing done."""
    return LaneGapPlan(
        lane=lane.name,
        definition=archive_lane_definition_name(lane),
        run_key=run_key,
        job_run_id=job_run_id,
        layer_reference=layer_reference,
        applied=False,
        publication_cadence_days=publication_cadence_days,
        floor_day=floor_day,
        through_day=None,
        observed_day_count=observed_day_count,
        missing_days=(),
        windows=(),
        unplannable_days=(),
        opened_count=0,
        reopened_count=0,
    )


async def _apply_gap_plan(  # noqa: PLR0913 - the session, lane and windows plus three keyword-only values
    session: AsyncSession,
    lane: BackfillLane,
    windows: Sequence[GapWindow],
    *,
    job_run_id: uuid.UUID | None,
    layer_reference: str,
    publication_cadence_days: int,
) -> tuple[uuid.UUID | None, int, int]:
    """Open the shards that do not exist, reopen the ones that succeeded over nothing, then recompute the run."""
    opened_count = 0
    to_open = [window for window in windows if window.action == "open"]
    if to_open:
        definition = await ensure_job_definition(session, archive_lane_definition_spec(lane))
        opened = await open_job_run(
            session,
            definition,
            logical_run_key=archive_lane_run_key(lane),
            work_items=tuple(
                JobWorkItemSpec(
                    shard_key=window.shard_key,
                    kind=ARCHIVE_WALK_WORK_ITEM_KIND,
                    payload=archive_window_payload(lane, window.lane_window),
                    # The window's index on the lane's fixed grid, exactly as `archive_lane_work_items`
                    # assigns it, so a gap-planned shard takes its turn in the same newest-first order and
                    # never jumps the queue by virtue of having been planned late.
                    priority=window.lane_window.grid_index,
                )
                for window in to_open
            ),
            requested_by="agri-service ops jobs-plan-gaps",
            target_partitions={"lane": lane.name, "floor": lane.floor_day},
        )
        job_run_id = opened.job_run_id
        opened_count = opened.added_work_items

    reopened_count = 0
    to_reopen = [window for window in windows if window.action == "reopen"]
    if to_reopen and job_run_id is not None:
        reopened_count = await _reopen_gap_windows(
            session,
            job_run_id=job_run_id,
            lane=lane,
            windows=to_reopen,
            layer_reference=layer_reference,
            publication_cadence_days=publication_cadence_days,
        )
    if job_run_id is not None and reopened_count:
        # `open_job_run` already refreshed the rollup, but a reopen moves a shard OUT of `succeeded` after
        # that, so the run's counters and its own status have to be recomputed once more. Absolute recompute
        # rather than an incremental bump, for the reason `_mark_windows_reconciled` gives.
        await refresh_job_run_rollup(session, job_run_id)
    return job_run_id, opened_count, reopened_count


async def _reopen_gap_windows(  # noqa: PLR0913 - the session plus five keyword-only values the statement binds
    session: AsyncSession,
    *,
    job_run_id: uuid.UUID,
    lane: BackfillLane,
    windows: Sequence[GapWindow],
    layer_reference: str,
    publication_cadence_days: int,
) -> int:
    """Move every succeeded-but-empty window back to `queued` in one statement, with the measurement attached."""
    reopened = [
        {
            "shard_key": window.shard_key,
            # Rendered to TEXT here and cast back to jsonb inside the statement, the same double hop
            # `open_job_run` and `_mark_windows_reconciled` both put a payload through.
            "marker": canonical_json(
                gap_reopen_marker(
                    window,
                    layer_reference=layer_reference,
                    publication_cadence_days=publication_cadence_days,
                )
            ),
        }
        for window in windows
    ]
    rows = await fetch_rows(
        session,
        _REOPEN_GAP_WINDOWS,
        {
            "job_run_id": job_run_id,
            "marker_key": GAP_PLAN_MARKER_KEY,
            "generation_key": PAYLOAD_WALK_GENERATION,
            # The lane's OWN declared budget, read through the single producer of the definition spec rather
            # than re-spelled here, so a shard reopened over a real hole gets the same number of chances the
            # lane grants any other window rather than whatever its previous pass happened to leave.
            "attempt_budget": archive_lane_definition_spec(lane).max_attempts,
            # The cap, re-asserted inside the write for the same reason the status gate is: a window can
            # cross it between the read that classified it and this statement.
            "max_reopen_generations": MAX_GAP_REOPEN_GENERATIONS,
            "reopened": json.dumps(reopened, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        },
    )
    return len(rows)


__all__ = [
    "CANCELLED_WORK_ITEM_STATE",
    "DEAD_LETTER_WORK_ITEM_STATE",
    "GAP_PLAN_MARKER_KEY",
    "GAP_WINDOW_ACTIONS",
    "HELD_WORK_ITEM_STATES",
    "MAX_LANE_WINDOW_ROWS",
    "MAX_OBSERVED_DAY_ROWS",
    "MAX_REPORTED_WINDOWS",
    "RECONCILABLE_WORK_ITEM_STATES",
    "RECONCILIATION_MARKER_KEY",
    "REOPENABLE_WORK_ITEM_STATE",
    "CoverageCategory",
    "CoverageVerdict",
    "GapWindow",
    "GapWindowAction",
    "LaneGapPlan",
    "LaneReconciliation",
    "ReconciliationError",
    "ReconciliationScanTooLargeError",
    "WindowCoverage",
    "build_coverage_category",
    "gap_reopen_marker",
    "gap_window_action",
    "map_days_to_grid",
    "observed_layer_days",
    "plan_lane_gaps",
    "reconcile_lane",
    "reconciliation_marker",
    "window_coverage",
    "window_days",
]
