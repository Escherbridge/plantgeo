"""The forward CLI: bounded arguments, and why `--max-days`/`--max-records` mean what they mean here.

Mirrors `test_weather_observations_forward.py`'s shape, adjusted for the rolling-window ceiling
(seven days, not two) and the lane-specific `--max-records` knob `source.py` deliberately does not
inherit from the shared ingest ceiling.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.ingest.sensors import NWS_OBSERVATION_RETENTION
from agri_data_service.pipeline.direct.sensors import forward

DAY_ONE = date(2026, 9, 1)
DAY_TWO = date(2026, 9, 2)
DAY_THREE = date(2026, 9, 3)


def _args(**overrides: object) -> Any:
    defaults = {
        "max_days": 1,
        "max_records": forward.SENSORS_DEFAULT_MAX_RECORDS,
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


class TestSensorsMaxDays:
    def test_is_derived_from_the_sources_own_retention_constant_not_a_guessed_round_number(self) -> None:
        """A half-open six-day window can straddle at most seven distinct calendar dates."""
        assert NWS_OBSERVATION_RETENTION.days + 1 == forward.SENSORS_MAX_DAYS
        assert forward.SENSORS_MAX_DAYS == 7  # noqa: PLR2004 - pins the derived value, not a magic number

    def test_the_default_equals_the_ceiling_matching_the_cannot_lose_data_reasoning(self) -> None:
        assert forward.SENSORS_DEFAULT_MAX_DAYS == forward.SENSORS_MAX_DAYS


class TestParseArgs:
    def test_the_defaults_land_on_the_namespace_run_reads(self) -> None:
        parsed = forward.parse_args([])

        assert parsed.bbox is None
        assert parsed.max_days == forward.SENSORS_DEFAULT_MAX_DAYS
        assert parsed.max_records == forward.SENSORS_DEFAULT_MAX_RECORDS

    def test_a_bbox_whose_first_ordinate_is_negative_survives_two_argv_tokens(self) -> None:
        """THE TWO ARGV TOKENS ARE THE TEST, and every western-US bbox starts with a negative longitude.

        `--bbox` and its value arrive separately, exactly as a shell hands them over, and the value's
        leading `-125` is not a pure negative number -- so argparse reads it as another option and
        raises "argument --bbox: expected one argument" unless `parse_args` rewrites it to
        `--bbox=-125,...` first. Passing one pre-joined `--bbox=...` token here would exercise the
        CLI without exercising the guard, which is how four sibling writers shipped the same crash.
        """
        parsed = forward.parse_args(["--bbox", "-125,42,-116,49"])

        assert parsed.bbox == "-125,42,-116,49"


class TestValidateArgs:
    def test_accepts_the_parser_defaults(self) -> None:
        forward._validate_args(_args())  # must not raise

    def test_refuses_max_days_above_the_seven_day_structural_ceiling(self) -> None:
        with pytest.raises(forward.SensorsForwardConfigError, match="max-days"):
            forward._validate_args(_args(max_days=8))

    def test_refuses_zero_max_days(self) -> None:
        with pytest.raises(forward.SensorsForwardConfigError, match="max-days"):
            forward._validate_args(_args(max_days=0))

    def test_refuses_a_time_budget_above_the_ceiling(self) -> None:
        with pytest.raises(forward.SensorsForwardConfigError, match="time-budget"):
            forward._validate_args(_args(time_budget_seconds=10_000.0))

    def test_refuses_a_retry_max_below_retry_base(self) -> None:
        with pytest.raises(forward.SensorsForwardConfigError, match="retry-max-seconds"):
            forward._validate_args(_args(retry_base_seconds=30.0, retry_max_seconds=5.0))

    def test_refuses_max_records_below_the_floor(self) -> None:
        with pytest.raises(forward.SensorsForwardConfigError, match="max-records"):
            forward._validate_args(_args(max_records=10))

    def test_refuses_max_records_above_the_safety_ceiling(self) -> None:
        with pytest.raises(forward.SensorsForwardConfigError, match="max-records"):
            forward._validate_args(_args(max_records=10_000_000))


class TestNewestDayBuckets:
    def test_keeps_only_the_newest_max_days_buckets(self) -> None:
        empty = pa.table({"x": [1]})
        tables = {DAY_ONE: empty, DAY_TWO: empty, DAY_THREE: empty}

        kept = forward._newest_day_buckets(tables, max_days=2)

        assert sorted(kept) == [DAY_TWO, DAY_THREE]

    def test_max_days_one_keeps_only_the_newest_bucket(self) -> None:
        empty = pa.table({"x": [1]})
        tables = {DAY_ONE: empty, DAY_TWO: empty}

        kept = forward._newest_day_buckets(tables, max_days=1)

        assert list(kept) == [DAY_TWO]

    def test_a_full_seven_day_span_is_never_truncated_at_the_default_ceiling(self) -> None:
        empty = pa.table({"x": [1]})
        seven_days = {DAY_ONE + timedelta(days=offset): empty for offset in range(7)}

        kept = forward._newest_day_buckets(seven_days, max_days=forward.SENSORS_DEFAULT_MAX_DAYS)

        assert sorted(kept) == sorted(seven_days)
