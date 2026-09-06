"""The direct sensors source: the rolling window, not a fixed history floor.

`_rolling_window` is the one function that decides whether this writer can ever manufacture a
governed absence for a day NWS has already aged past its ~6-day retention -- see `source.py`'s
module docstring. These tests pin the window's math directly rather than only through an end-to-end
network poll, which this test suite deliberately never performs (see
`TestPollRecentSensorReadingsBboxGuard` for the one path testable without a live upstream call).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agri_data_service.ingest.sensors import NWS_OBSERVATION_RETENTION, NWS_SENSOR_SOURCE, nws_sensor_source
from agri_data_service.pipeline.direct.sensors.source import (
    SENSORS_ROLLING_WINDOW,
    SensorsSourceError,
    _rolling_window,
    poll_recent_sensor_readings,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class TestSensorsRollingWindow:
    def test_the_module_constant_matches_the_sources_own_declared_retention(self) -> None:
        """Never a re-typed `timedelta(days=6)` -- if `NWS_OBSERVATION_RETENTION` ever changes, this
        constant (and the `SENSORS_MAX_DAYS` ceiling `forward.py` derives from it) must move with it."""
        assert SENSORS_ROLLING_WINDOW == NWS_OBSERVATION_RETENTION

    def test_spans_exactly_the_retention_ending_at_the_run_clock(self) -> None:
        window = _rolling_window(NOW)

        assert window.start == NOW - NWS_OBSERVATION_RETENTION
        assert window.end == NOW

    def test_the_window_it_builds_is_provably_inside_the_sources_own_declared_capability(self) -> None:
        """Must not raise: the window is derived FROM `nws_sensor_source(now).history.earliest`
        itself, so this can never disagree with what the source declares it can serve."""
        window = _rolling_window(NOW)

        nws_sensor_source(NOW).history.require(NWS_SENSOR_SOURCE, window)

    def test_the_floor_moves_with_the_run_clock_rather_than_naming_a_fixed_date(self) -> None:
        """The defining property of a rolling floor: two calls a day apart produce two different
        windows, unlike `LANE_REGISTRY['sensors'].history_floor`, which is one fixed calendar date."""
        later = NOW + timedelta(days=1)

        assert _rolling_window(later).start == _rolling_window(NOW).start + timedelta(days=1)


class TestPollRecentSensorReadingsBboxGuard:
    def test_refuses_when_no_bbox_is_configured_before_ever_touching_the_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("INGEST_BBOX", raising=False)

        with pytest.raises(SensorsSourceError, match="no bbox configured"):
            asyncio.run(poll_recent_sensor_readings(None, None, now=NOW))  # type: ignore[arg-type]
