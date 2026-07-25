"""Pure, bounded contract for the Railway rolling-year hot projection."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, time, timedelta
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from agri_data_service.execution.contracts import SHA256_PATTERN, ContractModel, canonical_json_bytes

HOT_PROJECTION_SCHEMA_VERSION: Literal[1] = 1
HOT_PROJECTION_FORMAT: Literal["plantgeo-railway-hot-projection-v1"] = "plantgeo-railway-hot-projection-v1"
HOT_PROJECTION_TARGET_KEY: Literal["railway-hot-observations-v1"] = "railway-hot-observations-v1"
HOT_PROJECTION_WINDOW_DAYS = 365
MAX_HOT_PROJECTION_SOURCE_RECEIPTS = 1_000
MAX_HOT_PROJECTION_FORECAST_RECEIPTS = 10_000
MAX_HOT_PROJECTION_OBSERVATIONS = 25_000_000
MAX_HOT_PROJECTION_FORECASTS = 25_000_000
MAX_HOT_PROJECTION_FORECAST_HORIZON_DAYS = 365
SOURCE_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{1,98}$"
RECEIPT_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _require_utc_midnight(value: datetime, field_name: str) -> datetime:
    normalized = _require_aware_utc(value, field_name)
    if normalized.timetz().replace(tzinfo=None) != time.min:
        raise ValueError(f"{field_name} must be an exact UTC day boundary")
    return normalized


class HotProjectionWindow(ContractModel):
    """The exact half-open daily-observation interval selected for Railway."""

    observed_from: datetime
    observed_until: datetime

    @field_validator("observed_from", "observed_until")
    @classmethod
    def require_utc_day_boundary(cls, value: datetime, info: object) -> datetime:
        return _require_utc_midnight(value, str(getattr(info, "field_name", "window boundary")))

    @model_validator(mode="after")
    def require_exact_rolling_year(self) -> Self:
        if self.observed_from != self.observed_until - timedelta(days=HOT_PROJECTION_WINDOW_DAYS):
            raise ValueError("hot projection window must be exactly 365 UTC days and half-open")
        return self


def rolling_hot_projection_window(as_of_time: datetime) -> HotProjectionWindow:
    """Derive the one approved, half-open observation window from an UTC day boundary."""
    observed_until = _require_utc_midnight(as_of_time, "as_of_time")
    return HotProjectionWindow(
        observed_from=observed_until - timedelta(days=HOT_PROJECTION_WINDOW_DAYS),
        observed_until=observed_until,
    )


class HotProjectionSourceReceipt(ContractModel):
    """A source-local selection receipt without a database identifier or source payload."""

    source_key: str = Field(pattern=SOURCE_KEY_PATTERN)
    source_release_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    receipt_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    observed_from: datetime
    observed_until: datetime
    observation_count: int = Field(gt=0, le=MAX_HOT_PROJECTION_OBSERVATIONS)

    @field_validator("observed_from", "observed_until")
    @classmethod
    def require_utc_day_boundary(cls, value: datetime, info: object) -> datetime:
        return _require_utc_midnight(value, str(getattr(info, "field_name", "source boundary")))

    @model_validator(mode="after")
    def require_half_open_source_selection(self) -> Self:
        if self.observed_from >= self.observed_until:
            raise ValueError("source receipt must use a non-empty half-open observation interval")
        return self


class HotProjectionForecastReceipt(ContractModel):
    """One immutable forecast output receipt selected alongside rolling observations."""

    forecast_key: str = Field(pattern=RECEIPT_KEY_PATTERN)
    forecast_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    forecast_receipt_checksum: str = Field(pattern=SHA256_PATTERN)
    issued_at: datetime
    valid_from: datetime
    valid_until: datetime
    forecast_count: int = Field(gt=0, le=MAX_HOT_PROJECTION_FORECASTS)

    @field_validator("issued_at")
    @classmethod
    def require_aware_issued_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "issued_at")

    @field_validator("valid_from", "valid_until")
    @classmethod
    def require_utc_day_boundary(cls, value: datetime, info: object) -> datetime:
        return _require_utc_midnight(value, str(getattr(info, "field_name", "forecast boundary")))

    @model_validator(mode="after")
    def require_half_open_forecast_selection(self) -> Self:
        if self.valid_from >= self.valid_until:
            raise ValueError("forecast receipt must use a non-empty half-open valid-time interval")
        return self


class HotProjectionManifest(ContractModel):
    """Checksum-bound local projection input for a future Railway receiver."""

    schema_version: Literal[1] = HOT_PROJECTION_SCHEMA_VERSION
    format: Literal["plantgeo-railway-hot-projection-v1"] = HOT_PROJECTION_FORMAT
    target_key: Literal["railway-hot-observations-v1"] = HOT_PROJECTION_TARGET_KEY
    window: HotProjectionWindow
    source_receipts: list[HotProjectionSourceReceipt] = Field(
        min_length=1, max_length=MAX_HOT_PROJECTION_SOURCE_RECEIPTS
    )
    forecast_receipts: list[HotProjectionForecastReceipt] = Field(
        min_length=1, max_length=MAX_HOT_PROJECTION_FORECAST_RECEIPTS
    )
    total_observation_count: int = Field(gt=0, le=MAX_HOT_PROJECTION_OBSERVATIONS)
    total_forecast_count: int = Field(gt=0, le=MAX_HOT_PROJECTION_FORECASTS)
    manifest_checksum: str = Field(pattern=SHA256_PATTERN)

    @field_validator("source_receipts")
    @classmethod
    def require_sorted_unique_sources(cls, value: list[HotProjectionSourceReceipt]) -> list[HotProjectionSourceReceipt]:
        source_keys = [receipt.source_key for receipt in value]
        if source_keys != sorted(source_keys) or len(source_keys) != len(set(source_keys)):
            raise ValueError("source receipts must be sorted and unique by source_key")
        return value

    @field_validator("forecast_receipts")
    @classmethod
    def require_sorted_unique_forecasts(
        cls, value: list[HotProjectionForecastReceipt]
    ) -> list[HotProjectionForecastReceipt]:
        forecast_keys = [receipt.forecast_key for receipt in value]
        if forecast_keys != sorted(forecast_keys) or len(forecast_keys) != len(set(forecast_keys)):
            raise ValueError("forecast receipts must be sorted and unique by forecast_key")
        return value

    @model_validator(mode="after")
    def require_bound_complete_projection(self) -> Self:
        if sum(receipt.observation_count for receipt in self.source_receipts) != self.total_observation_count:
            raise ValueError("total_observation_count must equal source receipt counts")
        if sum(receipt.forecast_count for receipt in self.forecast_receipts) != self.total_forecast_count:
            raise ValueError("total_forecast_count must equal forecast receipt counts")
        if any(
            receipt.observed_from != self.window.observed_from or receipt.observed_until != self.window.observed_until
            for receipt in self.source_receipts
        ):
            raise ValueError("each source receipt must bind the exact rolling hot-projection window")
        forecast_horizon = self.window.observed_until + timedelta(days=MAX_HOT_PROJECTION_FORECAST_HORIZON_DAYS)
        if any(
            receipt.issued_at > self.window.observed_until
            or receipt.valid_from != self.window.observed_until
            or receipt.valid_until > forecast_horizon
            for receipt in self.forecast_receipts
        ):
            raise ValueError("forecasts must start at the projection boundary and stay within the bounded horizon")
        if self.manifest_checksum != hot_projection_manifest_checksum(self):
            raise ValueError("manifest_checksum does not match hot projection content")
        return self


def hot_projection_manifest_checksum(manifest: HotProjectionManifest) -> str:
    """Hash projection content excluding its self-referential checksum."""
    payload = manifest.model_dump(mode="json", exclude={"manifest_checksum"})
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_hot_projection_manifest(
    *,
    as_of_time: datetime,
    source_receipts: list[HotProjectionSourceReceipt],
    forecast_receipts: list[HotProjectionForecastReceipt],
) -> HotProjectionManifest:
    """Build the deterministic, local-only Railway hot-projection manifest."""
    window = rolling_hot_projection_window(as_of_time)
    sources = sorted(
        (HotProjectionSourceReceipt.model_validate(receipt.model_dump(mode="json")) for receipt in source_receipts),
        key=lambda receipt: receipt.source_key,
    )
    forecasts = sorted(
        (HotProjectionForecastReceipt.model_validate(receipt.model_dump(mode="json")) for receipt in forecast_receipts),
        key=lambda receipt: receipt.forecast_key,
    )
    provisional = {
        "schema_version": HOT_PROJECTION_SCHEMA_VERSION,
        "format": HOT_PROJECTION_FORMAT,
        "target_key": HOT_PROJECTION_TARGET_KEY,
        "window": window.model_dump(mode="json"),
        "source_receipts": [receipt.model_dump(mode="json") for receipt in sources],
        "forecast_receipts": [receipt.model_dump(mode="json") for receipt in forecasts],
        "total_observation_count": sum(receipt.observation_count for receipt in sources),
        "total_forecast_count": sum(receipt.forecast_count for receipt in forecasts),
    }
    checksum = hashlib.sha256(canonical_json_bytes(provisional)).hexdigest()
    return HotProjectionManifest.model_validate({**provisional, "manifest_checksum": checksum})


class HotProjectionPointer(ContractModel):
    """A pure snapshot of the Railway hot-projection publication pointer."""

    target_key: Literal["railway-hot-observations-v1"] = HOT_PROJECTION_TARGET_KEY
    generation: int = Field(ge=1)
    manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    observed_until: datetime

    @field_validator("observed_until")
    @classmethod
    def require_utc_day_boundary(cls, value: datetime) -> datetime:
        return _require_utc_midnight(value, "observed_until")


class HotProjectionPointerAdvance(ContractModel):
    """A compare-and-swap intent that a Railway adapter must apply atomically."""

    target_key: Literal["railway-hot-observations-v1"] = HOT_PROJECTION_TARGET_KEY
    expected_generation: int = Field(ge=0)
    expected_manifest_checksum: str | None = Field(default=None, pattern=SHA256_PATTERN)
    next_generation: int = Field(ge=1)
    next_manifest_checksum: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_compare_and_swap_shape(self) -> Self:
        if (self.expected_generation == 0) != (self.expected_manifest_checksum is None):
            raise ValueError("an empty pointer must use generation zero and no expected checksum")
        if self.next_generation != self.expected_generation + 1:
            raise ValueError("next_generation must advance the expected pointer by exactly one")
        return self


def prepare_hot_projection_pointer_advance(
    current: HotProjectionPointer | None, manifest: HotProjectionManifest
) -> HotProjectionPointerAdvance | None:
    """Create the pointer compare-and-swap intent, or return None for an idempotent replay."""
    verified_manifest = HotProjectionManifest.model_validate(manifest.model_dump(mode="json"))
    if current is None:
        return HotProjectionPointerAdvance(
            expected_generation=0,
            expected_manifest_checksum=None,
            next_generation=1,
            next_manifest_checksum=verified_manifest.manifest_checksum,
        )
    verified_current = HotProjectionPointer.model_validate(current.model_dump(mode="json"))
    if verified_manifest.window.observed_until < verified_current.observed_until:
        raise ValueError("hot projection pointer must not regress observed_until")
    if verified_current.manifest_checksum == verified_manifest.manifest_checksum:
        return None
    return HotProjectionPointerAdvance(
        expected_generation=verified_current.generation,
        expected_manifest_checksum=verified_current.manifest_checksum,
        next_generation=verified_current.generation + 1,
        next_manifest_checksum=verified_manifest.manifest_checksum,
    )


def apply_hot_projection_pointer_advance(
    current: HotProjectionPointer | None,
    advance: HotProjectionPointerAdvance,
    manifest: HotProjectionManifest,
) -> HotProjectionPointer:
    """Validate a compare-and-swap outcome without a Railway or database dependency."""
    verified_manifest = HotProjectionManifest.model_validate(manifest.model_dump(mode="json"))
    verified_advance = HotProjectionPointerAdvance.model_validate(advance.model_dump(mode="json"))
    if verified_advance.next_manifest_checksum != verified_manifest.manifest_checksum:
        raise ValueError("pointer advance must target the supplied projection manifest")
    if current is not None:
        verified_current = HotProjectionPointer.model_validate(current.model_dump(mode="json"))
        if verified_manifest.window.observed_until < verified_current.observed_until:
            raise ValueError("hot projection pointer must not regress observed_until")
        if (
            verified_current.manifest_checksum == verified_advance.next_manifest_checksum
            and verified_current.generation == verified_advance.next_generation
            and verified_current.observed_until == verified_manifest.window.observed_until
        ):
            return verified_current
        if (
            verified_current.generation != verified_advance.expected_generation
            or verified_current.manifest_checksum != verified_advance.expected_manifest_checksum
        ):
            raise ValueError("pointer compare-and-swap guard does not match the current pointer")
    elif verified_advance.expected_generation != 0 or verified_advance.expected_manifest_checksum is not None:
        raise ValueError("pointer compare-and-swap expected an existing pointer")
    return HotProjectionPointer(
        generation=verified_advance.next_generation,
        manifest_checksum=verified_manifest.manifest_checksum,
        observed_until=verified_manifest.window.observed_until,
    )
