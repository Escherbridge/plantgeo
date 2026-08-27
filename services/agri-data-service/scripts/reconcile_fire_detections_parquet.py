"""Reconcile the complete settled FIRMS PostgreSQL record with its four Parquet tiers.

Run from ``services/agri-data-service``. The audit is read-only unless ``--repair`` is supplied;
repairs reuse the normal lane-day lock and export path and never delete PostgreSQL data.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import os
import random
import sys
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from filelock import FileLock, Timeout
from sqlalchemy import text

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.config import settings  # noqa: E402
from agri_data_service.db.engine import local_source_loader_engine  # noqa: E402
from agri_data_service.foundation.parquet.absence import GovernedAbsence  # noqa: E402
from agri_data_service.foundation.parquet.completion import PartitionCompletion  # noqa: E402
from agri_data_service.foundation.parquet.paths import (  # noqa: E402
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.gap_fill import (  # noqa: E402
    DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    fill_one_lane_day,
    postgres_lane_day_lock,
)
from agri_data_service.pipeline.parquet.lane_registry import resolve_lanes  # noqa: E402
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, conform_to_stream_schema  # noqa: E402
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS, derive_tier  # noqa: E402
from agri_data_service.warehouse.schemas.fire_detections import (  # noqa: E402
    FIRE_DETECTIONS_SCHEMA,
    FIRE_DETECTIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration


CHECKPOINT_SCHEMA_VERSION: Final = 2
TIERS: Final[tuple[ZoomTier, ...]] = (13, 9, 5, 0)
KIND: Final = "observed"
DEFAULT_CHECKPOINT: Final = SERVICE_ROOT / ".agri-local-runs" / "fire-detections-reconcile-checkpoint.json"
DEFAULT_MANIFEST: Final = SERVICE_ROOT / ".agri-local-runs" / "fire-detections-reconciliation.json"
DEFAULT_SOURCE_READ_EVIDENCE: Final = (
    SERVICE_ROOT / ".agri-local-runs" / "fire-detections-source-read-invariant-failures.jsonl"
)
DEFAULT_CHECKPOINT_LOCK_ATTEMPTS: Final = 3
DEFAULT_CHECKPOINT_LOCK_POLL_SECONDS: Final = 1.0
MAX_R2_WORKERS: Final = 32
MAX_RETRY_ATTEMPTS: Final = 20
MAX_REPAIR_ATTEMPTS: Final = 20
MAX_LOCK_POLLS: Final = 360
MAX_CHECKPOINT_LOCK_ATTEMPTS: Final = 60
MAX_STATEMENT_TIMEOUT_SECONDS: Final = 3_600
MAX_RETRY_DELAY_SECONDS: Final = 60.0
MAX_POLL_DELAY_SECONDS: Final = 60.0
MAX_REPAIR_DELAY_SECONDS: Final = 60.0

_SOURCE_MONTH_SQL: Final = text(
    """
    WITH day_detections AS (
        SELECT
            geo.feature_observation_day(feature.properties) AS observed_day,
            ST_X(feature.geom)::numeric AS longitude,
            ST_Y(feature.geom)::numeric AS latitude,
            CASE
                WHEN jsonb_typeof(feature.properties -> 'frp') = 'number'
                    THEN (feature.properties ->> 'frp')::double precision
            END AS frp,
            feature.properties ->> 'confidenceNormalized' AS confidence_normalized,
            (feature.properties ->> 'observedAt')::timestamptz AS observed_at
        FROM geo.features AS feature
        WHERE feature.layer_id = CAST(:layer_id AS uuid)
          AND feature.status = 'published'
          AND feature.geometry_id IS NOT NULL
          AND feature.geom IS NOT NULL
          AND geo.feature_observation_day(feature.properties) BETWEEN CAST(:first_day AS date)
                                                                  AND CAST(:last_day AS date)
    ),
    gridded AS (
        SELECT
            observed_day,
            floor(longitude / 0.005) * 0.005 AS cell_longitude,
            floor(latitude / 0.005) * 0.005 AS cell_latitude,
            frp,
            confidence_normalized,
            observed_at
        FROM day_detections
    )
    SELECT
        cell_longitude::double precision AS cell_longitude,
        cell_latitude::double precision AS cell_latitude,
        observed_day,
        COUNT(*)::bigint AS detection_count,
        SUM(frp::numeric)::double precision AS frp_sum,
        COUNT(frp)::bigint AS frp_observation_count,
        (COUNT(*) FILTER (WHERE confidence_normalized = 'high'))::bigint
            AS high_confidence_detection_count,
        MAX(observed_at) AS newest_observed_at
    FROM gridded
    GROUP BY observed_day, cell_longitude, cell_latitude
    ORDER BY observed_day, cell_longitude, cell_latitude
    """
)

_LAYER_ID_SQL: Final = text("SELECT id::text FROM geo.layers WHERE name = :name")


@dataclass(frozen=True, slots=True)
class DaySemantic:
    """Canonical content and additive totals for one cell-day table."""

    row_count: int
    detection_count: int
    frp_observation_count: int
    high_confidence_detection_count: int
    frp_sum_hex: str
    newest_observed_at: str | None
    sha256: str


@dataclass(frozen=True, slots=True)
class TierSnapshot:
    """One bounded tier listing plus the rows and marker evidence behind it."""

    statuses: Mapping[date, PartitionDayStatus]
    table: pa.Table
    marker_payloads: Mapping[str, bytes]
    absence_payloads: Mapping[str, bytes]
    part_keys: tuple[str, ...]
    marker_keys: tuple[str, ...]
    absence_keys: tuple[str, ...]


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO date") from error


def _float_token(value: object | None) -> str:
    if value is None:
        return "null"
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"semantic checksum refuses non-finite float {number!r}")
    return number.hex()


def _timestamp_token(value: object | None) -> str:
    if value is None:
        return "null"
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        raise ValueError("semantic checksum refuses a timezone-naive newest_observed_at")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _frp_token(value: object | None) -> str:
    """Normalize FRP to the lane's micro-unit semantic checksum precision."""
    if value is None:
        return "null"
    number = Decimal(str(float(value)))
    if not number.is_finite():
        raise ValueError(f"semantic checksum refuses non-finite FRP {value!r}")
    return format(number.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN), "f")


def _day_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _canonical_row(row: Mapping[str, object]) -> tuple[str, ...]:
    """Encode every stored field without lossy float or timestamp formatting."""
    return (
        _float_token(row["cell_longitude"]),
        _float_token(row["cell_latitude"]),
        _day_value(row["observed_day"]).isoformat(),
        str(int(row["detection_count"])),
        _frp_token(row["frp_sum"]),
        str(int(row["frp_observation_count"])),
        str(int(row["high_confidence_detection_count"])),
        _timestamp_token(row["newest_observed_at"]),
    )


def _semantic_days(table: pa.Table) -> dict[date, DaySemantic]:
    rows_by_day: dict[date, list[tuple[str, ...]]] = defaultdict(list)
    totals: dict[date, dict[str, object]] = defaultdict(
        lambda: {"detections": 0, "frp_observations": 0, "high": 0, "frp": [], "newest": None}
    )
    for raw in table.to_pylist():
        row: Mapping[str, object] = raw
        day = _day_value(row["observed_day"])
        rows_by_day[day].append(_canonical_row(row))
        total = totals[day]
        total["detections"] = int(total["detections"]) + int(row["detection_count"])
        total["frp_observations"] = int(total["frp_observations"]) + int(row["frp_observation_count"])
        total["high"] = int(total["high"]) + int(row["high_confidence_detection_count"])
        if row["frp_sum"] is not None:
            frp_values = total["frp"]
            if not isinstance(frp_values, list):
                raise TypeError("internal FRP accumulator is not a list")
            frp_values.append(float(row["frp_sum"]))
        newest = row["newest_observed_at"]
        if newest is not None and (total["newest"] is None or newest > total["newest"]):
            total["newest"] = newest

    summaries: dict[date, DaySemantic] = {}
    for day, canonical_rows in rows_by_day.items():
        digest = hashlib.sha256()
        for row in sorted(canonical_rows):
            digest.update("\x1f".join(row).encode("utf-8"))
            digest.update(b"\n")
        total = totals[day]
        frp_values = total["frp"]
        if not isinstance(frp_values, list):
            raise TypeError("internal FRP accumulator is not a list")
        summaries[day] = DaySemantic(
            row_count=len(canonical_rows),
            detection_count=int(total["detections"]),
            frp_observation_count=int(total["frp_observations"]),
            high_confidence_detection_count=int(total["high"]),
            frp_sum_hex=math.fsum(frp_values).hex(),
            newest_observed_at=None if total["newest"] is None else _timestamp_token(total["newest"]),
            sha256=digest.hexdigest(),
        )
    return summaries


def _empty_fire_table() -> pa.Table:
    return FIRE_DETECTIONS_SCHEMA.arrow_schema.empty_table()


def _source_row_evidence(row: Mapping[str, object]) -> dict[str, object]:
    """Keep a bounded, credential-free snapshot of one aggregate row."""
    return {
        key: row.get(key)
        for key in (
            "observed_day",
            "cell_longitude",
            "cell_latitude",
            "detection_count",
            "frp_observation_count",
            "high_confidence_detection_count",
            "frp_sum",
            "newest_observed_at",
        )
    }


def _validate_source_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    first_day: date,
    last_day: date,
    stage: str,
) -> None:
    """Reject impossible aggregate rows before they can authorize a repair."""
    for index, row in enumerate(rows):
        day = _day_value(row["observed_day"])
        detection_count = int(row["detection_count"])
        frp_count = int(row["frp_observation_count"])
        high_count = int(row["high_confidence_detection_count"])
        valid = (
            first_day <= day <= last_day
            and detection_count > 0
            and 0 <= frp_count <= detection_count
            and 0 <= high_count <= detection_count
        )
        if not valid:
            evidence = _source_row_evidence(row)
            raise ValueError(
                f"invalid fire source aggregate at {stage} row {index}: "
                f"{json.dumps(evidence, default=str, sort_keys=True)}"
            )


def _validate_arrow_conversion(
    raw_rows: Sequence[Mapping[str, object]],
    arrow_rows: Sequence[Mapping[str, object]],
) -> None:
    """Require Arrow materialization to preserve every canonical source row."""
    if len(raw_rows) != len(arrow_rows):
        raise ValueError(f"fire source Arrow row count changed from {len(raw_rows)} to {len(arrow_rows)}")
    for index, (raw, arrow) in enumerate(zip(raw_rows, arrow_rows, strict=True)):
        if _canonical_row(raw) != _canonical_row(arrow):
            evidence = {
                "raw": _source_row_evidence(raw),
                "arrow": _source_row_evidence(arrow),
            }
            raise ValueError(
                f"fire source Arrow conversion changed row {index}: {json.dumps(evidence, default=str, sort_keys=True)}"
            )


def _record_source_read_failure(
    *,
    first_day: date,
    last_day: date,
    attempt: int,
    error: Exception,
) -> None:
    """Append retry evidence without source credentials or raw detections."""
    DEFAULT_SOURCE_READ_EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "attempt": attempt,
        "error": f"{type(error).__name__}: {error}",
        "first_day": first_day.isoformat(),
        "last_day": last_day.isoformat(),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    with DEFAULT_SOURCE_READ_EVIDENCE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _month_key(first_day: date) -> str:
    return f"{first_day.year:04d}-{first_day.month:02d}"


def _month_windows(first_day: date, last_day: date) -> Iterable[tuple[date, date]]:
    cursor = first_day
    while cursor <= last_day:
        next_month = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
        batch_last = min(last_day, next_month - timedelta(days=1))
        yield cursor, batch_last
        cursor = batch_last + timedelta(days=1)


def _days(first_day: date, last_day: date) -> Iterable[date]:
    for offset in range((last_day - first_day).days + 1):
        yield first_day + timedelta(days=offset)


def _retry[T](
    operation: str,
    call: Callable[[], T],
    *,
    attempts: int,
    base_delay_seconds: float,
) -> T:
    """Retry an idempotent R2 read/list with capped exponential backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:
            if attempt == attempts:
                raise RuntimeError(
                    f"{operation} failed after {attempts} attempt(s): {type(error).__name__}: {error}"
                ) from error
            delay = min(30.0, base_delay_seconds * (2 ** (attempt - 1))) + random.uniform(0, 0.25)
            print(
                f"R2 retry {attempt}/{attempts - 1} for {operation} after {type(error).__name__}: {error}; "
                f"sleeping {delay:.2f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("retry loop exhausted without returning or raising")


def _download_payloads(
    store: ObjectStore,
    relative_keys: Sequence[str],
    *,
    workers: int,
    attempts: int,
    base_delay_seconds: float,
) -> dict[str, bytes]:
    """Download one month's selected objects with bounded concurrency and per-object retries."""

    def fetch(relative_key: str) -> tuple[str, bytes]:
        payload = _retry(
            f"GET {relative_key}",
            lambda: store._backend.get(store.key_for(relative_key)),
            attempts=attempts,
            base_delay_seconds=base_delay_seconds,
        )
        if payload is None:
            raise RuntimeError(f"GET {relative_key} returned no object after it appeared in the month listing")
        return relative_key, payload

    payloads: dict[str, bytes] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fire-r2-read") as executor:
        futures = [executor.submit(fetch, key) for key in relative_keys]
        for future in as_completed(futures):
            key, payload = future.result()
            payloads[key] = payload
    return payloads


def _load_tier_snapshot(
    store: ObjectStore,
    *,
    tier: ZoomTier,
    first_day: date,
    last_day: date,
    workers: int,
    attempts: int,
    base_delay_seconds: float,
) -> TierSnapshot:
    keys = _retry(
        f"LIST fire-detections z{tier} {_month_key(first_day)}",
        lambda: store.list_partition_keys(
            FIRE_DETECTIONS_STREAM,
            KIND,
            tier,
            year=first_day.year,
            month=first_day.month,
        ),
        attempts=attempts,
        base_delay_seconds=base_delay_seconds,
    )
    selected: list[str] = []
    part_keys: list[str] = []
    marker_keys: list[str] = []
    absence_keys: list[str] = []
    for key in keys:
        parsed_part = try_parse_partition_path(key)
        parsed_marker = try_parse_completion_marker_path(key)
        parsed_absence = try_parse_absence_marker_path(key)
        parsed_day = (
            parsed_part.day
            if parsed_part is not None
            else parsed_marker.day
            if parsed_marker is not None
            else parsed_absence.day
            if parsed_absence is not None
            else None
        )
        if parsed_day is None or not first_day <= parsed_day <= last_day:
            continue
        if parsed_part is not None:
            part_keys.append(key)
            selected.append(key)
        elif parsed_marker is not None:
            marker_keys.append(key)
            selected.append(key)
        elif parsed_absence is not None:
            absence_keys.append(key)
            selected.append(key)

    payloads = _download_payloads(
        store,
        selected,
        workers=workers,
        attempts=attempts,
        base_delay_seconds=base_delay_seconds,
    )
    part_tables = [
        pq.read_table(io.BytesIO(payloads[key]))
        for key in sorted(
            part_keys,
            key=lambda value: (
                try_parse_partition_path(value).day,  # type: ignore[union-attr]
                try_parse_partition_path(value).part_index,  # type: ignore[union-attr]
            ),
        )
    ]
    raw_table = pa.concat_tables(part_tables) if part_tables else _empty_fire_table()
    table = conform_to_stream_schema(raw_table, FIRE_DETECTIONS_SCHEMA)
    statuses = partition_day_statuses(
        layer=FIRE_DETECTIONS_STREAM,
        kind=KIND,
        zoom=tier,
        first_day=first_day,
        last_day=last_day,
        keys=keys,
    )
    return TierSnapshot(
        statuses=statuses,
        table=table,
        marker_payloads={key: payloads[key] for key in marker_keys},
        absence_payloads={key: payloads[key] for key in absence_keys},
        part_keys=tuple(part_keys),
        marker_keys=tuple(marker_keys),
        absence_keys=tuple(absence_keys),
    )


async def _source_month_table(
    session: AsyncSession,
    *,
    layer_id: str,
    first_day: date,
    last_day: date,
    statement_timeout_seconds: int,
    attempts: int,
    base_delay_seconds: float,
) -> pa.Table:
    """Read one month or partial month from the same filtered cell-day contract as the lane adapter."""
    timeout = f"{statement_timeout_seconds}s"
    for attempt in range(1, attempts + 1):
        try:
            await session.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": timeout})
            result = await session.execute(
                _SOURCE_MONTH_SQL,
                {"layer_id": layer_id, "first_day": first_day, "last_day": last_day},
            )
            rows = [dict(row) for row in result.mappings()]
            _validate_source_rows(rows, first_day=first_day, last_day=last_day, stage="database_mapping")
            table = (
                pa.Table.from_pylist(rows, schema=FIRE_DETECTIONS_SCHEMA.arrow_schema) if rows else _empty_fire_table()
            )
            arrow_rows = table.to_pylist()
            _validate_source_rows(arrow_rows, first_day=first_day, last_day=last_day, stage="arrow_conversion")
            _validate_arrow_conversion(rows, arrow_rows)
            await session.rollback()
            return table
        except Exception as error:
            with suppress(Exception):
                await session.rollback()
            _record_source_read_failure(
                first_day=first_day,
                last_day=last_day,
                attempt=attempt,
                error=error,
            )
            if attempt == attempts:
                raise RuntimeError(
                    f"PostgreSQL read {first_day}..{last_day} failed after {attempts} attempt(s): "
                    f"{type(error).__name__}: {error}"
                ) from error
            delay = min(30.0, base_delay_seconds * (2 ** (attempt - 1))) + random.uniform(0, 0.25)
            print(
                f"PostgreSQL retry {attempt}/{attempts - 1} for {first_day}..{last_day} after "
                f"{type(error).__name__}: {error}; sleeping {delay:.2f}s",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)
    raise AssertionError("PostgreSQL retry loop exhausted without returning or raising")


def _derive_month(base: pa.Table) -> dict[ZoomTier, pa.Table]:
    if base.num_rows == 0:
        return {tier: _empty_fire_table() for tier in DERIVED_ZOOM_TIERS}
    frame = pl.from_arrow(base)
    if not isinstance(frame, pl.DataFrame):
        raise TypeError("pl.from_arrow unexpectedly returned a Series for a fire table")
    derived: dict[ZoomTier, list[pa.Table]] = {tier: [] for tier in DERIVED_ZOOM_TIERS}
    for day in sorted(frame.get_column("observed_day").unique().to_list()):
        day_frame = frame.filter(pl.col("observed_day") == day)
        for tier in DERIVED_ZOOM_TIERS:
            derived[tier].append(derive_tier(day_frame, stream=FIRE_DETECTIONS_STREAM, tier=tier).to_arrow())
    return {tier: pa.concat_tables(tables) if tables else _empty_fire_table() for tier, tables in derived.items()}


def _aggregate_semantics(days: Mapping[date, DaySemantic]) -> dict[str, object]:
    ordered = sorted(days.items())
    digest = hashlib.sha256()
    for day, semantic in ordered:
        digest.update(f"{day.isoformat()}:{semantic.sha256}\n".encode())
    return {
        "data_days": len(ordered),
        "cell_rows": sum(item.row_count for _, item in ordered),
        "detection_count": sum(item.detection_count for _, item in ordered),
        "frp_observation_count": sum(item.frp_observation_count for _, item in ordered),
        "high_confidence_detection_count": sum(item.high_confidence_detection_count for _, item in ordered),
        "frp_sum_hex": math.fsum(float.fromhex(item.frp_sum_hex) for _, item in ordered).hex(),
        "first_data_day": None if not ordered else ordered[0][0].isoformat(),
        "last_data_day": None if not ordered else ordered[-1][0].isoformat(),
        "semantic_sha256": digest.hexdigest(),
    }


def _marker_issues(
    snapshot: TierSnapshot,
    semantics: Mapping[date, DaySemantic],
    *,
    tier: ZoomTier,
) -> list[dict[str, object]]:
    part_counts: dict[date, int] = defaultdict(int)
    for key in snapshot.part_keys:
        parsed = try_parse_partition_path(key)
        if parsed is not None:
            part_counts[parsed.day] += 1
    marker_by_day = {
        parsed.day: key for key in snapshot.marker_keys if (parsed := try_parse_completion_marker_path(key)) is not None
    }
    issues: list[dict[str, object]] = []
    for key, payload in snapshot.absence_payloads.items():
        parsed = try_parse_absence_marker_path(key)
        try:
            GovernedAbsence.from_json_bytes(payload)
        except Exception as error:
            issues.append(
                {
                    "day": parsed.day.isoformat() if parsed is not None else "unknown",
                    "tier": tier,
                    "code": "invalid_governed_absence_marker",
                    "actual": f"{type(error).__name__}: {error}",
                }
            )
    for day, key in marker_by_day.items():
        try:
            marker = PartitionCompletion.from_json_bytes(snapshot.marker_payloads[key])
        except Exception as error:
            issues.append(
                {
                    "day": day.isoformat(),
                    "tier": tier,
                    "code": "invalid_completion_marker",
                    "actual": f"{type(error).__name__}: {error}",
                }
            )
            continue
        actual_rows = semantics.get(day)
        if actual_rows is None:
            issues.append(
                {
                    "day": day.isoformat(),
                    "tier": tier,
                    "code": "completion_marker_without_rows",
                    "actual": asdict(marker),
                }
            )
            continue
        if marker.part_count != part_counts[day]:
            issues.append(
                {
                    "day": day.isoformat(),
                    "tier": tier,
                    "code": "completion_part_count_mismatch",
                    "expected": part_counts[day],
                    "actual": marker.part_count,
                }
            )
        if marker.row_count != actual_rows.row_count:
            issues.append(
                {
                    "day": day.isoformat(),
                    "tier": tier,
                    "code": "completion_row_count_mismatch",
                    "expected": actual_rows.row_count,
                    "actual": marker.row_count,
                }
            )
    return issues


def _absence_checksum(snapshot: TierSnapshot) -> str:
    digest = hashlib.sha256()
    for key, payload in sorted(snapshot.absence_payloads.items()):
        digest.update(key.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def _same_additive_totals(left: DaySemantic, right: DaySemantic) -> bool:
    return (
        left.detection_count == right.detection_count
        and left.frp_observation_count == right.frp_observation_count
        and left.high_confidence_detection_count == right.high_confidence_detection_count
        and math.isclose(
            float.fromhex(left.frp_sum_hex),
            float.fromhex(right.frp_sum_hex),
            rel_tol=0.0,
            abs_tol=1e-6,
        )
        and left.newest_observed_at == right.newest_observed_at
    )


async def _audit_month(
    session: AsyncSession,
    store: ObjectStore,
    *,
    layer_id: str,
    first_day: date,
    last_day: date,
    args: argparse.Namespace,
) -> tuple[dict[str, object], set[date], set[date]]:
    source_table = await _source_month_table(
        session,
        layer_id=layer_id,
        first_day=first_day,
        last_day=last_day,
        statement_timeout_seconds=args.statement_timeout_seconds,
        attempts=args.postgres_read_attempts,
        base_delay_seconds=args.postgres_read_base_delay_seconds,
    )
    source_days = _semantic_days(source_table)
    snapshots: dict[ZoomTier, TierSnapshot] = {}
    parquet_days: dict[ZoomTier, dict[date, DaySemantic]] = {}
    for tier in TIERS:
        snapshots[tier] = _load_tier_snapshot(
            store,
            tier=tier,
            first_day=first_day,
            last_day=last_day,
            workers=args.r2_workers,
            attempts=args.r2_attempts,
            base_delay_seconds=args.r2_base_delay_seconds,
        )
        parquet_days[tier] = _semantic_days(snapshots[tier].table)

    derived = _derive_month(source_table)
    derived_days = {tier: _semantic_days(table) for tier, table in derived.items()}
    issues: list[dict[str, object]] = []
    for tier in TIERS:
        issues.extend(_marker_issues(snapshots[tier], parquet_days[tier], tier=tier))

    for day in _days(first_day, last_day):
        source = source_days.get(day)
        for tier in TIERS:
            expected_status: PartitionDayStatus = (
                "data" if source is not None else "absent" if tier == 13 else "missing"
            )
            actual_status = snapshots[tier].statuses[day]
            if actual_status != expected_status:
                issues.append(
                    {
                        "day": day.isoformat(),
                        "tier": tier,
                        "code": "partition_status_mismatch",
                        "expected": expected_status,
                        "actual": actual_status,
                    }
                )
        if source is None:
            continue
        base = parquet_days[13].get(day)
        if base is None or base.sha256 != source.sha256:
            issues.append(
                {
                    "day": day.isoformat(),
                    "tier": 13,
                    "code": "postgres_parquet_semantic_mismatch",
                    "expected": None if source is None else asdict(source),
                    "actual": None if base is None else asdict(base),
                }
            )
        if base is None:
            continue
        for tier in DERIVED_ZOOM_TIERS:
            expected = derived_days[tier].get(day)
            actual = parquet_days[tier].get(day)
            if expected is None or actual is None or expected.sha256 != actual.sha256:
                issues.append(
                    {
                        "day": day.isoformat(),
                        "tier": tier,
                        "code": "derived_tier_semantic_mismatch",
                        "expected": None if expected is None else asdict(expected),
                        "actual": None if actual is None else asdict(actual),
                    }
                )
            if actual is not None and not _same_additive_totals(base, actual):
                issues.append(
                    {
                        "day": day.isoformat(),
                        "tier": tier,
                        "code": "derived_tier_additive_invariant_mismatch",
                        "expected_base": asdict(base),
                        "actual": asdict(actual),
                    }
                )

    issue_days = {date.fromisoformat(str(issue["day"])) for issue in issues}
    repairable: set[date] = set()
    manual: set[date] = set()
    for day in issue_days:
        if day not in source_days:
            manual.add(day)
            continue
        statuses = {snapshots[tier].statuses[day] for tier in TIERS}
        if statuses & {"absent", "conflict"}:
            manual.add(day)
        else:
            repairable.add(day)

    status_counts = {
        str(tier): {
            status: sum(1 for value in snapshots[tier].statuses.values() if value == status)
            for status in ("data", "absent", "conflict", "incomplete", "missing")
        }
        for tier in TIERS
    }
    result: dict[str, object] = {
        "month": _month_key(first_day),
        "first_day": first_day.isoformat(),
        "last_day": last_day.isoformat(),
        "calendar_days": (last_day - first_day).days + 1,
        "audited_at": datetime.now(UTC).isoformat(),
        "source_postgres": _aggregate_semantics(source_days),
        "parquet": {str(tier): _aggregate_semantics(parquet_days[tier]) for tier in TIERS},
        "partition_status_counts": status_counts,
        "absence_sha256": {str(tier): _absence_checksum(snapshots[tier]) for tier in TIERS},
        "issues": issues,
        "repairable_days": [day.isoformat() for day in sorted(repairable)],
        "manual_intervention_days": [day.isoformat() for day in sorted(manual)],
        "parity": not issues,
    }
    return result, repairable, manual


async def _saved_source_is_current(
    session: AsyncSession,
    saved: Mapping[str, object],
    *,
    layer_id: str,
    first_day: date,
    last_day: date,
    statement_timeout_seconds: int,
    attempts: int,
    base_delay_seconds: float,
) -> bool:
    """Re-read one bounded PostgreSQL month before trusting a resumable checkpoint."""
    current = _aggregate_semantics(
        _semantic_days(
            await _source_month_table(
                session,
                layer_id=layer_id,
                first_day=first_day,
                last_day=last_day,
                statement_timeout_seconds=statement_timeout_seconds,
                attempts=attempts,
                base_delay_seconds=base_delay_seconds,
            )
        )
    )
    saved_source = saved.get("source_postgres")
    return isinstance(saved_source, Mapping) and (current["semantic_sha256"] == saved_source.get("semantic_sha256"))


async def _repair_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    today: date,
    run_id: str,
    args: argparse.Namespace,
) -> dict[str, object]:
    lane = resolve_lanes((FIRE_DETECTIONS_STREAM,))[0]
    raised_attempts = 0
    contention_polls = 0
    while True:
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                lane,
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=today,
                lane_day_lock=postgres_lane_day_lock,
                statement_timeout_seconds=args.statement_timeout_seconds,
            )
            await session.rollback()
        except Exception as error:
            with suppress(Exception):
                await session.rollback()
            outcome = "raised"
            parts = rows = written_bytes = 0
            detail = f"{day.isoformat()}: {type(error).__name__}: {error}"
        if outcome == "contended":
            contention_polls += 1
            if contention_polls >= args.lock_polls:
                return {
                    "day": day.isoformat(),
                    "outcome": outcome,
                    "contention_polls": contention_polls,
                    "detail": detail,
                }
            print(
                f"repair {day.isoformat()} contended ({contention_polls}/{args.lock_polls}); "
                f"polling again in {args.lock_poll_seconds:.1f}s",
                flush=True,
            )
            await asyncio.sleep(args.lock_poll_seconds)
            continue
        if outcome == "raised":
            raised_attempts += 1
            if raised_attempts >= args.repair_attempts:
                return {
                    "day": day.isoformat(),
                    "outcome": outcome,
                    "raised_attempts": raised_attempts,
                    "detail": detail,
                }
            delay = min(30.0, args.r2_base_delay_seconds * (2 ** (raised_attempts - 1)))
            print(
                f"repair {day.isoformat()} raised ({raised_attempts}/{args.repair_attempts}); "
                f"retrying in {delay:.1f}s: {detail}",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)
            continue
        return {
            "day": day.isoformat(),
            "outcome": outcome,
            "parts": parts,
            "rows": rows,
            "bytes": written_bytes,
            "contention_polls": contention_polls,
            "raised_attempts": raised_attempts,
            "detail": detail,
        }


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


@contextmanager
def _checkpoint_run_lock(path: Path, *, attempts: int, poll_seconds: float) -> Iterator[None]:
    """Admit one live process per checkpoint path with an OS-backed bounded lock."""
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_seconds = max(0.0, (attempts - 1) * poll_seconds)
    lock = FileLock(lock_path)
    try:
        with lock.acquire(timeout=timeout_seconds, poll_interval=max(0.01, poll_seconds)):
            yield
    except Timeout as error:
        raise RuntimeError(
            f"checkpoint lock {lock_path} is held by another live run; refused after {timeout_seconds:g}s"
        ) from error


def _new_checkpoint(first_day: date, last_day: date) -> dict[str, object]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "stream": FIRE_DETECTIONS_STREAM,
        "kind": KIND,
        "tiers": list(TIERS),
        "first_day": first_day.isoformat(),
        "last_day": last_day.isoformat(),
        "months": {},
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _load_checkpoint(path: Path, *, first_day: date, last_day: date, restart: bool) -> dict[str, object]:
    if restart or not path.exists():
        return _new_checkpoint(first_day, last_day)
    decoded_raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded_raw, dict):
        raise ValueError(f"checkpoint {path} is not a JSON object")
    decoded = decoded_raw
    expected = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "stream": FIRE_DETECTIONS_STREAM,
        "kind": KIND,
        "tiers": list(TIERS),
        "first_day": first_day.isoformat(),
        "last_day": last_day.isoformat(),
    }
    conflicts = {
        key: (expected_value, decoded.get(key))
        for key, expected_value in expected.items()
        if decoded.get(key) != expected_value
    }
    if conflicts:
        raise ValueError(f"checkpoint {path} belongs to a different audit: {conflicts}; use --restart-checkpoint")
    if not isinstance(decoded.get("months"), dict):
        raise ValueError(f"checkpoint {path} has no months object")
    return decoded


def _tree_checksum(months: Mapping[str, object], path: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for month, raw in sorted(months.items()):
        value: Any = raw
        for key in path:
            value = value[key]
        digest.update(f"{month}:{value}\n".encode())
    return digest.hexdigest()


def _sum_month_metric(months: Mapping[str, object], path: Sequence[str]) -> int:
    total = 0
    for raw in months.values():
        value: Any = raw
        for key in path:
            value = value[key]
        total += int(value)
    return total


def _checkpoint_value(raw: object, path: Sequence[str]) -> object:
    value: Any = raw
    for key in path:
        value = value[key]
    return value


def _sum_month_frp(months: Mapping[str, object], path: Sequence[str]) -> str:
    values = [float.fromhex(str(_checkpoint_value(raw, path))) for raw in months.values()]
    return math.fsum(values).hex()


def _extreme_month_value(months: Mapping[str, object], path: Sequence[str], *, latest: bool) -> object | None:
    values = [value for raw in months.values() if (value := _checkpoint_value(raw, path)) is not None]
    return (max(values) if latest else min(values)) if values else None


def _final_manifest(
    checkpoint: Mapping[str, object],
    *,
    run_id: str,
    expected_data_days: int | None,
) -> dict[str, object]:
    raw_months = checkpoint["months"]
    if not isinstance(raw_months, dict):
        raise TypeError("checkpoint months is not a mapping")
    months: Mapping[str, object] = raw_months
    data_days = _sum_month_metric(months, ("source_postgres", "data_days"))
    calendar_days = _sum_month_metric(months, ("calendar_days",))
    issues = [issue for raw in months.values() for issue in raw["issues"]]  # type: ignore[index]
    repaired_issues = [
        issue
        for raw in months.values()
        for issue in raw.get("pre_repair_issues", [])  # type: ignore[union-attr]
    ]
    repair_attempts = [
        repair
        for raw in months.values()
        for repair in raw.get("repair_attempts", [])  # type: ignore[union-attr]
    ]
    expectation_met = expected_data_days is None or data_days == expected_data_days
    source_summary = {
        "data_days": data_days,
        "governed_absence_days": calendar_days - data_days,
        "calendar_days": calendar_days,
        "first_data_day": _extreme_month_value(months, ("source_postgres", "first_data_day"), latest=False),
        "last_data_day": _extreme_month_value(months, ("source_postgres", "last_data_day"), latest=True),
        "cell_rows": _sum_month_metric(months, ("source_postgres", "cell_rows")),
        "detection_count": _sum_month_metric(months, ("source_postgres", "detection_count")),
        "frp_observation_count": _sum_month_metric(months, ("source_postgres", "frp_observation_count")),
        "high_confidence_detection_count": _sum_month_metric(
            months, ("source_postgres", "high_confidence_detection_count")
        ),
        "frp_sum_hex": _sum_month_frp(months, ("source_postgres", "frp_sum_hex")),
        "semantic_sha256_tree": _tree_checksum(months, ("source_postgres", "semantic_sha256")),
    }
    parquet = {
        str(tier): {
            "data_days": _sum_month_metric(months, ("parquet", str(tier), "data_days")),
            "first_data_day": _extreme_month_value(months, ("parquet", str(tier), "first_data_day"), latest=False),
            "last_data_day": _extreme_month_value(months, ("parquet", str(tier), "last_data_day"), latest=True),
            "cell_rows": _sum_month_metric(months, ("parquet", str(tier), "cell_rows")),
            "detection_count": _sum_month_metric(months, ("parquet", str(tier), "detection_count")),
            "frp_observation_count": _sum_month_metric(months, ("parquet", str(tier), "frp_observation_count")),
            "high_confidence_detection_count": _sum_month_metric(
                months, ("parquet", str(tier), "high_confidence_detection_count")
            ),
            "frp_sum_hex": _sum_month_frp(months, ("parquet", str(tier), "frp_sum_hex")),
            "semantic_sha256_tree": _tree_checksum(months, ("parquet", str(tier), "semantic_sha256")),
            "absence_sha256_tree": _tree_checksum(months, ("absence_sha256", str(tier))),
            "statuses": {
                status: _sum_month_metric(months, ("partition_status_counts", str(tier), status))
                for status in ("data", "absent", "conflict", "incomplete", "missing")
            },
        }
        for tier in TIERS
    }
    first_month = months[min(months)]
    floor_day = str(checkpoint["first_day"])
    floor_issues = [issue for issue in first_month["issues"] if issue["day"] == floor_day]  # type: ignore[index]
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": run_id,
        "stream": FIRE_DETECTIONS_STREAM,
        "kind": KIND,
        "tiers": list(TIERS),
        "first_day": checkpoint["first_day"],
        "last_day": checkpoint["last_day"],
        "completed_at": datetime.now(UTC).isoformat(),
        "source_postgres": source_summary,
        "parquet": parquet,
        "issue_count": len(issues),
        "issues": issues,
        "repaired_issue_count": len(repaired_issues),
        "repaired_issues": repaired_issues,
        "repair_attempts": repair_attempts,
        "modis_floor": {
            "day": floor_day,
            "source_has_data": first_month["source_postgres"]["first_data_day"] == floor_day,  # type: ignore[index]
            "issues": floor_issues,
        },
        "expected_data_days": expected_data_days,
        "expected_data_days_met": expectation_met,
        "parity": not issues and expectation_met,
        "checkpoint": str(checkpoint.get("checkpoint_path", "")),
    }


def _resolve_audit_window(args: argparse.Namespace, lane: LaneRegistration) -> tuple[date, date]:
    today = args.today
    settled_last_day = today - timedelta(days=lane.publication_lag_days)
    first_day = args.first_day or lane.history_floor
    auditable_last_day = settled_last_day if lane.writer_ceiling is None else min(settled_last_day, lane.writer_ceiling)
    if args.through is not None and lane.writer_ceiling is not None and args.through > lane.writer_ceiling:
        raise ValueError(
            f"through day {args.through} exceeds the generic writer ceiling {lane.writer_ceiling}; "
            "the direct writer owns newer days"
        )
    last_day = args.through or auditable_last_day
    if first_day < lane.history_floor:
        raise ValueError(f"first day {first_day} precedes the declared MODIS floor {lane.history_floor}")
    if last_day > settled_last_day:
        raise ValueError(
            f"through day {last_day} is not settled; for today={today} and lag={lane.publication_lag_days}, "
            f"the latest auditable day is {settled_last_day}"
        )
    if last_day < first_day:
        raise ValueError(f"audit window {first_day}..{last_day} runs backwards")
    return first_day, last_day


async def _run(args: argparse.Namespace) -> int:
    checkpoint_path = args.checkpoint.resolve()
    with _checkpoint_run_lock(
        checkpoint_path,
        attempts=args.checkpoint_lock_attempts,
        poll_seconds=args.checkpoint_lock_poll_seconds,
    ):
        return await _run_locked(args, checkpoint_path=checkpoint_path)


async def _run_locked(args: argparse.Namespace, *, checkpoint_path: Path) -> int:
    lane = resolve_lanes((FIRE_DETECTIONS_STREAM,))[0]
    today = args.today
    first_day, last_day = _resolve_audit_window(args, lane)

    manifest_path = args.manifest.resolve()
    checkpoint = _load_checkpoint(
        checkpoint_path,
        first_day=first_day,
        last_day=last_day,
        restart=args.restart_checkpoint,
    )
    checkpoint["checkpoint_path"] = str(checkpoint_path)
    months = checkpoint["months"]
    if not isinstance(months, dict):
        raise TypeError("checkpoint months is not a dict")
    run_id = f"fire-parquet-reconcile:{uuid.uuid4()}"
    database_url = settings.require_local_source_loader_database_url()
    store = ObjectStore.from_settings(settings)
    repaired_total = 0
    saved_months_revalidated = 0
    saved_parity_months_revalidated = 0

    async with local_source_loader_engine(database_url) as session_factory, session_factory() as session:
        layer_result = await session.execute(_LAYER_ID_SQL, {"name": FIRE_DETECTIONS_STREAM})
        layer_id = layer_result.scalar_one_or_none()
        await session.rollback()
        if layer_id is None:
            raise RuntimeError("geo.layers has no fire-detections row")

        for batch_first, batch_last in _month_windows(first_day, last_day):
            month = _month_key(batch_first)
            saved = months.get(month)
            if isinstance(saved, dict):
                saved_months_revalidated += 1
                if saved.get("parity") is True:
                    saved_parity_months_revalidated += 1
                print(
                    f"{month} checkpoint found parity={saved.get('parity')}; "
                    "fully re-auditing PostgreSQL and all R2 tiers/markers",
                    flush=True,
                )
            print(f"{month} audit {batch_first}..{batch_last}", flush=True)
            result, repairable, manual = await _audit_month(
                session,
                store,
                layer_id=str(layer_id),
                first_day=batch_first,
                last_day=batch_last,
                args=args,
            )
            pre_repair_issues = list(result["issues"])
            repair_reports: list[dict[str, object]] = []
            if args.repair and repairable:
                candidates = sorted(repairable)
                if args.max_repairs is not None:
                    candidates = candidates[: max(0, args.max_repairs - repaired_total)]
                for day in candidates:
                    print(f"{month} repairing {day.isoformat()} through fill_one_lane_day", flush=True)
                    report = await _repair_day(
                        session,
                        store,
                        day=day,
                        today=today,
                        run_id=run_id,
                        args=args,
                    )
                    repair_reports.append(report)
                    repaired_total += 1
                    checkpoint["updated_at"] = datetime.now(UTC).isoformat()
                    checkpoint["last_repair"] = report
                    _atomic_json(checkpoint_path, checkpoint)
                    if args.repair_delay_seconds:
                        await asyncio.sleep(args.repair_delay_seconds)
                print(f"{month} re-auditing after {len(repair_reports)} repair attempt(s)", flush=True)
                result, repairable, manual = await _audit_month(
                    session,
                    store,
                    layer_id=str(layer_id),
                    first_day=batch_first,
                    last_day=batch_last,
                    args=args,
                )
            result["repair_attempts"] = repair_reports
            result["pre_repair_issues"] = pre_repair_issues if repair_reports else []
            result["remaining_repairable_days"] = [day.isoformat() for day in sorted(repairable)]
            result["manual_intervention_days"] = [day.isoformat() for day in sorted(manual)]
            months[month] = result
            checkpoint["updated_at"] = datetime.now(UTC).isoformat()
            _atomic_json(checkpoint_path, checkpoint)
            print(
                f"{month} parity={result['parity']} source_days={result['source_postgres']['data_days']} "
                f"issues={len(result['issues'])}",  # type: ignore[arg-type,index]
                flush=True,
            )

    manifest = _final_manifest(checkpoint, run_id=run_id, expected_data_days=args.expect_data_days)
    manifest["checkpoint_revalidation"] = {
        "strategy": "full_source_and_r2_reaudit",
        "saved_months_revalidated": saved_months_revalidated,
        "saved_parity_months_revalidated": saved_parity_months_revalidated,
        "skipped_months": 0,
    }
    _atomic_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "parity": manifest["parity"],
                "first_day": manifest["first_day"],
                "last_day": manifest["last_day"],
                "data_days": manifest["source_postgres"]["data_days"],  # type: ignore[index]
                "governed_absence_days": manifest["source_postgres"]["governed_absence_days"],  # type: ignore[index]
                "detections": manifest["source_postgres"]["detection_count"],  # type: ignore[index]
                "issues": manifest["issue_count"],
                "manifest": str(manifest_path),
                "checkpoint": str(checkpoint_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if manifest["parity"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--first-day", type=_parse_day, default=None, help="Default: the lane's MODIS floor.")
    parser.add_argument(
        "--through",
        type=_parse_day,
        default=None,
        help="Default: the earlier of today's settled cutoff and the generic writer ceiling.",
    )
    parser.add_argument("--today", type=_parse_day, default=datetime.now(UTC).date())
    parser.add_argument("--repair", action="store_true", help="Rewrite repairable mismatched data days.")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--restart-checkpoint", action="store_true", help="Start a fresh exact all-history pass.")
    parser.add_argument("--expect-data-days", type=int, default=None)
    parser.add_argument("--max-repairs", type=int, default=None)
    parser.add_argument("--r2-workers", type=int, default=8, help=f"1..{MAX_R2_WORKERS}")
    parser.add_argument("--r2-attempts", type=int, default=8, help=f"1..{MAX_RETRY_ATTEMPTS}")
    parser.add_argument(
        "--r2-base-delay-seconds", type=float, default=1.0, help=f"finite 0..{MAX_RETRY_DELAY_SECONDS:g}"
    )
    parser.add_argument("--repair-attempts", type=int, default=8, help=f"1..{MAX_REPAIR_ATTEMPTS}")
    parser.add_argument(
        "--repair-delay-seconds", type=float, default=0.25, help=f"finite 0..{MAX_REPAIR_DELAY_SECONDS:g}"
    )
    parser.add_argument("--lock-polls", type=int, default=180, help=f"1..{MAX_LOCK_POLLS}")
    parser.add_argument("--lock-poll-seconds", type=float, default=10.0, help=f"finite 0..{MAX_POLL_DELAY_SECONDS:g}")
    parser.add_argument(
        "--checkpoint-lock-attempts",
        type=int,
        default=DEFAULT_CHECKPOINT_LOCK_ATTEMPTS,
        help=f"1..{MAX_CHECKPOINT_LOCK_ATTEMPTS}",
    )
    parser.add_argument(
        "--checkpoint-lock-poll-seconds",
        type=float,
        default=DEFAULT_CHECKPOINT_LOCK_POLL_SECONDS,
        help=f"finite 0..{MAX_POLL_DELAY_SECONDS:g}",
    )
    parser.add_argument(
        "--statement-timeout-seconds",
        type=int,
        default=DEFAULT_STATEMENT_TIMEOUT_SECONDS,
        help=f"1..{MAX_STATEMENT_TIMEOUT_SECONDS}",
    )
    parser.add_argument("--postgres-read-attempts", type=int, default=8, help=f"1..{MAX_RETRY_ATTEMPTS}")
    parser.add_argument(
        "--postgres-read-base-delay-seconds",
        type=float,
        default=1.0,
        help=f"finite 0..{MAX_RETRY_DELAY_SECONDS:g}",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    integer_bounds = {
        "r2-workers": (args.r2_workers, MAX_R2_WORKERS),
        "r2-attempts": (args.r2_attempts, MAX_RETRY_ATTEMPTS),
        "repair-attempts": (args.repair_attempts, MAX_REPAIR_ATTEMPTS),
        "lock-polls": (args.lock_polls, MAX_LOCK_POLLS),
        "checkpoint-lock-attempts": (args.checkpoint_lock_attempts, MAX_CHECKPOINT_LOCK_ATTEMPTS),
        "statement-timeout-seconds": (args.statement_timeout_seconds, MAX_STATEMENT_TIMEOUT_SECONDS),
        "postgres-read-attempts": (args.postgres_read_attempts, MAX_RETRY_ATTEMPTS),
    }
    invalid_integers = [
        f"{name}=1..{maximum}" for name, (value, maximum) in integer_bounds.items() if not 1 <= value <= maximum
    ]
    if invalid_integers:
        raise SystemExit(f"bounded positive integer required for {', '.join(invalid_integers)}")
    float_bounds = {
        "r2-base-delay-seconds": (args.r2_base_delay_seconds, MAX_RETRY_DELAY_SECONDS),
        "postgres-read-base-delay-seconds": (
            args.postgres_read_base_delay_seconds,
            MAX_RETRY_DELAY_SECONDS,
        ),
        "lock-poll-seconds": (args.lock_poll_seconds, MAX_POLL_DELAY_SECONDS),
        "checkpoint-lock-poll-seconds": (
            args.checkpoint_lock_poll_seconds,
            MAX_POLL_DELAY_SECONDS,
        ),
        "repair-delay-seconds": (args.repair_delay_seconds, MAX_REPAIR_DELAY_SECONDS),
    }
    invalid_floats = [
        f"{name}=finite 0..{maximum:g}"
        for name, (value, maximum) in float_bounds.items()
        if not math.isfinite(value) or not 0 <= value <= maximum
    ]
    if invalid_floats:
        raise SystemExit(f"bounded finite delay required for {', '.join(invalid_floats)}")


def main() -> int:
    args = _parser().parse_args()
    _validate_args(args)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
