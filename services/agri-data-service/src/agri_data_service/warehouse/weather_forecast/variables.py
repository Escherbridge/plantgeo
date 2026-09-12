"""Canonical forecast variables and units; see AGENTS.md."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

VariableName = Literal[
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "dew_point_2m",
    "cloud_cover",
    "pressure_msl",
    "precipitation_probability",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "wind_u_10m",
    "wind_v_10m",
]
Unit = Literal["degC", "%", "hPa", "mm", "code", "m/s", "degree"]


@dataclass(frozen=True, slots=True)
class VariableDefinition:
    """Canonical storage unit and admissible numeric support."""

    unit: Unit
    minimum: float | None = None
    maximum: float | None = None
    requires_interval: bool = False


VARIABLES: MappingProxyType[VariableName, VariableDefinition] = MappingProxyType(
    {
        "temperature_2m": VariableDefinition("degC", minimum=-273.15),
        "apparent_temperature": VariableDefinition("degC"),
        "relative_humidity_2m": VariableDefinition("%", minimum=0, maximum=100),
        "dew_point_2m": VariableDefinition("degC", minimum=-273.15),
        "cloud_cover": VariableDefinition("%", minimum=0, maximum=100),
        "pressure_msl": VariableDefinition("hPa", minimum=0),
        "precipitation_probability": VariableDefinition("%", minimum=0, maximum=100, requires_interval=True),
        "precipitation": VariableDefinition("mm", minimum=0, requires_interval=True),
        "weather_code": VariableDefinition("code", minimum=0),
        "wind_speed_10m": VariableDefinition("m/s", minimum=0),
        "wind_gusts_10m": VariableDefinition("m/s", minimum=0, requires_interval=True),
        "wind_direction_10m": VariableDefinition("degree", minimum=0, maximum=360),
        "wind_u_10m": VariableDefinition("m/s"),
        "wind_v_10m": VariableDefinition("m/s"),
    }
)
