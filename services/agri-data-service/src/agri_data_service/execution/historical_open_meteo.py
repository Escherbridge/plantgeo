"""Cache-first contracts for the Open-Meteo ERA5-Land archive replay over the NDVI lattice."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from pydantic import Field, field_validator, model_validator

from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.historical_backfill import (
    AnalysisGridCell,
    HistoricalBackfillWindow,
    HistoricalCoverageAudit,
    HistoricalSignalObservation,
)
from agri_data_service.execution.source_ingestion import SourceDefinition  # noqa: TC001
from agri_data_service.ingest.http import UpstreamError, upstream_client
from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_ARCHIVE_BASE_URL,
    OPEN_METEO_ARCHIVE_BOUNDS,
    OPEN_METEO_ARCHIVE_CELL_SELECTION,
    OPEN_METEO_ERA5_LAND_MODEL,
    OpenMeteoArchiveBaseUrl,
    OpenMeteoRateLimitError,
    archive_daily_request,
    archive_daily_url,
    fetch_archive_daily,
    require_archive_base_url,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    import httpx

OPEN_METEO_ARCHIVE_SCHEMA_VERSION: Literal["open-meteo-era5-land-archive-daily-v1"] = (
    "open-meteo-era5-land-archive-daily-v1"
)
OPEN_METEO_ARCHIVE_SOURCE_KEY: Final = "open-meteo-era5-land-archive"

# Deliberately not `surface`; see execution/AGENTS.md §historical_open_meteo.
OPEN_METEO_ARCHIVE_SUPPORT_KEY: Final = "era5-land-0.1deg"

OPEN_METEO_ARCHIVE_NATIVE_GRID_NAME: Final = "era5-land-0.1-degree"
OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES: Final = 0.1
OPEN_METEO_ARCHIVE_NATIVE_RESOLUTION_M: Final = 9_000

# Half the native grid spacing plus a float-comparison epsilon. A returned point further than this
# from the requested centroid is a different grid box and must fail rather than be attributed.
OPEN_METEO_ARCHIVE_MAX_GRID_OFFSET_DEGREES: Final = 0.05 + 1e-6

OPEN_METEO_ARCHIVE_MAX_RESPONSE_BYTES: Final = OPEN_METEO_ARCHIVE_BOUNDS.max_bytes
OPEN_METEO_ARCHIVE_MAX_CELLS: Final = 10_000
OPEN_METEO_ARCHIVE_MAX_CHUNKS: Final = 2_000
OPEN_METEO_ARCHIVE_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
OPEN_METEO_ARCHIVE_RAW_CACHE_SCHEMA_VERSION: Literal[1] = 1

# Only a minutely refusal is waited out; every other scope, including an unrecognised body, fails
# closed. See execution/AGENTS.md §historical_open_meteo.
RATE_LIMIT_BACKOFF_SECONDS: Final = {"minute": 70.0}
TRANSPORT_BACKOFF_SECONDS: Final = 15.0
MAX_FETCH_ATTEMPTS: Final = 4
DEFAULT_CHUNK_CONCURRENCY: Final = 2
MAX_CHUNK_CONCURRENCY: Final = 4

ISO_DATE_LENGTH: Final = 10


class OpenMeteoArchiveSignal(NamedTuple):
    """One daily variable's warehouse naming, units, and inclusive physical acceptance range."""

    signal_name: str
    original_unit: str
    normalized_unit: str
    minimum: float
    maximum: float


# Open-Meteo daily variable -> warehouse signal, units, and the range a value must fall inside.
# The bounds are the only thing standing between a provider sentinel (-999, a netCDF `_FillValue`)
# and 1,462 accepted rows per cell. See execution/AGENTS.md §historical_open_meteo.
OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS: Final[dict[str, OpenMeteoArchiveSignal]] = {
    "soil_moisture_0_to_7cm_mean": OpenMeteoArchiveSignal(
        "soil_water_content_layer_1", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_moisture_7_to_28cm_mean": OpenMeteoArchiveSignal(
        "soil_water_content_layer_2", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_moisture_28_to_100cm_mean": OpenMeteoArchiveSignal(
        "soil_water_content_layer_3", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_temperature_0_to_7cm_mean": OpenMeteoArchiveSignal("soil_temperature_level_1", "C", "C", -100.0, 70.0),
    # The remaining three bands align with ERA5-Land's CDS levels 2-4, so this lane can carry the
    # soil-state profile the CDS lane was fetching -- at 0.1 degrees instead of 1.0, and keyless.
    "soil_temperature_7_to_28cm_mean": OpenMeteoArchiveSignal("soil_temperature_level_2", "C", "C", -100.0, 70.0),
    "soil_temperature_28_to_100cm_mean": OpenMeteoArchiveSignal(
        "soil_temperature_level_3", "C", "C", -100.0, 70.0
    ),
    "soil_temperature_100_to_255cm_mean": OpenMeteoArchiveSignal(
        "soil_temperature_level_4", "C", "C", -100.0, 70.0
    ),
    # An atmospheric-dryness covariate, not a soil-state one; see execution/AGENTS.md §historical_open_meteo.
    "vapour_pressure_deficit_max": OpenMeteoArchiveSignal("vapor_pressure_deficit", "kPa", "kPa", 0.0, 15.0),
}

OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS: Final = (
    "soil_moisture_0_to_7cm_mean",
    "soil_moisture_28_to_100cm_mean",
    "soil_moisture_7_to_28cm_mean",
)

# The four ERA5-Land soil-temperature bands, sorted to match the plan validator's ordering rule.
OPEN_METEO_ARCHIVE_SOIL_TEMPERATURE_PARAMETERS: Final = (
    "soil_temperature_0_to_7cm_mean",
    "soil_temperature_100_to_255cm_mean",
    "soil_temperature_28_to_100cm_mean",
    "soil_temperature_7_to_28cm_mean",
)


@dataclass(frozen=True)
class OpenMeteoArchiveChunk:
    """One bounded multi-location archive request derived from the reviewed plan's cell order."""

    key: str
    cells: tuple[AnalysisGridCell, ...]


@dataclass(frozen=True)
class OpenMeteoArchiveCapture:
    """What is known about one retrieval before its content is normalized."""

    retrieved_at: datetime
    wire_payload_bytes: int
    wire_payload_checksum: str
    # The host this retrieval really answered from; see execution/AGENTS.md §historical_open_meteo.
    request_base_url: OpenMeteoArchiveBaseUrl = OPEN_METEO_ARCHIVE_BASE_URL


class HistoricalOpenMeteoArchivePlan(ContractModel):
    """Reviewed four-year Open-Meteo ERA5-Land archive replay over an existing analysis lattice."""

    schema_version: Literal["open-meteo-era5-land-archive-daily-v1"] = OPEN_METEO_ARCHIVE_SCHEMA_VERSION
    source: SourceDefinition
    window: HistoricalBackfillWindow
    model: Literal["era5_land"] = OPEN_METEO_ERA5_LAND_MODEL
    cell_selection: Literal["nearest"] = OPEN_METEO_ARCHIVE_CELL_SELECTION
    time_zone: Literal["GMT"] = "GMT"
    grid_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,98}$")
    grid_resolution_m: int = Field(gt=0)
    native_grid_name: Literal["era5-land-0.1-degree"] = OPEN_METEO_ARCHIVE_NATIVE_GRID_NAME
    native_grid_degrees: float = Field(gt=0, le=1)
    native_grid_resolution_m: int = Field(gt=0)
    support_key: Literal["era5-land-0.1deg"] = OPEN_METEO_ARCHIVE_SUPPORT_KEY
    cells: list[AnalysisGridCell] = Field(min_length=1, max_length=OPEN_METEO_ARCHIVE_MAX_CELLS)
    chunk_cell_count: int = Field(ge=1, le=200)
    parameters: list[str] = Field(min_length=1, max_length=len(OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS))
    transform_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("cells")
    @classmethod
    def require_sorted_unique_cells(cls, value: list[AnalysisGridCell]) -> list[AnalysisGridCell]:
        keys = [cell.cell_key for cell in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("Open-Meteo archive cells must be sorted and unique by cell_key")
        if len({(cell.latitude, cell.longitude) for cell in value}) != len(value):
            raise ValueError("Open-Meteo archive cells must not repeat a requested coordinate")
        return value

    @field_validator("parameters")
    @classmethod
    def require_supported_sorted_parameters(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("Open-Meteo archive parameters must be sorted and unique")
        unsupported = sorted(set(value).difference(OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS))
        if unsupported:
            raise ValueError(f"unsupported Open-Meteo archive parameter(s): {', '.join(unsupported)}")
        return value

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_release_set_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "release_set_as_of")

    @model_validator(mode="after")
    def require_governed_lattice(self) -> HistoricalOpenMeteoArchivePlan:
        if self.source.key != OPEN_METEO_ARCHIVE_SOURCE_KEY:
            raise ValueError(f"Open-Meteo archive plans require source.key='{OPEN_METEO_ARCHIVE_SOURCE_KEY}'")
        if self.native_grid_degrees != OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES:
            raise ValueError("Open-Meteo archive plans must record the product's 0.1-degree native grid")
        if self.native_grid_resolution_m != OPEN_METEO_ARCHIVE_NATIVE_RESOLUTION_M:
            raise ValueError("Open-Meteo archive plans must record the product's documented 9-km resolution")
        nearest_points = {_nearest_native_grid_point(cell) for cell in self.cells}
        if len(nearest_points) != len(self.cells):
            raise ValueError("Open-Meteo archive cells must not share a native grid point")
        if len(self.chunks) > OPEN_METEO_ARCHIVE_MAX_CHUNKS:
            raise ValueError("Open-Meteo archive plan exceeds the reviewed chunk ceiling")
        return self

    @cached_property
    def chunks(self) -> tuple[OpenMeteoArchiveChunk, ...]:
        """Cut the sorted cell list into stable request chunks anchored at the first cell."""
        size = self.chunk_cell_count
        return tuple(
            OpenMeteoArchiveChunk(key=f"cells-{index // size:04d}", cells=tuple(self.cells[index : index + size]))
            for index in range(0, len(self.cells), size)
        )

    @cached_property
    def plan_checksum(self) -> str:
        """Fingerprint every governed input controlling this replay, once per validated instance."""
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


class HistoricalOpenMeteoReceipt(ContractModel):
    """One fully validated, cache-backed archive chunk receipt."""

    chunk_key: str = Field(pattern=r"^cells-\d{4}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=OPEN_METEO_ARCHIVE_MAX_RESPONSE_BYTES)
    cell_count: int = Field(ge=1)
    observation_count: int = Field(ge=0)
    observed_value_count: int = Field(ge=0)
    coverage_count: int = Field(ge=1)
    no_data_series_count: int = Field(ge=0)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "retrieved_at")


class HistoricalOpenMeteoRawCacheReceipt(ContractModel):
    """Checksum-bound metadata for one reusable archive chunk download."""

    schema_version: Literal[1] = OPEN_METEO_ARCHIVE_RAW_CACHE_SCHEMA_VERSION
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_key: str = Field(pattern=r"^cells-\d{4}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=OPEN_METEO_ARCHIVE_MAX_RESPONSE_BYTES)
    wire_payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    wire_payload_bytes: int = Field(ge=1, le=OPEN_METEO_ARCHIVE_MAX_RESPONSE_BYTES)
    retrieved_at: datetime
    # Additive within schema_version 1: a receipt written without it predates the paid host, so the
    # free host is derived, not defaulted. See execution/AGENTS.md §historical_open_meteo.
    request_base_url: OpenMeteoArchiveBaseUrl = OPEN_METEO_ARCHIVE_BASE_URL

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "retrieved_at")


class HistoricalOpenMeteoCheckpoint(ContractModel):
    """Durable resumable state for the complete chunked archive plan."""

    schema_version: Literal[1] = OPEN_METEO_ARCHIVE_CHECKPOINT_SCHEMA_VERSION
    state: Literal["initialized", "running", "validated", "blocked"]
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipts: list[HistoricalOpenMeteoReceipt] = Field(default_factory=list, max_length=OPEN_METEO_ARCHIVE_MAX_CHUNKS)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("updated_at")
    @classmethod
    def require_aware_update_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "updated_at")

    @field_validator("receipts")
    @classmethod
    def require_sorted_unique_receipts(
        cls,
        value: list[HistoricalOpenMeteoReceipt],
    ) -> list[HistoricalOpenMeteoReceipt]:
        keys = [receipt.chunk_key for receipt in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("Open-Meteo archive receipts must be sorted and unique by chunk_key")
        return value


@dataclass(frozen=True)
class OpenMeteoArchiveChunkResult:
    """One validated canonical archive document plus its normalized daily facts and coverage evidence."""

    chunk_key: str
    retrieved_at: datetime
    payload: bytes
    payload_checksum: str
    wire_payload_bytes: int
    wire_payload_checksum: str
    request_base_url: OpenMeteoArchiveBaseUrl
    observations: tuple[HistoricalSignalObservation, ...]
    coverage: tuple[HistoricalCoverageAudit, ...]
    grid_points: tuple[tuple[str, float, float, float | None], ...]


def open_meteo_archive_chunk_url(
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
    *,
    base_url: str | None = None,
) -> str:
    """Return the credential-free request one chunk is answered by, so a release records a reproducible query."""
    _plan_chunk(plan, chunk.key)
    return archive_daily_url(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        plan.window.start_date,
        plan.window.end_date,
        base_url=base_url,
    )


def historical_open_meteo_plan_checksum(plan: HistoricalOpenMeteoArchivePlan) -> str:
    """Fingerprint every governed input controlling this replay; memoized on the validated plan."""
    return plan.plan_checksum


def historical_open_meteo_checkpoint_path(root: Path, plan: HistoricalOpenMeteoArchivePlan) -> Path:
    """Return a plan-bound durable checkpoint file path."""
    return root / "historical-open-meteo" / f"{historical_open_meteo_plan_checksum(plan)}.json"


def historical_open_meteo_raw_cache_paths(
    root: Path,
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
) -> tuple[Path, Path]:
    """Return canonical-document and receipt locations for one chunk beneath the local run root."""
    _plan_chunk(plan, chunk.key)
    directory = root / "historical-open-meteo" / historical_open_meteo_plan_checksum(plan) / "raw"
    return directory / f"{chunk.key}.json", directory / f"{chunk.key}.receipt.json"


def initialize_historical_open_meteo_checkpoint(
    plan: HistoricalOpenMeteoArchivePlan,
    *,
    updated_at: datetime | None = None,
) -> HistoricalOpenMeteoCheckpoint:
    """Create an empty checkpoint without opening the network or PostgreSQL."""
    return HistoricalOpenMeteoCheckpoint(
        state="initialized",
        plan_checksum=historical_open_meteo_plan_checksum(plan),
        updated_at=_require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def load_historical_open_meteo_checkpoint(path: Path) -> HistoricalOpenMeteoCheckpoint:
    """Read a local archive checkpoint without requesting a provider payload."""
    return HistoricalOpenMeteoCheckpoint.model_validate_json(path.read_bytes())


def write_historical_open_meteo_checkpoint(path: Path, checkpoint: HistoricalOpenMeteoCheckpoint) -> None:
    """Atomically update credential-free archive checkpoint metadata."""
    _atomic_write(path, canonical_json_bytes(checkpoint.model_dump(mode="json")))


def rederive_historical_open_meteo_checkpoint_state(
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
) -> HistoricalOpenMeteoCheckpoint:
    """Recompute `state` from receipt completeness so a recorded `blocked` cannot outlive its cause.

    `reason` is preserved: it is the evidence of the last stop, and only `state` gates a resume.
    """
    if checkpoint.plan_checksum != historical_open_meteo_plan_checksum(plan):
        raise ValueError("Open-Meteo archive checkpoint does not bind the reviewed plan")
    receipted = {receipt.chunk_key for receipt in checkpoint.receipts}
    if not receipted:
        derived = "initialized"
    elif all(chunk.key in receipted for chunk in plan.chunks):
        derived = "validated"
    else:
        derived = "running"
    if derived == checkpoint.state:
        return checkpoint
    return checkpoint.model_copy(update={"state": derived})


def record_historical_open_meteo_result(
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
    result: OpenMeteoArchiveChunkResult,
    *,
    updated_at: datetime | None = None,
) -> HistoricalOpenMeteoCheckpoint:
    """Advance a chunk receipt only after every requested cell, signal and day is accounted for."""
    if checkpoint.plan_checksum != historical_open_meteo_plan_checksum(plan):
        raise ValueError("Open-Meteo archive checkpoint does not bind the reviewed plan")
    chunk = _plan_chunk(plan, result.chunk_key)
    require_accounted_open_meteo_result(plan, result)
    receipt = HistoricalOpenMeteoReceipt(
        chunk_key=result.chunk_key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        cell_count=len(chunk.cells),
        observation_count=len(result.observations),
        observed_value_count=sum(1 for item in result.observations if item.is_observed),
        coverage_count=len(result.coverage),
        no_data_series_count=sum(1 for item in result.coverage if item.status == "no_data"),
        retrieved_at=result.retrieved_at,
    )
    prior = {item.chunk_key: item for item in checkpoint.receipts}
    existing = prior.get(receipt.chunk_key)
    if existing is not None and existing != receipt:
        raise ValueError("Open-Meteo archive checkpoint already binds this chunk to different source content")
    prior[receipt.chunk_key] = receipt
    receipts = [prior[key] for key in sorted(prior)]
    complete = [item.key for item in plan.chunks] == [item.chunk_key for item in receipts]
    return checkpoint.model_copy(
        update={
            "state": "validated" if complete else "running",
            "receipts": receipts,
            "updated_at": _require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
            "reason": None,
        }
    )


def historical_open_meteo_release_manifest(
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
) -> str:
    """Hash the complete ordered chunk receipt set a release must pin."""
    expected_checksum = historical_open_meteo_plan_checksum(plan)
    if checkpoint.plan_checksum != expected_checksum or checkpoint.state != "validated":
        raise ValueError("a complete validated checkpoint is required for an Open-Meteo archive release manifest")
    expected_chunks = [chunk.key for chunk in plan.chunks]
    if [receipt.chunk_key for receipt in checkpoint.receipts] != expected_chunks:
        raise ValueError("Open-Meteo archive receipt coverage does not match the reviewed chunk plan")
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "plan_checksum": expected_checksum,
                "transform_version": plan.transform_version,
                "receipts": [receipt.model_dump(mode="json") for receipt in checkpoint.receipts],
            }
        )
    ).hexdigest()


def cache_historical_open_meteo_result(
    root: Path,
    plan: HistoricalOpenMeteoArchivePlan,
    result: OpenMeteoArchiveChunkResult,
) -> HistoricalOpenMeteoRawCacheReceipt:
    """Persist one accounted-for canonical chunk document before the warehouse transaction begins."""
    chunk = _plan_chunk(plan, result.chunk_key)
    require_accounted_open_meteo_result(plan, result)
    payload_path, receipt_path = historical_open_meteo_raw_cache_paths(root, plan, chunk)
    receipt = HistoricalOpenMeteoRawCacheReceipt(
        plan_checksum=historical_open_meteo_plan_checksum(plan),
        chunk_key=chunk.key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        wire_payload_checksum=result.wire_payload_checksum,
        wire_payload_bytes=result.wire_payload_bytes,
        retrieved_at=result.retrieved_at,
        request_base_url=require_archive_base_url(result.request_base_url),
    )
    if payload_path.exists() or receipt_path.exists():
        cached = load_cached_historical_open_meteo_result(root, plan, chunk)
        if cached is None:
            raise ValueError("Open-Meteo archive raw cache unexpectedly has no reusable source document")
        if cached.payload_checksum != result.payload_checksum:
            raise ValueError("Open-Meteo archive raw cache already binds this chunk to different source content")
        return HistoricalOpenMeteoRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    _atomic_write(payload_path, result.payload)
    _atomic_write(receipt_path, canonical_json_bytes(receipt.model_dump(mode="json")))
    return receipt


def load_cached_historical_open_meteo_result(
    root: Path,
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
) -> OpenMeteoArchiveChunkResult | None:
    """Re-parse one validated local chunk document, never contacting the provider."""
    payload_path, receipt_path = historical_open_meteo_raw_cache_paths(root, plan, chunk)
    if not payload_path.exists() and not receipt_path.exists():
        return None
    if not payload_path.exists() or not receipt_path.exists():
        raise ValueError("Open-Meteo archive raw cache has incomplete content or receipt")
    receipt = HistoricalOpenMeteoRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    if receipt.plan_checksum != historical_open_meteo_plan_checksum(plan) or receipt.chunk_key != chunk.key:
        raise ValueError("Open-Meteo archive raw cache receipt does not bind this reviewed plan and chunk")
    payload = payload_path.read_bytes()
    if len(payload) != receipt.payload_bytes or hashlib.sha256(payload).hexdigest() != receipt.payload_checksum:
        raise ValueError("Open-Meteo archive raw cache payload does not match its receipt")
    result = parse_open_meteo_archive_payload(
        plan,
        chunk,
        payload,
        OpenMeteoArchiveCapture(
            retrieved_at=receipt.retrieved_at,
            wire_payload_bytes=receipt.wire_payload_bytes,
            wire_payload_checksum=receipt.wire_payload_checksum,
            request_base_url=receipt.request_base_url,
        ),
    )
    require_accounted_open_meteo_result(plan, result)
    return result


async def fetch_open_meteo_archive_chunk(
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
    *,
    client: httpx.AsyncClient,
    retrieved_at: datetime | None = None,
    sleep: object = None,
) -> OpenMeteoArchiveChunkResult:
    """Fetch one chunk with bounded retries; an exhausted quota is raised, never swallowed."""
    _plan_chunk(plan, chunk.key)
    # The credential lives only in `request.request_url`; only `request.base_url` is ever recorded.
    request = archive_daily_request(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        plan.window.start_date,
        plan.window.end_date,
    )
    waiter = sleep if callable(sleep) else asyncio.sleep
    last_error: UpstreamError | None = None
    attempt = 0
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            body = await fetch_archive_daily(client, request.request_url)
        except OpenMeteoRateLimitError as error:
            last_error = error
            backoff = RATE_LIMIT_BACKOFF_SECONDS.get(error.scope)
            if backoff is None or attempt == MAX_FETCH_ATTEMPTS:
                break
            await waiter(backoff)
            continue
        except UpstreamError as error:
            last_error = error
            if attempt == MAX_FETCH_ATTEMPTS:
                break
            await waiter(TRANSPORT_BACKOFF_SECONDS * attempt)
            continue
        wire_payload = body.encode("utf-8")
        return parse_open_meteo_archive_payload(
            plan,
            chunk,
            _canonical_archive_document(wire_payload),
            OpenMeteoArchiveCapture(
                retrieved_at=_require_aware_utc(retrieved_at or datetime.now(UTC), "retrieved_at"),
                wire_payload_bytes=len(wire_payload),
                wire_payload_checksum=hashlib.sha256(wire_payload).hexdigest(),
                request_base_url=request.base_url,
            ),
        )
    raise OpenMeteoArchiveFetchError(chunk.key, last_error, attempt)


class OpenMeteoArchiveFetchError(RuntimeError):
    """Raised when a chunk stopped for good; carries the chunk so a resume is unambiguous."""

    def __init__(self, chunk_key: str, cause: UpstreamError | None, attempts: int) -> None:
        """Name the chunk, how many attempts it really made, and the provider condition that stopped it."""
        detail = "no upstream response" if cause is None else str(cause)
        plural = "attempt" if attempts == 1 else "attempts"
        super().__init__(f"Open-Meteo archive chunk {chunk_key} failed after {attempts} {plural}: {detail}")
        self.chunk_key = chunk_key
        self.cause = cause
        self.attempts = attempts


async def run_open_meteo_archive_chunks(
    plan: HistoricalOpenMeteoArchivePlan,
    chunks: Sequence[OpenMeteoArchiveChunk],
    *,
    concurrency: int = DEFAULT_CHUNK_CONCURRENCY,
    client: httpx.AsyncClient | None = None,
) -> list[OpenMeteoArchiveChunkResult | BaseException]:
    """Fetch several chunks under a bounded semaphore, preserving each chunk's own failure."""
    if not 1 <= concurrency <= MAX_CHUNK_CONCURRENCY:
        raise ValueError("Open-Meteo archive chunk concurrency must be between 1 and 4")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(active: httpx.AsyncClient, chunk: OpenMeteoArchiveChunk) -> OpenMeteoArchiveChunkResult:
        async with semaphore:
            return await fetch_open_meteo_archive_chunk(plan, chunk, client=active)

    if client is None:
        async with upstream_client(OPEN_METEO_ARCHIVE_BOUNDS) as owned:
            return await asyncio.gather(*(one(owned, chunk) for chunk in chunks), return_exceptions=True)
    return await asyncio.gather(*(one(client, chunk) for chunk in chunks), return_exceptions=True)


def parse_open_meteo_archive_payload(
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
    payload: bytes,
    capture: OpenMeteoArchiveCapture,
) -> OpenMeteoArchiveChunkResult:
    """Validate one canonical archive document and normalize every reviewed cell/signal/day."""
    _plan_chunk(plan, chunk.key)
    timestamp = _require_aware_utc(capture.retrieved_at, "retrieved_at")
    if not payload or len(payload) > OPEN_METEO_ARCHIVE_MAX_RESPONSE_BYTES:
        raise ValueError("Open-Meteo archive response exceeds the reviewed byte boundary")
    locations = _archive_locations(payload, len(chunk.cells))
    expected_dates = list(_date_range(plan.window.start_date, plan.window.end_date))
    payload_checksum = hashlib.sha256(payload).hexdigest()

    observations: list[HistoricalSignalObservation] = []
    coverage: list[HistoricalCoverageAudit] = []
    grid_points: list[tuple[str, float, float, float | None]] = []
    seen_grid_points: set[tuple[float, float]] = set()
    for cell, location in zip(chunk.cells, locations, strict=True):
        latitude, longitude, elevation = _validated_grid_point(cell, location)
        if (latitude, longitude) in seen_grid_points:
            raise ValueError("Open-Meteo archive returned one native grid point for two reviewed cells")
        seen_grid_points.add((latitude, longitude))
        grid_points.append((cell.cell_key, latitude, longitude, elevation))
        daily = _archive_daily_block(location, plan.parameters)
        days = _archive_days(daily, expected_dates)
        for parameter in plan.parameters:
            specification = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter]
            values = _archive_values(daily, parameter, specification, len(days))
            observed_count = sum(1 for value in values if value is not None)
            if observed_count:
                observations.extend(
                    _cell_observations(
                        cell_key=cell.cell_key,
                        parameter=parameter,
                        specification=specification,
                        dated_values=list(zip(days, values, strict=True)),
                        payload_checksum=payload_checksum,
                    )
                )
            coverage.append(
                HistoricalCoverageAudit(
                    cell_key=cell.cell_key,
                    source_parameter=parameter,
                    signal_name=specification.signal_name,
                    window_start=datetime.combine(plan.window.start_date, time.min, tzinfo=UTC),
                    window_end=datetime.combine(plan.window.end_date, time.max, tzinfo=UTC),
                    expected_observation_count=len(days),
                    received_observation_count=observed_count,
                    status=_coverage_status(observed_count, len(days)),
                )
            )
    result = OpenMeteoArchiveChunkResult(
        chunk_key=chunk.key,
        retrieved_at=timestamp,
        payload=payload,
        payload_checksum=payload_checksum,
        wire_payload_bytes=capture.wire_payload_bytes,
        wire_payload_checksum=capture.wire_payload_checksum,
        request_base_url=require_archive_base_url(capture.request_base_url),
        observations=tuple(observations),
        coverage=tuple(coverage),
        grid_points=tuple(grid_points),
    )
    require_accounted_open_meteo_result(plan, result)
    return result


def require_accounted_open_meteo_result(
    plan: HistoricalOpenMeteoArchivePlan,
    result: OpenMeteoArchiveChunkResult,
) -> None:
    """Reject any chunk that cannot account for every requested cell, signal, and day.

    Accounting, not completeness; see execution/AGENTS.md §historical_open_meteo.
    """
    chunk = _plan_chunk(plan, result.chunk_key)
    day_count = plan.window.day_count
    if len(result.payload) < 1 or len(result.payload) > OPEN_METEO_ARCHIVE_MAX_RESPONSE_BYTES:
        raise ValueError("Open-Meteo archive result payload violates the reviewed byte boundary")
    if hashlib.sha256(result.payload).hexdigest() != result.payload_checksum:
        raise ValueError("Open-Meteo archive result payload checksum does not match its content")
    expected_series = {(cell.cell_key, parameter) for cell in chunk.cells for parameter in plan.parameters}
    coverage_series = [(item.cell_key, item.source_parameter) for item in result.coverage]
    if sorted(coverage_series) != sorted(expected_series) or len(coverage_series) != len(expected_series):
        raise ValueError("Open-Meteo archive coverage audit does not describe every reviewed cell and signal")
    if any(item.expected_observation_count != day_count for item in result.coverage):
        raise ValueError("Open-Meteo archive coverage audit does not span the reviewed window")
    expected_rows = sum(0 if item.status == "no_data" else day_count for item in result.coverage)
    if len(result.observations) != expected_rows:
        raise ValueError("Open-Meteo archive result dropped or duplicated normalized daily rows")
    observed_by_series: dict[tuple[str, str], int] = {}
    for observation in result.observations:
        key = (observation.cell_key, observation.source_parameter)
        observed_by_series[key] = observed_by_series.get(key, 0) + (1 if observation.is_observed else 0)
    for item in result.coverage:
        actual = observed_by_series.get((item.cell_key, item.source_parameter), 0)
        if actual != item.received_observation_count:
            raise ValueError("Open-Meteo archive coverage counts disagree with the normalized daily rows")
    if len(result.grid_points) != len(chunk.cells):
        raise ValueError("Open-Meteo archive result does not record a native grid point for every reviewed cell")


def _cell_observations(
    *,
    cell_key: str,
    parameter: str,
    specification: OpenMeteoArchiveSignal,
    dated_values: Sequence[tuple[date, float | None]],
    payload_checksum: str,
) -> Iterator[HistoricalSignalObservation]:
    """Emit one row per publisher-named day, preserving an absent measurement as absent."""
    for observed_date, value in dated_values:
        yield HistoricalSignalObservation(
            cell_key=cell_key,
            source_parameter=parameter,
            signal_name=specification.signal_name,
            observed_at=datetime.combine(observed_date, time.min, tzinfo=UTC),
            original_value=value,
            original_unit=specification.original_unit,
            normalized_value=value,
            normalized_unit=specification.normalized_unit,
            quality_flag="accepted" if value is not None else "source_missing",
            is_observed=value is not None,
            payload_checksum=payload_checksum,
        )


def _coverage_status(observed_count: int, expected_count: int) -> str:
    if observed_count == 0:
        return "no_data"
    return "complete" if observed_count == expected_count else "partial"


def _canonical_archive_document(wire_payload: bytes) -> bytes:
    """Strip only `generationtime_ms` so identical content yields an identical checksum.

    Two checksums, on purpose; see execution/AGENTS.md §historical_open_meteo.
    """
    try:
        parsed = json.loads(wire_payload)
    except ValueError as exc:
        raise ValueError("Open-Meteo archive response contained invalid JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("Open-Meteo archive multi-location response must be a JSON array")
    stripped: list[object] = []
    for location in parsed:
        if not isinstance(location, dict):
            raise ValueError("Open-Meteo archive response must contain only location objects")
        stripped.append({key: value for key, value in location.items() if key != "generationtime_ms"})
    return canonical_json_bytes(stripped)


def _archive_locations(payload: bytes, expected_count: int) -> list[dict[str, object]]:
    parsed = json.loads(payload)
    if not isinstance(parsed, list) or len(parsed) != expected_count:
        raise ValueError("Open-Meteo archive response does not carry one entry per requested cell")
    locations: list[dict[str, object]] = []
    for index, location in enumerate(parsed):
        if not isinstance(location, dict):
            raise ValueError("Open-Meteo archive response must contain only location objects")
        location_id = location.get("location_id")
        # The provider omits `location_id` on the first entry and numbers the rest from 1.
        if index and (isinstance(location_id, bool) or not isinstance(location_id, int) or location_id != index):
            raise ValueError("Open-Meteo archive response locations are not in the requested order")
        locations.append(location)
    return locations


def _validated_grid_point(cell: AnalysisGridCell, location: dict[str, object]) -> tuple[float, float, float | None]:
    """Bind one returned native grid point to its reviewed analysis cell or refuse the attribution."""
    latitude = _required_float(location, "latitude")
    longitude = _required_float(location, "longitude")
    if (
        abs(latitude - cell.latitude) > OPEN_METEO_ARCHIVE_MAX_GRID_OFFSET_DEGREES
        or abs(longitude - cell.longitude) > OPEN_METEO_ARCHIVE_MAX_GRID_OFFSET_DEGREES
    ):
        raise ValueError(f"Open-Meteo archive returned a grid point outside the reviewed cell {cell.cell_key}")
    if location.get("timezone") != "GMT" or location.get("utc_offset_seconds") != 0:
        raise ValueError("Open-Meteo archive response is not on the reviewed GMT time base")
    elevation = location.get("elevation")
    if elevation is not None and (isinstance(elevation, bool) or not isinstance(elevation, int | float)):
        raise ValueError("Open-Meteo archive elevation must be numeric when present")
    return latitude, longitude, None if elevation is None else float(elevation)


def _archive_daily_block(location: dict[str, object], parameters: Sequence[str]) -> dict[str, object]:
    daily = location.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("Open-Meteo archive location is missing its daily block")
    missing = sorted(set(parameters).difference(daily))
    if missing:
        raise ValueError(f"Open-Meteo archive location is missing requested variable(s): {', '.join(missing)}")
    return daily


def _archive_days(daily: dict[str, object], expected_dates: Sequence[date]) -> list[date]:
    """Bucket by the ISO date prefix the publisher named, never by recasting an instant."""
    raw = daily.get("time")
    if not isinstance(raw, list):
        raise ValueError("Open-Meteo archive daily block is missing its time axis")
    days: list[date] = []
    for value in raw:
        if not isinstance(value, str) or len(value) < ISO_DATE_LENGTH:
            raise ValueError("Open-Meteo archive daily time values must be ISO-8601 date strings")
        try:
            days.append(date.fromisoformat(value[:ISO_DATE_LENGTH]))
        except ValueError as exc:
            raise ValueError("Open-Meteo archive daily time values must be ISO-8601 date strings") from exc
    if days != list(expected_dates):
        raise ValueError("Open-Meteo archive daily time axis does not match the reviewed window")
    return days


def _archive_values(
    daily: dict[str, object],
    parameter: str,
    specification: OpenMeteoArchiveSignal,
    day_count: int,
) -> list[float | None]:
    """Read one variable's daily series; a value outside its physical range fails the whole chunk.

    A sentinel is a provider failure, not a gap: downgrading it to `no_data` would assert the
    provider modelled nothing here, which is a different and unevidenced claim. See
    execution/AGENTS.md §historical_open_meteo.
    """
    raw = daily.get(parameter)
    if not isinstance(raw, list) or len(raw) != day_count:
        raise ValueError(f"Open-Meteo archive variable {parameter} does not align with its daily time axis")
    values: list[float | None] = []
    for value in raw:
        if value is None:
            values.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError(f"Open-Meteo archive variable {parameter} carries a non-numeric value")
        numeric = float(value)
        if not specification.minimum <= numeric <= specification.maximum:
            raise ValueError(
                f"Open-Meteo archive variable {parameter} carries a value outside its reviewed physical "
                f"range [{specification.minimum}, {specification.maximum}]"
            )
        values.append(numeric)
    return values


def _required_float(location: dict[str, object], field_name: str) -> float:
    value = location.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"Open-Meteo archive location is missing a numeric {field_name}")
    return float(value)


def _nearest_native_grid_point(cell: AnalysisGridCell) -> tuple[int, int]:
    """Return the integer index of the 0.1-degree grid node a cell centroid resolves to."""
    step = OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES
    return round(cell.latitude / step), round(cell.longitude / step)


def _plan_chunk(plan: HistoricalOpenMeteoArchivePlan, chunk_key: str) -> OpenMeteoArchiveChunk:
    try:
        return next(chunk for chunk in plan.chunks if chunk.key == chunk_key)
    except StopIteration as exc:
        raise ValueError("Open-Meteo archive chunk is not part of the reviewed plan") from exc


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
