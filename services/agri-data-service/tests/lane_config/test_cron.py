"""The 5-field UTC cron grammar (spec S9): what each real lane cadence expands to, and what is refused."""

from __future__ import annotations

import pytest

from agri_data_service.foundation.lane_config import CronSyntaxError, parse_cron


@pytest.mark.parametrize(
    ("text", "hours", "minutes", "fires_per_day"),
    [
        pytest.param("50 * * * *", frozenset(range(24)), frozenset({50}), 24, id="legacy-soil-hourly"),
        pytest.param("50 */6 * * *", frozenset({0, 6, 12, 18}), frozenset({50}), 4, id="soil-after-g1"),
        pytest.param("20 3 * * *", frozenset({3}), frozenset({20}), 1, id="daily-gap-fill"),
        pytest.param("0,30 8-9 * * *", frozenset({8, 9}), frozenset({0, 30}), 4, id="list-and-range"),
        pytest.param("15 2/8 * * *", frozenset({2, 10, 18}), frozenset({15}), 3, id="start-and-step"),
        pytest.param("55 8 * * 2", frozenset({8}), frozenset({55}), 1, id="weekly-release-window"),
    ],
)
def test_a_cadence_expands_to_the_utc_fires_it_names(
    text: str, hours: frozenset[int], minutes: frozenset[int], fires_per_day: int
) -> None:
    """`max_fires_per_utc_day` is what decides whether a settled weighted lane must probe (S19)."""
    expression = parse_cron(text)

    assert (expression.hours, expression.minutes) == (hours, minutes)
    assert expression.max_fires_per_utc_day == fires_per_day


def test_sunday_is_both_zero_and_seven() -> None:
    assert parse_cron("0 4 * * 7").days_of_week == parse_cron("0 4 * * 0").days_of_week == frozenset({0})


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("50 * * *", id="four-fields"),
        pytest.param("60 * * * *", id="minute-out-of-range"),
        pytest.param("0 5-3 * * *", id="backwards-range"),
        pytest.param("*/0 * * * *", id="zero-step"),
        pytest.param("0 0 * * MON", id="day-name"),
        pytest.param("@hourly", id="macro"),
        pytest.param("0,,5 * * * *", id="empty-list-item"),
    ],
)
def test_anything_outside_the_numeric_grammar_is_refused(text: str) -> None:
    """A lane cannot ship a cron the executor would read differently from its author."""
    with pytest.raises(CronSyntaxError):
        parse_cron(text)
