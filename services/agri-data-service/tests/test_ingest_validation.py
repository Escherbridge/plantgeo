"""Pin the completeness/validity report: the two mirrored axis rules, gap detection, verdicts and both renderers.

No database: the pure rules are exercised directly and the SQL-bearing entry point is fed a stubbed session
that answers each statement by the `-- <marker>` comment the statement opens with.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest

from agri_data_service.ingest import validation
from agri_data_service.ingest.archive_walk import archive_lane_definition_name, archive_lane_run_key
from agri_data_service.ingest.identity import MAX_NATURAL_KEY_LENGTH, PRODUCER_BY_LAYER_NAME
from agri_data_service.ingest.lanes import BACKFILL_LANES, FIRMS_ARCHIVE_LANE, STREAMFLOW_ARCHIVE_LANE
from agri_data_service.ingest.validation import (
    DEAD_LETTER_WORK_ITEM_STATE,
    DUPLICATE_IDENTITY_CHECK,
    MISSING_VALUE_SENTINEL_CHECK,
    NULL_GEOMETRY_CHECK,
    OBSERVATION_CLUSTER_GAP_DAYS,
    OBSERVATION_DENSITY_FLOOR_FRACTION,
    OUTSIDE_BBOX_CHECK,
    UNDATED_DAY_CHECK,
    UNLINKED_GEOMETRY_CHECK,
    USGS_NO_DATA_SENTINEL,
    VALIDITY_CHECK_CONSEQUENCES,
    VALIDITY_CHECK_ORDER,
    LaneState,
    ObservedDay,
    ObservedDayScanTooLargeError,
    StreamDefinition,
    StreamObservations,
    ValidationRowError,
    ValidityFinding,
    apply_density_floor,
    build_stream_report,
    build_validation_report,
    build_validity_findings,
    decide_verdict,
    density_floor_for,
    find_observation_gaps,
    find_thin_days,
    lane_floor_day,
    newest_observation_cluster,
    producer_local_id_ceiling,
    rank_gaps,
    split_days_at_expected_floor,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.ingest.lanes import BackfillLane


# ---------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------

# The only spellings of a lane the ledger can hold, taken from the functions that mint them rather than typed
# out. Writing `agri.backfill.streamflow` here once let the catalog and the ledger disagree in silence.
FIRMS_LANE_NAME = archive_lane_definition_name(FIRMS_ARCHIVE_LANE)
FIRMS_RUN_KEY = archive_lane_run_key(FIRMS_ARCHIVE_LANE)
STREAMFLOW_LANE_NAME = archive_lane_definition_name(STREAMFLOW_ARCHIVE_LANE)
STREAMFLOW_RUN_KEY = archive_lane_run_key(STREAMFLOW_ARCHIVE_LANE)


def day(text: str) -> date:
    """Read a YYYY-MM-DD literal, so the test bodies read as calendars rather than as constructor calls."""
    return date.fromisoformat(text)


def observed(text: str, count: int = 100) -> ObservedDay:
    """Build one observed day at a stated row count."""
    return ObservedDay(day(text), count)


def run_of_days(start: str, length: int, count: int = 100) -> list[ObservedDay]:
    """Build `length` consecutive observed days from `start`, each carrying the same row count."""
    first = day(start)
    return [ObservedDay(date.fromordinal(first.toordinal() + offset), count) for offset in range(length)]


def lane(  # noqa: PLR0913 - one parameter per work-item state a lane row can report
    backfill_lane: BackfillLane = STREAMFLOW_ARCHIVE_LANE,
    *,
    name: str | None = None,
    run_key: str | None = None,
    succeeded: int = 0,
    retry_wait: int = 0,
    dead_letter: int = 0,
    queued: int = 0,
    floor: date | None = None,
) -> LaneState:
    """Build one lane-run row, named the way the runtime names a REGISTERED lane's definition and run.

    `name`/`run_key` override only for the foreign-lane cases: a definition some other tool put in the ledger
    that no entry in `lanes.BACKFILL_LANES` could have produced, which is what `unmatched_lanes` is for.
    """
    return LaneState(
        lane=name if name is not None else archive_lane_definition_name(backfill_lane),
        run_key=run_key if run_key is not None else archive_lane_run_key(backfill_lane),
        total_windows=succeeded + retry_wait + dead_letter + queued,
        succeeded=succeeded,
        retry_wait=retry_wait,
        dead_letter=dead_letter,
        queued=queued,
        other_states=MappingProxyType({}),
        oldest_outstanding_window="example:2026-01-01" if queued or retry_wait or dead_letter else None,
        newest_outstanding_window="example:2026-02-01" if queued or retry_wait or dead_letter else None,
        lane_floor_day=floor,
    )


TIME_SERIES = StreamDefinition(
    stream="water-gauges",
    kind="time_series",
    store="features",
    publication_cadence_days=1,
    cadence_basis="infra/cron-streamflow/railway.json runs `*/30 * * * *`",
)

REFERENCE = StreamDefinition(stream="watersheds", kind="reference", store="features")


def observations(
    *,
    total_rows: int = 100,
    days: Sequence[ObservedDay] = (),
    counts: Mapping[str, int] | None = None,
) -> StreamObservations:
    """Build the raw per-stream observations the pure builder consumes."""
    return StreamObservations(
        total_rows=total_rows,
        day_counts=tuple(days),
        check_counts=MappingProxyType(dict(counts or {})),
    )


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own INGEST_BBOX out of the bbox-resolution paths under test."""
    monkeypatch.delenv("INGEST_BBOX", raising=False)


# ---------------------------------------------------------------------------------------------------------------
# The constants mirrored from the TypeScript read model
# ---------------------------------------------------------------------------------------------------------------


def test_the_mirrored_axis_constants_hold_the_values_the_read_model_actually_uses() -> None:
    # 21, not 45: water-gauges' largest real gap is 15 days and the gap back to its 1990s stragglers is 23, so
    # a 45-day threshold would swallow the straggler gap and restore the 36-year axis the clustering exists to
    # prevent. environmental-read-model.ts:1961.
    assert OBSERVATION_CLUSTER_GAP_DAYS == 21
    assert float(OBSERVATION_DENSITY_FLOOR_FRACTION) == 0.01
    assert USGS_NO_DATA_SENTINEL == -999999.0


# The TypeScript this module claims to mirror, read from the repository root. Three directories up from
# `tests/`: `tests` -> `agri-data-service` -> `services` -> repository root, the same idiom
# `test_migration_runtime_contract.py` uses to reach `infra/`.
READ_MODEL_SOURCE_PATH = Path(__file__).resolve().parents[3] / validation.MIRRORED_READ_MODEL_PATH


def read_model_constant(name: str) -> str:
    """Read one top-level `const <name> = <value>;` literal out of the TypeScript read model.

    Matched by NAME and not by line number, because line numbers drift on every edit above them while the
    declaration does not. TypeScript permits `_` digit separators (this very file writes `5_000`), so they are
    stripped before the value is parsed.
    """
    source = READ_MODEL_SOURCE_PATH.read_text(encoding="utf-8")
    declaration = re.search(rf"^const {re.escape(name)} = ([^;]+);", source, flags=re.MULTILINE)
    assert declaration is not None, (
        f"{name} is no longer declared at the top level of {validation.MIRRORED_READ_MODEL_PATH}; "
        "validation.py mirrors it and can no longer prove what it mirrors"
    )
    return declaration.group(1).strip().replace("_", "")


def test_the_mirrored_axis_constants_equal_the_typescript_declarations_they_are_copied_from() -> None:
    """The one thing that makes this a mirror rather than a coincidence: both sides are read and compared.

    The reviewer walked this module's clustering and density algorithm against `readObservationWindows`
    member for member and found no drift today, but nothing enforced it -- unlike geo.feature_observation_day,
    which has `src/__tests__/lib/observation-day-contract.test.ts`. A report that applies a 45-day continuity
    rule while the slider applies 21 does not fail; it quietly answers a question the UI never asked.
    """
    assert int(read_model_constant("OBSERVATION_CLUSTER_GAP_DAYS")) == OBSERVATION_CLUSTER_GAP_DAYS
    assert Decimal(read_model_constant("OBSERVATION_DENSITY_FLOOR_FRACTION")) == OBSERVATION_DENSITY_FLOOR_FRACTION
    assert float(read_model_constant("USGS_NO_DATA_SENTINEL")) == USGS_NO_DATA_SENTINEL


def test_every_declared_validity_check_carries_the_line_saying_what_it_breaks() -> None:
    assert set(VALIDITY_CHECK_ORDER) == set(VALIDITY_CHECK_CONSEQUENCES)
    assert all(VALIDITY_CHECK_CONSEQUENCES[check].strip() for check in VALIDITY_CHECK_ORDER)


# ---------------------------------------------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------------------------------------------


def test_a_single_missing_day_between_two_observed_days_is_reported_as_a_one_day_gap() -> None:
    gaps = find_observation_gaps([observed("2026-03-01"), observed("2026-03-03")])

    assert [(gap.gap_from, gap.gap_to, gap.days) for gap in gaps] == [(day("2026-03-02"), day("2026-03-02"), 1)]


def test_consecutive_observed_days_produce_no_gap_at_all() -> None:
    assert find_observation_gaps(run_of_days("2026-03-01", 5)) == ()


def test_a_gap_at_the_head_is_measured_from_the_expected_first_day_not_the_first_observed_day() -> None:
    gaps = find_observation_gaps(
        [observed("2026-03-05"), observed("2026-03-06")],
        expected_first_day=day("2026-03-01"),
    )

    assert (gaps[0].gap_from, gaps[0].gap_to, gaps[0].days) == (day("2026-03-01"), day("2026-03-04"), 4)


def test_an_expected_first_day_at_or_after_the_first_observed_day_opens_no_head_gap() -> None:
    gaps = find_observation_gaps(run_of_days("2026-03-01", 3), expected_first_day=day("2026-03-01"))

    assert gaps == ()


def test_a_gap_at_the_tail_runs_from_the_last_observed_day_to_the_reporting_day() -> None:
    gaps = find_observation_gaps(
        [observed("2026-03-01"), observed("2026-03-02")],
        through_day=day("2026-03-06"),
    )

    assert (gaps[-1].gap_from, gaps[-1].gap_to, gaps[-1].days) == (day("2026-03-03"), day("2026-03-06"), 4)


def test_a_reporting_day_equal_to_the_last_observed_day_opens_no_tail_gap() -> None:
    assert find_observation_gaps(run_of_days("2026-03-01", 3), through_day=day("2026-03-03")) == ()


def test_a_stream_with_no_observed_day_reports_the_whole_expected_span_as_one_gap() -> None:
    gaps = find_observation_gaps([], expected_first_day=day("2026-03-01"), through_day=day("2026-03-10"))

    assert (gaps[0].gap_from, gaps[0].gap_to, gaps[0].days) == (day("2026-03-01"), day("2026-03-10"), 10)


def test_a_stream_with_neither_observations_nor_an_expected_span_reports_no_gap_rather_than_guessing() -> None:
    assert find_observation_gaps([]) == ()


def test_gaps_are_reported_worst_first_and_the_omitted_count_names_what_was_dropped() -> None:
    gaps = find_observation_gaps(
        [observed("2026-01-01"), observed("2026-01-11"), observed("2026-01-14"), observed("2026-02-14")],
    )

    worst, omitted = rank_gaps(gaps, 2)

    assert [gap.days for gap in worst] == [30, 9]
    assert omitted == 1
    # Never silently truncated: the two listed plus the one omitted account for every gap found.
    assert len(worst) + omitted == len(gaps)


def test_gaps_of_equal_length_are_ranked_earliest_first_so_the_listing_is_reproducible() -> None:
    gaps = find_observation_gaps(
        [observed("2026-01-01"), observed("2026-01-05"), observed("2026-01-09")],
    )

    worst, _ = rank_gaps(gaps, 2)

    assert [gap.gap_from for gap in worst] == [day("2026-01-02"), day("2026-01-06")]


def test_ranking_gaps_with_a_negative_limit_is_refused_rather_than_silently_emptied() -> None:
    with pytest.raises(ValueError, match="limit must not be negative"):
        rank_gaps((), -1)


# ---------------------------------------------------------------------------------------------------------------
# Publication cadence. The gap walk runs on the grid the stream declared it publishes on, not on the calendar:
# a daily grid called the six ordinary days between two weekly USDM releases missing, so a stream that had
# published every single release it owed reported roughly 6/7 of the calendar absent.
# ---------------------------------------------------------------------------------------------------------------

WEEKLY = StreamDefinition(
    stream="drought_areas",
    kind="time_series",
    store="drought_areas",
    publication_cadence_days=7,
    cadence_basis="USDM publishes one release every Tuesday",
)

FIVE_DAY = StreamDefinition(
    stream="vegetation",
    kind="time_series",
    store="features",
    publication_cadence_days=5,
    cadence_basis="Sentinel-2 L2A revisits mid-latitudes about every five days",
)


def weekly_issues(start: str, length: int, count: int = 40) -> list[ObservedDay]:
    """Build `length` consecutive weekly releases from `start`, the rhythm USDM actually publishes on."""
    first = day(start)
    return [ObservedDay(date.fromordinal(first.toordinal() + 7 * index), count) for index in range(length)]


def test_a_weekly_stream_holding_every_release_it_owed_reports_no_missing_day_at_all() -> None:
    report = build_stream_report(
        WEEKLY,
        observations(days=weekly_issues("2026-06-02", 10)),
        (),
        bbox=None,
        server_day=day("2026-08-04"),
    )

    assert report.completeness.missing_day_count == 0
    assert report.completeness.gap_count == 0
    assert report.verdict == "complete"


def test_the_daily_grid_called_that_same_complete_weekly_stream_54_days_short() -> None:
    """The defect in one assertion: nine six-day holes between ten releases that were all published on time."""
    assert sum(gap.days for gap in find_observation_gaps(weekly_issues("2026-06-02", 10))) == 54


def test_a_weekly_stream_missing_one_release_reports_exactly_one_missing_publication() -> None:
    without_a_release = [entry for entry in weekly_issues("2026-06-02", 10) if entry.day != day("2026-07-07")]

    report = build_stream_report(
        WEEKLY, observations(days=without_a_release), (), bbox=None, server_day=day("2026-08-04")
    )
    gap = report.completeness.worst_gaps[0]

    assert report.completeness.missing_day_count == 1
    assert report.completeness.gap_count == 1
    # The silence is still measured in calendar days, because that is the number `decide_verdict` compares
    # against the declared cadence -- and 13 days of silence on a 7-day cadence is what makes this incomplete.
    assert (gap.gap_from, gap.gap_to, gap.days, gap.missed_publications) == (
        day("2026-07-01"),
        day("2026-07-13"),
        13,
        1,
    )
    assert gap.to_summary() == {"from": "2026-07-01", "to": "2026-07-13", "days": 13, "missed_publications": 1}
    assert report.verdict == "incomplete"


def test_a_weekly_stream_owes_nothing_at_the_tail_until_a_whole_cadence_period_has_passed() -> None:
    published = weekly_issues("2026-06-02", 2)

    assert find_observation_gaps(published, through_day=day("2026-06-15"), publication_cadence_days=7) == ()
    # 2026-06-16 is one full cadence past the release on 2026-06-09, so exactly one release is now owed.
    due = find_observation_gaps(published, through_day=day("2026-06-16"), publication_cadence_days=7)
    assert [gap.missed_publications for gap in due] == [1]
    assert [gap.days for gap in due] == [7]


def test_a_five_day_cadence_walks_the_five_day_grid_so_a_sentinel_revisit_is_not_four_missing_days() -> None:
    on_revisit = [observed("2026-06-01"), observed("2026-06-06"), observed("2026-06-11")]

    assert find_observation_gaps(on_revisit, publication_cadence_days=5) == ()
    report = build_stream_report(FIVE_DAY, observations(days=on_revisit), (), bbox=None, server_day=day("2026-06-11"))
    assert report.completeness.missing_day_count == 0


def test_a_cadence_shorter_than_a_day_is_refused_by_the_walk_as_well_as_by_the_catalog() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        find_observation_gaps(run_of_days("2026-03-01", 3), publication_cadence_days=0)


# ---------------------------------------------------------------------------------------------------------------
# The expected-floor clip. A day below the declared floor is a real observation, but it is neither a gap nor
# coverage, so it leaves the gap walk and is reported on its own.
# ---------------------------------------------------------------------------------------------------------------


def decommissioned_gauge_stragglers() -> list[ObservedDay]:
    """The 46 last-ever readings from decommissioned USGS gauges: 38 days spanning 1990-09-30 to 2020-12-03."""
    first, last = day("1990-09-30"), day("2020-12-03")
    step = (last.toordinal() - first.toordinal()) // 37
    spread = [date.fromordinal(first.toordinal() + step * index) for index in range(37)]
    # 46 rows over 38 days: four gauges reported three times before they were retired, every other one once.
    return [ObservedDay(entry, 3 if index < 4 else 1) for index, entry in enumerate([*spread, last])]


def water_gauges_production_shape() -> list[ObservedDay]:
    """The water-gauges day series measured on 2026-08-08: 38 straggler days plus 448 real ones.

    Shaped to reproduce every published number at once -- 486 observed days, 448 of them inside the lane's
    declared floor of 2022-08-05, and a newest cluster of 46 days beginning 2026-05-24.
    """
    return [
        *decommissioned_gauge_stragglers(),
        *run_of_days("2022-08-05", 402, 800),
        observed("2026-05-24", 3),
        observed("2026-06-08", 850),
        *run_of_days("2026-06-26", 44, 10_911),
    ]


def water_gauges_report() -> validation.StreamReport:
    """Build the measured water-gauges stream against its lane floor and the 2026-08-08 server day."""
    return build_stream_report(
        TIME_SERIES,
        observations(total_rows=312_360, days=water_gauges_production_shape()),
        [lane(floor=day("2022-08-05"))],
        bbox=None,
        server_day=day("2026-08-08"),
    )


def test_a_day_below_the_expected_first_day_is_split_out_of_the_series_rather_than_walked() -> None:
    below, inside = split_days_at_expected_floor(
        [observed("1990-09-30"), observed("2020-12-03"), observed("2026-03-01")], day("2022-08-05")
    )

    assert [entry.day for entry in below] == [day("1990-09-30"), day("2020-12-03")]
    assert [entry.day for entry in inside] == [day("2026-03-01")]


def test_a_series_with_no_declared_floor_is_left_whole_because_nothing_says_which_days_were_owed() -> None:
    below, inside = split_days_at_expected_floor([observed("1990-09-30"), observed("2026-03-01")], None)

    assert below == ()
    assert [entry.day for entry in inside] == [day("1990-09-30"), day("2026-03-01")]


def test_a_day_exactly_on_the_expected_first_day_is_inside_the_window_rather_than_below_it() -> None:
    below, inside = split_days_at_expected_floor([observed("2022-08-05")], day("2022-08-05"))

    assert below == ()
    assert [entry.day for entry in inside] == [day("2022-08-05")]


def test_the_holes_between_two_days_below_the_expected_floor_open_no_missing_day() -> None:
    # The head-gap branch cannot catch these on its own: it fires only when `expected_first_day <
    # first_observed`, and a straggler at 1990-09-30 makes that false, so 30 years of inter-straggler holes
    # used to stay in the count.
    gaps = find_observation_gaps(
        [observed("1990-09-30"), observed("2020-12-03"), *run_of_days("2022-08-05", 3)],
        expected_first_day=day("2022-08-05"),
        through_day=day("2022-08-07"),
    )

    assert gaps == ()


def test_a_stream_holding_only_days_below_the_expected_floor_owes_its_whole_declared_window() -> None:
    gaps = find_observation_gaps(
        [observed("1990-09-30"), observed("2020-12-03")],
        expected_first_day=day("2022-08-05"),
        through_day=day("2022-08-14"),
    )

    assert [(gap.gap_from, gap.gap_to, gap.days) for gap in gaps] == [(day("2022-08-05"), day("2022-08-14"), 10)]


def test_the_measured_water_gauges_shape_reports_1017_missing_days_of_1465_not_12611_of_13097() -> None:
    """The whole of the clip in one assertion, against the figures measured in production on 2026-08-08.

    Unclipped, `pairwise` walked all 13,097 days from the oldest straggler and reported 12,611 missing: 12.4x
    the truth, and larger than the stream's own declared span, which is the tell.
    """
    completeness = water_gauges_report().completeness

    assert completeness.expected_first_day == day("2022-08-05")
    assert completeness.expected_first_day_source == "lane_floor"
    assert completeness.expected_day_span == 1_465
    assert completeness.missing_day_count == 1_017
    # The invariant the unclipped walk violated: a stream cannot miss more days than it ever owed.
    assert completeness.missing_day_count <= completeness.expected_day_span
    assert completeness.observed_day_count == 486
    assert completeness.observed_day_count_inside_expected_window == 448


def test_the_days_below_the_expected_floor_are_reported_as_their_own_finding_rather_than_discarded() -> None:
    completeness = water_gauges_report().completeness
    below = completeness.days_below_expected_floor

    assert below is not None
    assert (below.day_count, below.observation_count) == (38, 46)
    assert (below.earliest_day, below.latest_day) == (day("1990-09-30"), day("2020-12-03"))
    # Still reported as the oldest thing the warehouse holds; clipping is about what was OWED, not what is here.
    assert completeness.first_observed_day == day("1990-09-30")


def test_clipping_the_gap_walk_leaves_the_slider_window_the_ui_draws_exactly_where_it_was() -> None:
    """The half that already agreed with the UI must keep agreeing: the axis rules see the UNCLIPPED series."""
    window = water_gauges_report().completeness.slider_window

    assert window.earliest_recorded_day == day("1990-09-30")
    assert window.gap_excluded_day_count == 440
    assert window.earliest_continuous_day == day("2026-05-24")
    assert window.earliest_observed_day == day("2026-06-08")
    assert window.rule == "density_floored"


def test_a_stream_whose_floor_is_its_own_first_observed_day_reports_no_days_below_the_floor() -> None:
    report = build_stream_report(
        TIME_SERIES,
        observations(days=run_of_days("2026-03-01", 3)),
        (),
        bbox=None,
        server_day=day("2026-03-03"),
    )

    assert report.completeness.days_below_expected_floor is None
    assert report.completeness.observed_day_count_inside_expected_window == 3


# ---------------------------------------------------------------------------------------------------------------
# Continuity clustering, matching the UI rule
# ---------------------------------------------------------------------------------------------------------------


def test_continuity_clustering_drops_a_run_separated_by_more_than_the_mirrored_gap_threshold() -> None:
    stragglers = [observed("1990-09-30"), observed("1990-10-01")]
    real_record = run_of_days("2026-06-01", 10)

    cluster = newest_observation_cluster(stragglers + real_record)

    assert cluster[0].day == day("2026-06-01")
    assert len(cluster) == 10


def test_continuity_clustering_keeps_a_run_separated_by_exactly_the_mirrored_gap_threshold() -> None:
    # 2026-03-01 to 2026-03-22 is a 21-day step, which is `> 21` false, so the two days stay one run.
    cluster = newest_observation_cluster([observed("2026-03-01"), observed("2026-03-22")])

    assert [entry.day for entry in cluster] == [day("2026-03-01"), day("2026-03-22")]


def test_continuity_clustering_splits_one_day_past_the_mirrored_gap_threshold() -> None:
    cluster = newest_observation_cluster([observed("2026-03-01"), observed("2026-03-23")])

    assert [entry.day for entry in cluster] == [day("2026-03-23")]


def test_continuity_clustering_of_an_empty_series_returns_nothing_rather_than_raising() -> None:
    assert newest_observation_cluster([]) == ()


def test_continuity_clustering_keeps_only_the_newest_run_when_several_are_separated() -> None:
    cluster = newest_observation_cluster(
        [observed("2020-01-01"), observed("2023-01-01"), observed("2026-01-01"), observed("2026-01-02")]
    )

    assert [entry.day for entry in cluster] == [day("2026-01-01"), day("2026-01-02")]


# ---------------------------------------------------------------------------------------------------------------
# The density floor
# ---------------------------------------------------------------------------------------------------------------


def test_the_density_floor_is_one_percent_of_the_busiest_day_rounded_up() -> None:
    assert density_floor_for([observed("2026-01-01", 10_911), observed("2026-01-02", 7)]) == 110


def test_the_density_floor_never_falls_below_one_observation() -> None:
    assert density_floor_for([observed("2026-01-01", 19), observed("2026-01-02", 1)]) == 1


def test_the_density_floor_uses_exact_numeric_arithmetic_so_it_agrees_with_postgres_ceil() -> None:
    # In binary floating point 300 * 0.01 is 3.0000000000000004, so math.ceil would answer 4 while
    # `CEIL(300 * 0.01::numeric)` answers 3 -- and the report would exclude a day the slider keeps.
    assert density_floor_for([observed("2026-01-01", 300)]) == 3


def test_the_density_floor_of_an_empty_cluster_is_the_minimum_rather_than_an_error() -> None:
    assert density_floor_for([]) == 1


def test_the_density_floor_moves_the_start_of_the_axis_but_keeps_the_thin_days_inside_it() -> None:
    cluster = [
        observed("2026-05-24", 3),
        observed("2026-06-15", 1),
        observed("2026-08-02", 2_236),
        observed("2026-08-03", 10_911),
    ]

    dense = apply_density_floor(cluster, density_floor_for(cluster))

    assert [entry.day for entry in dense] == [day("2026-08-02"), day("2026-08-03")]


def test_the_density_floor_keeps_a_sparse_day_that_sits_after_the_first_dense_day() -> None:
    cluster = [observed("2026-08-01", 500), observed("2026-08-02", 1), observed("2026-08-03", 500)]

    dense = apply_density_floor(cluster, density_floor_for(cluster))

    assert [entry.day for entry in dense] == [day("2026-08-01"), day("2026-08-02"), day("2026-08-03")]


# ---------------------------------------------------------------------------------------------------------------
# The slider window
# ---------------------------------------------------------------------------------------------------------------


def test_the_slider_window_reports_full_history_when_neither_rule_moved_the_start() -> None:
    window = validation.build_slider_window(run_of_days("2026-03-01", 5))

    assert window.rule == "full_history"
    assert window.earliest_observed_day == day("2026-03-01")
    assert window.latest_observed_day == day("2026-03-05")
    assert (window.gap_excluded_day_count, window.density_excluded_day_count) == (0, 0)
    assert window.span_days == 5


def test_the_slider_window_reports_gap_clustered_when_only_clustering_moved_the_start() -> None:
    window = validation.build_slider_window([observed("1990-09-30"), *run_of_days("2026-03-01", 5)])

    assert window.rule == "gap_clustered"
    assert window.earliest_recorded_day == day("1990-09-30")
    assert window.earliest_continuous_day == day("2026-03-01")
    assert window.earliest_observed_day == day("2026-03-01")
    assert (window.gap_excluded_day_count, window.density_excluded_day_count) == (1, 0)


def test_the_slider_window_reports_density_floored_when_the_floor_moved_the_start_further() -> None:
    window = validation.build_slider_window(
        [
            observed("1990-09-30", 1),
            observed("2026-05-24", 3),
            observed("2026-06-10", 1),
            observed("2026-06-25", 2_236),
            observed("2026-07-05", 10_911),
        ]
    )

    assert window.rule == "density_floored"
    assert window.earliest_recorded_day == day("1990-09-30")
    assert window.earliest_continuous_day == day("2026-05-24")
    assert window.earliest_observed_day == day("2026-06-25")
    assert window.gap_excluded_day_count == 1
    assert window.density_excluded_day_count == 2
    assert window.density_floor == 110


def test_a_stream_with_no_datable_observation_reports_no_observations_and_an_empty_span() -> None:
    window = validation.build_slider_window([])

    assert window.rule == "no_observations"
    assert window.earliest_observed_day is None
    assert window.span_days == 0
    assert window.density_floor is None


def test_thin_days_inside_the_window_are_listed_thinnest_first_with_the_omitted_count() -> None:
    days = [
        observed("2026-07-01", 1_000),
        observed("2026-07-02", 3),
        observed("2026-07-03", 1),
        observed("2026-07-04", 7),
        observed("2026-07-05", 1_000),
    ]
    window = validation.build_slider_window(days)

    thin, omitted = find_thin_days(days, window, 2)

    assert [(entry.day, entry.observation_count) for entry in thin] == [
        (day("2026-07-03"), 1),
        (day("2026-07-02"), 3),
    ]
    assert omitted == 1
    assert all(entry.density_floor == 10 for entry in thin)


def test_a_day_that_the_density_floor_excluded_from_the_axis_is_not_also_counted_as_a_thin_day() -> None:
    days = [observed("2026-05-01", 1), observed("2026-05-10", 1_000)]
    window = validation.build_slider_window(days)

    thin, omitted = find_thin_days(days, window, 10)

    assert window.earliest_observed_day == day("2026-05-10")
    assert (thin, omitted) == ((), 0)


def test_finding_thin_days_on_a_stream_with_no_window_returns_nothing_rather_than_raising() -> None:
    assert find_thin_days([], validation.build_slider_window([]), 10) == ((), 0)


# ---------------------------------------------------------------------------------------------------------------
# Validity findings
# ---------------------------------------------------------------------------------------------------------------


def test_an_unevaluated_check_must_carry_the_reason_it_could_not_run() -> None:
    with pytest.raises(ValueError, match="must carry the reason"):
        ValidityFinding(NULL_GEOMETRY_CHECK, 0, "breaks", evaluated=False)


def test_an_evaluated_check_may_not_also_claim_it_was_skipped() -> None:
    with pytest.raises(ValueError, match="must not also carry a skip reason"):
        ValidityFinding(NULL_GEOMETRY_CHECK, 0, "breaks", skipped_reason="nope")


def test_an_unevaluated_check_never_counts_as_failing_however_it_is_read() -> None:
    finding = ValidityFinding(OUTSIDE_BBOX_CHECK, 0, "breaks", evaluated=False, skipped_reason="no bbox")

    assert finding.is_failing is False
    assert finding.to_summary()["evaluated"] is False


def test_the_out_of_bbox_check_is_unevaluated_rather_than_zero_when_no_bbox_is_configured() -> None:
    findings = {finding.check: finding for finding in build_validity_findings(TIME_SERIES, observations(), bbox=None)}

    assert findings[OUTSIDE_BBOX_CHECK].evaluated is False
    assert "INGEST_BBOX is not configured" in (findings[OUTSIDE_BBOX_CHECK].skipped_reason or "")


def test_the_sentinel_check_is_unevaluated_for_a_producer_that_emits_no_numeric_missing_marker() -> None:
    findings = {
        finding.check: finding for finding in build_validity_findings(REFERENCE, observations(), bbox="-125,42,-111,49")
    }

    assert findings[MISSING_VALUE_SENTINEL_CHECK].evaluated is False
    assert findings[OUTSIDE_BBOX_CHECK].evaluated is True


def test_a_store_that_cannot_answer_a_check_reports_the_store_reason_rather_than_a_reassuring_zero() -> None:
    drought = StreamDefinition(
        stream="drought_areas",
        kind="time_series",
        store="drought_areas",
        publication_cadence_days=7,
        cadence_basis="USDM publishes one release every Tuesday",
    )
    raw = StreamObservations(
        total_rows=1_035,
        unsupported_checks=frozenset({UNLINKED_GEOMETRY_CHECK}),
        unsupported_reason="geo.drought_areas holds no geometry link",
    )

    findings = {finding.check: finding for finding in build_validity_findings(drought, raw, bbox=None)}

    assert findings[UNLINKED_GEOMETRY_CHECK].evaluated is False
    assert findings[UNLINKED_GEOMETRY_CHECK].skipped_reason == "geo.drought_areas holds no geometry link"


def test_a_reference_stream_whose_rows_carry_no_observation_date_is_a_fact_about_the_layer_not_a_defect() -> None:
    """soil-survey, measured 2026-08-08: 218,653 SSURGO map units carry mukey/muname/hydric and no date field.

    `geo.feature_observation_day` therefore returns NULL for every row, and the report called a
    correctly-modelled reference layer INVALID for being exactly what it is.
    """
    soil_survey = StreamDefinition(stream="soil-survey", kind="reference", store="features")

    findings = build_validity_findings(
        soil_survey, observations(total_rows=218_653, counts={UNDATED_DAY_CHECK: 218_653}), bbox="-125,42,-111,49"
    )
    verdict, _ = decide_verdict(
        total_rows=218_653, validity=findings, lanes=(), largest_gap_days=0, publication_cadence_days=None
    )
    undated = next(finding for finding in findings if finding.check == UNDATED_DAY_CHECK)

    assert verdict == "complete"
    assert undated.evaluated is False
    assert undated.is_failing is False
    # Exempted, NOT hidden: the count and its downstream consequence both stay on the page.
    assert "218,653 of 218,653 published row(s)" in (undated.skipped_reason or "")
    assert "EVERY date on the slider" in (undated.skipped_reason or "")
    assert undated.to_summary()["detail"] == {"undated_row_count": 218_653, "total_rows": 218_653}


def test_a_non_reference_stream_whose_rows_carry_no_observation_date_is_still_invalid() -> None:
    findings = build_validity_findings(
        TIME_SERIES, observations(total_rows=100, counts={UNDATED_DAY_CHECK: 7}), bbox=None
    )
    verdict, evidence = decide_verdict(
        total_rows=100, validity=findings, lanes=(), largest_gap_days=0, publication_cadence_days=1
    )

    assert next(finding for finding in findings if finding.check == UNDATED_DAY_CHECK).evaluated is True
    assert verdict == "invalid"
    assert any(item.startswith("undated_day: 7 row(s)") for item in evidence)


def test_a_snapshot_stream_whose_rows_carry_no_observation_date_is_invalid_because_only_reference_is_exempt() -> None:
    sensors = StreamDefinition(stream="sensors", kind="snapshot", store="features")

    findings = build_validity_findings(sensors, observations(total_rows=40, counts={UNDATED_DAY_CHECK: 40}), bbox=None)
    verdict, _ = decide_verdict(
        total_rows=40, validity=findings, lanes=(), largest_gap_days=0, publication_cadence_days=None
    )

    assert verdict == "invalid"


def test_the_undated_exemption_is_decided_by_the_stream_kind_not_by_whether_the_rows_happen_to_be_undated() -> None:
    # watersheds is reference-kind and DOES carry dates. The exemption must still apply to it, because the rule
    # is about how a reference layer is modelled; a rule reading "every row here is undated, so exempt it"
    # would flip a stream's verdict the day its data changed shape.
    findings = {
        finding.check: finding
        for finding in build_validity_findings(
            REFERENCE, observations(total_rows=9_396, counts={UNDATED_DAY_CHECK: 0}), bbox=None
        )
    }

    assert findings[UNDATED_DAY_CHECK].evaluated is False
    assert "0 of 9,396 published row(s)" in (findings[UNDATED_DAY_CHECK].skipped_reason or "")


def test_findings_come_back_in_the_declared_order_so_two_reports_diff_cleanly() -> None:
    findings = build_validity_findings(TIME_SERIES, observations(), bbox="-125,42,-111,49")

    assert tuple(finding.check for finding in findings) == VALIDITY_CHECK_ORDER


# ---------------------------------------------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------------------------------------------


def test_a_stream_with_no_dead_letter_no_oversized_gap_and_no_finding_is_complete() -> None:
    verdict, evidence = decide_verdict(
        total_rows=100,
        validity=build_validity_findings(TIME_SERIES, observations(), bbox="-125,42,-111,49"),
        lanes=[lane(succeeded=52)],
        largest_gap_days=1,
        publication_cadence_days=1,
    )

    assert verdict == "complete"
    assert evidence == ("no dead-lettered window, no gap beyond the cadence, and every validity check at zero",)


def test_a_gap_exactly_equal_to_the_publication_cadence_is_not_a_finding() -> None:
    verdict, _ = decide_verdict(
        total_rows=100,
        validity=(),
        lanes=(),
        largest_gap_days=7,
        publication_cadence_days=7,
    )

    assert verdict == "complete"


def test_a_gap_one_day_past_the_publication_cadence_makes_the_stream_incomplete() -> None:
    verdict, evidence = decide_verdict(
        total_rows=100,
        validity=(),
        lanes=(),
        largest_gap_days=8,
        publication_cadence_days=7,
    )

    assert verdict == "incomplete"
    assert "8 day(s) against a 7-day publication cadence" in evidence[0]


def test_a_stream_with_no_declared_cadence_is_never_made_incomplete_by_a_gap() -> None:
    verdict, _ = decide_verdict(
        total_rows=100,
        validity=(),
        lanes=(),
        largest_gap_days=4_000,
        publication_cadence_days=None,
    )

    assert verdict == "complete"


def test_a_single_dead_lettered_lane_window_makes_the_stream_incomplete_however_full_it_looks() -> None:
    verdict, evidence = decide_verdict(
        total_rows=476_016,
        validity=(),
        lanes=[lane(succeeded=1_000, dead_letter=1)],
        largest_gap_days=0,
        publication_cadence_days=1,
    )

    assert verdict == "incomplete"
    assert "dead-lettered window(s)" in evidence[0]


def test_a_stream_holding_no_rows_at_all_is_incomplete_even_with_no_cadence_and_no_lane() -> None:
    verdict, evidence = decide_verdict(
        total_rows=0,
        validity=(),
        lanes=(),
        largest_gap_days=0,
        publication_cadence_days=None,
    )

    assert verdict == "incomplete"
    assert "holds no published rows" in evidence[0]


def test_any_failing_validity_check_makes_the_stream_invalid() -> None:
    verdict, evidence = decide_verdict(
        total_rows=312_360,
        validity=[ValidityFinding(MISSING_VALUE_SENTINEL_CHECK, 671, "breaks the colour scale")],
        lanes=(),
        largest_gap_days=0,
        publication_cadence_days=1,
    )

    assert verdict == "invalid"
    assert evidence[0] == "missing_value_sentinel: 671 row(s) -- breaks the colour scale"


def test_invalid_outranks_incomplete_and_the_evidence_still_names_both_reasons() -> None:
    verdict, evidence = decide_verdict(
        total_rows=312_360,
        validity=[ValidityFinding(MISSING_VALUE_SENTINEL_CHECK, 671, "breaks the colour scale")],
        lanes=[lane(dead_letter=3)],
        largest_gap_days=800,
        publication_cadence_days=1,
    )

    assert verdict == "invalid"
    assert any("dead-lettered" in item for item in evidence)
    assert any("800 day(s)" in item for item in evidence)


def test_an_unevaluated_check_alone_never_turns_a_stream_invalid() -> None:
    verdict, _ = decide_verdict(
        total_rows=100,
        validity=[ValidityFinding(OUTSIDE_BBOX_CHECK, 0, "breaks", evaluated=False, skipped_reason="no bbox")],
        lanes=(),
        largest_gap_days=0,
        publication_cadence_days=1,
    )

    assert verdict == "complete"


# ---------------------------------------------------------------------------------------------------------------
# Stream definitions and lane floors
# ---------------------------------------------------------------------------------------------------------------


def test_a_declared_cadence_without_the_measurement_behind_it_is_refused() -> None:
    with pytest.raises(ValueError, match="must carry the basis"):
        StreamDefinition(stream="x", kind="time_series", store="features", publication_cadence_days=1)


def test_a_cadence_shorter_than_a_day_is_refused_because_the_axis_is_a_calendar() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        StreamDefinition(
            stream="x", kind="time_series", store="features", publication_cadence_days=0, cadence_basis="none"
        )


def test_every_default_stream_definition_constructs_and_names_a_distinct_stream() -> None:
    names = [definition.stream for definition in validation.DEFAULT_STREAM_DEFINITIONS]

    assert len(names) == len(set(names))


def test_every_lane_the_catalog_declares_is_producible_by_the_archive_walk_naming_function() -> None:
    # The whole defect in one assertion. The catalog used to spell `agri.backfill.firms` while the ONLY
    # producer of an `agri.job_definition.name` spells `agri.ingest.archive_walk.firms-archive`, so against
    # production every stream's lane list was empty: a lane holding 169 dead-lettered FIRMS windows was no
    # evidence at all, and with no lane floor the 2000->2022 hole measured as zero missing days.
    producible = {archive_lane_definition_name(registered) for registered in BACKFILL_LANES.values()}
    declared = {name for definition in validation.DEFAULT_STREAM_DEFINITIONS for name in definition.lane_names}

    assert declared, "no stream claims a lane at all, so no dead letter can reach a verdict"
    assert declared <= producible
    assert producible == validation.ARCHIVE_LANE_DEFINITION_NAMES


def test_the_two_registered_archive_lanes_are_claimed_by_the_streams_they_fill() -> None:
    by_stream = {definition.stream: definition for definition in validation.DEFAULT_STREAM_DEFINITIONS}

    assert by_stream["fire-detections"].lane_names == (FIRMS_LANE_NAME,)
    assert by_stream["water-gauges"].lane_names == (STREAMFLOW_LANE_NAME,)


def test_every_registered_archive_lane_is_claimed_by_exactly_one_stream() -> None:
    # The same silence as F3, reached from the other direction: a REGISTERED lane no stream claims lands in
    # `unmatched_lanes`, where by construction its dead letters decide nothing. Adding an entry to
    # `lanes.BACKFILL_LANES` without naming it on a stream here is therefore a failure, not an omission.
    claimed = [name for definition in validation.DEFAULT_STREAM_DEFINITIONS for name in definition.lane_names]

    assert sorted(claimed) == sorted(validation.ARCHIVE_LANE_DEFINITION_NAMES)
    assert len(claimed) == len(set(claimed))


def test_a_stream_naming_a_lane_no_registry_could_mint_is_refused_at_construction() -> None:
    # A hand-written lane name fails silently everywhere else: it simply matches no ledger row, and an empty
    # lane list is indistinguishable from a lane with nothing outstanding.
    with pytest.raises(ValueError, match="name no registered lane"):
        StreamDefinition(
            stream="fire-detections", kind="time_series", store="features", lane_names=("agri.backfill.firms",)
        )


def test_the_stored_id_ceiling_leaves_room_for_the_producer_prefix_identity_adds_to_it() -> None:
    # writer.py:66-68 stores `identity.producer_local_id`, not the namespaced `identity.natural_key`, so the
    # ceiling the warehouse must respect is 255 minus the producer token and its colon. Checking for the
    # prefix instead reported all 476,016 production fire-detections rows as malformed on 2026-08-08.
    assert producer_local_id_ceiling("usgs-nwis") == MAX_NATURAL_KEY_LENGTH - len("usgs-nwis") - 1
    assert producer_local_id_ceiling("firms") == 249


def test_every_producer_backed_stream_has_a_stored_id_ceiling_derived_from_its_own_producer() -> None:
    assert set(validation.PRODUCER_LOCAL_ID_CEILING_BY_STREAM) == set(PRODUCER_BY_LAYER_NAME)
    assert all(ceiling < MAX_NATURAL_KEY_LENGTH for ceiling in validation.PRODUCER_LOCAL_ID_CEILING_BY_STREAM.values())


def test_a_lane_floor_is_the_oldest_iso_day_its_shard_keys_name() -> None:
    assert lane_floor_day(["firms:2003-01-01", "firms:2002-06-30", "firms:2026-08-01"]) == day("2002-06-30")


def test_a_shard_key_naming_no_day_contributes_no_lane_floor() -> None:
    assert lane_floor_day(["firms:pnw", "firms:all"]) is None


def test_a_shard_key_naming_a_shaped_but_unreal_day_is_ignored_rather_than_becoming_the_floor() -> None:
    assert lane_floor_day(["firms:2026-02-31", "firms:2026-03-01"]) == day("2026-03-01")


def test_the_expected_first_day_falls_back_from_a_declared_floor_to_the_lane_floor_to_what_is_held() -> None:
    held = run_of_days("2026-03-01", 3)

    from_lane = build_stream_report(
        TIME_SERIES, observations(days=held), [lane(floor=day("2022-08-05"))], bbox=None, server_day=day("2026-03-03")
    )
    from_data = build_stream_report(TIME_SERIES, observations(days=held), (), bbox=None, server_day=day("2026-03-03"))
    declared = build_stream_report(
        StreamDefinition(
            stream="water-gauges",
            kind="time_series",
            store="features",
            publication_cadence_days=1,
            cadence_basis="cron",
            expected_first_day=day("2020-01-01"),
        ),
        observations(days=held),
        [lane(floor=day("2022-08-05"))],
        bbox=None,
        server_day=day("2026-03-03"),
    )

    assert (from_lane.completeness.expected_first_day, from_lane.completeness.expected_first_day_source) == (
        day("2022-08-05"),
        "lane_floor",
    )
    assert (from_data.completeness.expected_first_day, from_data.completeness.expected_first_day_source) == (
        day("2026-03-01"),
        "first_observed",
    )
    assert (declared.completeness.expected_first_day, declared.completeness.expected_first_day_source) == (
        day("2020-01-01"),
        "declared",
    )


def test_a_stream_with_nothing_at_all_reports_an_unknown_span_instead_of_a_fabricated_zero() -> None:
    report = build_stream_report(REFERENCE, observations(total_rows=0), (), bbox=None, server_day=day("2026-08-08"))

    assert report.completeness.expected_first_day is None
    assert report.completeness.expected_first_day_source == "none"
    assert report.completeness.expected_day_span is None
    assert report.verdict == "incomplete"


def test_a_time_series_stream_is_measured_through_the_server_day_but_a_reference_stream_is_not() -> None:
    held = run_of_days("2026-03-01", 3)

    series = build_stream_report(TIME_SERIES, observations(days=held), (), bbox=None, server_day=day("2026-03-10"))
    reference = build_stream_report(REFERENCE, observations(days=held), (), bbox=None, server_day=day("2026-03-10"))

    assert series.completeness.reported_through_day == day("2026-03-10")
    assert series.completeness.missing_day_count == 7
    assert reference.completeness.reported_through_day == day("2026-03-03")
    assert reference.completeness.missing_day_count == 0


# ---------------------------------------------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------------------------------------------


def sample_report() -> validation.ValidationReport:
    """Build a two-stream report holding one invalid stream and one clean one."""
    gauges = build_stream_report(
        TIME_SERIES,
        observations(
            total_rows=312_360,
            days=[observed("1990-09-30", 2), *run_of_days("2026-08-01", 3, 10_000)],
            counts={MISSING_VALUE_SENTINEL_CHECK: 671, UNDATED_DAY_CHECK: 0},
        ),
        [lane(STREAMFLOW_ARCHIVE_LANE, succeeded=40, dead_letter=2, floor=day("2022-08-05"))],
        bbox="-125,42,-111,49",
        server_day=day("2026-08-03"),
        max_reported_gaps=1,
    )
    basins = build_stream_report(
        REFERENCE,
        observations(total_rows=9_396, days=run_of_days("2019-11-18", 4)),
        (),
        bbox="-125,42,-111,49",
        server_day=day("2026-08-03"),
    )
    return validation.ValidationReport(
        generated_at=datetime(2026, 8, 8, 0, 53, tzinfo=UTC),
        server_day=day("2026-08-03"),
        bbox="-125,42,-111,49",
        streams=(gauges, basins),
        unknown_streams=("soil-survey",),
        unmatched_lanes=(lane(name="agri.backfill.orphan", run_key="legacy:orphan:2026-01-01", queued=4),),
    )


def test_the_json_renderer_produces_a_parseable_document_that_names_every_stream_and_verdict() -> None:
    payload = json.loads(sample_report().to_json())

    assert payload["verdicts"] == {"complete": 1, "incomplete": 0, "invalid": 1}
    assert [stream["stream"] for stream in payload["streams"]] == ["water-gauges", "watersheds"]
    assert payload["streams"][0]["verdict"] == "invalid"
    assert payload["bbox"] == "-125,42,-111,49"
    assert payload["unknown_streams"] == ["soil-survey"]


def test_the_json_renderer_states_the_axis_rules_and_the_scan_bounds_it_measured_under() -> None:
    payload = json.loads(sample_report().to_json())

    assert payload["axis_rules"]["observation_cluster_gap_days"] == 21
    assert payload["axis_rules"]["observation_density_floor_fraction"] == 0.01
    assert payload["axis_rules"]["mirrored_from"].endswith("environmental-read-model.ts")
    assert payload["scan_bounds"]["statement_timeout_seconds"] == 120
    assert payload["scan_bounds"]["feature_status"] == "published"


def test_the_json_renderer_emits_each_gap_as_an_explicit_from_to_days_triple_and_counts_the_rest() -> None:
    stream = json.loads(sample_report().to_json())["streams"][0]

    # From the lane's declared floor, NOT from the 1990 straggler: the head gap opens where the stream first
    # owed data, and the 32 years before that were never owed.
    assert stream["completeness"]["worst_gaps"] == [{"from": "2022-08-05", "to": "2026-07-31", "days": 1_457}]
    assert stream["completeness"]["omitted_gap_count"] == 0
    assert stream["completeness"]["gap_count"] == 1


def test_the_json_renderer_reports_the_days_below_the_expected_floor_beside_the_gaps_it_no_longer_counts() -> None:
    completeness = json.loads(sample_report().to_json())["streams"][0]["completeness"]

    assert completeness["days_below_expected_floor"] == {
        "day_count": 1,
        "observation_count": 2,
        "earliest_day": "1990-09-30",
        "latest_day": "1990-09-30",
    }
    assert completeness["observed_day_count"] == 4
    assert completeness["observed_day_count_inside_expected_window"] == 3


def test_the_json_renderer_reports_the_slider_window_the_two_axis_rules_actually_produce() -> None:
    window = json.loads(sample_report().to_json())["streams"][0]["completeness"]["slider_window"]

    assert window["rule"] == "gap_clustered"
    assert window["earliest_recorded_day"] == "1990-09-30"
    assert window["earliest_observed_day"] == "2026-08-01"
    assert window["gap_excluded_day_count"] == 1
    assert window["span_days"] == 3


def test_the_json_renderer_is_stable_across_two_renders_of_one_object() -> None:
    report = sample_report()

    assert report.to_json() == report.to_json()


def test_the_markdown_renderer_leads_with_the_verdict_headline_and_the_mirrored_rules() -> None:
    document = sample_report().to_markdown()

    assert document.startswith("# PlantGeo warehouse completeness and validity")
    assert "1 complete, 0 incomplete, 1 invalid" in document
    assert "environmental-read-model.ts" in document
    assert "continuity gap 21 days" in document
    assert "density floor 1%" in document


def test_the_markdown_renderer_writes_every_gap_as_a_readable_from_to_days_line() -> None:
    document = sample_report().to_markdown()

    assert "2022-08-05 to 2026-07-31 (1,457 days)" in document


def test_the_markdown_renderer_says_the_days_below_the_floor_are_neither_a_gap_nor_coverage() -> None:
    document = sample_report().to_markdown()

    assert "fall BELOW the expected first day, 1990-09-30 to 1990-09-30" in document
    assert "neither a gap nor coverage" in document


def test_the_markdown_renderer_says_how_many_gaps_it_omitted_rather_than_truncating_in_silence() -> None:
    stream = build_stream_report(
        TIME_SERIES,
        observations(
            total_rows=10,
            days=[observed("2026-01-01"), observed("2026-01-05"), observed("2026-01-20"), observed("2026-02-20")],
        ),
        (),
        bbox=None,
        server_day=day("2026-02-20"),
        max_reported_gaps=1,
    )
    report = validation.ValidationReport(
        generated_at=datetime(2026, 8, 8, tzinfo=UTC),
        server_day=day("2026-02-20"),
        bbox=None,
        streams=(stream,),
        unknown_streams=(),
        unmatched_lanes=(),
    )

    assert "and 2 further gap(s) not listed." in report.to_markdown()


def test_the_markdown_renderer_names_each_failing_check_with_what_it_breaks_downstream() -> None:
    document = sample_report().to_markdown()

    assert "**missing_value_sentinel: 671**" in document
    assert "flattens every colour scale it lands in" in document


def test_the_markdown_renderer_surfaces_an_unevaluated_check_instead_of_hiding_it_as_a_zero() -> None:
    report = validation.ValidationReport(
        generated_at=datetime(2026, 8, 8, tzinfo=UTC),
        server_day=day("2026-08-08"),
        bbox=None,
        streams=(build_stream_report(TIME_SERIES, observations(), (), bbox=None, server_day=day("2026-08-08")),),
        unknown_streams=(),
        unmatched_lanes=(),
    )

    document = report.to_markdown()

    assert "Bbox: **unset**" in document
    assert "outside_bbox: not evaluated" in document


def test_the_markdown_renderer_reports_lane_windows_and_the_lanes_no_stream_claims() -> None:
    document = sample_report().to_markdown()

    assert f"`{STREAMFLOW_LANE_NAME}` run `{STREAMFLOW_RUN_KEY}`: 42 window(s), 40 succeeded" in document
    assert "2 dead_letter" in document
    assert "## Lanes no stream claims" in document
    assert "`agri.backfill.orphan` run `legacy:orphan:2026-01-01`" in document
    assert "DEFECT IN THIS FILE'S CATALOG" in document


def test_the_markdown_renderer_says_when_no_lane_records_what_filled_a_stream() -> None:
    document = sample_report().to_markdown()

    assert "No lane in the job ledger claims this stream" in document


def test_both_renderers_report_the_same_verdict_for_the_same_object() -> None:
    report = sample_report()
    payload = json.loads(report.to_json())

    for stream in payload["streams"]:
        assert f"`{stream['stream']}` -- " in report.to_markdown()


# ---------------------------------------------------------------------------------------------------------------
# The SQL-bearing entry point, against a stubbed session
# ---------------------------------------------------------------------------------------------------------------


class StubResult:
    """Stand in for a SQLAlchemy Result so a report test needs no database."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> StubResult:
        """Return self; the report only ever calls `.mappings().all()`."""
        return self

    def all(self) -> list[Mapping[str, object]]:
        """Return the canned rows; the name matches SQLAlchemy's own Result method."""
        return self._rows


class StubSession:
    """Answer each statement by the `-- <marker>` comment it opens with, and record what was asked."""

    def __init__(self, rows_by_marker: Mapping[str, Sequence[Mapping[str, object]]]) -> None:
        self._rows_by_marker = dict(rows_by_marker)
        self.markers: list[str] = []
        self.parameters: dict[str, Mapping[str, object]] = {}

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> StubResult:
        """Look the statement's marker up and answer with its canned rows."""
        marker = next(
            (line.removeprefix("-- ").strip() for line in str(statement).splitlines() if line.startswith("-- ")),
            "",
        )
        self.markers.append(marker)
        if parameters is not None:
            self.parameters[marker] = parameters
        return StubResult(self._rows_by_marker.get(marker, ()))


def stub_rows() -> dict[str, list[Mapping[str, object]]]:
    """Canned rows for one whole report: one dense feature stream, one lane, and empty non-feature stores."""
    return {
        "server_day": [{"server_day": day("2026-08-08")}],
        "feature_observed_days": [
            {"stream": "water-gauges", "observed_day": day("2026-08-06"), "observation_count": 9_000},
            {"stream": "water-gauges", "observed_day": day("2026-08-07"), "observation_count": 9_500},
            {"stream": "water-gauges", "observed_day": day("2026-08-08"), "observation_count": 9_200},
        ],
        "feature_validity_counts": [
            {
                "stream": "water-gauges",
                "total_rows": 312_360,
                "null_geom": 0,
                "unlinked_geometry": 0,
                "missing_external_id": 0,
                "malformed_identity": 0,
                "undated_day": 0,
                "future_day": 0,
                "outside_bbox": 0,
                "missing_value_sentinel": 671,
            },
            {
                "stream": "interventions",
                "total_rows": 2,
                "null_geom": 0,
                "unlinked_geometry": 2,
                "missing_external_id": 2,
                "malformed_identity": 0,
                "undated_day": 2,
                "future_day": 0,
                "outside_bbox": 0,
                "missing_value_sentinel": 0,
            },
        ],
        "feature_duplicate_identities": [],
        "drought_area_observed_days": [{"observed_day": day("2026-08-04"), "observation_count": 5}],
        "drought_area_validity_counts": [
            {"total_rows": 1_035, "null_geom": 0, "undated_day": 0, "future_day": 0, "outside_bbox": 0}
        ],
        "historical_observed_days": [],
        "historical_validity_counts": [
            {
                "stream": "historical_vegetation",
                "total_rows": 0,
                "null_geom": 0,
                "undated_day": 0,
                "future_day": 0,
                "outside_bbox": 0,
            }
        ],
        "job_lane_state": [
            {
                "lane": STREAMFLOW_LANE_NAME,
                "run_key": STREAMFLOW_RUN_KEY,
                "work_item_status": "succeeded",
                "window_count": 40,
                "oldest_shard_key": "streamflow-archive:2022-08-05..2022-09-04",
                "newest_shard_key": "streamflow-archive:2026-07-01..2026-07-31",
            },
            {
                "lane": STREAMFLOW_LANE_NAME,
                "run_key": STREAMFLOW_RUN_KEY,
                "work_item_status": DEAD_LETTER_WORK_ITEM_STATE,
                "window_count": 2,
                "oldest_shard_key": "streamflow-archive:2023-03-01..2023-03-31",
                "newest_shard_key": "streamflow-archive:2023-04-01..2023-05-01",
            },
        ],
    }


def clean_feature_validity_row(stream: str, total_rows: int) -> dict[str, object]:
    """One `feature_validity_counts` row with every per-row check at zero, so only the lane can decide."""
    return {
        "stream": stream,
        "total_rows": total_rows,
        **{check: 0 for check in VALIDITY_CHECK_ORDER if check != DUPLICATE_IDENTITY_CHECK},
    }


async def test_the_entry_point_pins_a_read_only_snapshot_and_a_statement_timeout_before_it_reads() -> None:
    session = StubSession(stub_rows())

    await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    assert session.markers[:2] == ["set_read_only_snapshot", "set_statement_timeout"]


async def test_the_entry_point_folds_the_canned_rows_into_a_report_that_names_the_measured_defects() -> None:
    session = StubSession(stub_rows())

    report = await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    by_stream = {stream.stream: stream for stream in report.streams}
    assert report.server_day == day("2026-08-08")
    assert by_stream["water-gauges"].verdict == "invalid"
    assert by_stream["water-gauges"].failing_validity[0].check == MISSING_VALUE_SENTINEL_CHECK
    assert by_stream["water-gauges"].failing_validity[0].count == 671
    assert by_stream["interventions"].verdict == "invalid"
    assert {finding.check for finding in by_stream["interventions"].failing_validity} == {
        UNLINKED_GEOMETRY_CHECK,
        "missing_external_id",
    }
    # interventions is reference-kind, so its 2 undated rows are a fact about the layer rather than a defect;
    # the two checks above are what make it invalid, and the fact still reaches the page.
    undated = next(finding for finding in by_stream["interventions"].validity if finding.check == UNDATED_DAY_CHECK)
    assert undated.evaluated is False
    assert "2 of 2 published row(s)" in (undated.skipped_reason or "")


async def test_the_entry_point_attaches_a_lane_to_the_stream_that_declares_it_and_reads_its_floor() -> None:
    session = StubSession(stub_rows())

    report = await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    gauges = next(stream for stream in report.streams if stream.stream == "water-gauges")
    assert [lane_state.lane for lane_state in gauges.lanes] == [STREAMFLOW_LANE_NAME]
    assert gauges.lanes[0].run_key == STREAMFLOW_RUN_KEY
    assert gauges.lanes[0].dead_letter == 2
    assert gauges.lanes[0].lane_floor_day == day("2022-08-05")
    assert gauges.lanes[0].oldest_outstanding_window == "streamflow-archive:2023-03-01..2023-03-31"
    assert report.unmatched_lanes == ()


async def test_the_entry_point_binds_every_value_as_a_parameter_rather_than_interpolating_it() -> None:
    session = StubSession(stub_rows())

    await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    parameters = session.parameters["feature_validity_counts"]
    assert parameters["bbox_west"] == -125.0
    assert parameters["bbox_north"] == 49.0
    assert parameters["missing_value_sentinel"] == USGS_NO_DATA_SENTINEL
    ceilings = json.loads(str(parameters["producer_local_id_ceiling_by_stream"]))
    assert ceilings["water-gauges"] == producer_local_id_ceiling("usgs-nwis")


async def test_an_unset_bbox_binds_four_nulls_so_the_strict_envelope_makes_the_predicate_a_no_op() -> None:
    session = StubSession(stub_rows())

    report = await build_validation_report(session)  # type: ignore[arg-type]

    assert session.parameters["feature_validity_counts"]["bbox_west"] is None
    assert report.bbox is None
    gauges = next(stream for stream in report.streams if stream.stream == "water-gauges")
    assert next(finding for finding in gauges.validity if finding.check == OUTSIDE_BBOX_CHECK).evaluated is False


async def test_a_stream_the_catalog_does_not_declare_is_surfaced_rather_than_dropped() -> None:
    rows = stub_rows()
    rows["feature_observed_days"].append(
        {"stream": "mystery-layer", "observed_day": day("2026-08-08"), "observation_count": 4}
    )
    session = StubSession(rows)

    report = await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    assert report.unknown_streams == ("mystery-layer",)


async def test_a_lane_no_stream_claims_is_surfaced_rather_than_deciding_nothing_in_silence() -> None:
    rows = stub_rows()
    rows["job_lane_state"].append(
        {
            "lane": "agri.backfill.orphan",
            "run_key": "legacy:orphan:2026-01-01",
            "work_item_status": "queued",
            "window_count": 3,
            "oldest_shard_key": "orphan:2026-01-01",
            "newest_shard_key": "orphan:2026-01-03",
        }
    )
    session = StubSession(rows)

    report = await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    assert [lane_state.lane for lane_state in report.unmatched_lanes] == ["agri.backfill.orphan"]


async def test_a_dead_lettered_firms_window_reaches_the_stream_it_fills_instead_of_deciding_nothing() -> None:
    """The whole point of the lane half of this report, end to end through the SQL-bearing entry point.

    fire-detections here is as healthy as a stream gets: rows, a datable day, and every per-row check at
    zero. The only thing wrong with it is 169 dead-lettered windows in the ledger -- the exact count the bash
    driver lost on 2026-08-07 while reporting success. Under the old `agri.backfill.firms` spelling that lane
    matched nothing, landed in `unmatched_lanes`, and this stream reported COMPLETE.
    """
    rows = stub_rows()
    rows["feature_observed_days"].append(
        {"stream": "fire-detections", "observed_day": day("2026-08-08"), "observation_count": 21_000}
    )
    rows["feature_validity_counts"].append(clean_feature_validity_row("fire-detections", 476_016))
    rows["job_lane_state"].append(
        {
            "lane": FIRMS_LANE_NAME,
            "run_key": FIRMS_RUN_KEY,
            "work_item_status": DEAD_LETTER_WORK_ITEM_STATE,
            "window_count": 169,
            "oldest_shard_key": "firms-archive:2000-11-01..2000-11-06",
            "newest_shard_key": "firms-archive:2022-03-05..2022-03-10",
        }
    )
    session = StubSession(rows)

    report = await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    fires = next(stream for stream in report.streams if stream.stream == "fire-detections")
    assert report.unmatched_lanes == ()
    assert [lane_state.lane for lane_state in fires.lanes] == [FIRMS_LANE_NAME]
    assert fires.lanes[0].dead_letter == 169
    assert fires.verdict != "complete"
    assert any(FIRMS_LANE_NAME in item and "dead-lettered window(s)" in item for item in fires.evidence)
    # And the same wiring opens the head gap the full-archive walk exists to close: with no lane the expected
    # first day fell through to `first_observed`, which makes a 2000->2022 hole definitionally zero days.
    assert fires.completeness.expected_first_day == day("2000-11-01")
    assert fires.completeness.expected_first_day_source == "lane_floor"
    assert fires.completeness.missing_day_count > 9_000


async def test_two_runs_of_one_lane_are_reported_per_run_rather_than_folded_into_one_doubled_total() -> None:
    """Lowering a lane's floor deliberately mints a second `logical_run_key` with its own floor-anchored grid.

    Grouping by definition alone folded the two runs' overlapping calendar coverage into one `total_windows`,
    reporting the lane as owing roughly double. Keeping only the newest run was the other candidate fix and
    was rejected: it silently drops the superseded run's dead letters.
    """
    deeper_run_key = "archive-walk:streamflow-archive:2019-01-01"
    rows = stub_rows()
    rows["job_lane_state"].append(
        {
            "lane": STREAMFLOW_LANE_NAME,
            "run_key": deeper_run_key,
            "work_item_status": DEAD_LETTER_WORK_ITEM_STATE,
            "window_count": 5,
            "oldest_shard_key": "streamflow-archive:2019-01-01..2019-01-31",
            "newest_shard_key": "streamflow-archive:2019-06-01..2019-07-01",
        }
    )
    session = StubSession(rows)

    report = await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    gauges = next(stream for stream in report.streams if stream.stream == "water-gauges")
    assert [lane_state.run_key for lane_state in gauges.lanes] == [deeper_run_key, STREAMFLOW_RUN_KEY]
    assert [lane_state.total_windows for lane_state in gauges.lanes] == [5, 42]
    assert [lane_state.dead_letter for lane_state in gauges.lanes] == [5, 2]
    # The deepest floor either run declared is what the stream owes data from.
    assert gauges.completeness.expected_first_day == day("2019-01-01")


async def test_a_day_series_that_reaches_its_row_cap_is_refused_rather_than_silently_truncated() -> None:
    # A truncated series would invent a tail gap that is not in the warehouse, which is the exact class of
    # defect this report exists to find.
    rows = stub_rows()
    session = StubSession(rows)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(validation, "MAX_OBSERVED_DAY_ROWS", 2)
        with pytest.raises(ObservedDayScanTooLargeError, match="feature_observed_days returned more than 2"):
            await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]


async def test_a_column_in_a_shape_the_report_did_not_declare_is_a_typed_refusal_not_a_coerced_guess() -> None:
    rows = stub_rows()
    rows["server_day"] = [{"server_day": "2026-08-08"}]
    session = StubSession(rows)

    with pytest.raises(ValidationRowError, match="must be a date"):
        await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]


async def test_a_day_column_carrying_a_time_is_refused_so_a_day_can_never_silently_drift() -> None:
    rows = stub_rows()
    rows["server_day"] = [{"server_day": datetime(2026, 8, 8, 13, 30, tzinfo=UTC)}]
    session = StubSession(rows)

    with pytest.raises(ValidationRowError, match="must be a date"):
        await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]


async def test_an_empty_filter_returning_sql_null_is_read_as_zero_rather_than_refused() -> None:
    rows = stub_rows()
    rows["drought_area_validity_counts"] = [
        {"total_rows": 0, "null_geom": None, "undated_day": None, "future_day": None, "outside_bbox": None}
    ]
    session = StubSession(rows)

    report = await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    drought = next(stream for stream in report.streams if stream.stream == "drought_areas")
    assert all(finding.count == 0 for finding in drought.validity)


async def test_every_statement_the_report_issues_is_read_only() -> None:
    session = StubSession(stub_rows())

    await build_validation_report(session, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    forbidden = ("insert ", "update ", "delete ", "truncate ", "create ", "drop ", "alter ")
    for statement in (
        validation._SERVER_DAY,
        validation._FEATURE_OBSERVED_DAYS,
        validation._FEATURE_VALIDITY_COUNTS,
        validation._FEATURE_DUPLICATE_IDENTITIES,
        validation._DROUGHT_AREA_OBSERVED_DAYS,
        validation._DROUGHT_AREA_VALIDITY_COUNTS,
        validation._HISTORICAL_OBSERVED_DAYS,
        validation._HISTORICAL_VALIDITY_COUNTS,
        validation._JOB_LANE_STATE,
    ):
        text_body = str(statement).lower()
        assert not any(keyword in text_body for keyword in forbidden)
        # Schema-qualified: db/base.py sets no search_path, so an unqualified table resolves to nothing.
        # `server_day` reads no table at all, which is why the check is on the statements that do.
        if " from " in text_body:
            assert "geo." in text_body or "agri." in text_body


def test_the_duplicate_identity_finding_carries_the_group_count_beside_the_excess_row_count() -> None:
    raw = StreamObservations(
        total_rows=10,
        check_counts=MappingProxyType({DUPLICATE_IDENTITY_CHECK: 3}),
        duplicate_identity_groups=2,
    )

    finding = next(
        entry
        for entry in build_validity_findings(TIME_SERIES, raw, bbox="-125,42,-111,49")
        if entry.check == DUPLICATE_IDENTITY_CHECK
    )

    assert finding.count == 3
    assert finding.to_summary()["detail"] == {"duplicate_identity_groups": 2}
