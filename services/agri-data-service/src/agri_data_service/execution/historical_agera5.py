"""Cache-first contracts for the Copernicus CDS AgERA5 agrometeorological indicators replay."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cached_property
from typing import Final, Literal

from pydantic import Field, field_validator

from agri_data_service.execution.backfill_types import (
    AnalysisGridCell,  # noqa: TC001
    HistoricalBackfillWindow,  # noqa: TC001
)
from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.historical_era5 import _require_cds_credentials
from agri_data_service.execution.source_ingestion import SourceDefinition  # noqa: TC001

AGERA5_SOURCE_KEY: Final = "copernicus-cds-agera5"
AGERA5_DATASET_ID: Final = "sis-agrometeorological-indicators"
AGERA5_DATASET_VERSION: Final = "2_0"

AGERA5_SIGNAL_SPECIFICATIONS: Final[dict[str, tuple[str, str, str, float, float]]] = {
    "2m_temperature_max": ("air_temperature_max", "K", "degC", -50.0, 60.0),
    "2m_temperature_mean": ("air_temperature_mean", "K", "degC", -50.0, 60.0),
    "2m_temperature_min": ("air_temperature_min", "K", "degC", -50.0, 60.0),
    "precipitation_flux": ("precipitation", "kg/m^2/s", "mm/day", 0.0, 500.0),
    "solar_radiation_flux": ("surface_shortwave_radiation", "J/m^2/day", "MJ/m^2/day", 0.0, 60.0),
    "2m_relative_humidity": ("relative_humidity", "%", "%", 0.0, 100.0),
    "10m_wind_speed": ("wind_speed", "m/s", "m/s", 0.0, 100.0),
}


@dataclass(frozen=True)
class Agera5Period:
    """One calendar month period for an AgERA5 backfill plan."""

    key: str
    start_date: date
    end_date: date
    year: str
    month: str


class HistoricalAgera5BackfillPlan(ContractModel):
    """Reviewed AgERA5 agrometeorological indicators plan."""

    schema_version: str = Field(default="cds-agera5-v1")
    source: SourceDefinition
    window: HistoricalBackfillWindow
    dataset: Literal["sis-agrometeorological-indicators"] = AGERA5_DATASET_ID
    version: Literal["2_0"] = AGERA5_DATASET_VERSION
    requested_grid_degrees: float = Field(default=0.1, gt=0, le=1.0)
    cells: list[AnalysisGridCell] = Field(min_length=1)
    parameters: list[str] = Field(min_length=1)
    transform_version: str = Field(default="cds-agera5-normalization-v1")
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


def require_agera5_cds_credentials() -> tuple[str, str]:
    """Retrieve CDSAPI credentials for classic CDS host."""
    return _require_cds_credentials()
