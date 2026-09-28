"""Per-unit retries (spec §4.3 step 4): exponential on 5xx, the 429 series, then `deferred_quota`; units independent."""

from __future__ import annotations

from datetime import date

import pytest

from agri_data_service.pipeline.runner.contract import (
    ProviderConfigurationError,
    SourceThrottledError,
    SourceUnavailableError,
)
from agri_data_service.pipeline.runner.fetch import THROTTLE_SERIES_SECONDS, FetchRetryPolicy, UnitFetcher
from tests.runner.fakes import ManualClock, ScriptedUpstream, grid_lane
from tests.runner.fixtures.grid_refuse import GridRefuseStrategy, GridWorld

DAY = date(2026, 9, 10)
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


async def test_a_retry_after_replaces_the_series_step_but_never_shortens_or_more_than_doubles_it() -> None:
    """SOFT-8's clamp in the runner's ladder: 100 s asked on the 20 s step waits 40; 5 s on the 40 s step waits 40."""
    world = GridWorld()
    world.publish([DAY])
    asked = iter([100.0, 5.0])

    def answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:
        wait = next(asked, None)
        if wait is not None:
            raise SourceThrottledError("status 429", retry_after_seconds=wait)
        return world.answer(endpoint, parameters, probe)

    clock = ManualClock()
    outcome = await _fetcher(ScriptedUpstream(answer), clock).fetch_unit(_units()[0])

    assert outcome.response is not None
    assert clock.slept == [40.0, 40.0]


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
