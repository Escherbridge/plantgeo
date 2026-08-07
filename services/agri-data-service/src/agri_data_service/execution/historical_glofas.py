"""Cache-first contracts for the Open-Meteo GloFAS river-discharge replay over an existing analysis lattice."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
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
from agri_data_service.ingest.open_meteo_flood import (
    OPEN_METEO_FLOOD_BASE_URL,
    OPEN_METEO_FLOOD_BOUNDS,
    OPEN_METEO_FLOOD_CELL_SELECTION,
    OPEN_METEO_FLOOD_ENDPOINT,
    OpenMeteoFloodBaseUrl,
    fetch_flood_daily,
    flood_daily_request,
    flood_daily_url,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    import httpx

    from agri_data_service.ingest.http import UpstreamError

GLOFAS_SOURCE_KEY: Final = "open-meteo-glofas-flood"

GLOFAS_MAX_RESPONSE_BYTES: Final = OPEN_METEO_FLOOD_BOUNDS.max_bytes
GLOFAS_MAX_CELLS: Final = 10_000
GLOFAS_MAX_CHUNKS: Final = 2_000
GLOFAS_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
GLOFAS_RAW_CACHE_SCHEMA_VERSION: Literal[1] = 1

# Retrieval, caching, checksums and checkpoint state are the shared scaffold's, not this lane's:
# `GLOFAS_LANE.label` is what prefixes every message they raise. See execution/AGENTS.md.
GLOFAS_LANE: Final = OpenMeteoLane(
    label="GloFAS flood",
    cache_directory_name="historical-glofas",
    endpoint=OPEN_METEO_FLOOD_ENDPOINT,
)


class GlofasFloodSignal(NamedTuple):
    """One daily variable's warehouse naming, units, and inclusive physical acceptance range."""

    signal_name: str
    original_unit: str
    normalized_unit: str
    minimum: float
    maximum: float


# Open-Meteo flood variable -> warehouse signal, units, and the range a value must fall inside. The
# bounds are the only thing standing between a provider sentinel and a wall of `accepted` rows: the
# Amazon peaks near 300,000 m^3/s, so the ceiling is generous, finite, and still rejects a fill value.
GLOFAS_FLOOD_SIGNAL_SPECIFICATIONS: Final[dict[str, GlofasFloodSignal]] = {
    "river_discharge": GlofasFloodSignal("river_discharge", "m^3/s", "m^3/s", 0.0, 1_000_000.0),
    "river_discharge_max": GlofasFloodSignal(
        "river_discharge_ensemble_maximum", "m^3/s", "m^3/s", 0.0, 1_000_000.0
    ),
    "river_discharge_mean": GlofasFloodSignal("river_discharge_ensemble_mean", "m^3/s", "m^3/s", 0.0, 1_000_000.0),
    "river_discharge_median": GlofasFloodSignal(
        "river_discharge_ensemble_median", "m^3/s", "m^3/s", 0.0, 1_000_000.0
    ),
    "river_discharge_min": GlofasFloodSignal(
        "river_discharge_ensemble_minimum", "m^3/s", "m^3/s", 0.0, 1_000_000.0
    ),
    "river_discharge_p25": GlofasFloodSignal(
        "river_discharge_ensemble_percentile_25", "m^3/s", "m^3/s", 0.0, 1_000_000.0
    ),
    "river_discharge_p75": GlofasFloodSignal(
        "river_discharge_ensemble_percentile_75", "m^3/s", "m^3/s", 0.0, 1_000_000.0
    ),
}

# The reanalysis models publish a single deterministic reach value; the ensemble statistics exist
# only for the forecast products, so asking a consolidated plan for them is a plan error, not a gap.
GLOFAS_REANALYSIS_PARAMETERS: Final = ("river_discharge",)
GLOFAS_FORECAST_PARAMETERS: Final = tuple(sorted(GLOFAS_FLOOD_SIGNAL_SPECIFICATIONS))


class GlofasProduct(NamedTuple):
    """One reviewed GloFAS model and everything the choice of model itself decides about a plan."""

    model: str
    schema_version: str
    native_grid_name: str
    native_grid_degrees: float
    native_grid_resolution_m: int
    support_key: str
    supported_parameters: tuple[str, ...]


# The model is not an independent axis: it drags the native lattice, the support key and the document
# schema with it, so a plan names a product and inherits the rest rather than restating it.
GLOFAS_PRODUCTS: Final[dict[str, GlofasProduct]] = {
    "consolidated_v3": GlofasProduct(
        "consolidated_v3",
        "open-meteo-glofas-v3-flood-daily-v1",
        "glofas-v3-0.1-degree",
        0.1,
        11_000,
        "glofas-v3-0.1deg",
        GLOFAS_REANALYSIS_PARAMETERS,
    ),
    "consolidated_v4": GlofasProduct(
        "consolidated_v4",
        "open-meteo-glofas-v4-flood-daily-v1",
        "glofas-v4-0.05-degree",
        0.05,
        5_000,
        "glofas-v4-0.05deg",
        GLOFAS_REANALYSIS_PARAMETERS,
    ),
    "forecast_v3": GlofasProduct(
        "forecast_v3",
        "open-meteo-glofas-v3-flood-daily-v1",
        "glofas-v3-0.1-degree",
        0.1,
        11_000,
        "glofas-v3-0.1deg",
        GLOFAS_FORECAST_PARAMETERS,
    ),
    "forecast_v4": GlofasProduct(
        "forecast_v4",
        "open-meteo-glofas-v4-flood-daily-v1",
        "glofas-v4-0.05-degree",
        0.05,
        5_000,
        "glofas-v4-0.05deg",
        GLOFAS_FORECAST_PARAMETERS,
    ),
    "seamless_v3": GlofasProduct(
        "seamless_v3",
        "open-meteo-glofas-v3-flood-daily-v1",
        "glofas-v3-0.1-degree",
        0.1,
        11_000,
        "glofas-v3-0.1deg",
        GLOFAS_FORECAST_PARAMETERS,
    ),
    "seamless_v4": GlofasProduct(
        "seamless_v4",
        "open-meteo-glofas-v4-flood-daily-v1",
        "glofas-v4-0.05-degree",
        0.05,
        5_000,
        "glofas-v4-0.05deg",
        GLOFAS_FORECAST_PARAMETERS,
    ),
}


@dataclass(frozen=True)
class GlofasFloodChunk:
    """One bounded multi-location flood request derived from the reviewed plan's cell order."""

    key: str
    cells: tuple[AnalysisGridCell, ...]


@dataclass(frozen=True)
class GlofasFloodCapture:
    """What is known about one retrieval before its content is normalized."""

    retrieved_at: datetime
    wire_payload_bytes: int
    wire_payload_checksum: str
    # The host this retrieval really answered from; the paid tier is a different host plus one key.
    request_base_url: OpenMeteoFloodBaseUrl = OPEN_METEO_FLOOD_BASE_URL


class HistoricalGlofasFloodPlan(ContractModel):
    """Reviewed four-year Open-Meteo GloFAS river-discharge replay over an existing analysis lattice."""

    schema_version: str = Field(pattern=r"^open-meteo-glofas-v[0-9]{1,2}-flood-daily-v[0-9]{1,3}$")
    source: SourceDefinition
    window: HistoricalBackfillWindow
    model: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    cell_selection: Literal["nearest"] = OPEN_METEO_FLOOD_CELL_SELECTION
    grid_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,98}$")
    grid_resolution_m: int = Field(gt=0)
    native_grid_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,98}$")
    native_grid_degrees: float = Field(gt=0, le=1)
    native_grid_resolution_m: int = Field(gt=0)
    support_key: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,62}$")
    cells: list[AnalysisGridCell] = Field(min_length=1, max_length=GLOFAS_MAX_CELLS)
    chunk_cell_count: int = Field(ge=1, le=200)
    parameters: list[str] = Field(min_length=1, max_length=len(GLOFAS_FLOOD_SIGNAL_SPECIFICATIONS))
    transform_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("cells")
    @classmethod
    def require_sorted_unique_cells(cls, value: list[AnalysisGridCell]) -> list[AnalysisGridCell]:
        keys = [cell.cell_key for cell in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("GloFAS flood cells must be sorted and unique by cell_key")
        if len({(cell.latitude, cell.longitude) for cell in value}) != len(value):
            raise ValueError("GloFAS flood cells must not repeat a requested coordinate")
        return value

    @field_validator("parameters")
    @classmethod
    def require_supported_sorted_parameters(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("GloFAS flood parameters must be sorted and unique")
        unsupported = sorted(set(value).difference(GLOFAS_FLOOD_SIGNAL_SPECIFICATIONS))
        if unsupported:
            raise ValueError(f"unsupported GloFAS flood parameter(s): {', '.join(unsupported)}")
        return value

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_release_set_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "release_set_as_of")

    @model_validator(mode="after")
    def require_governed_lattice(self) -> HistoricalGlofasFloodPlan:
        if self.source.key != GLOFAS_SOURCE_KEY:
            raise ValueError(f"GloFAS flood plans require source.key='{GLOFAS_SOURCE_KEY}'")
        product = GLOFAS_PRODUCTS.get(self.model)
        if product is None:
            raise ValueError(f"GloFAS flood model must be one of: {', '.join(sorted(GLOFAS_PRODUCTS))}")
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
                f"GloFAS flood plan restates its product incorrectly for model '{self.model}': "
                f"{', '.join(disagreeing)}"
            )
        unserved = sorted(set(self.parameters).difference(product.supported_parameters))
        if unserved:
            raise ValueError(f"GloFAS model '{self.model}' does not serve parameter(s): {', '.join(unserved)}")
        nearest_points = {nearest_native_grid_point(cell, product.native_grid_degrees) for cell in self.cells}
        if len(nearest_points) != len(self.cells):
            raise ValueError("GloFAS flood cells must not share a native grid point")
        if len(self.chunks) > GLOFAS_MAX_CHUNKS:
            raise ValueError("GloFAS flood plan exceeds the reviewed chunk ceiling")
        return self

    @cached_property
    def product(self) -> GlofasProduct:
        """Return the reviewed product bundle this plan's model selects."""
        return GLOFAS_PRODUCTS[self.model]

    @cached_property
    def chunks(self) -> tuple[GlofasFloodChunk, ...]:
        """Cut the sorted cell list into stable request chunks anchored at the first cell."""
        size = self.chunk_cell_count
        return tuple(
            GlofasFloodChunk(key=f"cells-{index // size:04d}", cells=tuple(self.cells[index : index + size]))
            for index in range(0, len(self.cells), size)
        )

    @cached_property
    def plan_checksum(self) -> str:
        """Fingerprint every governed input controlling this replay, once per validated instance."""
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


class HistoricalGlofasReceipt(ContractModel):
    """One fully validated, cache-backed flood chunk receipt."""

    chunk_key: str = Field(pattern=r"^cells-\d{4}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=GLOFAS_MAX_RESPONSE_BYTES)
    cell_count: int = Field(ge=1)
    observation_count: int = Field(ge=0)
    observed_value_count: int = Field(ge=0)
    coverage_count: int = Field(ge=1)
    no_data_series_count: int = Field(ge=0)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "retrieved_at")


class HistoricalGlofasRawCacheReceipt(ContractModel):
    """Checksum-bound metadata for one reusable flood chunk download."""

    schema_version: Literal[1] = GLOFAS_RAW_CACHE_SCHEMA_VERSION
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_key: str = Field(pattern=r"^cells-\d{4}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=GLOFAS_MAX_RESPONSE_BYTES)
    wire_payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    wire_payload_bytes: int = Field(ge=1, le=GLOFAS_MAX_RESPONSE_BYTES)
    retrieved_at: datetime
    request_base_url: OpenMeteoFloodBaseUrl = OPEN_METEO_FLOOD_BASE_URL

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "retrieved_at")


class HistoricalGlofasCheckpoint(ContractModel):
    """Durable resumable state for the complete chunked flood plan."""

    schema_version: Literal[1] = GLOFAS_CHECKPOINT_SCHEMA_VERSION
    state: Literal["initialized", "running", "validated", "blocked"]
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipts: list[HistoricalGlofasReceipt] = Field(default_factory=list, max_length=GLOFAS_MAX_CHUNKS)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("updated_at")
    @classmethod
    def require_aware_update_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "updated_at")

    @field_validator("receipts")
    @classmethod
    def require_sorted_unique_receipts(cls, value: list[HistoricalGlofasReceipt]) -> list[HistoricalGlofasReceipt]:
        keys = [receipt.chunk_key for receipt in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("GloFAS flood receipts must be sorted and unique by chunk_key")
        return value


@dataclass(frozen=True)
class GlofasFloodChunkResult:
    """One validated canonical flood document plus its normalized daily facts and coverage evidence."""

    chunk_key: str
    retrieved_at: datetime
    payload: bytes
    payload_checksum: str
    wire_payload_bytes: int
    wire_payload_checksum: str
    request_base_url: OpenMeteoFloodBaseUrl
    observations: tuple[HistoricalSignalObservation, ...]
    coverage: tuple[HistoricalCoverageAudit, ...]
    grid_points: tuple[tuple[str, float, float], ...]


def glofas_flood_chunk_url(
    plan: HistoricalGlofasFloodPlan,
    chunk: GlofasFloodChunk,
    *,
    base_url: str | None = None,
) -> str:
    """Return the credential-free request one chunk is answered by, so a release records a reproducible query."""
    _plan_chunk(plan, chunk.key)
    return flood_daily_url(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        plan.window.start_date,
        plan.window.end_date,
        plan.model,
        base_url=base_url,
    )


def historical_glofas_plan_checksum(plan: HistoricalGlofasFloodPlan) -> str:
    """Fingerprint every governed input controlling this replay; memoized on the validated plan."""
    return plan.plan_checksum


def historical_glofas_checkpoint_path(root: Path, plan: HistoricalGlofasFloodPlan) -> Path:
    """Return a plan-bound durable checkpoint file path."""
    return lane_checkpoint_path(root, GLOFAS_LANE, historical_glofas_plan_checksum(plan))


def historical_glofas_raw_cache_paths(
    root: Path,
    plan: HistoricalGlofasFloodPlan,
    chunk: GlofasFloodChunk,
) -> tuple[Path, Path]:
    """Return canonical-document and receipt locations for one chunk beneath the local run root."""
    _plan_chunk(plan, chunk.key)
    return lane_raw_cache_paths(root, GLOFAS_LANE, historical_glofas_plan_checksum(plan), chunk.key)


def initialize_historical_glofas_checkpoint(
    plan: HistoricalGlofasFloodPlan,
    *,
    updated_at: datetime | None = None,
) -> HistoricalGlofasCheckpoint:
    """Create an empty checkpoint without opening the network or PostgreSQL."""
    return HistoricalGlofasCheckpoint(
        state="initialized",
        plan_checksum=historical_glofas_plan_checksum(plan),
        updated_at=require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def load_historical_glofas_checkpoint(path: Path) -> HistoricalGlofasCheckpoint:
    """Read a local flood checkpoint without requesting a provider payload."""
    return HistoricalGlofasCheckpoint.model_validate_json(path.read_bytes())


def write_historical_glofas_checkpoint(path: Path, checkpoint: HistoricalGlofasCheckpoint) -> None:
    """Atomically update credential-free flood checkpoint metadata."""
    atomic_write(path, canonical_json_bytes(checkpoint.model_dump(mode="json")))


def rederive_historical_glofas_checkpoint_state(
    plan: HistoricalGlofasFloodPlan,
    checkpoint: HistoricalGlofasCheckpoint,
) -> HistoricalGlofasCheckpoint:
    """Recompute `state` from receipt completeness so a recorded `blocked` cannot outlive its cause.

    `reason` is preserved: it is the evidence of the last stop, and only `state` gates a resume.
    """
    if checkpoint.plan_checksum != historical_glofas_plan_checksum(plan):
        raise ValueError("GloFAS flood checkpoint does not bind the reviewed plan")
    derived = derived_checkpoint_state(
        {receipt.chunk_key for receipt in checkpoint.receipts},
        [chunk.key for chunk in plan.chunks],
    )
    if derived == checkpoint.state:
        return checkpoint
    return checkpoint.model_copy(update={"state": derived})


def record_historical_glofas_result(
    plan: HistoricalGlofasFloodPlan,
    checkpoint: HistoricalGlofasCheckpoint,
    result: GlofasFloodChunkResult,
    *,
    updated_at: datetime | None = None,
) -> HistoricalGlofasCheckpoint:
    """Advance a chunk receipt only after every requested cell, signal and day is accounted for."""
    if checkpoint.plan_checksum != historical_glofas_plan_checksum(plan):
        raise ValueError("GloFAS flood checkpoint does not bind the reviewed plan")
    chunk = _plan_chunk(plan, result.chunk_key)
    require_accounted_glofas_result(plan, result)
    receipt = HistoricalGlofasReceipt(
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
    receipts = merged_chunk_receipts(GLOFAS_LANE, checkpoint.receipts, receipt)
    complete = [item.key for item in plan.chunks] == [item.chunk_key for item in receipts]
    return checkpoint.model_copy(
        update={
            "state": "validated" if complete else "running",
            "receipts": receipts,
            "updated_at": require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
            "reason": None,
        }
    )


def historical_glofas_release_manifest(
    plan: HistoricalGlofasFloodPlan,
    checkpoint: HistoricalGlofasCheckpoint,
) -> str:
    """Hash the complete ordered chunk receipt set a release must pin."""
    return lane_release_manifest(
        GLOFAS_LANE,
        plan_checksum=historical_glofas_plan_checksum(plan),
        transform_version=plan.transform_version,
        checkpoint_plan_checksum=checkpoint.plan_checksum,
        checkpoint_state=checkpoint.state,
        expected_chunk_keys=[chunk.key for chunk in plan.chunks],
        receipts=checkpoint.receipts,
    )


def cache_historical_glofas_result(
    root: Path,
    plan: HistoricalGlofasFloodPlan,
    result: GlofasFloodChunkResult,
) -> HistoricalGlofasRawCacheReceipt:
    """Persist one accounted-for canonical chunk document before the warehouse transaction begins."""
    chunk = _plan_chunk(plan, result.chunk_key)
    require_accounted_glofas_result(plan, result)
    payload_path, receipt_path = historical_glofas_raw_cache_paths(root, plan, chunk)
    receipt = HistoricalGlofasRawCacheReceipt(
        plan_checksum=historical_glofas_plan_checksum(plan),
        chunk_key=chunk.key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        wire_payload_checksum=result.wire_payload_checksum,
        wire_payload_bytes=result.wire_payload_bytes,
        retrieved_at=result.retrieved_at,
        request_base_url=OPEN_METEO_FLOOD_ENDPOINT.require_base_url(result.request_base_url),
    )
    if payload_path.exists() or receipt_path.exists():
        cached = load_cached_historical_glofas_result(root, plan, chunk)
        if cached is None:
            raise ValueError("GloFAS flood raw cache unexpectedly has no reusable source document")
        if cached.payload_checksum != result.payload_checksum:
            raise ValueError("GloFAS flood raw cache already binds this chunk to different source content")
        return HistoricalGlofasRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    write_raw_cache_pair(
        payload_path,
        receipt_path,
        result.payload,
        canonical_json_bytes(receipt.model_dump(mode="json")),
    )
    return receipt


def load_cached_historical_glofas_result(
    root: Path,
    plan: HistoricalGlofasFloodPlan,
    chunk: GlofasFloodChunk,
) -> GlofasFloodChunkResult | None:
    """Re-parse one validated local chunk document, never contacting the provider."""
    payload_path, receipt_path = historical_glofas_raw_cache_paths(root, plan, chunk)
    if not require_complete_raw_cache_pair(GLOFAS_LANE, payload_path, receipt_path):
        return None
    receipt = HistoricalGlofasRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    if receipt.plan_checksum != historical_glofas_plan_checksum(plan) or receipt.chunk_key != chunk.key:
        raise ValueError("GloFAS flood raw cache receipt does not bind this reviewed plan and chunk")
    payload = verified_cached_payload(
        GLOFAS_LANE,
        payload_path,
        expected_bytes=receipt.payload_bytes,
        expected_checksum=receipt.payload_checksum,
    )
    result = parse_glofas_flood_payload(
        plan,
        chunk,
        payload,
        GlofasFloodCapture(
            retrieved_at=receipt.retrieved_at,
            wire_payload_bytes=receipt.wire_payload_bytes,
            wire_payload_checksum=receipt.wire_payload_checksum,
            request_base_url=receipt.request_base_url,
        ),
    )
    require_accounted_glofas_result(plan, result)
    return result


class GlofasFloodFetchError(OpenMeteoLaneFetchError):
    """Raised when a flood chunk stopped for good; carries the chunk so a resume is unambiguous."""

    def __init__(self, chunk_key: str, cause: UpstreamError | None, attempts: int) -> None:
        """Name the chunk, how many attempts it really made, and the provider condition that stopped it."""
        super().__init__(GLOFAS_LANE.label, chunk_key, cause, attempts)


async def fetch_glofas_flood_chunk(
    plan: HistoricalGlofasFloodPlan,
    chunk: GlofasFloodChunk,
    *,
    client: httpx.AsyncClient,
    retrieved_at: datetime | None = None,
    sleep: object = None,
) -> GlofasFloodChunkResult:
    """Fetch one chunk with bounded retries; an exhausted quota is raised, never swallowed."""
    _plan_chunk(plan, chunk.key)
    # The credential lives only in `request.request_url`; only `request.base_url` is ever recorded.
    request = flood_daily_request(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        plan.window.start_date,
        plan.window.end_date,
        plan.model,
    )
    capture = await fetch_lane_capture(
        GLOFAS_LANE,
        chunk.key,
        request,
        client=client,
        fetch_text=fetch_flood_daily,
        error_factory=GlofasFloodFetchError,
        retrieved_at=retrieved_at,
        sleep=sleep,
    )
    return parse_glofas_flood_payload(
        plan,
        chunk,
        capture.canonical_payload,
        GlofasFloodCapture(
            retrieved_at=capture.retrieved_at,
            wire_payload_bytes=capture.wire_payload_bytes,
            wire_payload_checksum=capture.wire_payload_checksum,
            request_base_url=OPEN_METEO_FLOOD_ENDPOINT.require_base_url(capture.request_base_url),
        ),
    )


async def run_glofas_flood_chunks(
    plan: HistoricalGlofasFloodPlan,
    chunks: Sequence[GlofasFloodChunk],
    *,
    concurrency: int = DEFAULT_CHUNK_CONCURRENCY,
    client: httpx.AsyncClient | None = None,
) -> list[GlofasFloodChunkResult | BaseException]:
    """Fetch several chunks under a bounded semaphore, preserving each chunk's own failure."""

    async def one(active: httpx.AsyncClient, chunk: GlofasFloodChunk) -> GlofasFloodChunkResult:
        return await fetch_glofas_flood_chunk(plan, chunk, client=active)

    return await run_lane_chunks(GLOFAS_LANE, chunks, one, concurrency=concurrency, client=client)


def parse_glofas_flood_payload(
    plan: HistoricalGlofasFloodPlan,
    chunk: GlofasFloodChunk,
    payload: bytes,
    capture: GlofasFloodCapture,
) -> GlofasFloodChunkResult:
    """Validate one canonical flood document and normalize every reviewed cell/signal/day."""
    _plan_chunk(plan, chunk.key)
    timestamp = require_aware_utc(capture.retrieved_at, "retrieved_at")
    if not payload or len(payload) > GLOFAS_MAX_RESPONSE_BYTES:
        raise ValueError("GloFAS flood response exceeds the reviewed byte boundary")
    locations = ordered_locations(GLOFAS_LANE, payload, len(chunk.cells))
    expected_dates = list(date_range(plan.window.start_date, plan.window.end_date))
    payload_checksum = hashlib.sha256(payload).hexdigest()
    max_offset = max_grid_offset_degrees(plan.product.native_grid_degrees)

    observations: list[HistoricalSignalObservation] = []
    coverage: list[HistoricalCoverageAudit] = []
    grid_points: list[tuple[str, float, float]] = []
    seen_grid_points: set[tuple[float, float]] = set()
    for cell, location in zip(chunk.cells, locations, strict=True):
        latitude, longitude = validated_grid_point(GLOFAS_LANE, cell, location, max_offset)
        if (latitude, longitude) in seen_grid_points:
            raise ValueError("GloFAS flood returned one native grid point for two reviewed cells")
        seen_grid_points.add((latitude, longitude))
        grid_points.append((cell.cell_key, latitude, longitude))
        daily = _flood_daily_block(location, plan.parameters)
        days = _flood_days(daily, expected_dates)
        for parameter in plan.parameters:
            specification = GLOFAS_FLOOD_SIGNAL_SPECIFICATIONS[parameter]
            values = _flood_values(daily, parameter, specification, len(days))
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
    result = GlofasFloodChunkResult(
        chunk_key=chunk.key,
        retrieved_at=timestamp,
        payload=payload,
        payload_checksum=payload_checksum,
        wire_payload_bytes=capture.wire_payload_bytes,
        wire_payload_checksum=capture.wire_payload_checksum,
        request_base_url=OPEN_METEO_FLOOD_ENDPOINT.require_base_url(capture.request_base_url),
        observations=tuple(observations),
        coverage=tuple(coverage),
        grid_points=tuple(grid_points),
    )
    require_accounted_glofas_result(plan, result)
    return result


def require_accounted_glofas_result(plan: HistoricalGlofasFloodPlan, result: GlofasFloodChunkResult) -> None:
    """Reject any chunk that cannot account for every requested cell, signal, and day.

    Accounting, not completeness: a series the provider modelled nowhere is allowed, but it must
    arrive as one `no_data` coverage row rather than as silently absent daily rows.
    """
    chunk = _plan_chunk(plan, result.chunk_key)
    day_count = plan.window.day_count
    if len(result.payload) < 1 or len(result.payload) > GLOFAS_MAX_RESPONSE_BYTES:
        raise ValueError("GloFAS flood result payload violates the reviewed byte boundary")
    if hashlib.sha256(result.payload).hexdigest() != result.payload_checksum:
        raise ValueError("GloFAS flood result payload checksum does not match its content")
    expected_series = {(cell.cell_key, parameter) for cell in chunk.cells for parameter in plan.parameters}
    coverage_series = [(item.cell_key, item.source_parameter) for item in result.coverage]
    if sorted(coverage_series) != sorted(expected_series) or len(coverage_series) != len(expected_series):
        raise ValueError("GloFAS flood coverage audit does not describe every reviewed cell and signal")
    if any(item.expected_observation_count != day_count for item in result.coverage):
        raise ValueError("GloFAS flood coverage audit does not span the reviewed window")
    expected_rows = sum(0 if item.status == "no_data" else day_count for item in result.coverage)
    if len(result.observations) != expected_rows:
        raise ValueError("GloFAS flood result dropped or duplicated normalized daily rows")
    observed_by_series: dict[tuple[str, str], int] = {}
    for observation in result.observations:
        key = (observation.cell_key, observation.source_parameter)
        observed_by_series[key] = observed_by_series.get(key, 0) + (1 if observation.is_observed else 0)
    for item in result.coverage:
        actual = observed_by_series.get((item.cell_key, item.source_parameter), 0)
        if actual != item.received_observation_count:
            raise ValueError("GloFAS flood coverage counts disagree with the normalized daily rows")
    if len(result.grid_points) != len(chunk.cells):
        raise ValueError("GloFAS flood result does not record a native grid point for every reviewed cell")


def _cell_observations(
    *,
    cell_key: str,
    parameter: str,
    specification: GlofasFloodSignal,
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


def _flood_daily_block(location: dict[str, object], parameters: Sequence[str]) -> dict[str, object]:
    daily = location.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("GloFAS flood location is missing its daily block")
    missing = sorted(set(parameters).difference(daily))
    if missing:
        raise ValueError(f"GloFAS flood location is missing requested variable(s): {', '.join(missing)}")
    return daily


def _flood_days(daily: dict[str, object], expected_dates: Sequence[date]) -> list[date]:
    """Bucket by the ISO date prefix the publisher named, never by recasting an instant."""
    raw = daily.get("time")
    if not isinstance(raw, list):
        raise ValueError("GloFAS flood daily block is missing its time axis")
    days: list[date] = []
    for value in raw:
        if not isinstance(value, str) or len(value) < ISO_DATE_LENGTH:
            raise ValueError("GloFAS flood daily time values must be ISO-8601 date strings")
        try:
            days.append(date.fromisoformat(value[:ISO_DATE_LENGTH]))
        except ValueError as exc:
            raise ValueError("GloFAS flood daily time values must be ISO-8601 date strings") from exc
    if days != list(expected_dates):
        raise ValueError("GloFAS flood daily time axis does not match the reviewed window")
    return days


def _flood_values(
    daily: dict[str, object],
    parameter: str,
    specification: GlofasFloodSignal,
    day_count: int,
) -> list[float | None]:
    """Read one variable's daily series through the shared bounded reader."""
    return bounded_numeric_series(
        GLOFAS_LANE,
        daily,
        parameter,
        minimum=specification.minimum,
        maximum=specification.maximum,
        expected_count=day_count,
        subject="variable",
    )


def _plan_chunk(plan: HistoricalGlofasFloodPlan, chunk_key: str) -> GlofasFloodChunk:
    try:
        return next(chunk for chunk in plan.chunks if chunk.key == chunk_key)
    except StopIteration as exc:
        raise ValueError("GloFAS flood chunk is not part of the reviewed plan") from exc
