"""Cache-first contracts for the reviewed ERA5-Land monthly historical replay."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import cdsapi  # type: ignore[import-untyped]
import xarray as xr
from pydantic import Field, field_validator, model_validator

from agri_data_service.config import settings
from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.historical_backfill import (
    AnalysisGridCell,
    HistoricalBackfillWindow,
    HistoricalCoverageAudit,
    HistoricalSignalObservation,
)
from agri_data_service.execution.source_ingestion import SourceDefinition  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Iterator

ERA5_LAND_DAILY_SCHEMA_VERSION: Literal["era5-land-daily-v1"] = "era5-land-daily-v1"
ERA5_LAND_DAILY_DATASET: Literal["derived-era5-land-daily-statistics"] = "derived-era5-land-daily-statistics"
ERA5_LAND_DAILY_MAX_RESPONSE_BYTES = 512_000_000
ERA5_LAND_DAILY_MAX_ZIP_MEMBERS = 16
ERA5_LAND_DAILY_MAX_EXPANDED_BYTES = 1_500_000_000
ERA5_LAND_NATIVE_GRID_NAME: Literal["era5-land-0.1-degree"] = "era5-land-0.1-degree"
ERA5_LAND_NATIVE_GRID_RESOLUTION_M = 9_000
ERA5_LAND_REQUESTED_GRID_DEGREES = 1.0
ERA5_LAND_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
ERA5_LAND_RAW_CACHE_SCHEMA_VERSION: Literal[1] = 1
ERA5_LAND_FINALIZATION_SCHEMA_VERSION: Literal[1] = 1
ERA5_LAND_DAY_STRING_LENGTH = 2
WGS84_HALF_CIRCLE_DEGREES = 180
ERA5_LAND_COORDINATE_EPSILON = 0.000_001
ERA5_LAND_LICENCE_REFUSED_HTTP_STATUS = 403

# The CDS daily-statistics product returns the original analysis units.  The
# normalized unit is explicit so local readers never guess which temperature scale
# they received. See execution/AGENTS.md §historical_era5.
ERA5_LAND_SIGNAL_SPECIFICATIONS: dict[str, tuple[str, str, str]] = {
    "10m_u_component_of_wind": ("eastward_wind_10m_mean", "m/s", "m/s"),
    "10m_v_component_of_wind": ("northward_wind_10m_mean", "m/s", "m/s"),
    "2m_dewpoint_temperature": ("dew_point_temperature", "K", "C"),
    "2m_temperature": ("air_temperature_mean", "K", "C"),
    "soil_temperature_level_1": ("soil_temperature_level_1", "K", "C"),
    "volumetric_soil_water_layer_1": ("soil_water_content_layer_1", "m^3/m^3", "m^3/m^3"),
}

ERA5_LAND_VARIABLE_ALIASES: dict[str, frozenset[str]] = {
    "10m_u_component_of_wind": frozenset({"10m_u_component_of_wind", "u10"}),
    "10m_v_component_of_wind": frozenset({"10m_v_component_of_wind", "v10"}),
    "2m_dewpoint_temperature": frozenset({"2m_dewpoint_temperature", "d2m"}),
    "2m_temperature": frozenset({"2m_temperature", "t2m"}),
    "soil_temperature_level_1": frozenset({"soil_temperature_level_1", "stl1"}),
    "volumetric_soil_water_layer_1": frozenset({"volumetric_soil_water_layer_1", "swvl1"}),
}


class Era5LandArea(ContractModel):
    """One inclusive CDS request envelope in north, west, south, east order."""

    north: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def require_ordered_bounds(self) -> Era5LandArea:
        if self.north <= self.south or self.east <= self.west:
            raise ValueError("ERA5 requested area must have north > south and east > west")
        return self

    @property
    def cds_value(self) -> list[float]:
        """Return the CDS API's documented north/west/south/east ordering."""
        return [self.north, self.west, self.south, self.east]


class Era5LandPeriod(ContractModel):
    """One deterministic calendar-month source artifact request."""

    key: str = Field(pattern=r"^\d{4}-\d{2}$")
    start_date: date
    end_date: date
    year: str = Field(pattern=r"^\d{4}$")
    month: str = Field(pattern=r"^\d{2}$")
    days: list[str] = Field(min_length=1, max_length=31)

    @field_validator("days")
    @classmethod
    def require_sorted_days(cls, value: list[str]) -> list[str]:
        has_invalid_day = any(not day.isdigit() or len(day) != ERA5_LAND_DAY_STRING_LENGTH for day in value)
        if value != sorted(value) or len(value) != len(set(value)) or has_invalid_day:
            raise ValueError("ERA5 period days must be sorted, unique two-digit day values")
        return value

    @model_validator(mode="after")
    def require_consistent_month(self) -> Era5LandPeriod:
        if self.start_date > self.end_date:
            raise ValueError("ERA5 period end_date must not precede start_date")
        if self.start_date.year != self.end_date.year or self.start_date.month != self.end_date.month:
            raise ValueError("ERA5 source periods must stay within one calendar month")
        if (
            self.key != f"{self.start_date:%Y-%m}"
            or self.year != f"{self.start_date:%Y}"
            or self.month != f"{self.start_date:%m}"
        ):
            raise ValueError("ERA5 period key, year, and month must match its dates")
        expected_days = [f"{current.day:02d}" for current in _date_range(self.start_date, self.end_date)]
        if self.days != expected_days:
            raise ValueError("ERA5 period days must cover every date from start_date through end_date")
        return self


class HistoricalEra5LandBackfillPlan(ContractModel):
    """Reviewed four-year ERA5-Land source plan with explicit point-sample support."""

    schema_version: Literal["era5-land-daily-v1"] = ERA5_LAND_DAILY_SCHEMA_VERSION
    source: SourceDefinition
    window: HistoricalBackfillWindow
    dataset: Literal["derived-era5-land-daily-statistics"] = ERA5_LAND_DAILY_DATASET
    daily_statistic: Literal["daily_mean"]
    frequency: Literal["1_hourly"]
    time_zone: Literal["utc+00:00"]
    requested_grid_degrees: float = Field(gt=0, le=1)
    requested_area: Era5LandArea
    native_grid_name: Literal["era5-land-0.1-degree"] = ERA5_LAND_NATIVE_GRID_NAME
    native_grid_resolution_m: int = Field(gt=0)
    cells: list[AnalysisGridCell] = Field(min_length=1, max_length=10_000)
    parameters: list[str] = Field(min_length=1, max_length=len(ERA5_LAND_SIGNAL_SPECIFICATIONS))
    periods: list[Era5LandPeriod] = Field(min_length=1, max_length=60)
    nasa_lattice_plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    terms_acceptance_required: Literal[True]
    transform_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("cells")
    @classmethod
    def require_sorted_unique_cells(cls, value: list[AnalysisGridCell]) -> list[AnalysisGridCell]:
        keys = [cell.cell_key for cell in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("ERA5 cells must be sorted and unique by cell_key")
        if len({(cell.latitude, cell.longitude) for cell in value}) != len(value):
            raise ValueError("ERA5 cells must not repeat a requested coordinate")
        return value

    @field_validator("parameters")
    @classmethod
    def require_supported_sorted_parameters(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("ERA5 parameters must be sorted and unique")
        unsupported = sorted(set(value).difference(ERA5_LAND_SIGNAL_SPECIFICATIONS))
        if unsupported:
            raise ValueError(f"unsupported ERA5-Land parameter(s): {', '.join(unsupported)}")
        return value

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_release_set_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "release_set_as_of")

    @model_validator(mode="after")
    def require_governed_monthly_coverage(self) -> HistoricalEra5LandBackfillPlan:
        if self.source.key != "era5-land":
            raise ValueError("ERA5-Land plans require source.key='era5-land'")
        if self.requested_grid_degrees != ERA5_LAND_REQUESTED_GRID_DEGREES:
            raise ValueError("ERA5-Land historical plan must explicitly request the reviewed one-degree output grid")
        if self.native_grid_resolution_m != ERA5_LAND_NATIVE_GRID_RESOLUTION_M:
            raise ValueError("ERA5-Land historical plan must record its documented 9-km native resolution")
        if any(
            cell.latitude % self.requested_grid_degrees or cell.longitude % self.requested_grid_degrees
            for cell in self.cells
        ):
            raise ValueError("ERA5 requested cells must align to the explicit output grid")
        keys = [period.key for period in self.periods]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("ERA5 source periods must be sorted and unique")
        covered_dates = [
            current for period in self.periods for current in _date_range(period.start_date, period.end_date)
        ]
        expected_dates = list(_date_range(self.window.start_date, self.window.end_date))
        if covered_dates != expected_dates:
            raise ValueError("ERA5 monthly periods must exactly cover the reviewed four-year window")
        return self


class HistoricalEra5Finalization(ContractModel):
    """Controlled release-set correction that preserves a completed ERA5 source replay."""

    schema_version: Literal[1] = ERA5_LAND_FINALIZATION_SCHEMA_VERSION
    source_plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str = Field(min_length=1, max_length=10_000)

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_finalization_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "release_set_as_of")


class HistoricalEra5Receipt(ContractModel):
    """One fully validated, cache-backed monthly CDS artifact receipt."""

    period_key: str = Field(pattern=r"^\d{4}-\d{2}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=ERA5_LAND_DAILY_MAX_RESPONSE_BYTES)
    observation_count: int = Field(ge=1)
    coverage_count: int = Field(ge=1)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "retrieved_at")


class HistoricalEra5RawCacheReceipt(ContractModel):
    """Checksum-bound metadata for a reusable monthly CDS download."""

    schema_version: Literal[1] = ERA5_LAND_RAW_CACHE_SCHEMA_VERSION
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    period_key: str = Field(pattern=r"^\d{4}-\d{2}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=ERA5_LAND_DAILY_MAX_RESPONSE_BYTES)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "retrieved_at")


class HistoricalEra5Checkpoint(ContractModel):
    """Durable resumable state for the complete monthly ERA5-Land plan."""

    schema_version: Literal[1] = ERA5_LAND_CHECKPOINT_SCHEMA_VERSION
    state: Literal["initialized", "running", "validated", "blocked"]
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_cache_plan_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    receipts: list[HistoricalEra5Receipt] = Field(default_factory=list, max_length=60)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("updated_at")
    @classmethod
    def require_aware_update_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "updated_at")

    @field_validator("receipts")
    @classmethod
    def require_sorted_unique_receipts(cls, value: list[HistoricalEra5Receipt]) -> list[HistoricalEra5Receipt]:
        keys = [receipt.period_key for receipt in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("ERA5 receipts must be sorted and unique by period_key")
        return value


@dataclass(frozen=True)
class Era5LandMonthlyResult:
    """Validated monthly raw archive plus normalized daily point-sample facts."""

    period_key: str
    retrieved_at: datetime
    payload: bytes
    payload_checksum: str
    observations: tuple[HistoricalSignalObservation, ...]
    coverage: tuple[HistoricalCoverageAudit, ...]


def historical_era5_plan_checksum(plan: HistoricalEra5LandBackfillPlan) -> str:
    """Fingerprint every governed input controlling this local replay."""
    return hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()


def historical_era5_checkpoint_path(root: Path, plan: HistoricalEra5LandBackfillPlan) -> Path:
    """Return a plan-bound durable checkpoint file path."""
    return root / "historical-era5" / f"{historical_era5_plan_checksum(plan)}.json"


def historical_era5_raw_cache_paths(
    root: Path,
    plan: HistoricalEra5LandBackfillPlan,
    period: Era5LandPeriod,
    *,
    cache_plan_checksum: str | None = None,
) -> tuple[Path, Path]:
    """Return monthly archive and receipt locations beneath the local run root."""
    _plan_period(plan, period.key)
    directory = root / "historical-era5" / (cache_plan_checksum or historical_era5_plan_checksum(plan)) / "raw"
    return directory / f"{period.key}.zip", directory / f"{period.key}.receipt.json"


def initialize_historical_era5_checkpoint(
    plan: HistoricalEra5LandBackfillPlan,
    *,
    updated_at: datetime | None = None,
) -> HistoricalEra5Checkpoint:
    """Create an empty ERA5 checkpoint without opening CDS or PostgreSQL."""
    return HistoricalEra5Checkpoint(
        state="initialized",
        plan_checksum=historical_era5_plan_checksum(plan),
        updated_at=_require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def load_historical_era5_checkpoint(path: Path) -> HistoricalEra5Checkpoint:
    """Read a local ERA5 receipt checkpoint without requesting a provider payload."""
    return HistoricalEra5Checkpoint.model_validate_json(path.read_bytes())


def write_historical_era5_checkpoint(path: Path, checkpoint: HistoricalEra5Checkpoint) -> None:
    """Atomically update credential-free ERA5 checkpoint metadata."""
    _atomic_write(path, canonical_json_bytes(checkpoint.model_dump(mode="json")))


def write_historical_era5_release_plan(path: Path, plan: HistoricalEra5LandBackfillPlan) -> None:
    """Persist a finalization-derived ERA5 plan for matching local persistence and Parquet steps."""
    payload = canonical_json_bytes(plan.model_dump(mode="json"))
    if path.exists():
        existing = HistoricalEra5LandBackfillPlan.model_validate_json(path.read_bytes())
        if existing != plan:
            raise ValueError("ERA5 release plan path already contains a different governed plan")
        return
    _atomic_write(path, payload)


def record_historical_era5_result(
    plan: HistoricalEra5LandBackfillPlan,
    checkpoint: HistoricalEra5Checkpoint,
    result: Era5LandMonthlyResult,
    *,
    updated_at: datetime | None = None,
) -> HistoricalEra5Checkpoint:
    """Advance a monthly receipt only after every requested point and day validates."""
    if checkpoint.plan_checksum != historical_era5_plan_checksum(plan):
        raise ValueError("ERA5 checkpoint does not bind the reviewed plan")
    _plan_period(plan, result.period_key)
    require_complete_era5_result(plan, result)
    receipt = HistoricalEra5Receipt(
        period_key=result.period_key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        observation_count=len(result.observations),
        coverage_count=len(result.coverage),
        retrieved_at=result.retrieved_at,
    )
    prior = {item.period_key: item for item in checkpoint.receipts}
    existing = prior.get(receipt.period_key)
    if existing is not None and existing != receipt:
        raise ValueError("ERA5 checkpoint already binds this month to different source content")
    prior[receipt.period_key] = receipt
    receipts = [prior[key] for key in sorted(prior)]
    complete = [period.key for period in plan.periods] == [item.period_key for item in receipts]
    return checkpoint.model_copy(
        update={
            "state": "validated" if complete else "running",
            "raw_cache_plan_checksum": checkpoint.raw_cache_plan_checksum,
            "receipts": receipts,
            "updated_at": _require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
            "reason": None,
        }
    )


def historical_era5_release_manifest(
    plan: HistoricalEra5LandBackfillPlan,
    checkpoint: HistoricalEra5Checkpoint,
) -> str:
    """Hash the complete ordered monthly receipt set a local ERA5 release must pin."""
    expected_checksum = historical_era5_plan_checksum(plan)
    if checkpoint.plan_checksum != expected_checksum or checkpoint.state != "validated":
        raise ValueError("a complete validated checkpoint is required for an ERA5 release manifest")
    expected_periods = [period.key for period in plan.periods]
    actual_periods = [receipt.period_key for receipt in checkpoint.receipts]
    if actual_periods != expected_periods:
        raise ValueError("ERA5 receipt coverage does not match the reviewed monthly plan")
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "plan_checksum": expected_checksum,
                "raw_cache_plan_checksum": checkpoint.raw_cache_plan_checksum or expected_checksum,
                "transform_version": plan.transform_version,
                "receipts": [receipt.model_dump(mode="json") for receipt in checkpoint.receipts],
            }
        )
    ).hexdigest()


def rebind_historical_era5_checkpoint_for_finalization(
    source_plan: HistoricalEra5LandBackfillPlan,
    finalization: HistoricalEra5Finalization,
    checkpoint: HistoricalEra5Checkpoint,
    *,
    updated_at: datetime | None = None,
) -> tuple[HistoricalEra5LandBackfillPlan, HistoricalEra5Checkpoint]:
    """Create a new governed ERA5 release plan only from complete immutable monthly receipts."""
    source_checksum = historical_era5_plan_checksum(source_plan)
    if finalization.source_plan_checksum != source_checksum:
        raise ValueError("ERA5 finalization does not identify the reviewed source plan")
    if checkpoint.plan_checksum != source_checksum or checkpoint.state != "validated":
        raise ValueError("ERA5 checkpoint does not match a complete reviewed source plan")
    raw_cache_plan_checksum = checkpoint.raw_cache_plan_checksum or source_checksum
    if raw_cache_plan_checksum != source_checksum:
        raise ValueError("ERA5 finalization raw cache must remain bound to the reviewed source plan")
    if [receipt.period_key for receipt in checkpoint.receipts] != [period.key for period in source_plan.periods]:
        raise ValueError("ERA5 receipt coverage does not match the reviewed monthly plan")
    if finalization.release_set_as_of <= source_plan.release_set_as_of:
        raise ValueError("ERA5 finalization as-of time must advance the reviewed source plan")
    if any(receipt.retrieved_at > finalization.release_set_as_of for receipt in checkpoint.receipts):
        raise ValueError("ERA5 finalization as-of time must not precede a persisted source receipt")
    release_plan = HistoricalEra5LandBackfillPlan.model_validate(
        {
            **source_plan.model_dump(mode="json"),
            "release_set_key": finalization.release_set_key,
            "release_set_as_of": finalization.release_set_as_of,
            "description": finalization.description,
        }
    )
    return (
        release_plan,
        checkpoint.model_copy(
            update={
                "state": "validated",
                "plan_checksum": historical_era5_plan_checksum(release_plan),
                "raw_cache_plan_checksum": raw_cache_plan_checksum,
                "updated_at": _require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
                "reason": None,
            }
        ),
    )


def cache_historical_era5_result(
    root: Path,
    plan: HistoricalEra5LandBackfillPlan,
    result: Era5LandMonthlyResult,
) -> HistoricalEra5RawCacheReceipt:
    """Persist one complete raw CDS archive before the warehouse transaction begins."""
    period = _plan_period(plan, result.period_key)
    require_complete_era5_result(plan, result)
    payload_path, receipt_path = historical_era5_raw_cache_paths(root, plan, period)
    receipt = HistoricalEra5RawCacheReceipt(
        plan_checksum=historical_era5_plan_checksum(plan),
        period_key=period.key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        retrieved_at=result.retrieved_at,
    )
    if payload_path.exists() or receipt_path.exists():
        cached = load_cached_historical_era5_result(root, plan, period)
        if cached is None:
            raise ValueError("ERA5 raw cache unexpectedly has no reusable source archive")
        if cached.payload_checksum != result.payload_checksum or cached.payload != result.payload:
            raise ValueError("ERA5 raw cache already binds this month to different source content")
        return HistoricalEra5RawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    _atomic_write(payload_path, result.payload)
    _atomic_write(receipt_path, canonical_json_bytes(receipt.model_dump(mode="json")))
    return receipt


def load_cached_historical_era5_result(
    root: Path,
    plan: HistoricalEra5LandBackfillPlan,
    period: Era5LandPeriod,
    *,
    cache_plan_checksum: str | None = None,
) -> Era5LandMonthlyResult | None:
    """Re-parse one validated monthly local source artifact, never contacting CDS."""
    expected_cache_plan_checksum = cache_plan_checksum or historical_era5_plan_checksum(plan)
    payload_path, receipt_path = historical_era5_raw_cache_paths(
        root,
        plan,
        period,
        cache_plan_checksum=expected_cache_plan_checksum,
    )
    if not payload_path.exists() and not receipt_path.exists():
        return None
    if not payload_path.exists() or not receipt_path.exists():
        raise ValueError("ERA5 raw cache has incomplete content or receipt")
    receipt = HistoricalEra5RawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    if receipt.plan_checksum != expected_cache_plan_checksum or receipt.period_key != period.key:
        raise ValueError("ERA5 raw cache receipt does not bind this reviewed plan and month")
    payload = payload_path.read_bytes()
    if len(payload) != receipt.payload_bytes or hashlib.sha256(payload).hexdigest() != receipt.payload_checksum:
        raise ValueError("ERA5 raw cache payload does not match its receipt")
    result = parse_era5_land_monthly_payload(plan, period, payload, retrieved_at=receipt.retrieved_at)
    require_complete_era5_result(plan, result)
    return result


async def fetch_era5_land_monthly(
    plan: HistoricalEra5LandBackfillPlan,
    period: Era5LandPeriod,
    *,
    retrieved_at: datetime | None = None,
) -> Era5LandMonthlyResult:
    """Fetch one CDS artifact only when local credentials and accepted terms are present."""
    _require_cds_credentials()
    timestamp = _require_aware_utc(retrieved_at or datetime.now(UTC), "retrieved_at")
    payload = await asyncio.to_thread(_download_era5_land_monthly, plan, period)
    # The parse is the larger of the two blocking halves -- unzip, HDF5 decompression and one xarray point
    # selection per reviewed cell over a payload capped at ERA5_LAND_DAILY_MAX_RESPONSE_BYTES -- so offloading
    # only the download left the event loop stalled for the expensive part. See execution/AGENTS.md.
    return await asyncio.to_thread(parse_era5_land_monthly_payload, plan, period, payload, retrieved_at=timestamp)


def parse_era5_land_monthly_payload(
    plan: HistoricalEra5LandBackfillPlan,
    period: Era5LandPeriod,
    payload: bytes,
    *,
    retrieved_at: datetime,
) -> Era5LandMonthlyResult:
    """Validate one CDS NetCDF ZIP and normalize every reviewed point/day/variable."""
    _plan_period(plan, period.key)
    timestamp = _require_aware_utc(retrieved_at, "retrieved_at")
    if not payload or len(payload) > ERA5_LAND_DAILY_MAX_RESPONSE_BYTES:
        raise ValueError("ERA5 monthly response exceeds the reviewed byte boundary")
    members = _netcdf_members(payload)
    expected_dates = list(_date_range(period.start_date, period.end_date))
    observations: list[HistoricalSignalObservation] = []
    coverage: list[HistoricalCoverageAudit] = []
    payload_checksum = hashlib.sha256(payload).hexdigest()
    variables: dict[str, xr.DataArray] = {}
    with tempfile.TemporaryDirectory(prefix="plantgeo-era5-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(_bytes_io(payload)) as archive:
            for member in members:
                target = root / member.filename.rsplit("/", maxsplit=1)[-1]
                target.write_bytes(archive.read(member))
                with xr.open_dataset(target, engine="h5netcdf") as dataset:
                    parameter, variable_name = _resolve_era5_variable_name(dataset, plan.parameters, target.name)
                    if parameter in variables:
                        raise ValueError("ERA5 archive contains more than one file for a requested variable")
                    variables[parameter] = dataset[variable_name].load()
    if set(variables) != set(plan.parameters):
        missing = sorted(set(plan.parameters).difference(variables))
        unexpected = sorted(set(variables).difference(plan.parameters))
        raise ValueError(
            f"ERA5 archive variables do not match the reviewed plan (missing={missing}, unexpected={unexpected})"
        )

    for parameter in plan.parameters:
        values_by_date = _era5_values_by_cell_and_date(variables[parameter], plan, period, parameter)
        signal_name, original_unit, normalized_unit = ERA5_LAND_SIGNAL_SPECIFICATIONS[parameter]
        for cell in plan.cells:
            cell_values = values_by_date[cell.cell_key]
            received_dates = sorted(cell_values)
            if received_dates != expected_dates:
                raise ValueError(f"ERA5 {parameter} does not provide complete daily coverage for {cell.cell_key}")
            for observed_date in expected_dates:
                original_value = cell_values[observed_date]
                is_observed = original_value is not None
                normalized_value = _normalize_era5_value(parameter, original_value)
                observations.append(
                    HistoricalSignalObservation(
                        cell_key=cell.cell_key,
                        source_parameter=parameter,
                        signal_name=signal_name,
                        observed_at=datetime.combine(observed_date, time.min, tzinfo=UTC),
                        original_value=original_value,
                        original_unit=original_unit,
                        normalized_value=normalized_value,
                        normalized_unit=normalized_unit,
                        quality_flag="accepted" if is_observed else "source_missing",
                        is_observed=is_observed,
                        payload_checksum=payload_checksum,
                    )
                )
            coverage.append(
                HistoricalCoverageAudit(
                    cell_key=cell.cell_key,
                    source_parameter=parameter,
                    signal_name=signal_name,
                    window_start=datetime.combine(period.start_date, time.min, tzinfo=UTC),
                    window_end=datetime.combine(period.end_date, time.max, tzinfo=UTC),
                    expected_observation_count=len(expected_dates),
                    received_observation_count=len(expected_dates),
                    status="complete",
                )
            )
    result = Era5LandMonthlyResult(
        period_key=period.key,
        retrieved_at=timestamp,
        payload=payload,
        payload_checksum=payload_checksum,
        observations=tuple(observations),
        coverage=tuple(coverage),
    )
    require_complete_era5_result(plan, result)
    return result


def require_complete_era5_result(plan: HistoricalEra5LandBackfillPlan, result: Era5LandMonthlyResult) -> None:
    """Reject any monthly archive that cannot prove every requested point/signal/day."""
    period = _plan_period(plan, result.period_key)
    expected_days = (period.end_date - period.start_date).days + 1
    expected_observations = len(plan.cells) * len(plan.parameters) * expected_days
    expected_coverage = len(plan.cells) * len(plan.parameters)
    if len(result.payload) < 1 or len(result.payload) > ERA5_LAND_DAILY_MAX_RESPONSE_BYTES:
        raise ValueError("ERA5 result payload violates the reviewed byte boundary")
    if hashlib.sha256(result.payload).hexdigest() != result.payload_checksum:
        raise ValueError("ERA5 result payload checksum does not match its content")
    if len(result.observations) != expected_observations or len(result.coverage) != expected_coverage:
        raise ValueError("ERA5 result has incomplete point, variable, or daily coverage")
    coverage_keys = {(item.cell_key, item.source_parameter) for item in result.coverage}
    expected_keys = {(cell.cell_key, parameter) for cell in plan.cells for parameter in plan.parameters}
    if coverage_keys != expected_keys or any(item.status != "complete" for item in result.coverage):
        raise ValueError("ERA5 coverage audit is not complete for every reviewed point and variable")


def _download_era5_land_monthly(plan: HistoricalEra5LandBackfillPlan, period: Era5LandPeriod) -> bytes:
    """Use the official CDS client with a temporary local target and no credential logging."""
    url, key = _require_cds_credentials()
    request = {
        "variable": plan.parameters,
        "year": period.year,
        "month": period.month,
        "day": period.days,
        "daily_statistic": plan.daily_statistic,
        "time_zone": plan.time_zone,
        "frequency": plan.frequency,
        "area": plan.requested_area.cds_value,
        "grid": [plan.requested_grid_degrees, plan.requested_grid_degrees],
        "data_format": "netcdf",
        "download_format": "zip",
    }
    with tempfile.TemporaryDirectory(prefix="plantgeo-era5-download-") as temporary:
        target = Path(temporary) / f"{period.key}.zip"
        client = cdsapi.Client(url=url, key=key, quiet=True, progress=False)
        try:
            client.retrieve(plan.dataset, request, str(target))
        except Exception as exc:
            _reject_unaccepted_era5_licences(exc, plan.dataset)
            raise
        payload = target.read_bytes()
    if not payload:
        raise ValueError("CDS returned an empty ERA5-Land monthly artifact")
    return payload


def _reject_unaccepted_era5_licences(exc: BaseException, dataset: str) -> None:
    """Restate a CDS licence refusal as an actionable, credential-free operator error.

    The CLI prints ValueError text verbatim and collapses every other exception to its class
    name, so an untranslated refusal surfaces only as "operation failed (HTTPError)" and hides
    the one thing an operator must act on. Licence acceptance is a browser action for the
    account behind CDSAPI_KEY; no retry, credential change, or plan edit can clear it.
    """
    response = getattr(exc, "response", None)
    if response is None or getattr(response, "status_code", None) != ERA5_LAND_LICENCE_REFUSED_HTTP_STATUS:
        return
    try:
        body = response.json()
    except ValueError:
        return
    if not isinstance(body, dict):
        return
    title = str(body.get("title", ""))
    if "licence" not in title.lower() and "license" not in title.lower():
        return
    raise ValueError(
        f"CDS refused the ERA5-Land request for {dataset}: {title}. {body.get('detail', '')} "
        "Accepting a licence is a browser action for the Copernicus account that owns "
        "CDSAPI_KEY; the API client cannot accept it and retrying will not clear it."
    ) from exc


def _require_cds_credentials() -> tuple[str, str]:
    """Resolve CDS credentials -- process environment first, then Settings/`.env` -- or fail closed.

    Both halves are read at call time, not import time, so an operator who exports after the
    process starts is still honoured and a `.env` entry is no longer silently inert. The pair
    goes straight to `cdsapi.Client`; neither value is logged, checkpointed, or persisted, and
    the refusal below names only the variables. See execution/AGENTS.md.
    """
    configured_key = settings.cdsapi_key
    url = os.environ.get("CDSAPI_URL", "").strip() or (settings.cdsapi_url or "").strip()
    key = os.environ.get("CDSAPI_KEY", "").strip() or (
        configured_key.get_secret_value().strip() if configured_key is not None else ""
    )
    if not url or not key:
        raise ValueError(
            "ERA5-Land requires accepted CDS web terms plus CDSAPI_URL and CDSAPI_KEY in the local "
            "operator environment or services/agri-data-service/.env"
        )
    return url, key


def _netcdf_members(payload: bytes) -> list[zipfile.ZipInfo]:
    """Validate bounded archive structure before unpacking NetCDF member content."""
    try:
        with zipfile.ZipFile(_bytes_io(payload)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
    except zipfile.BadZipFile as exc:
        raise ValueError("ERA5 monthly response must be a NetCDF ZIP archive") from exc
    if not members or len(members) > ERA5_LAND_DAILY_MAX_ZIP_MEMBERS:
        raise ValueError("ERA5 archive has an invalid number of source files")
    if any(
        not member.filename.lower().endswith((".nc", ".nc4"))
        or member.filename.startswith(("/", "\\"))
        or ".." in Path(member.filename).parts
        for member in members
    ):
        raise ValueError("ERA5 archive contains an invalid NetCDF member path")
    if sum(member.file_size for member in members) > ERA5_LAND_DAILY_MAX_EXPANDED_BYTES:
        raise ValueError("ERA5 archive exceeds the reviewed expanded-content boundary")
    return sorted(members, key=lambda member: member.filename)


def _resolve_era5_variable_name(dataset: xr.Dataset, parameters: list[str], filename: str) -> tuple[str, str]:
    """Bind exactly one NetCDF variable to one reviewed CDS parameter."""
    candidates: list[str] = []
    lower_filename = filename.lower()
    data_variables = {str(name) for name in dataset.data_vars}
    for parameter in parameters:
        aliases = ERA5_LAND_VARIABLE_ALIASES[parameter]
        if data_variables.intersection(aliases) or any(alias.lower() in lower_filename for alias in aliases):
            candidates.append(parameter)
    if len(candidates) != 1:
        raise ValueError("ERA5 NetCDF file cannot be bound to exactly one reviewed parameter")
    parameter = candidates[0]
    aliases = ERA5_LAND_VARIABLE_ALIASES[parameter]
    matching_variables = sorted(data_variables.intersection(aliases))
    if len(matching_variables) != 1:
        raise ValueError("ERA5 NetCDF file has an ambiguous or missing source variable")
    return parameter, matching_variables[0]


def _era5_values_by_cell_and_date(
    data: xr.DataArray,
    plan: HistoricalEra5LandBackfillPlan,
    period: Era5LandPeriod,
    parameter: str,
) -> dict[str, dict[date, float | None]]:
    """Select only reviewed output points while retaining complete UTC daily coverage."""
    latitude_name = _coordinate_name(data, ("latitude", "lat"))
    longitude_name = _coordinate_name(data, ("longitude", "lon"))
    time_name = _coordinate_name(data, ("time", "valid_time"))
    longitude_values = [float(value) for value in data[longitude_name].values.tolist()]
    uses_360_longitude = (
        bool(longitude_values) and min(longitude_values) >= 0 and max(longitude_values) > WGS84_HALF_CIRCLE_DEGREES
    )
    expected_dates = set(_date_range(period.start_date, period.end_date))
    values_by_cell: dict[str, dict[date, float | None]] = {}
    for cell in plan.cells:
        longitude = cell.longitude % 360 if uses_360_longitude else cell.longitude
        selected = data.sel({latitude_name: cell.latitude, longitude_name: longitude}, method="nearest")
        selected_latitude = float(selected[latitude_name].item())
        selected_longitude = float(selected[longitude_name].item())
        longitude_distance = abs(
            ((selected_longitude - longitude + WGS84_HALF_CIRCLE_DEGREES) % 360) - WGS84_HALF_CIRCLE_DEGREES
        )
        maximum_distance = plan.requested_grid_degrees / 2 + ERA5_LAND_COORDINATE_EPSILON
        if abs(selected_latitude - cell.latitude) > maximum_distance or longitude_distance > maximum_distance:
            raise ValueError(f"ERA5 {parameter} selected a grid point outside the reviewed requested output cell")
        selected = selected.transpose(time_name, ...)
        if selected.ndim != 1:
            raise ValueError("ERA5 daily source variable must have only a time dimension after point selection")
        dates = [_as_utc_date(value) for value in selected[time_name].values]
        if len(dates) != len(set(dates)) or set(dates) != expected_dates:
            raise ValueError(f"ERA5 {parameter} NetCDF time coordinate does not match its requested days")
        values_by_cell[cell.cell_key] = {
            observed_date: _finite_float(value)
            for observed_date, value in zip(dates, selected.values.tolist(), strict=True)
        }
    return values_by_cell


def _normalize_era5_value(parameter: str, value: float | None) -> float | None:
    """Apply only the documented Kelvin-to-Celsius normalization for temperature variables."""
    if value is None:
        return None
    if parameter in {"2m_dewpoint_temperature", "2m_temperature", "soil_temperature_level_1"}:
        return value - 273.15
    return value


def _coordinate_name(data: xr.DataArray, candidates: tuple[str, ...]) -> str:
    """Return one unambiguous expected coordinate name from a NetCDF variable."""
    matches = [name for name in candidates if name in data.coords]
    if len(matches) != 1:
        raise ValueError("ERA5 NetCDF variable is missing an expected coordinate")
    return matches[0]


def _as_utc_date(value: object) -> date:
    """Normalize NetCDF time values to their UTC calendar date without local-time coercion."""
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise ValueError("ERA5 NetCDF time coordinate is not ISO-8601 compatible") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.date()


def _finite_float(value: object) -> float | None:
    """Preserve NetCDF NaN as provider missingness rather than inventing a value."""
    try:
        numeric = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("ERA5 NetCDF values must be numeric") from exc
    return numeric if math.isfinite(numeric) else None


def _plan_period(plan: HistoricalEra5LandBackfillPlan, period_key: str) -> Era5LandPeriod:
    try:
        return next(period for period in plan.periods if period.key == period_key)
    except StopIteration as exc:
        raise ValueError("ERA5 result is not part of the reviewed historical plan") from exc


def _date_range(start: date, end: date) -> Iterator[date]:
    """Yield an inclusive date range with no timezone-dependent arithmetic."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write one local source object atomically so retries never accept a partial cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _bytes_io(payload: bytes) -> BytesIO:
    """Delay the small binary stream dependency until a bounded payload is already validated."""
    return BytesIO(payload)
