"""Per-unit retries (spec §4.3 step 4): exponential on 5xx, the 429 series, then `deferred_quota`; units independent."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agri_data_service.pipeline.runner.contract import (
    ProviderConfigurationError,
    SourceThrottledError,
    SourceUnavailableError,
)
from agri_data_service.pipeline.runner.cooldown import ProviderCooldowns
from agri_data_service.pipeline.runner.fetch import THROTTLE_SERIES_SECONDS, FetchRetryPolicy, UnitFetcher
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.runner.fakes import NOW, ManualClock, ScriptedUpstream, grid_lane
from tests.runner.fixtures.grid_refuse import GridRefuseStrategy, GridWorld

DAY = date(2026, 9, 10)
HOST = "archive-api.open-meteo.com"
STRATEGY = GridRefuseStrategy(span_days=1, chunk_size=1)
#: No jitter, so the waits are exact.
POLICY = FetchRetryPolicy(server_error_attempts=3, server_error_base_seconds=2.0, server_error_max_seconds=30.0)


def _units() -> list:
    return STRATEGY.plan_requests([DAY], grid_lane(), None)  # type: ignore[arg-type]


def _fetcher(upstream: ScriptedUpstream, clock: ManualClock, *, deadline: float = 10_000.0) -> UnitFetcher:
    return UnitFetcher(strategy=STRATEGY, client=upstream, clock=clock, deadline=deadline, policy=POLICY)


def _failing_cell(cell: str, error: Exception, world: GridWorld):  # noqa: ANN202 - a ScriptedUpstream answer
    def answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:
        if parameters["cells"] == cell:
            raise error
        return world.answer(endpoint, parameters, probe)

    return answer


async def test_one_failed_unit_never_discards_its_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A unit that keeps failing 5xx ends `upstream_unavailable` after its attempts; the other three are answered."""
    monkeypatch.setattr("agri_data_service.pipeline.runner.fetch.random.random", lambda: 0.5)
    world = GridWorld()
    world.publish([DAY])
    upstream = ScriptedUpstream(_failing_cell("c2", SourceUnavailableError("status 503"), world))
    clock = ManualClock()

    outcomes = await _fetcher(upstream, clock).fetch_all(_units(), concurrency=2)

    failed = {unit: outcome for unit, outcome in outcomes.items() if outcome.response is None}
    assert [outcome.reason for outcome in failed.values()] == ["upstream_unavailable"]
    assert next(iter(failed.values())).attempts == POLICY.server_error_attempts
    assert sum(outcome.response is not None for outcome in outcomes.values()) == len(outcomes) - 1
    assert clock.slept == [2.0, 4.0]


async def test_a_429_walks_the_series_then_defers_quota_and_opens_the_circuit() -> None:
    """20/40/80/160 s, then `deferred_quota`; every unit not yet sent is deferred without a request."""
    upstream = ScriptedUpstream(_failing_cell("c1", SourceThrottledError("status 429"), GridWorld()))
    clock = ManualClock()

    outcomes = await _fetcher(upstream, clock).fetch_all(_units(), concurrency=1)

    assert clock.slept == list(THROTTLE_SERIES_SECONDS)
    assert {outcome.reason for outcome in outcomes.values()} == {"deferred_quota"}
    assert {send.parameters["cells"] for send in upstream.sends} == {"c1"}


async def test_a_retry_after_inside_its_cap_is_waited_and_one_past_it_opens_the_circuit_for_later_turns() -> None:
    """SOFT-8 in the runner's ladder: 30 s asked on the 20 s step waits 30; 5 s on the 40 s step waits 40 (never
    shorter); 400 s on the 80 s step (cap 160) is not waited at all: the circuit opens and the wait is persisted."""
    waits = (30.0, 5.0, 400.0)
    asked = iter(waits)

    def answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:  # noqa: ARG001 - the answer shape
        raise SourceThrottledError("status 429", retry_after_seconds=next(asked))

    clock = ManualClock()
    upstream = ScriptedUpstream(answer)
    cooldowns = ProviderCooldowns(MemoryAvailabilityStorage())
    fetcher = UnitFetcher(
        strategy=STRATEGY,
        client=upstream,
        clock=clock,
        deadline=10_000.0,
        policy=POLICY,
        backoff_host=HOST,
        cooldowns=cooldowns,
    )

    outcomes = await fetcher.fetch_all(_units(), concurrency=1)

    assert clock.slept == [30.0, 40.0]
    assert {outcome.reason for outcome in outcomes.values()} == {"deferred_quota"}
    assert len(upstream.sends) == len(waits)
    held = NOW + timedelta(seconds=waits[-1])
    assert cooldowns.throttled_until(HOST, now=NOW) == held
    assert cooldowns.throttled_until(HOST, now=held) is None


async def test_a_retry_that_would_pass_the_deadline_is_not_slept() -> None:
    """The ladder stops at the turn's deadline: the unit ends typed instead of sleeping past it."""
    upstream = ScriptedUpstream(_failing_cell("c1", SourceUnavailableError("status 502"), GridWorld()))
    clock = ManualClock()

    outcome = await _fetcher(upstream, clock, deadline=1.0).fetch_unit(_units()[0])

    assert outcome.reason == "upstream_unavailable"
    assert clock.slept == []


async def test_a_rejected_key_is_a_configuration_error_for_the_whole_turn() -> None:
    """401/403 or an empty required key is never one unit's failure: it propagates (exit 78)."""
    upstream = ScriptedUpstream(_failing_cell("c1", ProviderConfigurationError("key rejected"), GridWorld()))

    with pytest.raises(ProviderConfigurationError):
        await _fetcher(upstream, ManualClock()).fetch_all(_units(), concurrency=1)
