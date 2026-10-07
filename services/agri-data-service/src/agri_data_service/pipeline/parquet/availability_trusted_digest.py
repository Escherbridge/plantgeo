"""Upgrade manifest-trusted availability days to digested rows by hashing the parts their markers count.

A branch on the physical-ladder reconciler: same evidence and document builders, a different day
selection and per-rung check. Never writes Parquet, completion markers or `_LATEST.json`. See
`AGENTS.md` in this directory, "Digesting manifest-trusted days".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, cast

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.paths import partition_path, try_parse_partition_path
from agri_data_service.pipeline.parquet.availability_documents import AvailabilityConfig, EvidenceReceipt
from agri_data_service.pipeline.parquet.availability_index import _serialize_generation, read_latest_availability
from agri_data_service.pipeline.parquet.availability_primitives import (
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    _format_datetime,
)
from agri_data_service.pipeline.parquet.availability_reconciliation import (
    MAX_RECONCILIATION_DAYS,
    AvailabilityReconciliationError,
    ReconciliationCompilation,
    _compile_publication,
    _ladder_evidence,
    _PhysicalRung,
)
from agri_data_service.pipeline.parquet.objectstore import availability_lane_root

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.availability_documents import AvailabilityIndex, AvailabilityRow
    from agri_data_service.pipeline.parquet.availability_evidence import TypedEvidenceArtifact
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.objectstore import CompletionMarkerRead, ObjectStore, ReadPartReceipt

MAX_TRUSTED_DIGEST_DAYS: Final = MAX_RECONCILIATION_DAYS

DayOutcome = Literal["ready", "refused", "skipped"]
RungOutcome = Literal["verified", "refused", "not_checked"]


@dataclass(frozen=True, slots=True)
class IndexCacheLimits:
    """The serving process's per-generation cache ceilings, passed in so this module stays below serving."""

    generation_bytes: int
    rows: int


@dataclass(frozen=True, slots=True)
class TrustedRungReport:
    """What one rung of one candidate day was found to hold, and whether it passed."""

    rung: int
    outcome: RungOutcome
    reason: str | None = None
    detail: str | None = None
    indexed_row_count: int | None = None
    marker_row_count: int | None = None
    marker_part_count: int | None = None
    physical_part_count: int | None = None
    parquet_row_count: int | None = None
    bytes_downloaded: int = 0

    def to_wire(self) -> dict[str, object]:
        """Return the JSON report projection."""
        return {
            "bytes_downloaded": self.bytes_downloaded,
            "detail": self.detail,
            "indexed_row_count": self.indexed_row_count,
            "marker_part_count": self.marker_part_count,
            "marker_row_count": self.marker_row_count,
            "outcome": self.outcome,
            "parquet_row_count": self.parquet_row_count,
            "physical_part_count": self.physical_part_count,
            "reason": self.reason,
            "rung": self.rung,
        }


@dataclass(frozen=True, slots=True)
class TrustedDayReport:
    """One day's outcome: ready to publish, refused by a failed check, or skipped as not wholly trusted."""

    day: date
    outcome: DayOutcome
    reason: str | None
    rungs: tuple[TrustedRungReport, ...] = ()
    #: For a skipped day, the held shape per rung (`z13=manifest_trusted ...`), so a mixed ladder is diagnosable.
    held_shape: str | None = None

    def to_wire(self) -> dict[str, object]:
        """Return the JSON report projection."""
        return {
            "day": self.day.isoformat(),
            "held_shape": self.held_shape,
            "outcome": self.outcome,
            "reason": self.reason,
            "rungs": [rung.to_wire() for rung in self.rungs],
        }


@dataclass(frozen=True, slots=True)
class IndexSizeProjection:
    """Generation size now, after this run, and after every trusted row of the lane is digested."""

    head_generation_bytes: int
    head_rows: int
    after_run_generation_bytes: int
    trusted_rows_after_run: int
    full_history_generation_bytes: int
    parts_per_rung_assumed: int
    limits: IndexCacheLimits | None

    def to_wire(self) -> dict[str, object]:
        """Return the JSON report projection, with the cache verdicts when limits were supplied."""
        wire: dict[str, object] = {
            "after_run_generation_bytes": self.after_run_generation_bytes,
            "full_history_generation_bytes_estimate": self.full_history_generation_bytes,
            "full_history_parts_per_rung_assumed": self.parts_per_rung_assumed,
            "head_generation_bytes": self.head_generation_bytes,
            "head_rows": self.head_rows,
            "trusted_rows_after_run": self.trusted_rows_after_run,
        }
        if self.limits is not None:
            # Rows never change here: a digest adds receipts INSIDE an existing (day, rung) row.
            fits_rows = self.head_rows <= self.limits.rows
            wire["cache_generation_byte_limit"] = self.limits.generation_bytes
            wire["cache_row_limit"] = self.limits.rows
            wire["fits_cache_after_run"] = fits_rows and self.after_run_generation_bytes <= self.limits.generation_bytes
            wire["fits_cache_full_history"] = (
                fits_rows and self.full_history_generation_bytes <= self.limits.generation_bytes
            )
        return wire


@dataclass(frozen=True, slots=True)
class TrustedDigestCompilation:
    """The per-day report, the head it was audited against, and the publication when any day is ready."""

    lane: str
    kind: PartitionKind
    start_day: date
    end_day: date
    head_generation_key: str
    head_generation_sha256: str
    head_pointer_sha256: str
    published_at: datetime
    days: tuple[TrustedDayReport, ...]
    publication: ReconciliationCompilation | None
    index_size: IndexSizeProjection

    @property
    def document_sha256(self) -> str | None:
        """Return the publication digest an operator pins, or `None` when no day is ready."""
        return None if self.publication is None else self.publication.document_sha256

    def report(self) -> dict[str, object]:
        """Return the whole JSON report: per day and rung, totals, pins and the index-size verdict."""
        rungs = [rung for day in self.days for rung in day.rungs]
        skipped: dict[str, int] = {}
        for day in self.days:
            if day.outcome == "skipped" and day.reason is not None:
                skipped[day.reason] = skipped.get(day.reason, 0) + 1
        return {
            "days": [day.to_wire() for day in self.days],
            "document_sha256": self.document_sha256,
            "end": self.end_day.isoformat(),
            "head_generation_key": self.head_generation_key,
            "head_generation_sha256": self.head_generation_sha256,
            "head_pointer_sha256": self.head_pointer_sha256,
            "index_size": self.index_size.to_wire(),
            "kind": self.kind,
            "lane": self.lane,
            "published_at": _format_datetime(self.published_at),
            "start": self.start_day.isoformat(),
            "totals": {
                "bytes_downloaded": sum(rung.bytes_downloaded for rung in rungs),
                "days_in_range": len(self.days),
                "days_ready": sum(1 for day in self.days if day.outcome == "ready"),
                "days_refused": sum(1 for day in self.days if day.outcome == "refused"),
                "days_skipped": skipped,
                "parts_hashed": sum(rung.physical_part_count or 0 for rung in rungs if rung.outcome == "verified"),
                "rows_to_publish": 0 if self.publication is None else self.publication.row_count,
                "rungs_refused": sum(1 for rung in rungs if rung.outcome == "refused"),
                "rungs_verified": sum(1 for rung in rungs if rung.outcome == "verified"),
            },
        }


class _RungRefusedError(Exception):
    """One failed check on one rung; refuses that day only."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass
class _RungObservation:
    """Counts gathered while checking one rung, kept so a refusal still reports what was seen."""

    indexed_row_count: int
    marker_row_count: int | None = None
    marker_part_count: int | None = None
    physical_part_count: int | None = None
    parquet_row_count: int | None = None
    bytes_downloaded: int = 0
    parts: tuple[ReadPartReceipt, ...] = field(default_factory=tuple)

    def report(self, rung: int, outcome: RungOutcome, refusal: _RungRefusedError | None = None) -> TrustedRungReport:
        return TrustedRungReport(
            rung=rung,
            outcome=outcome,
            reason=None if refusal is None else refusal.reason,
            detail=None if refusal is None else refusal.detail,
            indexed_row_count=self.indexed_row_count,
            marker_row_count=self.marker_row_count,
            marker_part_count=self.marker_part_count,
            physical_part_count=self.physical_part_count,
            parquet_row_count=self.parquet_row_count,
            bytes_downloaded=self.bytes_downloaded,
        )


def compile_trusted_digest(  # noqa: PLR0913 - one bounded range coordinate, its two stores and the cache limits
    store: ObjectStore,
    availability: AvailabilityStorage,
    *,
    lane: str,
    kind: PartitionKind,
    start_day: date,
    end_day: date,
    cache_limits: IndexCacheLimits | None = None,
) -> TrustedDigestCompilation:
    """Hash every part of the wholly manifest-trusted days in `[start_day, end_day]`; never write anything."""
    span = (end_day - start_day).days + 1
    if span < 1 or span > MAX_TRUSTED_DIGEST_DAYS:
        raise AvailabilityReconciliationError(
            f"trusted-digest range must contain 1..{MAX_TRUSTED_DIGEST_DAYS} days, got {span}"
        )
    index = read_latest_availability(availability, lane_root=availability_lane_root(lane, kind), expected_lane=lane)
    # THE INDEX'S OWN "NOW": one microsecond after the reviewed head, not the wall clock. Strictly
    # later than every held row (rows are published by their generation's created_at), so the
    # publisher treats the upgrade as a correction rather than a stale conflict -- and the same pinned
    # head always yields the same bytes, which is what lets `--expected-sha256` survive a recompile.
    published_at = index.pointer.created_at + timedelta(microseconds=1)
    required = index.pointer.required_rungs

    reports: list[TrustedDayReport] = []
    rows: list[AvailabilityRow] = []
    artifacts: list[TypedEvidenceArtifact] = []
    ready_days: list[date] = []
    hashed_days: list[date] = []
    # One pass over the head, not one per day: a 366-day range over a 100k-row head stays linear.
    held_by_day: dict[date, list[AvailabilityRow]] = {}
    for row in index.rows:
        if start_day <= row.day <= end_day:
            held_by_day.setdefault(row.day, []).append(row)
    for day in (start_day + timedelta(days=offset) for offset in range(span)):
        held = tuple(sorted(held_by_day.get(day, ()), key=lambda row: row.rung))
        skip_reason = _skip_reason(held, required_rungs=required)
        if skip_reason is not None:
            if skip_reason == "already_hashed":
                hashed_days.append(day)
            reports.append(
                TrustedDayReport(day=day, outcome="skipped", reason=skip_reason, held_shape=_held_shape(held))
            )
            continue
        rung_reports, physical = _check_trusted_day(store, lane=lane, kind=kind, held=held, published_at=published_at)
        if physical is None:
            refused = next(rung for rung in rung_reports if rung.outcome == "refused")
            reports.append(TrustedDayReport(day=day, outcome="refused", reason=refused.reason, rungs=rung_reports))
            continue
        day_rows, day_artifacts = _ladder_evidence(
            index,
            day=day,
            rungs=physical,
            published_at=published_at,
            lineage_receipts=_lineage_receipts(held),
        )
        rows.extend(day_rows)
        artifacts.extend(day_artifacts)
        ready_days.append(day)
        reports.append(TrustedDayReport(day=day, outcome="ready", reason=None, rungs=rung_reports))

    publication = (
        _compile_publication(
            index,
            rows=rows,
            artifacts=artifacts,
            candidate_days=ready_days,
            blessed_days=hashed_days,
        )
        if rows
        else None
    )
    verified = [rung for report in reports for rung in report.rungs if rung.outcome == "verified"]
    parts_per_rung = (
        max(1, math.ceil(sum(rung.physical_part_count or 0 for rung in verified) / len(verified))) if verified else 1
    )
    return TrustedDigestCompilation(
        lane=lane,
        kind=kind,
        start_day=start_day,
        end_day=end_day,
        head_generation_key=index.pointer.generation_key,
        head_generation_sha256=index.pointer.generation_sha256,
        head_pointer_sha256=_pointer_sha256(index),
        published_at=published_at,
        days=tuple(reports),
        publication=publication,
        index_size=_project_index_size(
            index,
            lane=lane,
            kind=kind,
            upgraded=rows,
            parts_per_rung=parts_per_rung,
            published_at=published_at,
            limits=cache_limits,
        ),
    )


def _skip_reason(held: Sequence[AvailabilityRow], *, required_rungs: tuple[int, ...]) -> str | None:
    """Return why a day is not a candidate, or `None` when EVERY required rung is manifest-trusted."""
    if not held:
        return "not_indexed"
    if tuple(row.rung for row in held) != tuple(sorted(required_rungs)):
        return "mixed"
    if any(row.terminal_state != "published" for row in held):
        return "governed_absence" if all(row.terminal_state == "governed_absence" for row in held) else "mixed"
    provenances = {row.provenance for row in held}
    if provenances == {MANIFEST_TRUSTED_PROVENANCE}:
        return None
    if provenances == {DIGESTED_PROVENANCE}:
        return "already_hashed"
    return "mixed"


def _held_shape(held: Sequence[AvailabilityRow]) -> str | None:
    if not held:
        return None
    return " ".join(
        f"z{row.rung}={row.provenance if row.terminal_state == 'published' else row.terminal_state}" for row in held
    )


def _check_trusted_day(
    store: ObjectStore,
    *,
    lane: str,
    kind: PartitionKind,
    held: Sequence[AvailabilityRow],
    published_at: datetime,
) -> tuple[tuple[TrustedRungReport, ...], tuple[_PhysicalRung, ...] | None]:
    """Check every rung in order; the first refusal stops the day's downloads and refuses the day."""
    reports: list[TrustedRungReport] = []
    physical: list[_PhysicalRung] = []
    for position, row in enumerate(held):
        observed = _RungObservation(indexed_row_count=row.row_count)
        try:
            physical.append(
                _digest_trusted_rung(
                    store, lane=lane, kind=kind, held=row, published_at=published_at, observed=observed
                )
            )
        except _RungRefusedError as refusal:
            reports.append(observed.report(row.rung, "refused", refusal))
            reports.extend(
                TrustedRungReport(rung=rest.rung, outcome="not_checked", indexed_row_count=rest.row_count)
                for rest in held[position + 1 :]
            )
            return tuple(reports), None
        reports.append(observed.report(row.rung, "verified"))
    return tuple(reports), tuple(physical)


def _digest_trusted_rung(  # noqa: PLR0913 - one rung coordinate plus the observation it fills
    store: ObjectStore,
    *,
    lane: str,
    kind: PartitionKind,
    held: AvailabilityRow,
    published_at: datetime,
    observed: _RungObservation,
) -> _PhysicalRung:
    """Prove one trusted rung's marker is the indexed one and that its bytes match its counts, or refuse."""
    day = held.day
    zoom = cast("ZoomTier", held.rung)
    if store.absence_exists(lane, kind, zoom, day):
        raise _RungRefusedError("absence_marker_present", "an absent.json sits beside the indexed completion marker")
    marker = store.read_completion_receipt(lane, kind, zoom, day)
    if marker is None:
        raise _RungRefusedError("completion_marker_missing", "no completion marker (a re-export may be in progress)")
    observed.bytes_downloaded += marker.byte_count
    observed.marker_row_count = marker.completion.row_count
    observed.marker_part_count = marker.completion.part_count
    indexed = held.completion_receipt
    # THE BINDING CHECK: everything below is only meaningful because the marker these counts come
    # from is byte-for-byte the one the index already vouched for.
    if indexed is None or (marker.relative_path, marker.sha256) != (indexed.key, indexed.sha256):
        raise _RungRefusedError(
            "completion_receipt_mismatch",
            f"physical marker {marker.relative_path} sha256 {marker.sha256} is not the indexed receipt "
            f"{None if indexed is None else indexed.key} sha256 {None if indexed is None else indexed.sha256}",
        )
    if marker.completion.derived_empty:
        raise _RungRefusedError("derived_empty_marker", "the indexed marker is derived-empty, not a trusted data rung")
    if marker.completion.row_count != held.row_count:
        raise _RungRefusedError(
            "row_count_mismatch",
            f"marker counts {marker.completion.row_count} rows, the index holds {held.row_count}",
        )
    if marker.completion.completed_at > published_at:
        raise _RungRefusedError("marker_postdates_head", "the marker completed after the reviewed head was created")
    try:
        parts = store.read_partition_with_receipts(lane, kind, zoom, day).parts
    except Exception as error:
        raise _RungRefusedError("parts_unreadable", f"{type(error).__name__}: {error}") from error
    observed.physical_part_count = len(parts)
    observed.bytes_downloaded += sum(part.byte_count for part in parts)
    observed.parquet_row_count = sum(part.row_count for part in parts)
    # A re-export that committed while the parts were hashed would pair its new bytes with the old,
    # indexed marker. Reading the marker again brackets the part reads, so such a day is refused.
    reread = store.read_completion_receipt(lane, kind, zoom, day)
    if reread is not None:
        observed.bytes_downloaded += reread.byte_count
    if reread is None or reread.sha256 != marker.sha256:
        raise _RungRefusedError(
            "marker_changed_during_digest",
            f"the completion marker read {marker.sha256} before its parts were hashed and "
            f"{None if reread is None else reread.sha256} after",
        )
    _require_parts_match_marker(marker, parts, observed=observed)
    return _PhysicalRung(
        rung=held.rung,
        row_count=observed.parquet_row_count,
        completed_at=marker.completion.completed_at,
        data_receipts=tuple(
            EvidenceReceipt(key=part.relative_path, sha256=part.sha256)
            for part in sorted(parts, key=lambda receipt: receipt.relative_path)
        ),
        completion_receipt=indexed,
    )


def _require_parts_match_marker(
    marker: CompletionMarkerRead,
    parts: Sequence[ReadPartReceipt],
    *,
    observed: _RungObservation,
) -> None:
    if len(parts) != marker.completion.part_count:
        raise _RungRefusedError(
            "part_count_mismatch",
            f"marker claims {marker.completion.part_count} part(s), {len(parts)} were listed and read",
        )
    indexes = sorted(
        parsed.part_index for part in parts if (parsed := try_parse_partition_path(part.relative_path)) is not None
    )
    if indexes != list(range(len(parts))):
        raise _RungRefusedError("part_indexes_not_contiguous", f"part indexes {indexes} are not 0..{len(parts) - 1}")
    if observed.parquet_row_count != marker.completion.row_count:
        raise _RungRefusedError(
            "row_count_mismatch",
            f"Parquet parts hold {observed.parquet_row_count} rows, the marker counts {marker.completion.row_count}",
        )
    if marker.completion.parts:
        actual = tuple(
            (part.relative_path, part.row_count, part.byte_count, part.sha256)
            for part in sorted(parts, key=lambda receipt: receipt.relative_path)
        )
        declared = tuple(
            (part.relative_path, part.row_count, part.byte_count, part.sha256) for part in marker.completion.parts
        )
        if declared != actual:
            raise _RungRefusedError(
                "recorded_parts_mismatch", "the marker's recorded part receipts differ from the bytes"
            )


def _lineage_receipts(held: Sequence[AvailabilityRow]) -> tuple[EvidenceReceipt, ...]:
    """Cite the superseded trusted rows' typed evidence, so the upgrade names what it replaced."""
    by_key = {row.source_receipt.key: row.source_receipt for row in held}
    by_key.update((row.terminal_receipt.key, row.terminal_receipt) for row in held)
    return tuple(by_key[key] for key in sorted(by_key))


def _pointer_sha256(index: AvailabilityIndex) -> str:
    return sha256_digest(canonical_json(index.pointer.to_wire()).encode("utf-8"))


def _project_index_size(  # noqa: PLR0913 - the head, the upgrade, and the estimate's one assumption
    index: AvailabilityIndex,
    *,
    lane: str,
    kind: PartitionKind,
    upgraded: Sequence[AvailabilityRow],
    parts_per_rung: int,
    published_at: datetime,
    limits: IndexCacheLimits | None,
) -> IndexSizeProjection:
    """Serialize the generation this run would publish, and one where every trusted row is digested.

    The full-history figure is an ESTIMATE: rows outside the range get `parts_per_rung` synthetic
    receipts with real partition keys and incompressible digests, the same shape real receipts have.
    """
    replacements = {row.grain: row for row in upgraded}
    after_run = tuple(replacements.get(row.grain, row) for row in index.rows)
    full_history = tuple(
        _with_synthetic_receipts(row, lane=lane, kind=kind, parts=parts_per_rung)
        if row.provenance == MANIFEST_TRUSTED_PROVENANCE
        else row
        for row in after_run
    )
    return IndexSizeProjection(
        head_generation_bytes=index.pointer.generation_bytes,
        head_rows=index.pointer.rows,
        after_run_generation_bytes=_generation_bytes(index, after_run, created_at=published_at),
        trusted_rows_after_run=sum(1 for row in after_run if row.provenance == MANIFEST_TRUSTED_PROVENANCE),
        full_history_generation_bytes=_generation_bytes(index, full_history, created_at=published_at),
        parts_per_rung_assumed=parts_per_rung,
        limits=limits,
    )


def _with_synthetic_receipts(row: AvailabilityRow, *, lane: str, kind: PartitionKind, parts: int) -> AvailabilityRow:
    keys = [partition_path(lane, kind, cast("ZoomTier", row.rung), row.day, index) for index in range(parts)]
    return replace(
        row,
        data_receipts=tuple(EvidenceReceipt(key=key, sha256=sha256_digest(key)) for key in sorted(keys)),
    )


def _generation_bytes(index: AvailabilityIndex, rows: tuple[AvailabilityRow, ...], *, created_at: datetime) -> int:
    pointer = index.pointer
    return len(
        _serialize_generation(
            config=AvailabilityConfig(
                identity=pointer.identity,
                source_ceiling=max(row.source_ceiling for row in rows),
                bootstrap_receipt=pointer.bootstrap_receipt,
            ),
            rows=rows,
            generation_receipt_sha256=pointer.generation_receipt_sha256,
            earliest_terminal_day=min(row.day for row in rows),
            latest_terminal_day=max(row.day for row in rows),
            prior_generation_key=pointer.generation_key,
            prior_generation_sha256=pointer.generation_sha256,
            created_at=created_at,
        )
    )


__all__ = [
    "MAX_TRUSTED_DIGEST_DAYS",
    "IndexCacheLimits",
    "IndexSizeProjection",
    "TrustedDayReport",
    "TrustedDigestCompilation",
    "TrustedRungReport",
    "compile_trusted_digest",
]
