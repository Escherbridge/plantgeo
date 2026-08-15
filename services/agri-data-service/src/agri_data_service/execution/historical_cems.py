"""Cache-first contracts for the Copernicus EWDS CEMS fire danger indices replay."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from functools import cached_property
from typing import Final, Literal

from pydantic import Field, field_validator

from agri_data_service.config import settings
from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.historical_backfill import (
    AnalysisGridCell,  # noqa: TC001
    HistoricalBackfillWindow,  # noqa: TC001
)
from agri_data_service.execution.source_ingestion import SourceDefinition  # noqa: TC001

CEMS_SOURCE_KEY: Final = "copernicus-ewds-cems-fire"
CEMS_DATASET_ID: Final = "cems-fire-historical-v1"
EWDS_DEFAULT_URL: Final = "https://ewds.climate.copernicus.eu/api"

CEMS_FIRE_SIGNAL_SPECIFICATIONS: Final[dict[str, tuple[str, str, str, float, float]]] = {
    "fire_weather_index": ("fire_weather_index", "index", "index", 0.0, 200.0),
    "fine_fuel_moisture_code": ("fine_fuel_moisture_code", "index", "index", 0.0, 100.0),
    "duff_moisture_code": ("duff_moisture_code", "index", "index", 0.0, 500.0),
    "drought_code": ("drought_code", "index", "index", 0.0, 2000.0),
    "initial_spread_index": ("initial_spread_index", "index", "index", 0.0, 100.0),
    "build_up_index": ("build_up_index", "index", "index", 0.0, 500.0),
    "fire_daily_severity_index": ("daily_severity_rating", "index", "index", 0.0, 100.0),
    "energy_release_component": ("energy_release_component", "index", "index", 0.0, 200.0),
    "burning_index": ("burning_index", "index", "index", 0.0, 300.0),
    "spread_component": ("spread_component", "index", "index", 0.0, 200.0),
    "ignition_component": ("ignition_component", "index", "index", 0.0, 100.0),
    "keetch_byram_drought_index": ("keetch_byram_drought_index", "index", "index", 0.0, 800.0),
}


class HistoricalCemsBackfillPlan(ContractModel):
    """Reviewed CEMS fire danger indices plan for the EWDS host."""

    schema_version: str = Field(default="ewds-cems-v1")
    source: SourceDefinition
    window: HistoricalBackfillWindow
    dataset: Literal["cems-fire-historical-v1"] = CEMS_DATASET_ID
    product_type: Literal["reanalysis"] = "reanalysis"
    system_version: str = Field(default="4_1")
    requested_grid_degrees: float = Field(default=0.25, gt=0, le=1.0)
    cells: list[AnalysisGridCell] = Field(min_length=1)
    parameters: list[str] = Field(min_length=1)
    transform_version: str = Field(default="cems-fire-normalization-v1")
    release_set_key: str
    release_set_as_of: datetime
    description: str | None = None

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("release_set_as_of must be an aware UTC datetime")
        return value

    @cached_property
    def plan_checksum(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


def require_ewds_credentials() -> tuple[str, str]:
    """Retrieve EWDS API URL and key from process environment or settings.

    Falls back gracefully to CDS credentials or defaults if EWDS is not configured.
    """
    url = (
        os.environ.get("EWDSAPI_URL")
        or settings.ewds_url
        or os.environ.get("CDSAPI_URL")
        or settings.cdsapi_url
        or EWDS_DEFAULT_URL
    ).strip()
    key_secret = settings.ewds_api_key or settings.cdsapi_key
    key = (
        os.environ.get("EWDSAPI_KEY")
        or (key_secret.get_secret_value() if key_secret else None)
        or os.environ.get("CDSAPI_KEY")
        or ""
    ).strip()
    return url, key
