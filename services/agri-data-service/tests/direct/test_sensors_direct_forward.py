"""The forward CLI: bounded arguments, why `--max-days`/`--max-records` mean what they mean, and the bucket verdict.

Mirrors `test_weather_observations_forward.py`'s shape, adjusted for the rolling-window ceiling
(seven days, not two) and the lane-specific `--max-records` knob `source.py` deliberately does not
inherit from the shared ingest ceiling. `TestBucketVerdict` is this lane's own: the exit code fails
only when NO day wrote, because "every day or exit 1" held the breaker for a week over two refused
days in buckets whose other five had written (`forward.py`'s module docstring).
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.ingest.sensors import NWS_OBSERVATION_RETENTION
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.sensors import forward
from agri_data_service.pipeline.direct.sensors.adapter import OverturnedAbsence, RetractedAbsenceMarker
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY_ONE = date(2026, 9, 1)
DAY_TWO = date(2026, 9, 2)
DAY_THREE = date(2026, 9, 3)
REFUSAL = "DirectSensorsError: refusing to merge poll rows into sensors z13 2026-09-02 with status=conflict"


def _result(
    day: date,
    outcome: str,
    *,
    detail: str | None = None,
    absence_overturned: OverturnedAbsence | None = None,
) -> forward.ForwardDayResult:
    """One day's publication outcome with the counters a verdict does not read zeroed."""
    return forward.ForwardDayResult(
        day=day,
        outcome=outcome,
        attempts=1,
        incoming_rows=3,
        existing_rows=0,
        added_rows=3 if outcome == "written" else 0,
        updated_rows=0,
        stale_rows=0,
        merged_rows=3 if outcome == "written" else 0,
        actual_z13_rows=3 if outcome == "written" else 0,
        merged_rows_verified=3 if outcome == "written" else 0,
        parts=1 if outcome == "written" else 0,
        rows=3 if outcome == "written" else 0,
        written_bytes=0,
        detail=detail,
        absence_overturned=absence_overturned,
    )


def _overturned(day: date) -> OverturnedAbsence:
    return OverturnedAbsence(
        day=day,
        overturned_by_run_id="sensors-direct-forward-test",
        incoming_rows=3,
        markers=(
            RetractedAbsenceMarker(
                tier=LANE_BASE_ZOOM_TIER,
                absence=GovernedAbsence(
                    reason="the sensors export for this day produced zero rows from geo.features",
                    upstream_response='{"producer": "pipeline/lanes/sensors.py", "rows": 0}',
                    recorded_at=datetime(2026, 9, 6, 1, 20, tzinfo=UTC),
                    run_id="pipeline/lanes/sensors.py:export_sensors_day:20260906T012000Z",
                ),
            ),
        ),
    )


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
        assert forward.SENSORS_MAX_DAYS == 7

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


class TestBucketVerdict:
    """The exit code says whether the lane can write AT ALL; `outcome` says whether every day settled."""

    def test_every_day_written_is_complete_at_exit_zero(self) -> None:
        verdict = forward._bucket_verdict([_result(DAY_THREE, "written"), _result(DAY_TWO, "written")])

        assert verdict.outcome == "complete"
        assert verdict.exit_code == 0
        assert verdict.days_written == 2
        assert verdict.days_unwritten == 0
        assert verdict.unwritten == ()

    def test_a_mixed_bucket_is_incomplete_at_exit_zero_with_every_unwritten_day_named(self) -> None:
        """The 2026-09-09 bucket: five written, two refused, and the whole poll exited 1 -- never again."""
        results = [
            _result(DAY_THREE, "written"),
            _result(DAY_TWO, "raised", detail=REFUSAL),
            _result(DAY_ONE, "written"),
        ]

        verdict = forward._bucket_verdict(results)

        assert verdict.outcome == "incomplete", "a partial bucket may not pass for a clean success"
        assert verdict.exit_code == 0, "a partial bucket may not spend the lane's run on the breaker"
        assert verdict.days_written == 2
        assert verdict.days_unwritten == 1
        assert [result.day for result in verdict.unwritten] == [DAY_TWO]
        assert forward._unwritten_event(verdict.unwritten[0]) == {
            "day": "2026-09-02",
            "outcome": "raised",
            "attempts": 1,
            "incoming_rows": 3,
            "detail": REFUSAL,
        }

    def test_a_bucket_where_no_day_wrote_is_incomplete_at_exit_one(self) -> None:
        """Nothing published is the real failure the exit code exists to report."""
        results = [_result(DAY_TWO, "raised", detail=REFUSAL), _result(DAY_ONE, "raised", detail=REFUSAL)]

        verdict = forward._bucket_verdict(results)

        assert verdict.outcome == "incomplete"
        assert verdict.exit_code == 1
        assert verdict.days_written == 0
        assert verdict.days_unwritten == 2

    def test_days_that_ran_out_of_clock_or_lock_count_as_unwritten_not_failed(self) -> None:
        """The next hourly poll re-fetches the same rolling window, so an untouched day is owed, not lost."""
        results = [
            _result(DAY_THREE, "written"),
            _result(DAY_TWO, "contended", detail="another run holds this lane-day"),
            _result(DAY_ONE, "time_budget_exhausted", detail="time budget exhausted before this day was attempted"),
        ]

        verdict = forward._bucket_verdict(results)

        assert verdict.outcome == "incomplete"
        assert verdict.exit_code == 0
        assert [result.outcome for result in verdict.unwritten] == ["contended", "time_budget_exhausted"]

    def test_a_mixed_bucket_is_announced_on_stderr_and_a_clean_bucket_is_not(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """stderr is the only stream the executor tees today; a partial bucket must reach it (module docstring)."""
        forward._emit_bucket_incomplete(
            forward._bucket_verdict([_result(DAY_THREE, "written"), _result(DAY_TWO, "written")]), run_id="clean"
        )
        assert capsys.readouterr().err == ""

        forward._emit_bucket_incomplete(
            forward._bucket_verdict([_result(DAY_THREE, "written"), _result(DAY_TWO, "raised", detail=REFUSAL)]),
            run_id="mixed",
        )

        event = json.loads(capsys.readouterr().err.strip())
        assert event == {
            "event": "sensors_forward_bucket_incomplete",
            "run_id": "mixed",
            "outcome": "incomplete",
            "exit_code": 0,
            "days_written": 1,
            "days_unwritten": 1,
            "unwritten": [
                {"day": "2026-09-02", "outcome": "raised", "attempts": 1, "incoming_rows": 3, "detail": REFUSAL}
            ],
        }

    def test_overturned_absences_are_listed_by_day_whether_or_not_the_day_then_wrote(self) -> None:
        results = [
            _result(DAY_THREE, "written"),
            _result(DAY_TWO, "written", absence_overturned=_overturned(DAY_TWO)),
            _result(DAY_ONE, "raised", detail="post-write verification", absence_overturned=_overturned(DAY_ONE)),
        ]

        verdict = forward._bucket_verdict(results)

        assert verdict.absences_overturned == (DAY_TWO, DAY_ONE)


def test_a_retraction_followed_by_a_failed_write_is_still_reported_on_the_attempt_and_the_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The marker is gone the moment the adapter clears it; a later failure must not make that removal silent."""
    overturned = _overturned(DAY_ONE)

    async def fill_then_fail(
        _session: object, _store: object, lane: Any, **_kwargs: object
    ) -> tuple[str, int, int, int, str]:
        lane.adapter.absence_overturned = overturned
        return ("raised", 0, 0, 0, "DirectSensorsError: the write after the retraction failed")

    monkeypatch.setattr(forward, "fill_one_lane_day", fill_then_fail)

    class _SessionDouble:
        async def rollback(self) -> None:
            return None

    result = asyncio.run(
        forward._publish_day(
            cast("Any", _SessionDouble()),
            ObjectStore(RecordingBackend()),
            day=DAY_ONE,
            table=SENSORS_SCHEMA.arrow_schema.empty_table(),
            run_id="sensors-direct-forward-test",
            max_day_attempts=1,
            retry_base_seconds=0.0,
            retry_max_seconds=0.0,
            contention_timeout_seconds=0.0,
        )
    )

    assert result.outcome == "raised"
    assert result.absence_overturned is overturned
    attempt = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert attempt["event"] == "sensors_forward_attempt"
    assert attempt["absence_overturned"]["day"] == "2026-09-01"
    assert attempt["absence_overturned"]["markers"][0]["run_id"] == (
        "pipeline/lanes/sensors.py:export_sensors_day:20260906T012000Z"
    ), "the retired producer's provenance must reach the parsed report, not only stderr"
