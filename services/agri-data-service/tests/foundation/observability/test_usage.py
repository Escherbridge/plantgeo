"""`usage.py`: host -> provider/pool resolution, the Open-Meteo weight table, and the usage line.

See its `AGENTS.md` entry "Usage line" for the envelope this pins down, and "Open-Meteo weight" for
why `open_meteo_request_weight` has no `models` factor despite the design record's general prose.
"""

from __future__ import annotations

import json

import pytest

from agri_data_service.foundation.observability import usage
from agri_data_service.foundation.observability.vocabulary import LANE_LOGICAL_CAPS
from agri_data_service.pipeline.direct.soil.source import open_meteo_request_weight as g0_weight

_SOIL_LOGICAL_CAP = 1602
_SAMPLE_TURN_PID = 4321
_STDERR_FD = 2

# --- Host -> provider/pool resolution --------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        pytest.param("api.open-meteo.com", usage.HostResolution("open-meteo", "open-meteo-free"), id="forecast-free"),
        pytest.param(
            "archive-api.open-meteo.com", usage.HostResolution("open-meteo", "open-meteo-free"), id="archive-free"
        ),
        pytest.param(
            "customer-archive-api.open-meteo.com",
            usage.HostResolution("open-meteo", "open-meteo-paid"),
            id="archive-paid",
        ),
        pytest.param("firms.modaps.eosdis.nasa.gov", usage.HostResolution("firms", "firms"), id="firms"),
        pytest.param(
            "waterservices.usgs.gov",
            usage.HostResolution("usgs-water-data", "usgs-water-data"),
            id="usgs-waterservices",
        ),
        # finding 5b: `api.waterdata.usgs.gov` is the modern USGS Water Data API host (D9), distinct
        # from the legacy `waterservices.usgs.gov` host already covered above; both resolve alike.
        pytest.param(
            "api.waterdata.usgs.gov",
            usage.HostResolution("usgs-water-data", "usgs-water-data"),
            id="usgs-waterdata-api",
        ),
        pytest.param("power.larc.nasa.gov", usage.HostResolution("nasa-power", "nasa-power"), id="nasa-power"),
        pytest.param("api.weather.gov", usage.HostResolution("nws", "nws"), id="nws"),
        pytest.param(
            "services.arcgis.com", usage.HostResolution("arcgis-online", "arcgis-online"), id="arcgis-online-bare"
        ),
        pytest.param(
            "services1.arcgis.com",
            usage.HostResolution("arcgis-online", "arcgis-online"),
            id="arcgis-online-numbered",
        ),
        pytest.param("example.com", None, id="unrecognised"),
    ],
)
def test_provider_and_pool_resolve_from_host(host: str, expected: usage.HostResolution | None) -> None:
    assert usage.provider_for_host(host) == expected


# --- Open-Meteo weight table (design §2.1's shape table) -------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # latitude=a,b,c -> 3 locations; start/end -> 14-day inclusive span; hourly=3 vars.
        (
            "https://archive-api.open-meteo.com/v1/archive"
            "?latitude=1,2,3&longitude=4,5,6&start_date=2020-01-01&end_date=2020-01-14&hourly=a,b,c",
            3 * max(1.0, 14 / 14) * max(1.0, 3 / 10),
        ),
        # No latitude at all -> 1 location assumed.
        (
            "https://archive-api.open-meteo.com/v1/archive?start_date=2020-01-01&end_date=2020-01-01&daily=a",
            1 * max(1.0, 1 / 14) * max(1.0, 1 / 10),
        ),
        # past_days + forecast_days, no dates.
        (
            "https://api.open-meteo.com/v1/forecast?latitude=1&past_days=7&forecast_days=7&hourly=a,b",
            1 * max(1.0, 14 / 14) * max(1.0, 2 / 10),
        ),
        # current= only, on the forecast endpoint, no past/forecast days given -> days=1.
        (
            "https://api.open-meteo.com/v1/forecast?latitude=1&current=temperature_2m,wind_speed_10m",
            1 * max(1.0, 1 / 14) * max(1.0, 2 / 10),
        ),
        # Neither dates nor past/forecast nor current, on /v1/forecast -> forecast_days defaults to 7.
        (
            "https://api.open-meteo.com/v1/forecast?latitude=1,2",
            2 * max(1.0, 7 / 14) * max(1.0, 0 / 10),
        ),
        # models=a,b -> a 2x multiplier (review finding 3): counts_models = true in
        # lanes/_providers/open-meteo.toml, so a real two-model archive URL must price like the
        # provider rule's own `models=2` case.
        (
            "https://customer-archive-api.open-meteo.com/v1/archive"
            "?latitude=1&start_date=2020-01-01&end_date=2020-01-14&daily=a&models=era5_land,era5",
            2 * 1 * max(1.0, 14 / 14) * max(1.0, 1 / 10),
        ),
        # A single model (the ordinary case, e.g. models=era5_land) is a 1x multiplier, unchanged.
        (
            "https://customer-archive-api.open-meteo.com/v1/archive"
            "?latitude=1&start_date=2020-01-01&end_date=2020-01-14&daily=a&models=era5_land",
            1 * max(1.0, 14 / 14) * max(1.0, 1 / 10),
        ),
    ],
)
def test_open_meteo_weight_table(url: str, expected: float) -> None:
    assert usage.open_meteo_weight_for_url(url) == pytest.approx(expected)


def test_weight_equals_g0_on_soil_request_builder() -> None:
    """Pinned equal to G0's real soil request builder (`pipeline/direct/soil/source.py`), per plan 0W.1."""
    for locations, days, variables in [(1, 14, 10), (2, 31, 3), (1568, 14, 5), (1, 1, 1)]:
        assert usage.open_meteo_request_weight(locations, days, variables) == g0_weight(locations, days, variables)


# --- Logical caps come from the budget headline ----------------------------------------------------


def test_logical_caps_come_from_budget_headline() -> None:
    assert LANE_LOGICAL_CAPS["soil"] == _SOIL_LOGICAL_CAP


# --- The usage line itself --------------------------------------------------------------------------


def _captured_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, bytes]]:
    calls: list[tuple[int, bytes]] = []
    monkeypatch.setattr(usage.os, "write", lambda fd, data: calls.append((fd, data)))
    return calls


def test_turn_usage_line_is_flat_bounded_numeric_and_carries_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_TURN_ID", "turn-123")
    monkeypatch.setattr(usage, "_host_counters", {})
    calls = _captured_writes(monkeypatch)

    usage.write_usage_line(pid=_SAMPLE_TURN_PID, opening=False)

    assert len(calls) == 1
    fd, data = calls[0]
    assert fd == 1  # the turn line always goes to stdout
    payload = json.loads(data.decode("utf-8").strip())
    assert payload["event"] == "plantgeo_turn_usage"
    assert payload["level"] == "debug"
    assert payload["pid"] == _SAMPLE_TURN_PID
    assert payload["turn_id"] == "turn-123"
    assert payload["usage_version"] == 1
    # Flat and bounded: every top-level value is a JSON-primitive, or `hosts`/`other`, never a
    # further-nested object under any other key.
    for key, value in payload.items():
        if key in ("hosts", "other"):
            assert isinstance(value, dict)
            continue
        assert isinstance(value, (str, int, float, bool, type(None)))
    assert isinstance(payload["cpu_seconds"], (int, float))


def test_zero_host_usage_line_is_still_written(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    monkeypatch.setattr(usage, "_host_counters", {})
    calls = _captured_writes(monkeypatch)

    usage.write_usage_line(pid=999, opening=False)

    assert len(calls) == 1
    fd, data = calls[0]
    assert fd == _STDERR_FD  # the operator summary always goes to stderr
    payload = json.loads(data.decode("utf-8").strip())
    assert payload["event"] == "plantgeo_source_usage"
    assert payload["hosts"] == {}
    assert payload["level"] == "info"  # zero hosts -> no weighted pool touched


def test_operator_line_warns_when_a_weighted_pool_was_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    monkeypatch.setattr(
        usage, "_host_counters", {"api.open-meteo.com": {"provider": "open-meteo", "pool": "open-meteo-free"}}
    )
    calls = _captured_writes(monkeypatch)

    usage.write_usage_line(pid=1, opening=False)

    payload = json.loads(calls[0][1].decode("utf-8").strip())
    assert payload["level"] == "warn"


def test_rss_is_null_when_resource_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "resource", None)
    assert usage._rss_peak_kib() is None
