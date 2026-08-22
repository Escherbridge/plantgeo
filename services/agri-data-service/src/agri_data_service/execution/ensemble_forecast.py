"""Cache-first contracts for the Open-Meteo Ensemble forecast, staged as forecast-receipt quantile carriage."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple

from pydantic import Field, field_validator, model_validator

from agri_data_service.execution.backfill_types import AnalysisGridCell  # noqa: TC001
from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.open_meteo_lane import (
    DEFAULT_CHUNK_CONCURRENCY,
    OpenMeteoLane,
    OpenMeteoLaneFetchError,
    atomic_write,
    bounded_numeric_series,
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
from agri_data_service.ingest.open_meteo_ensemble import (
    OPEN_METEO_ENSEMBLE_BASE_URL,
    OPEN_METEO_ENSEMBLE_BOUNDS,
    OPEN_METEO_ENSEMBLE_CELL_SELECTION,
    OPEN_METEO_ENSEMBLE_ENDPOINT,
    OPEN_METEO_ENSEMBLE_TIME_ZONE,
    OpenMeteoEnsembleBaseUrl,
    ensemble_hourly_request,
    ensemble_hourly_url,
    ensemble_member_field_name,
    fetch_ensemble_hourly,
)
from agri_data_service.ingest.policy import format_javascript_number

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import httpx

    from agri_data_service.ingest.http import UpstreamError

ENSEMBLE_SOURCE_KEY: Final = "open-meteo-ensemble-forecast"

ENSEMBLE_MAX_RESPONSE_BYTES: Final = OPEN_METEO_ENSEMBLE_BOUNDS.max_bytes
ENSEMBLE_MAX_CELLS: Final = 5_000
ENSEMBLE_MAX_CHUNKS: Final = 1_000
ENSEMBLE_MAX_HORIZON_DAYS: Final = 16
ENSEMBLE_MAX_QUANTILE_LEVELS: Final = 11
ENSEMBLE_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
ENSEMBLE_RAW_CACHE_SCHEMA_VERSION: Literal[1] = 1
ENSEMBLE_STAGED_DOCUMENT_SCHEMA_VERSION: Literal[1] = 1

HOURS_PER_DAY: Final = 24

# The quantile level every receipt must declare; `agri.forecast_receipt` CHECKs it in SQL and this
# lane must not build a receipt it knows the database would reject.
REQUIRED_QUANTILE_LEVEL: Final = 0.5

# A series must be this complete across its declared members before it may carry a quantile at all.
MIN_COMPLETE_STEP_FRACTION: Final = 0.9

SERIES_FAILURE_INSUFFICIENT_MEMBER_COVERAGE: Final = "insufficient_member_coverage"

# Retrieval, caching, checksums and checkpoint state are the shared scaffold's, not this lane's:
# `ENSEMBLE_LANE.label` is what prefixes every message they raise. See execution/AGENTS.md.
ENSEMBLE_LANE: Final = OpenMeteoLane(
    label="ensemble forecast",
    cache_directory_name="ensemble-forecast",
    endpoint=OPEN_METEO_ENSEMBLE_ENDPOINT,
)

# The warehouse cannot accept these receipts yet; see models/AGENTS.md on ensemble quantile carriage.
ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE: Final = "blocked_forecast_method_check"


class EnsembleForecastSignal(NamedTuple):
    """One hourly variable's warehouse naming, units, and inclusive physical acceptance range."""

    signal_name: str
    original_unit: str
    metric_unit: str
    minimum: float
    maximum: float


# Open-Meteo ensemble hourly variable -> warehouse signal, units, and the range a value must fall
# inside. The bounds are the only thing standing between a provider sentinel and a wall of accepted
# member values that would silently move every derived quantile.
ENSEMBLE_FORECAST_SIGNAL_SPECIFICATIONS: Final[dict[str, EnsembleForecastSignal]] = {
    "precipitation": EnsembleForecastSignal("precipitation_total", "mm", "mm", 0.0, 2_000.0),
    "relative_humidity_2m": EnsembleForecastSignal("relative_humidity_2m", "%", "%", 0.0, 100.0),
    "shortwave_radiation": EnsembleForecastSignal("shortwave_radiation", "W/m^2", "W/m^2", 0.0, 1_500.0),
    "temperature_2m": EnsembleForecastSignal("air_temperature_2m", "C", "C", -100.0, 70.0),
    "wind_speed_10m": EnsembleForecastSignal("wind_speed_10m", "km/h", "km/h", 0.0, 500.0),
}

ENSEMBLE_FORECAST_PARAMETERS: Final = tuple(sorted(ENSEMBLE_FORECAST_SIGNAL_SPECIFICATIONS))


class EnsembleProduct(NamedTuple):
    """One reviewed ensemble model and everything the choice of model itself decides about a plan."""

    model: str
    schema_version: str
    native_grid_name: str
    native_grid_degrees: float
    native_grid_resolution_m: int
    support_key: str
    member_count: int
    supported_parameters: tuple[str, ...]


# The model is not an independent axis: it drags the native lattice, the support key, the document
# schema AND the member count with it, so a plan names a product and inherits the rest. The member
# count is the denominator of every quantile, which is why the parser demands exactly that many
# member series rather than summarizing whatever arrived.
ENSEMBLE_PRODUCTS: Final[dict[str, EnsembleProduct]] = {
    "ecmwf_ifs04": EnsembleProduct(
        "ecmwf_ifs04",
        "open-meteo-ensemble-ecmwf_ifs04-hourly-v1",
        "ecmwf-ifs04-0.4-degree",
        0.4,
        44_000,
        "ecmwf-ifs04-0p4deg",
        51,
        ENSEMBLE_FORECAST_PARAMETERS,
    ),
    "gem_global": EnsembleProduct(
        "gem_global",
        "open-meteo-ensemble-gem_global-hourly-v1",
        "gem-global-0.5-degree",
        0.5,
        55_000,
        "gem-global-0p5deg",
        21,
        ENSEMBLE_FORECAST_PARAMETERS,
    ),
    "gfs025": EnsembleProduct(
        "gfs025",
        "open-meteo-ensemble-gfs025-hourly-v1",
        "gfs-ensemble-0.25-degree",
        0.25,
        27_000,
        "gfs-ensemble-0p25deg",
        31,
        ENSEMBLE_FORECAST_PARAMETERS,
    ),
}


@dataclass(frozen=True)
class EnsembleForecastChunk:
    """One bounded multi-location ensemble request derived from the reviewed plan's cell order."""

    key: str
    cells: tuple[AnalysisGridCell, ...]


@dataclass(frozen=True)
class EnsembleForecastCapture:
    """What is known about one retrieval before its content is normalized."""

    retrieved_at: datetime
    wire_payload_bytes: int
    wire_payload_checksum: str
    # The host this retrieval really answered from; the paid tier is a different host plus one key.
    request_base_url: OpenMeteoEnsembleBaseUrl = OPEN_METEO_ENSEMBLE_BASE_URL


class StagedForecastValue(ContractModel):
    """One staged `agri.forecast_value` row: the ensemble median plus its full quantile carriage."""

    valid_time: datetime
    horizon_step: int = Field(ge=1)
    point_value: float
    p10_value: float | None = None
    p50_value: float | None = None
    p90_value: float | None = None
    quantile_values: dict[str, float] = Field(min_length=1)
    # True when this step's hour had ALREADY PASSED at retrieval, so its members are analysis rather
    # than forecast. It defaults to False and is then checked against `data_available_at` by
    # `require_consistent_quantile_carriage`, so a writer that omits it fails at the checksum
    # boundary instead of publishing a hindcast hour as a forecast one.
    is_elapsed_at_retrieval: bool = False

    @field_validator("valid_time")
    @classmethod
    def require_aware_valid_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "valid_time")

    @field_validator("point_value", "p10_value", "p50_value", "p90_value")
    @classmethod
    def require_finite_band(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("staged forecast band values must be finite")
        return value

    @field_validator("quantile_values")
    @classmethod
    def require_finite_quantile_values(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(item) for item in value.values()):
            raise ValueError("staged quantile values must be finite")
        return value

    @model_validator(mode="after")
    def require_ordered_bands(self) -> StagedForecastValue:
        """Mirror `ck_forecast_value_ordered_bands`, which is pairwise null-tolerant."""
        if self.p10_value is not None and self.p50_value is not None and self.p10_value > self.p50_value:
            raise ValueError("staged forecast p10 exceeds p50")
        if self.p50_value is not None and self.p90_value is not None and self.p50_value > self.p90_value:
            raise ValueError("staged forecast p50 exceeds p90")
        return self


class StagedForecastReceipt(ContractModel):
    """One staged `agri.forecast_receipt` plus its values, carrying the series identity a writer needs."""

    series_key: str = Field(max_length=255, pattern=r"^[a-z0-9][A-Za-z0-9._:-]{1,254}$")
    cell_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$")
    source_parameter: str = Field(max_length=150)
    signal_name: str = Field(max_length=150)
    metric_unit: str = Field(max_length=64)
    support_key: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,62}$")
    member_count: int = Field(ge=2)
    issue_time: datetime
    # When this content actually became available: the retrieval instant, not a plan-declared one.
    # A plan may name any issue_date, so without this a horizon executed weeks later would be staged
    # as though it had been issued then -- a simulated cutoff written as an operational issue time.
    data_available_at: datetime
    valid_from: datetime
    valid_to: datetime
    expected_value_count: int = Field(ge=1)
    quantile_levels: list[float] = Field(min_length=1, max_length=ENSEMBLE_MAX_QUANTILE_LEVELS)
    quality_summary: dict[str, int] = Field(default_factory=dict)
    values: list[StagedForecastValue] = Field(min_length=1)

    @field_validator("issue_time", "data_available_at", "valid_from", "valid_to")
    @classmethod
    def require_aware_times(cls, value: datetime, info: Any) -> datetime:
        return require_aware_utc(value, info.field_name)

    @field_validator("quantile_levels")
    @classmethod
    def require_valid_quantile_levels(cls, value: list[float]) -> list[float]:
        return require_valid_quantile_levels(value)


class EnsembleForecastPlan(ContractModel):
    """Reviewed Open-Meteo Ensemble forecast over an existing analysis lattice, summarized to quantiles."""

    schema_version: str = Field(pattern=r"^open-meteo-ensemble-[a-z0-9_]{1,40}-hourly-v[0-9]{1,3}$")
    source: SourceDefinition
    model: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    issue_date: date
    horizon_days: int = Field(ge=1, le=ENSEMBLE_MAX_HORIZON_DAYS)
    cell_selection: Literal["nearest"] = OPEN_METEO_ENSEMBLE_CELL_SELECTION
    time_zone: Literal["GMT"] = OPEN_METEO_ENSEMBLE_TIME_ZONE
    grid_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,98}$")
    grid_resolution_m: int = Field(gt=0)
    native_grid_name: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,98}$")
    native_grid_degrees: float = Field(gt=0, le=1)
    native_grid_resolution_m: int = Field(gt=0)
    support_key: str = Field(pattern=r"^[a-z0-9][a-z0-9.:_-]{1,62}$")
    member_count: int = Field(ge=2, le=99)
    cells: list[AnalysisGridCell] = Field(min_length=1, max_length=ENSEMBLE_MAX_CELLS)
    # One member series per variable per cell, so a chunk is bounded far tighter than the 200-location
    # transport ceiling; see execution/AGENTS.md on the ensemble lane's response budget.
    chunk_cell_count: int = Field(ge=1, le=25)
    parameters: list[str] = Field(min_length=1, max_length=len(ENSEMBLE_FORECAST_SIGNAL_SPECIFICATIONS))
    quantile_levels: list[float] = Field(min_length=1, max_length=ENSEMBLE_MAX_QUANTILE_LEVELS)
    transform_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("cells")
    @classmethod
    def require_sorted_unique_cells(cls, value: list[AnalysisGridCell]) -> list[AnalysisGridCell]:
        keys = [cell.cell_key for cell in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("ensemble forecast cells must be sorted and unique by cell_key")
        if len({(cell.latitude, cell.longitude) for cell in value}) != len(value):
            raise ValueError("ensemble forecast cells must not repeat a requested coordinate")
        return value

    @field_validator("parameters")
    @classmethod
    def require_supported_sorted_parameters(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("ensemble forecast parameters must be sorted and unique")
        unsupported = sorted(set(value).difference(ENSEMBLE_FORECAST_SIGNAL_SPECIFICATIONS))
        if unsupported:
            raise ValueError(f"unsupported ensemble forecast parameter(s): {', '.join(unsupported)}")
        return value

    @field_validator("quantile_levels")
    @classmethod
    def require_valid_quantile_levels(cls, value: list[float]) -> list[float]:
        return require_valid_quantile_levels(value)

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_release_set_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "release_set_as_of")

    @model_validator(mode="after")
    def require_governed_lattice(self) -> EnsembleForecastPlan:
        if self.source.key != ENSEMBLE_SOURCE_KEY:
            raise ValueError(f"ensemble forecast plans require source.key='{ENSEMBLE_SOURCE_KEY}'")
        product = ENSEMBLE_PRODUCTS.get(self.model)
        if product is None:
            raise ValueError(f"ensemble forecast model must be one of: {', '.join(sorted(ENSEMBLE_PRODUCTS))}")
        inherited = {
            "schema_version": product.schema_version,
            "native_grid_name": product.native_grid_name,
            "native_grid_degrees": product.native_grid_degrees,
            "native_grid_resolution_m": product.native_grid_resolution_m,
            "support_key": product.support_key,
            "member_count": product.member_count,
        }
        disagreeing = sorted(field for field, value in inherited.items() if getattr(self, field) != value)
        if disagreeing:
            raise ValueError(
                f"ensemble forecast plan restates its product incorrectly for model '{self.model}': "
                f"{', '.join(disagreeing)}"
            )
        unserved = sorted(set(self.parameters).difference(product.supported_parameters))
        if unserved:
            raise ValueError(f"ensemble model '{self.model}' does not serve parameter(s): {', '.join(unserved)}")
        nearest_points = {nearest_native_grid_point(cell, product.native_grid_degrees) for cell in self.cells}
        if len(nearest_points) != len(self.cells):
            raise ValueError("ensemble forecast cells must not share a native grid point")
        if len(self.chunks) > ENSEMBLE_MAX_CHUNKS:
            raise ValueError("ensemble forecast plan exceeds the reviewed chunk ceiling")
        return self

    @cached_property
    def product(self) -> EnsembleProduct:
        """Return the reviewed product bundle this plan's model selects."""
        return ENSEMBLE_PRODUCTS[self.model]

    @cached_property
    def start_date(self) -> date:
        """Return the first forecast day; the issue day itself, so the horizon is reproducible."""
        return self.issue_date

    @cached_property
    def end_date(self) -> date:
        """Return the inclusive last forecast day the reviewed horizon covers."""
        return self.issue_date + timedelta(days=self.horizon_days - 1)

    @cached_property
    def issue_time(self) -> datetime:
        """Return the instant the reviewed horizon is anchored at."""
        return datetime.combine(self.issue_date, time.min, tzinfo=UTC)

    @cached_property
    def step_count(self) -> int:
        """Return the hourly step count one series must account for."""
        return self.horizon_days * HOURS_PER_DAY

    @cached_property
    def valid_to(self) -> datetime:
        """Return the exclusive end of the horizon; strictly after `issue_time` for the receipt CHECK."""
        return self.issue_time + timedelta(hours=self.step_count)

    @cached_property
    def chunks(self) -> tuple[EnsembleForecastChunk, ...]:
        """Cut the sorted cell list into stable request chunks anchored at the first cell."""
        size = self.chunk_cell_count
        return tuple(
            EnsembleForecastChunk(key=f"cells-{index // size:04d}", cells=tuple(self.cells[index : index + size]))
            for index in range(0, len(self.cells), size)
        )

    @cached_property
    def plan_checksum(self) -> str:
        """Fingerprint every governed input controlling this forecast, once per validated instance."""
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


class EnsembleForecastReceipt(ContractModel):
    """One fully validated, cache-backed ensemble chunk receipt."""

    chunk_key: str = Field(pattern=r"^cells-\d{4}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=ENSEMBLE_MAX_RESPONSE_BYTES)
    cell_count: int = Field(ge=1)
    staged_receipt_count: int = Field(ge=0)
    staged_value_count: int = Field(ge=0)
    failed_series_count: int = Field(ge=0)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "retrieved_at")


class EnsembleForecastRawCacheReceipt(ContractModel):
    """Checksum-bound metadata for one reusable ensemble chunk download."""

    schema_version: Literal[1] = ENSEMBLE_RAW_CACHE_SCHEMA_VERSION
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_key: str = Field(pattern=r"^cells-\d{4}$")
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=ENSEMBLE_MAX_RESPONSE_BYTES)
    wire_payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    wire_payload_bytes: int = Field(ge=1, le=ENSEMBLE_MAX_RESPONSE_BYTES)
    retrieved_at: datetime
    request_base_url: OpenMeteoEnsembleBaseUrl = OPEN_METEO_ENSEMBLE_BASE_URL

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "retrieved_at")


class EnsembleForecastCheckpoint(ContractModel):
    """Durable resumable state for the complete chunked ensemble forecast plan."""

    schema_version: Literal[1] = ENSEMBLE_CHECKPOINT_SCHEMA_VERSION
    state: Literal["initialized", "running", "validated", "blocked"]
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipts: list[EnsembleForecastReceipt] = Field(default_factory=list, max_length=ENSEMBLE_MAX_CHUNKS)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("updated_at")
    @classmethod
    def require_aware_update_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, "updated_at")

    @field_validator("receipts")
    @classmethod
    def require_sorted_unique_receipts(cls, value: list[EnsembleForecastReceipt]) -> list[EnsembleForecastReceipt]:
        keys = [receipt.chunk_key for receipt in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("ensemble forecast receipts must be sorted and unique by chunk_key")
        return value


@dataclass(frozen=True)
class EnsembleForecastSeriesFailure:
    """One reviewed cell/variable series the provider published but could not be summarized."""

    cell_key: str
    source_parameter: str
    signal_name: str
    expected_step_count: int
    complete_step_count: int
    reason: str


@dataclass(frozen=True)
class EnsembleForecastChunkResult:
    """One validated canonical ensemble document plus the receipts it stages and the series it refused."""

    chunk_key: str
    retrieved_at: datetime
    payload: bytes
    payload_checksum: str
    wire_payload_bytes: int
    wire_payload_checksum: str
    request_base_url: OpenMeteoEnsembleBaseUrl
    receipts: tuple[StagedForecastReceipt, ...]
    failures: tuple[EnsembleForecastSeriesFailure, ...]
    grid_points: tuple[tuple[str, float, float], ...]


def format_quantile_level_key(level: float) -> str:
    """Render one quantile level as the single canonical `quantile_values` object key."""
    if not 0.0 <= level <= 1.0:
        raise ValueError("a quantile level must lie in [0, 1]")
    return format_javascript_number(level)


def require_valid_quantile_levels(levels: list[float]) -> list[float]:
    """Mirror `agri.forecast_quantiles_valid` plus the `0.5 = ANY(...)` receipt CHECK, in sorted order."""
    if any(not math.isfinite(level) for level in levels):
        raise ValueError("quantile levels must be finite")
    if any(level < 0.0 or level > 1.0 for level in levels):
        raise ValueError("quantile levels must lie in [0, 1]")
    if len(set(levels)) != len(levels):
        raise ValueError("quantile levels must be distinct")
    if levels != sorted(levels):
        raise ValueError("quantile levels must be sorted")
    if REQUIRED_QUANTILE_LEVEL not in levels:
        raise ValueError("quantile levels must include the median level 0.5")
    if len({format_quantile_level_key(level) for level in levels}) != len(levels):
        raise ValueError("quantile levels must render to distinct canonical keys")
    return levels


def empirical_quantile(sorted_values: Sequence[float], level: float) -> float:
    """Interpolate between order statistics with the linear (R type-7) rule, monotone in `level`."""
    count = len(sorted_values)
    if count == 0:
        raise ValueError("an empirical quantile requires at least one member value")
    if not 0.0 <= level <= 1.0:
        raise ValueError("a quantile level must lie in [0, 1]")
    position = (count - 1) * level
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    # The fused form stays monotone in `weight` under rounding; the convex-combination form does not.
    lower = sorted_values[lower_index]
    return lower + (sorted_values[upper_index] - lower) * (position - lower_index)


def summarize_ensemble_members(member_values: Sequence[float], levels: Sequence[float]) -> dict[str, float]:
    """Reduce one step's member values to the canonical quantile object the receipt carries."""
    ordered = sorted(member_values)
    summarized: dict[str, float] = {}
    previous: float | None = None
    for level in levels:
        value = empirical_quantile(ordered, level)
        if previous is not None and value < previous:
            raise ValueError("derived ensemble quantiles are not non-decreasing in level")
        summarized[format_quantile_level_key(level)] = value
        previous = value
    return summarized


def require_consistent_quantile_carriage(receipt: StagedForecastReceipt) -> None:
    """Enforce every quantile invariant the database cannot: key/level agreement, and the median mirror.

    `forecast_value.quantile_values` is JSONB with no CHECK, so nothing downstream would catch a
    receipt whose declared levels and stored keys disagree. See models/AGENTS.md.
    """
    require_valid_quantile_levels(receipt.quantile_levels)
    expected_keys = {format_quantile_level_key(level) for level in receipt.quantile_levels}
    median_key = format_quantile_level_key(REQUIRED_QUANTILE_LEVEL)
    if receipt.valid_to <= receipt.valid_from:
        raise ValueError("a staged forecast receipt must cover a strictly positive window")
    if receipt.data_available_at < receipt.issue_time:
        raise ValueError("a staged forecast receipt claims an issue time later than its data became available")
    if len(receipt.values) > receipt.expected_value_count:
        raise ValueError("a staged forecast receipt carries more values than its declared horizon")
    steps = [value.horizon_step for value in receipt.values]
    if len(set(steps)) != len(steps) or any(step > receipt.expected_value_count for step in steps):
        raise ValueError("staged forecast horizon steps must be unique and inside the declared horizon")
    times = [value.valid_time for value in receipt.values]
    if len(set(times)) != len(times):
        raise ValueError("staged forecast valid times must be unique within a receipt")
    for value in receipt.values:
        if set(value.quantile_values) != expected_keys:
            raise ValueError("staged quantile value keys do not match the receipt's declared quantile levels")
        if value.valid_time < receipt.valid_from or value.valid_time >= receipt.valid_to:
            raise ValueError("a staged forecast value falls outside its receipt window")
        if value.point_value != value.quantile_values[median_key]:
            raise ValueError("a staged forecast point value is not the declared ensemble median")
        if value.is_elapsed_at_retrieval != (value.valid_time < receipt.data_available_at):
            raise ValueError("a staged forecast value misstates whether its hour had elapsed at retrieval")
        _require_matching_band(value.p10_value, value.quantile_values, 0.1, expected_keys)
        _require_matching_band(value.p50_value, value.quantile_values, REQUIRED_QUANTILE_LEVEL, expected_keys)
        _require_matching_band(value.p90_value, value.quantile_values, 0.9, expected_keys)


def staged_forecast_receipt_checksum(receipt: StagedForecastReceipt) -> str:
    """Digest one staged receipt so a later writer can finalize it without recomputing its content."""
    require_consistent_quantile_carriage(receipt)
    return hashlib.sha256(canonical_json_bytes(receipt.model_dump(mode="json"))).hexdigest()


def ensemble_forecast_staged_document(
    plan: EnsembleForecastPlan,
    receipts: Sequence[StagedForecastReceipt],
) -> dict[str, object]:
    """Assemble the exact `agri.forecast_receipt` / `agri.forecast_value` payload this lane would insert."""
    ordered = sorted(receipts, key=lambda receipt: receipt.series_key)
    keys = [receipt.series_key for receipt in ordered]
    if len(set(keys)) != len(keys):
        raise ValueError("a staged ensemble document cannot carry one series key twice")
    return {
        "schema_version": ENSEMBLE_STAGED_DOCUMENT_SCHEMA_VERSION,
        "plan_checksum": plan.plan_checksum,
        "transform_version": plan.transform_version,
        "model": plan.model,
        "support_key": plan.support_key,
        "warehouse_persistence": ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE,
        "receipts": [
            {
                **receipt.model_dump(mode="json"),
                "receipt_checksum": staged_forecast_receipt_checksum(receipt),
            }
            for receipt in ordered
        ],
    }


def write_ensemble_forecast_staged_document(path: Path, document: dict[str, object]) -> str:
    """Atomically write the staged receipt document and return the digest of exactly what was written."""
    payload = canonical_json_bytes(document)
    atomic_write(path, payload)
    return hashlib.sha256(payload).hexdigest()


def ensemble_forecast_chunk_url(
    plan: EnsembleForecastPlan,
    chunk: EnsembleForecastChunk,
    *,
    base_url: str | None = None,
) -> str:
    """Return the credential-free request one chunk is answered by, so a release records a reproducible query."""
    _plan_chunk(plan, chunk.key)
    return ensemble_hourly_url(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        plan.start_date,
        plan.end_date,
        plan.model,
        base_url=base_url,
    )


def ensemble_forecast_plan_checksum(plan: EnsembleForecastPlan) -> str:
    """Fingerprint every governed input controlling this forecast; memoized on the validated plan."""
    return plan.plan_checksum


def ensemble_forecast_checkpoint_path(root: Path, plan: EnsembleForecastPlan) -> Path:
    """Return a plan-bound durable checkpoint file path."""
    return lane_checkpoint_path(root, ENSEMBLE_LANE, ensemble_forecast_plan_checksum(plan))


def ensemble_forecast_staged_document_path(root: Path, plan: EnsembleForecastPlan) -> Path:
    """Return the plan-bound location of the staged receipt document."""
    checksum = ensemble_forecast_plan_checksum(plan)
    return root / ENSEMBLE_LANE.cache_directory_name / checksum / "staged-receipts.json"


def ensemble_forecast_raw_cache_paths(
    root: Path,
    plan: EnsembleForecastPlan,
    chunk: EnsembleForecastChunk,
) -> tuple[Path, Path]:
    """Return canonical-document and receipt locations for one chunk beneath the local run root."""
    _plan_chunk(plan, chunk.key)
    return lane_raw_cache_paths(root, ENSEMBLE_LANE, ensemble_forecast_plan_checksum(plan), chunk.key)


def initialize_ensemble_forecast_checkpoint(
    plan: EnsembleForecastPlan,
    *,
    updated_at: datetime | None = None,
) -> EnsembleForecastCheckpoint:
    """Create an empty checkpoint without opening the network or PostgreSQL."""
    return EnsembleForecastCheckpoint(
        state="initialized",
        plan_checksum=ensemble_forecast_plan_checksum(plan),
        updated_at=require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def load_ensemble_forecast_checkpoint(path: Path) -> EnsembleForecastCheckpoint:
    """Read a local ensemble checkpoint without requesting a provider payload."""
    return EnsembleForecastCheckpoint.model_validate_json(path.read_bytes())


def write_ensemble_forecast_checkpoint(path: Path, checkpoint: EnsembleForecastCheckpoint) -> None:
    """Atomically update credential-free ensemble checkpoint metadata."""
    atomic_write(path, canonical_json_bytes(checkpoint.model_dump(mode="json")))


def rederive_ensemble_forecast_checkpoint_state(
    plan: EnsembleForecastPlan,
    checkpoint: EnsembleForecastCheckpoint,
) -> EnsembleForecastCheckpoint:
    """Recompute `state` from receipt completeness so a recorded `blocked` cannot outlive its cause.

    `reason` is preserved: it is the evidence of the last stop, and only `state` gates a resume.
    """
    if checkpoint.plan_checksum != ensemble_forecast_plan_checksum(plan):
        raise ValueError("ensemble forecast checkpoint does not bind the reviewed plan")
    derived = derived_checkpoint_state(
        {receipt.chunk_key for receipt in checkpoint.receipts},
        [chunk.key for chunk in plan.chunks],
    )
    if derived == checkpoint.state:
        return checkpoint
    return checkpoint.model_copy(update={"state": derived})


def record_ensemble_forecast_result(
    plan: EnsembleForecastPlan,
    checkpoint: EnsembleForecastCheckpoint,
    result: EnsembleForecastChunkResult,
    *,
    updated_at: datetime | None = None,
) -> EnsembleForecastCheckpoint:
    """Advance a chunk receipt only after every requested cell and variable is accounted for."""
    if checkpoint.plan_checksum != ensemble_forecast_plan_checksum(plan):
        raise ValueError("ensemble forecast checkpoint does not bind the reviewed plan")
    chunk = _plan_chunk(plan, result.chunk_key)
    require_accounted_ensemble_forecast_result(plan, result)
    receipt = EnsembleForecastReceipt(
        chunk_key=result.chunk_key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        cell_count=len(chunk.cells),
        staged_receipt_count=len(result.receipts),
        staged_value_count=sum(len(item.values) for item in result.receipts),
        failed_series_count=len(result.failures),
        retrieved_at=result.retrieved_at,
    )
    receipts = merged_chunk_receipts(ENSEMBLE_LANE, checkpoint.receipts, receipt)
    complete = [item.key for item in plan.chunks] == [item.chunk_key for item in receipts]
    return checkpoint.model_copy(
        update={
            "state": "validated" if complete else "running",
            "receipts": receipts,
            "updated_at": require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
            "reason": None,
        }
    )


def ensemble_forecast_release_manifest(
    plan: EnsembleForecastPlan,
    checkpoint: EnsembleForecastCheckpoint,
) -> str:
    """Hash the complete ordered chunk receipt set a release must pin."""
    return lane_release_manifest(
        ENSEMBLE_LANE,
        plan_checksum=ensemble_forecast_plan_checksum(plan),
        transform_version=plan.transform_version,
        checkpoint_plan_checksum=checkpoint.plan_checksum,
        checkpoint_state=checkpoint.state,
        expected_chunk_keys=[chunk.key for chunk in plan.chunks],
        receipts=checkpoint.receipts,
    )


def cache_ensemble_forecast_result(
    root: Path,
    plan: EnsembleForecastPlan,
    result: EnsembleForecastChunkResult,
) -> EnsembleForecastRawCacheReceipt:
    """Persist one accounted-for canonical chunk document before any warehouse transaction begins."""
    chunk = _plan_chunk(plan, result.chunk_key)
    require_accounted_ensemble_forecast_result(plan, result)
    payload_path, receipt_path = ensemble_forecast_raw_cache_paths(root, plan, chunk)
    receipt = EnsembleForecastRawCacheReceipt(
        plan_checksum=ensemble_forecast_plan_checksum(plan),
        chunk_key=chunk.key,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        wire_payload_checksum=result.wire_payload_checksum,
        wire_payload_bytes=result.wire_payload_bytes,
        retrieved_at=result.retrieved_at,
        request_base_url=OPEN_METEO_ENSEMBLE_ENDPOINT.require_base_url(result.request_base_url),
    )
    if payload_path.exists() or receipt_path.exists():
        cached = load_cached_ensemble_forecast_result(root, plan, chunk)
        if cached is None:
            raise ValueError("ensemble forecast raw cache unexpectedly has no reusable source document")
        if cached.payload_checksum != result.payload_checksum:
            raise ValueError("ensemble forecast raw cache already binds this chunk to different source content")
        return EnsembleForecastRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    write_raw_cache_pair(
        payload_path,
        receipt_path,
        result.payload,
        canonical_json_bytes(receipt.model_dump(mode="json")),
    )
    return receipt


def load_cached_ensemble_forecast_result(
    root: Path,
    plan: EnsembleForecastPlan,
    chunk: EnsembleForecastChunk,
) -> EnsembleForecastChunkResult | None:
    """Re-parse one validated local chunk document, never contacting the provider."""
    payload_path, receipt_path = ensemble_forecast_raw_cache_paths(root, plan, chunk)
    if not require_complete_raw_cache_pair(ENSEMBLE_LANE, payload_path, receipt_path):
        return None
    receipt = EnsembleForecastRawCacheReceipt.model_validate_json(receipt_path.read_bytes())
    if receipt.plan_checksum != ensemble_forecast_plan_checksum(plan) or receipt.chunk_key != chunk.key:
        raise ValueError("ensemble forecast raw cache receipt does not bind this reviewed plan and chunk")
    payload = verified_cached_payload(
        ENSEMBLE_LANE,
        payload_path,
        expected_bytes=receipt.payload_bytes,
        expected_checksum=receipt.payload_checksum,
    )
    result = parse_ensemble_forecast_payload(
        plan,
        chunk,
        payload,
        EnsembleForecastCapture(
            retrieved_at=receipt.retrieved_at,
            wire_payload_bytes=receipt.wire_payload_bytes,
            wire_payload_checksum=receipt.wire_payload_checksum,
            request_base_url=receipt.request_base_url,
        ),
    )
    require_accounted_ensemble_forecast_result(plan, result)
    return result


class EnsembleForecastFetchError(OpenMeteoLaneFetchError):
    """Raised when an ensemble chunk stopped for good; carries the chunk so a resume is unambiguous."""

    def __init__(self, chunk_key: str, cause: UpstreamError | None, attempts: int) -> None:
        """Name the chunk, how many attempts it really made, and the provider condition that stopped it."""
        super().__init__(ENSEMBLE_LANE.label, chunk_key, cause, attempts)


async def fetch_ensemble_forecast_chunk(
    plan: EnsembleForecastPlan,
    chunk: EnsembleForecastChunk,
    *,
    client: httpx.AsyncClient,
    retrieved_at: datetime | None = None,
    sleep: object = None,
) -> EnsembleForecastChunkResult:
    """Fetch one chunk with bounded retries; an exhausted quota is raised, never swallowed."""
    _plan_chunk(plan, chunk.key)
    # The credential lives only in `request.request_url`; only `request.base_url` is ever recorded.
    request = ensemble_hourly_request(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        plan.start_date,
        plan.end_date,
        plan.model,
    )
    capture = await fetch_lane_capture(
        ENSEMBLE_LANE,
        chunk.key,
        request,
        client=client,
        fetch_text=fetch_ensemble_hourly,
        error_factory=EnsembleForecastFetchError,
        retrieved_at=retrieved_at,
        sleep=sleep,
    )
    return parse_ensemble_forecast_payload(
        plan,
        chunk,
        capture.canonical_payload,
        EnsembleForecastCapture(
            retrieved_at=capture.retrieved_at,
            wire_payload_bytes=capture.wire_payload_bytes,
            wire_payload_checksum=capture.wire_payload_checksum,
            request_base_url=OPEN_METEO_ENSEMBLE_ENDPOINT.require_base_url(capture.request_base_url),
        ),
    )


async def run_ensemble_forecast_chunks(
    plan: EnsembleForecastPlan,
    chunks: Sequence[EnsembleForecastChunk],
    *,
    concurrency: int = DEFAULT_CHUNK_CONCURRENCY,
    client: httpx.AsyncClient | None = None,
) -> list[EnsembleForecastChunkResult | BaseException]:
    """Fetch several chunks under a bounded semaphore, preserving each chunk's own failure."""

    async def one(active: httpx.AsyncClient, chunk: EnsembleForecastChunk) -> EnsembleForecastChunkResult:
        return await fetch_ensemble_forecast_chunk(plan, chunk, client=active)

    return await run_lane_chunks(ENSEMBLE_LANE, chunks, one, concurrency=concurrency, client=client)


def parse_ensemble_forecast_payload(
    plan: EnsembleForecastPlan,
    chunk: EnsembleForecastChunk,
    payload: bytes,
    capture: EnsembleForecastCapture,
) -> EnsembleForecastChunkResult:
    """Validate one canonical ensemble document and reduce every reviewed cell/variable to quantiles."""
    _plan_chunk(plan, chunk.key)
    timestamp = require_aware_utc(capture.retrieved_at, "retrieved_at")
    require_issue_time_not_ahead_of_retrieval(plan, timestamp)
    if not payload or len(payload) > ENSEMBLE_MAX_RESPONSE_BYTES:
        raise ValueError("ensemble forecast response exceeds the reviewed byte boundary")
    locations = ordered_locations(ENSEMBLE_LANE, payload, len(chunk.cells))
    expected_times = _expected_step_times(plan)
    max_offset = max_grid_offset_degrees(plan.product.native_grid_degrees)

    receipts: list[StagedForecastReceipt] = []
    failures: list[EnsembleForecastSeriesFailure] = []
    grid_points: list[tuple[str, float, float]] = []
    seen_grid_points: set[tuple[float, float]] = set()
    for cell, location in zip(chunk.cells, locations, strict=True):
        latitude, longitude = validated_grid_point(ENSEMBLE_LANE, cell, location, max_offset)
        if (latitude, longitude) in seen_grid_points:
            raise ValueError("ensemble forecast returned one native grid point for two reviewed cells")
        seen_grid_points.add((latitude, longitude))
        grid_points.append((cell.cell_key, latitude, longitude))
        hourly = _ensemble_hourly_block(location)
        _require_expected_time_axis(hourly, expected_times)
        for parameter in plan.parameters:
            specification = ENSEMBLE_FORECAST_SIGNAL_SPECIFICATIONS[parameter]
            members = _member_series(hourly, parameter, specification, plan.member_count, len(expected_times))
            staged = _stage_series(
                plan,
                cell,
                parameter,
                specification,
                members,
                expected_times,
                retrieved_at=timestamp,
            )
            if isinstance(staged, EnsembleForecastSeriesFailure):
                failures.append(staged)
            else:
                receipts.append(staged)
    result = EnsembleForecastChunkResult(
        chunk_key=chunk.key,
        retrieved_at=timestamp,
        payload=payload,
        payload_checksum=hashlib.sha256(payload).hexdigest(),
        wire_payload_bytes=capture.wire_payload_bytes,
        wire_payload_checksum=capture.wire_payload_checksum,
        request_base_url=OPEN_METEO_ENSEMBLE_ENDPOINT.require_base_url(capture.request_base_url),
        receipts=tuple(receipts),
        failures=tuple(failures),
        grid_points=tuple(grid_points),
    )
    require_accounted_ensemble_forecast_result(plan, result)
    return result


def require_accounted_ensemble_forecast_result(
    plan: EnsembleForecastPlan,
    result: EnsembleForecastChunkResult,
) -> None:
    """Reject any chunk that cannot account for every requested cell and variable exactly once.

    Accounting, not completeness: a series too sparse to summarize is allowed, but it must arrive as
    one explicit failure rather than as a receipt with missing values or as silently absent.
    """
    chunk = _plan_chunk(plan, result.chunk_key)
    require_issue_time_not_ahead_of_retrieval(plan, result.retrieved_at)
    if len(result.payload) < 1 or len(result.payload) > ENSEMBLE_MAX_RESPONSE_BYTES:
        raise ValueError("ensemble forecast result payload violates the reviewed byte boundary")
    if hashlib.sha256(result.payload).hexdigest() != result.payload_checksum:
        raise ValueError("ensemble forecast result payload checksum does not match its content")
    expected_series = {(cell.cell_key, parameter) for cell in chunk.cells for parameter in plan.parameters}
    accounted = [(item.cell_key, item.source_parameter) for item in result.receipts]
    accounted.extend((item.cell_key, item.source_parameter) for item in result.failures)
    if sorted(accounted) != sorted(expected_series) or len(accounted) != len(expected_series):
        raise ValueError("ensemble forecast result does not account for every reviewed cell and variable")
    for receipt in result.receipts:
        if receipt.expected_value_count != plan.step_count:
            raise ValueError("a staged ensemble receipt does not declare the reviewed horizon step count")
        if receipt.member_count != plan.member_count:
            raise ValueError("a staged ensemble receipt does not declare the reviewed member count")
        if receipt.issue_time != plan.issue_time or receipt.valid_from != plan.issue_time:
            raise ValueError("a staged ensemble receipt is not anchored at the reviewed issue time")
        if receipt.data_available_at != result.retrieved_at:
            raise ValueError("a staged ensemble receipt does not record the retrieval that made its data available")
        if receipt.valid_to != plan.valid_to:
            raise ValueError("a staged ensemble receipt does not close at the reviewed horizon end")
        if receipt.quantile_levels != plan.quantile_levels:
            raise ValueError("a staged ensemble receipt does not declare the reviewed quantile levels")
        require_consistent_quantile_carriage(receipt)
    if len(result.grid_points) != len(chunk.cells):
        raise ValueError("ensemble forecast result does not record a native grid point for every reviewed cell")


def ensemble_forecast_series_key(plan: EnsembleForecastPlan, cell_key: str, signal_name: str) -> str:
    """Compose the stable `agri.forecast_series` key one reviewed cell/variable pair resolves to."""
    return ":".join((ENSEMBLE_SOURCE_KEY, plan.model, plan.support_key, cell_key, signal_name))


def require_issue_time_not_ahead_of_retrieval(plan: EnsembleForecastPlan, retrieved_at: datetime) -> None:
    """Refuse a plan whose declared issue time postdates the retrieval that answered it.

    `issue_date` is plan-declared and the provider always answers with its CURRENT model runs, so a
    plan naming a future issue date would stamp today's content as tomorrow's forecast. The reverse
    (an issue date in the past) is caught per step by the elapsed-hour flag rather than refused,
    because a same-day plan legitimately covers hours that elapsed between midnight and the fetch.
    """
    if plan.issue_time > require_aware_utc(retrieved_at, "retrieved_at"):
        raise ValueError("an ensemble forecast plan cannot declare an issue time later than its retrieval")


def _stage_series(  # noqa: PLR0913
    plan: EnsembleForecastPlan,
    cell: AnalysisGridCell,
    parameter: str,
    specification: EnsembleForecastSignal,
    members: Sequence[Sequence[float | None]],
    expected_times: Sequence[datetime],
    *,
    retrieved_at: datetime,
) -> StagedForecastReceipt | EnsembleForecastSeriesFailure:
    """Reduce one cell/variable's member matrix to a staged receipt, or refuse it as too sparse."""
    values: list[StagedForecastValue] = []
    for index, valid_time in enumerate(expected_times):
        step_values = [series[index] for series in members]
        if any(value is None for value in step_values):
            continue
        quantile_values = summarize_ensemble_members(
            [value for value in step_values if value is not None],
            plan.quantile_levels,
        )
        median = quantile_values[format_quantile_level_key(REQUIRED_QUANTILE_LEVEL)]
        values.append(
            StagedForecastValue(
                valid_time=valid_time,
                horizon_step=index + 1,
                point_value=median,
                p10_value=quantile_values.get(format_quantile_level_key(0.1)),
                p50_value=median,
                p90_value=quantile_values.get(format_quantile_level_key(0.9)),
                quantile_values=quantile_values,
                is_elapsed_at_retrieval=valid_time < retrieved_at,
            )
        )
    expected_step_count = len(expected_times)
    if len(values) < math.ceil(MIN_COMPLETE_STEP_FRACTION * expected_step_count):
        return EnsembleForecastSeriesFailure(
            cell_key=cell.cell_key,
            source_parameter=parameter,
            signal_name=specification.signal_name,
            expected_step_count=expected_step_count,
            complete_step_count=len(values),
            reason=SERIES_FAILURE_INSUFFICIENT_MEMBER_COVERAGE,
        )
    receipt = StagedForecastReceipt(
        series_key=ensemble_forecast_series_key(plan, cell.cell_key, specification.signal_name),
        cell_key=cell.cell_key,
        source_parameter=parameter,
        signal_name=specification.signal_name,
        metric_unit=specification.metric_unit,
        support_key=plan.support_key,
        member_count=plan.member_count,
        issue_time=plan.issue_time,
        data_available_at=retrieved_at,
        valid_from=plan.issue_time,
        valid_to=plan.valid_to,
        expected_value_count=expected_step_count,
        quantile_levels=list(plan.quantile_levels),
        quality_summary={
            "member_count": plan.member_count,
            "complete_step_count": len(values),
            "incomplete_step_count": expected_step_count - len(values),
            # Steps whose hour had already passed at retrieval: analysis carried on a forecast
            # receipt. A downstream writer must not score these as forecast skill.
            "elapsed_step_count": sum(1 for value in values if value.is_elapsed_at_retrieval),
        },
        values=values,
    )
    require_consistent_quantile_carriage(receipt)
    return receipt


def _require_matching_band(
    band: float | None,
    quantile_values: dict[str, float],
    level: float,
    expected_keys: set[str],
) -> None:
    """Refuse a p10/p90 mirror that disagrees with, or was invented beside, the quantile object."""
    key = format_quantile_level_key(level)
    if key in expected_keys:
        if band is None or band != quantile_values[key]:
            raise ValueError(f"a staged forecast band mirror disagrees with quantile level {key}")
    elif band is not None:
        raise ValueError(f"a staged forecast band mirror declares an undeclared quantile level {key}")


def _ensemble_hourly_block(location: dict[str, object]) -> dict[str, object]:
    hourly = location.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("ensemble forecast location is missing its hourly block")
    return hourly


def _expected_step_times(plan: EnsembleForecastPlan) -> tuple[datetime, ...]:
    """Derive the exact hourly instants the reviewed horizon covers, without timezone arithmetic."""
    return tuple(plan.issue_time + timedelta(hours=step) for step in range(plan.step_count))


def _require_expected_time_axis(hourly: dict[str, object], expected_times: Sequence[datetime]) -> None:
    """Compare the returned axis instant by instant; the block is pinned to GMT, so parsing is exact."""
    raw = hourly.get("time")
    if not isinstance(raw, list) or len(raw) != len(expected_times):
        raise ValueError("ensemble forecast hourly time axis does not match the reviewed horizon")
    for value, expected in zip(raw, expected_times, strict=True):
        if not isinstance(value, str):
            raise ValueError("ensemble forecast hourly time values must be ISO-8601 strings")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("ensemble forecast hourly time values must be ISO-8601 strings") from exc
        moment = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        if moment != expected:
            raise ValueError("ensemble forecast hourly time axis does not match the reviewed horizon")


def _member_series(
    hourly: dict[str, object],
    parameter: str,
    specification: EnsembleForecastSignal,
    member_count: int,
    step_count: int,
) -> tuple[tuple[float | None, ...], ...]:
    """Read exactly the declared member series; a missing or extra member fails the whole chunk.

    The member count is the quantile denominator, so a provider that adds or drops a member must not
    silently change every summary this lane derives.
    """
    expected_fields = [ensemble_member_field_name(parameter, number) for number in range(1, member_count + 1)]
    published = sorted(key for key in hourly if isinstance(key, str) and key.startswith(f"{parameter}_member"))
    if published != sorted(expected_fields):
        raise ValueError(
            f"ensemble forecast variable {parameter} published {len(published)} member series, "
            f"not the reviewed {member_count}"
        )
    return tuple(
        tuple(
            bounded_numeric_series(
                ENSEMBLE_LANE,
                hourly,
                field_name,
                minimum=specification.minimum,
                maximum=specification.maximum,
                expected_count=step_count,
                subject="member series",
            )
        )
        for field_name in expected_fields
    )


def _plan_chunk(plan: EnsembleForecastPlan, chunk_key: str) -> EnsembleForecastChunk:
    try:
        return next(chunk for chunk in plan.chunks if chunk.key == chunk_key)
    except StopIteration as exc:
        raise ValueError("ensemble forecast chunk is not part of the reviewed plan") from exc
