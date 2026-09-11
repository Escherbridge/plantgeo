"""Provider pacing, durable pauses and publication reporting without real upstream or storage I/O."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from agri_data_service.pipeline.direct.climate import forward, source
from agri_data_service.pipeline.direct.climate.cooldown import (
    NASA_POWER_COOLDOWN_CAS_ATTEMPTS,
    NASA_POWER_COOLDOWN_KEY,
    NASA_POWER_COOLDOWN_MAX_BYTES,
    ClimateCooldownError,
    NasaPowerCooldown,
)
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.direct.climate.conftest import FETCHED_AT, cell_day_response, filled_cache, product_for
from tests.direct.climate.test_forward_command import PLANE_STREAM, TODAY, SessionDouble, bounded_config
from tests.parquet.test_availability_index import MemoryAvailabilityStorage
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport, NasaPowerSupportCell

DAY = date(2026, 8, 20)
PAUSE_UNTIL = FETCHED_AT + timedelta(hours=2)
REQUEST_PAIR = 2


@pytest.mark.asyncio
async def test_pacing_is_shared_across_days_and_stops_before_an_unaffordable_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def advance(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds
        await real_sleep(0)

    monkeypatch.setattr(source, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(source.asyncio, "sleep", advance)
    cache = source.ClimateSourceCache(request_budget=3)
    starts: list[float] = []

    async def start(day: date) -> None:
        await cache.start_request(day=day, deadline=12.0)
        starts.append(clock[0])

    await asyncio.gather(start(DAY), start(DAY + timedelta(days=1)))
    assert starts == [10.0, 10.5]
    assert sleeps == [0.5]
    assert cache.requests_spent == REQUEST_PAIR
    with pytest.raises(source.ClimateTimeBudgetExhaustedError):
        await cache.start_request(day=DAY, deadline=10.75)
    assert cache.requests_spent == REQUEST_PAIR
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_a_refusal_during_pacing_prevents_the_waiting_start(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [10.0]
    cache = source.ClimateSourceCache(request_budget=2, next_request_start=10.5)

    async def refuse(seconds: float) -> None:
        clock[0] += seconds
        cache.deferred_refusal = source.ClimateProviderDeferredError("provider stopped the turn")

    monkeypatch.setattr(source, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(source.asyncio, "sleep", refuse)
    with pytest.raises(source.ClimateProviderDeferredError):
        await cache.start_request(day=DAY, deadline=12.0)
    assert cache.requests_spent == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("120", FETCHED_AT + timedelta(minutes=2)),
        ("Wed, 26 Aug 2026 08:00:00 GMT", PAUSE_UNTIL),
        ("Wed, 26 Aug 2026 05:00:00 GMT", FETCHED_AT),
        ("garbled", None),
        ("-1", None),
        ("9" * 128, None),
        (None, None),
    ],
)
def test_retry_after_seconds_and_dates_never_invent_a_valid_pause(value: str | None, expected: datetime | None) -> None:
    assert source.retry_after_instant(value, now=FETCHED_AT) == expected


@pytest.mark.asyncio
async def test_a_later_hourly_turn_obeys_the_persisted_429_pause(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryAvailabilityStorage()
    calls: list[httpx.Request] = []

    def throttled(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "7200"})

    monkeypatch.setattr(
        source, "upstream_client", lambda _bounds: httpx.AsyncClient(transport=httpx.MockTransport(throttled))
    )
    first = source.ClimateSourceCache(request_budget=len(support.cells), cooldown=NasaPowerCooldown(storage))
    with pytest.raises(source.ClimateProviderDeferredError):
        await source.fill_cell_day_cache(day=DAY, support=support, cache=first, now=FETCHED_AT, concurrency=1)
    assert NasaPowerCooldown(storage).read() == PAUSE_UNTIL
    assert first.deferred_refusal is not None
    assert first.deferred_refusal.retry_hint_enforced_across_turns is True

    resumed = source.ClimateSourceCache(request_budget=len(support.cells), cooldown=NasaPowerCooldown(storage))
    with pytest.raises(source.ClimateProviderDeferredError):
        await source.fill_cell_day_cache(
            day=DAY, support=support, cache=resumed, now=FETCHED_AT + timedelta(hours=1), concurrency=1
        )
    assert len(calls) == 1
    assert resumed.requests_spent == 0
    assert resumed.deferred_refusal is not None
    assert resumed.deferred_refusal.retry_not_before == PAUSE_UNTIL


@pytest.mark.asyncio
async def test_expired_pause_allows_access_and_reads_only_once_per_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = MemoryAvailabilityStorage()
    cooldown = NasaPowerCooldown(storage)
    cooldown.advance(PAUSE_UNTIL)
    read = Mock(wraps=storage.read)
    monkeypatch.setattr(storage, "read", read)
    cache = source.ClimateSourceCache(request_budget=2, cooldown=cooldown)
    await cache.require_provider_access(now=PAUSE_UNTIL)
    await cache.require_provider_access(now=PAUSE_UNTIL + timedelta(minutes=1))
    read.assert_called_once_with(NASA_POWER_COOLDOWN_KEY, max_bytes=NASA_POWER_COOLDOWN_MAX_BYTES)
    assert NASA_POWER_COOLDOWN_KEY in storage.objects
    assert cache.deferred_refusal is None


@pytest.mark.asyncio
async def test_cached_complete_cells_remain_usable_during_a_pause(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryAvailabilityStorage()
    NasaPowerCooldown(storage).advance(PAUSE_UNTIL)
    cache = filled_cache(support, day=DAY)
    cache.cooldown = NasaPowerCooldown(storage)
    fetch = AsyncMock(side_effect=AssertionError("cached days do not request the provider"))
    monkeypatch.setattr(source, "_fetch_cell_day", fetch)
    result = await source.fetch_climate_day(
        product_for(PLANE_STREAM), day=DAY, support=support, cache=cache, now=FETCHED_AT
    )
    assert len(result.values) == len(support.cells)
    assert result.is_governed_absence is False
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["malformed", "read_failed", "oversized"])
async def test_unverifiable_cooldown_never_permits_new_source_requests(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    storage = MemoryAvailabilityStorage()
    if fault == "read_failed":
        monkeypatch.setattr(storage, "read", Mock(side_effect=OSError("storage unavailable")))
    else:
        storage.seed(
            NASA_POWER_COOLDOWN_KEY, b"?" if fault == "malformed" else b"x" * (NASA_POWER_COOLDOWN_MAX_BYTES + 1)
        )
    cache = source.ClimateSourceCache(request_budget=len(support.cells), cooldown=NasaPowerCooldown(storage))
    fetch = AsyncMock(side_effect=AssertionError("unverified constraint must stop provider I/O"))
    monkeypatch.setattr(source, "_fetch_cell_day", fetch)
    for _attempt in range(REQUEST_PAIR):
        with pytest.raises(ClimateCooldownError):
            await source.fill_cell_day_cache(day=DAY, support=support, cache=cache, now=FETCHED_AT)
    assert cache.requests_spent == 0
    fetch.assert_not_awaited()


def test_cooldown_cas_preserves_a_later_concurrent_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = MemoryAvailabilityStorage()
    later = PAUSE_UNTIL + timedelta(hours=1)
    competing = MemoryAvailabilityStorage()
    NasaPowerCooldown(competing).advance(later)
    first = True

    def lose_race(_key: str, _payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        nonlocal first
        assert expected_etag is None
        assert content_type == "application/json"
        assert first
        first = False
        storage.seed(NASA_POWER_COOLDOWN_KEY, competing.objects[NASA_POWER_COOLDOWN_KEY].payload)
        return False

    monkeypatch.setattr(storage, "compare_and_swap", lose_race)
    assert NasaPowerCooldown(storage).advance(PAUSE_UNTIL) == later
    assert NasaPowerCooldown(storage).read() == later


@pytest.mark.asyncio
async def test_concurrent_retry_hints_report_the_effective_persisted_maximum() -> None:
    storage = MemoryAvailabilityStorage()
    cache = source.ClimateSourceCache(request_budget=2, cooldown=NasaPowerCooldown(storage))
    first = source.ClimateProviderDeferredError("first refusal", outcome="provider_access_denied")
    await cache.retain_provider_refusal(first)
    later = PAUSE_UNTIL + timedelta(hours=1)
    await asyncio.gather(
        cache.retain_provider_refusal(source.ClimateProviderDeferredError("429", retry_not_before=PAUSE_UNTIL)),
        cache.retain_provider_refusal(source.ClimateProviderDeferredError("429", retry_not_before=later)),
    )
    assert cache.deferred_refusal is first
    assert cache.deferred_refusal.outcome == "provider_access_denied"
    assert cache.deferred_refusal.retry_not_before == later
    assert cache.deferred_refusal.retry_hint_enforced_across_turns is True
    assert NasaPowerCooldown(storage).read() == later


@pytest.mark.asyncio
async def test_nasa_redirects_cannot_bypass_the_request_counter(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.test/data"})

    monkeypatch.setattr(
        source,
        "upstream_client",
        lambda _bounds: httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True),
    )
    cache = filled_cache(support, day=DAY, omit_cell_keys=[support.cells[0].cell_key])
    with pytest.raises(source.ClimateSourceUnsettledError, match="302"):
        await source.fill_cell_day_cache(day=DAY, support=support, cache=cache, now=FETCHED_AT, concurrency=1)
    assert len(requests) == 1
    assert cache.requests_spent == 1


@pytest.mark.asyncio
async def test_failed_cooldown_persistence_stops_the_queue_and_is_an_operational_error(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryAvailabilityStorage()
    cas = Mock(return_value=False)
    monkeypatch.setattr(storage, "compare_and_swap", cas)
    cache = source.ClimateSourceCache(request_budget=len(support.cells), cooldown=NasaPowerCooldown(storage))
    fetch = AsyncMock(side_effect=source.ClimateProviderDeferredError("429", retry_not_before=PAUSE_UNTIL))
    monkeypatch.setattr(source, "_fetch_cell_day", fetch)
    with pytest.raises(ClimateCooldownError, match="CAS contention"):
        await source.fill_cell_day_cache(day=DAY, support=support, cache=cache, now=FETCHED_AT, concurrency=1)
    assert cas.call_count == NASA_POWER_COOLDOWN_CAS_ATTEMPTS
    assert fetch.await_count == 1
    assert cache.requests_spent == 1
    assert not cache.responses


@pytest.mark.asyncio
async def test_a_pending_positive_response_is_kept_after_another_capture_is_denied(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = filled_cache(support, day=DAY, omit_cell_keys=[cell.cell_key for cell in support.cells[:3]])
    released = asyncio.Event()
    positive = cell_day_response(support.cells[0], day=DAY)
    calls: list[str] = []

    async def fetch(
        _client: httpx.AsyncClient, cell: NasaPowerSupportCell, *, day: date, now: datetime | None
    ) -> source.ClimateCellDayResponse:
        assert day == DAY
        assert now == FETCHED_AT
        calls.append(cell.cell_key)
        if cell == support.cells[0]:
            await released.wait()
            return positive
        assert cell == support.cells[1], "the third queued capture must not start"
        released.set()
        raise source.ClimateProviderDeferredError("403", outcome="provider_access_denied")

    monkeypatch.setattr(source, "_fetch_cell_day", fetch)
    monkeypatch.setattr(source, "NASA_POWER_REQUEST_START_INTERVAL_SECONDS", 0.0)
    with pytest.raises(source.ClimateProviderDeferredError):
        await source.fill_cell_day_cache(day=DAY, support=support, cache=cache, now=FETCHED_AT, concurrency=3)
    assert cache.requests_spent == REQUEST_PAIR
    assert len(calls) == REQUEST_PAIR
    assert cache.responses[(positive.cell.cell_key, DAY)] == positive


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        (["written", "provider_rate_limited"], "incomplete", 1, 1),
        (["provider_access_denied", "provider_access_denied"], "incomplete", 0, 2),
        (["written", "written"], "published", 2, 0),
        (["absent", "absent"], "complete", 0, 0),
    ],
)
async def test_product_summary_counts_publications_instead_of_backlog(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[list[str], str, int, int],
) -> None:
    outcomes, expected, written, deferred = case
    publish = AsyncMock(side_effect=[{"outcome": outcome} for outcome in outcomes])
    monkeypatch.setattr(forward, "_publish_day_with_retries", publish)
    result = await forward._publish_product(
        SessionDouble(),
        ObjectStore(RecordingBackend()),
        product_for(PLANE_STREAM),
        support=support,
        cache=source.ClimateSourceCache(request_budget=len(support.cells) * 2),
        today=TODAY,
        run_id="summary",
        config=bounded_config(max_days=2),
        deadline=time.monotonic() + 60,
        availability_storage=None,
        availability=AvailabilityExtensionTally(),
    )
    assert result["outcome"] == expected
    assert result["written_days"] == written
    assert result["deferred_days"] == deferred


@pytest.mark.asyncio
async def test_cooldown_storage_failure_survives_gap_fill_without_publication_retry(
    support: NasaPowerSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = AsyncMock(side_effect=ClimateCooldownError("failed to save pause"))
    monkeypatch.setattr(forward, "fetch_climate_day", fetch)
    backend = RecordingBackend()
    with pytest.raises(ClimateCooldownError):
        await forward._publish_locked_day(
            SessionDouble(),
            ObjectStore(backend),
            product_for(PLANE_STREAM),
            DAY,
            support=support,
            cache=source.ClimateSourceCache(request_budget=len(support.cells)),
            today=TODAY,
            run_id="cooldown-failed",
            config=bounded_config(),
            deadline=time.monotonic() + 60,
            availability_storage=None,
            availability=AvailabilityExtensionTally(),
            mirrored_past=None,
        )
    assert fetch.await_count == 1
    assert not backend.objects


@pytest.mark.asyncio
async def test_partial_run_keeps_the_ordinary_success_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forward, "parse_args", lambda _argv: bounded_config())
    report: Mapping[str, object] = {"status": "partial", "outcome": "incomplete", "incomplete_products": 1}
    monkeypatch.setattr(forward, "run_climate_forward", AsyncMock(return_value=report))
    assert await forward.main([]) == 0
