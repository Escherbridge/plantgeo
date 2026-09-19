"""Retained Open-Meteo parser inputs survive a lost write and refuse a partial support grid.

No network and no object store: the checkpoint store is the in-memory availability storage the
`source_checkpoint` tests already use, and every body here is a literal provider response.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from agri_data_service.pipeline.direct.weather_observations.recovery import (
    checkpoint_current_poll,
    recover_weather_day,
    weather_checkpoint_identity,
    weather_support_sha256,
)
from agri_data_service.pipeline.direct.weather_observations.source import (
    WeatherPointObservation,
    WeatherPollResult,
)
from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints
from tests.parquet.test_availability_index import MemoryAvailabilityStorage

FETCHED_AT = datetime(2026, 9, 13, 18, 30, tzinfo=UTC)
OBSERVED_UNIX = int(datetime(2026, 9, 13, 18, 0, tzinfo=UTC).timestamp())
POINTS: tuple[tuple[float, float], ...] = ((45.5, -122.6), (47.6, -122.3))


def _body(temperature: float) -> bytes:
    """One provider response, spacing and all: the bytes are the evidence, not a re-render."""
    return json.dumps(
        {
            "current": {
                "time": OBSERVED_UNIX,
                "temperature_2m": temperature,
                "relative_humidity_2m": 55.0,
                "wind_speed_10m": 3.5,
                "wind_direction_10m": 180.0,
                "precipitation": 0.0,
            }
        }
    ).encode("utf-8")


def _poll(*captured: tuple[tuple[float, float], bytes | None]) -> WeatherPollResult:
    observations = tuple(
        WeatherPointObservation(
            latitude=point[0],
            longitude=point[1],
            observation={"observedAt": "2026-09-13T18:00:00.000Z"},
            response_body=body,
        )
        for point, body in captured
    )
    return WeatherPollResult(
        fetched_at=FETCHED_AT,
        points_sampled=len(POINTS),
        observations=observations,
        unavailable_points=len(POINTS) - len(observations),
    )


def _checkpoints() -> SourceResponseCheckpoints:
    return SourceResponseCheckpoints(MemoryAvailabilityStorage())


def test_a_full_poll_is_retained_and_reparses_into_the_same_observations() -> None:
    checkpoints = _checkpoints()
    poll = _poll((POINTS[0], _body(19.5)), (POINTS[1], _body(17.25)))

    report = checkpoint_current_poll(poll, POINTS, checkpoints)
    recovered = recover_weather_day(date(2026, 9, 13), POINTS, checkpoints, now=FETCHED_AT)

    assert report.to_summary() == {
        "source_checkpoints_attempted": 2,
        "source_checkpoints_retained": 2,
        "source_checkpoints_failed": 0,
    }
    assert recovered.state == "complete_capture"
    assert recovered.recovered_points == 2
    assert [observation.observation["temperature"] for observation in recovered.observations] == [19.5, 17.25]


def test_an_unavailable_point_leaves_no_evidence_and_the_day_reads_as_a_partial_capture() -> None:
    """Half a grid of readings is worth republishing; the alternative to a partial one is none."""
    checkpoints = _checkpoints()

    report = checkpoint_current_poll(_poll((POINTS[0], _body(19.5))), POINTS, checkpoints)
    recovered = recover_weather_day(date(2026, 9, 13), POINTS, checkpoints, now=FETCHED_AT)

    assert report.retained == 1
    assert report.attempted == 1
    assert recovered.state == "partial_capture"
    assert recovered.missing_or_rejected_points == 1
    assert recovered.recovered_points == 1


def test_a_capture_free_poll_retains_nothing_rather_than_inventing_a_body() -> None:
    report = checkpoint_current_poll(_poll((POINTS[0], None)), POINTS, _checkpoints())

    assert report.retained == 0
    assert report.failed == 1


def test_a_checkpoint_cannot_be_read_back_under_a_different_support_grid() -> None:
    checkpoints = _checkpoints()
    checkpoint_current_poll(_poll((POINTS[0], _body(19.5)), (POINTS[1], _body(17.25))), POINTS, checkpoints)

    recovered = recover_weather_day(date(2026, 9, 13), (POINTS[0],), checkpoints, now=FETCHED_AT)

    assert recovered.state == "no_retained_capture"
    assert recovered.recovered_points == 0


def test_a_corrupt_retained_body_is_a_rejection_rather_than_a_fabricated_reading() -> None:
    storage = MemoryAvailabilityStorage()
    checkpoints = SourceResponseCheckpoints(storage)
    checkpoint_current_poll(_poll((POINTS[0], b"{not json")), POINTS, checkpoints)

    recovered = recover_weather_day(date(2026, 9, 13), (POINTS[0],), checkpoints, now=FETCHED_AT)

    assert recovered.state == "no_retained_capture"
    assert recovered.observations == ()


def test_the_support_digest_refuses_an_empty_or_duplicated_grid() -> None:
    with pytest.raises(ValueError, match="nonempty and unique"):
        weather_support_sha256(())
    with pytest.raises(ValueError, match="nonempty and unique"):
        weather_support_sha256((POINTS[0], POINTS[0]))


def test_each_point_and_day_gets_its_own_operational_checkpoint_key() -> None:
    digest = weather_support_sha256(POINTS)
    first = weather_checkpoint_identity(POINTS[0], day=date(2026, 9, 13), support_sha256=digest)
    second = weather_checkpoint_identity(POINTS[1], day=date(2026, 9, 13), support_sha256=digest)
    next_day = weather_checkpoint_identity(POINTS[0], day=date(2026, 9, 14), support_sha256=digest)

    assert len({first.key, second.key, next_day.key}) == 3
    assert first.key.startswith("source-response-checkpoints/v1/")


def _straddling_poll() -> WeatherPollResult:
    """One poll whose two points name days on either side of UTC midnight -- `forward.py:108-110`'s case."""
    return WeatherPollResult(
        fetched_at=FETCHED_AT,
        points_sampled=2,
        observations=(
            WeatherPointObservation(
                latitude=POINTS[0][0],
                longitude=POINTS[0][1],
                observation={"observedAt": "2026-09-13T23:50:00.000Z"},
                response_body=_body(19.5),
            ),
            WeatherPointObservation(
                latitude=POINTS[1][0],
                longitude=POINTS[1][1],
                observation={"observedAt": "2026-09-14T00:10:00.000Z"},
                response_body=_body(17.25),
            ),
        ),
        unavailable_points=0,
    )


def test_a_midnight_straddling_poll_is_counted_under_each_day_it_retained_into() -> None:
    """Each half is whole for ITS day; the old whole-grid verdict called both halves unpublishable."""
    report = checkpoint_current_poll(_straddling_poll(), POINTS, _checkpoints())

    assert report.retained == 2
    assert report.days[date(2026, 9, 13)].retained == 1
    assert report.days[date(2026, 9, 14)].retained == 1
    assert report.retained_whole_day(date(2026, 9, 13)) is True
    assert report.retained_whole_day(date(2026, 9, 14)) is True


def test_a_point_whose_body_never_arrived_is_charged_to_its_own_day() -> None:
    """Hiding it in the total alone would let `retained_whole_day` call that day whole."""
    report = checkpoint_current_poll(_poll((POINTS[0], _body(19.5)), (POINTS[1], None)), POINTS, _checkpoints())

    assert report.failed == 1
    assert report.days[date(2026, 9, 13)].failed == 1
    assert report.retained_whole_day(date(2026, 9, 13)) is False


def test_a_day_nobody_retained_anything_for_is_not_called_retained() -> None:
    """The ordinary first poll of a new UTC day: nothing to repair with, and nothing lost either."""
    report = checkpoint_current_poll(_poll((POINTS[0], _body(19.5))), POINTS, _checkpoints())

    assert report.retained_whole_day(date(2026, 9, 14)) is False


def test_a_retained_body_naming_another_day_is_rejected_rather_than_moved_onto_the_requested_one() -> None:
    """The body is the evidence. A checkpoint keyed under day D whose bytes parse to D-1 is not day D.

    `_straddling_poll`'s second point is keyed under 2026-09-14 by its `observedAt` while its literal
    provider bytes name the 18:00 instant of the 13th -- exactly the disagreement the day-derivation
    guard exists for, and the one that would otherwise publish a reading onto a day it never named.
    """
    checkpoints = _checkpoints()
    checkpoint_current_poll(_straddling_poll(), POINTS, checkpoints)

    thirteenth = recover_weather_day(date(2026, 9, 13), POINTS, checkpoints, now=FETCHED_AT)
    fourteenth = recover_weather_day(date(2026, 9, 14), POINTS, checkpoints, now=FETCHED_AT)

    assert thirteenth.recovered_points == 1
    assert [str(reading.observation["observedAt"])[:10] for reading in thirteenth.observations] == ["2026-09-13"]
    assert fourteenth.state == "no_retained_capture"
    assert fourteenth.missing_or_rejected_points == 2


def test_the_recovery_event_names_the_verdict_without_carrying_the_readings() -> None:
    checkpoints = _checkpoints()
    checkpoint_current_poll(_poll((POINTS[0], _body(19.5)), (POINTS[1], _body(17.25))), POINTS, checkpoints)

    event = recover_weather_day(date(2026, 9, 13), POINTS, checkpoints, now=FETCHED_AT).to_event()

    assert event == {
        "day": "2026-09-13",
        "state": "complete_capture",
        "support_points": 2,
        "recovered_points": 2,
        "missing_or_rejected_points": 0,
    }
