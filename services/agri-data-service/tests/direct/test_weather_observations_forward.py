"""The forward CLI: bounded arguments, and why `--max-days` caps buckets rather than a backlog walk."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.direct.weather_observations import forward
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.weather_observations import WEATHER_OBSERVATIONS_SCHEMA
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY_ONE = date(2026, 9, 1)
DAY_TWO = date(2026, 9, 2)
DAY_THREE = date(2026, 9, 3)


def _args(**overrides: object) -> Any:
    defaults = {
        "max_days": 1,
        "time_budget_seconds": 60.0,
        "retry_attempts": 5,
        "retry_base_seconds": 2.0,
        "retry_max_seconds": 60.0,
        "contention_timeout_seconds": 900.0,
    }
    defaults.update(overrides)

    class Namespace:
        pass

    namespace = Namespace()
    for key, value in defaults.items():
        setattr(namespace, key, value)
    return namespace


class TestValidateArgs:
    def test_accepts_the_parser_defaults(self) -> None:
        forward._validate_args(_args())  # must not raise

    def test_refuses_max_days_above_the_structural_ceiling_of_two(self) -> None:
        """Unlike climate/soil, this lane can never see a third named day in one poll; see forward.py."""
        with pytest.raises(forward.WeatherObservationsForwardConfigError, match="max-days"):
            forward._validate_args(_args(max_days=3))

    def test_refuses_zero_max_days(self) -> None:
        with pytest.raises(forward.WeatherObservationsForwardConfigError, match="max-days"):
            forward._validate_args(_args(max_days=0))

    def test_refuses_a_time_budget_above_the_ceiling(self) -> None:
        with pytest.raises(forward.WeatherObservationsForwardConfigError, match="time-budget"):
            forward._validate_args(_args(time_budget_seconds=10_000.0))

    def test_refuses_a_retry_max_below_retry_base(self) -> None:
        with pytest.raises(forward.WeatherObservationsForwardConfigError, match="retry-max-seconds"):
            forward._validate_args(_args(retry_base_seconds=30.0, retry_max_seconds=5.0))


class TestNewestDayBuckets:
    def test_keeps_only_the_newest_max_days_buckets(self) -> None:
        empty = pa.table({"x": [1]})
        tables = {DAY_ONE: empty, DAY_TWO: empty, DAY_THREE: empty}

        kept = forward._newest_day_buckets(tables, max_days=2)

        assert sorted(kept) == [DAY_TWO, DAY_THREE]

    def test_max_days_one_keeps_only_todays_bucket_when_two_are_present(self) -> None:
        empty = pa.table({"x": [1]})
        tables = {DAY_ONE: empty, DAY_TWO: empty}

        kept = forward._newest_day_buckets(tables, max_days=1)

        assert list(kept) == [DAY_TWO]


def test_the_forward_writer_hands_its_availability_storage_to_every_day_it_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors `tests/parquet/test_direct_writers.py`'s water-gauges availability-tally contract."""
    storage = object()
    handed: list[object] = []

    async def record(*_args: object, **kwargs: object) -> tuple[str, int, int, int, None]:
        handed.append(kwargs.get("availability_storage"))
        return ("raised", 0, 0, 0, None)

    monkeypatch.setattr(forward, "fill_one_lane_day", record)
    table = WEATHER_OBSERVATIONS_SCHEMA.arrow_schema.empty_table()

    class _SessionDouble:
        async def rollback(self) -> None:
            return None

    result = asyncio.run(
        forward._publish_day(
            cast("Any", _SessionDouble()),
            ObjectStore(RecordingBackend()),
            day=DAY_ONE,
            table=table,
            run_id="weather-observations-forward-test",
            max_day_attempts=1,
            retry_base_seconds=0.0,
            retry_max_seconds=0.0,
            contention_timeout_seconds=0.0,
            availability_storage=cast("Any", storage),
        )
    )

    assert handed == [storage], "the lane-day path must receive this writer's own storage, not None"
    assert result.outcome == "raised"
