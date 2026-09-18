"""Day selection, per-product lag, idempotence, the argument bounds and the turn deadline of one climate turn."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.climate import forward
from agri_data_service.pipeline.direct.climate.adapter import CLIMATE_DIRECT_KIND, DirectClimateFieldAdapter
from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_DISTINCT_PUBLICATION_CLOCKS,
    CLIMATE_FIELD_PRODUCTS,
    CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    CLIMATE_PRODUCT_IDS,
    CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
    products_for,
)
from agri_data_service.pipeline.direct.climate.source import (
    ClimateProviderDeferredError,
    ClimateSourceCache,
    ClimateTimeBudgetExhaustedError,
    climate_day_from_cache,
)
from agri_data_service.pipeline.direct.climate.support import NASA_POWER_SUPPORT_CELL_COUNT
from agri_data_service.pipeline.parquet.availability_extension import (
    AvailabilityExtensionOutcome,
    AvailabilityExtensionTally,
)
from agri_data_service.pipeline.parquet.gap_fill import GAP_FILL_PARTITION_KIND, fill_one_lane_day, unlocked_lane_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.direct.climate.conftest import filled_cache, product_for
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
#: The day POWER's live solar edge was measured, and the clock the catch-up arithmetic is stated at.
#: `products.SHORTWAVE_LAG_MEASUREMENT_EVIDENCE`.
MEASUREMENT_DAY = date(2026, 9, 15)
#: The measured 4-day solar edge plus the couple of days the edge moved between the August (5-day)
#: and September (3-day) meteorology readings -- jitter, not a copied margin.
EXPECTED_SHORTWAVE_LAG_DAYS = 6
#: 2026-06-01 through 2026-09-09 inclusive: the owed tail the corrected lag makes eligible at once.
EXPECTED_CATCH_UP_DAYS = 101
#: A day written while one support cell still answered POWER's fill: 396 rows under a complete marker.
SHORT_ROW_COUNT = NASA_POWER_SUPPORT_CELL_COUNT - 1
#: The two all-rungs-complete days a fifteen-day census window holds in the marker-read test below.
EXPECTED_MARKER_READS = 2
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


@pytest.mark.asyncio
async def test_provider_quota_passes_through_gap_fill_as_one_deferred_attempt(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caught adapter refusal must remain a deferral through the real gap-fill driver."""
    fetch = AsyncMock(side_effect=ClimateProviderDeferredError("provider quota exceeded"))
    sleep = AsyncMock(side_effect=AssertionError("a deferred provider must not enter publication retry sleep"))
    monkeypatch.setattr(forward, "fetch_climate_day", fetch)
    monkeypatch.setattr(forward.asyncio, "sleep", sleep)
    backend = RecordingBackend()

    result = await forward._publish_locked_day(
        SessionDouble(),
        ObjectStore(backend),
        product_for(PLANE_STREAM),
        date(2026, 8, 20),
        support=support,
        cache=ClimateSourceCache(request_budget=NASA_POWER_SUPPORT_CELL_COUNT),
        today=TODAY,
        run_id="quota-run",
        config=bounded_config(),
        deadline=time.monotonic() + 60,
        availability_storage=None,
        availability=AvailabilityExtensionTally(),
        mirrored_past=None,
    )

    assert result["outcome"] == forward.CLIMATE_SOURCE_UNSETTLED_OUTCOME
    assert result["attempts"] == 1
    assert fetch.await_count == 1
    sleep.assert_not_awaited()
    assert not backend.objects


class SessionDouble:
    """Counts rollbacks and executes no real SQL."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        self.bound = (statement, params)

    async def rollback(self) -> None:
        self.rollbacks += 1


class CountingBackend(RecordingBackend):
    """The in-memory backend, counting every single-object read so a census's read cost can be asserted."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    def get(self, key: str) -> bytes | None:
        self.reads.append(key)
        return super().get(key)


def seed_complete_day(backend: RecordingBackend, stream: str, day: date, *, row_count: int = SEEDED_ROW_COUNT) -> None:
    """Write one day's parts and completion markers at every rung, as a finished export leaves them."""
    marker = PartitionCompletion(part_count=1, row_count=row_count, completed_at=SEEDED_AT, run_id="seed")
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


def test_shortwave_radiation_waits_exactly_one_day_longer_than_the_meteorology_products() -> None:
    """MEASURED 2026-09-15: POWER's solar edge was 2026-09-11 and its T2M edge 2026-09-12, one day apart.

    The lag was 75 until that measurement, inferred from a stale snapshot's internal 67-day gap, and it
    held this lane's settled ceiling 107 days behind its siblings in production. Sharing the
    meteorology lag outright would be the opposite failure: a fetch after a day POWER has not produced
    is a governed absence that is simply wrong, so the solar clock keeps its own extra day.
    """
    shortwave = product_for(SHORTWAVE_STREAM)
    meteorology = product_for(PLANE_STREAM)

    assert CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS == EXPECTED_SHORTWAVE_LAG_DAYS
    assert shortwave.publication_lag_days == CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS
    assert shortwave.publication_lag_days - meteorology.publication_lag_days == 1
    assert forward.settled_through(shortwave, today=TODAY) == forward.settled_through(
        meteorology, today=TODAY
    ) - timedelta(days=1)


def test_a_premature_absence_is_always_inside_the_recheck_window() -> None:
    """What the recheck window guards: a WRONG ABSENCE behind the frontier, at either lag.

    If the whole lattice ever trails past a lag, the all-fill day behind the frontier is governed
    absent against a later published day, and that marker is only undone by
    `adapter._retract_disproven_absence`, which runs on selected days only -- so the absence must
    still be inside the window the walk comes back to. This says NOTHING about a day where SOME
    cells trail: that day is written short and is `data` everywhere; see the partial-day tests below.
    """
    assert forward.CLIMATE_ABSENCE_RECHECK_DAYS > CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS
    assert forward.CLIMATE_ABSENCE_RECHECK_DAYS > CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS


def test_the_clock_count_is_derived_from_the_products_and_reads_two_while_the_solar_lag_differs() -> None:
    """Not pinned: were every product to share one lag it would read 1, and a budget of 397 would be right.

    One distinct day is one 397-cell fan-out however many products read it, because the turn cache is
    keyed by cell and day. The cost of a second clock is therefore not a smaller budget but a SECOND
    fan-out in the same turn -- the one POWER answered 429 in production on 2026-09-15 and 2026-09-16.
    """
    distinct_lags = {product.publication_lag_days for product in CLIMATE_FIELD_PRODUCTS}

    assert len(distinct_lags) == CLIMATE_DISTINCT_PUBLICATION_CLOCKS
    assert CLIMATE_DISTINCT_PUBLICATION_CLOCKS == EXPECTED_DISTINCT_CLOCKS
    assert CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS != CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS


@pytest.mark.asyncio
async def test_one_turn_censuses_the_whole_owed_solar_tail_and_takes_only_its_newest_day(
    support: NasaPowerSupport,
) -> None:
    """The floor needs no correction: one turn's census reaches 2026-06-01, so the walk owns the whole tail.

    Driven through `_publish_product` on the measurement day over an empty bucket, with a request
    budget of zero so no socket opens: the census is real, the selection is real, and the one
    selected day is stopped at the budget check. Under the old 75 the ceiling sat at 2026-07-02 and
    most of the tail was not eligible. What this turn reports is also the operational cost: at the
    default `--max-days` of 1 a turn takes ONE owed day, so `backlog_days` is the number of drain
    turns owed, on top of the one turn a day the advancing ceiling takes -- IF the executor runs
    the lane hourly. Once a day, as observed in production on 2026-09-15/16, the number never falls.
    """
    shortwave = product_for(SHORTWAVE_STREAM)

    result = await forward._publish_product(
        SessionDouble(),
        ObjectStore(RecordingBackend()),
        shortwave,
        support=support,
        cache=ClimateSourceCache(request_budget=0),
        today=MEASUREMENT_DAY,
        run_id="census-run",
        config=bounded_config(product_id="shortwave-radiation"),
        deadline=time.monotonic() + 60,
        availability_storage=None,
        availability=AvailabilityExtensionTally(),
    )

    assert result["settled_through"] == "2026-09-09"
    assert result["scan_first_day"] == shortwave.history_floor.isoformat() == "2026-06-01"
    assert result["backlog_days"] == EXPECTED_CATCH_UP_DAYS
    assert result["partial_day_rechecks"] == 0
    days = result["days"]
    assert isinstance(days, list)
    assert [day["day"] for day in days] == ["2026-09-09"], "one owed day per turn, newest first"
    assert days[0]["outcome"] == forward.CLIMATE_REQUEST_BUDGET_OUTCOME
    assert result["outcome"] == forward.CLIMATE_REQUEST_BUDGET_OUTCOME, "a turn that wrote nothing says so"


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


def test_a_turn_that_only_met_an_unsettled_day_does_not_report_itself_as_published() -> None:
    """The mask that hides this exact defect: a green `published` for a turn that wrote nothing.

    `source_unsettled` is deliberately non-failing, so a lane whose lag is too large -- or whose
    source stopped publishing -- ticks green forever while its edge stands still. The product-level
    word has to be the day's own.
    """
    unsettled = forward._stopped_day(date(2026, 9, 1), outcome=forward.CLIMATE_SOURCE_UNSETTLED_OUTCOME)

    assert forward._product_outcome([date(2026, 9, 1)], [unsettled]) == forward.CLIMATE_SOURCE_UNSETTLED_OUTCOME


@pytest.mark.parametrize(
    ("day_outcomes", "expected"),
    [
        ((), "idempotent_noop"),
        (("written",), "published"),
        (("absent",), "published"),
        ((forward.CLIMATE_TIME_BUDGET_OUTCOME,), forward.CLIMATE_TIME_BUDGET_OUTCOME),
        ((forward.CLIMATE_REQUEST_BUDGET_OUTCOME,), forward.CLIMATE_REQUEST_BUDGET_OUTCOME),
        ((forward.CLIMATE_SOURCE_UNSETTLED_OUTCOME, "written"), "published"),
    ],
)
def test_the_product_outcome_is_the_word_the_days_actually_earned(day_outcomes: tuple[str, ...], expected: str) -> None:
    """Every bound reaches the run report under its own name, and a settled day still reads published."""
    backlog = [date(2026, 9, 1) + timedelta(days=offset) for offset in range(len(day_outcomes))]
    days = [{"outcome": outcome} for outcome in day_outcomes]

    assert forward._product_outcome(backlog, days) == expected


def test_an_empty_backlog_is_still_an_idempotent_no_op() -> None:
    """Idempotence is the whole contract of an hourly writer over a window it has already filled."""
    assert forward._product_outcome([], []) == "idempotent_noop"


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
        ("product_id", "soil-moisture"),
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
        availability=AvailabilityExtensionTally(),
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
        availability=AvailabilityExtensionTally(),
        mirrored_past=None,
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
        availability=AvailabilityExtensionTally(),
        mirrored_past=None,
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
        availability=AvailabilityExtensionTally(),
        mirrored_past=None,
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
        availability=AvailabilityExtensionTally(),
        mirrored_past=None,
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
            availability=AvailabilityExtensionTally(),
            mirrored_past=None,
        )

    assert handed == [storage], "the export path must receive the writer's own storage, not None"


@pytest.mark.asyncio
async def test_the_climate_writer_drains_its_own_owed_availability_claims(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DO NOT DELETE. Nothing else retries a climate lane's claims, so without this they are never read.

    `retry_pending_availability` is otherwise reached only from `run_gap_fill`, and activating
    `climate-nasa-power-direct-forward` DEACTIVATES the eleven generic lanes through `conflicts_with`.
    A climate day whose pointer read failed therefore wrote a claim that no driver in this service
    would ever come back for -- and the base-tier census never revisits a completed day.
    """
    storage = object()
    asked: list[tuple[object, object, object]] = []

    async def record_retry(*_args: object, **kwargs: object) -> tuple[AvailabilityExtensionOutcome, ...]:
        asked.append((kwargs["lane"], kwargs["kind"], kwargs["availability"]))
        return (
            AvailabilityExtensionOutcome(
                state="extended",
                lane_root="layer=climate-field-air-temperature-mean/kind=observed",
                day=date(2026, 8, 20),
                reason="the owed day joined the generation",
            ),
        )

    async def no_days(*_args: object, **_kwargs: object) -> tuple[str, int, int, int, None]:
        return ("written", 1, 1, 1, None)

    monkeypatch.setattr(forward, "retry_pending_availability", record_retry)
    monkeypatch.setattr(forward, "fill_one_lane_day", no_days)
    tally = AvailabilityExtensionTally()

    result = await forward._publish_product(
        SessionDouble(),
        ObjectStore(RecordingBackend()),
        product_for(PLANE_STREAM),
        support=support,
        cache=ClimateSourceCache(request_budget=0),
        today=TODAY,
        run_id="retry-run",
        config=bounded_config(),
        deadline=time.monotonic() + 60,
        availability_storage=storage,
        availability=tally,
    )

    assert asked == [(PLANE_STREAM, GAP_FILL_PARTITION_KIND, storage)], "once, for this product's own lane"
    assert result["availability_retried_days"] == 1
    assert tally.to_summary()["availability_extended"] == 1, "a retried day is a number in the run report"


@pytest.mark.asyncio
async def test_an_owed_retry_is_not_started_after_the_turn_deadline(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry re-verifies every physical part of a day; a turn out of clock must not begin one."""

    async def refuse_retry(*_args: object, **_kwargs: object) -> tuple[AvailabilityExtensionOutcome, ...]:
        raise AssertionError("an expired turn started an availability retry")

    monkeypatch.setattr(forward, "retry_pending_availability", refuse_retry)

    result = await forward._publish_product(
        SessionDouble(),
        ObjectStore(RecordingBackend()),
        product_for(PLANE_STREAM),
        support=support,
        cache=ClimateSourceCache(request_budget=0),
        today=TODAY,
        run_id="expired-run",
        config=bounded_config(),
        deadline=time.monotonic() - 1,
        availability_storage=object(),
        availability=AvailabilityExtensionTally(),
    )

    assert result["availability_retried_days"] == 0


@pytest.mark.asyncio
async def test_the_climate_run_report_states_its_availability_verdicts_as_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verdict that lives only in a day's detail string reports a permanently unindexed day as green."""

    async def publish(*_args: object, **kwargs: object) -> dict[str, object]:
        tally = kwargs["availability"]
        assert isinstance(tally, AvailabilityExtensionTally)
        tally.record(
            AvailabilityExtensionOutcome(
                state="ladder_incomplete",
                lane_root="layer=climate-field-dew-point/kind=observed",
                day=date(2026, 8, 20),
                reason="the day is terminal and cannot form the required ladder",
            )
        )
        return {"layer": "climate-field-dew-point", "days": []}

    @asynccontextmanager
    async def session(_url: str) -> AsyncIterator[SessionDouble]:
        yield SessionDouble()

    async def support_of(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(forward, "_publish_product", publish)
    monkeypatch.setattr(forward, "local_source_loader_session", session)
    monkeypatch.setattr(forward, "load_nasa_power_support", support_of)
    monkeypatch.setattr(
        forward.ObjectStore, "from_settings", classmethod(lambda _cls, _source=None: ObjectStore(RecordingBackend()))
    )
    monkeypatch.setattr(
        forward.BotoAvailabilityStorage, "from_settings", classmethod(lambda _cls, _source=None: object())
    )
    monkeypatch.setattr(
        forward.settings.__class__,
        "require_local_source_loader_database_url",
        lambda _self: "postgresql+asyncpg://unused/never-opened",
    )

    report = await forward.run_climate_forward(bounded_config(product_id="dew-point", today=TODAY))

    assert report["availability_ladder_incomplete"] == 1, "the loss is a counter, not a sentence in one day"
    assert report["availability_extended"] == 0


# --- Absences are re-examined, and an all-fill day needs the release proven past it ----------------


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
        for tier in forward.CLIMATE_DIRECT_ALL_TIERS
    }


def test_a_recent_absence_is_re_examined_so_the_retraction_is_reachable_at_all() -> None:
    """DO NOT DELETE. `_retract_disproven_absence` runs only on a day the walk SELECTS.

    POWER revises a fill-value day into real values once its inputs land. Skipping every `absent` day made that
    retraction unreachable, so an absence, once written, was permanent whatever the archive did next.
    """
    product = products_for("all")[0]
    newest = date(2026, 8, 20)
    days = {newest - timedelta(days=offset): "absent" for offset in range(3)}

    pending = forward._pending_days(product, _statuses(days))

    assert set(pending) == set(days), "every recent absence is owed a second look"


def test_an_absence_older_than_the_recheck_window_is_left_alone() -> None:
    """Bounded, or every turn re-fetches days that settled years ago and never reaches a real gap."""
    product = products_for("all")[0]
    newest = date(2026, 8, 20)
    stale = newest - timedelta(days=forward.CLIMATE_ABSENCE_RECHECK_DAYS)
    days = {stale: "absent", newest: "data"}

    pending = forward._pending_days(product, _statuses(days))

    assert pending == ()


def test_a_recheck_never_outranks_a_day_that_holds_no_data_at_all() -> None:
    """A turn publishes one day; spending it on an answered day while a real gap waits is a regression."""
    product = products_for("all")[0]
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


# --- A day written short of the support is re-examined too, or its missing cells are permanent -------


def test_a_completed_day_short_of_the_support_is_re_selected_as_a_recheck_behind_real_work() -> None:
    """DO NOT DELETE. A mixed day is written short, stamped complete, and is `data` at every rung.

    Nothing about its statuses distinguishes it from a whole day, so before this a fill cell caught
    at the edge -- `fixtures/nasa-power-point-response-2026-09-02.json` shows one thirteen days back
    -- was a permanent silent hole, at lag 5 for the seven siblings as much as at any solar lag. Named
    in `partial_days`, it is a recheck: behind every day that owes real work, newest first.
    """
    product = products_for("all")[0]
    newest = date(2026, 8, 20)
    gap = newest - timedelta(days=1)
    short = newest - timedelta(days=2)
    whole = newest - timedelta(days=3)
    statuses = _statuses({whole: "data", short: "data", gap: "missing", newest: "data"})

    assert forward._pending_days(product, statuses, partial_days={short}) == (gap, short)
    assert forward._pending_days(product, statuses) == (gap,), "unnamed, a short day is invisible"


def test_a_short_day_older_than_the_recheck_window_is_left_alone() -> None:
    """Bounded like the absences: a cell that has trailed for a fortnight is not re-asked for every hour forever."""
    product = products_for("all")[0]
    newest = date(2026, 8, 20)
    stale = newest - timedelta(days=forward.CLIMATE_ABSENCE_RECHECK_DAYS)
    statuses = _statuses({stale: "data", newest: "data"})

    assert forward._pending_days(product, statuses, partial_days={stale}) == ()


def test_short_days_are_read_off_the_base_marker_for_completed_days_inside_the_window_only() -> None:
    """The base completion marker's `row_count` is the durable record of a short day; reading it is bounded.

    One GET per all-rungs-`data` day inside the recheck window, none for the days outside it, so a
    turn's census cost grows by at most `CLIMATE_ABSENCE_RECHECK_DAYS` small reads per product.
    """
    backend = CountingBackend()
    store = ObjectStore(backend)
    product = product_for(PLANE_STREAM)
    newest = date(2026, 9, 9)
    short = newest - timedelta(days=1)
    stale_short = newest - timedelta(days=forward.CLIMATE_ABSENCE_RECHECK_DAYS)
    seed_complete_day(backend, PLANE_STREAM, newest)
    seed_complete_day(backend, PLANE_STREAM, short, row_count=SHORT_ROW_COUNT)
    seed_complete_day(backend, PLANE_STREAM, stale_short, row_count=SHORT_ROW_COUNT)
    statuses = forward._tier_status_window(store, product, stale_short, newest)
    backend.reads.clear()

    partial = forward._partial_days_in_recheck_window(store, product, statuses)

    assert partial == {short: SHORT_ROW_COUNT}, "keyed to the count the re-bind must strictly exceed"
    assert len(backend.reads) == EXPECTED_MARKER_READS, "the two complete days inside the window, and nothing else"
    pending = forward._pending_days(product, statuses, partial_days=partial)
    assert pending[-1] == short, "a recheck, so behind every unseeded day of the window that owes real work"
    assert stale_short not in pending


@pytest.mark.asyncio
async def test_a_short_day_is_re_bound_whole_through_the_same_lane_day_path_and_its_marker_counts_every_cell(
    support: NasaPowerSupport,
) -> None:
    """End to end: written short, censused as short, re-selected, re-bound whole, censused as whole.

    The re-bind goes through `fill_one_lane_day` exactly as the first write did: `write_partition`
    retracts the marker as it overwrites `part-0`, the coarse rungs are re-derived, and the base
    marker is re-stamped with the count that was actually written.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    product = product_for(PLANE_STREAM)
    day = date(2026, 8, 20)

    async def publish(cache: ClimateSourceCache, *, existing_row_count: int | None = None) -> tuple[str, int]:
        async def fetch() -> Any:
            return climate_day_from_cache(product, day=day, support=support, cache=cache)

        adapter = DirectClimateFieldAdapter(
            product=product,
            fetch_source=fetch,
            mirrored_past_proof=lambda: "proof",
            existing_row_count=existing_row_count,
        )
        outcome, _parts, rows, _written_bytes, _detail = await fill_one_lane_day(
            SessionDouble(),
            store,
            replace(LANE_REGISTRY[PLANE_STREAM], adapter=adapter),
            day=day,
            run_id="rebind-run",
            now=lambda: SEEDED_AT,
            today=TODAY,
            lane_day_lock=unlocked_lane_day,
        )
        return outcome, rows

    def census() -> tuple[dict[date, int], tuple[date, ...]]:
        statuses = forward._tier_status_window(store, product, day, day)
        partial = forward._partial_days_in_recheck_window(store, product, statuses)
        return partial, forward._pending_days(product, statuses, partial_days=partial)

    trailing_cell = support.cells[3].cell_key
    assert await publish(filled_cache(support, day=day, fill_cell_keys=[trailing_cell])) == ("written", SHORT_ROW_COUNT)
    partial, pending = census()
    assert partial == {day: SHORT_ROW_COUNT}, "one fill cell made a short day, and the census sees it"
    assert pending == (day,)

    assert await publish(filled_cache(support, day=day), existing_row_count=partial[day]) == (
        "written",
        NASA_POWER_SUPPORT_CELL_COUNT,
    )
    marker = store.read_completion_marker(PLANE_STREAM, CLIMATE_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
    assert marker is not None
    assert marker.row_count == NASA_POWER_SUPPORT_CELL_COUNT
    assert store.read_partition(PLANE_STREAM, CLIMATE_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day).num_rows == (
        NASA_POWER_SUPPORT_CELL_COUNT
    )
    assert census() == ({}, ()), "whole now, so an idempotent no-op again"


@pytest.mark.asyncio
async def test_a_short_day_whose_cell_still_trails_is_left_exactly_as_it_is(
    support: NasaPowerSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DO NOT DELETE. A recheck that finds the same short answer must not touch the bucket at all.

    `write_partition` clears the base completion marker as it uploads `part-0` and it is re-stamped
    only after the prune and three derivations; every reader spanning the day faults `day_incomplete`
    in between. Rewriting an identical 396-row day every idle hour would be a served outage per
    product for nothing. The refetched count did not grow, so: no write, no delete, no marker clear,
    and the honest word `idempotent_noop` rather than `published`.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    product = product_for(PLANE_STREAM)
    day = date(2026, 8, 20)
    seed_complete_day(backend, PLANE_STREAM, day, row_count=SHORT_ROW_COUNT)
    cache = filled_cache(support, day=day, fill_cell_keys=[support.cells[3].cell_key])

    async def still_short(*_args: object, **_kwargs: object) -> Any:
        return climate_day_from_cache(product, day=day, support=support, cache=cache)

    monkeypatch.setattr(forward, "fetch_climate_day", still_short)
    before = dict(backend.objects)

    result = await forward._publish_locked_day(
        SessionDouble(),
        store,
        product,
        day,
        support=support,
        cache=cache,
        today=TODAY,
        run_id="recheck-run",
        config=bounded_config(),
        deadline=time.monotonic() + 60,
        availability_storage=None,
        availability=AvailabilityExtensionTally(),
        mirrored_past=None,
        existing_row_count=SHORT_ROW_COUNT,
    )

    assert result["outcome"] == "idempotent_noop"
    assert result["fill_value_cells"] == 1, "the receipt still says which cell is trailing"
    assert result["parts"] == 0
    assert backend.objects == before, "not one object written or replaced"
    assert backend.deleted == [], "not one marker cleared"
    marker = store.read_completion_marker(PLANE_STREAM, CLIMATE_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
    assert marker is not None
    assert marker.row_count == SHORT_ROW_COUNT
    assert forward._product_outcome([day], [result]) == "idempotent_noop"


def test_rechecks_are_visited_round_robin_so_a_standing_short_day_cannot_starve_the_others() -> None:
    """DO NOT DELETE. An idle turn takes ONE recheck; two short days with a trailing cell must both come up.

    Newest-first took the same newest short day every hour while its cell trailed and never returned
    to the older one; oldest-first would do the same from the other end once the oldest was a
    standing no-op. Rotated by the turn's clock hour, successive idle turns visit each in turn.
    """
    product = products_for("all")[0]
    newest = date(2026, 8, 20)
    older, newer = newest - timedelta(days=2), newest - timedelta(days=1)
    absent = newest - timedelta(days=3)
    statuses = _statuses({absent: "absent", older: "data", newer: "data", newest: "data"})
    rechecks = {older, newer}

    first_choices = [
        forward._pending_days(product, statuses, partial_days=rechecks, recheck_rotation=turn)[0] for turn in range(6)
    ]

    assert first_choices == [absent, older, newer, absent, older, newer], "oldest first, then round the list"
    every_turn = [
        set(forward._pending_days(product, statuses, partial_days=rechecks, recheck_rotation=turn)) for turn in range(6)
    ]
    assert every_turn == [rechecks | {absent}] * 6, "rotation reorders, it never drops"


def test_a_real_gap_still_outranks_every_recheck_whatever_the_rotation() -> None:
    """Rotation applies to the rechecks alone; a day with no data at all is taken first on every turn."""
    product = products_for("all")[0]
    newest = date(2026, 8, 20)
    gap = newest - timedelta(days=1)
    statuses = _statuses({newest - timedelta(days=2): "data", gap: "missing", newest: "data"})

    for turn in range(3):
        pending = forward._pending_days(
            product, statuses, partial_days={newest, newest - timedelta(days=2)}, recheck_rotation=turn
        )
        assert pending[0] == gap


def test_the_clock_rotation_advances_by_one_per_hourly_turn() -> None:
    """Consecutive hourly turns must land on consecutive rechecks, or the rotation is decorative."""
    first = datetime(2026, 9, 15, 0, 40, tzinfo=UTC)

    rotations = [forward._clock_recheck_rotation(first + timedelta(hours=hour)) for hour in range(3)]

    assert rotations == [rotations[0], rotations[0] + 1, rotations[0] + 2]
