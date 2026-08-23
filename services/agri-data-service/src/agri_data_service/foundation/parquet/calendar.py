"""The conformed calendar dimension: one row per civil day, derived from a date range and nothing else.

Layer L0: stdlib only. May NOT import any first-party module outside `foundation`, nor
SQLAlchemy, httpx, asyncpg, or click.

WHY THIS IS IN `foundation` AND NOT A LANE-SHAPED DATABASE READ. Every other stream exports rows
Postgres already holds; this one has no source system at all. `calendar` is PURE COMPUTATION from
a date range, so it belongs at the bottom of the lattice where that purity is enforced rather than
asserted -- there is no session to pass it and no query it could get wrong.

WHY IT EXISTS. Twelve lanes each re-derive "the 30 days after an as-of date", "which ISO week is
this", "is this a month end". Twelve derivations are twelve chances to disagree, and a forecast
horizon that resolves differently in two lanes is a silent join defect, not a visible error. One
dimension, keyed by the civil date, makes `as_of + 1..30` resolve identically everywhere.

WHAT IT IS NOT. It is not a business calendar. There are no fiscal years, no holidays, no trading
days -- nobody asked for them and inventing them would put unsourced policy in a dimension every
lane keys to. It is also NOT a `dim_time` that collapses the clocks: `docs/holonic-kimball-modeling.md`
is explicit that observed, valid, available and warehouse-recorded instants stay separate
role-named facts. Lanes KEY their own role-named date columns to this dimension; none of them
surrender the distinction between which clock a date came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

CALENDAR_STREAM: Final = "calendar"

CALENDAR_GRAIN: Final[tuple[str, ...]] = ("calendar_day",)

# The forward reach a version must guarantee. 400 days covers a 30-day horizon issued from any
# as-of date up to a year out, with room for a lane that starts issuing before anyone extends this.
CALENDAR_REQUIRED_FORWARD_DAYS: Final = 400

# The forward reach a version actually WRITES. Deliberately double the requirement: a version that
# covered exactly the requirement would be stale the day after it was written, and this dimension
# would churn a full re-generation every single day -- the very schedule-driven behaviour the
# static-lookup nature exists to remove. At 800 the lane regenerates roughly once every 400 days.
CALENDAR_VERSION_FORWARD_DAYS: Final = 800

# ~110 years of days. A span past this is a caller error (a floor parsed as year 1000, say), not a
# calendar anybody wants; refusing is cheaper than materialising 40,000 rows nobody reads.
MAX_CALENDAR_DAYS: Final = 40_000

MONTHS_PER_QUARTER: Final = 3


class CalendarError(ValueError):
    """Raised when a requested calendar span runs backwards or exceeds the day budget."""


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """One civil day, fully decomposed. Every field is a function of `calendar_day` alone."""

    calendar_day: date
    year: int
    quarter: int
    month: int
    day_of_month: int
    day_of_year: int
    iso_year: int
    iso_week: int
    iso_day_of_week: int
    is_month_start: bool
    is_month_end: bool


def calendar_day_for(day: date) -> CalendarDay:
    """Decompose one civil day. Pure, total, and independent of locale, timezone and clock."""
    iso_year, iso_week, iso_day_of_week = day.isocalendar()
    return CalendarDay(
        calendar_day=day,
        year=day.year,
        quarter=(day.month - 1) // MONTHS_PER_QUARTER + 1,
        month=day.month,
        day_of_month=day.day,
        day_of_year=day.timetuple().tm_yday,
        iso_year=iso_year,
        iso_week=iso_week,
        # ISO 8601: Monday is 1, Sunday is 7. Deliberately NOT `date.weekday()`'s 0-6, so a reader
        # never has to remember which convention this column follows.
        iso_day_of_week=iso_day_of_week,
        is_month_start=day.day == 1,
        is_month_end=(day + timedelta(days=1)).month != day.month,
    )


def calendar_days(first_day: date, last_day: date) -> tuple[CalendarDay, ...]:
    """Every day in `[first_day, last_day]`, chronologically. Refuses a backwards or absurd span."""
    if last_day < first_day:
        raise CalendarError(f"calendar span {first_day} to {last_day} runs backwards")
    span = (last_day - first_day).days + 1
    if span > MAX_CALENDAR_DAYS:
        raise CalendarError(f"calendar span of {span} days exceeds the {MAX_CALENDAR_DAYS}-day budget")
    return tuple(calendar_day_for(first_day + timedelta(days=offset)) for offset in range(span))


def calendar_version_span(version_day: date, *, floor: date) -> tuple[date, date]:
    """Return the `[first, last]` civil days the version stamped `version_day` covers."""
    last_day = version_day + timedelta(days=CALENDAR_VERSION_FORWARD_DAYS)
    if last_day < floor:
        raise CalendarError(
            f"a calendar version stamped {version_day} would end at {last_day}, before its floor {floor}"
        )
    return floor, last_day


def calendar_version_covers(version_day: date, *, today: date) -> bool:
    """True when the version stamped `version_day` still reaches `today + CALENDAR_REQUIRED_FORWARD_DAYS`."""
    covered_through = version_day + timedelta(days=CALENDAR_VERSION_FORWARD_DAYS)
    required_through = today + timedelta(days=CALENDAR_REQUIRED_FORWARD_DAYS)
    return covered_through >= required_through


def required_calendar_version_day(*, today: date, newest_version_day: date | None) -> date:
    """Return the version day this dimension must carry: the newest held one, or today if it fell short.

    This is the `calendar` lane's SOURCE WATERMARK, and it answers the same question every other
    static lane's watermark answers -- "what version must exist?" -- from the clock rather than from
    a table. Returning the newest held version when coverage is still sufficient is what makes the
    generic "a partition dated at or after the watermark means current" rule resolve to `current`
    without this lane needing a special case in the driver.
    """
    if newest_version_day is None:
        return today
    if calendar_version_covers(newest_version_day, today=today):
        # Clamped: a version stamped in the future still covers, but a watermark after today would
        # (correctly) be refused as a clock disagreement by `resolve_static_lane`.
        return min(newest_version_day, today)
    return today
