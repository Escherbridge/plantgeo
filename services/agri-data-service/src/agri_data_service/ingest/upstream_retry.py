"""One retry ladder for the ArcGIS-family upstreams: what is worth another attempt, how long to wait, when to stop."""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.ingest.http import (
    HTTP_SERVER_ERROR_MINIMUM,
    HTTP_TOO_MANY_REQUESTS,
    UpstreamHttpError,
    UpstreamPayloadError,
    record_backoff_seconds,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

logger = structlog.get_logger()

# ArcGIS reports throttling INSIDE an HTTP 200 body, so a status-only rule reads an outage as a
# successful empty fetch. Matched against an UpstreamPayloadError's message only.
BUSY_MESSAGE_PATTERN: Final = re.compile(r"too many requests|busy|try again", re.IGNORECASE)

# Measured 2026-08-10: two retries ~3s apart lost every hourly `plantgeo-cron-fire-perimeters` run
# through a sustained ArcGIS throttle. See ingest/AGENTS.md "upstream_retry.py" for the incident.
DEFAULT_MAX_ATTEMPTS: Final = 6
DEFAULT_BASE_DELAY_SECONDS: Final = 1.0
DEFAULT_MAX_DELAY_SECONDS: Final = 20.0
DEFAULT_WALL_CLOCK_CEILING_SECONDS: Final = 60.0

NO_RETRY_CONTEXT: Final[Mapping[str, object]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class RetryLadder:
    """The bounded backoff an upstream is retried under: attempts, a doubling capped delay, a wall-clock backstop."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS
    wall_clock_ceiling_seconds: float = DEFAULT_WALL_CLOCK_CEILING_SECONDS

    def delay_seconds(self, attempt_index: int) -> float:
        """Exponential backoff (doubling, capped) for one attempt, randomised by a 0.5-1.5x jitter."""
        delay = min(self.base_delay_seconds * float(2**attempt_index), self.max_delay_seconds)
        return delay * (0.5 + random.random())


DEFAULT_RETRY_LADDER: Final = RetryLadder()


@dataclass(frozen=True, slots=True)
class UpstreamRetryPolicy:
    """One upstream's retry contract: its operator-facing log event, its exhausted-loop message, and its ladder.

    `probe_attempts` is a per-policy opt-in (design SOF2-07, §3.2B point 8): `None` (every existing
    policy) means a probe turn retries exactly as a normal turn does. A source that names a smaller
    budget for its GL-6 single-attempt probe turns (USGS's `usgs_nwis.py::USGS_STREAMFLOW_RETRY` sets
    it to 1) sets this field instead of writing a second policy or a second loop.
    """

    event: str
    exhausted_message: str
    ladder: RetryLadder = DEFAULT_RETRY_LADDER
    probe_attempts: int | None = None

    def __post_init__(self) -> None:
        """Refuse a `probe_attempts` that could never make a single attempt (design SOF2-07)."""
        if self.probe_attempts is not None and self.probe_attempts < 1:
            raise ValueError("probe_attempts must be None or >= 1")


def is_retryable_failure(error: Exception) -> bool:
    """True for an ArcGIS busy payload or a transient 429/5xx worth another attempt."""
    if isinstance(error, UpstreamPayloadError):
        return BUSY_MESSAGE_PATTERN.search(str(error)) is not None
    return isinstance(error, UpstreamHttpError) and (
        error.status == HTTP_TOO_MANY_REQUESTS or error.status >= HTTP_SERVER_ERROR_MINIMUM
    )


async def retry_upstream[ResultT](  # noqa: PLR0913 - attempt, policy, context, probe, host and the two test clocks are distinct
    attempt_once: Callable[[], Awaitable[ResultT]],
    policy: UpstreamRetryPolicy,
    *,
    context: Mapping[str, object] = NO_RETRY_CONTEXT,
    probe: bool = False,
    host: str | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> ResultT:
    """Repeat one bounded upstream attempt, retrying only a busy or transient failure inside the policy's budget.

    `probe=True` selects `policy.probe_attempts` in place of `policy.ladder.max_attempts` when the
    policy names one (design SOF2-07); every policy that leaves `probe_attempts` unset (the default)
    retries exactly as a normal turn does, probe or not. `host`, when a caller names one, counts this
    ladder's own sleep against `ingest/http.py`'s per-host `backoff_seconds` (design §2.1 lists this
    loop as one of that field's three origins, alongside `fetch_bounded`'s transport-retry sleep and
    the runner's own 429 series) -- a caller with no reasonable single host to blame (context spans
    several) simply leaves it unset and this loop counts nothing, exactly as it did before.
    """
    ladder = policy.ladder
    max_attempts = policy.probe_attempts if probe and policy.probe_attempts is not None else ladder.max_attempts
    started_at = monotonic()
    for attempt in range(max_attempts):
        try:
            return await attempt_once()
        except (UpstreamHttpError, UpstreamPayloadError) as error:
            elapsed_seconds = monotonic() - started_at
            out_of_attempts = attempt == max_attempts - 1
            out_of_time = elapsed_seconds >= ladder.wall_clock_ceiling_seconds
            if out_of_attempts or out_of_time or not is_retryable_failure(error):
                raise
            logger.info(
                policy.event,
                attempt=attempt + 1,
                **context,
                error=str(error),
                elapsed_seconds=round(elapsed_seconds, 2),
            )
            delay = ladder.delay_seconds(attempt)
            if host is not None:
                record_backoff_seconds(host, delay)
            await sleep(delay)
    raise UpstreamPayloadError(policy.exhausted_message)  # pragma: no cover - unreachable.
