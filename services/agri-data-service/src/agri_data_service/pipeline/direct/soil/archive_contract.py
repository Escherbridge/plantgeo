"""The Open-Meteo ERA5/ERA5-Land archive lane, native lattice and per-variable acceptance table.

Moved 2026-10-26 from the purged `execution/weather_observations/era5_land.py` historical backfill;
every value is byte-identical to what the history was normalized through. See
`pipeline/direct/soil/AGENTS.md`.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from agri_data_service.execution.open_meteo_lane import OpenMeteoLane
from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_ARCHIVE_BASE_URL,
    OPEN_METEO_ARCHIVE_BOUNDS,
    OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL,
    OPEN_METEO_ERA5_LAND_MODEL,
    OPEN_METEO_ERA5_MODEL,
    OpenMeteoArchiveBaseUrl,
    OpenMeteoArchiveModel,
)
from agri_data_service.ingest.open_meteo_endpoint import OpenMeteoEndpoint

OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES: Final = 0.1

# Assembled here rather than imported because `ingest/open_meteo.py` predates `OpenMeteoEndpoint`;
# both hosts and the byte budget are still its constants.
OPEN_METEO_ARCHIVE_ENDPOINT: Final[OpenMeteoEndpoint[OpenMeteoArchiveBaseUrl]] = OpenMeteoEndpoint(
    free_base_url=OPEN_METEO_ARCHIVE_BASE_URL,
    customer_base_url=OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL,
    bounds=OPEN_METEO_ARCHIVE_BOUNDS,
)

# Retrieval, caching, checksums and checkpoint state are the shared scaffold's, not this lane's:
# `OPEN_METEO_ARCHIVE_LANE.label` is what prefixes every message they raise.
OPEN_METEO_ARCHIVE_LANE: Final = OpenMeteoLane(
    label="Open-Meteo archive",
    cache_directory_name="historical-open-meteo",
    endpoint=OPEN_METEO_ARCHIVE_ENDPOINT,
)


class OpenMeteoArchiveSignal(NamedTuple):
    """One daily variable's archive model, warehouse naming, units, and inclusive acceptance range."""

    # The reanalysis that actually publishes this variable, verified live. Required, and first,
    # because assuming it was the lane's usual model is exactly how `shortwave_radiation_sum`
    # silently returned 580,414 nulls.
    model: OpenMeteoArchiveModel
    signal_name: str
    original_unit: str
    normalized_unit: str
    minimum: float
    maximum: float
    # The provider's own `daily_units` spelling for this variable, verified live against the API
    # (e.g. "MJ/m²", not the warehouse's "MJ/m^2/day"). None for the variables whose provider
    # spelling has not been independently confirmed, rather than guessing a Unicode string that
    # would reject every valid payload if the guess were wrong.
    provider_unit: str | None = None


# Open-Meteo daily variable -> the model that publishes it, warehouse signal, units, and the range a
# value must fall inside. The bounds are the only thing standing between a provider sentinel (-999, a
# netCDF `_FillValue`) and an accepted row.
_LAND: Final = OPEN_METEO_ERA5_LAND_MODEL
OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS: Final[dict[str, OpenMeteoArchiveSignal]] = {
    "soil_moisture_0_to_7cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_water_content_layer_1", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_moisture_7_to_28cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_water_content_layer_2", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_moisture_28_to_100cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_water_content_layer_3", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_temperature_0_to_7cm_mean": OpenMeteoArchiveSignal(_LAND, "soil_temperature_level_1", "C", "C", -100.0, 70.0),
    # The remaining three bands align with ERA5-Land's CDS levels 2-4.
    "soil_temperature_7_to_28cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_temperature_level_2", "C", "C", -100.0, 70.0
    ),
    "soil_temperature_28_to_100cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_temperature_level_3", "C", "C", -100.0, 70.0
    ),
    "soil_temperature_100_to_255cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_temperature_level_4", "C", "C", -100.0, 70.0
    ),
    # An atmospheric-dryness covariate, not a soil-state one.
    "vapour_pressure_deficit_max": OpenMeteoArchiveSignal(_LAND, "vapor_pressure_deficit", "kPa", "kPa", 0.0, 15.0),
    # A SECOND upstream for the signal NASA POWER already writes, deliberately sharing its name AND
    # its unit with NO conversion: `ALLSKY_SFC_SW_DWN` and `shortwave_radiation_sum` are both a daily
    # sum of megajoules per square metre.
    #
    # `era5`, NOT `era5_land`: ERA5-Land publishes no radiation flux through this endpoint and answers
    # the variable with a present, entirely-null series. Measured live 2026-08-09 at 31N/104W --
    # models=era5_land returned 1,462 nulls per cell, models=era5 returned 30.76, 31.01, 30.21 MJ/m².
    #
    # provider_unit is set because this is the one variable in this table whose plausible wrong-unit
    # failure -- a kWh/m^2/day series, 0-10 -- lands entirely inside the [0.0, 60.0] acceptance range
    # below. Verified live against the archive API 2026-08-09.
    "shortwave_radiation_sum": OpenMeteoArchiveSignal(
        OPEN_METEO_ERA5_MODEL, "surface_shortwave_radiation", "MJ/m^2/day", "MJ/m^2/day", 0.0, 60.0, "MJ/m²"
    ),
}

__all__ = [
    "OPEN_METEO_ARCHIVE_ENDPOINT",
    "OPEN_METEO_ARCHIVE_LANE",
    "OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES",
    "OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS",
    "OpenMeteoArchiveSignal",
]
