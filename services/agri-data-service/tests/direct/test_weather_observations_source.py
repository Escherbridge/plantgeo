"""The current-conditions poll: one point's failure or staleness never discards the rest of the grid."""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import agri_data_service.pipeline.direct.weather_observations.source as source_module
from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.pipeline.direct.weather_observations.source import poll_current_conditions

if TYPE_CHECKING:
    import pytest

NOW = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
POINTS = [(46.0, -117.0), (47.0, -116.0), (48.0, -115.0)]


def _observation(temperature: float) -> dict[str, object]:
    return {
        "observedAt": "2026-09-03T17:58:00.000Z",
        "temperature": temperature,
        "humidity": 40.0,
        "windSpeed": 3.0,
        "windDirection": 180.0,
        "precipitation": 0.0,
    }


def test_every_successful_point_is_kept_in_request_order(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_current_weather(
        _client: object, latitude: float, _longitude: float, _now: datetime | None
    ) -> dict[str, object]:
        return _observation(temperature=latitude)

    monkeypatch.setattr(source_module, "get_current_weather", fake_get_current_weather)

    result = asyncio.run(poll_current_conditions(object(), POINTS, now=NOW))

    assert result.points_sampled == len(POINTS)
    assert result.unavailable_points == 0
    assert [point.latitude for point in result.observations] == [46.0, 47.0, 48.0]
    assert result.fetched_at == NOW


def test_one_points_failure_does_not_discard_the_rest_of_the_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    async def flaky(_client: object, latitude: float, _longitude: float, _now: datetime | None) -> dict[str, object]:
        if latitude == 47.0:
            raise UpstreamPayloadError("Open-Meteo returned a stale current observation")
        return _observation(temperature=latitude)

    monkeypatch.setattr(source_module, "get_current_weather", flaky)

    result = asyncio.run(poll_current_conditions(object(), POINTS, now=NOW))

    assert result.unavailable_points == 1
    assert [point.latitude for point in result.observations] == [46.0, 48.0]


def test_every_point_unavailable_returns_zero_observations_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def always_fails(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise UpstreamPayloadError("Open-Meteo returned an invalid current observation")

    monkeypatch.setattr(source_module, "get_current_weather", always_fails)

    result = asyncio.run(poll_current_conditions(object(), POINTS, now=NOW))

    assert result.observations == ()
    assert result.unavailable_points == len(POINTS)
