"""Freeze the Boise-area governed series into a database-free, checksummed evaluation export.

Everything downstream of this module (the candidate ladder, the metrics, the decision record) reads
the export, never the warehouse. That is what makes the benchmark reproducible from immutable inputs
and what keeps a scoring run from being able to touch a governed row.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.seasonal_evidence_report import (
    EVIDENCE_WINDOW_END,
    EVIDENCE_WINDOW_START,
    ReleaseLineageRow,
    load_release_lineage,
)
from agri_data_service.execution.seasonal_row_types import (
    optional_float,
    read_only_session,
    require_bool,
    require_date,
    require_datetime,
    require_int,
    require_str,
    require_uuid,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

_DAILY_EXPORT_SQL: Final = text(load_query_sql("execution/seasonal_series_daily_export.sql"))

EXPORT_FORMAT_VERSION: Final = "seasonal-eval-export-v1"
OBSERVATION_FILE_NAME: Final = "observations.csv"
LINEAGE_FILE_NAME: Final = "release_lineage.csv"
MANIFEST_FILE_NAME: Final = "manifest.json"
MANIFEST_DIGEST_FILE_NAME: Final = "MANIFEST.sha256"

OBSERVATION_COLUMNS: Final[tuple[str, ...]] = (
    "cell_key",
    "signal_name",
    "support_key",
    "observed_date",
    "normalized_value",
    "normalized_unit",
    "is_observed",
    "quality_flag",
    "data_available_at",
    "source_key",
    "source_release_id",
    "source_payload_checksum",
    "source_version",
    "transform_version",
)

LINEAGE_COLUMNS: Final[tuple[str, ...]] = (
    "source_key",
    "source_release_id",
    "source_version",
    "transform_version",
    "payload_checksum",
    "validation_state",
    "schema_version",
    "license_snapshot_checksum",
    "retrieved_at",
    "release_data_available_at",
    "contributed_row_count",
    "first_observed_date",
    "last_observed_date",
    "earliest_data_available_at",
    "latest_data_available_at",
)


@dataclass(frozen=True)
class ExportScope:
    """What one freeze covers: its identity, its cells and its half-open observation window."""

    export_key: str
    frozen_at: datetime
    cell_keys: tuple[str, ...]
    window_start: datetime = EVIDENCE_WINDOW_START
    window_end: datetime = EVIDENCE_WINDOW_END


class SeasonalExportError(RuntimeError):
    """The export could not be frozen because the governed selection failed a stated precondition."""


@dataclass(frozen=True)
class ExportObservation:
    """One deduplicated governed observation day."""

    cell_key: str
    signal_name: str
    support_key: str
    observed_date: date
    normalized_value: float | None
    normalized_unit: str
    is_observed: bool
    quality_flag: str
    data_available_at: datetime
    source_key: str
    source_release_id: UUID
    source_payload_checksum: str
    source_version: str
    transform_version: str

    @property
    def series_key(self) -> str:
        """The (cell, signal, support) identity this row belongs to."""
        return f"{self.cell_key}|{self.signal_name}|{self.support_key}"


@dataclass(frozen=True)
class ExportSeriesSummary:
    """Per-series row counts and availability cutoffs, recorded so leakage claims can be checked."""

    series_key: str
    cell_key: str
    signal_name: str
    support_key: str
    normalized_unit: str
    source_key: str
    observed_day_count: int
    value_count: int
    missing_value_count: int
    first_observed_date: date
    last_observed_date: date
    span_day_count: int
    gap_day_count: int
    earliest_data_available_at: datetime
    latest_data_available_at: datetime


@dataclass(frozen=True)
class SeasonalExportManifest:
    """The export's identity: what was frozen, from which releases, and with which digests."""

    format_version: str
    export_key: str
    frozen_at: datetime
    cell_keys: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    observation_row_count: int
    series: tuple[ExportSeriesSummary, ...]
    source_releases: tuple[ReleaseLineageRow, ...]
    known_missing_inputs: tuple[str, ...]
    file_checksums: tuple[tuple[str, str], ...]
    # Set only when the manifest was read back off disk. `read_manifest` cannot rebuild every field
    # it digests, so recomputing there would produce a plausible digest that matches no export -- the
    # exact failure mode this field exists to prevent, caught after it reached persisted receipts.
    recorded_checksum: str | None = None

    def to_canonical_json(self) -> str:
        """Render the manifest deterministically: sorted keys, ISO-8601 UTC, no float rendering."""
        document = {
            "format_version": self.format_version,
            "export_key": self.export_key,
            "frozen_at": self.frozen_at.astimezone(UTC).isoformat(),
            "cell_keys": list(self.cell_keys),
            "window_start": self.window_start.astimezone(UTC).isoformat(),
            "window_end": self.window_end.astimezone(UTC).isoformat(),
            "observation_row_count": self.observation_row_count,
            "known_missing_inputs": list(self.known_missing_inputs),
            "series": [
                {
                    "series_key": summary.series_key,
                    "cell_key": summary.cell_key,
                    "signal_name": summary.signal_name,
                    "support_key": summary.support_key,
                    "normalized_unit": summary.normalized_unit,
                    "source_key": summary.source_key,
                    "observed_day_count": summary.observed_day_count,
                    "value_count": summary.value_count,
                    "missing_value_count": summary.missing_value_count,
                    "first_observed_date": summary.first_observed_date.isoformat(),
                    "last_observed_date": summary.last_observed_date.isoformat(),
                    "span_day_count": summary.span_day_count,
                    "gap_day_count": summary.gap_day_count,
                    "earliest_data_available_at": summary.earliest_data_available_at.astimezone(UTC).isoformat(),
                    "latest_data_available_at": summary.latest_data_available_at.astimezone(UTC).isoformat(),
                }
                for summary in self.series
            ],
            "source_releases": [
                {
                    "source_key": release.source_key,
                    "source_release_id": str(release.source_release_id),
                    "source_version": release.source_version,
                    "transform_version": release.transform_version,
                    "payload_checksum": release.payload_checksum,
                    "validation_state": release.validation_state,
                    "schema_version": release.schema_version,
                    "license_snapshot_checksum": release.license_snapshot_checksum,
                    "retrieved_at": release.retrieved_at.astimezone(UTC).isoformat(),
                    "release_data_available_at": release.release_data_available_at.astimezone(UTC).isoformat(),
                    "contributed_row_count": release.contributed_row_count,
                }
                for release in self.source_releases
            ],
            "file_checksums": dict(self.file_checksums),
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @property
    def manifest_checksum(self) -> str:
        """The export's identity: the digest read off disk, or the one this manifest's text produces."""
        if self.recorded_checksum is not None:
            return self.recorded_checksum
        return hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest()


def render_float(value: float | None) -> str:
    """Render a float so the CSV round-trips exactly and is byte-stable between runs."""
    return "" if value is None else repr(value)


def _observation_csv(observations: Sequence[ExportObservation]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(OBSERVATION_COLUMNS)
    for observation in observations:
        writer.writerow(
            (
                observation.cell_key,
                observation.signal_name,
                observation.support_key,
                observation.observed_date.isoformat(),
                render_float(observation.normalized_value),
                observation.normalized_unit,
                "true" if observation.is_observed else "false",
                observation.quality_flag,
                observation.data_available_at.astimezone(UTC).isoformat(),
                observation.source_key,
                str(observation.source_release_id),
                observation.source_payload_checksum,
                observation.source_version,
                observation.transform_version,
            )
        )
    return buffer.getvalue()


def _lineage_csv(releases: Sequence[ReleaseLineageRow]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(LINEAGE_COLUMNS)
    for release in releases:
        writer.writerow(
            (
                release.source_key,
                str(release.source_release_id),
                release.source_version,
                release.transform_version,
                release.payload_checksum,
                release.validation_state,
                release.schema_version,
                release.license_snapshot_checksum,
                release.retrieved_at.astimezone(UTC).isoformat(),
                release.release_data_available_at.astimezone(UTC).isoformat(),
                str(release.contributed_row_count),
                release.first_observed_date.isoformat(),
                release.last_observed_date.isoformat(),
                release.earliest_data_available_at.astimezone(UTC).isoformat(),
                release.latest_data_available_at.astimezone(UTC).isoformat(),
            )
        )
    return buffer.getvalue()


def summarize_series(observations: Sequence[ExportObservation]) -> tuple[ExportSeriesSummary, ...]:
    """Group the frozen rows into per-series summaries with their availability cutoffs."""
    grouped: dict[str, list[ExportObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.series_key, []).append(observation)
    summaries: list[ExportSeriesSummary] = []
    for series_key in sorted(grouped):
        rows = sorted(grouped[series_key], key=lambda item: item.observed_date)
        first = rows[0]
        span = (rows[-1].observed_date - rows[0].observed_date).days + 1
        values = [row for row in rows if row.normalized_value is not None]
        summaries.append(
            ExportSeriesSummary(
                series_key=series_key,
                cell_key=first.cell_key,
                signal_name=first.signal_name,
                support_key=first.support_key,
                normalized_unit=first.normalized_unit,
                source_key=first.source_key,
                observed_day_count=len(rows),
                value_count=len(values),
                missing_value_count=len(rows) - len(values),
                first_observed_date=rows[0].observed_date,
                last_observed_date=rows[-1].observed_date,
                span_day_count=span,
                gap_day_count=span - len(rows),
                earliest_data_available_at=min(row.data_available_at for row in rows),
                latest_data_available_at=max(row.data_available_at for row in rows),
            )
        )
    return tuple(summaries)


def known_missing_inputs(summaries: Sequence[ExportSeriesSummary]) -> tuple[str, ...]:
    """Name every series with a calendar gap or a null value, so partial stays visibly partial."""
    notes: list[str] = []
    for summary in summaries:
        if summary.gap_day_count:
            notes.append(f"{summary.series_key}: {summary.gap_day_count} calendar day(s) with no observation")
        if summary.missing_value_count:
            notes.append(f"{summary.series_key}: {summary.missing_value_count} observation(s) with a null value")
    return tuple(notes)


async def load_export_observations(
    session: AsyncSession,
    cell_keys: Sequence[str],
    window_start: datetime,
    window_end: datetime,
) -> tuple[ExportObservation, ...]:
    """Read one deterministic row per (cell, signal, support, UTC day)."""
    result = await session.execute(
        _DAILY_EXPORT_SQL,
        {"cell_keys": list(cell_keys), "window_start": window_start, "window_end": window_end},
    )
    return tuple(
        ExportObservation(
            cell_key=require_str(row["cell_key"], "cell_key"),
            signal_name=require_str(row["signal_name"], "signal_name"),
            support_key=require_str(row["support_key"], "support_key"),
            observed_date=require_date(row["observed_date"], "observed_date"),
            normalized_value=optional_float(row["normalized_value"], "normalized_value"),
            normalized_unit=require_str(row["normalized_unit"], "normalized_unit"),
            is_observed=require_bool(row["is_observed"], "is_observed"),
            quality_flag=require_str(row["quality_flag"], "quality_flag"),
            data_available_at=require_datetime(row["data_available_at"], "data_available_at"),
            source_key=require_str(row["source_key"], "source_key"),
            source_release_id=require_uuid(row["source_release_id"], "source_release_id"),
            source_payload_checksum=require_str(row["source_payload_checksum"], "source_payload_checksum"),
            source_version=require_str(row["source_version"], "source_version"),
            transform_version=require_str(row["transform_version"], "transform_version"),
        )
        for row in result.mappings().all()
    )


def build_manifest(
    scope: ExportScope,
    observations: Sequence[ExportObservation],
    releases: Sequence[ReleaseLineageRow],
) -> tuple[SeasonalExportManifest, str, str]:
    """Return the manifest plus the exact observation and lineage CSV texts it digests."""
    if not observations:
        raise SeasonalExportError("the governed selection returned no observation; refusing to freeze an empty export")
    observation_text = _observation_csv(observations)
    lineage_text = _lineage_csv(releases)
    summaries = summarize_series(observations)
    manifest = SeasonalExportManifest(
        format_version=EXPORT_FORMAT_VERSION,
        export_key=scope.export_key,
        frozen_at=scope.frozen_at,
        cell_keys=tuple(scope.cell_keys),
        window_start=scope.window_start,
        window_end=scope.window_end,
        observation_row_count=len(observations),
        series=summaries,
        source_releases=tuple(releases),
        known_missing_inputs=known_missing_inputs(summaries),
        file_checksums=(
            (OBSERVATION_FILE_NAME, hashlib.sha256(observation_text.encode("utf-8")).hexdigest()),
            (LINEAGE_FILE_NAME, hashlib.sha256(lineage_text.encode("utf-8")).hexdigest()),
        ),
    )
    return manifest, observation_text, lineage_text


def write_export(
    destination: Path,
    manifest: SeasonalExportManifest,
    observation_text: str,
    lineage_text: str,
) -> Path:
    """Write the frozen export and its digest file; refuse to overwrite an existing export."""
    if destination.exists() and any(destination.iterdir()):
        raise SeasonalExportError(f"{destination} already holds an export; a frozen export is never overwritten")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / OBSERVATION_FILE_NAME).write_text(observation_text, encoding="utf-8", newline="")
    (destination / LINEAGE_FILE_NAME).write_text(lineage_text, encoding="utf-8", newline="")
    manifest_text = manifest.to_canonical_json()
    (destination / MANIFEST_FILE_NAME).write_text(manifest_text, encoding="utf-8", newline="")
    (destination / MANIFEST_DIGEST_FILE_NAME).write_text(
        f"{manifest.manifest_checksum}  {MANIFEST_FILE_NAME}\n", encoding="utf-8", newline=""
    )
    return destination


async def freeze_export(
    database_url: str,
    destination: Path,
    scope: ExportScope,
) -> SeasonalExportManifest:
    """Read the governed selection read-only and write the frozen, checksummed export."""
    async with read_only_session(database_url) as session:
        observations = await load_export_observations(session, scope.cell_keys, scope.window_start, scope.window_end)
        releases = await load_release_lineage(session, scope.cell_keys, scope.window_start, scope.window_end)
    manifest, observation_text, lineage_text = build_manifest(scope, observations, releases)
    write_export(destination, manifest, observation_text, lineage_text)
    return manifest


def verify_export(destination: Path) -> SeasonalExportManifest:
    """Recompute every digest in a frozen export and fail loudly on any mismatch."""
    manifest_text = (destination / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
    recorded_digest = (destination / MANIFEST_DIGEST_FILE_NAME).read_text(encoding="utf-8").split()[0]
    actual_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    if actual_digest != recorded_digest:
        raise SeasonalExportError(f"manifest digest mismatch: recorded {recorded_digest}, recomputed {actual_digest}")
    document: object = json.loads(manifest_text)
    if not isinstance(document, dict):
        raise SeasonalExportError("manifest is not a JSON object")
    checksums: object = document.get("file_checksums")
    if not isinstance(checksums, dict):
        raise SeasonalExportError("manifest carries no file_checksums object")
    for name, expected in sorted(checksums.items()):
        if not isinstance(name, str) or not isinstance(expected, str):
            raise SeasonalExportError("file_checksums must map file name to hex digest")
        payload = (destination / name).read_text(encoding="utf-8")
        recomputed = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if recomputed != expected:
            raise SeasonalExportError(f"{name} digest mismatch: recorded {expected}, recomputed {recomputed}")
    return read_manifest(destination)


def read_manifest(destination: Path) -> SeasonalExportManifest:
    """Reconstruct the typed manifest from a frozen export directory, carrying its recorded digest."""
    manifest_text = (destination / MANIFEST_FILE_NAME).read_text(encoding="utf-8")
    document: object = json.loads(manifest_text)
    if not isinstance(document, dict):
        raise SeasonalExportError("manifest is not a JSON object")
    return SeasonalExportManifest(
        recorded_checksum=hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
        format_version=require_str(document.get("format_version"), "format_version"),
        export_key=require_str(document.get("export_key"), "export_key"),
        frozen_at=datetime.fromisoformat(require_str(document.get("frozen_at"), "frozen_at")),
        cell_keys=tuple(_require_str_list(document.get("cell_keys"), "cell_keys")),
        window_start=datetime.fromisoformat(require_str(document.get("window_start"), "window_start")),
        window_end=datetime.fromisoformat(require_str(document.get("window_end"), "window_end")),
        observation_row_count=require_int(document.get("observation_row_count"), "observation_row_count"),
        series=(),
        source_releases=(),
        known_missing_inputs=tuple(_require_str_list(document.get("known_missing_inputs"), "known_missing_inputs")),
        file_checksums=(),
    )


def _require_str_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise SeasonalExportError(f"{field} must be a list")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SeasonalExportError(f"{field} must hold only strings")
        items.append(item)
    return items


def load_observations_csv(destination: Path) -> tuple[ExportObservation, ...]:
    """Read a frozen export's observation rows back into typed records."""
    text_payload = (destination / OBSERVATION_FILE_NAME).read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text_payload, newline=""))
    observations: list[ExportObservation] = []
    for row in reader:
        raw_value = row["normalized_value"]
        observations.append(
            ExportObservation(
                cell_key=row["cell_key"],
                signal_name=row["signal_name"],
                support_key=row["support_key"],
                observed_date=date.fromisoformat(row["observed_date"]),
                normalized_value=None if raw_value == "" else float(raw_value),
                normalized_unit=row["normalized_unit"],
                is_observed=row["is_observed"] == "true",
                quality_flag=row["quality_flag"],
                data_available_at=datetime.fromisoformat(row["data_available_at"]),
                source_key=row["source_key"],
                source_release_id=require_uuid(row["source_release_id"], "source_release_id"),
                source_payload_checksum=row["source_payload_checksum"],
                source_version=row["source_version"],
                transform_version=row["transform_version"],
            )
        )
    return tuple(observations)
