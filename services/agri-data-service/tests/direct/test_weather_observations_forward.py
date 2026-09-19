"""The forward CLI: bounded arguments, why `--max-days` caps buckets rather than a backlog walk, and the bucket verdict.

`TestBucketVerdict` mirrors `test_sensors_direct_forward.py`'s: the exit code fails only when NO day
wrote, because "every day or exit 1" held that lane's breaker for a week over refused days in buckets
whose other days had written (`forward.py`'s module docstring). This lane's bucket is at most two days,
so one refused yesterday-bucket used to fail a cleanly written today-bucket with it.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.weather_observations import forward
from agri_data_service.pipeline.direct.weather_observations.adapter import OverturnedAbsence, RetractedAbsenceMarker
from agri_data_service.pipeline.direct.weather_observations.source import WeatherPointObservation
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.weather_observations import WEATHER_OBSERVATIONS_SCHEMA
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY_ONE = date(2026, 9, 1)
DAY_TWO = date(2026, 9, 2)
DAY_THREE = date(2026, 9, 3)
REFUSAL = (
    "DirectWeatherObservationsError: refusing to merge poll rows into weather-observations z13 2026-09-02 "
    "with status=conflict"
)
RETIRED_PRODUCER_RUN_ID = "pipeline/lanes/weather_observations.py:export_weather_observations_day:20260905T020000Z"


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


def _result(
    day: date,
    outcome: str,
    *,
    detail: str | None = None,
    absence_overturned: OverturnedAbsence | None = None,
) -> forward.ForwardDayResult:
    """One day's publication outcome with the counters a verdict does not read zeroed."""
    written = outcome == "written"
    return forward.ForwardDayResult(
        day=day,
        outcome=outcome,
        attempts=1,
        incoming_rows=3,
        existing_rows=0,
        added_rows=3 if written else 0,
        updated_rows=0,
        merged_rows=3 if written else 0,
        actual_z13_rows=3 if written else 0,
        incoming_rows_verified=3 if written else 0,
        parts=1 if written else 0,
        rows=3 if written else 0,
        written_bytes=0,
        detail=detail,
        absence_overturned=absence_overturned,
    )


def _observation(latitude: float, longitude: float, observed_at: str) -> WeatherPointObservation:
    """One accepted reading, identified by exactly the published grain a merge deduplicates on."""
    return WeatherPointObservation(
        latitude=latitude,
        longitude=longitude,
        observation={"observedAt": observed_at, "temperature": 19.5},
    )


@dataclass(frozen=True)
class _PollDouble:
    """Only the two members `_retain_current_poll` reads; the real poll needs a live HTTP client."""

    observations: tuple[WeatherPointObservation, ...]
    fetched_at: datetime = datetime(2026, 9, 3, 18, tzinfo=UTC)


def _overturned(day: date) -> OverturnedAbsence:
    return OverturnedAbsence(
        day=day,
        overturned_by_run_id="weather-observations-direct-forward-test",
        incoming_rows=3,
        markers=(
            RetractedAbsenceMarker(
                tier=LANE_BASE_ZOOM_TIER,
                absence=GovernedAbsence(
                    reason="the ingest cron never ran this day",
                    upstream_response='{"producer": "pipeline/lanes/weather_observations.py", "rows": 0}',
                    recorded_at=datetime(2026, 9, 5, 2, 0, tzinfo=UTC),
                    run_id=RETIRED_PRODUCER_RUN_ID,
                ),
            ),
        ),
    )


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
        """A written today-bucket and a refused yesterday-bucket: the poll no longer exits 1 for it."""
        results = [_result(DAY_THREE, "written"), _result(DAY_TWO, "raised", detail=REFUSAL)]

        verdict = forward._bucket_verdict(results)

        assert verdict.outcome == "incomplete", "a partial bucket may not pass for a clean success"
        assert verdict.exit_code == 0, "a partial bucket may not spend the lane's run on the breaker"
        assert verdict.days_written == 1
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
        """The next poll re-buckets the same rolling instant, so an untouched day is owed, not lost."""
        results = [
            _result(DAY_THREE, "written"),
            _result(DAY_TWO, "contended", detail="another run holds this lane-day"),
            _result(DAY_ONE, "time_budget_exhausted", detail="time budget exhausted before this day was attempted"),
        ]

        verdict = forward._bucket_verdict(results)

        assert verdict.outcome == "incomplete"
        assert verdict.exit_code == 0
        assert [result.outcome for result in verdict.unwritten] == ["contended", "time_budget_exhausted"]

    def test_overturned_absences_are_listed_by_day_whether_or_not_the_day_then_wrote(self) -> None:
        results = [
            _result(DAY_THREE, "written"),
            _result(DAY_TWO, "written", absence_overturned=_overturned(DAY_TWO)),
            _result(DAY_ONE, "raised", detail="post-write verification", absence_overturned=_overturned(DAY_ONE)),
        ]

        verdict = forward._bucket_verdict(results)

        assert verdict.absences_overturned == (DAY_TWO, DAY_ONE)

    def test_a_mixed_bucket_announces_its_unwritten_days_on_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Exit 0 is silent to the executor, so the degradation must reach stderr on its own."""
        verdict = forward._bucket_verdict([_result(DAY_THREE, "written"), _result(DAY_TWO, "raised", detail=REFUSAL)])

        forward._report_bucket_incomplete("run", verdict)

        event = json.loads(capsys.readouterr().err.strip())
        assert event["event"] == "weather_observations_forward_bucket_incomplete"
        assert event["run_id"] == "run"
        assert event["exit_code"] == 0
        assert event["days_written"] == 1
        assert event["days_unwritten"] == 1
        assert event["unwritten"] == [forward._unwritten_event(verdict.unwritten[0])]

    def test_a_clean_bucket_announces_nothing_on_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        verdict = forward._bucket_verdict([_result(DAY_THREE, "written"), _result(DAY_TWO, "written")])

        forward._report_bucket_incomplete("run", verdict)

        assert capsys.readouterr().err == ""


class _SessionDouble:
    async def rollback(self) -> None:
        return None


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
    assert result.absence_overturned is None


def test_a_retraction_followed_by_a_failed_write_is_still_reported_on_the_attempt_and_the_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The marker is gone the moment the adapter clears it; a later failure must not make that removal silent."""
    overturned = _overturned(DAY_ONE)

    async def fill_then_fail(
        _session: object, _store: object, lane: Any, **_kwargs: object
    ) -> tuple[str, int, int, int, str]:
        lane.adapter.absence_overturned = overturned
        return ("raised", 0, 0, 0, "DirectWeatherObservationsError: the write after the retraction failed")

    monkeypatch.setattr(forward, "fill_one_lane_day", fill_then_fail)

    result = asyncio.run(
        forward._publish_day(
            cast("Any", _SessionDouble()),
            ObjectStore(RecordingBackend()),
            day=DAY_ONE,
            table=WEATHER_OBSERVATIONS_SCHEMA.arrow_schema.empty_table(),
            run_id="weather-observations-direct-forward-test",
            max_day_attempts=1,
            retry_base_seconds=0.0,
            retry_max_seconds=0.0,
            contention_timeout_seconds=0.0,
        )
    )

    assert result.outcome == "raised"
    assert result.absence_overturned is overturned
    attempt = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert attempt["event"] == "weather_observations_forward_attempt"
    assert attempt["absence_overturned"]["day"] == "2026-09-01"
    assert attempt["absence_overturned"]["markers"][0]["run_id"] == RETIRED_PRODUCER_RUN_ID, (
        "the retired producer's provenance must reach the parsed report, not only stderr"
    )


class TestRetentionIsNotAdvisory:
    """A safety net whose failure changes no outcome reads as cover the lane does not have."""

    def test_a_fully_written_bucket_whose_retention_failed_is_not_complete(self) -> None:
        verdict = forward._bucket_verdict([_result(DAY_THREE, "written")], retention_failed=2)

        assert verdict.outcome == "incomplete", "a poll that retained nothing may not pass for a clean success"
        assert verdict.exit_code == 0, "publishing rows already in hand must not be abandoned over a failed backup"
        assert verdict.days_written == 1
        assert verdict.retention_failed == 2

    def test_a_retention_failure_alone_still_reaches_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        verdict = forward._bucket_verdict([_result(DAY_THREE, "written")], retention_failed=1)

        forward._report_bucket_incomplete("run", verdict)

        event = json.loads(capsys.readouterr().err.strip())
        assert event["event"] == "weather_observations_forward_bucket_incomplete"
        assert event["source_checkpoints_failed"] == 1
        assert event["unwritten"] == []

    def test_an_unwritten_day_names_whether_its_source_survived(self) -> None:
        """The difference between a bucket the next turn can repair and one the rolling feed has lost."""
        lost = replace(_result(DAY_TWO, "raised", detail=REFUSAL), source_retained=False)

        assert forward._unwritten_event(lost)["source_retained"] is False

    def test_an_unmeasured_day_says_nothing_rather_than_guessing(self) -> None:
        assert "source_retained" not in forward._unwritten_event(_result(DAY_TWO, "raised", detail=REFUSAL))


class TestRecoveryWiring:
    """`recover_weather_day` had no caller until wave 10; these pin the paths that now call it."""

    def test_only_days_without_a_complete_publication_are_probed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        statuses = {DAY_THREE: {13: "data", 12: "data"}, DAY_TWO: {13: "data", 12: "missing"}}
        monkeypatch.setattr(forward, "_tier_statuses", lambda _store, day: statuses[day])

        owed, deferred = forward._days_owed_a_recovery(cast("Any", None), (DAY_THREE, DAY_TWO))

        assert owed == (DAY_TWO,), "a day already data at every tier is not re-merged from checkpoints"
        assert deferred == (), "an unbounded probe defers nothing"

    def test_a_recovered_reading_the_poll_already_carries_is_not_offered_twice(self) -> None:
        """`merge_weather_observations_day` refuses one poll offering a grain twice."""
        live = _observation(45.5, -122.6, "2026-09-03T18:00:00.000Z")
        same = _observation(45.5, -122.6, "2026-09-03T18:00:00.000Z")
        earlier = _observation(45.5, -122.6, "2026-09-03T17:00:00.000Z")

        combined = forward._with_recovered_observations((live,), (same, earlier))

        assert combined == (live, earlier)

    def test_recovery_adds_a_point_the_current_poll_never_answered(self) -> None:
        live = _observation(45.5, -122.6, "2026-09-03T18:00:00.000Z")
        other_point = _observation(47.6, -122.3, "2026-09-03T18:00:00.000Z")

        assert forward._with_recovered_observations((live,), (other_point,)) == (live, other_point)


class TestTheProbeCannotCostThePollItsRetention:
    """STYLE-REVIEW-W10 B2: the repair ran ahead of the retention UNWRAPPED and OUTSIDE the deadline.

    A rolling feed keeps no archive, so a turn that dies in the probe loses the instants it was
    holding for good. Every case here is about the poll surviving the probe, not about the repair
    succeeding.
    """

    def test_a_store_fault_in_the_probe_is_a_report_rather_than_an_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(_store: object, _day: date) -> dict[int, str]:
            raise OSError("R2 returned 503 for list_partition_keys")

        monkeypatch.setattr(forward, "_tier_statuses", explode)

        phase = asyncio.run(
            forward._repair_owed_days(
                cast("Any", None),
                cast("Any", None),
                (DAY_THREE, DAY_TWO),
                ((45.5, -122.6),),
                now=datetime(2026, 9, 3, 18, tzinfo=UTC),
                deadline=time.monotonic() + 60.0,
            )
        )

        assert phase.state == "failed", "a probe fault must not unwind into main()'s catch-all"
        assert phase.error_type == "OSError"
        assert phase.observations == ()
        assert phase.days_deferred == (DAY_THREE, DAY_TWO), "the days it never repaired are named, not dropped"
        assert phase.degraded is True

    def test_a_spent_budget_skips_the_probe_entirely_rather_than_spending_the_turn(self) -> None:
        phase = asyncio.run(
            forward._repair_owed_days(
                cast("Any", None),
                cast("Any", None),
                (DAY_THREE,),
                ((45.5, -122.6),),
                now=datetime(2026, 9, 3, 18, tzinfo=UTC),
                deadline=time.monotonic() - 1.0,
            )
        )

        assert phase.state == "skipped_no_budget"
        assert phase.days_deferred == (DAY_THREE,)

    def test_the_probe_defers_a_day_it_cannot_afford_to_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Checked BEFORE each day's listing, so a slow bucket stops costing the turn at the first overrun."""
        monkeypatch.setattr(forward, "_tier_statuses", lambda _store, _day: {13: "missing"})

        owed, deferred = forward._days_owed_a_recovery(
            cast("Any", None), (DAY_THREE, DAY_TWO), probe_deadline=time.monotonic() - 1.0
        )

        assert owed == ()
        assert deferred == (DAY_THREE, DAY_TWO), "unreached is not not-owed"

    def test_a_degraded_probe_costs_the_turn_its_complete_but_not_its_exit_code(self) -> None:
        verdict = forward._bucket_verdict([_result(DAY_THREE, "written")], recovery_degraded=True)

        assert verdict.outcome == "incomplete", "this turn's retention overwrote what the probe would have read"
        assert verdict.exit_code == 0, "a repair that did not happen is not a lane that cannot write"
        assert verdict.recovery_degraded is True

    def test_a_degraded_probe_alone_still_reaches_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        verdict = forward._bucket_verdict([_result(DAY_THREE, "written")], recovery_degraded=True)

        forward._report_bucket_incomplete("run", verdict)

        event = json.loads(capsys.readouterr().err.strip())
        assert event["recovery_degraded"] is True
        assert event["unwritten"] == []

    def test_a_retention_fault_leaves_the_poll_publishable_and_says_every_point_is_unretained(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Retention is the BACKUP; dropping the rows it backs up is the inversion it exists to prevent."""

        def explode(*_args: object, **_kwargs: object) -> object:
            raise OSError("R2 returned 503 for the checkpoint write")

        monkeypatch.setattr(forward, "checkpoint_current_poll", explode)
        poll = cast("Any", _PollDouble((_observation(45.5, -122.6, "2026-09-03T18:00:00.000Z"),)))

        report = asyncio.run(forward._retain_current_poll(poll, ((45.5, -122.6),), cast("Any", None), run_id="run"))

        assert report.failed == 1, "an unretained point is reported as unretained, never as retained"
        assert report.retained == 0
        assert report.retained_whole_day(DAY_THREE) is False
        event = json.loads(capsys.readouterr().out.strip())
        assert event["event"] == "weather_observations_source_retention_failed"
        assert event["error_type"] == "OSError"


class TestRecoverDayArgument:
    """`--help` is the only contract an operator reads before running the repair."""

    def test_the_flag_parses_as_an_iso_date_and_defaults_to_none(self) -> None:
        assert forward.parse_args(["--recover-day", "2026-09-03"]).recover_day == DAY_THREE
        assert forward.parse_args([]).recover_day is None

    def test_the_help_text_says_it_republishes_from_retained_responses(self) -> None:
        # Whitespace-collapsed because argparse WRAPS help into a column, so the phrase an operator
        # reads on one visual line is split by a newline and an indent in the raw string.
        help_text = " ".join(forward.parser().format_help().split())

        assert "--recover-day" in help_text
        assert "RETAINED PROVIDER RESPONSES" in help_text

    def test_a_future_day_is_refused_because_this_feed_has_no_forecast(self) -> None:
        with pytest.raises(forward.WeatherObservationsForwardConfigError, match="in the future"):
            forward._validate_args(_args(recover_day=date(2026, 9, 4)), today=DAY_THREE)

    def test_a_day_past_the_checkpoint_retention_window_is_refused_by_name(self) -> None:
        with pytest.raises(forward.WeatherObservationsForwardConfigError, match="retention window"):
            forward._validate_args(_args(recover_day=date(2026, 8, 1)), today=DAY_THREE)

    def test_a_day_inside_the_window_is_accepted(self) -> None:
        forward._validate_args(_args(recover_day=DAY_ONE), today=DAY_THREE)
