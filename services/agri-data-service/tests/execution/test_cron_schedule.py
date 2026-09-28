"""The config-lane cron clock (spec S9, §4.4 "Cron"): due buckets, next fires and the vixie day rule, in UTC.

Pure functions over `foundation/lane_config/cron.py::parse_cron`; no database and no subprocess. The random
property cases are checked against a brute-force minute scan written independently of the clock.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Final

import pytest

from agri_data_service.execution.cron_schedule import (
    CronNeverFiresError,
    cron_expression,
    fires_between,
    fires_on_day,
    latest_fire_at_or_before,
    next_fire_after,
)
from agri_data_service.execution.lane_ids import SOIL_DIRECT_LANE_ID
from agri_data_service.execution.lane_scheduling import bucket_after, scheduled_bucket
from agri_data_service.execution.lane_specs import LANE_SPECS, ExecutorConfigurationError, LaneExecutionSpec

PROPERTY_SEED: Final = 20260928
PROPERTY_CASES: Final = 150
#: The brute-force oracle only scans crons that fire at least once a week.
ORACLE_SCAN_MINUTES: Final = 8 * 24 * 60


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _config_spec(cron: str) -> LaneExecutionSpec:
    return LaneExecutionSpec(
        lane_id="cron-probe",
        conflicts_with=(),
        work_class="incremental",
        migration_disposition="source-specific",
        cadence_seconds=None,
        phase_offset_seconds=0,
        schedule=cron,
        publication_lag_days=None,
        publication_cadence_days=None,
        publication_lag_source="test",
        selection_policy="test",
        catch_up_policy="coalesce_latest",
        command=("python", "-m", "agri_data_service.pipeline.runner", "--lane", "cron-probe", "--mode", "forward"),
        command_timeout_seconds=60,
        description="probe",
        executor="config",
        cron=cron,
        config_lane_id="cron-probe",
        config_mode="forward",
    )


# --- the due bucket and the next fire ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("cron", "now", "due", "following"),
    [
        pytest.param("15 * * * *", "2026-09-28T12:44:00", "2026-09-28T12:15:00", "2026-09-28T13:15:00", id="hourly"),
        pytest.param(
            "15 * * * *", "2026-09-28T12:15:59", "2026-09-28T12:15:00", "2026-09-28T13:15:00", id="seconds-ignored"
        ),
        pytest.param("20 3 * * *", "2026-09-28T03:19:00", "2026-09-27T03:20:00", "2026-09-28T03:20:00", id="daily"),
        pytest.param(
            "55 7 * * 2", "2026-09-28T12:00:00", "2026-09-22T07:55:00", "2026-09-29T07:55:00", id="weekly-tuesday"
        ),
        pytest.param(
            "0 0 1 * *", "2026-03-01T00:00:00", "2026-03-01T00:00:00", "2026-04-01T00:00:00", id="month-boundary"
        ),
        pytest.param(
            "59 23 31 12 *", "2027-01-01T00:00:00", "2026-12-31T23:59:00", "2027-12-31T23:59:00", id="year-boundary"
        ),
    ],
)
def test_the_due_bucket_is_the_latest_fire_at_or_before_now_and_the_next_is_strictly_after(
    cron: str, now: str, due: str, following: str
) -> None:
    """S9: due = the latest fire <= now; a replayed lane opens the fire after its last bucket."""
    spec = _config_spec(cron)

    assert scheduled_bucket(spec, _at(now)) == _at(due)
    assert bucket_after(spec, _at(due)) == _at(following)
    assert next_fire_after(cron_expression(cron), _at(due)) == _at(following)


def test_a_zoned_instant_reads_the_same_utc_fire() -> None:
    """The cron is UTC by contract (S9): Pacific daylight time is converted, never reinterpreted."""
    pacific_daylight = timezone(timedelta(hours=-7))
    instant = datetime(2026, 9, 28, 5, 44, tzinfo=pacific_daylight)  # 12:44 UTC

    assert latest_fire_at_or_before(cron_expression("15 * * * *"), instant) == _at("2026-09-28T12:15:00")


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        latest_fire_at_or_before(cron_expression("15 * * * *"), datetime(2026, 9, 28, 12, 44))  # noqa: DTZ001


def test_fires_between_is_the_half_open_window_oldest_first() -> None:
    fires = fires_between(cron_expression("50 */6 * * *"), _at("2026-09-28T00:50:00"), _at("2026-09-28T18:50:00"))

    assert fires == (_at("2026-09-28T06:50:00"), _at("2026-09-28T12:50:00"), _at("2026-09-28T18:50:00"))


# --- the legacy soil cadence and its schedule string agree (O6/FR-21) -------------------------------


def test_the_six_hourly_soil_schedule_string_names_the_buckets_its_cadence_opens() -> None:
    """Legacy soil runs on cadence and phase; its `"50 */6 * * *"` string must name the same four buckets."""
    soil = LANE_SPECS[SOIL_DIRECT_LANE_ID]
    assert soil.schedule is not None
    expression = cron_expression(soil.schedule)
    instant = _at("2026-09-27T00:00:00")
    for _ in range(4 * 24 * 3):  # three days in 15-minute steps
        assert latest_fire_at_or_before(expression, instant) == scheduled_bucket(soil, instant), instant
        instant += timedelta(minutes=15)


# --- the vixie day rule -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cron", "day", "fires"),
    [
        pytest.param("0 0 1 * 1", date(2026, 9, 28), True, id="both-restricted-monday-matches"),
        pytest.param("0 0 1 * 1", date(2026, 10, 1), True, id="both-restricted-first-matches"),
        pytest.param("0 0 1 * 1", date(2026, 9, 29), False, id="both-restricted-neither"),
        pytest.param("0 0 */2 * 1", date(2026, 9, 28), False, id="star-step-is-unrestricted-so-both-must-match"),
        pytest.param("0 0 */2 * 1", date(2026, 9, 21), True, id="odd-monday-matches-both"),
        pytest.param("0 0 * 10 *", date(2026, 9, 30), False, id="month-gates-first"),
    ],
)
def test_the_day_rule_is_vixie_crons(cron: str, day: date, fires: bool) -> None:
    """Either restricted day field may match; a field starting with `*` makes both required."""
    assert fires_on_day(cron_expression(cron), day) is fires


# --- expressions that never fire --------------------------------------------------------------------


def test_a_leap_day_schedule_crosses_the_skipped_century_leap_day() -> None:
    expression = cron_expression("0 0 29 2 *")

    assert latest_fire_at_or_before(expression, _at("2101-03-01T00:00:00")) == _at("2096-02-29T00:00:00")
    assert next_fire_after(expression, _at("2096-03-01T00:00:00")) == _at("2104-02-29T00:00:00")


def test_a_grammatical_cron_that_names_no_real_day_is_that_lane_s_configuration_error() -> None:
    """`0 0 31 2 *` parses but never fires; the planner turns it into one lane's error, never a hang."""
    with pytest.raises(CronNeverFiresError):
        next_fire_after(cron_expression("0 0 31 2 *"), _at("2026-09-28T00:00:00"))
    with pytest.raises(ExecutorConfigurationError, match="cron-probe"):
        scheduled_bucket(_config_spec("0 0 31 2 *"), _at("2026-09-28T00:00:00"))


# --- property: the clock agrees with a brute-force minute scan ---------------------------------------


def _random_field(generator: random.Random, lowest: int, highest: int) -> str:
    shape = generator.choice(("star", "value", "list", "range", "step"))
    if shape == "star":
        return "*"
    if shape == "value":
        return str(generator.randint(lowest, highest))
    if shape == "list":
        return ",".join(str(value) for value in sorted(generator.sample(range(lowest, highest + 1), 3)))
    start = generator.randint(lowest, highest)
    stop = generator.randint(start, highest)
    if shape == "range":
        return f"{start}-{stop}"
    return f"*/{generator.randint(1, max(1, (highest - lowest) // 2))}"


def _weekly_cron(generator: random.Random) -> str:
    """Minute, hour and day-of-week vary; day-of-month and month stay `*`, so the day rule is dow alone."""
    return " ".join(
        (_random_field(generator, 0, 59), _random_field(generator, 0, 23), "*", "*", _random_field(generator, 0, 6))
    )


def _oracle_next(text: str, instant: datetime) -> datetime:
    """The first minute strictly after `instant` whose fields match, found by scanning minute by minute."""
    expression = cron_expression(text)
    candidate = instant.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(ORACLE_SCAN_MINUTES):
        if (
            candidate.minute in expression.minutes
            and candidate.hour in expression.hours
            and candidate.isoweekday() % 7 in expression.days_of_week
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise AssertionError(f"{text!r} did not fire within the oracle's scan window")


def test_next_and_latest_fires_agree_with_a_brute_force_scan() -> None:
    generator = random.Random(PROPERTY_SEED)
    for _ in range(PROPERTY_CASES):
        text = _weekly_cron(generator)
        instant = _at("2026-01-01T00:00:00") + timedelta(minutes=generator.randint(0, 366 * 24 * 60))
        expression = cron_expression(text)

        following = next_fire_after(expression, instant)
        assert following == _oracle_next(text, instant), (text, instant)
        assert latest_fire_at_or_before(expression, following) == following, (text, instant)
        assert latest_fire_at_or_before(expression, following - timedelta(minutes=1)) <= instant, (text, instant)
