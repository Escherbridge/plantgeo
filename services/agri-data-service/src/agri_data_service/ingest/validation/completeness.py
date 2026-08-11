"""Pure logic. Everything in this module is testable with no database at all, which is where the report's
credibility lives: the SQL only counts rows, the rules that turn counts into a verdict are here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

from agri_data_service.ingest.validation.constants import (
    _ISO_DAY_IN_SHARD_KEY,
    DAILY_PUBLICATION_CADENCE_DAYS,
    MINIMUM_DENSITY_FLOOR,
    OBSERVATION_CLUSTER_GAP_DAYS,
    OBSERVATION_DENSITY_FLOOR_FRACTION,
)
from agri_data_service.ingest.validation.models import (
    ObservationGap,
    ObservationsBelowExpectedFloor,
    SliderWindow,
    ThinDay,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from agri_data_service.ingest.validation.constants import EarliestObservedDayRule, StreamVerdict
    from agri_data_service.ingest.validation.models import LaneState, ObservedDay, ValidityFinding

_ONE_DAY = timedelta(days=1)


def sort_observed_days(days: Iterable[ObservedDay]) -> tuple[ObservedDay, ...]:
    """Return the day series in ascending calendar order, which every rule below assumes."""
    return tuple(sorted(days, key=lambda observed: observed.day))


def split_days_at_expected_floor(
    days: Sequence[ObservedDay],
    expected_first_day: date | None,
) -> tuple[tuple[ObservedDay, ...], tuple[ObservedDay, ...]]:
    """Split the ordered day series into the days below the declared floor and the days inside the window."""
    # A DAY BELOW THE FLOOR IS NEITHER A GAP NOR COVERAGE, and that is why it gets its own return value rather
    # than being dropped or merged. It is not coverage: the stream never claimed to serve it, so counting it
    # would credit the stream for a day outside its declared window. It is not a gap either, and it does not
    # OPEN one: nobody owes data for the 30 years between two stragglers that sit below the floor.
    #
    # Measured on water-gauges 2026-08-08. 46 rows from decommissioned USGS gauges carry their last-ever
    # reading's own timestamp, 38 distinct days spanning 1990-09-30 to 2020-12-03 at 1-4 rows each, against 448
    # real days carrying 800+ rows each. Walking `pairwise` over the WHOLE series counted every inter-straggler
    # hole, and the head-gap branch could not compensate because it only fires when
    # `expected_first_day < first_observed` -- with floor 2022-08-05 against first_observed 1990-09-30 it is
    # skipped, so 30 years of holes stayed in the total. The report read 12,611 missing days while the stream's
    # own declared span is 1,465 days; clipped here it reads 1,017 of 1,465, and `missing_day_count` can no
    # longer exceed `expected_day_span`.
    #
    # The stragglers are still REAL OBSERVATIONS, so they are handed back for `days_below_expected_floor` to
    # report rather than discarded. The slider window is deliberately built from the UNCLIPPED series: the
    # 21-day continuity rule plus the density floor already drop them, and that half agrees with what the UI
    # draws.
    ordered = sort_observed_days(days)
    if expected_first_day is None:
        return (), ordered
    below = tuple(entry for entry in ordered if entry.day < expected_first_day)
    inside = tuple(entry for entry in ordered if entry.day >= expected_first_day)
    return below, inside


@dataclass(frozen=True, slots=True)
class _Silence:
    """One run of calendar days nothing was published on, plus the first cadence grid point inside it."""

    gap_from: date
    gap_to: date
    first_owed: date


def _require_cadence(publication_cadence_days: int) -> timedelta:
    """Turn a declared cadence into an interval, refusing a zero or negative rhythm rather than dividing by it."""
    if publication_cadence_days < 1:
        raise ValueError("publication_cadence_days must be at least one day")
    return timedelta(days=publication_cadence_days)


def _silences(
    observed: Sequence[date],
    *,
    expected_first_day: date | None,
    through_day: date | None,
    cadence: timedelta,
) -> tuple[_Silence, ...]:
    """THE gap walk, once: every silence that ran past one whole cadence period, with its first owed grid point."""
    # THE WALK IS ON THE STREAM'S OWN CADENCE, NOT ON A DAILY GRID. A daily grid is only correct for a daily
    # stream: against drought_areas, which USDM publishes once every Tuesday, it called the six ordinary days
    # between two consecutive releases missing -- so a perfectly complete weekly stream reported roughly 6/7 of
    # the calendar as absent, and vegetation (a five-day Sentinel-2 revisit) 4/5 of it. Those phantom days
    # drowned the real ones. A gap therefore opens only when the silence runs PAST one whole cadence period,
    # and what it counts is the grid points `last publication + n * cadence` that passed with nothing published.
    #
    # This function is the ONE implementation of that rule. `find_observation_gaps` renders it as the report's
    # calendar runs and `missing_publication_days` renders it as the individual owed days a backfill lane has
    # to re-plan; a second day-comparison anywhere else is how the two halves of the pipeline start disagreeing
    # about what "missing" means. See ingest/AGENTS.md, "One gap rule".
    #
    # Clipped here rather than by the caller so no caller can walk the unclipped series by omission.
    ordered = sorted(day for day in observed if expected_first_day is None or day >= expected_first_day)
    if not ordered:
        if expected_first_day is None or through_day is None or through_day < expected_first_day:
            return ()
        # Nothing inside the window at all: the declared floor was itself owed, and so was every cadence step
        # from it up to the reporting day.
        return (_Silence(expected_first_day, through_day, expected_first_day),)

    silences: list[_Silence] = []

    first_observed = ordered[0]
    if expected_first_day is not None and expected_first_day < first_observed:
        silences.append(_Silence(expected_first_day, first_observed - _ONE_DAY, expected_first_day))

    silences.extend(
        _Silence(earlier + _ONE_DAY, later - _ONE_DAY, earlier + cadence)
        for earlier, later in pairwise(ordered)
        if later - earlier > cadence
    )

    last_observed = ordered[-1]
    if through_day is not None and through_day - last_observed >= cadence:
        silences.append(_Silence(last_observed + _ONE_DAY, through_day, last_observed + cadence))

    return tuple(silences)


def publication_grid_points(first_owed: date, through_day: date, cadence: timedelta) -> tuple[date, ...]:
    """The cadence grid points from the first owed publication through the end of a silence, inclusive."""
    return tuple(first_owed + cadence * step for step in range((through_day - first_owed) // cadence + 1))


def find_observation_gaps(
    days: Sequence[ObservedDay],
    *,
    expected_first_day: date | None = None,
    through_day: date | None = None,
    publication_cadence_days: int = DAILY_PUBLICATION_CADENCE_DAYS,
) -> tuple[ObservationGap, ...]:
    """List every run of days the stream owed a publication and produced none, head and tail runs included."""
    cadence = _require_cadence(publication_cadence_days)
    return tuple(
        _gap_from_silence(silence, cadence)
        for silence in _silences(
            [entry.day for entry in days],
            expected_first_day=expected_first_day,
            through_day=through_day,
            cadence=cadence,
        )
    )


def missing_publication_days(
    observed: Iterable[date],
    *,
    expected_first_day: date,
    through_day: date,
    publication_cadence_days: int = DAILY_PUBLICATION_CADENCE_DAYS,
) -> tuple[date, ...]:
    """Every day inside the span that owed a publication and got none, from the same walk the report runs."""
    # The lane-facing rendering of `_silences`. A backfill planner needs the individual owed DAYS rather than
    # the calendar runs `find_observation_gaps` returns, because each of them has to be mapped onto a window of
    # the lane's grid; on a daily cadence the two renderings carry exactly the same days.
    cadence = _require_cadence(publication_cadence_days)
    return tuple(
        point
        for silence in _silences(
            tuple(observed),
            expected_first_day=expected_first_day,
            through_day=through_day,
            cadence=cadence,
        )
        for point in publication_grid_points(silence.first_owed, silence.gap_to, cadence)
    )


def summarise_days_below_expected_floor(days: Sequence[ObservedDay]) -> ObservationsBelowExpectedFloor | None:
    """Summarise the days clipped off below the declared floor, or None when every day is inside the window."""
    if not days:
        return None
    ordered = sort_observed_days(days)
    return ObservationsBelowExpectedFloor(
        day_count=len(ordered),
        observation_count=sum(entry.observation_count for entry in ordered),
        earliest_day=ordered[0].day,
        latest_day=ordered[-1].day,
    )


def _gap_from_silence(silence: _Silence, cadence: timedelta) -> ObservationGap:
    """Render one silence as the report's gap, counting the publications owed inside it."""
    # `first_owed` is the first cadence grid point inside the silence: the declared floor for a head gap, and
    # one cadence period past the last publication otherwise. `_silences` opens a silence only once that point
    # is reached, so the count below is never zero.
    owed = len(publication_grid_points(silence.first_owed, silence.gap_to, cadence))
    return ObservationGap(silence.gap_from, silence.gap_to, (silence.gap_to - silence.gap_from).days + 1, owed)


def rank_gaps(gaps: Sequence[ObservationGap], limit: int) -> tuple[tuple[ObservationGap, ...], int]:
    """Return the worst `limit` gaps, longest first then earliest, and how many were left out."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    ranked = sorted(gaps, key=lambda gap: (-gap.days, gap.gap_from))
    return tuple(ranked[:limit]), max(0, len(ranked) - limit)


def newest_observation_cluster(
    days: Sequence[ObservedDay],
    *,
    cluster_gap_days: int = OBSERVATION_CLUSTER_GAP_DAYS,
) -> tuple[ObservedDay, ...]:
    """Keep the most recent run of days, where any gap wider than the mirrored threshold ends the run."""
    if not days:
        return ()
    ordered = sort_observed_days(days)
    start_index = 0
    for index in range(1, len(ordered)):
        if (ordered[index].day - ordered[index - 1].day).days > cluster_gap_days:
            start_index = index
    return ordered[start_index:]


def density_floor_for(cluster: Sequence[ObservedDay]) -> int:
    """The observations a day needs to anchor the axis: 1% of the busiest day in the cluster, never below one."""
    if not cluster:
        return MINIMUM_DENSITY_FLOOR
    busiest = max(observed.observation_count for observed in cluster)
    # Exact numeric arithmetic, matching `CEIL(MAX(observation_count) * 0.01::numeric)`; see the constant's note.
    scaled = Decimal(busiest) * OBSERVATION_DENSITY_FLOOR_FRACTION
    return max(MINIMUM_DENSITY_FLOOR, int(scaled.to_integral_value(rounding=ROUND_CEILING)))


def apply_density_floor(cluster: Sequence[ObservedDay], floor: int) -> tuple[ObservedDay, ...]:
    """Move the start of the axis to the first day that meets the floor; days inside stay, however thin."""
    ordered = sort_observed_days(cluster)
    for index, observed in enumerate(ordered):
        if observed.observation_count >= floor:
            return ordered[index:]
    return ()


def build_slider_window(
    days: Sequence[ObservedDay],
    *,
    cluster_gap_days: int = OBSERVATION_CLUSTER_GAP_DAYS,
) -> SliderWindow:
    """Run the day series through both axis rules and report the window the slider would offer."""
    ordered = sort_observed_days(days)
    if not ordered:
        return SliderWindow(None, None, 0, "no_observations", None, None, 0, 0, None)

    recorded_earliest = ordered[0].day
    cluster = newest_observation_cluster(ordered, cluster_gap_days=cluster_gap_days)
    continuous_earliest = cluster[0].day
    floor = density_floor_for(cluster)
    dense = apply_density_floor(cluster, floor)
    dense_earliest = dense[0].day if dense else None

    if dense_earliest is None:
        rule: EarliestObservedDayRule = "no_observations"
    elif dense_earliest != continuous_earliest:
        rule = "density_floored"
    elif continuous_earliest != recorded_earliest:
        rule = "gap_clustered"
    else:
        rule = "full_history"

    return SliderWindow(
        earliest_observed_day=dense_earliest,
        latest_observed_day=ordered[-1].day if dense else None,
        observed_day_count=len(dense),
        rule=rule,
        earliest_recorded_day=recorded_earliest,
        earliest_continuous_day=continuous_earliest,
        gap_excluded_day_count=max(0, len(ordered) - len(cluster)),
        density_excluded_day_count=max(0, len(cluster) - len(dense)),
        density_floor=floor,
    )


def find_thin_days(days: Sequence[ObservedDay], window: SliderWindow, limit: int) -> tuple[tuple[ThinDay, ...], int]:
    """List the days inside the slider window that fall under its density floor, thinnest first."""
    if window.earliest_observed_day is None or window.density_floor is None:
        return (), 0
    floor = window.density_floor
    thin = [
        ThinDay(observed.day, observed.observation_count, floor)
        for observed in sort_observed_days(days)
        if observed.day >= window.earliest_observed_day and observed.observation_count < floor
    ]
    thin.sort(key=lambda entry: (entry.observation_count, entry.day))
    return tuple(thin[:limit]), max(0, len(thin) - limit)


def decide_verdict(
    *,
    total_rows: int,
    validity: Sequence[ValidityFinding],
    lanes: Sequence[LaneState],
    largest_gap_days: int,
    publication_cadence_days: int | None,
) -> tuple[StreamVerdict, tuple[str, ...]]:
    """Decide the stream's verdict and name the evidence that decided it; `invalid` outranks `incomplete`."""
    failing = [finding for finding in validity if finding.is_failing]
    dead_lettered = [lane for lane in lanes if lane.dead_letter > 0]

    evidence: list[str] = [f"{finding.check}: {finding.count:,} row(s) -- {finding.breaks}" for finding in failing]
    evidence.extend(
        f"lane {lane.lane} run {lane.run_key} has {lane.dead_letter:,} dead-lettered window(s); "
        "a dead letter is never success"
        for lane in dead_lettered
    )
    if total_rows == 0:
        evidence.append("the stream holds no published rows at all, so nothing it claims to serve is served")
    if publication_cadence_days is not None and largest_gap_days > publication_cadence_days:
        evidence.append(
            f"largest gap is {largest_gap_days:,} day(s) against a {publication_cadence_days}-day publication cadence"
        )

    if failing:
        return "invalid", tuple(evidence)
    if evidence:
        return "incomplete", tuple(evidence)
    return "complete", ("no dead-lettered window, no gap beyond the cadence, and every validity check at zero",)


def lane_floor_day(keys: Iterable[str]) -> date | None:
    """Read the oldest ISO day a run's own keys name; that day is the floor the lane declared it would fill."""
    days: list[date] = []
    for key in keys:
        match = _ISO_DAY_IN_SHARD_KEY.search(key)
        if match is None:
            continue
        try:
            days.append(date.fromisoformat(match.group(0)))
        except ValueError:
            # A key can embed a shaped-but-unreal day; it names no floor and must not become one.
            continue
    return min(days) if days else None
