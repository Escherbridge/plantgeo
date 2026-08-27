"""Exact governed-source to Parquet reconciliation for the vegetation ladder."""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from typing import TYPE_CHECKING, Final, Protocol, cast

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.db.vegetation_publication import (
    postgres_vegetation_publication_barrier,
    unlocked_vegetation_publication_barrier,
)
from agri_data_service.foundation.parquet.paths import partition_day_statuses, try_parse_partition_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, ZoomTier
from agri_data_service.pipeline.lanes.vegetation import read_vegetation_day
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.pipeline.vegetation_source import fetch_source_cell_days
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS, derive_tier
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA, VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.absence import GovernedAbsence
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

VEGETATION_KIND: Final[PartitionKind] = "observed"
BASE_ZOOM: Final[ZoomTier] = ZOOM_TIERS[-1]
DEFAULT_READ_ATTEMPTS: Final = 3
DEFAULT_PROGRESS_EVERY_DAYS: Final = 25
LAST_MONTH_OF_YEAR: Final = 12


class _Digest(Protocol):
    def update(self, value: bytes) -> object: ...


@dataclass(frozen=True, slots=True)
class ExactVegetationFinding:
    """One concrete source, ladder, schema, marker, or row disagreement."""

    day: date
    zoom: ZoomTier
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class ExactVegetationReport:
    """Measured parity for every governed data day and every required tier."""

    first_day: date
    last_day: date
    coverage_last_day: date
    source_day_count: int
    source_cell_count: int
    source_row_count: int
    parquet_base_row_count: int
    source_sha256: str
    parquet_base_sha256: str
    compared_day_count: int
    findings: tuple[ExactVegetationFinding, ...]

    @property
    def is_clean(self) -> bool:
        return not self.findings and self.source_sha256 == self.parquet_base_sha256

    def to_summary(self) -> dict[str, object]:
        return {
            "clean": self.is_clean,
            "compared_day_count": self.compared_day_count,
            "coverage_last_day": self.coverage_last_day.isoformat(),
            "finding_count": len(self.findings),
            "findings": [
                {
                    "day": finding.day.isoformat(),
                    "detail": finding.detail,
                    "kind": finding.kind,
                    "zoom": finding.zoom,
                }
                for finding in self.findings
            ],
            "first_day": self.first_day.isoformat(),
            "last_day": self.last_day.isoformat(),
            "parquet_base_row_count": self.parquet_base_row_count,
            "parquet_base_sha256": self.parquet_base_sha256,
            "source_cell_count": self.source_cell_count,
            "source_day_count": self.source_day_count,
            "source_row_count": self.source_row_count,
            "source_sha256": self.source_sha256,
        }


def _retry_read[T](
    operation: Callable[[], T],
    *,
    attempts: int,
    sleeper: Callable[[float], None],
) -> T:
    """Run one object-store read with a small bounded exponential retry."""
    if attempts < 1:
        raise ValueError("read attempts must be at least one")
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception:
            if attempt == attempts:
                raise
            sleeper(0.25 * 2 ** (attempt - 1))
    raise AssertionError("retry loop exhausted without returning or raising")


def _month_starts(first_day: date, last_day: date) -> tuple[date, ...]:
    cursor = first_day.replace(day=1)
    months: list[date] = []
    while cursor <= last_day:
        months.append(cursor)
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == LAST_MONTH_OF_YEAR
            else date(cursor.year, cursor.month + 1, 1)
        )
    return tuple(months)


def _listed_keys(store: ObjectStore, zoom: ZoomTier, first_day: date, last_day: date) -> tuple[str, ...]:
    keys: list[str] = []
    for month in _month_starts(first_day, last_day):
        keys.extend(
            store.list_partition_keys(
                VEGETATION_PLANE_STREAM,
                VEGETATION_KIND,
                zoom,
                year=month.year,
                month=month.month,
            )
        )
    return tuple(keys)


def _canonical(table: pa.Table) -> pa.Table:
    return conform_to_stream_schema(table, VEGETATION_PLANE_SCHEMA).combine_chunks()


def _table_digest(table: pa.Table) -> str:
    buffer = io.BytesIO()
    with pa.ipc.new_stream(buffer, table.schema) as writer:
        writer.write_table(table)
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _fold_digest(digest: _Digest, day: date, table: pa.Table) -> None:
    digest.update(day.isoformat().encode("ascii"))
    digest.update(b"\0")
    digest.update(bytes.fromhex(_table_digest(table)))


def _grain_duplicate_count(table: pa.Table) -> int:
    keys = [(row["cell_id"], row["observed_day"]) for row in table.select(["cell_id", "observed_day"]).to_pylist()]
    return len(keys) - len(set(keys))


def _different_columns(expected: pa.Table, actual: pa.Table) -> tuple[str, ...]:
    if expected.num_rows != actual.num_rows:
        return tuple(expected.column_names)
    return tuple(name for name in expected.column_names if not expected.column(name).equals(actual.column(name)))


def _marker_findings(  # noqa: PLR0913
    store: ObjectStore,
    *,
    day: date,
    zoom: ZoomTier,
    table: pa.Table,
    listed_keys: Sequence[str],
    read_attempts: int,
    sleeper: Callable[[float], None],
) -> tuple[ExactVegetationFinding, ...]:
    marker = _retry_read(
        partial(store.read_completion_marker, VEGETATION_PLANE_STREAM, VEGETATION_KIND, zoom, day),
        attempts=read_attempts,
        sleeper=sleeper,
    )
    if marker is None:
        return (ExactVegetationFinding(day, zoom, "missing_completion", "data exists without a completion marker"),)
    physical_parts = sum(
        1 for key in listed_keys if (parsed := try_parse_partition_path(key)) is not None and parsed.day == day
    )
    findings: list[ExactVegetationFinding] = []
    if marker.part_count != physical_parts:
        findings.append(
            ExactVegetationFinding(
                day,
                zoom,
                "completion_part_count",
                f"marker claims {marker.part_count} part(s), listing holds {physical_parts}",
            )
        )
    if marker.row_count != table.num_rows:
        findings.append(
            ExactVegetationFinding(
                day,
                zoom,
                "completion_row_count",
                f"marker claims {marker.row_count} row(s), Parquet holds {table.num_rows}",
            )
        )
    return tuple(findings)


def _absence_evidence_findings(
    store: ObjectStore,
    *,
    day: date,
    read_attempts: int,
    sleeper: Callable[[float], None],
) -> tuple[ExactVegetationFinding, ...]:
    """Require every settled absence rung to carry the same decodable evidence."""
    evidence_by_zoom: dict[ZoomTier, GovernedAbsence] = {}
    findings: list[ExactVegetationFinding] = []
    for zoom in ZOOM_TIERS:
        try:
            evidence = _retry_read(
                partial(store.read_absence, VEGETATION_PLANE_STREAM, VEGETATION_KIND, zoom, day),
                attempts=read_attempts,
                sleeper=sleeper,
            )
        except Exception as error:
            findings.append(ExactVegetationFinding(day, zoom, "absence_evidence", f"{type(error).__name__}: {error}"))
            continue
        if evidence is None:
            findings.append(ExactVegetationFinding(day, zoom, "absence_evidence", "absence marker disappeared"))
            continue
        evidence_by_zoom[zoom] = evidence
    base_evidence = evidence_by_zoom.get(BASE_ZOOM)
    if base_evidence is not None:
        for zoom in DERIVED_ZOOM_TIERS:
            evidence = evidence_by_zoom.get(zoom)
            if evidence is not None and evidence != base_evidence:
                findings.append(ExactVegetationFinding(day, zoom, "absence_evidence", "evidence differs from z13"))
    return tuple(findings)


def _compare_tier(  # noqa: PLR0913
    store: ObjectStore,
    *,
    day: date,
    zoom: ZoomTier,
    expected: pa.Table,
    listed_keys: Sequence[str],
    read_attempts: int,
    sleeper: Callable[[float], None],
) -> tuple[pa.Table | None, tuple[ExactVegetationFinding, ...]]:
    try:
        actual_raw = _retry_read(
            partial(store.read_partition, VEGETATION_PLANE_STREAM, VEGETATION_KIND, zoom, day),
            attempts=read_attempts,
            sleeper=sleeper,
        )
    except Exception as error:
        return None, (ExactVegetationFinding(day, zoom, "partition_read", f"{type(error).__name__}: {error}"),)
    if not actual_raw.schema.equals(VEGETATION_PLANE_SCHEMA.arrow_schema, check_metadata=False):
        return None, (
            ExactVegetationFinding(
                day,
                zoom,
                "schema",
                f"expected {VEGETATION_PLANE_SCHEMA.arrow_schema}, got {actual_raw.schema}",
            ),
        )
    try:
        actual = _canonical(actual_raw)
    except Exception as error:
        return None, (ExactVegetationFinding(day, zoom, "schema", f"{type(error).__name__}: {error}"),)
    try:
        findings = list(
            _marker_findings(
                store,
                day=day,
                zoom=zoom,
                table=actual,
                listed_keys=listed_keys,
                read_attempts=read_attempts,
                sleeper=sleeper,
            )
        )
    except Exception as error:
        findings = [ExactVegetationFinding(day, zoom, "completion_read", f"{type(error).__name__}: {error}")]
    duplicate_count = _grain_duplicate_count(actual) if zoom == BASE_ZOOM else 0
    if duplicate_count:
        findings.append(
            ExactVegetationFinding(day, zoom, "duplicate_grain", f"Parquet holds {duplicate_count} duplicate row(s)")
        )
    if not expected.equals(actual, check_metadata=False):
        findings.append(
            ExactVegetationFinding(
                day,
                zoom,
                "row_mismatch",
                f"expected {expected.num_rows} row(s), got {actual.num_rows}; differing columns="
                f"{','.join(_different_columns(expected, actual))}",
            )
        )
    return actual, tuple(findings)


async def _reconcile_exact_vegetation_unlocked(  # noqa: PLR0912, PLR0913, PLR0915
    session: AsyncSession,
    store: ObjectStore,
    *,
    cell_ids: Sequence[UUID],
    first_day: date,
    last_day: date,
    coverage_last_day: date,
    read_attempts: int = DEFAULT_READ_ATTEMPTS,
    progress_every_days: int = DEFAULT_PROGRESS_EVERY_DAYS,
    progress: Callable[[dict[str, object]], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> ExactVegetationReport:
    """Compare every exact source row and derived rung, plus settled governed absences."""
    if last_day < first_day:
        raise ValueError("last day precedes first day")
    if not first_day <= coverage_last_day <= last_day:
        raise ValueError("coverage last day must fall inside the reconciliation window")
    if progress_every_days < 1:
        raise ValueError("progress interval must be at least one day")

    source_cells = await fetch_source_cell_days(
        session,
        cell_ids=cell_ids,
        first_day=first_day,
        last_day=last_day,
    )
    await session.rollback()
    source_days = tuple(sorted({row.observed_day for row in source_cells}))
    source_day_set = frozenset(source_days)
    source_cell_set = frozenset(row.cell_id for row in source_cells)
    listed = {
        zoom: _retry_read(
            partial(_listed_keys, store, zoom, first_day, last_day),
            attempts=read_attempts,
            sleeper=sleeper,
        )
        for zoom in ZOOM_TIERS
    }
    findings: list[ExactVegetationFinding] = []

    statuses_by_zoom = {
        zoom: partition_day_statuses(
            layer=VEGETATION_PLANE_STREAM,
            kind=VEGETATION_KIND,
            zoom=zoom,
            first_day=first_day,
            last_day=last_day,
            keys=listed[zoom],
        )
        for zoom in ZOOM_TIERS
    }
    for zoom, statuses in statuses_by_zoom.items():
        for day, status in statuses.items():
            if day in source_day_set:
                expected_status = "data"
            elif day <= coverage_last_day:
                expected_status = "absent"
            else:
                expected_status = "missing"
            if status != expected_status:
                findings.append(
                    ExactVegetationFinding(
                        day,
                        zoom,
                        "tier_status",
                        f"expected {expected_status} from governed source; status={status}",
                    )
                )
    settled_day_count = coverage_day_count(first_day, coverage_last_day)
    for day in (first_day + timedelta(days=offset) for offset in range(settled_day_count)):
        if day not in source_day_set and all(statuses_by_zoom[zoom][day] == "absent" for zoom in ZOOM_TIERS):
            findings.extend(
                _absence_evidence_findings(
                    store,
                    day=day,
                    read_attempts=read_attempts,
                    sleeper=sleeper,
                )
            )

    source_hash = hashlib.sha256()
    parquet_hash = hashlib.sha256()
    source_rows = 0
    parquet_rows = 0
    compared = 0
    source_day_digests: dict[date, str] = {}
    for index, day in enumerate(source_days, start=1):
        expected_base = _canonical(await read_vegetation_day(session, day=day, cell_ids=cell_ids))
        await session.rollback()
        source_day_digests[day] = _table_digest(expected_base)
        source_rows += expected_base.num_rows
        _fold_digest(source_hash, day, expected_base)
        actual_base, day_findings = _compare_tier(
            store,
            day=day,
            zoom=BASE_ZOOM,
            expected=expected_base,
            listed_keys=listed[BASE_ZOOM],
            read_attempts=read_attempts,
            sleeper=sleeper,
        )
        findings.extend(day_findings)
        if actual_base is not None:
            parquet_rows += actual_base.num_rows
            _fold_digest(parquet_hash, day, actual_base)

        base_frame = cast("pl.DataFrame", pl.from_arrow(expected_base))
        for zoom in DERIVED_ZOOM_TIERS:
            expected_derived = _canonical(derive_tier(base_frame, stream=VEGETATION_PLANE_STREAM, tier=zoom).to_arrow())
            _, tier_findings = _compare_tier(
                store,
                day=day,
                zoom=zoom,
                expected=expected_derived,
                listed_keys=listed[zoom],
                read_attempts=read_attempts,
                sleeper=sleeper,
            )
            findings.extend(tier_findings)
        compared += 1
        if progress is not None and (index % progress_every_days == 0 or index == len(source_days)):
            progress(
                {
                    "compared_days": index,
                    "day": day.isoformat(),
                    "finding_count": len(findings),
                    "source_days": len(source_days),
                    "source_rows": source_rows,
                }
            )

    source_cells_after = await fetch_source_cell_days(
        session,
        cell_ids=cell_ids,
        first_day=first_day,
        last_day=last_day,
    )
    await session.rollback()
    source_days_after = frozenset(row.observed_day for row in source_cells_after)
    if source_cells_after != source_cells:
        before = {(row.cell_id, row.observed_day, row.source_release_count) for row in source_cells}
        after = {(row.cell_id, row.observed_day, row.source_release_count) for row in source_cells_after}
        changed_days = sorted({entry[1] for entry in before.symmetric_difference(after)})
        findings.extend(
            ExactVegetationFinding(
                day,
                BASE_ZOOM,
                "source_changed_during_reconciliation",
                "governed source keys or release counts changed between the opening and closing census",
            )
            for day in changed_days
        )
    for day in sorted(source_day_set | source_days_after):
        closing_table = _canonical(await read_vegetation_day(session, day=day, cell_ids=cell_ids))
        await session.rollback()
        if source_day_digests.get(day) != _table_digest(closing_table):
            findings.append(
                ExactVegetationFinding(
                    day,
                    BASE_ZOOM,
                    "source_values_changed_during_reconciliation",
                    "one or more of the 12 exported source fields changed between the opening and closing reads",
                )
            )

    return ExactVegetationReport(
        first_day=first_day,
        last_day=last_day,
        coverage_last_day=coverage_last_day,
        source_day_count=len(source_days),
        source_cell_count=len(source_cell_set),
        source_row_count=source_rows,
        parquet_base_row_count=parquet_rows,
        source_sha256=source_hash.hexdigest(),
        parquet_base_sha256=parquet_hash.hexdigest(),
        compared_day_count=compared,
        findings=tuple(findings),
    )


async def reconcile_exact_vegetation(  # noqa: PLR0913
    session: AsyncSession,
    store: ObjectStore,
    *,
    cell_ids: Sequence[UUID],
    first_day: date,
    last_day: date,
    coverage_last_day: date,
    read_attempts: int = DEFAULT_READ_ATTEMPTS,
    progress_every_days: int = DEFAULT_PROGRESS_EVERY_DAYS,
    progress: Callable[[dict[str, object]], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    barrier_held: bool = False,
) -> ExactVegetationReport:
    """Run the exact audit under the vegetation-wide source/publication stability barrier."""
    barrier = unlocked_vegetation_publication_barrier if barrier_held else postgres_vegetation_publication_barrier
    async with barrier(session):
        return await _reconcile_exact_vegetation_unlocked(
            session,
            store,
            cell_ids=cell_ids,
            first_day=first_day,
            last_day=last_day,
            coverage_last_day=coverage_last_day,
            read_attempts=read_attempts,
            progress_every_days=progress_every_days,
            progress=progress,
            sleeper=sleeper,
        )


def coverage_day_count(first_day: date, last_day: date) -> int:
    """Return the inclusive day count used by progress and production guards."""
    return (last_day - first_day) // timedelta(days=1) + 1


__all__ = [
    "DEFAULT_PROGRESS_EVERY_DAYS",
    "DEFAULT_READ_ATTEMPTS",
    "ExactVegetationFinding",
    "ExactVegetationReport",
    "coverage_day_count",
    "reconcile_exact_vegetation",
]
