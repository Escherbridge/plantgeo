"""The shared retry ladder: what is retried, the doubling capped backoff, and the two ways the loop is bounded."""

# ruff: noqa: PLR2004

from __future__ import annotations

import pytest

from agri_data_service.foundation.observability import usage
from agri_data_service.ingest import upstream_retry
from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError, UpstreamTimeoutError
from agri_data_service.ingest.upstream_retry import (
    DEFAULT_RETRY_LADDER,
    RetryLadder,
    UpstreamRetryPolicy,
    is_retryable_failure,
    retry_upstream,
)

TEST_POLICY = UpstreamRetryPolicy(event="test_upstream_retry", exhausted_message="test retry loop ended")

MIN_JITTER = 0.5
MAX_JITTER = 1.5


async def _no_sleep(_delay: float) -> None:
    """Skip the backoff wait so a retry test runs at full speed."""


def test_only_a_busy_or_transient_upstream_is_retried() -> None:
    # Consolidated from the byte-identical copies that lived in wfigs.py and evacuation_zones.py;
    # both producers' busy wordings are pinned here so neither can drift out from under the other.
    assert is_retryable_failure(UpstreamHttpError(429))
    assert is_retryable_failure(UpstreamHttpError(503))
    assert is_retryable_failure(UpstreamPayloadError("WFIGS API error: service is busy"))
    assert is_retryable_failure(UpstreamPayloadError("Oregon OEM evacuation API error: service is busy"))
    assert is_retryable_failure(UpstreamPayloadError("WFIGS API error: Too many requests."))
    assert not is_retryable_failure(UpstreamHttpError(400))
    assert not is_retryable_failure(UpstreamHttpError(404))
    assert not is_retryable_failure(UpstreamPayloadError("unexpected feature collection shape"))
    # A byte-cap refusal is a real failure of a real page, never a throttle to wait out.
    assert not is_retryable_failure(UpstreamPayloadError("upstream response exceeded the byte limit"))
    assert not is_retryable_failure(UpstreamTimeoutError("upstream request timed out"))


def test_the_ladder_doubles_and_then_caps_inside_the_jitter_band() -> None:
    ladder = RetryLadder(base_delay_seconds=1.0, max_delay_seconds=20.0)
    for attempt_index, undelayed in enumerate((1.0, 2.0, 4.0, 8.0, 16.0, 20.0, 20.0)):
        delay = ladder.delay_seconds(attempt_index)
        assert undelayed * MIN_JITTER <= delay <= undelayed * MAX_JITTER


def test_the_shipped_default_is_the_post_incident_budget() -> None:
    # Widened 2026-08-10; a "simplification" back to 3 attempts / a fixed (1.0, 2.0) tuple is the
    # exact shape that lost every hourly fire-perimeters run. See ingest/AGENTS.md.
    assert DEFAULT_RETRY_LADDER.max_attempts == 6
    assert DEFAULT_RETRY_LADDER.base_delay_seconds == 1.0
    assert DEFAULT_RETRY_LADDER.max_delay_seconds == 20.0
    assert DEFAULT_RETRY_LADDER.wall_clock_ceiling_seconds == 60.0


async def test_a_retryable_failure_is_survived_and_the_result_returned() -> None:
    attempts: list[int] = []

    async def attempt_once() -> str:
        attempts.append(1)
        if len(attempts) < DEFAULT_RETRY_LADDER.max_attempts:
            raise UpstreamPayloadError("Too many requests.")
        return "page"

    assert await retry_upstream(attempt_once, TEST_POLICY, context={"offset": 0}, sleep=_no_sleep) == "page"
    assert len(attempts) == DEFAULT_RETRY_LADDER.max_attempts


async def test_a_sustained_retryable_failure_still_raises_once_the_budget_is_spent() -> None:
    attempts: list[int] = []

    async def attempt_once() -> str:
        attempts.append(1)
        raise UpstreamPayloadError("Too many requests.")

    with pytest.raises(UpstreamPayloadError, match="Too many requests"):
        await retry_upstream(attempt_once, TEST_POLICY, sleep=_no_sleep)
    assert len(attempts) == DEFAULT_RETRY_LADDER.max_attempts


async def test_a_non_retryable_failure_is_raised_on_the_first_attempt() -> None:
    attempts: list[int] = []

    async def attempt_once() -> str:
        attempts.append(1)
        raise UpstreamHttpError(400)

    with pytest.raises(UpstreamHttpError):
        await retry_upstream(attempt_once, TEST_POLICY, sleep=_no_sleep)
    assert len(attempts) == 1


async def test_the_wall_clock_ceiling_bounds_the_loop_independently_of_the_attempt_budget() -> None:
    clock_seconds = 0.0

    async def jump_clock(_delay: float) -> None:
        nonlocal clock_seconds
        clock_seconds += DEFAULT_RETRY_LADDER.wall_clock_ceiling_seconds

    attempts: list[int] = []

    async def attempt_once() -> str:
        attempts.append(1)
        raise UpstreamPayloadError("Too many requests.")

    with pytest.raises(UpstreamPayloadError, match="Too many requests"):
        await retry_upstream(attempt_once, TEST_POLICY, sleep=jump_clock, monotonic=lambda: clock_seconds)
    assert 1 <= len(attempts) < DEFAULT_RETRY_LADDER.max_attempts


async def test_a_source_may_narrow_the_ladder_without_re_inventing_it() -> None:
    policy = UpstreamRetryPolicy(event="narrow", exhausted_message="narrow", ladder=RetryLadder(max_attempts=2))
    attempts: list[int] = []

    async def attempt_once() -> str:
        attempts.append(1)
        raise UpstreamHttpError(503)

    with pytest.raises(UpstreamHttpError):
        await retry_upstream(attempt_once, policy, sleep=_no_sleep)
    assert len(attempts) == 2


# --- probe_attempts (design SOF2-07, plan 0W.2 GL-2): a per-policy opt-in ---------------------------


async def test_evacuation_zones_policy_keeps_six_attempts_under_probe() -> None:
    """A policy that never sets `probe_attempts` (evacuation-zones'/WFIGS' shape) is unaffected by `probe=True`."""
    policy = UpstreamRetryPolicy(
        event="evacuation_zones_upstream_retry",
        exhausted_message="evacuation zones retry loop ended without a response",
    )
    attempts: list[int] = []

    async def attempt_once() -> str:
        attempts.append(1)
        raise UpstreamPayloadError("Too many requests.")

    with pytest.raises(UpstreamPayloadError):
        await retry_upstream(attempt_once, policy, probe=True, sleep=_no_sleep)
    assert len(attempts) == DEFAULT_RETRY_LADDER.max_attempts


async def test_usgs_policy_uses_one_attempt_under_probe() -> None:
    """A policy with `probe_attempts=1` (USGS's) gives up after one attempt only when `probe=True`."""
    policy = UpstreamRetryPolicy(
        event="usgs_nwis_upstream_retry",
        exhausted_message="USGS NWIS tile retry loop ended without a response",
        probe_attempts=1,
    )
    attempts: list[int] = []

    async def attempt_once() -> str:
        attempts.append(1)
        raise UpstreamHttpError(503)

    with pytest.raises(UpstreamHttpError):
        await retry_upstream(attempt_once, policy, probe=True, sleep=_no_sleep)
    assert len(attempts) == 1

    # Off probe, the same policy still uses its ladder's ordinary budget.
    attempts.clear()
    with pytest.raises(UpstreamHttpError):
        await retry_upstream(attempt_once, policy, sleep=_no_sleep)
    assert len(attempts) == DEFAULT_RETRY_LADDER.max_attempts


# --- backoff accounting (design §2.1's third `backoff_seconds` origin, plan 0W.2 GL-2) --------------


async def test_a_named_hosts_ladder_sleeps_are_counted_as_backoff_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two survived retries against a named host add exactly their two delays to that host's counter."""
    monkeypatch.setattr(usage, "_host_counters", {})
    monkeypatch.setattr(upstream_retry.random, "random", lambda: 0.5)  # pins the 0.5-1.5x jitter at 1.0x
    policy = UpstreamRetryPolicy(
        event="test_upstream_retry_backoff",
        exhausted_message="test retry loop ended without a response",
        ladder=RetryLadder(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=20.0),
    )
    calls = {"n": 0}

    async def attempt_once() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise UpstreamHttpError(503)
        return "ok"

    result = await retry_upstream(attempt_once, policy, sleep=_no_sleep, host="example.test")

    assert result == "ok"
    # delay_seconds(0) = 1.0 * 2**0 * 1.0 = 1.0; delay_seconds(1) = 1.0 * 2**1 * 1.0 = 2.0.
    assert usage._host_counters["example.test"]["backoff_seconds"] == pytest.approx(3.0)


async def test_a_ladder_with_no_named_host_counts_nothing() -> None:
    """A caller that never names a host (no reasonable single host to blame) counts no backoff, as before."""
    policy = UpstreamRetryPolicy(
        event="test_upstream_retry_backoff",
        exhausted_message="test retry loop ended without a response",
        ladder=RetryLadder(max_attempts=2),
    )

    async def attempt_once() -> str:
        raise UpstreamHttpError(503)

    with pytest.raises(UpstreamHttpError):
        await retry_upstream(attempt_once, policy, sleep=_no_sleep)
