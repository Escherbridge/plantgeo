"""Bounded daily Parquet materialization for validated local ERA5-Land archives."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from pydantic import Field, field_validator

if TYPE_CHECKING:
    from pathlib import Path

from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.historical_era5 import (
    Era5LandMonthlyResult,
    Era5LandPeriod,
    HistoricalEra5Checkpoint,
    HistoricalEra5LandBackfillPlan,
    historical_era5_plan_checksum,
    historical_era5_release_manifest,
    load_cached_historical_era5_result,
    require_complete_era5_result,
)

HISTORICAL_ERA5_PARQUET_SCHEMA_VERSION: Literal[1] = 1
HISTORICAL_ERA5_PARQUET_MANIFEST_FILE = "manifest.json"


class HistoricalEra5ParquetManifest(ContractModel):
    """Evidence binding daily local Parquet files to complete immutable ERA5 receipts."""

    schema_version: Literal[1] = HISTORICAL_ERA5_PARQUET_SCHEMA_VERSION
    source_plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_receipt_manifest_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=1)
    partition_count: int = Field(ge=1)
    data_file_count: int = Field(ge=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_aware_created_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)


def historical_era5_parquet_root(root: Path, plan: HistoricalEra5LandBackfillPlan) -> Path:
    """Return the immutable local data-lake root for one reviewed ERA5 plan."""
    return root / "warehouse" / "era5-land-daily" / historical_era5_plan_checksum(plan)


def load_historical_era5_parquet_manifest(path: Path) -> HistoricalEra5ParquetManifest:
    """Read a completed ERA5 data-lake manifest without provider or database access."""
    return HistoricalEra5ParquetManifest.model_validate_json(path.read_bytes())


def materialize_historical_era5_parquet(
    root: Path,
    plan: HistoricalEra5LandBackfillPlan,
    checkpoint: HistoricalEra5Checkpoint,
) -> HistoricalEra5ParquetManifest:
    """Write one Zstandard-compressed Hive file per requested UTC day without a provider call."""
    _require_complete_checkpoint(plan, checkpoint)
    expected_plan_checksum = historical_era5_plan_checksum(plan)
    receipt_manifest = historical_era5_release_manifest(plan, checkpoint)
    target = historical_era5_parquet_root(root, plan)
    manifest_path = target / HISTORICAL_ERA5_PARQUET_MANIFEST_FILE
    if manifest_path.exists():
        existing = load_historical_era5_parquet_manifest(manifest_path)
        if (
            existing.source_plan_checksum != expected_plan_checksum
            or existing.source_receipt_manifest_checksum != receipt_manifest
        ):
            raise ValueError("existing ERA5 Parquet dataset does not bind the reviewed source receipts")
        if not any(target.rglob("*.parquet")):
            raise ValueError("ERA5 Parquet manifest has no materialized data files")
        return existing
    if target.exists():
        raise ValueError("ERA5 Parquet target exists without a completed manifest")

    build = target.parent / f".{target.name}.building-{uuid4().hex}"
    data_root = build / "source=era5-land-daily"
    row_count = 0
    for period in plan.periods:
        result = load_cached_historical_era5_result(
            root,
            plan,
            period,
            cache_plan_checksum=checkpoint.raw_cache_plan_checksum,
        )
        if result is None:
            raise ValueError("ERA5 Parquet materialization requires every validated local raw archive")
        require_complete_era5_result(plan, result)
        for day_offset in range(_period_day_count(period)):
            day_rows = _daily_rows(plan, period, result, day_offset)
            observed_date = day_rows[0]["observed_date"]
            if not isinstance(observed_date, date):
                raise ValueError("ERA5 Parquet day must have a date partition key")
            directory = (
                data_root
                / f"year={observed_date.year:04d}"
                / f"month={observed_date.month:02d}"
                / f"day={observed_date.day:02d}"
            )
            directory.mkdir(parents=True, exist_ok=True)
            pq.write_table(
                pa.Table.from_pylist(day_rows),
                directory / f"part-{period.key}.parquet",
                compression="zstd",
                row_group_size=16_384,
            )
            row_count += len(day_rows)

    expected_row_count = len(plan.cells) * len(plan.parameters) * plan.window.day_count
    data_file_count = len(list(data_root.rglob("*.parquet")))
    partition_count = len({path.parent for path in data_root.rglob("*.parquet")})
    if (
        row_count != expected_row_count
        or data_file_count != plan.window.day_count
        or partition_count != plan.window.day_count
    ):
        raise ValueError("ERA5 Parquet materialization did not create every expected daily partition")
    manifest = HistoricalEra5ParquetManifest(
        source_plan_checksum=expected_plan_checksum,
        source_receipt_manifest_checksum=receipt_manifest,
        row_count=row_count,
        partition_count=partition_count,
        data_file_count=data_file_count,
        created_at=datetime.now(UTC),
    )
    (build / HISTORICAL_ERA5_PARQUET_MANIFEST_FILE).write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))
    build.replace(target)
    return manifest


def _daily_rows(
    plan: HistoricalEra5LandBackfillPlan,
    period: Era5LandPeriod,
    result: Era5LandMonthlyResult,
    day_offset: int,
) -> list[dict[str, object]]:
    """Select one day from deterministic parameter/cell/day parser order with bounded memory."""
    period_day_count = _period_day_count(period)
    cell_count = len(plan.cells)
    rows: list[dict[str, object]] = []
    for parameter_offset, _parameter in enumerate(plan.parameters):
        parameter_start = parameter_offset * cell_count * period_day_count
        for cell_offset, cell in enumerate(plan.cells):
            observation = result.observations[parameter_start + cell_offset * period_day_count + day_offset]
            rows.append(
                {
                    "cell_key": cell.cell_key,
                    "latitude": cell.latitude,
                    "longitude": cell.longitude,
                    "observed_date": observation.observed_at.date(),
                    "observed_at": observation.observed_at,
                    "source_parameter": observation.source_parameter,
                    "signal_name": observation.signal_name,
                    "original_value": observation.original_value,
                    "original_unit": observation.original_unit,
                    "normalized_value": observation.normalized_value,
                    "normalized_unit": observation.normalized_unit,
                    "quality_flag": observation.quality_flag,
                    "is_observed": observation.is_observed,
                    "payload_checksum": observation.payload_checksum,
                    "retrieved_at": result.retrieved_at,
                    "year": observation.observed_at.year,
                    "month": observation.observed_at.month,
                    "day": observation.observed_at.day,
                }
            )
    if not rows:
        raise ValueError("ERA5 Parquet day cannot be empty")
    return rows


def _period_day_count(period: Era5LandPeriod) -> int:
    """Return the already validated inclusive period-day count."""
    return (period.end_date - period.start_date).days + 1


def _require_complete_checkpoint(plan: HistoricalEra5LandBackfillPlan, checkpoint: HistoricalEra5Checkpoint) -> None:
    """Reject a Parquet conversion that could disguise incomplete source acquisition."""
    if checkpoint.plan_checksum != historical_era5_plan_checksum(plan) or checkpoint.state != "validated":
        raise ValueError("ERA5 Parquet materialization requires a validated matching checkpoint")
    if [receipt.period_key for receipt in checkpoint.receipts] != [period.key for period in plan.periods]:
        raise ValueError("ERA5 Parquet materialization requires every reviewed monthly source archive")
