"""The water-gauges forward driver's bucket verdict and its retraction reporting.

Mirrors `test_sensors_direct_forward.py::TestBucketVerdict`: the exit code fails only when NO
publisher day wrote. On this lane the hazard is live rather than historical -- one IV snapshot names
a publisher day per gauge `updatedAt`, so a single stale gauge can drag an old day into the bucket,
and that day's ambiguous-refresh refusal used to exit 1 over every other day's clean write
(`water_gauges_forward.py`'s module docstring).
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.water_gauges import OverturnedAbsence, RetractedAbsenceMarker
from agri_data_service.pipeline.parquet import water_gauges_forward as forward
from agri_data_service.pipeline.parquet.availability_extension import (
    AvailabilityExtensionOutcome,
    AvailabilityExtensionTally,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_SCHEMA
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    import pytest

DAY_ONE = date(2026, 9, 1)
DAY_TWO = date(2026, 9, 2)
DAY_THREE = date(2026, 9, 3)
REFUSAL = (
    "DirectWaterGaugesError: incoming NWIS grain ('13206000', ...) maps to 2 existing rows; "
    "refusing an ambiguous refresh so no historical duplicate is changed or dropped"
)
RETIRED_PRODUCER_RUN_ID = "pipeline/parquet/gap_fill.py:_fill_water_gauges:20260905T020000Z"


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
        recovered_duplicate_rows=0,
        merged_rows=3 if written else 0,
        actual_z13_rows=3 if written else 0,
        incoming_rows_verified=3 if written else 0,
        parts=1 if written else 0,
        rows=3 if written else 0,
        written_bytes=0,
        detail=detail,
        absence_overturned=absence_overturned,
    )


def _overturned(day: date) -> OverturnedAbsence:
    return OverturnedAbsence(
        day=day,
        overturned_by_run_id="water-gauges-nwis-forward-test",
        incoming_rows=3,
        markers=(
            RetractedAbsenceMarker(
                tier=LANE_BASE_ZOOM_TIER,
                absence=GovernedAbsence(
                    reason="the water-gauges export for this day produced zero rows from geo.features",
                    upstream_response='{"producer": "gap_fill._fill_water_gauges", "rows": 0}',
                    recorded_at=datetime(2026, 9, 5, 2, 0, tzinfo=UTC),
                    run_id=RETIRED_PRODUCER_RUN_ID,
                ),
            ),
        ),
    )


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
        """One stale gauge's old publisher day refused; today's and yesterday's clean writes must still count."""
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

    def test_days_that_ran_out_of_lock_count_as_unwritten_not_failed(self) -> None:
        """The next quarter-hourly fetch re-reads the same snapshot, so an untouched day is owed, not lost."""
        results = [
            _result(DAY_THREE, "written"),
            _result(DAY_TWO, "contended", detail="contention did not clear within 900s: another run holds this day"),
        ]

        verdict = forward._bucket_verdict(results)

        assert verdict.outcome == "incomplete"
        assert verdict.exit_code == 0
        assert [result.outcome for result in verdict.unwritten] == ["contended"]

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
        assert event["event"] == "water_gauges_forward_bucket_incomplete"
        assert event["run_id"] == "run"
        assert event["exit_code"] == 0
        assert event["days_written"] == 1
        assert event["days_unwritten"] == 1
        assert event["unwritten"] == [forward._unwritten_event(verdict.unwritten[0])]

    def test_a_clean_bucket_announces_nothing_on_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        verdict = forward._bucket_verdict([_result(DAY_THREE, "written"), _result(DAY_TWO, "written")])

        forward._report_bucket_incomplete("run", verdict)

        assert capsys.readouterr().err == ""


def test_a_retraction_followed_by_a_failed_write_is_still_reported_on_the_attempt_and_the_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The marker is gone the moment the adapter clears it; a later failure must not make that removal silent."""
    overturned = _overturned(DAY_ONE)

    async def fill_then_fail(
        _session: object, _store: object, lane: Any, **_kwargs: object
    ) -> tuple[str, int, int, int, str]:
        lane.adapter.absence_overturned = overturned
        return ("raised", 0, 0, 0, "DirectWaterGaugesError: the write after the retraction failed")

    monkeypatch.setattr(forward, "fill_one_lane_day", fill_then_fail)

    class _SessionDouble:
        async def rollback(self) -> None:
            return None

    result = asyncio.run(
        forward._publish_day(
            cast("Any", _SessionDouble()),
            ObjectStore(RecordingBackend()),
            day=DAY_ONE,
            table=WATER_GAUGES_SCHEMA.arrow_schema.empty_table(),
            run_id="water-gauges-nwis-forward-test",
            max_day_attempts=1,
            retry_base_seconds=0.0,
            contention_poll_seconds=0.0,
            contention_timeout_seconds=0.0,
        )
    )

    assert result.outcome == "raised"
    assert result.absence_overturned is overturned
    attempt = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert attempt["event"] == "water_gauges_forward_attempt"
    assert attempt["absence_overturned"]["day"] == "2026-09-01"
    assert attempt["absence_overturned"]["markers"][0]["run_id"] == RETIRED_PRODUCER_RUN_ID, (
        "the retired producer's provenance must reach the parsed report, not only stderr"
    )


def test_forward_self_drains_a_bounded_availability_batch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}
    owed = AvailabilityExtensionOutcome(
        state="extended",
        lane_root="layer=water-gauges/kind=observed",
        day=DAY_ONE,
        reason="recovered",
    )

    async def retry(_session: object, _store: object, **kwargs: object) -> tuple[AvailabilityExtensionOutcome, ...]:
        captured.update(kwargs)
        return (owed,)

    monkeypatch.setattr(forward, "retry_pending_availability", retry)
    tally = AvailabilityExtensionTally()

    count = asyncio.run(
        forward._retry_owed_availability(
            cast("Any", object()),
            cast("Any", object()),
            cast("Any", object()),
            tally,
        )
    )

    assert count == 1
    assert captured["lane"] == "water-gauges"
    assert captured["kind"] == "observed"
    assert captured["max_days"] == forward.MAX_AVAILABILITY_RETRIES_PER_TURN
    assert tally.extended == 1
    event = json.loads(capsys.readouterr().out.strip())
    assert event["event"] == "water_gauges_forward_availability_retry"


def test_forward_availability_drain_failure_never_blocks_source_publication(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def retry(*_args: object, **_kwargs: object) -> tuple[AvailabilityExtensionOutcome, ...]:
        raise RuntimeError("temporary read fault")

    monkeypatch.setattr(forward, "retry_pending_availability", retry)

    count = asyncio.run(
        forward._retry_owed_availability(
            cast("Any", object()),
            cast("Any", object()),
            cast("Any", object()),
            AvailabilityExtensionTally(),
        )
    )

    assert count == 0
    event = json.loads(capsys.readouterr().out.strip())
    assert event["event"] == "water_gauges_forward_availability_retry_failed"
    assert event["detail"] == "RuntimeError: temporary read fault"
