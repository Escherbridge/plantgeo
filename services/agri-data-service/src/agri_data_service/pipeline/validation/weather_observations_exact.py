"""Read-only exact PostgreSQL-to-Parquet audit for the governed current-weather lane."""

from __future__ import annotations

import hashlib
import io
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Protocol, cast

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]

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
    tier_row_counts: Mapping[ZoomTier, int]
    tier_sha256: Mapping[ZoomTier, str]
    expected_tier_sha256: Mapping[ZoomTier, str]
    findings: tuple[ExactWeatherFinding, ...]

    @property
    def is_clean(self) -> bool:
        return not self.findings and all(
            self.tier_sha256.get(zoom) == self.expected_tier_sha256.get(zoom) for zoom in ZOOM_TIERS
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
            key
            for key in keys_by_zoom[zoom]
            if (key_day := _key_day(key)) is not None and include(key_day)
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


def _inspect_tier(  # noqa: PLR0913 - one exact stream-day-tier comparison owns these coordinates
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
        (parsed := try_parse_completion_marker_path(key)) is not None and parsed.day == day for key in listed_keys
    )
    absence_listed = any(
        (parsed := try_parse_absence_marker_path(key)) is not None and parsed.day == day for key in listed_keys
    )
    completion_summary: dict[str, object] | None = None
    absence_sha256: str | None = None
    completion_error = False
    try:
        completion = store.read_completion_marker(WEATHER_OBSERVATIONS_STREAM, WEATHER_KIND, zoom, day)
    except Exception as error:
        completion_error = True
        completion = None
        findings.append(ExactWeatherFinding(day, zoom, "completion_marker", f"{type(error).__name__}: {error}"))
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
        findings.append(ExactWeatherFinding(day, zoom, "absence_marker", f"{type(error).__name__}: {error}"))
    if absence_listed and absence is None and not absence_error:
        findings.append(ExactWeatherFinding(day, zoom, "absence_marker", "listed marker disappeared before read"))
    if not absence_listed and absence is not None:
        findings.append(
            ExactWeatherFinding(day, zoom, "absence_marker", "marker appeared after the inventory listing")
        )
    if absence is not None:
        absence_sha256 = hashlib.sha256(absence.to_json_bytes()).hexdigest()

    actual: pa.Table | None = None
    if part_count:
        try:
            raw = store.read_partition(WEATHER_OBSERVATIONS_STREAM, WEATHER_KIND, zoom, day)
            if not raw.schema.equals(WEATHER_OBSERVATIONS_SCHEMA.arrow_schema, check_metadata=False):
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
            findings.append(ExactWeatherFinding(day, zoom, "partition_read", f"{type(error).__name__}: {error}"))
    elif status == "absent":
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


async def audit_exact_weather_observations(  # noqa: PLR0912, PLR0913, PLR0915
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
    tier_rows = Counter[ZoomTier]()
    source_rows = 0
    findings: list[ExactWeatherFinding] = []
    day_summaries: list[Mapping[str, object]] = []
    span = (governed_last_day - first_day).days + 1
    for day in (first_day + timedelta(days=offset) for offset in range(span)):
        source = _canonical(await read_weather_observations_day(session, day=day, layer_id=layer_id))
        source_rows += source.num_rows
        _fold_table(source_digest, day, source)
        expected_status: PartitionDayStatus = "data" if source.num_rows else "absent"
        expected_by_zoom: dict[ZoomTier, pa.Table] = {BASE_ZOOM: source}
        if source.num_rows:
            frame = cast("pl.DataFrame", pl.from_arrow(source))
            for zoom in ZOOM_TIERS:
                if zoom != BASE_ZOOM:
                    expected_by_zoom[zoom] = _canonical(
                        derive_tier(frame, stream=WEATHER_OBSERVATIONS_STREAM, tier=zoom).to_arrow()
                    )
        else:
            empty = _canonical(WEATHER_OBSERVATIONS_SCHEMA.arrow_schema.empty_table())
            expected_by_zoom.update({zoom: empty for zoom in ZOOM_TIERS if zoom != BASE_ZOOM})

        tier_summaries: dict[str, object] = {}
        absence_digests: dict[ZoomTier, str] = {}
        for zoom in ZOOM_TIERS:
            status = statuses[zoom][day]
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
            summary, actual, tier_findings, absence_sha = _inspect_tier(
                store,
                day=day,
                zoom=zoom,
                status=status,
                listed_keys=keys_by_zoom[zoom],
                expected=expected,
            )
            findings.extend(tier_findings)
            tier_summaries[str(zoom)] = summary
            if absence_sha is not None:
                absence_digests[zoom] = absence_sha
            if actual is not None:
                tier_rows[zoom] += actual.num_rows
                _fold_table(actual_digests[zoom], day, actual)
        if expected_status == "absent" and len(set(absence_digests.values())) > 1:
            findings.append(
                ExactWeatherFinding(day, BASE_ZOOM, "absence_evidence", "absence evidence differs across zoom tiers")
            )
        day_summaries.append(
            {
                "day": day.isoformat(),
                "postgres_row_count": source.num_rows,
                "postgres_sha256": _table_sha256(source),
                "expected_status": expected_status,
                "tiers": tier_summaries,
            }
        )

    return ExactWeatherReport(
        governed_first_day=first_day,
        governed_last_day=governed_last_day,
        excluded_prefix=excluded_prefix,
        excluded_unsettled_tail=excluded_tail,
        days=tuple(day_summaries),
        source_row_count=source_rows,
        source_sha256=source_digest.hexdigest(),
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
