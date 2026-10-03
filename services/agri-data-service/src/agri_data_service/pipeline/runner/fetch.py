"""Fetch every request unit with independent retries: exponential on 5xx and timeout, the 429 series, then typed.

One failed unit never discards its siblings. Once a unit exhausts the 429 series, or the provider
states a Retry-After past the series cap or the turn's deadline, the quota circuit opens and every
unit not yet sent is `deferred_quota` without a request; a stated wait is persisted for later turns
(`cooldown.py`). See `pipeline/runner/AGENTS.md` "One retry ladder".
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.observability.redaction import describe_error
from agri_data_service.ingest.http import (
    HTTP_FORBIDDEN,
    HTTP_SERVER_ERROR_MINIMUM,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_UNAUTHORIZED,
    UpstreamHttpError,
    UpstreamTimeoutError,
    UpstreamTransportError,
    record_backoff_seconds,
)
from agri_data_service.ingest.open_meteo import OpenMeteoRateLimitError
from agri_data_service.ingest.upstream_retry import clamped_retry_after
from agri_data_service.pipeline.runner.contract import (
    ProviderConfigurationError,
    SourceThrottledError,
    SourceUnavailableError,
    TurnBudgetExhaustedError,
)
from agri_data_service.pipeline.runner.cooldown import MAX_COOLDOWN_SECONDS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from agri_data_service.pipeline.runner.checkpoints import TurnCheckpoints
    from agri_data_service.pipeline.runner.clock import TurnClock
    from agri_data_service.pipeline.runner.contract import (
        IngestStrategy,
        ProviderClient,
        SourceRequest,
        SourceResponse,
        UnwrittenReason,
    )
    from agri_data_service.pipeline.runner.cooldown import ProviderCooldowns

#: Spec §4.3 step 4: the 429 series, then `deferred_quota`.
THROTTLE_SERIES_SECONDS: Final = (20.0, 40.0, 80.0, 160.0)
#: Attempts on a 5xx, timeout or transport failure before `upstream_unavailable`.
SERVER_ERROR_ATTEMPTS: Final = 3
SERVER_ERROR_BASE_SECONDS: Final = 2.0
SERVER_ERROR_MAX_SECONDS: Final = 30.0
#: SOFT-8: a stated Retry-After is waited up to this multiple of the series step; past it, the circuit opens.
RETRY_AFTER_CEILING_FACTOR: Final = 2.0


@dataclass(frozen=True, slots=True)
class FetchRetryPolicy:
    """The unit ladder: server-error attempts on a doubling capped delay, and the 429 series."""

    server_error_attempts: int = SERVER_ERROR_ATTEMPTS
    server_error_base_seconds: float = SERVER_ERROR_BASE_SECONDS
    server_error_max_seconds: float = SERVER_ERROR_MAX_SECONDS
    throttle_series_seconds: tuple[float, ...] = THROTTLE_SERIES_SECONDS

    def server_error_delay(self, attempt: int) -> float:
        """Exponential backoff with a 0.5-1.5x jitter for the `attempt`-th failure (1-based)."""
        delay = min(self.server_error_base_seconds * float(2 ** (attempt - 1)), self.server_error_max_seconds)
        return delay * (0.5 + random.random())


DEFAULT_FETCH_RETRY_POLICY: Final = FetchRetryPolicy()


@dataclass(frozen=True, slots=True)
class UnitOutcome:
    """One unit's end: its answer, or the typed reason every day it covers stays unwritten."""

    request: SourceRequest
    response: SourceResponse | None
    reason: UnwrittenReason | None = None
    detail: str | None = None
    attempts: int = 0


@dataclass(slots=True)
class FetchTally:
    """What the fan-out did, for the report."""

    units_fetched: int = 0
    units_failed: int = 0
    units_upstream_unavailable: int = 0
    retry_backoff_seconds: float = 0.0
    quota_circuit_open: bool = False
    #: Why the circuit is open, when a stated wait (this turn's or a persisted one) opened it.
    quota_circuit_detail: str | None = None
    #: The provider's stated "do not send before" instant, when one holds the circuit open.
    throttled_until: datetime | None = None
    detail: list[str] = field(default_factory=list)

    def hold_until(self, until: datetime, detail: str) -> None:
        """Open the quota circuit until `until` (the later instant wins); nothing more is sent this turn."""
        self.quota_circuit_open = True
        if self.throttled_until is None or until > self.throttled_until:
            self.throttled_until = until
            self.quota_circuit_detail = detail


def classify_fetch_error(error: BaseException) -> str:  # noqa: PLR0911 - one return per error class
    """Classify a fetch error: `throttled`, `server`, `budget`, `config` or `strategy`."""
    if isinstance(error, TurnBudgetExhaustedError):
        return "budget"
    if isinstance(error, ProviderConfigurationError):
        return "config"
    if isinstance(error, (SourceThrottledError, OpenMeteoRateLimitError)):
        return "throttled"
    if isinstance(error, (SourceUnavailableError, UpstreamTimeoutError, UpstreamTransportError)):
        return "server"
    if isinstance(error, UpstreamHttpError):
        if error.status == HTTP_TOO_MANY_REQUESTS:
            return "throttled"
        if error.status >= HTTP_SERVER_ERROR_MINIMUM:
            return "server"
        if error.status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
            return "config"
    return "strategy"


def _retry_after(error: BaseException, fallback: float) -> float:
    """A server's Retry-After clamped into the series step it replaces (never shorter, never past 2x); SOFT-8."""
    clamped = clamped_retry_after(error, floor=fallback, ceiling=fallback * RETRY_AFTER_CEILING_FACTOR)
    return fallback if clamped is None else clamped


def stated_retry_after(error: BaseException) -> float | None:
    """The server's own Retry-After in seconds, unclamped, or `None` when it named none."""
    stated = getattr(error, "retry_after_seconds", None)
    if isinstance(stated, bool) or not isinstance(stated, (int, float)) or stated < 0:
        return None
    return float(stated)


def exceeds_ladder(stated: float, *, step: int, policy: FetchRetryPolicy, now: float, deadline: float) -> bool:
    """A stated wait the ladder never sleeps: after the series, past 2x its `step`-th wait, or past the deadline."""
    series = policy.throttle_series_seconds
    return step >= len(series) or stated > RETRY_AFTER_CEILING_FACTOR * series[step] or now + stated >= deadline


async def hold_stated_wait(  # noqa: PLR0913 - the wait, and where it is held and persisted
    stated: float,
    *,
    tally: FetchTally,
    clock: TurnClock,
    host: str | None,
    cooldowns: ProviderCooldowns | None,
    persist: bool,
    run_id: str,
) -> str:
    """Open the circuit for the provider's stated wait and persist it; a failed persist never fails the send."""
    until = clock.now() + timedelta(seconds=min(stated, MAX_COOLDOWN_SECONDS))
    label = host or "the provider"
    detail = f"{label} asked for a {stated:.0f} s wait (Retry-After); nothing is sent before {until.isoformat()}"
    tally.hold_until(until, detail)
    if cooldowns is not None and persist and host is not None:
        try:
            await asyncio.to_thread(cooldowns.record, host, until, now=clock.now(), run_id=run_id)
        except Exception as error:  # a cooldown is advisory: persisting it never fails the send
            tally.detail.append(f"cooldown not persisted: {describe_error(error)}")
    return detail


@dataclass(slots=True)
class UnitFetcher:
    """Runs the ladder for each unit, sharing one quota circuit and one deadline across them."""

    strategy: IngestStrategy
    client: ProviderClient
    clock: TurnClock
    deadline: float
    policy: FetchRetryPolicy = DEFAULT_FETCH_RETRY_POLICY
    checkpoints: TurnCheckpoints | None = None
    backoff_host: str | None = None
    tally: FetchTally = field(default_factory=FetchTally)
    #: Where a stated wait is persisted for later turns; `None` (or a compare turn's `persist=False`) keeps it in-turn.
    cooldowns: ProviderCooldowns | None = None
    persist_cooldown: bool = True
    run_id: str = ""

    async def fetch_all(self, requests: Sequence[SourceRequest], *, concurrency: int) -> dict[str, UnitOutcome]:
        """Fetch every unit, at most `concurrency` at a time; a config error still propagates (exit 78)."""
        gate = asyncio.Semaphore(max(1, concurrency))

        async def one(request: SourceRequest) -> UnitOutcome:
            async with gate:
                return await self.fetch_unit(request)

        tasks = [asyncio.ensure_future(one(request)) for request in requests]
        try:
            outcomes = await asyncio.gather(*tasks)
        except BaseException:
            # A config error ends the turn: stop every sibling still sending before it propagates (exit 78).
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return {outcome.request.unit: outcome for outcome in outcomes}

    async def fetch_unit(self, request: SourceRequest) -> UnitOutcome:  # noqa: PLR0911, PLR0912 - one exit per ladder rung
        """One unit through the ladder; returns its answer or its typed unwritten reason."""
        server_failures = 0
        throttles = 0
        attempts = 0
        while True:
            if self.tally.quota_circuit_open:
                detail = self.tally.quota_circuit_detail or "the quota circuit opened on a sibling unit"
                return self._failed(request, "deferred_quota", detail, attempts)
            if self.clock.monotonic() >= self.deadline:
                return self._failed(request, "deferred_budget", "the turn's time budget ran out", attempts)
            attempts += 1
            try:
                response = await self.strategy.fetch(request, self.client)
            except Exception as error:
                kind = classify_fetch_error(error)
                if kind == "config":
                    raise
                if kind == "budget":
                    return self._failed(request, "deferred_budget", describe_error(error), attempts)
                if kind == "strategy":
                    return self._failed(request, "strategy_error", describe_error(error), attempts)
                if kind == "throttled":
                    series = self.policy.throttle_series_seconds
                    exhausted = throttles >= len(series)
                    stated = stated_retry_after(error)
                    if stated is not None and exceeds_ladder(
                        stated, step=throttles, policy=self.policy, now=self.clock.monotonic(), deadline=self.deadline
                    ):
                        detail = await hold_stated_wait(
                            stated,
                            tally=self.tally,
                            clock=self.clock,
                            host=self.backoff_host,
                            cooldowns=self.cooldowns,
                            persist=self.persist_cooldown,
                            run_id=self.run_id,
                        )
                        return self._failed(request, "deferred_quota", detail, attempts)
                    if exhausted:
                        self.tally.quota_circuit_open = True
                        return self._failed(request, "deferred_quota", describe_error(error), attempts)
                    delay = _retry_after(error, series[throttles])
                    throttles += 1
                else:
                    server_failures += 1
                    if server_failures >= self.policy.server_error_attempts:
                        self.tally.units_upstream_unavailable += 1
                        return self._failed(request, "upstream_unavailable", describe_error(error), attempts)
                    delay = self.policy.server_error_delay(server_failures)
                if self.clock.monotonic() + delay >= self.deadline:
                    reason: UnwrittenReason = "deferred_quota" if kind == "throttled" else "upstream_unavailable"
                    if reason == "upstream_unavailable":
                        self.tally.units_upstream_unavailable += 1
                    return self._failed(request, reason, "the next retry would pass the turn's deadline", attempts)
                await self._backoff(delay)
                continue
            self.tally.units_fetched += 1
            if self.checkpoints is not None:
                await self.checkpoints.retain(response)
            return UnitOutcome(request=request, response=response, attempts=attempts)

    async def _backoff(self, seconds: float) -> None:
        self.tally.retry_backoff_seconds += seconds
        if self.backoff_host is not None:
            record_backoff_seconds(self.backoff_host, seconds)
        await self.clock.sleep(seconds)

    def _failed(self, request: SourceRequest, reason: UnwrittenReason, detail: str, attempts: int) -> UnitOutcome:
        self.tally.units_failed += 1
        return UnitOutcome(request=request, response=None, reason=reason, detail=detail, attempts=attempts)


__all__ = [
    "DEFAULT_FETCH_RETRY_POLICY",
    "RETRY_AFTER_CEILING_FACTOR",
    "THROTTLE_SERIES_SECONDS",
    "FetchRetryPolicy",
    "FetchTally",
    "UnitFetcher",
    "UnitOutcome",
    "classify_fetch_error",
    "exceeds_ladder",
    "hold_stated_wait",
    "stated_retry_after",
]
