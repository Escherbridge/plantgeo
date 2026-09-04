"""The weather-observations sample grid: deterministic per bbox, and refuses when unconfigured."""

from __future__ import annotations

import pytest

from agri_data_service.pipeline.direct.weather_observations.support import (
    WeatherSupportError,
    weather_sample_points,
)

BBOX = "-117.0,46.0,-116.0,47.0"


def test_the_same_bbox_produces_the_identical_grid_every_call() -> None:
    """A repeat poll must be able to re-derive the identical grain, or every merge would see churn."""
    first = weather_sample_points(BBOX)
    second = weather_sample_points(BBOX)

    assert first == second
    assert len(first) > 0


def test_refuses_when_no_bbox_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGEST_BBOX", raising=False)

    with pytest.raises(WeatherSupportError, match="bbox"):
        weather_sample_points(None)


def test_every_point_lies_within_the_requested_bbox() -> None:
    west, south, east, north = -117.0, 46.0, -116.0, 47.0

    for latitude, longitude in weather_sample_points(BBOX):
        assert west <= longitude <= east
        assert south <= latitude <= north
