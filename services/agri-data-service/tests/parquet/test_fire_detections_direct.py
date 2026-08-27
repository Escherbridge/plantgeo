"""Direct FIRMS publisher ownership, lock ordering, and bounded retry tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.direct import fire_detections as direct
from agri_data_service.pipeline.lanes.fire_detections import FIRE_DETECTIONS_DIRECT_WRITER_START_DAY
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ParquetWriteReceipt

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

DAY = date(2026, 8, 25)
TODAY = date(2026, 8, 27)
BBOX = "-125,42,-111,49"
BBOX_ARGUMENT = f"--bbox={BBOX}"
EXPECTED_WRITE_ATTEMPTS = 2
FORWARD_ENV = (
    "FIRE_FORWARD_START_DAY",
    "FIRE_FORWARD_LOOKBACK_DAYS",
    "FIRE_FORWARD_MAX_DAYS",
    "FIRE_FORWARD_MAX_RECORDS_PER_DAY",
    "FIRE_FORWARD_RETRY_ATTEMPTS",
    "FIRE_FORWARD_RETRY_BASE_SECONDS",
    "FIRE_FORWARD_RETRY_MAX_SECONDS",
    "FIRE_FORWARD_CONTENTION_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def clear_forward_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in FORWARD_ENV:
        monkeypatch.delenv(name, raising=False)


class RecordingSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def rollback(self) -> None:
        self.events.append("rollback")


class RecordingStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.tables: list[pa.Table] = []

    def absence_exists(self, *_args: object) -> bool:
        return False

    def write_partition(self, table: pa.Table, **kwargs: Any) -> ParquetWriteReceipt:
        attempt = len(self.tables) + 1
        self.events.append(f"write:{attempt}")
        self.tables.append(table)
        return ParquetWriteReceipt(
            key=f"key-{attempt}",
            relative_path=f"part-{attempt}.parquet",
            stream=kwargs["layer"],
            kind=kwargs["kind"],
            zoom=kwargs["zoom"],
            day=kwargs["day"],
            row_count=table.num_rows,
            byte_count=attempt,
            sha256=str(attempt),
        )


def source(serial: int) -> direct.FireDaySource:
    return direct.FireDaySource(
        day=DAY,
        raw_records=serial,
        deduplicated_records=serial,
        source_products=(f"product-{serial}",),
        product_counts={f"product-{serial}": serial},
        table=pa.table({"serial": [serial]}),
    )


def config(**overrides: object) -> direct.FireForwardConfig:
    base = direct.FireForwardConfig(
        bbox=BBOX,
        lookback_days=1,
        max_days=1,
        max_records_per_day=50_000,
        retry_attempts=EXPECTED_WRITE_ATTEMPTS,
        retry_base_seconds=0.1,
        retry_max_seconds=0.1,
        contention_timeout_seconds=0.1,
        forward_start_day=FIRE_DETECTIONS_DIRECT_WRITER_START_DAY,
    )
    return replace(base, **overrides)


@pytest.mark.asyncio
async def test_lock_precedes_fetch_and_each_publish_retry_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    fetched: list[int] = []
    verified = [False, True]

    @asynccontextmanager
    async def lock(_session: object, _key: str) -> AsyncIterator[bool]:
        events.append("lock:acquire")
        try:
            yield True
        finally:
            events.append("lock:release")

    async def fetch(**kwargs: object) -> direct.FireDaySource:
        assert kwargs["retry_attempts"] == 1
        serial = len(fetched) + 1
        fetched.append(serial)
        events.append(f"fetch:{serial}")
        return source(serial)

    async def fill(
        session: RecordingSession,
        store: RecordingStore,
        lane: LaneRegistration,
        **kwargs: object,
    ) -> tuple[str, int, int, int, None]:
        serial = len(fetched) + 1
        events.append(f"statement-timeout:{serial}")
        result = await lane.adapter(
            cast("AsyncSession", session),
            cast("ObjectStore", store),
            day=cast("date", kwargs["day"]),
            run_id=cast("str", kwargs["run_id"]),
        )
        return "written", result.part_count, result.row_count, result.byte_count, None

    def status(_store: object, _day: date) -> dict[ZoomTier, PartitionDayStatus]:
        attempt = len(fetched)
        events.append(f"verify:{attempt}")
        state: PartitionDayStatus = "data" if verified.pop(0) else "missing"
        return dict.fromkeys(direct.FIRE_DIRECT_ALL_TIERS, state)

    async def no_sleep(_delay: float) -> None:
        events.append("sleep")

    monkeypatch.setattr(direct, "postgres_lane_day_lock", lock)
    monkeypatch.setattr(direct, "fetch_fire_day", fetch)
    monkeypatch.setattr(direct, "fill_one_lane_day", fill)
    monkeypatch.setattr(direct, "_tier_status_day", status)
    monkeypatch.setattr(direct.asyncio, "sleep", no_sleep)
    session = RecordingSession(events)
    store = RecordingStore(events)

    result = await direct._publish_day_with_retries(
        cast("AsyncSession", session),
        cast("ObjectStore", store),
        LANE_REGISTRY["fire-detections"],
        DAY,
        today=TODAY,
        run_id="test",
        config=config(),
    )

    assert fetched == [1, 2]
    assert [table.column("serial")[0].as_py() for table in store.tables] == [1, 2]
    assert events.count("lock:acquire") == 1
    assert events.count("lock:release") == 1
    assert events.index("lock:acquire") < events.index("rollback") < events.index("fetch:1")
    assert events.index("fetch:2") < events.index("lock:release")
    assert result["raw_records"] == EXPECTED_WRITE_ATTEMPTS


@pytest.mark.asyncio
async def test_contention_times_out_without_fetching_or_consuming_a_write_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"fetch": 0, "fill": 0}

    @asynccontextmanager
    async def contended(_session: object, _key: str) -> AsyncIterator[bool]:
        yield False

    async def fetch(**_kwargs: object) -> direct.FireDaySource:
        calls["fetch"] += 1
        return source(1)

    async def fill(*_args: object, **_kwargs: object) -> object:
        calls["fill"] += 1
        raise AssertionError("a contended day must not enter the writer")

    ticks = iter((0.0, 0.2))
    monkeypatch.setattr(direct, "postgres_lane_day_lock", contended)
    monkeypatch.setattr(direct, "fetch_fire_day", fetch)
    monkeypatch.setattr(direct, "fill_one_lane_day", fill)
    monkeypatch.setattr(direct, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    with pytest.raises(direct.DirectFireDetectionsError, match="contention"):
        await direct._publish_day_with_retries(
            cast("AsyncSession", RecordingSession([])),
            cast("ObjectStore", object()),
            LANE_REGISTRY["fire-detections"],
            DAY,
            today=TODAY,
            run_id="test",
            config=config(),
        )

    assert calls == {"fetch": 0, "fill": 0}


@pytest.mark.asyncio
async def test_publish_attempt_bound_also_bounds_source_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    fetches = 0

    @asynccontextmanager
    async def lock(_session: object, _key: str) -> AsyncIterator[bool]:
        yield True

    async def fetch(**_kwargs: object) -> direct.FireDaySource:
        nonlocal fetches
        fetches += 1
        return source(fetches)

    async def fill(
        session: RecordingSession,
        store: RecordingStore,
        lane: LaneRegistration,
        **kwargs: object,
    ) -> tuple[str, int, int, int, None]:
        result = await lane.adapter(
            cast("AsyncSession", session),
            cast("ObjectStore", store),
            day=cast("date", kwargs["day"]),
            run_id=cast("str", kwargs["run_id"]),
        )
        return "written", result.part_count, result.row_count, result.byte_count, None

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(direct, "postgres_lane_day_lock", lock)
    monkeypatch.setattr(direct, "fetch_fire_day", fetch)
    monkeypatch.setattr(direct, "fill_one_lane_day", fill)
    monkeypatch.setattr(
        direct,
        "_tier_status_day",
        lambda _store, _day: dict.fromkeys(direct.FIRE_DIRECT_ALL_TIERS, "missing"),
    )
    monkeypatch.setattr(direct.asyncio, "sleep", no_sleep)

    with pytest.raises(
        direct.DirectFireDetectionsError,
        match=f"after {EXPECTED_WRITE_ATTEMPTS} attempt",
    ):
        await direct._publish_day_with_retries(
            cast("AsyncSession", RecordingSession([])),
            cast("ObjectStore", RecordingStore([])),
            LANE_REGISTRY["fire-detections"],
            DAY,
            today=TODAY,
            run_id="test",
            config=config(retry_attempts=EXPECTED_WRITE_ATTEMPTS),
        )

    assert fetches == EXPECTED_WRITE_ATTEMPTS


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("--retry-base-seconds", "nan"),
        ("--retry-max-seconds", "inf"),
        ("--contention-timeout-seconds", "-inf"),
        ("--retry-max-seconds", "301"),
    ],
)
def test_cli_rejects_nonfinite_and_over_cap_waits(argument: str, value: str) -> None:
    with pytest.raises(SystemExit):
        direct._parse_args([BBOX_ARGUMENT, argument, value])


def test_direct_writer_boundary_is_pinned_for_defaults_env_and_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FIRE_FORWARD_START_DAY", raising=False)
    assert direct._parse_args([BBOX_ARGUMENT]).forward_start_day == FIRE_DETECTIONS_DIRECT_WRITER_START_DAY

    monkeypatch.setenv("FIRE_FORWARD_START_DAY", "2026-08-24")
    with pytest.raises(SystemExit):
        direct._parse_args([BBOX_ARGUMENT])

    monkeypatch.delenv("FIRE_FORWARD_START_DAY", raising=False)
    with pytest.raises(SystemExit):
        direct._parse_args([BBOX_ARGUMENT, "--forward-start-day", "2026-08-26"])


@pytest.mark.asyncio
async def test_programmatic_boundary_mismatch_fails_before_opening_any_dependency() -> None:
    with pytest.raises(direct.DirectFireDetectionsError, match="pinned to 2026-08-25"):
        await direct.run_fire_forward(replace(config(), forward_start_day=date(2026, 8, 24)))
