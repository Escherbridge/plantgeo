"""Choose a turn's days (D3): the forward window behind the S19 probe gate, the gap-fill holes, the dirty days.

Pure functions over the census, the lane's day facts and a probe result. Forward and gap-fill
differ only in the window they return. See `pipeline/runner/AGENTS.md` "Windows".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.paths import MAX_GAP_WINDOW_DAYS
from agri_data_service.pipeline.runner.contract import ProbeWindow

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig, LaneDays
    from agri_data_service.pipeline.runner.contract import EdgeSource, ProbeStatus, ProviderEdge

#: S19: a probe spends at most two locations over at most this many days.
PROBE_WINDOW_MAX_DAYS: Final = 14
#: A gap-fill turn selects at most this many holes; the rest wait for the next fire.
GAP_FILL_MAX_DAYS_PER_TURN: Final = 366
#: The detail on an owed day the probe showed without values: the ordinary publication lag, not a fault.
NEWER_THAN_PROBED_EDGE: Final = "newer_than_probed_edge"
#: The detail on an owed day OLDER than the probed edge that the probe still shows empty: a hole behind
#: the edge, so it counts toward the alarm (S5), yet it is never fanned out on a probe null (S19).
PROBE_NULL_BEHIND_EDGE: Final = "probe_null_behind_edge"
#: The detail on an owed day newer than the newest published day while the probe contradicts the census.
PROBE_INVALID: Final = "probe_invalid"
_GATE_DETAIL_BY_STATUS: Final[Mapping[str, str]] = {
    "blind": "probe_blind",
    "unavailable": "probe_unavailable",
    "deferred": "probe_deferred",
}


class GapFillDisabledError(Exception):
    """S12: a gap-fill turn of a lane whose `gap_fill_enabled` is false (exit 78)."""


@dataclass(frozen=True, slots=True)
class DayRange:
    """An inclusive run of UTC days."""

    first: date
    last: date

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.first <= day <= self.last

    def days(self) -> tuple[date, ...]:
        """Every day, ascending."""
        return tuple(self.first + timedelta(days=offset) for offset in range((self.last - self.first).days + 1))


@dataclass(frozen=True, slots=True)
class GatedDay:
    """An owed day the turn held back, and the word it is reported under."""

    day: date
    detail: str

    @property
    def behind_edge(self) -> bool:
        """Only a day the probe showed unpublished is ahead of the edge; every other hold counts (S5)."""
        return self.detail != NEWER_THAN_PROBED_EDGE


@dataclass(frozen=True, slots=True)
class ForwardPlan:
    """The forward turn's days: what fans out, what the gate held, and the edge it judged by."""

    fan_out: tuple[date, ...]
    gated: tuple[GatedDay, ...]
    #: Absence-recheck days the probe showed still empty: nothing to write, nothing owed.
    unchanged_absences: tuple[date, ...]
    edge: date
    edge_source: EdgeSource
    probe_status: ProbeStatus | None


@dataclass(frozen=True, slots=True)
class GapFillPlan:
    """The gap-fill turn's days, oldest first, and the holes no source can fill."""

    fill: tuple[date, ...]
    retention_exceeded: tuple[date, ...]
    #: Holes past this turn's day cap; they wait for the next fire.
    overflow: tuple[date, ...]


def candidate_edge(days: LaneDays, *, today: date) -> date:
    """`today - lag` (S6's fallback); a provisional (`write_and_recheck`) lane never reaches past yesterday UTC (O1)."""
    edge = today - timedelta(days=days.publication_lag_days)
    if days.partial_day == "write_and_recheck":
        edge = min(edge, today - timedelta(days=1))
    return edge


def forward_window(days: LaneDays, *, today: date) -> DayRange | None:
    """`[edge - recheck + 1, edge]`, floored at the lane's history floor; `None` before the floor settles."""
    last = candidate_edge(days, today=today)
    first = last - timedelta(days=days.absence_recheck_days - 1)
    if days.floor is not None:
        first = max(first, days.floor)
    return None if first > last else DayRange(first=first, last=last)


def _rotation(revision_window_days: int, absence_recheck_days: int, block_days: int) -> int:
    """The most `block_days` blocks the revision span behind the forward window can touch."""
    return -(-(revision_window_days - absence_recheck_days) // block_days) + 1


def revision_rotation_days(days: LaneDays) -> int | None:
    """How many forward turns a full revision rotation takes, or `None` for a lane with no revision window."""
    if days.revision_window_days is None:
        return None
    return _rotation(days.revision_window_days, days.absence_recheck_days, days.revision_days_per_turn)


def revision_block(days: LaneDays, window: DayRange, *, today: date) -> DayRange | None:
    """S11 rolling revision: today's block of `[edge - revision_window_days + 1, window.first - 1]`, or `None`.

    Blocks of `revision_days_per_turn` days are anchored to the day ordinal, so a block keeps its days as
    the window slides. Block `b` is today's when `b = today (mod rotation)`, so every block is re-asked
    exactly once every `revision_rotation_days` turns; a day whose residue matches no block revises nothing.
    """
    window_days = days.revision_window_days
    if window_days is None:
        return None
    rotation = _rotation(window_days, days.absence_recheck_days, days.revision_days_per_turn)
    last = window.first - timedelta(days=1)
    first = window.last - timedelta(days=window_days - 1)
    if days.floor is not None:
        first = max(first, days.floor)
    if first > last:
        return None
    size = days.revision_days_per_turn
    first_block = first.toordinal() // size
    block = first_block + (today.toordinal() - first_block) % rotation
    if block > last.toordinal() // size:
        return None
    return DayRange(
        first=max(first, date.fromordinal(block * size)), last=min(last, date.fromordinal(block * size + size - 1))
    )


def probe_window(window: DayRange) -> ProbeWindow:
    """The newest `PROBE_WINDOW_MAX_DAYS` days of the forward window."""
    first = max(window.first, window.last - timedelta(days=PROBE_WINDOW_MAX_DAYS - 1))
    return ProbeWindow(first=first, last=window.last)


def effective_probe_status(probe: ProviderEdge, published_days: Iterable[date]) -> ProbeStatus:
    """G0's derivation: a probe null on a day the census holds as data is `invalid`; nothing either way is `blind`."""
    if probe.status != "ok":
        return probe.status
    published_in_window = {day for day in published_days if probe.window.first <= day <= probe.window.last}
    if published_in_window - probe.valued_days:
        return "invalid"
    if not probe.valued_days and not published_in_window:
        return "blind"
    return "ok"


def _gate(  # noqa: PLR0911, PLR0913 - one return per gate word; the probe and census facts are distinct
    day: date,
    *,
    probe: ProviderEdge,
    status: ProbeStatus,
    is_recheck: bool,
    base_is_data: bool,
    newest_published: date | None,
) -> str | None:
    """The detail `day` is held under, or `None` to fan it out (G0's `_probe_gate`, generalised)."""
    if base_is_data:
        return None
    if status == "invalid":
        return PROBE_INVALID if newest_published is not None and day > newest_published else None
    if not probe.window.first <= day <= probe.window.last:
        return None
    if status == "ok":
        if day in probe.valued_days:
            return None
        if is_recheck:
            return "absence_unchanged"
        edge = probe.edge
        return NEWER_THAN_PROBED_EDGE if edge is None or day > edge else PROBE_NULL_BEHIND_EDGE
    return _GATE_DETAIL_BY_STATUS[status]


def plan_forward(  # noqa: PLR0913 - the census's four readings, the window and the probe are distinct
    *,
    owed: Sequence[date],
    rechecks: Sequence[date],
    window: DayRange,
    probe: ProviderEdge | None,
    published_days: Sequence[date],
    base_data_days: frozenset[date] = frozenset(),
) -> ForwardPlan:
    """S19: fan out only the owed days the probe shows valued; hold the rest, oldest first.

    With no probe (a lane without `probe_edge`), every owed and recheck day in the window fans out
    and the edge is the lag fallback. Days in the window older than the probe window are walked
    whatever the probe said (O-R3-1: "keep walking older owed days").
    """
    recheck_set = {day for day in rechecks if day in window}
    candidates = sorted({day for day in owed if day in window} | recheck_set)
    if probe is None:
        return ForwardPlan(
            fan_out=tuple(candidates),
            gated=(),
            unchanged_absences=(),
            edge=window.last,
            edge_source="lag_fallback",
            probe_status=None,
        )
    status = effective_probe_status(probe, published_days)
    newest_published = max(published_days, default=None)
    fan_out: list[date] = []
    gated: list[GatedDay] = []
    unchanged: list[date] = []
    for day in candidates:
        detail = _gate(
            day,
            probe=probe,
            status=status,
            is_recheck=day in recheck_set,
            base_is_data=day in base_data_days,
            newest_published=newest_published,
        )
        if detail is None:
            fan_out.append(day)
        elif day in recheck_set:
            unchanged.append(day)
        else:
            gated.append(GatedDay(day=day, detail=detail))
    probed_edge = probe.edge if status == "ok" else None
    return ForwardPlan(
        fan_out=tuple(fan_out),
        gated=tuple(gated),
        unchanged_absences=tuple(unchanged),
        edge=probed_edge if probed_edge is not None else window.last,
        edge_source="probe" if probed_edge is not None else "lag_fallback",
        probe_status=status,
    )


def gap_fill_window(lane: LaneConfig, *, today: date) -> DayRange | None:
    """Every day older than the forward window, back to the lane's floor (or its source's earliest day).

    Capped at the census's own `MAX_GAP_WINDOW_DAYS` span, newest end kept: an older remainder waits
    until the newer holes are filled and the floor is raised, never a raise from the census.
    """
    if lane.days is None:
        return None
    forward = forward_window(lane.days, today=today)
    last = (forward.first if forward is not None else candidate_edge(lane.days, today=today)) - timedelta(days=1)
    earliest = lane.source.history.earliest if lane.source is not None else None
    first = lane.days.floor or earliest
    if first is None or first > last:
        return None
    first = max(first, last - timedelta(days=MAX_GAP_WINDOW_DAYS - 1))
    return DayRange(first=first, last=last)


def ensure_gap_fill_enabled(lane: LaneConfig) -> None:
    """S12: refuse a gap-fill turn of a lane whose `gap_fill_enabled` is false, before any census or send."""
    if not lane.schedule.gap_fill_enabled:
        raise GapFillDisabledError(
            f"lane {lane.id!r} has gap_fill_enabled = false; enabling it is an explicit TOML flip inside a "
            "named owner gate (S12)"
        )


def plan_gap_fill(
    lane: LaneConfig,
    *,
    owed: Sequence[date],
    max_days: int = GAP_FILL_MAX_DAYS_PER_TURN,
) -> GapFillPlan:
    """S12 + D3: the census holes, oldest first, capped by history capability, retention and a per-turn day cap."""
    ensure_gap_fill_enabled(lane)
    history = lane.source.history if lane.source is not None else None
    reachable: list[date] = []
    retention: list[date] = []
    for day in sorted(owed):
        if history is None or history.capability == "none" or (history.earliest is not None and day < history.earliest):
            retention.append(day)
        else:
            reachable.append(day)
    return GapFillPlan(
        fill=tuple(reachable[:max_days]),
        retention_exceeded=tuple(retention),
        overflow=tuple(reachable[max_days:]),
    )


def transform_dirty_days(
    days: Sequence[date],
    *,
    input_digests: Mapping[date, Mapping[str, str]],
    receipt_digests: Mapping[date, Mapping[str, str]],
) -> tuple[date, ...]:
    """D4 + S11: days where some input has published and the inputs' digests differ from the transform receipt."""
    return tuple(
        day
        for day in sorted(days)
        if input_digests.get(day) and dict(input_digests[day]) != dict(receipt_digests.get(day, {}))
    )


__all__ = [
    "GAP_FILL_MAX_DAYS_PER_TURN",
    "NEWER_THAN_PROBED_EDGE",
    "PROBE_INVALID",
    "PROBE_NULL_BEHIND_EDGE",
    "PROBE_WINDOW_MAX_DAYS",
    "DayRange",
    "ForwardPlan",
    "GapFillDisabledError",
    "GapFillPlan",
    "GatedDay",
    "candidate_edge",
    "effective_probe_status",
    "ensure_gap_fill_enabled",
    "forward_window",
    "gap_fill_window",
    "plan_forward",
    "plan_gap_fill",
    "probe_window",
    "revision_block",
    "revision_rotation_days",
    "transform_dirty_days",
]
