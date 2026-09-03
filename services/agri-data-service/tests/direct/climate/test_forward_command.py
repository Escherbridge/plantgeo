"""Day selection, per-product lag, idempotence, the argument bounds and the turn deadline of one climate turn."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.climate import forward
from agri_data_service.pipeline.direct.climate.adapter import CLIMATE_DIRECT_KIND
from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_DISTINCT_PUBLICATION_CLOCKS,
    CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    CLIMATE_PRODUCT_IDS,
    CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
    products_for,
)
from agri_data_service.pipeline.direct.climate.source import ClimateSourceCache, ClimateTimeBudgetExhaustedError
from agri_data_service.pipeline.direct.climate.support import NASA_POWER_SUPPORT_CELL_COUNT
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.direct.climate.conftest import product_for
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport

TODAY = date(2026, 9, 2)
PLANE_STREAM = "climate-field-air-temperature-mean"
SHORTWAVE_STREAM = "climate-field-shortwave-radiation"
SEEDED_AT = datetime(2026, 8, 26, tzinfo=UTC)
SEEDED_ROW_COUNT = 397
EXPECTED_DEFAULT_MAX_DAYS = 1
EXPECTED_DISTINCT_CLOCKS = 2
#: Long enough that an unclamped wait is unmistakable, short enough that the test stays fast.
OVERSHOOT_SECONDS = 0.1
NARROW_BUDGET_SECONDS = 0.02
AIR_TEMPERATURE_STREAMS = (
    "climate-field-air-temperature-max",
    "climate-field-air-temperature-mean",
    "climate-field-air-temperature-min",
)


def bounded_config(**overrides: Any) -> forward.ClimateForwardConfig:
    """One fully-bounded turn, so a test names only the knob it is about."""
    base = forward.ClimateForwardConfig(
        product_id="all",
        max_days=1,
        time_budget_seconds=60.0,
        retry_attempts=2,
        retry_base_seconds=1.0,
        retry_max_seconds=2.0,
        contention_timeout_seconds=300.0,
    )
    return replace(base, **overrides)


class SessionDouble:
    """Counts rollbacks and executes no real SQL."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        self.bound = (statement, params)

    async def rollback(self) -> None:
        self.rollbacks += 1


def seed_complete_day(backend: RecordingBackend, stream: str, day: date) -> None:
    """Write one day's parts and completion markers at every rung, as a finished export leaves them."""
    marker = PartitionCompletion(part_count=1, row_count=SEEDED_ROW_COUNT, completed_at=SEEDED_AT, run_id="seed")
    for tier in ZOOM_TIERS:
        backend.put(partition_path(stream, CLIMATE_DIRECT_KIND, tier, day), b"parquet", content_type="x")
        backend.put(
            completion_marker_path(stream, CLIMATE_DIRECT_KIND, tier, day),
            marker.to_json_bytes(),
            content_type="application/json",
        )


def test_the_meteorology_settled_edge_is_todays_date_minus_the_measured_lag() -> None:
    """A day newer than the settled edge does not exist upstream yet and must never be fetched."""
    product = product_for(PLANE_STREAM)

    edge = forward.settled_through(product, today=TODAY)

    assert edge == TODAY - timedelta(days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS)
    assert product.publication_lag_days == CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS


def test_shortwave_radiation_waits_far_longer_than_the_meteorology_products() -> None:
    """The solar product publishes months behind; sharing the meteorology lag would fabricate absences."""
    shortwave = product_for(SHORTWAVE_STREAM)

    assert shortwave.publication_lag_days == CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS
    assert forward.settled_through(shortwave, today=TODAY) < forward.settled_through(
        product_for(PLANE_STREAM), today=TODAY
    )


def test_shortwave_radiation_owns_the_nine_weeks_the_other_products_do_not() -> None:
    """Its immutable history ends 2026-05-31, so its forward floor is nine weeks below the others."""
    shortwave = product_for(SHORTWAVE_STREAM)
    meteorology = product_for(PLANE_STREAM)

    assert shortwave.history_floor == date(2026, 6, 1)
    assert meteorology.history_floor == date(2026, 8, 7)
    assert shortwave.history_floor < meteorology.history_floor


def test_a_completed_day_is_an_idempotent_no_op_and_an_unfilled_one_is_owed() -> None:
    """A re-run must select nothing once every rung of a day carries its completion marker."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    product = product_for(PLANE_STREAM)
    first_day = product.history_floor
    last_day = product.history_floor + timedelta(days=2)
    seed_complete_day(backend, PLANE_STREAM, first_day)
    seed_complete_day(backend, PLANE_STREAM, last_day)

    statuses = forward._tier_status_window(store, product, first_day, last_day)
    pending = forward._pending_days(product, statuses)

    assert pending == (first_day + timedelta(days=1),)


def test_every_rung_complete_across_the_window_selects_nothing_at_all() -> None:
    """Idempotence is the whole contract of an hourly writer over a settled window."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    product = product_for(PLANE_STREAM)
    first_day = product.history_floor
    for offset in range(3):
        seed_complete_day(backend, PLANE_STREAM, first_day + timedelta(days=offset))

    statuses = forward._tier_status_window(store, product, first_day, first_day + timedelta(days=2))

    assert forward._pending_days(product, statuses) == ()


def test_the_newest_owed_day_is_taken_first() -> None:
    """Newest-first is what keeps the visible edge of the map moving while a backlog drains."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    product = product_for(PLANE_STREAM)
    first_day = product.history_floor
    last_day = first_day + timedelta(days=3)

    statuses = forward._tier_status_window(store, product, first_day, last_day)
    pending = forward._pending_days(product, statuses)

    assert pending[0] == last_day
    assert pending == tuple(sorted(pending, reverse=True))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_days", 0),
        ("max_days", forward.CLIMATE_MAX_DAYS + 1),
        ("retry_attempts", 0),
        ("time_budget_seconds", float("inf")),
        ("time_budget_seconds", forward.CLIMATE_MAX_TIME_BUDGET_SECONDS + 1),
        ("contention_timeout_seconds", 0.0),
        ("product_id", "soil-wetness"),
    ],
)
def test_every_operator_knob_is_bounded_before_a_socket_or_a_session_opens(field: str, value: object) -> None:
    """An unbounded turn is how an hourly writer becomes an unbounded fetch against a public API."""
    with pytest.raises(forward.ClimateForwardConfigError):
        forward._validate_config(bounded_config(**{field: value}))


def test_retry_max_must_not_sit_below_retry_base() -> None:
    """A cap under the base is a series that never grows, which reads as backoff and is not."""
    with pytest.raises(forward.ClimateForwardConfigError, match="retry-max-seconds"):
        forward._validate_config(bounded_config(retry_base_seconds=10.0, retry_max_seconds=1.0))


def test_the_cli_selects_by_browser_product_and_all_covers_every_stream() -> None:
    """`--product` names what a user toggles; air temperature is one toggle over three streams."""
    config = forward.parse_args(["--product", "air-temperature"])

    assert config.product_id == "air-temperature"
    assert tuple(product.stream for product in products_for("air-temperature")) == AIR_TEMPERATURE_STREAMS
    assert len(products_for("all")) == len(  # every stream is reachable through exactly one toggle
        {product.stream for toggle in CLIMATE_PRODUCT_IDS for product in products_for(toggle)}
    )


def test_the_default_turn_publishes_one_day_per_product() -> None:
    """One day per product per tick is what keeps an hourly lane bounded against a public API."""
    config = forward.parse_args([])

    assert config.max_days == EXPECTED_DEFAULT_MAX_DAYS
    assert config.product_id == "all"


def test_the_lane_takes_no_bbox_because_the_pinned_support_is_its_extent() -> None:
    """A bbox knob here was the blocker: it covered 109 of the 397 cells the support pins."""
    with pytest.raises(SystemExit):
        forward.parse_args(["--bbox=-125,42,-111,49"])


def test_the_request_budget_is_the_support_times_the_days_times_the_two_publication_clocks() -> None:
    """397 points per day, and one turn can select days at two distinct settled edges, never more."""
    assert CLIMATE_DISTINCT_PUBLICATION_CLOCKS == EXPECTED_DISTINCT_CLOCKS
    assert bounded_config(max_days=1).request_budget == NASA_POWER_SUPPORT_CELL_COUNT * EXPECTED_DISTINCT_CLOCKS
    assert bounded_config(max_days=3).request_budget == NASA_POWER_SUPPORT_CELL_COUNT * 3 * EXPECTED_DISTINCT_CLOCKS


@pytest.mark.asyncio
async def test_a_day_the_request_budget_cannot_cover_is_reported_rather_than_half_fetched(
    support: NasaPowerSupport,
) -> None:
    """Beginning a 397-request fan-out that cannot finish spends the budget and publishes nothing."""
    store = ObjectStore(RecordingBackend())
    product = product_for(PLANE_STREAM)
    spent = ClimateSourceCache(
        request_budget=NASA_POWER_SUPPORT_CELL_COUNT,
        requests_spent=NASA_POWER_SUPPORT_CELL_COUNT,
    )

    result = await forward._publish_product(
        SessionDouble(),
        store,
        product,
        support=support,
        cache=spent,
        today=TODAY,
        run_id="budget-run",
        config=bounded_config(),
        deadline=time.monotonic() + 60,
        availability_storage=None,
    )

    days = result["days"]
    assert isinstance(days, list)
    assert [day["outcome"] for day in days] == [forward.CLIMATE_REQUEST_BUDGET_OUTCOME]
    assert days[0]["source_receipt"] is None


@pytest.mark.asyncio
async def test_an_expired_deadline_stops_a_locked_day_before_it_fetches_anything(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The turn deadline is checked inside the retry loop, not only between days."""
    calls: list[object] = []

    async def never(*_args: object, **_kwargs: object) -> tuple[str, int, int, int, None]:
        calls.append(_kwargs)
        return ("written", 1, 1, 1, None)

    monkeypatch.setattr(forward, "fill_one_lane_day", never)

    result = await forward._publish_locked_day(
        SessionDouble(),
        ObjectStore(RecordingBackend()),
        product_for(PLANE_STREAM),
        date(2026, 8, 20),
        support=support,
        cache=ClimateSourceCache(request_budget=NASA_POWER_SUPPORT_CELL_COUNT),
        today=TODAY,
        run_id="deadline-run",
        config=bounded_config(),
        deadline=time.monotonic() - 1.0,
        availability_storage=None,
    )

    assert result["outcome"] == forward.CLIMATE_TIME_BUDGET_OUTCOME
    assert result["attempts"] == 0
    assert calls == [], "an expired budget must not open a single upstream request"


@pytest.mark.asyncio
async def test_a_time_budget_exhausted_fetch_is_a_bounded_stop_and_not_a_lane_failure(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ClimateTimeBudgetExhaustedError` is a statement about the turn, so it is never a lane failure."""
    session = SessionDouble()

    async def budget_stop(*_args: object, **_kwargs: object) -> tuple[str, int, int, int, None]:
        raise ClimateTimeBudgetExhaustedError("the turn's time budget ran out before 2026-08-20 completed")

    monkeypatch.setattr(forward, "fill_one_lane_day", budget_stop)

    result = await forward._publish_locked_day(
        session,
        ObjectStore(RecordingBackend()),
        product_for(PLANE_STREAM),
        date(2026, 8, 20),
        support=support,
        cache=ClimateSourceCache(request_budget=NASA_POWER_SUPPORT_CELL_COUNT),
        today=TODAY,
        run_id="deadline-run",
        config=bounded_config(),
        deadline=time.monotonic() + 60,
        availability_storage=None,
    )

    assert result["outcome"] == forward.CLIMATE_TIME_BUDGET_OUTCOME
    assert result["attempts"] == 1
    assert session.rollbacks >= 1, "the session must be rolled back before the turn reports its stop"


@pytest.mark.asyncio
async def test_the_retry_wait_is_clamped_to_the_remaining_budget_rather_than_the_ladder(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unclamped, a non-terminal outcome sleeps the ladder and then raises; clamped, it stops cleanly."""

    async def slow_contention(*_args: object, **_kwargs: object) -> tuple[str, int, int, int, str]:
        await asyncio.sleep(OVERSHOOT_SECONDS)
        return ("contended", 0, 0, 0, "another run holds this lane-day")

    monkeypatch.setattr(forward, "fill_one_lane_day", slow_contention)

    result = await forward._publish_locked_day(
        SessionDouble(),
        ObjectStore(RecordingBackend()),
        product_for(PLANE_STREAM),
        date(2026, 8, 20),
        support=support,
        cache=ClimateSourceCache(request_budget=NASA_POWER_SUPPORT_CELL_COUNT),
        today=TODAY,
        run_id="deadline-run",
        config=bounded_config(retry_attempts=2, retry_base_seconds=30.0, retry_max_seconds=60.0),
        deadline=time.monotonic() + NARROW_BUDGET_SECONDS,
        availability_storage=None,
    )

    assert result["outcome"] == forward.CLIMATE_TIME_BUDGET_OUTCOME
    assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_the_contention_wait_is_bounded_by_the_turn_deadline_not_by_its_own_timeout(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 3600 s contention wait outlives the executor's command timeout while holding a session lock."""

    @asynccontextmanager
    async def never_granted(*_args: object, **_kwargs: object) -> AsyncIterator[bool]:
        await asyncio.sleep(OVERSHOOT_SECONDS)
        yield False

    monkeypatch.setattr(forward, "postgres_lane_day_lock", never_granted)

    result = await forward._publish_day_with_retries(
        SessionDouble(),
        ObjectStore(RecordingBackend()),
        product_for(PLANE_STREAM),
        date(2026, 8, 20),
        support=support,
        cache=ClimateSourceCache(request_budget=NASA_POWER_SUPPORT_CELL_COUNT),
        today=TODAY,
        run_id="contention-run",
        config=bounded_config(contention_timeout_seconds=3_600.0),
        deadline=time.monotonic() + NARROW_BUDGET_SECONDS,
        availability_storage=None,
    )

    assert result["outcome"] == forward.CLIMATE_TIME_BUDGET_OUTCOME


@pytest.mark.asyncio
async def test_the_climate_writer_hands_its_availability_storage_to_every_day_it_exports(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `PARQUET_COVERAGE_AUTHORITY=availability` a day published without an index entry is withheld.

    Mirrors `tests/parquet/test_drain.py::test_a_drain_hands_its_availability_storage_to_every_exported_day`.
    The attempt is then allowed to fail on its own terms; what this proves is the kwarg reaching the
    export, which happens before any outcome is decided.
    """
    storage = object()
    handed: list[object] = []

    async def record(*_args: object, **kwargs: object) -> tuple[str, int, int, int, str]:
        handed.append(kwargs.get("availability_storage"))
        return ("contended", 0, 0, 0, "another run holds this lane-day")

    monkeypatch.setattr(forward, "fill_one_lane_day", record)

    with pytest.raises(forward.DirectClimateFieldError, match="four-rung ladder"):
        await forward._publish_locked_day(
            SessionDouble(),
            ObjectStore(RecordingBackend()),
            product_for(PLANE_STREAM),
            date(2026, 8, 20),
            support=support,
            cache=ClimateSourceCache(request_budget=NASA_POWER_SUPPORT_CELL_COUNT),
            today=TODAY,
            run_id="availability-run",
            config=bounded_config(retry_attempts=1),
            deadline=time.monotonic() + 60,
            availability_storage=storage,
        )

    assert handed == [storage], "the export path must receive the writer's own storage, not None"
