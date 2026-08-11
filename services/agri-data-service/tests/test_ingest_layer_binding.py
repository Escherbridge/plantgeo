"""The shared layer binding: the call-time environment read, and that all nine producers still bind the same names."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.ingest.evacuation_zones import (
    DEFAULT_EVACUATION_ZONES_LAYER_NAME,
    EVACUATION_ZONES_CHANNEL,
    EVACUATION_ZONES_LAYER,
    EVACUATION_ZONES_LAYER_VARIABLE,
    resolve_evacuation_zones_layer_name,
)
from agri_data_service.ingest.firms import (
    DEFAULT_FIRMS_LAYER_NAME,
    FIRMS_CHANNEL,
    FIRMS_LAYER,
    FIRMS_LAYER_VARIABLE,
    resolve_firms_layer_name,
)
from agri_data_service.ingest.layer_binding import LayerBinding
from agri_data_service.ingest.mtbs import (
    BURN_SEVERITY_LAYER,
    BURN_SEVERITY_LAYER_VARIABLE,
    DEFAULT_BURN_SEVERITY_LAYER_NAME,
    MTBS_CHANNEL,
    resolve_burn_severity_layer_name,
)
from agri_data_service.ingest.open_meteo import (
    DEFAULT_WEATHER_LAYER_NAME,
    OPEN_METEO_CHANNEL,
    WEATHER_LAYER,
    WEATHER_LAYER_VARIABLE,
    resolve_weather_layer_name,
)
from agri_data_service.ingest.sensors import (
    DEFAULT_SENSORS_LAYER_NAME,
    SENSORS_CHANNEL,
    SENSORS_LAYER,
    SENSORS_LAYER_VARIABLE,
    resolve_sensors_layer_name,
)
from agri_data_service.ingest.usgs_nwis import (
    DEFAULT_WATER_GAUGES_LAYER_NAME,
    USGS_CHANNEL,
    WATER_GAUGES_LAYER,
    WATER_GAUGES_LAYER_VARIABLE,
    resolve_water_gauges_layer_name,
)
from agri_data_service.ingest.vegetation import (
    DEFAULT_VEGETATION_LAYER_NAME,
    VEGETATION_CHANNEL,
    VEGETATION_LAYER,
    VEGETATION_LAYER_VARIABLE,
    resolve_vegetation_layer_name,
)
from agri_data_service.ingest.watersheds import (
    DEFAULT_WATERSHEDS_LAYER_NAME,
    WATERSHEDS_CHANNEL,
    WATERSHEDS_LAYER,
    WATERSHEDS_LAYER_VARIABLE,
    resolve_watersheds_layer_name,
)
from agri_data_service.ingest.wfigs import (
    DEFAULT_FIRE_PERIMETERS_LAYER_NAME,
    FIRE_PERIMETERS_LAYER,
    FIRE_PERIMETERS_LAYER_VARIABLE,
    WFIGS_CHANNEL,
    resolve_fire_perimeters_layer_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

# (binding, the module's resolver, the module's re-exported variable/default/channel aliases).
LIVE_BINDINGS: tuple[tuple[LayerBinding, Callable[[], str], str, str, str], ...] = (
    (FIRMS_LAYER, resolve_firms_layer_name, FIRMS_LAYER_VARIABLE, DEFAULT_FIRMS_LAYER_NAME, FIRMS_CHANNEL),
    (
        WATER_GAUGES_LAYER,
        resolve_water_gauges_layer_name,
        WATER_GAUGES_LAYER_VARIABLE,
        DEFAULT_WATER_GAUGES_LAYER_NAME,
        USGS_CHANNEL,
    ),
    (WEATHER_LAYER, resolve_weather_layer_name, WEATHER_LAYER_VARIABLE, DEFAULT_WEATHER_LAYER_NAME, OPEN_METEO_CHANNEL),
    (
        FIRE_PERIMETERS_LAYER,
        resolve_fire_perimeters_layer_name,
        FIRE_PERIMETERS_LAYER_VARIABLE,
        DEFAULT_FIRE_PERIMETERS_LAYER_NAME,
        WFIGS_CHANNEL,
    ),
    (SENSORS_LAYER, resolve_sensors_layer_name, SENSORS_LAYER_VARIABLE, DEFAULT_SENSORS_LAYER_NAME, SENSORS_CHANNEL),
    (
        EVACUATION_ZONES_LAYER,
        resolve_evacuation_zones_layer_name,
        EVACUATION_ZONES_LAYER_VARIABLE,
        DEFAULT_EVACUATION_ZONES_LAYER_NAME,
        EVACUATION_ZONES_CHANNEL,
    ),
    (
        WATERSHEDS_LAYER,
        resolve_watersheds_layer_name,
        WATERSHEDS_LAYER_VARIABLE,
        DEFAULT_WATERSHEDS_LAYER_NAME,
        WATERSHEDS_CHANNEL,
    ),
    (
        VEGETATION_LAYER,
        resolve_vegetation_layer_name,
        VEGETATION_LAYER_VARIABLE,
        DEFAULT_VEGETATION_LAYER_NAME,
        VEGETATION_CHANNEL,
    ),
    (
        BURN_SEVERITY_LAYER,
        resolve_burn_severity_layer_name,
        BURN_SEVERITY_LAYER_VARIABLE,
        DEFAULT_BURN_SEVERITY_LAYER_NAME,
        MTBS_CHANNEL,
    ),
)

EXPECTED_BINDING_COUNT = 9


def test_an_unset_variable_resolves_to_the_default() -> None:
    binding = LayerBinding(variable="TEST_LAYER_ID", default="test-layer", channel="layer:test-layer")
    assert binding.resolve() == "test-layer"


def test_the_variable_is_read_at_call_time_never_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    # The load-bearing rule: a long-lived cron container must pick up a renamed layer without a
    # restart, so the binding is constructed at import and read on every call. An implementation
    # that resolved in __init__ would still pass every other test in this file.
    binding = LayerBinding(variable="TEST_LAYER_ID", default="test-layer", channel="layer:test-layer")
    monkeypatch.setenv("TEST_LAYER_ID", "renamed-mid-life")
    assert binding.resolve() == "renamed-mid-life"
    monkeypatch.delenv("TEST_LAYER_ID")
    assert binding.resolve() == "test-layer"


def test_a_blank_or_whitespace_value_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = LayerBinding(variable="TEST_LAYER_ID", default="test-layer", channel="layer:test-layer")
    monkeypatch.setenv("TEST_LAYER_ID", "   ")
    assert binding.resolve() == "test-layer"
    monkeypatch.setenv("TEST_LAYER_ID", "  padded-name  ")
    assert binding.resolve() == "padded-name"


def test_every_live_producer_still_exposes_its_old_public_names() -> None:
    assert len(LIVE_BINDINGS) == EXPECTED_BINDING_COUNT
    for binding, _resolve, variable, default, channel in LIVE_BINDINGS:
        assert binding.variable == variable
        assert binding.default == default
        assert binding.channel == channel


def test_every_producers_resolver_goes_through_its_own_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    for _binding, resolve, variable, default, _channel in LIVE_BINDINGS:
        monkeypatch.delenv(variable, raising=False)
        assert resolve() == default
        monkeypatch.setenv(variable, f"{default}-staging")
        assert resolve() == f"{default}-staging"
        monkeypatch.delenv(variable)


def test_a_channel_is_pinned_to_the_default_layer_name_not_to_the_resolved_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Renaming a layer through the environment must NOT re-point the realtime channel: the map
    # subscribes to a fixed name and `writer.py` validates the channel against `^layer:[a-z0-9-]+$`.
    for binding, _resolve, variable, default, channel in LIVE_BINDINGS:
        monkeypatch.setenv(variable, "renamed-layer")
        assert binding.resolve() == "renamed-layer"
        assert binding.channel == channel == f"layer:{default}"
        monkeypatch.delenv(variable)
