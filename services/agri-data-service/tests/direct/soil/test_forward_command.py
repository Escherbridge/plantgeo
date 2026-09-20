"""The bounded turn: what `--product` selects, what the budget covers, and what the CLI refuses."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import AsyncMock

import pytest

from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.soil import forward
from agri_data_service.pipeline.direct.soil.forward import (
    SOIL_DEFAULT_TIME_BUDGET_SECONDS,
    SOIL_MAX_DAYS,
    SoilForwardConfig,
    SoilForwardConfigError,
    parse_args,
    settled_through,
)
from agri_data_service.pipeline.direct.soil.products import (
    ERA5_LAND_ARCHIVE_FRONTIER_LOOKBACK_DAYS,
    ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS,
    SOIL_PRODUCT_IDS,
    SOIL_SOURCE_PARAMETERS,
    products_for,
)
from agri_data_service.pipeline.direct.soil.source import (
    ERA5_LAND_CHUNK_CELL_COUNT,
    SoilProviderDeferredError,
    SoilSourceCache,
    soil_day_from_cache,
)
from agri_data_service.pipeline.direct.soil.support import ERA5_LAND_SUPPORT_CELL_COUNT
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.direct.soil.conftest import filled_cache, product_for
from tests.direct.soil.test_adapter import SessionDouble
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agri_data_service.pipeline.direct.soil.source import Era5LandChunk
    from agri_data_service.pipeline.direct.soil.support import Era5LandSupport

MOISTURE_STREAM_COUNT: Final = 3
TEMPERATURE_STREAM_COUNT: Final = 4
VPD_STREAM_COUNT: Final = 1
ALL_STREAM_COUNT: Final = 8
CHUNKS_PER_DAY: Final = 32


def config(**overrides: Any) -> SoilForwardConfig:
    """One valid turn, so a test states only the field it is about."""
    base = SoilForwardConfig(
        product_id="all",
        max_days=1,
        time_budget_seconds=SOIL_DEFAULT_TIME_BUDGET_SECONDS,
        retry_attempts=4,
        retry_base_seconds=5.0,
        retry_max_seconds=60.0,
        contention_timeout_seconds=300.0,
    )
    return replace(base, **overrides)


@pytest.mark.asyncio
async def test_provider_quota_passes_through_gap_fill_as_one_deferred_attempt(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caught adapter refusal must remain a deferral through the real gap-fill driver."""
    fetch = AsyncMock(side_effect=SoilProviderDeferredError("provider quota exceeded"))
    sleep = AsyncMock(side_effect=AssertionError("a deferred provider must not enter publication retry sleep"))
    monkeypatch.setattr(forward, "fetch_soil_day", fetch)
    monkeypatch.setattr(forward.asyncio, "sleep", sleep)
    backend = RecordingBackend()

    result = await forward._publish_locked_day(
        SessionDouble(),
        ObjectStore(backend),
        product_for("soil-field-moisture-7-28cm"),
        date(2026, 8, 20),
        support=support,
        chunks=chunks,
        cache=SoilSourceCache(request_budget=len(chunks)),
        today=date(2026, 9, 2),
        run_id="quota-run",
        config=config(),
        deadline=time.monotonic() + 60,
        availability_storage=None,
        availability=AvailabilityExtensionTally(),
        mirrored_past=None,
    )

    assert result["outcome"] == forward.SOIL_SOURCE_UNSETTLED_OUTCOME
    assert result["attempts"] == 1
    assert fetch.await_count == 1
    sleep.assert_not_awaited()
    assert not backend.objects


def test_each_browser_toggle_resolves_to_the_streams_that_serve_it() -> None:
    """`--product` names a toggle, never a stream slug: three toggles cover eight physical lanes."""
    assert len(products_for("moisture")) == MOISTURE_STREAM_COUNT
    assert len(products_for("temperature")) == TEMPERATURE_STREAM_COUNT
    assert len(products_for("vpd")) == VPD_STREAM_COUNT
    assert len(products_for("all")) == ALL_STREAM_COUNT
    assert {product.product_id for product in products_for("all")} == set(SOIL_PRODUCT_IDS)


def test_an_unknown_product_is_refused_by_name() -> None:
    """A typo must not silently select nothing and report an empty success."""
    with pytest.raises(ValueError, match="unknown soil product"):
        products_for("soil-moisture")


def test_the_request_budget_is_counted_in_chunks_and_not_in_cells() -> None:
    """One request carries fifty locations and every variable, so a day costs 32 requests, not 1,568."""
    assert config().request_budget == CHUNKS_PER_DAY * 5
    assert config(max_days=3).request_budget == CHUNKS_PER_DAY * 7
    assert CHUNKS_PER_DAY == -(-ERA5_LAND_SUPPORT_CELL_COUNT // ERA5_LAND_CHUNK_CELL_COUNT)


def test_the_candidate_edge_is_today_minus_the_documented_redistributor_delay() -> None:
    """The five-day edge is probed, while an all-null answer remains unsettled rather than absent."""
    product = products_for("vpd")[0]

    assert settled_through(product, today=date(2026, 9, 20)) == date(2026, 9, 15)
    assert product.publication_lag_days == ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product_id", "moisture-0-7cm"),
        ("max_days", 0),
        ("max_days", SOIL_MAX_DAYS + 1),
        ("retry_attempts", 0),
        ("time_budget_seconds", 0.0),
        ("time_budget_seconds", float("inf")),
        ("contention_timeout_seconds", 100_000.0),
    ],
)
def test_every_process_bound_knob_fails_closed_before_a_socket_opens(field: str, value: Any) -> None:
    """An unbounded turn is how a shadow lane becomes an unbounded quota spend against a public API."""
    with pytest.raises(SoilForwardConfigError):
        forward._validate_config(config(**{field: value}))


def test_a_retry_ceiling_below_its_base_is_refused() -> None:
    """A capped exponential whose cap is under its base would shrink the wait on every attempt."""
    with pytest.raises(SoilForwardConfigError, match="at least"):
        forward._validate_config(config(retry_base_seconds=60.0, retry_max_seconds=10.0))


def test_the_cli_defaults_to_every_product_and_one_day() -> None:
    """The executor passes no arguments, so these defaults are the turn that actually runs."""
    parsed = parse_args([])

    assert parsed.product_id == "all"
    assert parsed.max_days == 1
    assert parsed.time_budget_seconds == SOIL_DEFAULT_TIME_BUDGET_SECONDS
    assert parsed.request_budget == CHUNKS_PER_DAY * 5


def test_one_request_carries_every_variable_so_the_products_share_a_fetch() -> None:
    """Eight streams, one parameter each, and one sorted list the request builder will accept."""
    assert tuple(sorted(SOIL_SOURCE_PARAMETERS)) == SOIL_SOURCE_PARAMETERS
    assert len(SOIL_SOURCE_PARAMETERS) == ALL_STREAM_COUNT
    assert set(SOIL_SOURCE_PARAMETERS) == {product.source_parameter for product in products_for("all")}


# --- Absences are re-examined, and an all-null day needs the mirror proven past it ----------------


def _statuses(
    per_tier: dict[date, str],
    *,
    derived: str = "data",
) -> dict[Any, dict[date, Any]]:
    """Build the per-rung status map `_pending_days` reads, with one dial per BASE-rung day.

    A base day dialled `absent` seeds the WHOLE ladder absent: since the atomic absence ladder, a
    governed absence is written at all four rungs at once, so a base-only absence is a state the
    bucket cannot hold and `_pending_days` rightly refuses it as derived parts under an absence.
    """
    return {
        tier: (
            dict(per_tier)
            if tier == LANE_BASE_ZOOM_TIER
            else {day: ("absent" if status == "absent" else derived) for day, status in per_tier.items()}
        )
        for tier in forward.SOIL_DIRECT_ALL_TIERS
    }


def test_a_recent_absence_is_re_examined_so_the_retraction_is_reachable_at_all() -> None:
    """DO NOT DELETE. `_retract_disproven_absence` runs only on a day the walk SELECTS.

    The archive backfills a day it first answered null for. Skipping every `absent` day made that
    retraction unreachable, so an absence, once written, was permanent whatever the archive did next.
    """
    product = products_for("vpd")[0]
    newest = date(2026, 8, 20)
    days = {newest - timedelta(days=offset): "absent" for offset in range(3)}

    pending = forward._pending_days(product, _statuses(days))

    assert set(pending) == set(days), "every recent absence is owed a second look"


def test_an_absence_older_than_the_recheck_window_is_left_alone() -> None:
    """Bounded, or every turn re-fetches days that settled years ago and never reaches a real gap."""
    product = products_for("vpd")[0]
    newest = date(2026, 8, 20)
    stale = newest - timedelta(days=forward.SOIL_ABSENCE_RECHECK_DAYS)
    days = {stale: "absent", newest: "data"}

    pending = forward._pending_days(product, _statuses(days))

    assert pending == ()


def test_a_recheck_never_outranks_a_day_that_holds_no_data_at_all() -> None:
    """A turn publishes one day; spending it on an answered day while a real gap waits is a regression."""
    product = products_for("vpd")[0]
    newest = date(2026, 8, 20)
    gap = newest - timedelta(days=1)
    days = {gap: "missing", newest: "absent"}

    pending = forward._pending_days(product, _statuses(days))

    assert pending[0] == gap, "the real gap must be taken first"
    assert pending[-1] == newest


def test_the_mirrored_past_proof_is_the_next_published_day_or_nothing() -> None:
    """The proof is read out of the census listing the turn already paid for: no extra request."""
    newest = date(2026, 8, 20)
    older = newest - timedelta(days=2)
    statuses = _statuses({older: "absent", newest: "data"})

    assert forward._mirrored_past_day(statuses, older) == newest
    assert forward._mirrored_past_day(statuses, newest) is None, (
        "the newest owed day can never satisfy the proof, so the leading edge refuses"
    )


# --- An unsettled candidate edge is stepped past, so the available day beneath it publishes -----

FRONTIER = date(2026, 9, 15)
NEXT_OLDER = FRONTIER - timedelta(days=1)


@asynccontextmanager
async def always_granted(*_args: object, **_kwargs: object) -> AsyncIterator[bool]:
    """Grant the lane-day lock so these tests isolate the bounded publication walk."""
    yield True


async def frontier_turn(  # noqa: PLR0913 - staged source days and turn controls are independent dials
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    monkeypatch: pytest.MonkeyPatch,
    *,
    staged: dict[date, SoilSourceCache],
    cache: SoilSourceCache,
    provider_throttled: bool = False,
) -> tuple[dict[str, object], list[date], ObjectStore]:
    """Run VPD over an empty bucket while serving complete synthetic archive days."""
    fetched: list[date] = []

    async def fetch(product: Any, *, day: date, cache: SoilSourceCache, **_kwargs: object) -> Any:
        fetched.append(day)
        cache.requests_spent += len(chunks)
        if provider_throttled:
            cache.deferred_refusal = SoilProviderDeferredError("Open-Meteo answered 429")
            raise cache.deferred_refusal
        return soil_day_from_cache(product, day=day, support=support, chunks=chunks, cache=staged[day])

    monkeypatch.setattr(forward, "fetch_soil_day", fetch)
    monkeypatch.setattr(forward, "postgres_lane_day_lock", always_granted)
    monkeypatch.setattr(forward, "_retry_owed_availability", AsyncMock(return_value=0))
    store = ObjectStore(RecordingBackend())
    result = await forward._publish_product(
        SessionDouble(),
        store,
        products_for("vpd")[0],
        support=support,
        chunks=chunks,
        cache=cache,
        today=date(2026, 9, 20),
        run_id="frontier-run",
        config=config(product_id="vpd"),
        deadline=time.monotonic() + 60,
        availability_storage=None,
        availability=AvailabilityExtensionTally(),
    )
    return result, fetched, store


@pytest.mark.asyncio
async def test_september_15_null_steps_to_september_14_and_publishes_it(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented five-day candidate may lag by one day without starving the available day beneath it."""
    every_cell = [cell.cell_key for cell in support.cells]
    staged = {
        FRONTIER: filled_cache(support, chunks, day=FRONTIER, null_cell_keys=every_cell),
        NEXT_OLDER: filled_cache(support, chunks, day=NEXT_OLDER),
    }

    result, fetched, store = await frontier_turn(
        support,
        chunks,
        monkeypatch,
        staged=staged,
        cache=SoilSourceCache(request_budget=CHUNKS_PER_DAY * 2),
    )

    assert fetched == [FRONTIER, NEXT_OLDER]
    days = result["days"]
    assert isinstance(days, list)
    assert [(day["day"], day["outcome"]) for day in days] == [
        (FRONTIER.isoformat(), forward.SOIL_SOURCE_UNSETTLED_OUTCOME),
        (NEXT_OLDER.isoformat(), "written"),
    ]
    assert result["outcome"] == "published"
    assert result["source_unsettled_days"] == 1
    assert result["unsettled_frontier_days"] == [FRONTIER.isoformat()]
    assert all(
        status == "data" for status in forward._tier_status_day(store, products_for("vpd")[0], NEXT_OLDER).values()
    )


@pytest.mark.asyncio
async def test_four_null_frontier_days_reach_the_measured_nine_day_edge_and_publish(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented T-5 candidate can discover and publish the measured T-9 redistributor edge."""
    every_cell = [cell.cell_key for cell in support.cells]
    frontier_days = [FRONTIER - timedelta(days=offset) for offset in range(4)]
    available = FRONTIER - timedelta(days=4)
    staged = {
        **{day: filled_cache(support, chunks, day=day, null_cell_keys=every_cell) for day in frontier_days},
        available: filled_cache(support, chunks, day=available),
    }

    result, fetched, store = await frontier_turn(
        support,
        chunks,
        monkeypatch,
        staged=staged,
        cache=SoilSourceCache(request_budget=CHUNKS_PER_DAY * 5),
    )

    assert forward.SOIL_UNSETTLED_FRONTIER_SKIPS == ERA5_LAND_ARCHIVE_FRONTIER_LOOKBACK_DAYS
    assert fetched == [*frontier_days, available]
    assert result["outcome"] == "published"
    assert result["source_unsettled_days"] == ERA5_LAND_ARCHIVE_FRONTIER_LOOKBACK_DAYS
    assert result["unsettled_frontier_days"] == [day.isoformat() for day in frontier_days]
    assert all(
        status == "data" for status in forward._tier_status_day(store, products_for("vpd")[0], available).values()
    )


@pytest.mark.asyncio
async def test_the_frontier_lookback_stops_after_four_skips_and_one_unsettled_slot(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fifth all-null day consumes the ordinary slot; a sixth owed day is never requested."""
    every_cell = [cell.cell_key for cell in support.cells]
    attempted = [FRONTIER - timedelta(days=offset) for offset in range(5)]
    staged = {day: filled_cache(support, chunks, day=day, null_cell_keys=every_cell) for day in attempted}

    result, fetched, _store = await frontier_turn(
        support,
        chunks,
        monkeypatch,
        staged=staged,
        cache=SoilSourceCache(request_budget=CHUNKS_PER_DAY * 5),
    )

    assert fetched == attempted
    assert result["outcome"] == forward.SOIL_SOURCE_UNSETTLED_OUTCOME
    assert result["source_unsettled_days"] == ERA5_LAND_ARCHIVE_FRONTIER_LOOKBACK_DAYS + 1
    assert result["unsettled_frontier_days"] == [
        day.isoformat() for day in attempted[:ERA5_LAND_ARCHIVE_FRONTIER_LOOKBACK_DAYS]
    ]


@pytest.mark.asyncio
async def test_a_provider_deferral_is_not_treated_as_an_unsettled_frontier(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quota circuit stops the walk; it must not trigger another request for an older day."""
    result, fetched, _store = await frontier_turn(
        support,
        chunks,
        monkeypatch,
        staged={},
        cache=SoilSourceCache(request_budget=CHUNKS_PER_DAY * 2),
        provider_throttled=True,
    )

    assert fetched == [FRONTIER]
    assert result["outcome"] == forward.SOIL_SOURCE_UNSETTLED_OUTCOME
    assert result["source_unsettled_days"] == 1
    assert result["unsettled_frontier_days"] == []


@pytest.mark.parametrize(
    ("day_outcomes", "expected"),
    [
        (("source_unsettled", forward.SOIL_REQUEST_BUDGET_OUTCOME), forward.SOIL_REQUEST_BUDGET_OUTCOME),
        (("source_unsettled", forward.SOIL_TIME_BUDGET_OUTCOME), forward.SOIL_TIME_BUDGET_OUTCOME),
        (("source_unsettled", "source_unsettled"), forward.SOIL_SOURCE_UNSETTLED_OUTCOME),
        (("source_unsettled", "written"), "published"),
    ],
)
def test_the_product_outcome_reports_what_happened_after_the_frontier(
    day_outcomes: tuple[str, ...], expected: str
) -> None:
    """An unsettled probe cannot mask a write or the bound that stopped the next older day."""
    backlog = [FRONTIER - timedelta(days=offset) for offset in range(len(day_outcomes))]
    days = [{"outcome": outcome} for outcome in day_outcomes]

    assert forward._product_outcome(backlog, days) == expected
