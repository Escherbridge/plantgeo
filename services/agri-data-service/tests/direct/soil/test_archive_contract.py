"""The soil writer asks `models=era5_land` for every variable, so every variable must be one it publishes."""

from __future__ import annotations

from agri_data_service.ingest.open_meteo import OPEN_METEO_ERA5_LAND_MODEL
from agri_data_service.pipeline.direct.soil.archive_contract import OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS
from agri_data_service.pipeline.direct.soil.products import SOIL_SOURCE_PARAMETERS


def test_every_soil_source_parameter_is_published_by_the_model_the_soil_request_names() -> None:
    """A variable only ERA5 publishes comes back as a present, entirely-null ERA5-Land series.

    That exact shape -- `shortwave_radiation_sum` under `era5_land` -- once fetched, validated and
    persisted 397 empty series, because range bounds wave nulls through.
    """
    wrong_model = {
        parameter: OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter].model
        for parameter in SOIL_SOURCE_PARAMETERS
        if OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter].model != OPEN_METEO_ERA5_LAND_MODEL
    }
    assert wrong_model == {}
