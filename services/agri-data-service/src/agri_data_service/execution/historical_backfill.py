"""Bounded contracts for the initial NASA POWER daily historical backfill."""

from __future__ import annotations

import hashlib
import json
import math
import os
from asyncio import sleep as async_sleep
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import Field, field_validator, model_validator

from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.source_ingestion import SourceDefinition  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

NASA_POWER_DAILY_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
NASA_POWER_DAILY_SCHEMA_VERSION: Literal["nasa-power-daily-v1"] = "nasa-power-daily-v1"
NASA_POWER_MAX_RESPONSE_BYTES = 5_000_000
NASA_POWER_TIMEOUT_SECONDS = 30.0
NASA_POWER_MAX_ATTEMPTS = 4
NASA_POWER_MAX_RETRY_AFTER_SECONDS = 60.0
NASA_POWER_MISSING_VALUE_CEILING = -900.0
NASA_POWER_GRID_NAME = "nasa-power-0.5-degree"
NASA_POWER_GRID_RESOLUTION_M = 55_660
NASA_POWER_CELL_HALF_SPAN_DEGREES = 0.25
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_MIN = 500
HTTP_SERVER_ERROR_MAX = 599
HISTORICAL_NASA_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
HISTORICAL_NASA_FINALIZATION_SCHEMA_VERSION: Literal[1] = 1
HISTORICAL_NASA_RAW_CACHE_SCHEMA_VERSION: Literal[1] = 1
WGS84_MAX_LATITUDE = 90
WGS84_MAX_LONGITUDE = 180

# Initial signals are the documented daily meteorology minimum plus the three
# documented soil-wetness depths. Each keeps the provider's analysis-ready unit so
# no unrecorded conversion occurs on ingest. The soil signals report a degree of
# saturation, not a volumetric water content, and name their depth support in the
# signal rather than in support_key. See execution/AGENTS.md §historical_backfill.
NASA_POWER_SIGNAL_SPECIFICATIONS: dict[str, tuple[str, str]] = {
    "ALLSKY_SFC_SW_DWN": ("surface_shortwave_radiation", "MJ/m^2/day"),
    "GWETPROF": ("soil_wetness_profile", "fraction_of_saturation"),
    "GWETROOT": ("soil_wetness_root_zone", "fraction_of_saturation"),
    "GWETTOP": ("soil_wetness_surface", "fraction_of_saturation"),
    "PRECTOTCORR": ("precipitation", "mm/day"),
    "RH2M": ("relative_humidity", "%"),
    "T2M": ("air_temperature_mean", "C"),
    "T2M_MAX": ("air_temperature_max", "C"),
    "T2M_MIN": ("air_temperature_min", "C"),
    "T2MDEW": ("dew_point_temperature", "C"),
    "WS2M": ("wind_speed", "m/s"),
}


class HistoricalBackfillWindow(ContractModel):
    """One inclusive four-calendar-year historical window."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def require_exact_four_calendar_years(self) -> HistoricalBackfillWindow:
        expected_start = _four_calendar_years_before(self.end_date)
        if self.start_date != expected_start:
            raise ValueError(
                "historical backfill windows must start exactly four calendar years before end_date "
                f"({expected_start.isoformat()})"
            )
        return self

    @property
    def day_count(self) -> int:
        """Return the inclusive number of daily observations per signal."""
        return (self.end_date - self.start_date).days + 1


class AnalysisGridCell(ContractModel):
    """One stable analysis-grid centroid authorized for an upstream point request."""

    cell_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def require_finite_coordinate(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError("analysis-cell coordinates must be finite numeric values")
        return value


class NasaPowerDailyPlan(ContractModel):
    """Canonical, source-specific input plan for a four-year POWER daily replay."""

    schema_version: Literal["nasa-power-daily-v1"] = NASA_POWER_DAILY_SCHEMA_VERSION
    window: HistoricalBackfillWindow
    cells: list[AnalysisGridCell] = Field(min_length=1, max_length=10_000)
    parameters: list[str] = Field(min_length=1, max_length=len(NASA_POWER_SIGNAL_SPECIFICATIONS))
    time_standard: str = "UTC"
    grid_name: str = NASA_POWER_GRID_NAME
    grid_resolution_m: int = Field(default=NASA_POWER_GRID_RESOLUTION_M, gt=0)
    cell_half_span_degrees: float = Field(default=NASA_POWER_CELL_HALF_SPAN_DEGREES, gt=0, le=0.25)

    @field_validator("cells")
    @classmethod
    def require_sorted_unique_cells(cls, value: list[AnalysisGridCell]) -> list[AnalysisGridCell]:
        keys = [cell.cell_key for cell in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("cells must be sorted and unique by cell_key")
        return value

    @field_validator("parameters")
    @classmethod
    def require_supported_sorted_parameters(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("parameters must be sorted and unique")
        unsupported = sorted(set(value).difference(NASA_POWER_SIGNAL_SPECIFICATIONS))
        if unsupported:
            raise ValueError(f"unsupported NASA POWER daily parameter(s): {', '.join(unsupported)}")
        return value

    @field_validator("time_standard")
    @classmethod
    def require_utc_time_standard(cls, value: str) -> str:
        if value != "UTC":
            raise ValueError("NASA POWER historical backfills require time_standard='UTC'")
        return value

    @field_validator("grid_name")
    @classmethod
    def require_power_grid_name(cls, value: str) -> str:
        if value != NASA_POWER_GRID_NAME:
            raise ValueError(f"NASA POWER historical backfills require grid_name='{NASA_POWER_GRID_NAME}'")
        return value

    @model_validator(mode="after")
    def require_unique_source_cells(self) -> NasaPowerDailyPlan:
        coordinate_keys = {(cell.latitude, cell.longitude) for cell in self.cells}
        if len(coordinate_keys) != len(self.cells):
            raise ValueError("NASA POWER backfill plans must not repeat a source-grid coordinate")
        if any(
            abs(cell.latitude) + self.cell_half_span_degrees > WGS84_MAX_LATITUDE
            or abs(cell.longitude) + self.cell_half_span_degrees > WGS84_MAX_LONGITUDE
            for cell in self.cells
        ):
            raise ValueError("NASA POWER cell extents must remain within WGS84 bounds")
        return self


class HistoricalNasaBackfillPlan(ContractModel):
    """Reviewed full-run inputs, source governance, and release identity for NASA POWER."""

    source: SourceDefinition
    nasa: NasaPowerDailyPlan
    transform_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_release_set_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "release_set_as_of")

    @model_validator(mode="after")
    def require_nasa_governance(self) -> HistoricalNasaBackfillPlan:
        if self.source.key != "nasa-power-daily":
            raise ValueError("NASA POWER backfill plans require source.key='nasa-power-daily'")
        return self


class HistoricalNasaFinalization(ContractModel):
    """Controlled release-set correction that preserves a completed source replay."""

    schema_version: Literal[1] = HISTORICAL_NASA_FINALIZATION_SCHEMA_VERSION
    source_plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str = Field(min_length=1, max_length=10_000)

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_finalization_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "release_set_as_of")


class HistoricalNasaReceipt(ContractModel):
    """One complete validated upstream shard retained by the local run checkpoint."""

    cell_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=NASA_POWER_MAX_RESPONSE_BYTES)
    observation_count: int = Field(ge=1)
    coverage_count: int = Field(ge=1)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "retrieved_at")


class HistoricalNasaRawCacheReceipt(ContractModel):
    """Checksum-bound provenance for one reusable, complete local source response."""

    schema_version: Literal[1] = HISTORICAL_NASA_RAW_CACHE_SCHEMA_VERSION
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    cell_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=NASA_POWER_MAX_RESPONSE_BYTES)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "retrieved_at")


class HistoricalNasaCheckpoint(ContractModel):
    """Resumable, checksum-bound local state for a reviewed NASA POWER backfill."""

    schema_version: Literal[1] = HISTORICAL_NASA_CHECKPOINT_SCHEMA_VERSION
    state: Literal["initialized", "running", "validated", "blocked"]
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_cache_plan_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    receipts: list[HistoricalNasaReceipt] = Field(default_factory=list, max_length=10_000)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("updated_at")
    @classmethod
    def require_aware_update_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "updated_at")

    @field_validator("receipts")
    @classmethod
    def require_sorted_unique_receipts(cls, value: list[HistoricalNasaReceipt]) -> list[HistoricalNasaReceipt]:
        keys = [receipt.cell_key for receipt in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("historical receipts must be sorted and unique by cell_key")
        return value


@dataclass(frozen=True)
class HistoricalSignalObservation:
    """One normalized source value or explicitly preserved source missingness."""

    cell_key: str
    source_parameter: str
    signal_name: str
    observed_at: datetime
    original_value: float | None
    original_unit: str
    normalized_value: float | None
    normalized_unit: str
    quality_flag: str
    is_observed: bool
    payload_checksum: str
    # What fraction of the sub-daily observations a daily row was actually reduced from. It maps
    # onto `agri.signal_observation.coverage_fraction` (CHECK 0..1). Lanes whose provider publishes
    # one value per day leave it at 1; a lane that reduces hours to a day -- CAMS -- must set it, or
    # an 18-of-24-hour mean is written as `accepted` with no per-row trace that the day was partial.
    coverage_fraction: float = 1.0


@dataclass(frozen=True)
class HistoricalCoverageAudit:
    """Per-cell, per-signal evidence that a requested date window is complete or not."""

    cell_key: str
    source_parameter: str
    signal_name: str
    window_start: datetime
    window_end: datetime
    expected_observation_count: int
    received_observation_count: int
    status: str


@dataclass(frozen=True)
class NasaPowerDailyResult:
    """Validated response plus append-only normalized rows and coverage evidence."""

    cell_key: str
    retrieved_at: datetime
    payload: bytes
    payload_checksum: str
    observations: tuple[HistoricalSignalObservation, ...]
    coverage: tuple[HistoricalCoverageAudit, ...]


def historical_nasa_plan_checksum(plan: HistoricalNasaBackfillPlan) -> str:
    """Fingerprint every reviewed input that controls a resumable historical replay."""
    return hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()


def historical_nasa_checkpoint_path(root: Path, plan: HistoricalNasaBackfillPlan) -> Path:
    """Derive one durable checkpoint location from the complete reviewed plan."""
    return root / "historical-nasa" / f"{historical_nasa_plan_checksum(plan)}.json"


def historical_nasa_raw_cache_paths(
    root: Path,
    plan: HistoricalNasaBackfillPlan,
    cell: AnalysisGridCell,
    *,
    cache_plan_checksum: str | None = None,
) -> tuple[Path, Path]:
    """Return content and receipt paths for one plan-bound raw POWER response."""
    if cell not in plan.nasa.cells:
        raise ValueError("cell is not present in the immutable NASA POWER backfill plan")
    safe_cell_key = hashlib.sha256(cell.cell_key.encode("utf-8")).hexdigest()
    directory = root / "historical-nasa" / (cache_plan_checksum or historical_nasa_plan_checksum(plan)) / "raw"
    return directory / f"{safe_cell_key}.json", directory / f"{safe_cell_key}.receipt.json"


def _plan_cell(plan: NasaPowerDailyPlan, cell_key: str) -> AnalysisGridCell:
    """Resolve one immutable plan cell by its stable external key."""
    for cell in plan.cells:
        if cell.cell_key == cell_key:
            return cell
    raise ValueError("NASA POWER result is not part of the reviewed historical plan")


def rebind_historical_nasa_checkpoint_for_finalization(
    source_plan: HistoricalNasaBackfillPlan,
    finalization: HistoricalNasaFinalization,
    checkpoint: HistoricalNasaCheckpoint,
    *,
    updated_at: datetime | None = None,
) -> tuple[HistoricalNasaBackfillPlan, HistoricalNasaCheckpoint]:
    """Create a new governed release plan only from a complete immutable source replay."""
    source_checksum = historical_nasa_plan_checksum(source_plan)
    if finalization.source_plan_checksum != source_checksum:
        raise ValueError("NASA finalization does not identify the reviewed source plan")
    if checkpoint.plan_checksum != source_checksum:
        raise ValueError("NASA checkpoint does not match the reviewed source plan")
    raw_cache_plan_checksum = checkpoint.raw_cache_plan_checksum or source_checksum
    if raw_cache_plan_checksum != source_checksum:
        raise ValueError("NASA finalization raw cache must remain bound to the reviewed source plan")
    if [receipt.cell_key for receipt in checkpoint.receipts] != [cell.cell_key for cell in source_plan.nasa.cells]:
        raise ValueError("NASA receipt coverage does not match the reviewed cell plan")
    if finalization.release_set_as_of <= source_plan.release_set_as_of:
        raise ValueError("NASA finalization as-of time must advance the reviewed source plan")
    if any(receipt.retrieved_at > finalization.release_set_as_of for receipt in checkpoint.receipts):
        raise ValueError("NASA finalization as-of time must not precede a persisted source receipt")
    release_plan = HistoricalNasaBackfillPlan.model_validate(
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
                "plan_checksum": historical_nasa_plan_checksum(release_plan),
                "raw_cache_plan_checksum": raw_cache_plan_checksum,
                "updated_at": _require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
                "reason": None,
            }
        ),
    )


def initialize_historical_nasa_checkpoint(
    plan: HistoricalNasaBackfillPlan,
    *,
    updated_at: datetime | None = None,
) -> HistoricalNasaCheckpoint:
    """Create an empty checkpoint without making a network or warehouse call."""
    return HistoricalNasaCheckpoint(
        state="initialized",
        plan_checksum=historical_nasa_plan_checksum(plan),
        updated_at=_require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def load_historical_nasa_checkpoint(path: Path) -> HistoricalNasaCheckpoint:
    """Read a local checkpoint without contacting an upstream or warehouse."""
    return HistoricalNasaCheckpoint.model_validate_json(path.read_bytes())


def write_historical_nasa_checkpoint(path: Path, checkpoint: HistoricalNasaCheckpoint) -> None:
    """Atomically persist only validated, credential-free receipt metadata."""
    _atomic_write(path, canonical_json_bytes(checkpoint.model_dump(mode="json")))


def write_historical_nasa_release_plan(path: Path, plan: HistoricalNasaBackfillPlan) -> None:
    """Persist a finalization-derived plan so later local-only steps can bind its checkpoint."""
    payload = canonical_json_bytes(plan.model_dump(mode="json"))
    if path.exists():
        existing = HistoricalNasaBackfillPlan.model_validate_json(path.read_bytes())
        if existing != plan:
            raise ValueError("NASA release plan path already contains a different governed plan")
        return
    _atomic_write(path, payload)


def cache_historical_nasa_result(
    root: Path,
    plan: HistoricalNasaBackfillPlan,
    result: NasaPowerDailyResult,
) -> HistoricalNasaRawCacheReceipt:
    """Durably cache a complete response before any warehouse transaction begins."""
    cell = _plan_cell(plan.nasa, result.cell_key)
    require_complete_nasa_result(plan.nasa, result)
    payload_path, receipt_path = historical_nasa_raw_cache_paths(root, plan, cell)
    receipt = HistoricalNasaRawCacheReceipt(
        plan_checksum=historical_nasa_plan_checksum(plan),
        cell_key=result.cell_key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        retrieved_at=result.retrieved_at,
    )
    if payload_path.exists() or receipt_path.exists():
        cached = load_cached_historical_nasa_result(root, plan, cell)
        if cached is None:
            raise ValueError("NASA raw cache unexpectedly has no reusable source response")
        if cached.payload_checksum != result.payload_checksum or cached.payload != result.payload:
            raise ValueError("NASA raw cache already binds this cell to different source content")
        return HistoricalNasaRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    _atomic_write(payload_path, result.payload)
    _atomic_write(receipt_path, canonical_json_bytes(receipt.model_dump(mode="json")))
    return receipt


def load_cached_historical_nasa_result(
    root: Path,
    plan: HistoricalNasaBackfillPlan,
    cell: AnalysisGridCell,
    *,
    cache_plan_checksum: str | None = None,
) -> NasaPowerDailyResult | None:
    """Return a validated local raw response, or None when this cell has not been captured."""
    expected_cache_plan_checksum = cache_plan_checksum or historical_nasa_plan_checksum(plan)
    payload_path, receipt_path = historical_nasa_raw_cache_paths(
        root,
        plan,
        cell,
        cache_plan_checksum=expected_cache_plan_checksum,
    )
    if not payload_path.exists() and not receipt_path.exists():
        return None
    if not payload_path.exists() or not receipt_path.exists():
        raise ValueError("NASA raw cache has incomplete content or receipt")
    receipt = HistoricalNasaRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    if receipt.plan_checksum != expected_cache_plan_checksum or receipt.cell_key != cell.cell_key:
        raise ValueError("NASA raw cache receipt does not bind this reviewed plan and cell")
    payload = payload_path.read_bytes()
    if len(payload) != receipt.payload_bytes or hashlib.sha256(payload).hexdigest() != receipt.payload_checksum:
        raise ValueError("NASA raw cache payload does not match its receipt")
    result = parse_nasa_power_daily_payload(plan.nasa, cell, payload, retrieved_at=receipt.retrieved_at)
    require_complete_nasa_result(plan.nasa, result)
    return result


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write one durable local object without exposing a partial replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def record_historical_nasa_result(
    plan: HistoricalNasaBackfillPlan,
    checkpoint: HistoricalNasaCheckpoint,
    result: NasaPowerDailyResult,
    *,
    updated_at: datetime | None = None,
) -> HistoricalNasaCheckpoint:
    """Advance a checkpoint only when a complete source cell response passed validation."""
    expected_checksum = historical_nasa_plan_checksum(plan)
    if checkpoint.plan_checksum != expected_checksum:
        raise ValueError("historical checkpoint does not match the reviewed plan")
    cell_keys = {cell.cell_key for cell in plan.nasa.cells}
    if result.cell_key not in cell_keys:
        raise ValueError("NASA POWER result is not part of the reviewed historical plan")
    require_complete_nasa_result(plan.nasa, result)
    receipt = HistoricalNasaReceipt(
        cell_key=result.cell_key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        observation_count=len(result.observations),
        coverage_count=len(result.coverage),
        retrieved_at=result.retrieved_at,
    )
    prior = {item.cell_key: item for item in checkpoint.receipts}
    existing = prior.get(receipt.cell_key)
    if existing is not None and existing != receipt:
        raise ValueError("historical checkpoint already binds this cell to different source content")
    prior[receipt.cell_key] = receipt
    receipts = [prior[key] for key in sorted(prior)]
    state: Literal["initialized", "running", "validated", "blocked"] = (
        "validated" if set(prior) == cell_keys else "running"
    )
    return HistoricalNasaCheckpoint(
        state=state,
        plan_checksum=expected_checksum,
        raw_cache_plan_checksum=checkpoint.raw_cache_plan_checksum,
        receipts=receipts,
        updated_at=_require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def historical_nasa_release_manifest(
    plan: HistoricalNasaBackfillPlan,
    checkpoint: HistoricalNasaCheckpoint,
) -> str:
    """Hash the exact complete source receipt set that a later release set must pin."""
    expected_checksum = historical_nasa_plan_checksum(plan)
    if checkpoint.plan_checksum != expected_checksum or checkpoint.state != "validated":
        raise ValueError("a complete validated checkpoint is required for a historical release manifest")
    expected_cells = [cell.cell_key for cell in plan.nasa.cells]
    actual_cells = [receipt.cell_key for receipt in checkpoint.receipts]
    if actual_cells != expected_cells:
        raise ValueError("historical receipt coverage does not match the reviewed cell plan")
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


def nasa_power_daily_url(plan: NasaPowerDailyPlan, cell: AnalysisGridCell) -> httpx.URL:
    """Build the credential-free, canonical UTC request URL for one analysis cell."""
    return httpx.URL(NASA_POWER_DAILY_URL).copy_merge_params(
        {
            "community": "AG",
            "latitude": _format_coordinate(cell.latitude),
            "longitude": _format_coordinate(cell.longitude),
            "parameters": ",".join(plan.parameters),
            "start": plan.window.start_date.strftime("%Y%m%d"),
            "end": plan.window.end_date.strftime("%Y%m%d"),
            "format": "JSON",
            "time-standard": plan.time_standard,
        }
    )


async def fetch_nasa_power_daily(  # noqa: PLR0913
    plan: NasaPowerDailyPlan,
    cell: AnalysisGridCell,
    *,
    client: httpx.AsyncClient | None = None,
    retrieved_at: datetime | None = None,
    max_attempts: int = NASA_POWER_MAX_ATTEMPTS,
    retry_base_seconds: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = async_sleep,
) -> NasaPowerDailyResult:
    """Fetch one bounded POWER point response and convert it into auditable rows."""
    if cell not in plan.cells:
        raise ValueError("cell is not present in the immutable NASA POWER backfill plan")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if retry_base_seconds < 0:
        raise ValueError("retry_base_seconds cannot be negative")
    timestamp = _require_aware_utc(retrieved_at or datetime.now(UTC), "retrieved_at")
    if client is not None:
        return await _fetch_with_client(
            client,
            plan,
            cell,
            timestamp,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
    timeout = httpx.Timeout(NASA_POWER_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as owned_client:
        return await _fetch_with_client(
            owned_client,
            plan,
            cell,
            timestamp,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )


def parse_nasa_power_daily_payload(
    plan: NasaPowerDailyPlan,
    cell: AnalysisGridCell,
    payload: bytes,
    *,
    retrieved_at: datetime,
) -> NasaPowerDailyResult:
    """Fail closed on an invalid response while retaining source-provided missing values."""
    if cell not in plan.cells:
        raise ValueError("cell is not present in the immutable NASA POWER backfill plan")
    if len(payload) > NASA_POWER_MAX_RESPONSE_BYTES:
        raise ValueError(f"NASA POWER response exceeds {NASA_POWER_MAX_RESPONSE_BYTES} bytes")
    timestamp = _require_aware_utc(retrieved_at, "retrieved_at")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NASA POWER response must be UTF-8 JSON") from exc
    parameters = _extract_parameter_values(decoded)
    missing_parameters = [parameter for parameter in plan.parameters if parameter not in parameters]
    if missing_parameters:
        raise ValueError(f"NASA POWER response is missing requested parameter(s): {', '.join(missing_parameters)}")

    checksum = hashlib.sha256(payload).hexdigest()
    days = tuple(_inclusive_dates(plan.window))
    observations: list[HistoricalSignalObservation] = []
    coverage: list[HistoricalCoverageAudit] = []
    for parameter in plan.parameters:
        signal_name, unit = NASA_POWER_SIGNAL_SPECIFICATIONS[parameter]
        series = parameters[parameter]
        received = 0
        for day in days:
            raw_value = _source_numeric_value(series.get(day.strftime("%Y%m%d")))
            is_observed = raw_value is not None
            if is_observed:
                received += 1
            observations.append(
                HistoricalSignalObservation(
                    cell_key=cell.cell_key,
                    source_parameter=parameter,
                    signal_name=signal_name,
                    observed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
                    original_value=raw_value,
                    original_unit=unit,
                    normalized_value=raw_value,
                    normalized_unit=unit,
                    quality_flag="accepted" if is_observed else "source_missing",
                    is_observed=is_observed,
                    payload_checksum=checksum,
                )
            )
        coverage.append(
            HistoricalCoverageAudit(
                cell_key=cell.cell_key,
                source_parameter=parameter,
                signal_name=signal_name,
                window_start=datetime.combine(plan.window.start_date, datetime.min.time(), tzinfo=UTC),
                window_end=datetime.combine(plan.window.end_date, datetime.max.time(), tzinfo=UTC),
                expected_observation_count=len(days),
                received_observation_count=received,
                status=_coverage_status(expected=len(days), received=received),
            )
        )
    return NasaPowerDailyResult(
        cell_key=cell.cell_key,
        retrieved_at=timestamp,
        payload=payload,
        payload_checksum=checksum,
        observations=tuple(observations),
        coverage=tuple(coverage),
    )


async def _fetch_with_client(  # noqa: PLR0913
    client: httpx.AsyncClient,
    plan: NasaPowerDailyPlan,
    cell: AnalysisGridCell,
    retrieved_at: datetime,
    *,
    max_attempts: int,
    retry_base_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> NasaPowerDailyResult:
    url = nasa_power_daily_url(plan, cell)
    for attempt in range(1, max_attempts + 1):
        try:
            async with client.stream(
                "GET",
                url,
                headers={"Accept": "application/json"},
                follow_redirects=False,
                timeout=NASA_POWER_TIMEOUT_SECONDS,
            ) as response:
                if _is_retryable_status(response.status_code) and attempt < max_attempts:
                    retry_after = _retry_delay(response, attempt, retry_base_seconds)
                else:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if "json" not in content_type.lower():
                        raise ValueError("NASA POWER response must declare a JSON content type")
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > NASA_POWER_MAX_RESPONSE_BYTES:
                            raise ValueError(f"NASA POWER response exceeds {NASA_POWER_MAX_RESPONSE_BYTES} bytes")
                    return parse_nasa_power_daily_payload(plan, cell, bytes(payload), retrieved_at=retrieved_at)
        except httpx.TransportError:
            if attempt == max_attempts:
                raise
            retry_after = retry_base_seconds * (2 ** (attempt - 1))
        await sleep(retry_after)
    raise RuntimeError("NASA POWER fetch exhausted without a response")


def _extract_parameter_values(decoded: object) -> dict[str, dict[str, object]]:
    if not isinstance(decoded, dict):
        raise ValueError("NASA POWER response must be a JSON object")
    properties = decoded.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("NASA POWER response is missing properties")
    parameter_values = properties.get("parameter")
    if not isinstance(parameter_values, dict):
        raise ValueError("NASA POWER response is missing properties.parameter")
    result: dict[str, dict[str, object]] = {}
    for parameter, series in parameter_values.items():
        if not isinstance(parameter, str) or not isinstance(series, dict):
            raise ValueError("NASA POWER parameter series must be string-keyed JSON objects")
        result[parameter] = series
    return result


def _source_numeric_value(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("NASA POWER parameter values must be numeric or null")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("NASA POWER parameter values must be finite")
    # POWER uses numeric fill values for unavailable daily observations. Preserve
    # the missingness rather than treating a sentinel as an environmental value.
    return None if number <= NASA_POWER_MISSING_VALUE_CEILING else number


def _coverage_status(*, expected: int, received: int) -> str:
    if received == expected:
        return "complete"
    if received == 0:
        return "no_data"
    return "partial"


def require_complete_nasa_result(plan: NasaPowerDailyPlan, result: NasaPowerDailyResult) -> None:
    expected_observations = plan.window.day_count * len(plan.parameters)
    if len(result.observations) != expected_observations or len(result.coverage) != len(plan.parameters):
        raise ValueError("NASA POWER result does not cover every requested day and parameter")
    coverage_by_parameter = {item.source_parameter: item for item in result.coverage}
    if set(coverage_by_parameter) != set(plan.parameters):
        raise ValueError("NASA POWER result has an unexpected coverage parameter set")
    incomplete = [
        parameter
        for parameter, coverage in coverage_by_parameter.items()
        if coverage.status != "complete"
        or coverage.expected_observation_count != plan.window.day_count
        or coverage.received_observation_count != plan.window.day_count
    ]
    if incomplete:
        raise ValueError(f"NASA POWER result has incomplete coverage: {', '.join(sorted(incomplete))}")


def _is_retryable_status(status_code: int) -> bool:
    return status_code == HTTP_TOO_MANY_REQUESTS or HTTP_SERVER_ERROR_MIN <= status_code <= HTTP_SERVER_ERROR_MAX


def _retry_delay(response: httpx.Response, attempt: int, retry_base_seconds: float) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            return max(0.0, min(float(retry_after), NASA_POWER_MAX_RETRY_AFTER_SECONDS))
        except ValueError:
            pass
    return retry_base_seconds * float(2 ** (attempt - 1))


def _inclusive_dates(window: HistoricalBackfillWindow) -> list[date]:
    return [window.start_date + timedelta(days=offset) for offset in range(window.day_count)]


def _four_calendar_years_before(value: date) -> date:
    try:
        return value.replace(year=value.year - 4)
    except ValueError:
        # A February 29 end date maps to February 28 when the matching past
        # calendar year is not a leap year.
        return value.replace(year=value.year - 4, day=28)


def _format_coordinate(value: float) -> str:
    return format(value, ".6f").rstrip("0").rstrip(".")


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)
