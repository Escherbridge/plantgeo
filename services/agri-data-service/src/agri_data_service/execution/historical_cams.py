"""Cache-first contracts for the Open-Meteo CAMS air-quality replay over an existing analysis lattice."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cached_property
from math import fsum
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from pydantic import Field, field_validator, model_validator

from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.historical_backfill import (
    AnalysisGridCell,
    HistoricalBackfillWindow,
    HistoricalCoverageAudit,
    HistoricalSignalObservation,
)
from agri_data_service.execution.open_meteo_lane import (
    DEFAULT_CHUNK_CONCURRENCY,
    ISO_DATE_LENGTH,
    OpenMeteoLane,
    OpenMeteoLaneFetchError,
    atomic_write,
    bounded_numeric_series,
    date_range,
    derived_checkpoint_state,
    fetch_lane_capture,
    lane_checkpoint_path,
    lane_raw_cache_paths,
    lane_release_manifest,
    max_grid_offset_degrees,
    merged_chunk_receipts,
    nearest_native_grid_point,
    ordered_locations,
    require_aware_utc,
    require_complete_raw_cache_pair,
    run_lane_chunks,
    validated_grid_point,
    verified_cached_payload,
    write_raw_cache_pair,
)
from agri_data_service.execution.source_ingestion import SourceDefinition  # noqa: TC001
from agri_data_service.ingest.open_meteo_air_quality import (
    HOURS_PER_DAY,
    OPEN_METEO_AIR_QUALITY_BASE_URL,
    OPEN_METEO_AIR_QUALITY_BOUNDS,
    OPEN_METEO_AIR_QUALITY_CELL_SELECTION,
    OPEN_METEO_AIR_QUALITY_ENDPOINT,
    OpenMeteoAirQualityBaseUrl,
    air_quality_hourly_request,
    air_quality_hourly_url,
    fetch_air_quality_hourly,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    import httpx

    from agri_data_service.ingest.http import UpstreamError

CAMS_SOURCE_KEY: Final = "open-meteo-cams-air-quality"

CAMS_MAX_RESPONSE_BYTES: Final = OPEN_METEO_AIR_QUALITY_BOUNDS.max_bytes
CAMS_MAX_CELLS: Final = 10_000
CAMS_MAX_CHUNKS: Final = 4_000
CAMS_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
CAMS_RAW_CACHE_SCHEMA_VERSION: Literal[1] = 1

# Retrieval, caching, checksums and checkpoint state are the shared scaffold's, not this lane's:
# `CAMS_LANE.label` is what prefixes every message they raise. See execution/AGENTS.md.
CAMS_LANE: Final = OpenMeteoLane(
    label="CAMS air-quality",
    cache_directory_name="historical-cams",
    endpoint=OPEN_METEO_AIR_QUALITY_ENDPOINT,
)

# Offsets into an ISO-8601 hourly stamp: `2026-08-06T13:00`. Only this lane reads an hour, because
# only this lane is served hourly and must prove a dense 24-hour axis before it reduces one.
ISO_HOUR_OFFSET: Final = 11
ISO_HOUR_TEXT_LENGTH: Final = 2
ISO_HOUR_STAMP_LENGTH: Final = 13

# A day summarized from fewer than this many hours is not a daily statistic, so it is written as an
# explicitly unobserved row carrying its own flag rather than as a value or as a `no_data` series.
CAMS_MINIMUM_OBSERVED_HOURS_PER_DAY: Final = 18

QUALITY_FLAG_ACCEPTED: Final = "accepted"
QUALITY_FLAG_SOURCE_MISSING: Final = "source_missing"
QUALITY_FLAG_INSUFFICIENT_HOURS: Final = "insufficient_hourly_coverage"

# The closed set of daily reductions a variable may declare; a concentration means, an index peaks.
CAMS_DAILY_STATISTICS: Final[dict[str, Callable[[Sequence[float]], float]]] = {
    "maximum": max,
    "mean": lambda values: fsum(values) / len(values),
}


class CamsDailyReduction(NamedTuple):
    """One day reduced from its hours: the statistic, why it is or is not a value, and its support."""

    value: float | None
    quality_flag: str
    # Hours actually observed out of 24. Carried onto the row as `coverage_fraction`, because a mean
    # of 18 hours and a mean of 24 are both `accepted` and must not look alike downstream.
    observed_hour_count: int


class CamsAirQualitySignal(NamedTuple):
    """One hourly variable's warehouse naming, units, daily reduction, and inclusive acceptance range."""

    signal_name: str
    original_unit: str
    normalized_unit: str
    daily_statistic: str
    minimum: float
    maximum: float


# Open-Meteo air-quality variable -> warehouse signal, units, daily reduction, and the range an
# HOURLY value must fall inside. The bounds are the only thing standing between a provider sentinel
# and a wall of `accepted` rows, and they are applied before the reduction, never after it.
CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS: Final[dict[str, CamsAirQualitySignal]] = {
    "aerosol_optical_depth": CamsAirQualitySignal(
        "aerosol_optical_depth", "dimensionless", "dimensionless", "mean", 0.0, 10.0
    ),
    "carbon_monoxide": CamsAirQualitySignal("carbon_monoxide", "ug/m^3", "ug/m^3", "mean", 0.0, 100_000.0),
    "dust": CamsAirQualitySignal("dust", "ug/m^3", "ug/m^3", "mean", 0.0, 100_000.0),
    "european_aqi": CamsAirQualitySignal("european_air_quality_index", "index", "index", "maximum", 0.0, 1_000.0),
    "nitrogen_dioxide": CamsAirQualitySignal("nitrogen_dioxide", "ug/m^3", "ug/m^3", "mean", 0.0, 10_000.0),
    "ozone": CamsAirQualitySignal("ozone", "ug/m^3", "ug/m^3", "mean", 0.0, 10_000.0),
    "pm10": CamsAirQualitySignal("particulate_matter_10", "ug/m^3", "ug/m^3", "mean", 0.0, 10_000.0),
    "pm2_5": CamsAirQualitySignal("particulate_matter_2_5", "ug/m^3", "ug/m^3", "mean", 0.0, 10_000.0),
    "sulphur_dioxide": CamsAirQualitySignal("sulphur_dioxide", "ug/m^3", "ug/m^3", "mean", 0.0, 10_000.0),
    "us_aqi": CamsAirQualitySignal("united_states_air_quality_index", "index", "index", "maximum", 0.0, 1_000.0),
    "uv_index": CamsAirQualitySignal("ultraviolet_index", "index", "index", "maximum", 0.0, 20.0),
}

# The regional index and the two aerosol fields are domain-specific; asking the wrong domain for one
# is a plan error, not a gap, so each domain names exactly what it serves.
CAMS_GLOBAL_PARAMETERS: Final = tuple(
    sorted(set(CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS).difference({"european_aqi"}))
)
CAMS_EUROPE_PARAMETERS: Final = tuple(
    sorted(set(CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS).difference({"aerosol_optical_depth", "dust"}))
)


class CamsProduct(NamedTuple):
    """One reviewed CAMS domain and everything the choice of domain itself decides about a plan."""

    domain: str
    schema_version: str
    native_grid_name: str
    native_grid_degrees: float
    native_grid_resolution_m: int
    support_key: str
    supported_parameters: tuple[str, ...]


# The domain is not an independent axis: it drags the native lattice, the support key and the
# document schema with it, so a plan names a product and inherits the rest rather than restating it.
CAMS_PRODUCTS: Final[dict[str, CamsProduct]] = {
    "cams_europe": CamsProduct(
        "cams_europe",
        "open-meteo-cams-europe-air-quality-hourly-v1",
        "cams-europe-0.1-degree",
        0.1,
        11_000,
        "cams-europe-0.1deg",
        CAMS_EUROPE_PARAMETERS,
    ),
    "cams_global": CamsProduct(
        "cams_global",
        "open-meteo-cams-global-air-quality-hourly-v1",
        "cams-global-0.4-degree",
        0.4,
        44_000,
        "cams-global-0.4deg",
        CAMS_GLOBAL_PARAMETERS,
    ),
}


@dataclass(frozen=True)
class CamsAirQualityChunk:
    """One bounded request over one cell block and one day block of the reviewed window."""

    key: str
    cells: tuple[AnalysisGridCell, ...]
    start_date: date
    end_date: date

    @property
    def day_count(self) -> int:
        """Return the inclusive number of days this chunk requests per cell and variable."""
        return (self.end_date - self.start_date).days + 1


@dataclass(frozen=True)
class CamsAirQualityCapture:
    """What is known about one retrieval before its content is normalized."""

    retrieved_at: datetime
    wire_payload_bytes: int
    wire_payload_checksum: str
    # The host this retrieval really answered from; the paid tier is a different host plus one key.
    request_base_url: OpenMeteoAirQualityBaseUrl = OPEN_METEO_AIR_QUALITY_BASE_URL


class HistoricalCamsAirQualityPlan(ContractModel):
    """Reviewed four-year Open-Meteo CAMS air-quality replay over an existing analysis lattice."""

    schema_version: str = Field(pattern=r"^open-meteo-cams-[a-z]{1,20}-air-quality-hourly-v[0-9]{1,3}$")
    source: SourceDefinition
    window: HistoricalBackfillWindow
    domain: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    cell_selection: Literal["nearest"] = OPEN_METEO_AIR_QUALITY_CELL_SELECTION
    time_zone: Literal["GMT"] = "GMT"
    grid_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,98}$")
    grid_resolution_m: int = Field(gt=0)
    native_grid_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,98}$")
    native_grid_degrees: float = Field(gt=0, le=1)
    native_grid_resolution_m: int = Field(gt=0)
    support_key: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,62}$")
    cells: list[AnalysisGridCell] = Field(min_length=1, max_length=CAMS_MAX_CELLS)
    chunk_cell_count: int = Field(ge=1, le=200)
    # CAMS answers hourly, so a chunk is bounded on both axes: 24 values per variable per cell per
    # day means the day block, not the cell block alone, is what keeps a response under its ceiling.
    chunk_day_count: int = Field(ge=1, le=92)
    parameters: list[str] = Field(min_length=1, max_length=len(CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS))
    transform_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("cells")
    @classmethod
    def require_sorted_unique_cells(cls, value: list[AnalysisGridCell]) -> list[AnalysisGridCell]:
        keys = [cell.cell_key for cell in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("CAMS air-quality cells must be sorted and unique by cell_key")
        if len({(cell.latitude, cell.longitude) for cell in value}) != len(value):
            raise ValueError("CAMS air-quality cells must not repeat a requested coordinate")
        return value

    @field_validator("parameters")
    @classmethod
    def require_supported_sorted_parameters(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("CAMS air-quality parameters must be sorted and unique")
        unsupported = sorted(set(value).difference(CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS))
        if unsupported:
            raise ValueError(f"unsupported CAMS air-quality parameter(s): {', '.join(unsupported)}")
        return value

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_release_set_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "release_set_as_of")

    @model_validator(mode="after")
    def require_governed_lattice(self) -> HistoricalCamsAirQualityPlan:
        if self.source.key != CAMS_SOURCE_KEY:
            raise ValueError(f"CAMS air-quality plans require source.key='{CAMS_SOURCE_KEY}'")
        product = CAMS_PRODUCTS.get(self.domain)
        if product is None:
            raise ValueError(f"CAMS air-quality domain must be one of: {', '.join(sorted(CAMS_PRODUCTS))}")
        inherited = {
            "schema_version": product.schema_version,
            "native_grid_name": product.native_grid_name,
            "native_grid_degrees": product.native_grid_degrees,
            "native_grid_resolution_m": product.native_grid_resolution_m,
            "support_key": product.support_key,
        }
        disagreeing = sorted(field for field, value in inherited.items() if getattr(self, field) != value)
        if disagreeing:
            raise ValueError(
                f"CAMS air-quality plan restates its product incorrectly for domain '{self.domain}': "
                f"{', '.join(disagreeing)}"
            )
        unserved = sorted(set(self.parameters).difference(product.supported_parameters))
        if unserved:
            raise ValueError(f"CAMS domain '{self.domain}' does not serve parameter(s): {', '.join(unserved)}")
        nearest_points = {nearest_native_grid_point(cell, product.native_grid_degrees) for cell in self.cells}
        if len(nearest_points) != len(self.cells):
            raise ValueError("CAMS air-quality cells must not share a native grid point")
        if len(self.chunks) > CAMS_MAX_CHUNKS:
            raise ValueError("CAMS air-quality plan exceeds the reviewed chunk ceiling")
        return self

    @cached_property
    def product(self) -> CamsProduct:
        """Return the reviewed product bundle this plan's domain selects."""
        return CAMS_PRODUCTS[self.domain]

    @cached_property
    def day_blocks(self) -> tuple[tuple[date, date], ...]:
        """Cut the reviewed window into consecutive inclusive day blocks anchored at its first day."""
        blocks: list[tuple[date, date]] = []
        current = self.window.start_date
        while current <= self.window.end_date:
            last = min(current + timedelta(days=self.chunk_day_count - 1), self.window.end_date)
            blocks.append((current, last))
            current = last + timedelta(days=1)
        return tuple(blocks)

    @cached_property
    def chunks(self) -> tuple[CamsAirQualityChunk, ...]:
        """Cut the sorted cell list and the reviewed window into stable request chunks."""
        size = self.chunk_cell_count
        return tuple(
            CamsAirQualityChunk(
                key=f"cells-{index // size:04d}-days-{block_index:04d}",
                cells=tuple(self.cells[index : index + size]),
                start_date=start_date,
                end_date=end_date,
            )
            for index in range(0, len(self.cells), size)
            for block_index, (start_date, end_date) in enumerate(self.day_blocks)
        )

    @cached_property
    def plan_checksum(self) -> str:
        """Fingerprint every governed input controlling this replay, once per validated instance."""
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


class HistoricalCamsReceipt(ContractModel):
    """One fully validated, cache-backed air-quality chunk receipt."""

    chunk_key: str = Field(pattern=r"^cells-\d{4}-days-\d{4}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=CAMS_MAX_RESPONSE_BYTES)
    cell_count: int = Field(ge=1)
    day_count: int = Field(ge=1)
    observation_count: int = Field(ge=0)
    observed_value_count: int = Field(ge=0)
    insufficient_hour_day_count: int = Field(ge=0)
    coverage_count: int = Field(ge=1)
    no_data_series_count: int = Field(ge=0)
    failed_series_count: int = Field(ge=0)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "retrieved_at")


class HistoricalCamsRawCacheReceipt(ContractModel):
    """Checksum-bound metadata for one reusable air-quality chunk download."""

    schema_version: Literal[1] = CAMS_RAW_CACHE_SCHEMA_VERSION
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_key: str = Field(pattern=r"^cells-\d{4}-days-\d{4}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=CAMS_MAX_RESPONSE_BYTES)
    wire_payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    wire_payload_bytes: int = Field(ge=1, le=CAMS_MAX_RESPONSE_BYTES)
    retrieved_at: datetime
    request_base_url: OpenMeteoAirQualityBaseUrl = OPEN_METEO_AIR_QUALITY_BASE_URL

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "retrieved_at")


class HistoricalCamsCheckpoint(ContractModel):
    """Durable resumable state for the complete chunked air-quality plan."""

    schema_version: Literal[1] = CAMS_CHECKPOINT_SCHEMA_VERSION
    state: Literal["initialized", "running", "validated", "blocked"]
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipts: list[HistoricalCamsReceipt] = Field(default_factory=list, max_length=CAMS_MAX_CHUNKS)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("updated_at")
    @classmethod
    def require_aware_update_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "updated_at")

    @field_validator("receipts")
    @classmethod
    def require_sorted_unique_receipts(cls, value: list[HistoricalCamsReceipt]) -> list[HistoricalCamsReceipt]:
        keys = [receipt.chunk_key for receipt in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("CAMS air-quality receipts must be sorted and unique by chunk_key")
        return value


@dataclass(frozen=True)
class CamsAirQualityChunkResult:
    """One validated canonical air-quality document plus its normalized daily facts and coverage evidence."""

    chunk_key: str
    retrieved_at: datetime
    payload: bytes
    payload_checksum: str
    wire_payload_bytes: int
    wire_payload_checksum: str
    request_base_url: OpenMeteoAirQualityBaseUrl
    observations: tuple[HistoricalSignalObservation, ...]
    coverage: tuple[HistoricalCoverageAudit, ...]
    grid_points: tuple[tuple[str, float, float], ...]

    @property
    def insufficient_hour_day_count(self) -> int:
        """Count the days summarized from too few hours to be a daily statistic."""
        return sum(1 for item in self.observations if item.quality_flag == QUALITY_FLAG_INSUFFICIENT_HOURS)


def cams_air_quality_chunk_url(
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
    *,
    base_url: str | None = None,
) -> str:
    """Return the credential-free request one chunk is answered by, so a release records a reproducible query."""
    _plan_chunk(plan, chunk.key)
    return air_quality_hourly_url(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        chunk.start_date,
        chunk.end_date,
        plan.domain,
        base_url=base_url,
    )


def historical_cams_plan_checksum(plan: HistoricalCamsAirQualityPlan) -> str:
    """Fingerprint every governed input controlling this replay; memoized on the validated plan."""
    return plan.plan_checksum


def historical_cams_checkpoint_path(root: Path, plan: HistoricalCamsAirQualityPlan) -> Path:
    """Return a plan-bound durable checkpoint file path."""
    return lane_checkpoint_path(root, CAMS_LANE, historical_cams_plan_checksum(plan))


def historical_cams_raw_cache_paths(
    root: Path,
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
) -> tuple[Path, Path]:
    """Return canonical-document and receipt locations for one chunk beneath the local run root."""
    _plan_chunk(plan, chunk.key)
    return lane_raw_cache_paths(root, CAMS_LANE, historical_cams_plan_checksum(plan), chunk.key)


def initialize_historical_cams_checkpoint(
    plan: HistoricalCamsAirQualityPlan,
    *,
    updated_at: datetime | None = None,
) -> HistoricalCamsCheckpoint:
    """Create an empty checkpoint without opening the network or PostgreSQL."""
    return HistoricalCamsCheckpoint(
        state="initialized",
        plan_checksum=historical_cams_plan_checksum(plan),
        updated_at=require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def load_historical_cams_checkpoint(path: Path) -> HistoricalCamsCheckpoint:
    """Read a local air-quality checkpoint without requesting a provider payload."""
    return HistoricalCamsCheckpoint.model_validate_json(path.read_bytes())


def write_historical_cams_checkpoint(path: Path, checkpoint: HistoricalCamsCheckpoint) -> None:
    """Atomically update credential-free air-quality checkpoint metadata."""
    atomic_write(path, canonical_json_bytes(checkpoint.model_dump(mode="json")))


def rederive_historical_cams_checkpoint_state(
    plan: HistoricalCamsAirQualityPlan,
    checkpoint: HistoricalCamsCheckpoint,
) -> HistoricalCamsCheckpoint:
    """Recompute `state` from receipt completeness so a recorded `blocked` cannot outlive its cause.

    `reason` is preserved: it is the evidence of the last stop, and only `state` gates a resume.
    """
    if checkpoint.plan_checksum != historical_cams_plan_checksum(plan):
        raise ValueError("CAMS air-quality checkpoint does not bind the reviewed plan")
    derived = derived_checkpoint_state(
        {receipt.chunk_key for receipt in checkpoint.receipts},
        [chunk.key for chunk in plan.chunks],
    )
    if derived == checkpoint.state:
        return checkpoint
    return checkpoint.model_copy(update={"state": derived})


def record_historical_cams_result(
    plan: HistoricalCamsAirQualityPlan,
    checkpoint: HistoricalCamsCheckpoint,
    result: CamsAirQualityChunkResult,
    *,
    updated_at: datetime | None = None,
) -> HistoricalCamsCheckpoint:
    """Advance a chunk receipt only after every requested cell, signal and day is accounted for."""
    if checkpoint.plan_checksum != historical_cams_plan_checksum(plan):
        raise ValueError("CAMS air-quality checkpoint does not bind the reviewed plan")
    chunk = _plan_chunk(plan, result.chunk_key)
    require_accounted_cams_result(plan, result)
    receipt = HistoricalCamsReceipt(
        chunk_key=result.chunk_key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        cell_count=len(chunk.cells),
        day_count=chunk.day_count,
        observation_count=len(result.observations),
        observed_value_count=sum(1 for item in result.observations if item.is_observed),
        insufficient_hour_day_count=result.insufficient_hour_day_count,
        coverage_count=len(result.coverage),
        no_data_series_count=sum(1 for item in result.coverage if item.status == "no_data"),
        failed_series_count=sum(1 for item in result.coverage if item.status == "failed"),
        retrieved_at=result.retrieved_at,
    )
    receipts = merged_chunk_receipts(CAMS_LANE, checkpoint.receipts, receipt)
    complete = [item.key for item in plan.chunks] == [item.chunk_key for item in receipts]
    return checkpoint.model_copy(
        update={
            "state": "validated" if complete else "running",
            "receipts": receipts,
            "updated_at": require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
            "reason": None,
        }
    )


def historical_cams_release_manifest(
    plan: HistoricalCamsAirQualityPlan,
    checkpoint: HistoricalCamsCheckpoint,
) -> str:
    """Hash the complete ordered chunk receipt set a release must pin."""
    return lane_release_manifest(
        CAMS_LANE,
        plan_checksum=historical_cams_plan_checksum(plan),
        transform_version=plan.transform_version,
        checkpoint_plan_checksum=checkpoint.plan_checksum,
        checkpoint_state=checkpoint.state,
        expected_chunk_keys=[chunk.key for chunk in plan.chunks],
        receipts=checkpoint.receipts,
    )


def cache_historical_cams_result(
    root: Path,
    plan: HistoricalCamsAirQualityPlan,
    result: CamsAirQualityChunkResult,
) -> HistoricalCamsRawCacheReceipt:
    """Persist one accounted-for canonical chunk document before the warehouse transaction begins."""
    chunk = _plan_chunk(plan, result.chunk_key)
    require_accounted_cams_result(plan, result)
    payload_path, receipt_path = historical_cams_raw_cache_paths(root, plan, chunk)
    receipt = HistoricalCamsRawCacheReceipt(
        plan_checksum=historical_cams_plan_checksum(plan),
        chunk_key=chunk.key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        wire_payload_checksum=result.wire_payload_checksum,
        wire_payload_bytes=result.wire_payload_bytes,
        retrieved_at=result.retrieved_at,
        request_base_url=OPEN_METEO_AIR_QUALITY_ENDPOINT.require_base_url(result.request_base_url),
    )
    if payload_path.exists() or receipt_path.exists():
        cached = load_cached_historical_cams_result(root, plan, chunk)
        if cached is None:
            raise ValueError("CAMS air-quality raw cache unexpectedly has no reusable source document")
        if cached.payload_checksum != result.payload_checksum:
            raise ValueError("CAMS air-quality raw cache already binds this chunk to different source content")
        return HistoricalCamsRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    write_raw_cache_pair(
        payload_path,
        receipt_path,
        result.payload,
        canonical_json_bytes(receipt.model_dump(mode="json")),
    )
    return receipt


def load_cached_historical_cams_result(
    root: Path,
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
) -> CamsAirQualityChunkResult | None:
    """Re-parse one validated local chunk document, never contacting the provider."""
    payload_path, receipt_path = historical_cams_raw_cache_paths(root, plan, chunk)
    if not require_complete_raw_cache_pair(CAMS_LANE, payload_path, receipt_path):
        return None
    receipt = HistoricalCamsRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    if receipt.plan_checksum != historical_cams_plan_checksum(plan) or receipt.chunk_key != chunk.key:
        raise ValueError("CAMS air-quality raw cache receipt does not bind this reviewed plan and chunk")
    payload = verified_cached_payload(
        CAMS_LANE,
        payload_path,
        expected_bytes=receipt.payload_bytes,
        expected_checksum=receipt.payload_checksum,
    )
    result = parse_cams_air_quality_payload(
        plan,
        chunk,
        payload,
        CamsAirQualityCapture(
            retrieved_at=receipt.retrieved_at,
            wire_payload_bytes=receipt.wire_payload_bytes,
            wire_payload_checksum=receipt.wire_payload_checksum,
            request_base_url=receipt.request_base_url,
        ),
    )
    require_accounted_cams_result(plan, result)
    return result


class CamsAirQualityFetchError(OpenMeteoLaneFetchError):
    """Raised when an air-quality chunk stopped for good; carries the chunk so a resume is unambiguous."""

    def __init__(self, chunk_key: str, cause: UpstreamError | None, attempts: int) -> None:
        """Name the chunk, how many attempts it really made, and the provider condition that stopped it."""
        super().__init__(CAMS_LANE.label, chunk_key, cause, attempts)


async def fetch_cams_air_quality_chunk(
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
    *,
    client: httpx.AsyncClient,
    retrieved_at: datetime | None = None,
    sleep: object = None,
) -> CamsAirQualityChunkResult:
    """Fetch one chunk with bounded retries; an exhausted quota is raised, never swallowed."""
    _plan_chunk(plan, chunk.key)
    # The credential lives only in `request.request_url`; only `request.base_url` is ever recorded.
    request = air_quality_hourly_request(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        chunk.start_date,
        chunk.end_date,
        plan.domain,
    )
    capture = await fetch_lane_capture(
        CAMS_LANE,
        chunk.key,
        request,
        client=client,
        fetch_text=fetch_air_quality_hourly,
        error_factory=CamsAirQualityFetchError,
        retrieved_at=retrieved_at,
        sleep=sleep,
    )
    return parse_cams_air_quality_payload(
        plan,
        chunk,
        capture.canonical_payload,
        CamsAirQualityCapture(
            retrieved_at=capture.retrieved_at,
            wire_payload_bytes=capture.wire_payload_bytes,
            wire_payload_checksum=capture.wire_payload_checksum,
            request_base_url=OPEN_METEO_AIR_QUALITY_ENDPOINT.require_base_url(capture.request_base_url),
        ),
    )


async def run_cams_air_quality_chunks(
    plan: HistoricalCamsAirQualityPlan,
    chunks: Sequence[CamsAirQualityChunk],
    *,
    concurrency: int = DEFAULT_CHUNK_CONCURRENCY,
    client: httpx.AsyncClient | None = None,
) -> list[CamsAirQualityChunkResult | BaseException]:
    """Fetch several chunks under a bounded semaphore, preserving each chunk's own failure."""

    async def one(active: httpx.AsyncClient, chunk: CamsAirQualityChunk) -> CamsAirQualityChunkResult:
        return await fetch_cams_air_quality_chunk(plan, chunk, client=active)

    return await run_lane_chunks(CAMS_LANE, chunks, one, concurrency=concurrency, client=client)


def parse_cams_air_quality_payload(
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
    payload: bytes,
    capture: CamsAirQualityCapture,
) -> CamsAirQualityChunkResult:
    """Validate one canonical air-quality document and reduce every reviewed cell/signal/day."""
    _plan_chunk(plan, chunk.key)
    timestamp = require_aware_utc(capture.retrieved_at, "retrieved_at")
    if not payload or len(payload) > CAMS_MAX_RESPONSE_BYTES:
        raise ValueError("CAMS air-quality response exceeds the reviewed byte boundary")
    locations = ordered_locations(CAMS_LANE, payload, len(chunk.cells))
    expected_dates = list(date_range(chunk.start_date, chunk.end_date))
    payload_checksum = hashlib.sha256(payload).hexdigest()
    max_offset = max_grid_offset_degrees(plan.product.native_grid_degrees)

    observations: list[HistoricalSignalObservation] = []
    coverage: list[HistoricalCoverageAudit] = []
    grid_points: list[tuple[str, float, float]] = []
    seen_grid_points: set[tuple[float, float]] = set()
    for cell, location in zip(chunk.cells, locations, strict=True):
        latitude, longitude = validated_grid_point(CAMS_LANE, cell, location, max_offset)
        if (latitude, longitude) in seen_grid_points:
            raise ValueError("CAMS air-quality returned one native grid point for two reviewed cells")
        seen_grid_points.add((latitude, longitude))
        grid_points.append((cell.cell_key, latitude, longitude))
        hourly = _air_quality_hourly_block(location, plan.parameters)
        _require_hourly_axis(hourly, expected_dates)
        for parameter in plan.parameters:
            specification = CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS[parameter]
            hourly_values = _air_quality_values(
                hourly, parameter, specification, len(expected_dates) * HOURS_PER_DAY
            )
            reduced = _reduce_to_daily(hourly_values, len(expected_dates), specification)
            observed_count = sum(1 for item in reduced if item.quality_flag == QUALITY_FLAG_ACCEPTED)
            hourly_observed_count = sum(1 for value in hourly_values if value is not None)
            status = _coverage_status(observed_count, len(expected_dates), hourly_observed_count)
            if status != "no_data":
                observations.extend(
                    _cell_observations(
                        cell_key=cell.cell_key,
                        parameter=parameter,
                        specification=specification,
                        dated_values=list(zip(expected_dates, reduced, strict=True)),
                        payload_checksum=payload_checksum,
                    )
                )
            coverage.append(
                HistoricalCoverageAudit(
                    cell_key=cell.cell_key,
                    source_parameter=parameter,
                    signal_name=specification.signal_name,
                    window_start=datetime.combine(chunk.start_date, time.min, tzinfo=UTC),
                    window_end=datetime.combine(chunk.end_date, time.max, tzinfo=UTC),
                    expected_observation_count=len(expected_dates),
                    received_observation_count=observed_count,
                    status=status,
                )
            )
    result = CamsAirQualityChunkResult(
        chunk_key=chunk.key,
        retrieved_at=timestamp,
        payload=payload,
        payload_checksum=payload_checksum,
        wire_payload_bytes=capture.wire_payload_bytes,
        wire_payload_checksum=capture.wire_payload_checksum,
        request_base_url=OPEN_METEO_AIR_QUALITY_ENDPOINT.require_base_url(capture.request_base_url),
        observations=tuple(observations),
        coverage=tuple(coverage),
        grid_points=tuple(grid_points),
    )
    require_accounted_cams_result(plan, result)
    return result


def require_accounted_cams_result(
    plan: HistoricalCamsAirQualityPlan,
    result: CamsAirQualityChunkResult,
) -> None:
    """Reject any chunk that cannot account for every requested cell, signal, and day.

    Accounting, not completeness: a series the provider modelled nowhere is allowed, but it must
    arrive as one `no_data` coverage row rather than as silently absent daily rows.
    """
    chunk = _plan_chunk(plan, result.chunk_key)
    day_count = chunk.day_count
    if len(result.payload) < 1 or len(result.payload) > CAMS_MAX_RESPONSE_BYTES:
        raise ValueError("CAMS air-quality result payload violates the reviewed byte boundary")
    if hashlib.sha256(result.payload).hexdigest() != result.payload_checksum:
        raise ValueError("CAMS air-quality result payload checksum does not match its content")
    expected_series = {(cell.cell_key, parameter) for cell in chunk.cells for parameter in plan.parameters}
    coverage_series = [(item.cell_key, item.source_parameter) for item in result.coverage]
    if sorted(coverage_series) != sorted(expected_series) or len(coverage_series) != len(expected_series):
        raise ValueError("CAMS air-quality coverage audit does not describe every reviewed cell and signal")
    if any(item.expected_observation_count != day_count for item in result.coverage):
        raise ValueError("CAMS air-quality coverage audit does not span the reviewed chunk window")
    expected_rows = sum(0 if item.status == "no_data" else day_count for item in result.coverage)
    if len(result.observations) != expected_rows:
        raise ValueError("CAMS air-quality result dropped or duplicated normalized daily rows")
    observed_by_series: dict[tuple[str, str], int] = {}
    for observation in result.observations:
        key = (observation.cell_key, observation.source_parameter)
        observed_by_series[key] = observed_by_series.get(key, 0) + (1 if observation.is_observed else 0)
    for item in result.coverage:
        actual = observed_by_series.get((item.cell_key, item.source_parameter), 0)
        if actual != item.received_observation_count:
            raise ValueError("CAMS air-quality coverage counts disagree with the normalized daily rows")
    if len(result.grid_points) != len(chunk.cells):
        raise ValueError("CAMS air-quality result does not record a native grid point for every reviewed cell")


def _reduce_to_daily(
    hourly_values: Sequence[float | None],
    day_count: int,
    specification: CamsAirQualitySignal,
) -> list[CamsDailyReduction]:
    """Reduce 24 bounded hourly values to one daily statistic per day, and say why a day has none."""
    reduce = CAMS_DAILY_STATISTICS[specification.daily_statistic]
    reduced: list[CamsDailyReduction] = []
    for index in range(day_count):
        block = hourly_values[index * HOURS_PER_DAY : (index + 1) * HOURS_PER_DAY]
        observed = [value for value in block if value is not None]
        if len(observed) >= CAMS_MINIMUM_OBSERVED_HOURS_PER_DAY:
            reduced.append(CamsDailyReduction(reduce(observed), QUALITY_FLAG_ACCEPTED, len(observed)))
        elif observed:
            reduced.append(CamsDailyReduction(None, QUALITY_FLAG_INSUFFICIENT_HOURS, len(observed)))
        else:
            reduced.append(CamsDailyReduction(None, QUALITY_FLAG_SOURCE_MISSING, 0))
    return reduced


def _cell_observations(
    *,
    cell_key: str,
    parameter: str,
    specification: CamsAirQualitySignal,
    dated_values: Sequence[tuple[date, CamsDailyReduction]],
    payload_checksum: str,
) -> Iterator[HistoricalSignalObservation]:
    """Emit one row per publisher-named day, preserving an absent or under-sampled day as unobserved.

    `coverage_fraction` is the per-row trace of how much of the day the statistic really saw. Without
    it an 18-hour mean and a 24-hour mean are both plain `accepted` rows, and only the chunk receipt
    remembers the difference -- which is not where a reader of one row would ever look.
    """
    for observed_date, reduction in dated_values:
        yield HistoricalSignalObservation(
            cell_key=cell_key,
            source_parameter=parameter,
            signal_name=specification.signal_name,
            observed_at=datetime.combine(observed_date, time.min, tzinfo=UTC),
            original_value=reduction.value,
            original_unit=specification.original_unit,
            normalized_value=reduction.value,
            normalized_unit=specification.normalized_unit,
            quality_flag=reduction.quality_flag,
            is_observed=reduction.quality_flag == QUALITY_FLAG_ACCEPTED,
            payload_checksum=payload_checksum,
            coverage_fraction=reduction.observed_hour_count / HOURS_PER_DAY,
        )


def _coverage_status(observed_count: int, expected_count: int, hourly_observed_count: int) -> str:
    """Classify one series, keeping "the provider published nothing" distinct from "no day had enough hours".

    Only an empty hourly series is `no_data`; a series the provider did publish but which never
    reached the daily-hour floor is `failed`, so its per-day evidence is still written.
    """
    if hourly_observed_count == 0:
        return "no_data"
    if observed_count == expected_count:
        return "complete"
    return "partial" if observed_count else "failed"


def _air_quality_hourly_block(location: dict[str, object], parameters: Sequence[str]) -> dict[str, object]:
    hourly = location.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("CAMS air-quality location is missing its hourly block")
    missing = sorted(set(parameters).difference(hourly))
    if missing:
        raise ValueError(f"CAMS air-quality location is missing requested variable(s): {', '.join(missing)}")
    return hourly


def _require_hourly_axis(hourly: dict[str, object], expected_dates: Sequence[date]) -> None:
    """Require a dense 24-hour axis per reviewed day, bucketed by the ISO prefix the publisher named."""
    raw = hourly.get("time")
    if not isinstance(raw, list) or len(raw) != len(expected_dates) * HOURS_PER_DAY:
        raise ValueError("CAMS air-quality hourly time axis does not match the reviewed chunk window")
    index = 0
    for expected_date in expected_dates:
        for hour in range(HOURS_PER_DAY):
            value = raw[index]
            index += 1
            if not isinstance(value, str) or len(value) < ISO_HOUR_STAMP_LENGTH:
                raise ValueError("CAMS air-quality hourly time values must be ISO-8601 timestamps")
            try:
                stamped = date.fromisoformat(value[:ISO_DATE_LENGTH])
            except ValueError as exc:
                raise ValueError("CAMS air-quality hourly time values must be ISO-8601 timestamps") from exc
            hour_text = value[ISO_HOUR_OFFSET : ISO_HOUR_OFFSET + ISO_HOUR_TEXT_LENGTH]
            if stamped != expected_date or not hour_text.isdigit() or int(hour_text) != hour:
                raise ValueError("CAMS air-quality hourly time axis does not match the reviewed chunk window")


def _air_quality_values(
    hourly: dict[str, object],
    parameter: str,
    specification: CamsAirQualitySignal,
    hour_count: int,
) -> list[float | None]:
    """Read one variable's hourly series through the shared bounded reader, before any reduction."""
    return bounded_numeric_series(
        CAMS_LANE,
        hourly,
        parameter,
        minimum=specification.minimum,
        maximum=specification.maximum,
        expected_count=hour_count,
        subject="variable",
    )


def _plan_chunk(plan: HistoricalCamsAirQualityPlan, chunk_key: str) -> CamsAirQualityChunk:
    try:
        return next(chunk for chunk in plan.chunks if chunk.key == chunk_key)
    except StopIteration as exc:
        raise ValueError("CAMS air-quality chunk is not part of the reviewed plan") from exc
