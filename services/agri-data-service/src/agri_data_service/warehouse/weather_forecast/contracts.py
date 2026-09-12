"""Immutable run and sampled forecast contracts; see AGENTS.md."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from agri_data_service.warehouse.weather_forecast.variables import VARIABLES, Unit, VariableName

MAX_SERIES_VALUES = 50_000
SafeToken = Annotated[str, Field(strict=True, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
Text = Annotated[str, Field(strict=True, min_length=1, max_length=2048, pattern=r"\S")]
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("forecast times must be explicit UTC instants")
    return value.astimezone(UTC)


UTCInstant = Annotated[datetime, BeforeValidator(_utc)]
ValueStatus = Literal[
    "available",
    "exact_absence",
    "outside_domain",
    "stale_run",
    "not_yet_generated",
    "upstream_unavailable",
    "missing",
]


class FrozenContract(BaseModel):
    """Reject extra fields and mutation of validated forecast contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class SpatialSupport(FrozenContract):
    """Declare represented support without inferring a field from samples."""

    kind: Literal["sampled_point", "native_grid", "derived_field"]
    represented_support: Literal["sample_coordinate"] = "sample_coordinate"
    source_resolution_m: Annotated[Finite, Field(gt=0)] | None = None
    resolution_evidence: Text | None = None

    @model_validator(mode="after")
    def admitted_shape(self) -> Self:
        if self.kind != "sampled_point":
            raise ValueError("native and derived fields require a separately admitted support contract")
        if (self.source_resolution_m is None) != (self.resolution_evidence is None):
            raise ValueError("source resolution and its evidence must be supplied together")
        return self


class ForecastRun(FrozenContract):
    """Bind one deterministic model run to separate source and publication times."""

    schema_version: Literal["weather-forecast/v1"] = "weather-forecast/v1"
    product_kind: Literal["forecast"] = "forecast"
    forecast_kind: Literal["deterministic"] = "deterministic"
    run_id: SafeToken
    product_id: SafeToken
    provider: SafeToken
    model: SafeToken
    model_version: Text | None = None
    model_init_at: UTCInstant
    provider_issued_at: UTCInstant | None
    fetched_at: UTCInstant
    admitted_at: UTCInstant
    published_at: UTCInstant
    licence: Text
    source_url: Annotated[str, Field(strict=True, max_length=2048, pattern=r"^https://\S+$")]
    source_payload_sha256: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
    support: SpatialSupport
    variables: Annotated[tuple[VariableName, ...], Field(min_length=1, max_length=len(VARIABLES))]

    @model_validator(mode="after")
    def temporal_identity(self) -> Self:
        if not self.model_init_at <= self.fetched_at <= self.admitted_at <= self.published_at:
            raise ValueError("run lifecycle must order initialization, fetch, admission and publication")
        if self.provider_issued_at is not None and not self.model_init_at <= self.provider_issued_at <= self.fetched_at:
            raise ValueError("provider issue must fall between initialization and fetch")
        if len(set(self.variables)) != len(self.variables):
            raise ValueError("run variables must be unique")
        return self


class ForecastValue(FrozenContract):
    """One value or explicit absence at a sample coordinate and valid instant."""

    run_id: SafeToken
    sample_id: SafeToken
    longitude: Annotated[Finite, Field(ge=-180, le=180)]
    latitude: Annotated[Finite, Field(ge=-90, le=90)]
    variable: VariableName
    unit: Unit
    valid_at: UTCInstant
    lead_seconds: Annotated[int, Field(strict=True, ge=0)]
    interval_start: UTCInstant | None = None
    interval_end: UTCInstant | None = None
    value: Finite | None
    status: ValueStatus

    @model_validator(mode="after")
    def value_contract(self) -> Self:
        definition = VARIABLES[self.variable]
        if self.unit != definition.unit:
            raise ValueError(f"{self.variable} requires unit {definition.unit}")
        if (self.status == "available") != (self.value is not None):
            raise ValueError("available requires a value; absence requires null")
        if self.value is not None:
            if definition.minimum is not None and self.value < definition.minimum:
                raise ValueError("value below variable support")
            if definition.maximum is not None and self.value > definition.maximum:
                raise ValueError("value above variable support")
            if self.variable == "weather_code" and not self.value.is_integer():
                raise ValueError("weather code must be integral")
        if (self.interval_start is None) != (self.interval_end is None):
            raise ValueError("interval bounds must be supplied together")
        if definition.requires_interval and self.interval_start is None:
            raise ValueError("accumulation or probability requires its interval")
        if (
            self.interval_start is not None
            and self.interval_end is not None
            and not self.interval_start < self.interval_end == self.valid_at
        ):
            raise ValueError("interval must be positive and end at valid time")
        return self


class ForecastSeries(FrozenContract):
    """A bounded immutable set of values pinned to one model run."""

    run: ForecastRun
    values: Annotated[tuple[ForecastValue, ...], Field(max_length=MAX_SERIES_VALUES)]

    @model_validator(mode="after")
    def pinned_run(self) -> Self:
        seen: set[tuple[str, str, datetime, datetime | None, datetime | None]] = set()
        coordinates: dict[str, tuple[float, float]] = {}
        for value in self.values:
            if value.run_id != self.run.run_id or value.variable not in self.run.variables:
                raise ValueError("series values must belong to the pinned run and its variable inventory")
            if (value.valid_at - self.run.model_init_at).total_seconds() != value.lead_seconds:
                raise ValueError("lead_seconds must equal valid time minus initialization")
            if (
                value.value is not None
                and value.interval_start is not None
                and value.interval_start < self.run.model_init_at
            ):
                raise ValueError("available forecast intervals cannot begin before model initialization")
            point = (value.longitude, value.latitude)
            if value.sample_id in coordinates and coordinates[value.sample_id] != point:
                raise ValueError("sample identity cannot change coordinates within a run")
            coordinates[value.sample_id] = point
            identity = (value.sample_id, value.variable, value.valid_at, value.interval_start, value.interval_end)
            if identity in seen:
                raise ValueError("duplicate forecast value identity")
            seen.add(identity)
        return self
