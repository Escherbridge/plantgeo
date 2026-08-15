"""Identity and row-count records the four historical persistence lanes return."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid
    from datetime import datetime


@dataclass(frozen=True)
class ReleaseSetIdentity:
    """The three fields that distinguish one governed release set from another."""

    logical_key: str
    as_of_time: datetime
    description: str | None


@dataclass(frozen=True)
class HistoricalNasaWriteResult:
    """Identifiers and row counts from one persisted NASA POWER source cell."""

    source_release_id: uuid.UUID
    cell_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_count: int
    coverage_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalReleaseSetResult:
    """One finalized immutable release set covering every checkpoint receipt."""

    release_set_id: uuid.UUID
    manifest_checksum: str
    source_release_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalUsdmWriteResult:
    """Identifiers and row counts from one persisted USDM weekly vector release."""

    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    polygon_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalEra5WriteResult:
    """Identifiers and row counts from one persisted ERA5-Land monthly source release."""

    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_count: int
    coverage_count: int
    crosswalk_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalOpenMeteoWriteResult:
    """Identifiers and row counts from one persisted Open-Meteo ERA5-Land archive chunk."""

    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_count: int
    observed_value_count: int
    coverage_count: int
    no_data_series_count: int
    crosswalk_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalGlofasWriteResult:
    """Identifiers and row counts from one persisted Open-Meteo GloFAS flood chunk."""

    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_count: int
    observed_value_count: int
    coverage_count: int
    no_data_series_count: int
    crosswalk_count: int
    idempotent: bool


@dataclass(frozen=True)
class HistoricalCamsWriteResult:
    """Identifiers and row counts from one persisted Open-Meteo CAMS air-quality chunk."""

    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_count: int
    observed_value_count: int
    insufficient_hour_day_count: int
    coverage_count: int
    no_data_series_count: int
    crosswalk_count: int
    idempotent: bool
