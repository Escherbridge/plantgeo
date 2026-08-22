"""Recoverable local Parquet materialization for validated NASA historical receipts."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from pydantic import Field, field_validator

if TYPE_CHECKING:
    from pathlib import Path

from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.weather_observations.nasa_power import (
    HistoricalNasaBackfillPlan,
    HistoricalNasaCheckpoint,
    historical_nasa_plan_checksum,
    historical_nasa_release_manifest,
    load_cached_historical_nasa_result,
    require_complete_nasa_result,
)

HISTORICAL_NASA_PARQUET_SCHEMA_VERSION: Literal[1] = 1
HISTORICAL_NASA_PARQUET_MANIFEST_FILE = "manifest.json"
HISTORICAL_NASA_PARQUET_MEMORY_LIMIT = "1GB"
HISTORICAL_NASA_PARQUET_THREAD_COUNT = 1


class HistoricalNasaParquetManifest(ContractModel):
    """Evidence binding a daily-partitioned Parquet dataset to immutable raw receipts."""

    schema_version: Literal[1] = HISTORICAL_NASA_PARQUET_SCHEMA_VERSION
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


def historical_nasa_parquet_root(root: Path, plan: HistoricalNasaBackfillPlan) -> Path:
    """Return the immutable local data-lake root for one reviewed NASA plan."""
    return root / "warehouse" / "nasa-power-daily" / historical_nasa_plan_checksum(plan)


def load_historical_nasa_parquet_manifest(path: Path) -> HistoricalNasaParquetManifest:
    """Read a completed local data-lake manifest without opening a database or network connection."""
    return HistoricalNasaParquetManifest.model_validate_json(path.read_bytes())


def materialize_historical_nasa_parquet(  # noqa: PLR0912, PLR0915
    root: Path,
    plan: HistoricalNasaBackfillPlan,
    checkpoint: HistoricalNasaCheckpoint,
) -> HistoricalNasaParquetManifest:
    """Create daily Hive partitions from a complete, locally cached four-year replay."""
    _require_complete_checkpoint(plan, checkpoint)
    expected_plan_checksum = historical_nasa_plan_checksum(plan)
    receipt_manifest = historical_nasa_release_manifest(plan, checkpoint)
    target = historical_nasa_parquet_root(root, plan)
    manifest_path = target / HISTORICAL_NASA_PARQUET_MANIFEST_FILE
    if manifest_path.exists():
        existing = load_historical_nasa_parquet_manifest(manifest_path)
        if (
            existing.source_plan_checksum != expected_plan_checksum
            or existing.source_receipt_manifest_checksum != receipt_manifest
        ):
            raise ValueError("existing NASA Parquet dataset does not bind the reviewed source receipts")
        if not any(target.rglob("*.parquet")):
            raise ValueError("NASA Parquet manifest has no materialized data files")
        return existing
    if target.exists():
        raise ValueError("NASA Parquet target exists without a completed manifest")

    build = _resumable_build_directory(target)
    staging = build / "staging"
    spill = build / "duckdb-spill"
    staging.mkdir(parents=True, exist_ok=True)
    spill.mkdir(exist_ok=True)
    receipt_by_cell = {receipt.cell_key: receipt for receipt in checkpoint.receipts}
    for cell in plan.nasa.cells:
        result = load_cached_historical_nasa_result(
            root,
            plan,
            cell,
            cache_plan_checksum=checkpoint.raw_cache_plan_checksum,
        )
        if result is None:
            raise ValueError("NASA Parquet materialization requires every validated local raw response")
        require_complete_nasa_result(plan.nasa, result)
        receipt = receipt_by_cell.get(cell.cell_key)
        if receipt is None or (
            receipt.payload_checksum != result.payload_checksum
            or receipt.payload_bytes != len(result.payload)
            or receipt.observation_count != len(result.observations)
            or receipt.coverage_count != len(result.coverage)
            or receipt.retrieved_at != result.retrieved_at
        ):
            raise ValueError("NASA Parquet raw cache does not match its checkpoint receipt")
        rows = [
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
            for observation in result.observations
        ]
        staging_path = staging / f"{cell.cell_key.replace(':', '_')}.parquet"
        if staging_path.exists():
            if not _staging_file_matches_receipt(
                staging_path,
                expected_row_count=len(rows),
                cell_key=cell.cell_key,
                payload_checksum=result.payload_checksum,
            ):
                raise ValueError("NASA Parquet staging file does not match its validated receipt")
            continue
        pq.write_table(pa.Table.from_pylist(rows), staging_path, compression="zstd", row_group_size=16_384)

    data_root = build / "source=nasa-power-daily"
    connection = duckdb.connect()
    copied = False
    try:
        connection.execute("SET memory_limit = " + _sql_literal(HISTORICAL_NASA_PARQUET_MEMORY_LIMIT))
        connection.execute(f"SET threads = {HISTORICAL_NASA_PARQUET_THREAD_COUNT}")
        connection.execute("SET preserve_insertion_order = false")
        connection.execute("SET temp_directory = " + _sql_literal(spill.as_posix()))
        source_glob = (staging / "*.parquet").as_posix()
        connection.execute("CREATE VIEW nasa_cells AS SELECT * FROM read_parquet(" + _sql_literal(source_glob) + ")")
        count_row = connection.execute("SELECT count(*) FROM nasa_cells").fetchone()
        if count_row is None:
            raise ValueError("NASA Parquet staging did not return a row count")
        row_count = int(count_row[0])
        expected_row_count = len(plan.nasa.cells) * len(plan.nasa.parameters) * plan.nasa.window.day_count
        if row_count != expected_row_count:
            raise ValueError("NASA Parquet staging does not contain every expected observation")
        connection.execute(
            "COPY (SELECT * FROM nasa_cells ORDER BY observed_date, cell_key, source_parameter) TO "
            + _sql_literal(data_root.as_posix())
            + " (FORMAT PARQUET, PARTITION_BY (year, month, day), COMPRESSION ZSTD, PER_THREAD_OUTPUT FALSE)"
        )
        copied = True
    finally:
        connection.close()
    if copied:
        shutil.rmtree(staging)
        shutil.rmtree(spill, ignore_errors=True)

    data_file_count = len(list(data_root.rglob("*.parquet")))
    partition_count = len({path.parent for path in data_root.rglob("*.parquet")})
    if data_file_count < 1 or partition_count != plan.nasa.window.day_count:
        raise ValueError("NASA Parquet materialization did not create every daily partition")
    manifest = HistoricalNasaParquetManifest(
        source_plan_checksum=expected_plan_checksum,
        source_receipt_manifest_checksum=receipt_manifest,
        row_count=row_count,
        partition_count=partition_count,
        data_file_count=data_file_count,
        created_at=datetime.now(UTC),
    )
    (build / HISTORICAL_NASA_PARQUET_MANIFEST_FILE).write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))
    build.replace(target)
    return manifest


def _require_complete_checkpoint(plan: HistoricalNasaBackfillPlan, checkpoint: HistoricalNasaCheckpoint) -> None:
    """Reject any Parquet conversion that could conceal incomplete source acquisition."""
    if checkpoint.plan_checksum != historical_nasa_plan_checksum(plan) or checkpoint.state != "validated":
        raise ValueError("NASA Parquet materialization requires a validated matching checkpoint")
    if [receipt.cell_key for receipt in checkpoint.receipts] != [cell.cell_key for cell in plan.nasa.cells]:
        raise ValueError("NASA Parquet materialization requires every reviewed source cell")


def _resumable_build_directory(target: Path) -> Path:
    """Reuse one interrupted build for the same immutable output target."""
    builds = sorted(target.parent.glob(f".{target.name}.building-*"))
    if len(builds) > 1:
        raise ValueError("NASA Parquet materialization has multiple interrupted build directories")
    if builds:
        return builds[0]
    return target.parent / f".{target.name}.building-{uuid4().hex}"


def _staging_file_matches_receipt(
    path: Path,
    *,
    expected_row_count: int,
    cell_key: str,
    payload_checksum: str,
) -> bool:
    """Verify resumable staging still represents one validated source-cell receipt."""
    try:
        table = pq.read_table(path, columns=["cell_key", "payload_checksum"])
    except (OSError, pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return False
    if table.num_rows != expected_row_count:
        return False
    return set(table.column("cell_key").to_pylist()) == {cell_key} and set(
        table.column("payload_checksum").to_pylist()
    ) == {payload_checksum}


def _sql_literal(value: str) -> str:
    """Quote a filesystem value for a DuckDB literal without interpolating executable SQL."""
    return "'" + value.replace("'", "''") + "'"
