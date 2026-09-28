"""The turn's clock: wall time for days and receipts, a monotonic clock for deadlines, and the one sleep."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol


class TurnClock(Protocol):
    """Everything time-shaped a turn reads; tests pass a clock whose `sleep` only advances it."""

    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """The production clock: UTC wall time, `time.monotonic`, `asyncio.sleep`."""

    def now(self) -> datetime:
        """The current UTC instant."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Seconds on a clock that never goes backwards."""
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Wait `seconds`, cooperatively."""
        await asyncio.sleep(max(0.0, seconds))


def utc_today(clock: TurnClock) -> date:
    """Today's UTC date on `clock`."""
    return clock.now().astimezone(UTC).date()


def utc_yesterday(clock: TurnClock) -> date:
    """Yesterday's UTC date on `clock`: the newest day a provisional lane may write (O1)."""
    return utc_today(clock) - timedelta(days=1)


__all__ = ["SystemClock", "TurnClock", "utc_today", "utc_yesterday"]
