"""Retained Open-Meteo parser inputs survive a lost write and refuse a partial support grid.

No network and no object store: the checkpoint store is the in-memory availability storage the
`source_checkpoint` tests already use, and every body here is a literal provider response.
"""

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
    assert recovered.state == "recoverable_capture"
    assert recovered.recovered_points == 2
    assert [observation.observation["temperature"] for observation in recovered.observations] == [19.5, 17.25]


def test_an_unavailable_point_leaves_no_evidence_and_the_day_reads_as_a_retention_loss() -> None:
    checkpoints = _checkpoints()

    report = checkpoint_current_poll(_poll((POINTS[0], _body(19.5))), POINTS, checkpoints)
    recovered = recover_weather_day(date(2026, 9, 13), POINTS, checkpoints, now=FETCHED_AT)

    assert report.retained == 1
    assert report.attempted == 1
    assert recovered.state == "source_retention_loss"
    assert recovered.missing_or_rejected_points == 1


def test_a_capture_free_poll_retains_nothing_rather_than_inventing_a_body() -> None:
    report = checkpoint_current_poll(_poll((POINTS[0], None)), POINTS, _checkpoints())

    assert report.retained == 0
    assert report.failed == 1


def test_a_checkpoint_cannot_be_read_back_under_a_different_support_grid() -> None:
    checkpoints = _checkpoints()
    checkpoint_current_poll(_poll((POINTS[0], _body(19.5)), (POINTS[1], _body(17.25))), POINTS, checkpoints)

    recovered = recover_weather_day(date(2026, 9, 13), (POINTS[0],), checkpoints, now=FETCHED_AT)

    assert recovered.state == "source_retention_loss"
    assert recovered.recovered_points == 0


def test_a_corrupt_retained_body_is_a_rejection_rather_than_a_fabricated_reading() -> None:
    storage = MemoryAvailabilityStorage()
    checkpoints = SourceResponseCheckpoints(storage)
    checkpoint_current_poll(_poll((POINTS[0], b"{not json")), POINTS, checkpoints)

    recovered = recover_weather_day(date(2026, 9, 13), (POINTS[0],), checkpoints, now=FETCHED_AT)

    assert recovered.state == "source_retention_loss"
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
