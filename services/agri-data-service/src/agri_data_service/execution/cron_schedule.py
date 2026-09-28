"""The next-fire clock over the in-house 5-field UTC cron grammar (spec S9, D6); config lanes only.

The grammar and its parser are `foundation/lane_config/cron.py::parse_cron`; this module adds only the
clock, so the loader and the executor can never disagree about what a cron string means. See
`execution/AGENTS.md` "Cron schedule".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.lane_config import CronExpression, parse_cron

if TYPE_CHECKING:
    from collections.abc import Iterator

#: How many days a search walks before declaring that an expression never fires: eight years and two
#: days covers a 29-February schedule across the skipped century leap day (2100).
SEARCH_HORIZON_DAYS: Final = 8 * 366 + 2
_DAY_OF_MONTH_FIELD: Final = 2
_DAY_OF_WEEK_FIELD: Final = 4
_UNRESTRICTED_PREFIX: Final = "*"
_DAYS_PER_WEEK: Final = 7
_ONE_MINUTE: Final = timedelta(minutes=1)


class CronNeverFiresError(ValueError):
    """A grammatical cron that names no real UTC minute, such as `0 0 31 2 *`."""


@lru_cache(maxsize=256)
def cron_expression(text: str) -> CronExpression:
    """Parse `text` once per process; the executor asks on every tick."""
    return parse_cron(text)


def _unrestricted(expression: CronExpression, field_index: int) -> bool:
    return expression.text.split()[field_index].startswith(_UNRESTRICTED_PREFIX)


def fires_on_day(expression: CronExpression, day: date) -> bool:
    """Vixie cron's day rule: when both day fields are restricted either may match, otherwise both must.

    A day field is unrestricted when its text starts with `*` (so `*/2` counts as unrestricted), exactly
    as vixie cron decides it.
    """
    if day.month not in expression.months:
        return False
    day_of_month_matches = day.day in expression.days_of_month
    day_of_week_matches = day.isoweekday() % _DAYS_PER_WEEK in expression.days_of_week
    either_field_unrestricted = _unrestricted(expression, _DAY_OF_MONTH_FIELD) or _unrestricted(
        expression, _DAY_OF_WEEK_FIELD
    )
    if either_field_unrestricted:
        return day_of_month_matches and day_of_week_matches
    return day_of_month_matches or day_of_week_matches


def fires_at(expression: CronExpression, minute: datetime) -> bool:
    """Whether `expression` fires at this UTC minute (seconds are ignored)."""
    utc = _utc_minute(minute)
    return utc.hour in expression.hours and utc.minute in expression.minutes and fires_on_day(expression, utc.date())


def _utc_minute(instant: datetime) -> datetime:
    if instant.utcoffset() is None:
        raise ValueError("the cron clock needs a timezone-aware instant")
    return instant.astimezone(UTC).replace(second=0, microsecond=0)


def _day_fires(expression: CronExpression, day: date, *, descending: bool) -> Iterator[datetime]:
    hours = sorted(expression.hours, reverse=descending)
    minutes = sorted(expression.minutes, reverse=descending)
    for hour in hours:
        for minute in minutes:
            yield datetime.combine(day, time(hour, minute), tzinfo=UTC)


def latest_fire_at_or_before(expression: CronExpression, instant: datetime) -> datetime:
    """The newest UTC minute at or before `instant` on which `expression` fires: S9's due bucket."""
    ceiling = _utc_minute(instant)
    for offset in range(SEARCH_HORIZON_DAYS + 1):
        day = ceiling.date() - timedelta(days=offset)
        if not fires_on_day(expression, day):
            continue
        for fire in _day_fires(expression, day, descending=True):
            if fire <= ceiling:
                return fire
    raise CronNeverFiresError(f"cron {expression.text!r} fires on no UTC minute at or before {ceiling.isoformat()}")


def next_fire_after(expression: CronExpression, instant: datetime) -> datetime:
    """The first UTC minute strictly after `instant` on which `expression` fires."""
    floor = _utc_minute(instant) + _ONE_MINUTE
    for offset in range(SEARCH_HORIZON_DAYS + 1):
        day = floor.date() + timedelta(days=offset)
        if not fires_on_day(expression, day):
            continue
        for fire in _day_fires(expression, day, descending=False):
            if fire >= floor:
                return fire
    raise CronNeverFiresError(f"cron {expression.text!r} fires on no UTC minute after {instant.isoformat()}")


def fires_between(expression: CronExpression, start: datetime, end: datetime) -> tuple[datetime, ...]:
    """Every fire in the half-open window `(start, end]`, oldest first."""
    fires: list[datetime] = []
    cursor = start
    while True:
        fire = next_fire_after(expression, cursor)
        if fire > _utc_minute(end):
            return tuple(fires)
        fires.append(fire)
        cursor = fire


__all__ = [
    "SEARCH_HORIZON_DAYS",
    "CronNeverFiresError",
    "cron_expression",
    "fires_at",
    "fires_between",
    "fires_on_day",
    "latest_fire_at_or_before",
    "next_fire_after",
]
