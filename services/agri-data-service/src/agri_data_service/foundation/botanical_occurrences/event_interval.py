"""Collecting-event dates as intervals: partial stays partial, and no day is ever invented."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

EventPrecision = Literal["day", "month", "year", "interval", "unknown"]

_YEAR_PATTERN: Final = re.compile(r"^(\d{4})$")
_YEAR_MONTH_PATTERN: Final = re.compile(r"^(\d{4})-(\d{1,2})$")
_FULL_DATE_PATTERN: Final = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")

#: The earliest year a collecting event is accepted for. Linnaean collecting predates it, but a
#: four-digit year below this in a modern DwC export is far more often a transcription artefact than
#: a real seventeenth-century specimen, and this lane would rather refuse to date a record than date
#: it wrongly. A collection that genuinely holds older material moves this with its own evidence.
MINIMUM_EVENT_YEAR: Final = 1700

#: An upper bound is not a cap on the future, it is a typo guard: a year past today cannot be a
#: collecting event that already happened.
MAXIMUM_EVENT_YEAR: Final = 2100


@dataclass(frozen=True, slots=True)
class EventInterval:
    """The span a collecting event is known to lie within, and how precisely that is known.

    `unknown` carries no dates at all. It is a distinct state from an interval that happens to be
    wide: "we do not know when" and "we know it was some time in 1913" are different claims, and
    only the second can answer an event-window filter.
    """

    start: date | None
    end: date | None
    precision: EventPrecision

    @property
    def known(self) -> bool:
        """True when this interval can be compared against a requested window at all."""
        return self.start is not None and self.end is not None

    def overlaps(self, window_start: date | None, window_end: date | None) -> bool:
        """Report interval OVERLAP against a requested window, never an invented exact-day match.

        An unbounded side of the window matches everything on that side. An unknown event interval
        overlaps nothing: a record whose date nobody knows cannot be evidence for a date range.
        """
        if self.start is None or self.end is None:
            return False
        if window_start is not None and self.end < window_start:
            return False
        return not (window_end is not None and self.start > window_end)


UNKNOWN_EVENT: Final = EventInterval(start=None, end=None, precision="unknown")


def _valid_year(year: int) -> bool:
    return MINIMUM_EVENT_YEAR <= year <= MAXIMUM_EVENT_YEAR


def _year_span(year: int) -> EventInterval | None:
    """A bare year spans its WHOLE year; it is never narrowed to January the first."""
    if not _valid_year(year):
        return None
    return EventInterval(date(year, 1, 1), date(year, 12, 31), "year")


def _month_span(year: int, month: int) -> EventInterval | None:
    """A year-month spans its whole month, whatever its length."""
    if not _valid_year(year) or not 1 <= month <= 12:  # noqa: PLR2004 - the twelve months of a year
        return None
    return EventInterval(date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]), "month")


def _day_span(year: int, month: int, day: int) -> EventInterval | None:
    try:
        exact = date(year, month, day)
    except ValueError:
        return None
    if not _valid_year(year):
        return None
    return EventInterval(exact, exact, "day")


def _parse_single(raw: str) -> EventInterval | None:
    """Parse one ISO date, year-month or year into its own span."""
    text = raw.strip()
    full = _FULL_DATE_PATTERN.match(text)
    if full:
        return _day_span(int(full.group(1)), int(full.group(2)), int(full.group(3)))
    year_month = _YEAR_MONTH_PATTERN.match(text)
    if year_month:
        return _month_span(int(year_month.group(1)), int(year_month.group(2)))
    year = _YEAR_PATTERN.match(text)
    if year:
        return _year_span(int(year.group(1)))
    return None


def parse_event_date(raw: str | None) -> EventInterval:
    """Parse a DwC `eventDate`, including an ISO 8601 `start/end` range, into one interval.

    A range keeps precision `interval` even when both ends are exact days, because the record does
    not say WHICH day inside it the event happened, and collapsing to the first would be an
    invention. A range whose ends are the same single day is an exact day and is reported as one.
    """
    if raw is None or not raw.strip():
        return UNKNOWN_EVENT
    text = raw.strip()
    if "/" in text:
        first, _, second = text.partition("/")
        left, right = _parse_single(first), _parse_single(second)
        if left is None or right is None or left.start is None or right.end is None:
            return UNKNOWN_EVENT
        if left.start > right.end:
            return UNKNOWN_EVENT
        if left.start == right.end:
            return EventInterval(left.start, right.end, "day")
        return EventInterval(left.start, right.end, "interval")
    parsed = _parse_single(text)
    return parsed if parsed is not None else UNKNOWN_EVENT


def _as_int(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def parse_event_parts(year: str | None, month: str | None, day: str | None) -> EventInterval:
    """Build an interval from the separate `year`/`month`/`day` terms, widening on every gap.

    A year with no month is a year-wide interval; a year and month with no day is a month-wide one.
    A month or day WITHOUT a year is unknown, not a date in some default year.
    """
    parsed_year, parsed_month, parsed_day = _as_int(year), _as_int(month), _as_int(day)
    if parsed_year is None:
        return UNKNOWN_EVENT
    if parsed_month is None:
        return _year_span(parsed_year) or UNKNOWN_EVENT
    if parsed_day is None:
        return _month_span(parsed_year, parsed_month) or UNKNOWN_EVENT
    return _day_span(parsed_year, parsed_month, parsed_day) or _month_span(parsed_year, parsed_month) or UNKNOWN_EVENT


def resolve_event_interval(
    event_date: str | None,
    *,
    year: str | None = None,
    month: str | None = None,
    day: str | None = None,
) -> EventInterval:
    """Resolve a record's event interval: `eventDate` first, the separate parts only as a fallback.

    The fallback runs only when `eventDate` yields nothing. A publisher that supplies both and
    disagrees with itself is believed on `eventDate`, which is the term its own schema treats as
    authoritative; the parts survive verbatim either way.
    """
    parsed = parse_event_date(event_date)
    if parsed.precision != "unknown":
        return parsed
    return parse_event_parts(year, month, day)


__all__ = [
    "MAXIMUM_EVENT_YEAR",
    "MINIMUM_EVENT_YEAR",
    "UNKNOWN_EVENT",
    "EventInterval",
    "EventPrecision",
    "parse_event_date",
    "parse_event_parts",
    "resolve_event_interval",
]
