"""The in-house 5-field UTC cron grammar (spec S9): parse and field expansion, no next-fire clock.

See `foundation/lane_config/AGENTS.md` "Cron" for why the parser lives here and the next-fire clock
lives in `execution/cron_schedule.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, NamedTuple


class CronSyntaxError(ValueError):
    """A cron string outside the supported 5-field numeric grammar."""


class _FieldBounds(NamedTuple):
    name: str
    lowest: int
    highest: int


#: minute, hour, day-of-month, month, day-of-week (0-7, both 0 and 7 are Sunday).
_FIELD_BOUNDS: Final[tuple[_FieldBounds, ...]] = (
    _FieldBounds("minute", 0, 59),
    _FieldBounds("hour", 0, 23),
    _FieldBounds("day-of-month", 1, 31),
    _FieldBounds("month", 1, 12),
    _FieldBounds("day-of-week", 0, 7),
)
_SUNDAY_ALIAS: Final = 7
_FIELD_COUNT: Final = len(_FIELD_BOUNDS)


@dataclass(frozen=True, slots=True)
class CronExpression:
    """One parsed cron string, every field expanded to the set of values it fires on (UTC)."""

    text: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]

    @property
    def max_fires_per_utc_day(self) -> int:
        """How many times this expression fires on any UTC day it fires at all."""
        return len(self.minutes) * len(self.hours)


def _parse_integer(token: str, bounds: _FieldBounds, text: str) -> int:
    if not token.isdigit():
        raise CronSyntaxError(f"cron {text!r}: {bounds.name} value {token!r} is not a non-negative integer")
    value = int(token)
    if not bounds.lowest <= value <= bounds.highest:
        raise CronSyntaxError(f"cron {text!r}: {bounds.name} value {value} is outside {bounds.lowest}-{bounds.highest}")
    return value


def _expand_item(item: str, bounds: _FieldBounds, text: str) -> set[int]:
    range_part, slash, step_part = item.partition("/")
    step = 1
    if slash:
        step = _parse_integer(step_part, _FieldBounds(f"{bounds.name} step", 1, bounds.highest), text)
    if range_part == "*":
        start, stop = bounds.lowest, bounds.highest
    elif "-" in range_part:
        low_token, _, high_token = range_part.partition("-")
        start = _parse_integer(low_token, bounds, text)
        stop = _parse_integer(high_token, bounds, text)
        if start > stop:
            raise CronSyntaxError(f"cron {text!r}: {bounds.name} range {range_part!r} runs backwards")
    else:
        start = _parse_integer(range_part, bounds, text)
        # `a/n` is vixie cron's "from a to the field's end, every n"; a bare `a` is the one value.
        stop = bounds.highest if slash else start
    return set(range(start, stop + 1, step))


def _expand_field(field: str, bounds: _FieldBounds, text: str) -> frozenset[int]:
    values: set[int] = set()
    for item in field.split(","):
        if not item:
            raise CronSyntaxError(f"cron {text!r}: {bounds.name} field {field!r} has an empty list item")
        values |= _expand_item(item, bounds, text)
    return frozenset(values)


def parse_cron(text: str) -> CronExpression:
    """Parse a 5-field numeric UTC cron string; raise `CronSyntaxError` on anything else.

    Supports `*`, `a`, `a-b`, `*/n`, `a/n`, `a-b/n` and comma lists. Names (`MON`, `JAN`), macros
    (`@hourly`), `?`, `L`, `W` and `#` are refused rather than half-supported.
    """
    fields = text.split()
    if len(fields) != _FIELD_COUNT:
        raise CronSyntaxError(f"cron {text!r} has {len(fields)} fields; exactly {_FIELD_COUNT} are required")
    expanded = [_expand_field(field, bounds, text) for field, bounds in zip(fields, _FIELD_BOUNDS, strict=True)]
    days_of_week = frozenset(0 if day == _SUNDAY_ALIAS else day for day in expanded[4])
    return CronExpression(
        text=text,
        minutes=expanded[0],
        hours=expanded[1],
        days_of_month=expanded[2],
        months=expanded[3],
        days_of_week=days_of_week,
    )


__all__ = ["CronExpression", "CronSyntaxError", "parse_cron"]
