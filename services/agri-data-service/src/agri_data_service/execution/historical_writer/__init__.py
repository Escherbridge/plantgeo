"""Transactional local persistence for validated historical source releases.

Four lanes -- NASA POWER, USDM, ERA5-Land and the Open-Meteo ERA5-Land archive -- write the governed
provenance plane in `agri.*`. The governed upsert itself lives once in `execution/provenance.py`; what
each lane genuinely does differently stays in its own module here. See `execution/AGENTS.md`
§"historical_writer" for the divergence ledger and for why nothing here touches `ingest/writer.py`.
"""

from agri_data_service.execution.historical_writer._results import (
    HistoricalEra5WriteResult,
    HistoricalNasaWriteResult,
    HistoricalOpenMeteoWriteResult,
    HistoricalReleaseSetResult,
    HistoricalUsdmWriteResult,
    ReleaseSetIdentity,
)
from agri_data_service.execution.historical_writer.era5 import (
    # Private, but imported by name in tests/test_historical_era5.py to assert the insert batching;
    # the re-export keeps that import resolving across the package split.
    _insert_era5_observations,
    finalize_era5_release_set,
    persist_era5_land_month,
)
from agri_data_service.execution.historical_writer.nasa import (
    finalize_nasa_release_set,
    persist_nasa_power_cell,
)
from agri_data_service.execution.historical_writer.open_meteo import (
    finalize_open_meteo_release_set,
    persist_open_meteo_archive_chunk,
)
from agri_data_service.execution.historical_writer.usdm import (
    finalize_usdm_release_set,
    persist_usdm_shapefile,
)

__all__ = [
    "HistoricalEra5WriteResult",
    "HistoricalNasaWriteResult",
    "HistoricalOpenMeteoWriteResult",
    "HistoricalReleaseSetResult",
    "HistoricalUsdmWriteResult",
    "ReleaseSetIdentity",
    "_insert_era5_observations",
    "finalize_era5_release_set",
    "finalize_nasa_release_set",
    "finalize_open_meteo_release_set",
    "finalize_usdm_release_set",
    "persist_era5_land_month",
    "persist_nasa_power_cell",
    "persist_open_meteo_archive_chunk",
    "persist_usdm_shapefile",
]
