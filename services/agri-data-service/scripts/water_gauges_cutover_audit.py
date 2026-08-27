"""Read-only, full-content reconciliation for the water-gauges Parquet namespace."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    PartitionKind,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
    zoom_prefix,
)
from agri_data_service.pipeline.lanes.water_gauges import SOURCE_DAY_COUNTS_SQL, read_water_gauges_day
from agri_data_service.pipeline.parquet.gap_fill import statement_timeout
from agri_data_service.pipeline.parquet.objectstore import (
    BotoObjectStoreBackend,
    ObjectStore,
    conform_to_stream_schema,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, derive_tier
from agri_data_service.warehouse.schemas.water_gauges import (
    WATER_GAUGES_GRAIN,
    WATER_GAUGES_SCHEMA,
    WATER_GAUGES_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from agri_data_service.foundation.parquet.zoom import ZoomTier

LANE: Final = WATER_GAUGES_STREAM
KIND: Final[PartitionKind] = "observed"
TIERS: Final[tuple[ZoomTier, ...]] = (13, 9, 5, 0)
SOURCE_CENSUS = SOURCE_DAY_COUNTS_SQL
DEFAULT_BATCH_DAYS: Final = 20
MAX_BATCH_DAYS: Final = 100
DEFAULT_R2_ATTEMPTS: Final = 5
MAX_R2_ATTEMPTS: Final = 10
DEFAULT_R2_BACKOFF_SECONDS: Final = 0.5
MAX_R2_BACKOFF_SECONDS: Final = 10.0
MAX_R2_RETRY_DELAY_SECONDS: Final = 10.0
STATEMENT_TIMEOUT_SECONDS: Final = 600
MAX_SAMPLES: Final = 50


class AuditError(RuntimeError):
    """Raised when an exact read cannot be completed."""


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """One listed object in the dedicated lane namespace."""

    relative_path: str
    last_modified: datetime | None
    part_index: int | None = None


@dataclass(slots=True)
class TierInventory:
    """One tier's immutable listing snapshot."""

    zoom: ZoomTier
    parts: dict[date, list[ObjectRef]] = field(default_factory=dict)
    completions: dict[date, ObjectRef] = field(default_factory=dict)
    absences: dict[date, ObjectRef] = field(default_factory=dict)
    unrecognized: list[str] = field(default_factory=list)
    signature: str = ""

    @property
    def evidence_days(self) -> set[date]:
        """Return every day named by a recognized object."""
        return set(self.parts) | set(self.completions) | set(self.absences)


@dataclass(frozen=True, slots=True)
class TierDayRead:
    """Physical parts and marker claims read for one day and tier."""

    table: pa.Table | None
    part_count: int
    part_bytes: int
    completion: PartitionCompletion | None
    completion_bytes: int
    absence_bytes: int
    errors: tuple[str, ...]


@dataclass(slots=True)
class TierTotals:
    """Bounded-memory aggregate of physical reads for one tier."""

    rows: int = 0
    parts: int = 0
    part_bytes: int = 0
    completion_bytes: int = 0
    absence_bytes: int = 0
    completion_claimed_rows: int = 0
    valid_days: set[date] = field(default_factory=set)
    error_count: int = 0
    error_samples: list[dict[str, object]] = field(default_factory=list)
    duplicate_grain_rows: int = 0
    duplicate_grain_days: set[date] = field(default_factory=set)
    derived_exact_days: set[date] = field(default_factory=set)
    derived_empty_days: set[date] = field(default_factory=set)
    derived_mismatch_days: set[date] = field(default_factory=set)
    digest: object = field(default_factory=hashlib.sha256)


def emit(event: str, **fields: object) -> None:
    """Emit one stable operator record."""
    print(json.dumps({"event": event, **fields}, sort_keys=True, separators=(",", ":")), flush=True)


def arguments() -> argparse.Namespace:
    """Parse bounded read and checkpoint controls."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-days", type=int, default=DEFAULT_BATCH_DAYS)
    parser.add_argument("--r2-attempts", type=int, default=DEFAULT_R2_ATTEMPTS)
    parser.add_argument("--r2-backoff-seconds", type=float, default=DEFAULT_R2_BACKOFF_SECONDS)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parsed = parser.parse_args()
    if parsed.batch_days < 1 or parsed.batch_days > MAX_BATCH_DAYS:
        parser.error(f"--batch-days must be between 1 and {MAX_BATCH_DAYS}")
    if parsed.r2_attempts < 1 or parsed.r2_attempts > MAX_R2_ATTEMPTS:
        parser.error(f"--r2-attempts must be between 1 and {MAX_R2_ATTEMPTS}")
    if parsed.r2_backoff_seconds < 0 or parsed.r2_backoff_seconds > MAX_R2_BACKOFF_SECONDS:
        parser.error(f"--r2-backoff-seconds must be between 0 and {MAX_R2_BACKOFF_SECONDS:g}")
    return parsed


def retry_r2[T](operation: Callable[[], T], *, label: str, attempts: int, backoff: float) -> T:
    """Retry one bounded R2 list/get operation with capped exponential backoff."""
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
            if attempt < attempts:
                time.sleep(min(backoff * (2 ** (attempt - 1)), MAX_R2_RETRY_DELAY_SECONDS))
    raise AuditError(f"R2 {label} failed after {attempts} attempts: {errors[-1]}")


def require_payload(
    backend: BotoObjectStoreBackend,
    store: ObjectStore,
    relative_path: str,
    *,
    attempts: int,
    backoff: float,
) -> bytes:
    """Read one listed object, retrying a concurrent disappearance as well as request failures."""

    def read() -> bytes:
        payload = backend.get(store.key_for(relative_path))
        if payload is None:
            raise AuditError("listed object is absent")
        return payload

    return retry_r2(read, label=f"GET {relative_path}", attempts=attempts, backoff=backoff)


def inventory_tier(
    backend: BotoObjectStoreBackend,
    store: ObjectStore,
    zoom: ZoomTier,
    *,
    attempts: int,
    backoff: float,
) -> TierInventory:
    """Take one complete listing snapshot without crossing lane or tier prefixes."""
    prefix = store.key_for(zoom_prefix(LANE, KIND, zoom))
    listed = retry_r2(
        lambda: tuple(backend.list_objects(prefix)),
        label=f"LIST {zoom_prefix(LANE, KIND, zoom)}",
        attempts=attempts,
        backoff=backoff,
    )
    inventory = TierInventory(zoom=zoom)
    signature = hashlib.sha256()
    for item in listed:
        relative = store.relative_key(item.key)
        instant = "" if item.last_modified is None else item.last_modified.isoformat()
        signature.update(f"{relative}\0{instant}\n".encode())
        ref = ObjectRef(relative_path=relative, last_modified=item.last_modified)
        if (part := try_parse_partition_path(relative)) is not None:
            inventory.parts.setdefault(part.day, []).append(
                ObjectRef(relative_path=relative, last_modified=item.last_modified, part_index=part.part_index)
            )
        elif (marker := try_parse_completion_marker_path(relative)) is not None:
            inventory.completions[marker.day] = ref
        elif (absence := try_parse_absence_marker_path(relative)) is not None:
            inventory.absences[absence.day] = ref
        else:
            inventory.unrecognized.append(relative)
    for refs in inventory.parts.values():
        refs.sort(key=lambda item: -1 if item.part_index is None else item.part_index)
    inventory.signature = signature.hexdigest()
    return inventory


def read_tier_day(  # noqa: PLR0912, PLR0913 - report every physical object state
    backend: BotoObjectStoreBackend,
    store: ObjectStore,
    inventory: TierInventory,
    day: date,
    *,
    attempts: int,
    backoff: float,
) -> TierDayRead:
    """Read every actual part and marker payload for one listed day."""
    errors: list[str] = []
    tables: list[pa.Table] = []
    part_bytes = 0
    refs = inventory.parts.get(day, [])
    indexes = [ref.part_index for ref in refs]
    if refs and indexes != list(range(len(refs))):
        errors.append(f"part indexes are not contiguous from zero: {indexes}")
    for ref in refs:
        try:
            payload = require_payload(backend, store, ref.relative_path, attempts=attempts, backoff=backoff)
            part_bytes += len(payload)
            tables.append(pq.read_table(io.BytesIO(payload)))
        except Exception as error:
            errors.append(f"{ref.relative_path}: {type(error).__name__}: {error}")
    table: pa.Table | None = None
    if tables:
        try:
            table = tables[0] if len(tables) == 1 else pa.concat_tables(tables)
        except Exception as error:
            errors.append(f"parts cannot be concatenated: {type(error).__name__}: {error}")

    completion: PartitionCompletion | None = None
    completion_bytes = 0
    if marker_ref := inventory.completions.get(day):
        try:
            payload = require_payload(backend, store, marker_ref.relative_path, attempts=attempts, backoff=backoff)
            completion_bytes = len(payload)
            completion = PartitionCompletion.from_json_bytes(payload)
        except Exception as error:
            errors.append(f"{marker_ref.relative_path}: {type(error).__name__}: {error}")

    absence_bytes = 0
    if absence_ref := inventory.absences.get(day):
        try:
            payload = require_payload(backend, store, absence_ref.relative_path, attempts=attempts, backoff=backoff)
            absence_bytes = len(payload)
            GovernedAbsence.from_json_bytes(payload)
        except Exception as error:
            errors.append(f"{absence_ref.relative_path}: {type(error).__name__}: {error}")

    if refs and completion is None:
        errors.append("part files have no readable completion marker")
    if not refs and completion is not None:
        errors.append("completion marker has no part files")
    if refs and day in inventory.absences:
        errors.append("part files conflict with a governed absence")
    if completion is not None and day in inventory.absences:
        errors.append("completion marker conflicts with a governed absence")
    if completion is not None and completion.part_count != len(refs):
        errors.append(f"marker part_count={completion.part_count}, physical parts={len(refs)}")
    if completion is not None and table is not None and completion.row_count != table.num_rows:
        errors.append(f"marker row_count={completion.row_count}, physical rows={table.num_rows}")
    return TierDayRead(
        table=table,
        part_count=len(refs),
        part_bytes=part_bytes,
        completion=completion,
        completion_bytes=completion_bytes,
        absence_bytes=absence_bytes,
        errors=tuple(errors),
    )


def canonical_table(table: pa.Table) -> pa.Table:
    """Apply the exact registered select/cast/sort contract used by the writer."""
    return conform_to_stream_schema(table, WATER_GAUGES_SCHEMA).combine_chunks()


def table_digest(table: pa.Table) -> str:
    """Hash one canonical Arrow table independently of Parquet encoding and part boundaries."""
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, WATER_GAUGES_SCHEMA.arrow_schema) as writer:
        writer.write_table(table.combine_chunks())
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def expected_coarse_table(base: pa.Table, *, zoom: ZoomTier) -> pa.Table:
    """Recompute one coarse rung from the exact z13 bytes using the production derivation."""
    frame = pl.from_arrow(base)
    if not isinstance(frame, pl.DataFrame):
        frame = pl.DataFrame(frame)
    derived = derive_tier(frame, stream=LANE, tier=zoom)
    return canonical_table(derived.to_arrow())


def validate_table(table: pa.Table, *, day: date, zoom: ZoomTier, source: bool = False) -> tuple[pa.Table, list[str]]:
    """Validate schema, day, sort, and the tier-appropriate unique grain."""
    errors: list[str] = []
    if table.schema != WATER_GAUGES_SCHEMA.arrow_schema:
        errors.append(f"physical schema differs from registered schema: {table.schema}")
    try:
        canonical = canonical_table(table)
    except Exception as error:
        return table, [*errors, f"registered conformance failed: {type(error).__name__}: {error}"]
    if table.num_rows == 0:
        errors.append("physical table is empty")
    if set(canonical.column("observed_day").to_pylist()) != {day}:
        errors.append("observed_day values do not equal the partition day")
    if not source and not table.combine_chunks().equals(canonical):
        errors.append("physical rows are not in registered schema/sort order")

    if zoom == BASE_ZOOM_TIER:
        for column in ("site_number", "site_name"):
            if canonical.column(column).null_count:
                errors.append(f"base rung has null {column}")
        grain_columns: Sequence[str] = WATER_GAUGES_GRAIN
    else:
        for column in ("latitude", "longitude"):
            if canonical.column(column).null_count:
                errors.append(f"coarse rung has null {column}")
        for column in ("site_number", "site_name", "condition", "trend"):
            if canonical.column(column).null_count != canonical.num_rows:
                errors.append(f"coarse rung carries non-null {column}")
        grain_columns = ("longitude", "latitude", "observed_day")
    grain = list(zip(*(canonical.column(column).to_pylist() for column in grain_columns), strict=True))
    # PostgreSQL is the lossless source of truth for z13. Four measured source days contain
    # repeated identity grains, and deleting either physical row would violate exact parity.
    # Coarse rungs remain unique at their registered cell/day grain.
    if zoom != BASE_ZOOM_TIER and len(set(grain)) != canonical.num_rows:
        errors.append(f"duplicate tier grain {tuple(grain_columns)}")
    return canonical, errors


def duplicate_base_grain_rows(table: pa.Table) -> int:
    """Count source-preserved rows beyond distinct z13 identity grains."""
    grain = list(zip(*(table.column(column).to_pylist() for column in WATER_GAUGES_GRAIN), strict=True))
    return len(grain) - len(set(grain))


def add_error(totals: TierTotals, *, day: date, errors: Sequence[str]) -> None:
    """Count every error while retaining only bounded samples."""
    totals.error_count += len(errors)
    if len(totals.error_samples) < MAX_SAMPLES:
        totals.error_samples.append({"day": day.isoformat(), "errors": list(errors)})


def add_digest(digest: object, *, day: date, rows: int, sha256: str) -> None:
    """Fold one day into a stable ordered corpus hash."""
    digest.update(f"{day.isoformat()}\0{rows}\0{sha256}\n".encode())  # type: ignore[attr-defined]


def bounds(days: set[date]) -> dict[str, object]:
    """Summarize a possibly sparse day set without materializing its calendar gaps."""
    if not days:
        return {"days": 0, "first_day": None, "last_day": None, "calendar_gap_days": 0}
    first = min(days)
    last = max(days)
    return {
        "days": len(days),
        "first_day": first.isoformat(),
        "last_day": last.isoformat(),
        "calendar_gap_days": (last - first).days + 1 - len(days),
    }


def finding(days: set[date]) -> dict[str, object]:
    """Render a bounded date-set finding."""
    ordered = sorted(days)
    return {"count": len(ordered), "samples": [day.isoformat() for day in ordered[:MAX_SAMPLES]]}


def write_checkpoint(path: Path, payload: dict[str, object]) -> None:
    """Atomically persist a local progress checkpoint; no source or object-store state is changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


async def source_day_counts(session: object) -> dict[date, int]:
    """Census actual published days on the exact exporter day expression."""
    result = await session.execute(SOURCE_CENSUS)  # type: ignore[attr-defined]
    counts: dict[date, int] = {}
    for row in result.mappings():
        day = row["observed_day"]
        if not isinstance(day, date):
            raise AuditError("the published census contains an undated row group")
        counts[day] = int(row["row_count"])
    if not counts:
        raise AuditError("PostgreSQL returned no published water-gauges days")
    return counts


def progress_payload(  # noqa: PLR0913 - one durable checkpoint captures every tier
    *,
    processed: int,
    total: int,
    last_day: date,
    source_rows_read: int,
    parity_days: set[date],
    source_errors: int,
    tiers: dict[ZoomTier, TierTotals],
) -> dict[str, object]:
    """Build a compact restart/evidence checkpoint after one bounded batch."""
    return {
        "schema_version": 1,
        "status": "running",
        "lane": LANE,
        "read_only": True,
        "processed_days": processed,
        "total_days": total,
        "last_completed_day": last_day.isoformat(),
        "source_rows_read": source_rows_read,
        "exact_parity_days": len(parity_days),
        "source_error_count": source_errors,
        "tiers": {
            str(zoom): {
                "rows_read": item.rows,
                "part_bytes_read": item.part_bytes,
                "valid_data_days": len(item.valid_days),
                "error_count": item.error_count,
                "content_sha256_so_far": item.digest.hexdigest(),  # type: ignore[attr-defined]
            }
            for zoom, item in tiers.items()
        },
        "resume_note": "checkpoint is evidence only; rerun rechecks every day under a fresh read-only snapshot",
    }


async def run(  # noqa: PLR0912, PLR0915 - one snapshot must own the full reconciliation
    args: argparse.Namespace,
) -> tuple[bool, dict[str, object]]:
    """Execute the full bounded reconciliation and return its cutover verdict."""
    credentials = settings.require_object_store()
    backend = BotoObjectStoreBackend.from_credentials(credentials)
    store = ObjectStore(backend, prefix=settings.object_store_prefix)
    database_url = settings.require_local_source_loader_database_url()

    async with local_source_loader_session(database_url) as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await session.execute(statement_timeout(STATEMENT_TIMEOUT_SECONDS))
        source_counts = await source_day_counts(session)
        inventories = {
            zoom: await asyncio.to_thread(
                inventory_tier,
                backend,
                store,
                zoom,
                attempts=args.r2_attempts,
                backoff=args.r2_backoff_seconds,
            )
            for zoom in TIERS
        }
        audit_days = sorted(set(source_counts).union(*(inventory.evidence_days for inventory in inventories.values())))
        tier_totals = {zoom: TierTotals() for zoom in TIERS}
        parity_days: set[date] = set()
        source_digest = hashlib.sha256()
        z13_source_digest = hashlib.sha256()
        source_rows_read = 0
        source_error_count = 0
        source_error_samples: list[dict[str, object]] = []
        source_duplicate_grain_rows = 0
        source_duplicate_grain_days: set[date] = set()
        mismatch_count = 0
        mismatch_samples: list[dict[str, object]] = []

        for index, day in enumerate(audit_days, start=1):
            reads = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        read_tier_day,
                        backend,
                        store,
                        inventories[zoom],
                        day,
                        attempts=args.r2_attempts,
                        backoff=args.r2_backoff_seconds,
                    )
                    for zoom in TIERS
                )
            )
            canonical_by_tier: dict[ZoomTier, pa.Table] = {}
            for zoom, read in zip(TIERS, reads, strict=True):
                totals = tier_totals[zoom]
                totals.parts += read.part_count
                totals.part_bytes += read.part_bytes
                totals.completion_bytes += read.completion_bytes
                totals.absence_bytes += read.absence_bytes
                if read.completion is not None:
                    totals.completion_claimed_rows += read.completion.row_count
                errors = list(read.errors)
                if read.table is not None:
                    totals.rows += read.table.num_rows
                    try:
                        canonical, content_errors = validate_table(read.table, day=day, zoom=zoom)
                        canonical_by_tier[zoom] = canonical
                        errors.extend(content_errors)
                        if zoom == BASE_ZOOM_TIER:
                            duplicate_rows = duplicate_base_grain_rows(canonical)
                            totals.duplicate_grain_rows += duplicate_rows
                            if duplicate_rows:
                                totals.duplicate_grain_days.add(day)
                        digest = table_digest(canonical)
                        add_digest(totals.digest, day=day, rows=canonical.num_rows, sha256=digest)
                    except Exception as error:  # Arrow conversion faults are day evidence, not audit aborts
                        errors.append(f"content validation failed: {type(error).__name__}: {error}")
                if errors:
                    add_error(totals, day=day, errors=errors)
                elif read.table is not None and read.completion is not None and day not in inventories[zoom].absences:
                    totals.valid_days.add(day)

            base = canonical_by_tier.get(13)
            if base is not None and day in tier_totals[13].valid_days:
                for zoom in TIERS[1:]:
                    totals = tier_totals[zoom]
                    try:
                        expected = expected_coarse_table(base, zoom=zoom)
                    except Exception as error:
                        totals.derived_mismatch_days.add(day)
                        totals.valid_days.discard(day)
                        add_error(
                            totals,
                            day=day,
                            errors=[f"z13 derivation failed: {type(error).__name__}: {error}"],
                        )
                        continue
                    actual = canonical_by_tier.get(zoom)
                    if expected.num_rows == 0:
                        totals.derived_empty_days.add(day)
                        if actual is not None or day in inventories[zoom].evidence_days:
                            totals.derived_mismatch_days.add(day)
                            totals.valid_days.discard(day)
                            add_error(totals, day=day, errors=["expected an empty derived rung but objects exist"])
                        else:
                            totals.derived_exact_days.add(day)
                    elif actual is None:
                        totals.derived_mismatch_days.add(day)
                        totals.valid_days.discard(day)
                        add_error(
                            totals,
                            day=day,
                            errors=[f"expected {expected.num_rows} rows derived from z13 but no readable table exists"],
                        )
                    else:
                        try:
                            exact = actual.equals(expected)
                            actual_sha = table_digest(actual)
                            expected_sha = table_digest(expected)
                        except Exception as error:
                            exact = False
                            actual_sha = "unavailable"
                            expected_sha = "unavailable"
                            comparison_error = f", comparison_error={type(error).__name__}: {error}"
                        else:
                            comparison_error = ""
                        if exact:
                            totals.derived_exact_days.add(day)
                        else:
                            totals.derived_mismatch_days.add(day)
                            totals.valid_days.discard(day)
                            add_error(
                                totals,
                                day=day,
                                errors=[
                                    "coarse content differs from production z13 derivation: "
                                    f"actual_rows={actual.num_rows}, expected_rows={expected.num_rows}, "
                                    f"actual_sha256={actual_sha}, expected_sha256={expected_sha}"
                                    f"{comparison_error}"
                                ],
                            )

            if day in source_counts:
                try:
                    source = await read_water_gauges_day(session, day=day)
                    source_rows_read += source.num_rows
                    canonical_source, errors = validate_table(source, day=day, zoom=13, source=True)
                    duplicate_rows = duplicate_base_grain_rows(canonical_source)
                    source_duplicate_grain_rows += duplicate_rows
                    if duplicate_rows:
                        source_duplicate_grain_days.add(day)
                    if source.num_rows != source_counts[day]:
                        errors.append(f"exporter rows={source.num_rows}, census rows={source_counts[day]}")
                    source_sha = table_digest(canonical_source)
                    add_digest(source_digest, day=day, rows=canonical_source.num_rows, sha256=source_sha)
                except Exception as error:
                    canonical_source = None
                    errors = [f"source read/validation failed: {type(error).__name__}: {error}"]
                    source_sha = None
                if errors:
                    source_error_count += len(errors)
                    if len(source_error_samples) < MAX_SAMPLES:
                        source_error_samples.append({"day": day.isoformat(), "errors": errors})
                published = canonical_by_tier.get(13)
                if published is not None and canonical_source is not None:
                    try:
                        published_sha = table_digest(published)
                        source_matches = canonical_source.equals(published)
                    except Exception as error:
                        published_sha = None
                        source_matches = False
                        errors.append(f"source/z13 comparison failed: {type(error).__name__}: {error}")
                        source_error_count += 1
                        if len(source_error_samples) < MAX_SAMPLES:
                            source_error_samples.append({"day": day.isoformat(), "errors": [errors[-1]]})
                    if published_sha is not None:
                        add_digest(z13_source_digest, day=day, rows=published.num_rows, sha256=published_sha)
                    if not errors and source_matches:
                        parity_days.add(day)
                    else:
                        mismatch_count += 1
                        if len(mismatch_samples) < MAX_SAMPLES:
                            try:
                                differing_columns = [
                                    name
                                    for name in WATER_GAUGES_SCHEMA.column_names
                                    if not canonical_source.column(name).equals(published.column(name))
                                ]
                            except Exception:
                                differing_columns = ["<comparison unavailable>"]
                            mismatch_samples.append(
                                {
                                    "day": day.isoformat(),
                                    "source_rows": canonical_source.num_rows,
                                    "z13_rows": published.num_rows,
                                    "source_sha256": source_sha,
                                    "z13_sha256": published_sha,
                                    "differing_columns": differing_columns,
                                }
                            )
                else:
                    mismatch_count += 1
                    if len(mismatch_samples) < MAX_SAMPLES:
                        mismatch_samples.append(
                            {
                                "day": day.isoformat(),
                                "source_rows": None if canonical_source is None else canonical_source.num_rows,
                                "z13_rows": None if published is None else published.num_rows,
                            }
                        )

            if index % args.batch_days == 0 or index == len(audit_days):
                checkpoint = progress_payload(
                    processed=index,
                    total=len(audit_days),
                    last_day=day,
                    source_rows_read=source_rows_read,
                    parity_days=parity_days,
                    source_errors=source_error_count,
                    tiers=tier_totals,
                )
                write_checkpoint(args.checkpoint, checkpoint)
                emit("water_gauges_audit_checkpoint", **checkpoint)

        final_inventories = {
            zoom: await asyncio.to_thread(
                inventory_tier,
                backend,
                store,
                zoom,
                attempts=args.r2_attempts,
                backoff=args.r2_backoff_seconds,
            )
            for zoom in TIERS
        }

    source_days = set(source_counts)
    inventory_changed = {str(zoom): inventories[zoom].signature != final_inventories[zoom].signature for zoom in TIERS}
    base_days = tier_totals[13].valid_days
    tier_reports: dict[str, object] = {}
    ladder_clean = True
    for zoom in TIERS:
        inventory = inventories[zoom]
        totals = tier_totals[zoom]
        expected_days = base_days if zoom == BASE_ZOOM_TIER else base_days - totals.derived_empty_days
        missing_from_base = expected_days - totals.valid_days
        extra_vs_base = totals.valid_days - expected_days
        part_without_completion = set(inventory.parts) - set(inventory.completions)
        completion_without_parts = set(inventory.completions) - set(inventory.parts)
        conflicts = set(inventory.absences) & (set(inventory.parts) | set(inventory.completions))
        tier_clean = not any(
            (
                totals.error_count,
                inventory.unrecognized,
                missing_from_base if zoom != BASE_ZOOM_TIER else set(),
                extra_vs_base if zoom != BASE_ZOOM_TIER else set(),
                part_without_completion,
                completion_without_parts,
                conflicts,
                inventory_changed[str(zoom)],
            )
        )
        ladder_clean = ladder_clean and tier_clean
        tier_reports[str(zoom)] = {
            "clean": tier_clean,
            "inventory_signature": inventory.signature,
            "inventory_stable": not inventory_changed[str(zoom)],
            "evidence_bounds": bounds(inventory.evidence_days),
            "valid_data_bounds": bounds(totals.valid_days),
            "part_objects": sum(len(refs) for refs in inventory.parts.values()),
            "part_bytes": totals.part_bytes,
            "physical_rows": totals.rows,
            "completion_objects": len(inventory.completions),
            "completion_bytes": totals.completion_bytes,
            "completion_claimed_rows": totals.completion_claimed_rows,
            "absence_objects": len(inventory.absences),
            "absence_bytes": totals.absence_bytes,
            "content_sha256": totals.digest.hexdigest(),  # type: ignore[attr-defined]
            "missing_from_z13": finding(missing_from_base) if zoom != BASE_ZOOM_TIER else finding(set()),
            "extra_vs_z13": finding(extra_vs_base) if zoom != BASE_ZOOM_TIER else finding(set()),
            "parts_without_completion": finding(part_without_completion),
            "completion_without_parts": finding(completion_without_parts),
            "conflicts": finding(conflicts),
            "unrecognized_objects": {
                "count": len(inventory.unrecognized),
                "samples": inventory.unrecognized[:MAX_SAMPLES],
            },
            "error_count": totals.error_count,
            "error_samples": totals.error_samples,
            "duplicate_grain_rows_preserved": totals.duplicate_grain_rows,
            "duplicate_grain_days": finding(totals.duplicate_grain_days),
            "derived_exact_days": finding(totals.derived_exact_days),
            "derived_empty_days": finding(totals.derived_empty_days),
            "derived_mismatch_days": finding(totals.derived_mismatch_days),
        }

    missing_source_days = source_days - base_days
    extra_z13_days = base_days - source_days
    exact_source_clean = (
        len(parity_days) == len(source_days)
        and mismatch_count == 0
        and source_error_count == 0
        and not missing_source_days
        and source_rows_read == sum(source_counts.values())
    )
    clean = exact_source_clean and ladder_clean
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "passed" if clean else "failed",
        "clean": clean,
        "scope": {
            "lane": LANE,
            "kind": KIND,
            "tiers": list(TIERS),
            "postgres_access": "repeatable_read_read_only",
            "object_store_access": "list_and_get_only",
            "batch_days": args.batch_days,
            "r2_attempts": args.r2_attempts,
        },
        "postgres": {
            **bounds(source_days),
            "census_rows": sum(source_counts.values()),
            "exporter_rows_read": source_rows_read,
            "content_sha256": source_digest.hexdigest(),
            "error_count": source_error_count,
            "error_samples": source_error_samples,
            "duplicate_grain_rows_preserved": source_duplicate_grain_rows,
            "duplicate_grain_days": finding(source_duplicate_grain_days),
        },
        "exact_z13_parity": {
            "clean": exact_source_clean,
            "matched_days": len(parity_days),
            "matched_rows": sum(source_counts[day] for day in parity_days),
            "z13_source_subset_sha256": z13_source_digest.hexdigest(),
            "missing_postgres_days": finding(missing_source_days),
            "z13_days_beyond_postgres": finding(extra_z13_days),
            "mismatch_count": mismatch_count,
            "mismatch_samples": mismatch_samples,
        },
        "tiers": tier_reports,
        "ladder_clean": ladder_clean,
        "inventory_changed_during_audit": inventory_changed,
        "checkpoint": str(args.checkpoint),
    }
    write_checkpoint(args.checkpoint, report)
    return clean, report


async def main() -> int:
    """Run the audit, always emitting a machine-readable terminal record."""
    args = arguments()
    try:
        clean, report = await run(args)
        emit("water_gauges_cutover_audit", **report)
        return 0 if clean else 1
    except Exception as error:
        report = {
            "schema_version": 1,
            "status": "failed",
            "clean": False,
            "lane": LANE,
            "read_only": True,
            "error": f"{type(error).__name__}: {error}",
        }
        write_checkpoint(args.checkpoint, report)
        emit("water_gauges_cutover_audit", **report)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
