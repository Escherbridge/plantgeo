"""Read-only exact PostgreSQL-to-Parquet audit for the governed current-weather lane."""

from __future__ import annotations

import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Protocol, cast

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.foundation.parquet.paths import (
    PartitionDayStatus,
    PartitionKind,
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, ZoomTier
from agri_data_service.pipeline.lanes.weather_observations import read_weather_observations_day
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.parquet.tiers import derive_tier
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

WEATHER_KIND: Final[PartitionKind] = "observed"
BASE_ZOOM: Final[ZoomTier] = ZOOM_TIERS[-1]
WEATHER_REGISTRATION: Final = LANE_REGISTRY[WEATHER_OBSERVATIONS_STREAM]


class _Digest(Protocol):
    def update(self, value: bytes) -> object: ...


@dataclass(frozen=True, slots=True)
class ExactWeatherFinding:
    """One concrete scope, status, marker, schema, or row disagreement."""

    day: date
    zoom: ZoomTier
    kind: str
    detail: str

    def to_summary(self) -> dict[str, object]:
        return {"day": self.day.isoformat(), "zoom": self.zoom, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ExactWeatherReport:
    """Credential-free evidence for one exact governed-window audit."""

    governed_first_day: date
    governed_last_day: date
    excluded_prefix: Mapping[str, object]
    excluded_unsettled_tail: Mapping[str, object]
    days: tuple[Mapping[str, object], ...]
    source_row_count: int
    source_sha256: str
    closing_source_row_count: int
    closing_source_sha256: str
    object_inventory_sha256: Mapping[ZoomTier, str]
    closing_object_inventory_sha256: Mapping[ZoomTier, str]
    object_evidence_sha256: Mapping[ZoomTier, str]
    closing_object_evidence_sha256: Mapping[ZoomTier, str]
    tier_row_counts: Mapping[ZoomTier, int]
    tier_sha256: Mapping[ZoomTier, str]
    expected_tier_sha256: Mapping[ZoomTier, str]
    findings: tuple[ExactWeatherFinding, ...]

    @property
    def is_clean(self) -> bool:
        return (
            self.source_is_stable
            and self.object_plane_is_stable
            and not self.findings
            and all(self.tier_sha256.get(zoom) == self.expected_tier_sha256.get(zoom) for zoom in ZOOM_TIERS)
        )

    @property
    def source_is_stable(self) -> bool:
        """Whether independent opening and closing PostgreSQL snapshots are identical."""
        return (
            self.source_row_count == self.closing_source_row_count and self.source_sha256 == self.closing_source_sha256
        )

    @property
    def object_plane_is_stable(self) -> bool:
        """Whether opening/closing keys, canonical rows, and marker bodies are identical."""
        return all(
            self.object_inventory_sha256.get(zoom) == self.closing_object_inventory_sha256.get(zoom)
            and self.object_evidence_sha256.get(zoom) == self.closing_object_evidence_sha256.get(zoom)
            for zoom in ZOOM_TIERS
        )

    def to_summary(self) -> dict[str, object]:
        return {
            "clean": self.is_clean,
            "scope": {
                "stream": WEATHER_OBSERVATIONS_STREAM,
                "kind": WEATHER_KIND,
                "registry_floor": self.governed_first_day.isoformat(),
                "governed_last_day": self.governed_last_day.isoformat(),
                "publication_lag_days": WEATHER_REGISTRATION.publication_lag_days,
                "excluded_historical_forecast_prefix": dict(self.excluded_prefix),
                "excluded_unsettled_tail": dict(self.excluded_unsettled_tail),
            },
            "day_count": len(self.days),
            "source_row_count": self.source_row_count,
            "source_sha256": self.source_sha256,
            "source_stability": {
                "stable": self.source_is_stable,
                "opening_row_count": self.source_row_count,
                "opening_sha256": self.source_sha256,
                "closing_row_count": self.closing_source_row_count,
                "closing_sha256": self.closing_source_sha256,
            },
            "object_stability": {
                "stable": self.object_plane_is_stable,
                "tiers": {
                    str(zoom): {
                        "opening_key_sha256": self.object_inventory_sha256.get(zoom),
                        "closing_key_sha256": self.closing_object_inventory_sha256.get(zoom),
                        "opening_evidence_sha256": self.object_evidence_sha256.get(zoom),
                        "closing_evidence_sha256": self.closing_object_evidence_sha256.get(zoom),
                        "stable": (
                            self.object_inventory_sha256.get(zoom) == self.closing_object_inventory_sha256.get(zoom)
                            and self.object_evidence_sha256.get(zoom) == self.closing_object_evidence_sha256.get(zoom)
                        ),
                    }
                    for zoom in ZOOM_TIERS
                },
            },
            "tiers": {
                str(zoom): {
                    "actual_row_count": self.tier_row_counts.get(zoom, 0),
                    "actual_sha256": self.tier_sha256.get(zoom),
                    "expected_sha256": self.expected_tier_sha256.get(zoom),
                }
                for zoom in ZOOM_TIERS
            },
            "days": [dict(day) for day in self.days],
            "finding_count": len(self.findings),
            "findings": [finding.to_summary() for finding in self.findings],
        }


def _canonical(table: pa.Table) -> pa.Table:
    """Cast, column-select, grain-sort, and coalesce chunks before equality or hashing."""
    return conform_to_stream_schema(table, WEATHER_OBSERVATIONS_SCHEMA).combine_chunks()


def _table_sha256(table: pa.Table) -> str:
    """Hash canonical Arrow IPC bytes: row content and types, independent of Parquet encoding."""
    payload = io.BytesIO()
    with pa.ipc.new_stream(payload, table.schema) as writer:
        writer.write_table(table)
    return hashlib.sha256(payload.getvalue()).hexdigest()


def _fold_table(digest: _Digest, day: date, table: pa.Table) -> None:
    """Frame one day and its canonical content into a stable aggregate digest."""
    digest.update(day.isoformat().encode("ascii"))
    digest.update(b"\0")
    digest.update(str(table.num_rows).encode("ascii"))
    digest.update(b"\0")
    digest.update(bytes.fromhex(_table_sha256(table)))


def _key_day(key: str) -> date | None:
    for parser in (try_parse_partition_path, try_parse_absence_marker_path, try_parse_completion_marker_path):
        parsed = parser(key)
        if parsed is not None:
            return parsed.day
    return None


def _day_set_sha256(days: Iterable[date]) -> str:
    payload = "\n".join(day.isoformat() for day in sorted(set(days))).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _key_set_sha256(keys: Iterable[str]) -> str:
    payload = "\n".join(sorted(set(keys))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_error_detail(error: Exception) -> str:
    """Classify a backend failure without persisting provider text or credentials."""
    return f"{type(error).__name__}: backend detail redacted"


def _object_evidence(summary: Mapping[str, object]) -> dict[str, object]:
    """Select the physical/object claims whose canonical digest must stay stable."""
    return {
        "status": summary["status"],
        "listed_part_count": summary["listed_part_count"],
        "actual_row_count": summary["actual_row_count"],
        "actual_sha256": summary["actual_sha256"],
        "completion": summary["completion"],
        "absence_canonical_sha256": summary["absence_canonical_sha256"],
    }


def _fold_object_evidence(digest: _Digest, day: date, summary: Mapping[str, object]) -> None:
    digest.update(day.isoformat().encode("ascii"))
    digest.update(b"\0")
    digest.update(json.dumps(_object_evidence(summary), sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _expected_tiers(source: pa.Table) -> dict[ZoomTier, pa.Table]:
    expected: dict[ZoomTier, pa.Table] = {BASE_ZOOM: source}
    if source.num_rows:
        frame = cast("pl.DataFrame", pl.from_arrow(source))
        expected.update(
            {
                zoom: _canonical(derive_tier(frame, stream=WEATHER_OBSERVATIONS_STREAM, tier=zoom).to_arrow())
                for zoom in ZOOM_TIERS
                if zoom != BASE_ZOOM
            }
        )
    else:
        empty = _canonical(WEATHER_OBSERVATIONS_SCHEMA.arrow_schema.empty_table())
        expected.update({zoom: empty for zoom in ZOOM_TIERS if zoom != BASE_ZOOM})
    return expected


def _excluded_window_summary(
    keys_by_zoom: Mapping[ZoomTier, Sequence[str]],
    *,
    include: Callable[[date], bool],
    label: str,
) -> dict[str, object]:
    """Summarise object-present days outside governance without reading or comparing their rows."""
    summaries: dict[str, object] = {}
    all_days: set[date] = set()
    for zoom in ZOOM_TIERS:
        scoped_keys = tuple(
            key for key in keys_by_zoom[zoom] if (key_day := _key_day(key)) is not None and include(key_day)
        )
        days = {_key_day(key) for key in scoped_keys}
        selected = sorted(day for day in days if day is not None and include(day))
        all_days.update(selected)
        status_counts: Mapping[str, int] = {}
        calendar_day_count = 0
        if selected:
            window_statuses = partition_day_statuses(
                layer=WEATHER_OBSERVATIONS_STREAM,
                kind=WEATHER_KIND,
                zoom=zoom,
                first_day=selected[0],
                last_day=selected[-1],
                keys=scoped_keys,
            )
            status_counts = dict(sorted(Counter(window_statuses.values()).items()))
            calendar_day_count = len(window_statuses)
        summaries[str(zoom)] = {
            "object_present_day_count": len(selected),
            "calendar_day_count": calendar_day_count,
            "status_counts": status_counts,
            "first_day": selected[0].isoformat() if selected else None,
            "last_day": selected[-1].isoformat() if selected else None,
            "day_sha256": _day_set_sha256(selected),
        }
    return {
        "label": label,
        "excluded_from_postgres_reconciliation": True,
        "distinct_object_present_days": len(all_days),
        "day_sha256": _day_set_sha256(all_days),
        "tiers": summaries,
    }


def _listed_part_count(keys: Sequence[str], day: date) -> int:
    return sum(1 for key in keys if (parsed := try_parse_partition_path(key)) is not None and parsed.day == day)


def _different_columns(expected: pa.Table, actual: pa.Table) -> tuple[str, ...]:
    if expected.num_rows != actual.num_rows:
        return tuple(expected.column_names)
    return tuple(name for name in expected.column_names if not expected.column(name).equals(actual.column(name)))


def _schema_is_read_compatible(actual: pa.Schema, expected: pa.Schema) -> bool:
    """Accept the registered types/order and any physically stricter field nullability."""
    if actual.names != expected.names:
        return False
    return all(
        actual_field.type.equals(expected_field.type) and not (actual_field.nullable and not expected_field.nullable)
        for actual_field, expected_field in zip(actual, expected, strict=True)
    )


def _inspect_tier(  # noqa: PLR0912, PLR0913, PLR0915 - one exact stream-day-tier comparison owns these coordinates
    store: ObjectStore,
    *,
    day: date,
    zoom: ZoomTier,
    status: PartitionDayStatus,
    listed_keys: Sequence[str],
    expected: pa.Table,
) -> tuple[dict[str, object], pa.Table | None, tuple[ExactWeatherFinding, ...], str | None]:
    """Read one rung and its marker, returning exact evidence without mutating either source."""
    findings: list[ExactWeatherFinding] = []
    part_count = _listed_part_count(listed_keys, day)
    completion_listed = any(
        (completion_path := try_parse_completion_marker_path(key)) is not None and completion_path.day == day
        for key in listed_keys
    )
    absence_listed = any(
        (absence_path := try_parse_absence_marker_path(key)) is not None and absence_path.day == day
        for key in listed_keys
    )
    completion_summary: dict[str, object] | None = None
    absence_sha256: str | None = None
    completion_error = False
    try:
        completion = store.read_completion_marker(WEATHER_OBSERVATIONS_STREAM, WEATHER_KIND, zoom, day)
    except Exception as error:
        completion_error = True
        completion = None
        findings.append(ExactWeatherFinding(day, zoom, "completion_marker", _safe_error_detail(error)))
    if completion_listed and completion is None and not completion_error:
        findings.append(ExactWeatherFinding(day, zoom, "completion_marker", "listed marker disappeared before read"))
    if not completion_listed and completion is not None:
        findings.append(
            ExactWeatherFinding(day, zoom, "completion_marker", "marker appeared after the inventory listing")
        )
    if completion is not None:
        completion_summary = {
            "part_count": completion.part_count,
            "row_count": completion.row_count,
            "completed_at": completion.completed_at.isoformat(),
            "run_id": completion.run_id,
            "canonical_sha256": hashlib.sha256(completion.to_json_bytes()).hexdigest(),
        }
        if completion.part_count != part_count:
            findings.append(
                ExactWeatherFinding(
                    day,
                    zoom,
                    "completion_part_count",
                    f"marker claims {completion.part_count} part(s), listing holds {part_count}",
                )
            )
    absence_error = False
    try:
        absence = store.read_absence(WEATHER_OBSERVATIONS_STREAM, WEATHER_KIND, zoom, day)
    except Exception as error:
        absence_error = True
        absence = None
        findings.append(ExactWeatherFinding(day, zoom, "absence_marker", _safe_error_detail(error)))
    if absence_listed and absence is None and not absence_error:
        findings.append(ExactWeatherFinding(day, zoom, "absence_marker", "listed marker disappeared before read"))
    if not absence_listed and absence is not None:
        findings.append(ExactWeatherFinding(day, zoom, "absence_marker", "marker appeared after the inventory listing"))
    if absence is not None:
        absence_sha256 = hashlib.sha256(absence.to_json_bytes()).hexdigest()

    actual: pa.Table | None = None
    if part_count:
        try:
            raw = store.read_partition(WEATHER_OBSERVATIONS_STREAM, WEATHER_KIND, zoom, day)
            if not _schema_is_read_compatible(raw.schema, WEATHER_OBSERVATIONS_SCHEMA.arrow_schema):
                findings.append(
                    ExactWeatherFinding(
                        day,
                        zoom,
                        "schema",
                        f"expected {WEATHER_OBSERVATIONS_SCHEMA.arrow_schema}, got {raw.schema}",
                    )
                )
            actual = _canonical(raw)
        except Exception as error:
            findings.append(ExactWeatherFinding(day, zoom, "partition_read", _safe_error_detail(error)))
    elif status == "absent" or (status == "missing" and expected.num_rows == 0):
        actual = _canonical(WEATHER_OBSERVATIONS_SCHEMA.arrow_schema.empty_table())

    if completion is not None and actual is not None and completion.row_count != actual.num_rows:
        findings.append(
            ExactWeatherFinding(
                day,
                zoom,
                "completion_row_count",
                f"marker claims {completion.row_count} row(s), canonical rows hold {actual.num_rows}",
            )
        )
    if completion is not None and part_count == 0:
        findings.append(
            ExactWeatherFinding(day, zoom, "stray_completion", "completion marker exists without any part file")
        )
    if actual is not None and not expected.equals(actual, check_metadata=False):
        findings.append(
            ExactWeatherFinding(
                day,
                zoom,
                "row_mismatch",
                f"expected {expected.num_rows} row(s), got {actual.num_rows}; differing columns="
                f"{','.join(_different_columns(expected, actual))}",
            )
        )
    summary: dict[str, object] = {
        "status": status,
        "listed_part_count": part_count,
        "expected_row_count": expected.num_rows,
        "expected_sha256": _table_sha256(expected),
        "actual_row_count": actual.num_rows if actual is not None else None,
        "actual_sha256": _table_sha256(actual) if actual is not None else None,
        "matches_expected": actual is not None and expected.equals(actual, check_metadata=False),
        "completion": completion_summary,
        "absence_canonical_sha256": absence_sha256,
    }
    return summary, actual, tuple(findings), absence_sha256


async def audit_exact_weather_observations(  # noqa: PLR0915
    session: AsyncSession,
    store: ObjectStore,
    *,
    layer_id: str,
    governed_last_day: date,
) -> ExactWeatherReport:
    """Audit registry-floor through `governed_last_day`; inventory but exclude every outside day."""
    first_day = WEATHER_REGISTRATION.history_floor
    if governed_last_day < first_day:
        raise ValueError("governed last day precedes the weather-observations registry floor")
    if not layer_id.strip():
        raise ValueError("weather-observations exact audit requires a resolved layer_id")

    keys_by_zoom = {
        zoom: store.list_partition_keys(WEATHER_OBSERVATIONS_STREAM, WEATHER_KIND, zoom) for zoom in ZOOM_TIERS
    }
    excluded_prefix = _excluded_window_summary(
        keys_by_zoom,
        include=lambda day: day < first_day,
        label="pre-registry Historical Forecast prefix; not current weather-observations governance",
    )
    excluded_tail = _excluded_window_summary(
        keys_by_zoom,
        include=lambda day: day > governed_last_day,
        label="post-cutoff unsettled tail; forward ingestion evidence only",
    )
    statuses = {
        zoom: partition_day_statuses(
            layer=WEATHER_OBSERVATIONS_STREAM,
            kind=WEATHER_KIND,
            zoom=zoom,
            first_day=first_day,
            last_day=governed_last_day,
            keys=keys_by_zoom[zoom],
        )
        for zoom in ZOOM_TIERS
    }

    source_digest = hashlib.sha256()
    expected_digests = {zoom: hashlib.sha256() for zoom in ZOOM_TIERS}
    actual_digests = {zoom: hashlib.sha256() for zoom in ZOOM_TIERS}
    opening_object_digests = {zoom: hashlib.sha256() for zoom in ZOOM_TIERS}
    tier_rows = Counter[ZoomTier]()
    source_rows = 0
    source_day_digests: dict[date, str] = {}
    findings: list[ExactWeatherFinding] = []
    day_summaries: list[Mapping[str, object]] = []
    span = (governed_last_day - first_day).days + 1
    for day in (first_day + timedelta(days=offset) for offset in range(span)):
        source = _canonical(await read_weather_observations_day(session, day=day, layer_id=layer_id))
        source_rows += source.num_rows
        source_day_digests[day] = _table_sha256(source)
        _fold_table(source_digest, day, source)
        expected_statuses: dict[ZoomTier, PartitionDayStatus] = {
            zoom: "data" if source.num_rows else ("absent" if zoom == BASE_ZOOM else "missing") for zoom in ZOOM_TIERS
        }
        expected_by_zoom = _expected_tiers(source)

        tier_summaries: dict[str, object] = {}
        for zoom in ZOOM_TIERS:
            status = statuses[zoom][day]
            expected_status = expected_statuses[zoom]
            if status != expected_status:
                findings.append(
                    ExactWeatherFinding(
                        day,
                        zoom,
                        "tier_status",
                        f"PostgreSQL expects {expected_status}, object-store status is {status}",
                    )
                )
            expected = expected_by_zoom[zoom]
            _fold_table(expected_digests[zoom], day, expected)
            summary, actual, tier_findings, _ = _inspect_tier(
                store,
                day=day,
                zoom=zoom,
                status=status,
                listed_keys=keys_by_zoom[zoom],
                expected=expected,
            )
            findings.extend(tier_findings)
            tier_summaries[str(zoom)] = summary
            _fold_object_evidence(opening_object_digests[zoom], day, summary)
            if actual is not None:
                tier_rows[zoom] += actual.num_rows
                _fold_table(actual_digests[zoom], day, actual)
        day_summaries.append(
            {
                "day": day.isoformat(),
                "postgres_row_count": source.num_rows,
                "postgres_sha256": _table_sha256(source),
                "expected_status": "data" if source.num_rows else "governed_absence_at_base",
                "expected_tier_statuses": {str(zoom): expected_statuses[zoom] for zoom in ZOOM_TIERS},
                "tiers": tier_summaries,
            }
        )

    # End the opening snapshot before re-reading the source. Re-reading in the same REPEATABLE READ
    # transaction would only prove that PostgreSQL snapshots are repeatable, not that the governed
    # source stayed unchanged while the independent object-store walk ran.
    await session.rollback()
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    closing_source_digest = hashlib.sha256()
    closing_source_rows = 0
    closing_source_by_day: dict[date, pa.Table] = {}
    for day in (first_day + timedelta(days=offset) for offset in range(span)):
        closing_source = _canonical(await read_weather_observations_day(session, day=day, layer_id=layer_id))
        closing_source_rows += closing_source.num_rows
        closing_source_by_day[day] = closing_source
        _fold_table(closing_source_digest, day, closing_source)
        if source_day_digests[day] != _table_sha256(closing_source):
            findings.append(
                ExactWeatherFinding(
                    day,
                    BASE_ZOOM,
                    "source_changed_during_reconciliation",
                    "one or more exported source rows or fields changed between the opening and closing snapshots",
                )
            )

    closing_keys_by_zoom = {
        zoom: store.list_partition_keys(WEATHER_OBSERVATIONS_STREAM, WEATHER_KIND, zoom) for zoom in ZOOM_TIERS
    }
    closing_statuses = {
        zoom: partition_day_statuses(
            layer=WEATHER_OBSERVATIONS_STREAM,
            kind=WEATHER_KIND,
            zoom=zoom,
            first_day=first_day,
            last_day=governed_last_day,
            keys=closing_keys_by_zoom[zoom],
        )
        for zoom in ZOOM_TIERS
    }
    for zoom in ZOOM_TIERS:
        changed_keys = set(keys_by_zoom[zoom]).symmetric_difference(closing_keys_by_zoom[zoom])
        changed_days = sorted(cast("set[date]", {_key_day(key) for key in changed_keys} - {None}))
        findings.extend(
            ExactWeatherFinding(
                day,
                zoom,
                "object_inventory_changed_during_reconciliation",
                "one or more partition or marker keys changed between the opening and closing inventories",
            )
            for day in changed_days
        )
        if changed_keys and not changed_days:
            findings.append(
                ExactWeatherFinding(
                    first_day,
                    zoom,
                    "object_inventory_changed_during_reconciliation",
                    "one or more unparseable object keys changed between the opening and closing inventories",
                )
            )

    closing_object_digests = {zoom: hashlib.sha256() for zoom in ZOOM_TIERS}
    for day in (first_day + timedelta(days=offset) for offset in range(span)):
        closing_expected = _expected_tiers(closing_source_by_day[day])
        for zoom in ZOOM_TIERS:
            closing_summary, _, closing_findings, _ = _inspect_tier(
                store,
                day=day,
                zoom=zoom,
                status=closing_statuses[zoom][day],
                listed_keys=closing_keys_by_zoom[zoom],
                expected=closing_expected[zoom],
            )
            findings.extend(closing_findings)
            _fold_object_evidence(closing_object_digests[zoom], day, closing_summary)
    findings.extend(
        ExactWeatherFinding(
            first_day,
            zoom,
            "object_content_changed_during_reconciliation",
            "canonical partition rows or marker bodies changed between the opening and closing reads",
        )
        for zoom in ZOOM_TIERS
        if opening_object_digests[zoom].digest() != closing_object_digests[zoom].digest()
    )

    return ExactWeatherReport(
        governed_first_day=first_day,
        governed_last_day=governed_last_day,
        excluded_prefix=excluded_prefix,
        excluded_unsettled_tail=excluded_tail,
        days=tuple(day_summaries),
        source_row_count=source_rows,
        source_sha256=source_digest.hexdigest(),
        closing_source_row_count=closing_source_rows,
        closing_source_sha256=closing_source_digest.hexdigest(),
        object_inventory_sha256={zoom: _key_set_sha256(keys) for zoom, keys in keys_by_zoom.items()},
        closing_object_inventory_sha256={zoom: _key_set_sha256(keys) for zoom, keys in closing_keys_by_zoom.items()},
        object_evidence_sha256={zoom: digest.hexdigest() for zoom, digest in opening_object_digests.items()},
        closing_object_evidence_sha256={zoom: digest.hexdigest() for zoom, digest in closing_object_digests.items()},
        tier_row_counts=dict(tier_rows),
        tier_sha256={zoom: digest.hexdigest() for zoom, digest in actual_digests.items()},
        expected_tier_sha256={zoom: digest.hexdigest() for zoom, digest in expected_digests.items()},
        findings=tuple(findings),
    )


def settled_cutoff(today: date) -> date:
    """Return the registry-defined settled cutoff; prefix objects never influence it."""
    return today - timedelta(days=WEATHER_REGISTRATION.publication_lag_days)


__all__ = [
    "BASE_ZOOM",
    "ExactWeatherFinding",
    "ExactWeatherReport",
    "audit_exact_weather_observations",
    "settled_cutoff",
]
