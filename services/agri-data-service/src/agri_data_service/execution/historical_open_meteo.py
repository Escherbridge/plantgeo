"""Cache-first contracts for the Open-Meteo ERA5/ERA5-Land archive replay over a reviewed lattice."""

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
from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_ARCHIVE_BASE_URL,
    OPEN_METEO_ARCHIVE_BOUNDS,
    OPEN_METEO_ARCHIVE_CELL_SELECTION,
    OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL,
    OPEN_METEO_ERA5_LAND_MODEL,
    OPEN_METEO_ERA5_MODEL,
    OpenMeteoArchiveBaseUrl,
    OpenMeteoArchiveModel,
    archive_daily_request,
    archive_daily_url,
    fetch_archive_daily,
    require_archive_base_url,
)
from agri_data_service.ingest.open_meteo_endpoint import OpenMeteoEndpoint, OpenMeteoProductRequest

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    import httpx

    from agri_data_service.ingest.http import UpstreamError

OPEN_METEO_ARCHIVE_SCHEMA_VERSION: Literal["open-meteo-era5-land-archive-daily-v1"] = (
    "open-meteo-era5-land-archive-daily-v1"
)
OPEN_METEO_ARCHIVE_SOURCE_KEY: Final = "open-meteo-era5-land-archive"

# Deliberately not `surface`; see execution/AGENTS.md §historical_open_meteo.
OPEN_METEO_ARCHIVE_SUPPORT_KEY: Final = "era5-land-0.1deg"

OPEN_METEO_ARCHIVE_NATIVE_GRID_NAME: Final = "era5-land-0.1-degree"
OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES: Final = 0.1
OPEN_METEO_ARCHIVE_NATIVE_RESOLUTION_M: Final = 9_000

OPEN_METEO_ERA5_SOURCE_KEY: Final = "open-meteo-era5-archive"
OPEN_METEO_ERA5_SUPPORT_KEY: Final = "era5-0.25deg"
OPEN_METEO_ERA5_NATIVE_GRID_NAME: Final = "era5-0.25-degree"
OPEN_METEO_ERA5_NATIVE_GRID_DEGREES: Final = 0.25
OPEN_METEO_ERA5_NATIVE_RESOLUTION_M: Final = 25_000


class OpenMeteoArchiveProduct(NamedTuple):
    """Everything the chosen archive model fixes: its native lattice, its spatial support, its source."""

    native_grid_name: str
    native_grid_degrees: float
    native_grid_resolution_m: int
    support_key: str
    source_key: str
    source_kind: str
    upstream_product: str
    # `agri.artifact.kind`, which `historical_export.py` copies into every export manifest as
    # `source_artifact_kind`. It names the product the bytes came from, so it cannot be one literal
    # for two reanalyses; the ERA5-Land spelling is frozen because artifacts are immutable.
    artifact_kind: str


# One archive endpoint, two reanalyses, and the model is what decides which. They are NOT
# interchangeable and each gets its own `data_source` key, because `_ensure_data_source` pins the
# model and native grid into the source's `configuration`: one key cannot honestly describe both,
# and `support_key` is what lets a reader tell a 0.1-degree sample from a 0.25-degree one.
OPEN_METEO_ARCHIVE_PRODUCTS: Final[dict[OpenMeteoArchiveModel, OpenMeteoArchiveProduct]] = {
    OPEN_METEO_ERA5_LAND_MODEL: OpenMeteoArchiveProduct(
        native_grid_name=OPEN_METEO_ARCHIVE_NATIVE_GRID_NAME,
        native_grid_degrees=OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES,
        native_grid_resolution_m=OPEN_METEO_ARCHIVE_NATIVE_RESOLUTION_M,
        support_key=OPEN_METEO_ARCHIVE_SUPPORT_KEY,
        source_key=OPEN_METEO_ARCHIVE_SOURCE_KEY,
        source_kind="open_meteo_era5_land_archive_daily",
        upstream_product="ECMWF ERA5-Land via Copernicus Climate Change Service",
        # Byte-identical to the literal every already-persisted ERA5-Land artifact carries.
        artifact_kind="source_open_meteo_era5_land_archive_daily_json",
    ),
    OPEN_METEO_ERA5_MODEL: OpenMeteoArchiveProduct(
        native_grid_name=OPEN_METEO_ERA5_NATIVE_GRID_NAME,
        native_grid_degrees=OPEN_METEO_ERA5_NATIVE_GRID_DEGREES,
        native_grid_resolution_m=OPEN_METEO_ERA5_NATIVE_RESOLUTION_M,
        support_key=OPEN_METEO_ERA5_SUPPORT_KEY,
        source_key=OPEN_METEO_ERA5_SOURCE_KEY,
        source_kind="open_meteo_era5_archive_daily",
        upstream_product="ECMWF ERA5 via Copernicus Climate Change Service",
        artifact_kind="source_open_meteo_era5_archive_daily_json",
    ),
}

# Assembled here rather than imported because `ingest/open_meteo.py` predates `OpenMeteoEndpoint`;
# both hosts and the byte budget are still its constants. See execution/AGENTS.md §historical_open_meteo.
OPEN_METEO_ARCHIVE_ENDPOINT: Final[OpenMeteoEndpoint[OpenMeteoArchiveBaseUrl]] = OpenMeteoEndpoint(
    free_base_url=OPEN_METEO_ARCHIVE_BASE_URL,
    customer_base_url=OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL,
    bounds=OPEN_METEO_ARCHIVE_BOUNDS,
)

# Retrieval, caching, checksums and checkpoint state are the shared scaffold's, not this lane's:
# `OPEN_METEO_ARCHIVE_LANE.label` is what prefixes every message they raise. See execution/AGENTS.md.
OPEN_METEO_ARCHIVE_LANE: Final = OpenMeteoLane(
    label="Open-Meteo archive",
    cache_directory_name="historical-open-meteo",
    endpoint=OPEN_METEO_ARCHIVE_ENDPOINT,
)

# The attribution tolerance -- half the native grid spacing plus a float-comparison epsilon -- is
# derived per plan from `plan.product.native_grid_degrees` in `parse_open_meteo_archive_payload`,
# never held as a module constant: ERA5's 0.25-degree box is two and a half times ERA5-Land's, and a
# single shared value would silently accept or reject the wrong points for one of the two models.

OPEN_METEO_ARCHIVE_MAX_RESPONSE_BYTES: Final = OPEN_METEO_ARCHIVE_BOUNDS.max_bytes
OPEN_METEO_ARCHIVE_MAX_CELLS: Final = 10_000
OPEN_METEO_ARCHIVE_MAX_CHUNKS: Final = 2_000
OPEN_METEO_ARCHIVE_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
OPEN_METEO_ARCHIVE_RAW_CACHE_SCHEMA_VERSION: Literal[1] = 1


class OpenMeteoArchiveSignal(NamedTuple):
    """One daily variable's archive model, warehouse naming, units, and inclusive acceptance range."""

    # The reanalysis that actually publishes this variable, verified live. Required, and first,
    # because assuming it was the lane's usual model is exactly how `shortwave_radiation_sum`
    # silently returned 580,414 nulls. See execution/AGENTS.md §historical_open_meteo.
    model: OpenMeteoArchiveModel
    signal_name: str
    original_unit: str
    normalized_unit: str
    minimum: float
    maximum: float
    # The provider's own `daily_units` spelling for this variable, verified live against the API
    # (e.g. "MJ/m²", not the warehouse's "MJ/m^2/day"). Checked against every payload in
    # `_archive_daily_block` when set; None for the seven variables whose provider spelling has
    # not been independently confirmed, rather than guessing a Unicode string that would reject
    # every valid payload if the guess were wrong.
    provider_unit: str | None = None


# Open-Meteo daily variable -> the model that publishes it, warehouse signal, units, and the range a
# value must fall inside. The bounds are the only thing standing between a provider sentinel (-999, a
# netCDF `_FillValue`) and 1,462 accepted rows per cell. See execution/AGENTS.md §historical_open_meteo.
_LAND: Final = OPEN_METEO_ERA5_LAND_MODEL
OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS: Final[dict[str, OpenMeteoArchiveSignal]] = {
    "soil_moisture_0_to_7cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_water_content_layer_1", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_moisture_7_to_28cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_water_content_layer_2", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_moisture_28_to_100cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_water_content_layer_3", "m^3/m^3", "m^3/m^3", 0.0, 1.0
    ),
    "soil_temperature_0_to_7cm_mean": OpenMeteoArchiveSignal(_LAND, "soil_temperature_level_1", "C", "C", -100.0, 70.0),
    # The remaining three bands align with ERA5-Land's CDS levels 2-4, so this lane can carry the
    # soil-state profile the CDS lane was fetching -- at 0.1 degrees instead of 1.0, and keyless.
    "soil_temperature_7_to_28cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_temperature_level_2", "C", "C", -100.0, 70.0
    ),
    "soil_temperature_28_to_100cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_temperature_level_3", "C", "C", -100.0, 70.0
    ),
    "soil_temperature_100_to_255cm_mean": OpenMeteoArchiveSignal(
        _LAND, "soil_temperature_level_4", "C", "C", -100.0, 70.0
    ),
    # An atmospheric-dryness covariate, not a soil-state one; see execution/AGENTS.md §historical_open_meteo.
    "vapour_pressure_deficit_max": OpenMeteoArchiveSignal(_LAND, "vapor_pressure_deficit", "kPa", "kPa", 0.0, 15.0),
    # A SECOND upstream for the signal NASA POWER already writes, deliberately sharing its name AND
    # its unit with NO conversion: `ALLSKY_SFC_SW_DWN` and `shortwave_radiation_sum` are both a daily
    # sum of megajoules per square metre. See execution/AGENTS.md §historical_open_meteo.
    #
    # `era5`, NOT `era5_land`: ERA5-Land publishes no radiation flux through this endpoint and answers
    # the variable with a present, entirely-null series. Measured live 2026-08-09 at 31N/104W --
    # models=era5_land returned 1,462 nulls per cell, models=era5 returned 30.76, 31.01, 30.21 MJ/m².
    #
    # provider_unit is set (unlike every sibling above) because this is the one variable in this
    # table whose plausible wrong-unit failure -- a kWh/m^2/day series, 0-10 -- lands entirely
    # inside the [0.0, 60.0] acceptance range below and would merge silently under the NASA
    # series' own signal_name. Verified live against the archive API 2026-08-09.
    "shortwave_radiation_sum": OpenMeteoArchiveSignal(
        OPEN_METEO_ERA5_MODEL, "surface_shortwave_radiation", "MJ/m^2/day", "MJ/m^2/day", 0.0, 60.0, "MJ/m²"
    ),
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
    """Reviewed four-year Open-Meteo archive replay, on one declared reanalysis, over an existing lattice."""

    schema_version: Literal["open-meteo-era5-land-archive-daily-v1"] = OPEN_METEO_ARCHIVE_SCHEMA_VERSION
    source: SourceDefinition
    window: HistoricalBackfillWindow
    # Every field below down to `support_key` is fixed by `model` and re-checked against
    # `OPEN_METEO_ARCHIVE_PRODUCTS` in `require_governed_lattice`; none of them is free text.
    model: OpenMeteoArchiveModel = OPEN_METEO_ERA5_LAND_MODEL
    cell_selection: Literal["nearest"] = OPEN_METEO_ARCHIVE_CELL_SELECTION
    time_zone: Literal["GMT"] = "GMT"
    grid_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,98}$")
    grid_resolution_m: int = Field(gt=0)
    native_grid_name: str = Field(default=OPEN_METEO_ARCHIVE_NATIVE_GRID_NAME, pattern=r"^[a-z0-9][a-z0-9.-]{1,98}$")
    native_grid_degrees: float = Field(gt=0, le=1)
    native_grid_resolution_m: int = Field(gt=0)
    support_key: str = Field(default=OPEN_METEO_ARCHIVE_SUPPORT_KEY, pattern=r"^[a-z0-9][a-z0-9.-]{1,62}$")
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

    @property
    def product(self) -> OpenMeteoArchiveProduct:
        """Return the native lattice, spatial support and source identity this plan's model fixes."""
        return OPEN_METEO_ARCHIVE_PRODUCTS[self.model]

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_release_set_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "release_set_as_of")

    @model_validator(mode="after")
    def require_governed_lattice(self) -> HistoricalOpenMeteoArchivePlan:
        product = self.product
        if self.source.key != product.source_key:
            raise ValueError(f"Open-Meteo archive plans on model '{self.model}' require '{product.source_key}'")
        # A variable the requested model does not publish comes back as a present, entirely-null
        # series rather than an error, so refusing the plan is the only place it can be caught
        # before a quota-bound fetch. See execution/AGENTS.md §historical_open_meteo.
        foreign = sorted(
            parameter
            for parameter in self.parameters
            if OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter].model != self.model
        )
        if foreign:
            raise ValueError(f"Open-Meteo model '{self.model}' does not publish: {', '.join(foreign)}")
        if self.native_grid_name != product.native_grid_name:
            raise ValueError(f"Open-Meteo archive plans on model '{self.model}' must name its native grid")
        if self.native_grid_degrees != product.native_grid_degrees:
            raise ValueError(f"Open-Meteo archive plans on model '{self.model}' must record its native spacing")
        if self.native_grid_resolution_m != product.native_grid_resolution_m:
            raise ValueError(f"Open-Meteo archive plans on model '{self.model}' must record its documented resolution")
        if self.support_key != product.support_key:
            raise ValueError(f"Open-Meteo archive plans on model '{self.model}' must record its spatial support")
        nearest_points = {nearest_native_grid_point(cell, product.native_grid_degrees) for cell in self.cells}
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
        return require_aware_utc(value, "retrieved_at")


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
        return require_aware_utc(value, "retrieved_at")


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
        return require_aware_utc(value, "updated_at")

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
        model=plan.model,
        base_url=base_url,
    )


def historical_open_meteo_plan_checksum(plan: HistoricalOpenMeteoArchivePlan) -> str:
    """Fingerprint every governed input controlling this replay; memoized on the validated plan."""
    return plan.plan_checksum


def historical_open_meteo_checkpoint_path(root: Path, plan: HistoricalOpenMeteoArchivePlan) -> Path:
    """Return a plan-bound durable checkpoint file path."""
    return lane_checkpoint_path(root, OPEN_METEO_ARCHIVE_LANE, historical_open_meteo_plan_checksum(plan))


def historical_open_meteo_raw_cache_paths(
    root: Path,
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
) -> tuple[Path, Path]:
    """Return canonical-document and receipt locations for one chunk beneath the local run root."""
    _plan_chunk(plan, chunk.key)
    return lane_raw_cache_paths(root, OPEN_METEO_ARCHIVE_LANE, historical_open_meteo_plan_checksum(plan), chunk.key)


def initialize_historical_open_meteo_checkpoint(
    plan: HistoricalOpenMeteoArchivePlan,
    *,
    updated_at: datetime | None = None,
) -> HistoricalOpenMeteoCheckpoint:
    """Create an empty checkpoint without opening the network or PostgreSQL."""
    return HistoricalOpenMeteoCheckpoint(
        state="initialized",
        plan_checksum=historical_open_meteo_plan_checksum(plan),
        updated_at=require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def load_historical_open_meteo_checkpoint(path: Path) -> HistoricalOpenMeteoCheckpoint:
    """Read a local archive checkpoint without requesting a provider payload."""
    return HistoricalOpenMeteoCheckpoint.model_validate_json(path.read_bytes())


def write_historical_open_meteo_checkpoint(path: Path, checkpoint: HistoricalOpenMeteoCheckpoint) -> None:
    """Atomically update credential-free archive checkpoint metadata."""
    atomic_write(path, canonical_json_bytes(checkpoint.model_dump(mode="json")))


def rederive_historical_open_meteo_checkpoint_state(
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
) -> HistoricalOpenMeteoCheckpoint:
    """Recompute `state` from receipt completeness so a recorded `blocked` cannot outlive its cause.

    `reason` is preserved: it is the evidence of the last stop, and only `state` gates a resume.
    """
    if checkpoint.plan_checksum != historical_open_meteo_plan_checksum(plan):
        raise ValueError("Open-Meteo archive checkpoint does not bind the reviewed plan")
    derived = derived_checkpoint_state(
        {receipt.chunk_key for receipt in checkpoint.receipts},
        [chunk.key for chunk in plan.chunks],
    )
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
    receipts = merged_chunk_receipts(OPEN_METEO_ARCHIVE_LANE, checkpoint.receipts, receipt)
    complete = [item.key for item in plan.chunks] == [item.chunk_key for item in receipts]
    return checkpoint.model_copy(
        update={
            "state": "validated" if complete else "running",
            "receipts": receipts,
            "updated_at": require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
            "reason": None,
        }
    )


def historical_open_meteo_release_manifest(
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
) -> str:
    """Hash the complete ordered chunk receipt set a release must pin."""
    return lane_release_manifest(
        OPEN_METEO_ARCHIVE_LANE,
        plan_checksum=historical_open_meteo_plan_checksum(plan),
        transform_version=plan.transform_version,
        checkpoint_plan_checksum=checkpoint.plan_checksum,
        checkpoint_state=checkpoint.state,
        expected_chunk_keys=[chunk.key for chunk in plan.chunks],
        receipts=checkpoint.receipts,
    )


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
    write_raw_cache_pair(
        payload_path,
        receipt_path,
        result.payload,
        canonical_json_bytes(receipt.model_dump(mode="json")),
    )
    return receipt


def load_cached_historical_open_meteo_result(
    root: Path,
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
) -> OpenMeteoArchiveChunkResult | None:
    """Re-parse one validated local chunk document, never contacting the provider."""
    payload_path, receipt_path = historical_open_meteo_raw_cache_paths(root, plan, chunk)
    if not require_complete_raw_cache_pair(OPEN_METEO_ARCHIVE_LANE, payload_path, receipt_path):
        return None
    receipt = HistoricalOpenMeteoRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    if receipt.plan_checksum != historical_open_meteo_plan_checksum(plan) or receipt.chunk_key != chunk.key:
        raise ValueError("Open-Meteo archive raw cache receipt does not bind this reviewed plan and chunk")
    payload = verified_cached_payload(
        OPEN_METEO_ARCHIVE_LANE,
        payload_path,
        expected_bytes=receipt.payload_bytes,
        expected_checksum=receipt.payload_checksum,
    )
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


class OpenMeteoArchiveFetchError(OpenMeteoLaneFetchError):
    """Raised when a chunk stopped for good; carries the chunk so a resume is unambiguous."""

    def __init__(self, chunk_key: str, cause: UpstreamError | None, attempts: int) -> None:
        """Name the chunk, how many attempts it really made, and the provider condition that stopped it."""
        super().__init__(OPEN_METEO_ARCHIVE_LANE.label, chunk_key, cause, attempts)


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
        model=plan.model,
    )
    capture = await fetch_lane_capture(
        OPEN_METEO_ARCHIVE_LANE,
        chunk.key,
        # The archive builder predates `OpenMeteoProductRequest`; restating the two fields it already
        # resolved is the whole adaptation, and no credential crosses it that `request_url` did not.
        OpenMeteoProductRequest(base_url=request.base_url, request_url=request.request_url),
        client=client,
        fetch_text=fetch_archive_daily,
        error_factory=OpenMeteoArchiveFetchError,
        retrieved_at=retrieved_at,
        sleep=sleep,
    )
    return parse_open_meteo_archive_payload(
        plan,
        chunk,
        capture.canonical_payload,
        OpenMeteoArchiveCapture(
            retrieved_at=capture.retrieved_at,
            wire_payload_bytes=capture.wire_payload_bytes,
            wire_payload_checksum=capture.wire_payload_checksum,
            request_base_url=require_archive_base_url(capture.request_base_url),
        ),
    )


async def run_open_meteo_archive_chunks(
    plan: HistoricalOpenMeteoArchivePlan,
    chunks: Sequence[OpenMeteoArchiveChunk],
    *,
    concurrency: int = DEFAULT_CHUNK_CONCURRENCY,
    client: httpx.AsyncClient | None = None,
) -> list[OpenMeteoArchiveChunkResult | BaseException]:
    """Fetch several chunks under a bounded semaphore, preserving each chunk's own failure."""

    async def one(active: httpx.AsyncClient, chunk: OpenMeteoArchiveChunk) -> OpenMeteoArchiveChunkResult:
        return await fetch_open_meteo_archive_chunk(plan, chunk, client=active)

    return await run_lane_chunks(OPEN_METEO_ARCHIVE_LANE, chunks, one, concurrency=concurrency, client=client)


def parse_open_meteo_archive_payload(
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
    payload: bytes,
    capture: OpenMeteoArchiveCapture,
) -> OpenMeteoArchiveChunkResult:
    """Validate one canonical archive document and normalize every reviewed cell/signal/day."""
    _plan_chunk(plan, chunk.key)
    timestamp = require_aware_utc(capture.retrieved_at, "retrieved_at")
    if not payload or len(payload) > OPEN_METEO_ARCHIVE_MAX_RESPONSE_BYTES:
        raise ValueError("Open-Meteo archive response exceeds the reviewed byte boundary")
    locations = ordered_locations(OPEN_METEO_ARCHIVE_LANE, payload, len(chunk.cells))
    expected_dates = list(date_range(plan.window.start_date, plan.window.end_date))
    payload_checksum = hashlib.sha256(payload).hexdigest()
    max_offset_degrees = max_grid_offset_degrees(plan.product.native_grid_degrees)

    observations: list[HistoricalSignalObservation] = []
    coverage: list[HistoricalCoverageAudit] = []
    grid_points: list[tuple[str, float, float, float | None]] = []
    seen_grid_points: set[tuple[float, float]] = set()
    for cell, location in zip(chunk.cells, locations, strict=True):
        latitude, longitude, elevation = _validated_grid_point(cell, location, max_offset_degrees)
        if (latitude, longitude) in seen_grid_points:
            raise ValueError("Open-Meteo archive returned one native grid point for two reviewed cells")
        seen_grid_points.add((latitude, longitude))
        grid_points.append((cell.cell_key, latitude, longitude, elevation))
        daily = _archive_daily_block(location, plan.parameters)
        days = _archive_days(daily, expected_dates)
        for parameter in plan.parameters:
            specification = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter]
            _require_provider_unit(location, parameter, specification)
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


def open_meteo_observed_values_by_parameter(result: OpenMeteoArchiveChunkResult) -> dict[str, int]:
    """Total one chunk's observed values per requested variable, for the plan-level emptiness gate."""
    totals: dict[str, int] = {}
    for item in result.coverage:
        totals[item.source_parameter] = totals.get(item.source_parameter, 0) + item.received_observation_count
    return totals


def unanswered_open_meteo_parameters(
    plan: HistoricalOpenMeteoArchivePlan,
    observed_by_parameter: Mapping[str, int],
) -> tuple[str, ...]:
    """Name every reviewed variable the provider answered with no value in any cell of the plan.

    Whole-plan, not per-chunk: one cell or one chunk may honestly hold no data, but a variable that
    is empty across every reviewed cell is a mapping or model-availability failure wearing a
    coverage failure's clothes. See execution/AGENTS.md §historical_open_meteo.
    """
    return tuple(parameter for parameter in plan.parameters if observed_by_parameter.get(parameter, 0) == 0)


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


def _validated_grid_point(
    cell: AnalysisGridCell,
    location: dict[str, object],
    max_offset_degrees: float,
) -> tuple[float, float, float | None]:
    """Bind the point through the shared attribution guard, then read the elevation only this lane keeps."""
    latitude, longitude = validated_grid_point(OPEN_METEO_ARCHIVE_LANE, cell, location, max_offset_degrees)
    elevation = location.get("elevation")
    if elevation is not None and (isinstance(elevation, bool) or not isinstance(elevation, int | float)):
        raise ValueError("Open-Meteo archive elevation must be numeric when present")
    return latitude, longitude, None if elevation is None else float(elevation)


def _require_provider_unit(location: dict[str, object], parameter: str, specification: OpenMeteoArchiveSignal) -> None:
    """Reject a payload whose provider unit drifted from the one the mapping was verified against.

    A silent unit change is worse than a loud one: it would land at the stored, already-correct
    normalized_unit while carrying the wrong magnitude, and the merge with a second producer of
    the same signal_name gives it no other chance to be caught. Only checked when the mapping
    records a verified provider_unit -- see OpenMeteoArchiveSignal's own comment for why the other
    variables are not asserted here.
    """
    if specification.provider_unit is None:
        return
    daily_units = location.get("daily_units")
    if not isinstance(daily_units, dict):
        raise ValueError("Open-Meteo archive location is missing its daily_units block")
    reported = daily_units.get(parameter)
    if reported != specification.provider_unit:
        raise ValueError(
            f"Open-Meteo archive reported unit {reported!r} for {parameter!r}, expected {specification.provider_unit!r}"
        )


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
    """Read one variable's daily series through the shared bounded reader, before any row is built.

    A sentinel is a provider failure, not a gap: downgrading it to `no_data` would assert the
    provider modelled nothing here, which is a different and unevidenced claim. See
    execution/AGENTS.md §historical_open_meteo.
    """
    return bounded_numeric_series(
        OPEN_METEO_ARCHIVE_LANE,
        daily,
        parameter,
        minimum=specification.minimum,
        maximum=specification.maximum,
        expected_count=day_count,
        subject="variable",
    )


def _plan_chunk(plan: HistoricalOpenMeteoArchivePlan, chunk_key: str) -> OpenMeteoArchiveChunk:
    try:
        return next(chunk for chunk in plan.chunks if chunk.key == chunk_key)
    except StopIteration as exc:
        raise ValueError("Open-Meteo archive chunk is not part of the reviewed plan") from exc
