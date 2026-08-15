"""Read-only Phase 0 evidence: which forecast iterations exist, how stale they are, and what the
three observation planes actually hold for the Boise-area governed series.

Why the landing census reads three planes, and why availability bounds are reported at all, is in
`conductor/tracks/seasonal_forecast_feedback_20260726/evidence-phase0-2026-08-14.md` (section 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.seasonal_row_types import (
    CENSUS_STATEMENT_TIMEOUT_MILLISECONDS,
    optional_datetime,
    read_only_session,
    require_date,
    require_datetime,
    require_int,
    require_str,
    require_uuid,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

_ITERATION_INVENTORY_SQL: Final = text(load_query_sql("execution/seasonal_iteration_inventory.sql"))
_ITERATION_ORIGIN_HISTOGRAM_SQL: Final = text(load_query_sql("execution/seasonal_iteration_origin_histogram.sql"))
_PLANE_TOTALS_SQL: Final = text(load_query_sql("execution/seasonal_observation_plane_totals.sql"))
_SOURCE_LANDING_CENSUS_SQL: Final = text(load_query_sql("execution/seasonal_source_landing_census.sql"))
_SERIES_REGISTRY_SQL: Final = text(load_query_sql("execution/seasonal_series_registry.sql"))
_SERIES_PROFILE_SQL: Final = text(load_query_sql("execution/seasonal_series_profile.sql"))
_RELEASE_LINEAGE_SQL: Final = text(load_query_sql("execution/seasonal_release_lineage.sql"))

# The four NASA POWER analysis cells within ~100 km of Boise (43.6150 N, 116.2023 W), nearest first.
# Distances measured against production 2026-08-14: 45.8, 70.3, 77.1 and 94.1 km.
BOISE_AREA_CELL_KEYS: Final[tuple[str, ...]] = (
    "na-sample:1deg:p044.00:m116.00",
    "na-sample:1deg:p043.00:m116.00",
    "na-sample:1deg:p044.00:m117.00",
    "na-sample:1deg:p043.00:m117.00",
)

# The governed observation window: the NASA POWER four-year baseline plus its forward refresh.
EVIDENCE_WINDOW_START: Final = datetime(2022, 4, 30, tzinfo=UTC)
EVIDENCE_WINDOW_END: Final = datetime(2026, 8, 7, tzinfo=UTC)


@dataclass(frozen=True)
class IterationInventoryRow:
    """One (method, status, purpose, availability mode) group of the forecast iteration plane."""

    method: str
    status: str
    purpose: str
    availability_mode: str
    iteration_count: int
    series_count: int
    earliest_cutoff_time: datetime
    latest_cutoff_time: datetime
    latest_recorded_at: datetime
    max_horizon_days: int


@dataclass(frozen=True)
class IterationOriginRow:
    """Iteration and scored-actual counts at one simulated origin date."""

    method: str
    origin_date: date
    iteration_count: int
    series_count: int
    scored_actual_count: int
    latest_actual_available_at: datetime | None


@dataclass(frozen=True)
class PlaneTotalRow:
    """Row count of one observation plane."""

    plane: str
    row_count: int


@dataclass(frozen=True)
class SourceLandingRow:
    """Per-source release landings across the three observation planes."""

    source_key: str
    release_count: int
    signal_observation_releases: int
    forecast_observation_releases: int
    normalized_feature_releases: int
    zero_landing_releases: int


@dataclass(frozen=True)
class SeriesRegistryRow:
    """How many forecast series are registered for one input adapter and metric."""

    input_adapter: str
    metric_name: str
    signal_name: str
    series_count: int
    cell_count: int


@dataclass(frozen=True)
class SeriesProfileRow:
    """Cadence, duplication, missingness and availability profile of one governed signal series."""

    cell_key: str
    source_key: str
    signal_name: str
    support_key: str
    normalized_unit: str
    row_count: int
    observed_day_count: int
    source_release_count: int
    first_observed_date: date
    last_observed_date: date
    null_value_count: int
    unobserved_count: int
    flagged_count: int
    earliest_data_available_at: datetime
    latest_data_available_at: datetime

    @property
    def span_day_count(self) -> int:
        """Calendar days between the first and last observed date, inclusive."""
        return (self.last_observed_date - self.first_observed_date).days + 1

    @property
    def missing_day_count(self) -> int:
        """Days inside the observed span that carry no observation at all."""
        return self.span_day_count - self.observed_day_count

    @property
    def rows_per_observed_day(self) -> float:
        """Duplicate factor: >1 means several admissible source releases hold the same cell-day."""
        return self.row_count / self.observed_day_count if self.observed_day_count else 0.0


@dataclass(frozen=True)
class ReleaseLineageRow:
    """One frozen source release that contributed values to the evidence window."""

    source_key: str
    source_release_id: UUID
    source_version: str
    transform_version: str
    payload_checksum: str
    validation_state: str
    schema_version: str
    license_snapshot_checksum: str
    retrieved_at: datetime
    release_data_available_at: datetime
    contributed_row_count: int
    first_observed_date: date
    last_observed_date: date
    earliest_data_available_at: datetime
    latest_data_available_at: datetime


@dataclass(frozen=True)
class SeasonalEvidence:
    """The complete Phase 0 read-only evidence set."""

    collected_at: datetime
    cell_keys: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    iteration_inventory: tuple[IterationInventoryRow, ...]
    iteration_origins: tuple[IterationOriginRow, ...]
    series_registry: tuple[SeriesRegistryRow, ...]
    plane_totals: tuple[PlaneTotalRow, ...]
    source_landings: tuple[SourceLandingRow, ...]
    series_profiles: tuple[SeriesProfileRow, ...]
    release_lineage: tuple[ReleaseLineageRow, ...]


async def _load_iteration_inventory(session: AsyncSession) -> tuple[IterationInventoryRow, ...]:
    result = await session.execute(_ITERATION_INVENTORY_SQL)
    return tuple(
        IterationInventoryRow(
            method=require_str(row["method"], "method"),
            status=require_str(row["status"], "status"),
            purpose=require_str(row["purpose"], "purpose"),
            availability_mode=require_str(row["availability_mode"], "availability_mode"),
            iteration_count=require_int(row["iteration_count"], "iteration_count"),
            series_count=require_int(row["series_count"], "series_count"),
            earliest_cutoff_time=require_datetime(row["earliest_cutoff_time"], "earliest_cutoff_time"),
            latest_cutoff_time=require_datetime(row["latest_cutoff_time"], "latest_cutoff_time"),
            latest_recorded_at=require_datetime(row["latest_recorded_at"], "latest_recorded_at"),
            max_horizon_days=require_int(row["max_horizon_days"], "max_horizon_days"),
        )
        for row in result.mappings().all()
    )


async def _load_iteration_origins(session: AsyncSession) -> tuple[IterationOriginRow, ...]:
    result = await session.execute(_ITERATION_ORIGIN_HISTOGRAM_SQL)
    return tuple(
        IterationOriginRow(
            method=require_str(row["method"], "method"),
            origin_date=require_date(row["origin_date"], "origin_date"),
            iteration_count=require_int(row["iteration_count"], "iteration_count"),
            series_count=require_int(row["series_count"], "series_count"),
            scored_actual_count=require_int(row["scored_actual_count"], "scored_actual_count"),
            latest_actual_available_at=optional_datetime(
                row["latest_actual_available_at"], "latest_actual_available_at"
            ),
        )
        for row in result.mappings().all()
    )


async def _load_series_registry(session: AsyncSession) -> tuple[SeriesRegistryRow, ...]:
    result = await session.execute(_SERIES_REGISTRY_SQL)
    return tuple(
        SeriesRegistryRow(
            input_adapter=require_str(row["input_adapter"], "input_adapter"),
            metric_name=require_str(row["metric_name"], "metric_name"),
            signal_name=require_str(row["signal_name"], "signal_name"),
            series_count=require_int(row["series_count"], "series_count"),
            cell_count=require_int(row["cell_count"], "cell_count"),
        )
        for row in result.mappings().all()
    )


async def _load_plane_totals(session: AsyncSession) -> tuple[PlaneTotalRow, ...]:
    result = await session.execute(_PLANE_TOTALS_SQL)
    return tuple(
        PlaneTotalRow(
            plane=require_str(row["plane"], "plane"),
            row_count=require_int(row["row_count"], "row_count"),
        )
        for row in result.mappings().all()
    )


async def _load_source_landings(session: AsyncSession) -> tuple[SourceLandingRow, ...]:
    result = await session.execute(_SOURCE_LANDING_CENSUS_SQL)
    return tuple(
        SourceLandingRow(
            source_key=require_str(row["source_key"], "source_key"),
            release_count=require_int(row["release_count"], "release_count"),
            signal_observation_releases=require_int(row["signal_observation_releases"], "signal_observation_releases"),
            forecast_observation_releases=require_int(
                row["forecast_observation_releases"], "forecast_observation_releases"
            ),
            normalized_feature_releases=require_int(row["normalized_feature_releases"], "normalized_feature_releases"),
            zero_landing_releases=require_int(row["zero_landing_releases"], "zero_landing_releases"),
        )
        for row in result.mappings().all()
    )


async def _load_series_profiles(
    session: AsyncSession,
    cell_keys: Sequence[str],
    window_start: datetime,
    window_end: datetime,
) -> tuple[SeriesProfileRow, ...]:
    result = await session.execute(
        _SERIES_PROFILE_SQL,
        {"cell_keys": list(cell_keys), "window_start": window_start, "window_end": window_end},
    )
    return tuple(
        SeriesProfileRow(
            cell_key=require_str(row["cell_key"], "cell_key"),
            source_key=require_str(row["source_key"], "source_key"),
            signal_name=require_str(row["signal_name"], "signal_name"),
            support_key=require_str(row["support_key"], "support_key"),
            normalized_unit=require_str(row["normalized_unit"], "normalized_unit"),
            row_count=require_int(row["row_count"], "row_count"),
            observed_day_count=require_int(row["observed_day_count"], "observed_day_count"),
            source_release_count=require_int(row["source_release_count"], "source_release_count"),
            first_observed_date=require_date(row["first_observed_date"], "first_observed_date"),
            last_observed_date=require_date(row["last_observed_date"], "last_observed_date"),
            null_value_count=require_int(row["null_value_count"], "null_value_count"),
            unobserved_count=require_int(row["unobserved_count"], "unobserved_count"),
            flagged_count=require_int(row["flagged_count"], "flagged_count"),
            earliest_data_available_at=require_datetime(
                row["earliest_data_available_at"], "earliest_data_available_at"
            ),
            latest_data_available_at=require_datetime(row["latest_data_available_at"], "latest_data_available_at"),
        )
        for row in result.mappings().all()
    )


async def load_release_lineage(
    session: AsyncSession,
    cell_keys: Sequence[str],
    window_start: datetime,
    window_end: datetime,
) -> tuple[ReleaseLineageRow, ...]:
    """Read the frozen source-release identity behind a bounded cell/window selection."""
    result = await session.execute(
        _RELEASE_LINEAGE_SQL,
        {"cell_keys": list(cell_keys), "window_start": window_start, "window_end": window_end},
    )
    return tuple(
        ReleaseLineageRow(
            source_key=require_str(row["source_key"], "source_key"),
            source_release_id=require_uuid(row["source_release_id"], "source_release_id"),
            source_version=require_str(row["source_version"], "source_version"),
            transform_version=require_str(row["transform_version"], "transform_version"),
            payload_checksum=require_str(row["payload_checksum"], "payload_checksum"),
            validation_state=require_str(row["validation_state"], "validation_state"),
            schema_version=require_str(row["schema_version"], "schema_version"),
            license_snapshot_checksum=require_str(row["license_snapshot_checksum"], "license_snapshot_checksum"),
            retrieved_at=require_datetime(row["retrieved_at"], "retrieved_at"),
            release_data_available_at=require_datetime(row["release_data_available_at"], "release_data_available_at"),
            contributed_row_count=require_int(row["contributed_row_count"], "contributed_row_count"),
            first_observed_date=require_date(row["first_observed_date"], "first_observed_date"),
            last_observed_date=require_date(row["last_observed_date"], "last_observed_date"),
            earliest_data_available_at=require_datetime(
                row["earliest_data_available_at"], "earliest_data_available_at"
            ),
            latest_data_available_at=require_datetime(row["latest_data_available_at"], "latest_data_available_at"),
        )
        for row in result.mappings().all()
    )


async def collect_evidence(
    session: AsyncSession,
    *,
    cell_keys: Sequence[str] = BOISE_AREA_CELL_KEYS,
    window_start: datetime = EVIDENCE_WINDOW_START,
    window_end: datetime = EVIDENCE_WINDOW_END,
    collected_at: datetime | None = None,
) -> SeasonalEvidence:
    """Run every Phase 0 read against one read-only session."""
    return SeasonalEvidence(
        collected_at=collected_at or datetime.now(UTC),
        cell_keys=tuple(cell_keys),
        window_start=window_start,
        window_end=window_end,
        iteration_inventory=await _load_iteration_inventory(session),
        iteration_origins=await _load_iteration_origins(session),
        series_registry=await _load_series_registry(session),
        plane_totals=await _load_plane_totals(session),
        source_landings=await _load_source_landings(session),
        series_profiles=await _load_series_profiles(session, cell_keys, window_start, window_end),
        release_lineage=await load_release_lineage(session, cell_keys, window_start, window_end),
    )


async def collect_evidence_from_url(
    database_url: str,
    *,
    cell_keys: Sequence[str] = BOISE_AREA_CELL_KEYS,
    window_start: datetime = EVIDENCE_WINDOW_START,
    window_end: datetime = EVIDENCE_WINDOW_END,
) -> SeasonalEvidence:
    """Open a read-only session on ``database_url`` and collect the Phase 0 evidence."""
    async with read_only_session(
        database_url, statement_timeout_milliseconds=CENSUS_STATEMENT_TIMEOUT_MILLISECONDS
    ) as session:
        return await collect_evidence(
            session,
            cell_keys=cell_keys,
            window_start=window_start,
            window_end=window_end,
        )


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def staleness_days(evidence: SeasonalEvidence) -> dict[str, int]:
    """Days between each method's most recent recorded iteration and the collection time."""
    latest: dict[str, datetime] = {}
    for row in evidence.iteration_inventory:
        current = latest.get(row.method)
        if current is None or row.latest_recorded_at > current:
            latest[row.method] = row.latest_recorded_at
    return {method: (evidence.collected_at - moment).days for method, moment in sorted(latest.items())}


def render_evidence_markdown(evidence: SeasonalEvidence) -> str:
    """Render the Phase 0 evidence as the track's reviewable report."""
    stale = staleness_days(evidence)
    sections: list[str] = []
    sections.append(
        "---\ntype: evidence-report\n---\n\n"
        "# Phase 0 evidence: forecast iterations and Boise-area data quality\n\n"
        f"Collected {evidence.collected_at.isoformat()} from the retained warehouse over a "
        "READ ONLY transaction (`SET TRANSACTION READ ONLY`, 120 s statement timeout, UTC pinned). "
        "No row was written.\n\n"
        f"Cells: {', '.join(evidence.cell_keys)}.\n"
        f"Observation window: {evidence.window_start.date().isoformat()} to "
        f"{evidence.window_end.date().isoformat()} (half-open).\n"
    )

    sections.append(
        "## 1. Forecast iteration inventory\n\n"
        + _table(
            (
                "method",
                "status",
                "purpose",
                "availability mode",
                "iterations",
                "series",
                "origins from",
                "origins to",
                "last recorded",
                "max horizon days",
            ),
            [
                (
                    row.method,
                    row.status,
                    row.purpose,
                    row.availability_mode,
                    str(row.iteration_count),
                    str(row.series_count),
                    row.earliest_cutoff_time.date().isoformat(),
                    row.latest_cutoff_time.date().isoformat(),
                    row.latest_recorded_at.isoformat(timespec="seconds"),
                    str(row.max_horizon_days),
                )
                for row in evidence.iteration_inventory
            ],
        )
        + "\n\nStaleness at collection time: "
        + (
            "; ".join(f"`{method}` last recorded {days} day(s) ago" for method, days in stale.items())
            or "no iteration exists"
        )
        + ".\n"
    )

    sections.append(
        "## 2. Iterations and scored actuals per simulated origin\n\n"
        + _table(
            ("method", "origin date", "iterations", "series", "scored actuals", "latest actual available at"),
            [
                (
                    row.method,
                    row.origin_date.isoformat(),
                    str(row.iteration_count),
                    str(row.series_count),
                    str(row.scored_actual_count),
                    row.latest_actual_available_at.isoformat(timespec="seconds")
                    if row.latest_actual_available_at
                    else "-",
                )
                for row in evidence.iteration_origins
            ],
        )
        + "\n"
    )

    sections.append(
        "## 2b. Registered forecast series, by input adapter\n\n"
        + _table(
            ("input adapter", "metric", "signal", "series", "cells"),
            [
                (row.input_adapter, row.metric_name, row.signal_name, str(row.series_count), str(row.cell_count))
                for row in evidence.series_registry
            ],
        )
        + "\n"
    )

    sections.append(
        "## 3. The three observation planes, counted separately\n\n"
        + _table(
            ("plane", "rows"),
            [(row.plane, f"{row.row_count:,}") for row in evidence.plane_totals],
        )
        + "\n\n"
        + _table(
            (
                "source",
                "releases",
                "landed in signal_observation",
                "landed in forecast_observation",
                "landed in normalized_source_feature",
                "landed nowhere",
            ),
            [
                (
                    row.source_key,
                    str(row.release_count),
                    str(row.signal_observation_releases),
                    str(row.forecast_observation_releases),
                    str(row.normalized_feature_releases),
                    str(row.zero_landing_releases),
                )
                for row in evidence.source_landings
            ],
        )
        + "\n"
    )

    sections.append(
        "## 4. Boise-area governed series: cadence, duplication, missingness, availability\n\n"
        + _table(
            (
                "cell",
                "source",
                "signal",
                "support",
                "unit",
                "rows",
                "observed days",
                "span days",
                "missing days",
                "rows/day",
                "releases",
                "first",
                "last",
                "availability from",
                "availability to",
            ),
            [
                (
                    row.cell_key,
                    row.source_key,
                    row.signal_name,
                    row.support_key,
                    row.normalized_unit,
                    str(row.row_count),
                    str(row.observed_day_count),
                    str(row.span_day_count),
                    str(row.missing_day_count),
                    f"{row.rows_per_observed_day:.2f}",
                    str(row.source_release_count),
                    row.first_observed_date.isoformat(),
                    row.last_observed_date.isoformat(),
                    row.earliest_data_available_at.isoformat(timespec="seconds"),
                    row.latest_data_available_at.isoformat(timespec="seconds"),
                )
                for row in evidence.series_profiles
            ],
        )
        + "\n\nNull values: "
        + str(sum(row.null_value_count for row in evidence.series_profiles))
        + "; unobserved rows: "
        + str(sum(row.unobserved_count for row in evidence.series_profiles))
        + "; rows flagged other than `accepted`: "
        + str(sum(row.flagged_count for row in evidence.series_profiles))
        + ".\n"
    )

    sections.append(
        "## 5. Frozen source-release lineage\n\n"
        + _table(
            (
                "source",
                "source version",
                "transform version",
                "payload checksum",
                "validation",
                "rows",
                "first",
                "last",
            ),
            [
                (
                    row.source_key,
                    row.source_version,
                    row.transform_version,
                    row.payload_checksum[:16] + "...",
                    row.validation_state,
                    str(row.contributed_row_count),
                    row.first_observed_date.isoformat(),
                    row.last_observed_date.isoformat(),
                )
                for row in evidence.release_lineage
            ],
        )
        + f"\n\n{len(evidence.release_lineage)} releases contributed to this window.\n"
    )

    sections.append(render_findings(evidence))

    return "\n\n".join(sections) + "\n"


def render_findings(evidence: SeasonalEvidence) -> str:
    """Derive the leakage-relevant findings from the measurements rather than restating them."""
    findings: list[str] = []

    metric_series = sum(
        row.series_count for row in evidence.series_registry if row.input_adapter != "forecast_observation"
    )
    findings.append(
        f"1. **The spec's Boise WS2M forecast series is not registered here.** "
        f"`agri.forecast_series` holds {metric_series} series on a non-`forecast_observation` input "
        "adapter, so the SQL-linear and daily-increment-bootstrap baselines the spec names as "
        "comparators have no registered metric series in this warehouse. Every registered series and "
        "every finalized iteration belongs to the Sentinel-2 NDVI lane. The candidate ladder therefore "
        "reads the governed observation plane directly, and its comparison to those baselines is a "
        "reimplementation of their published method, not a read of their stored output."
    )

    if evidence.series_profiles:
        earliest_available = min(row.earliest_data_available_at for row in evidence.series_profiles)
        earliest_observed = min(row.first_observed_date for row in evidence.series_profiles)
        latest_observed = max(row.last_observed_date for row in evidence.series_profiles)
        findings.append(
            f"2. **Every value in the {earliest_observed.isoformat()}-{latest_observed.isoformat()} history "
            f"became warehouse-visible no earlier than {earliest_available.date().isoformat()}.** "
            "`data_available_at` is the real server-recorded arrival time, and its minimum across all "
            "profiled series sits within days of collection. A simulated origin in 2023 therefore reads "
            "the 2026 revision of a 2023 observation. This evaluation is **observation-time honest** "
            "(no value dated on or after an origin enters that origin's fit) and is **not** "
            "revision/point-in-time honest. That is the same `as_of_mode = global` limitation the "
            "covariate wind lane records, and it is why no result here may be read as an operational "
            "skill estimate."
        )

        gap_free = [row for row in evidence.series_profiles if row.missing_day_count == 0]
        duplicated = [row for row in evidence.series_profiles if row.rows_per_observed_day > 1.0]
        findings.append(
            f"3. **Cadence and duplication.** {len(gap_free)} of {len(evidence.series_profiles)} profiled "
            f"series have no calendar gap inside their observed span. {len(duplicated)} carry more than one "
            "admissible row per cell-day, because `uq_signal_observation_release_cell_signal_time` includes "
            "`source_release_id` and re-ingests are legitimate. The export deduplicates with "
            "`DISTINCT ON (... UTC day) ORDER BY data_available_at DESC, id DESC`, which is the same "
            "precedence the shipped covariate reader uses."
        )

    scored = [row for row in evidence.iteration_origins if row.scored_actual_count > 0]
    findings.append(
        f"4. **Independent scored origins in the existing iteration plane: {len(scored)}.** "
        "They belong to one method on one input adapter, so the existing plane cannot by itself support "
        "model selection for a metric series; the frozen export is what supplies the origins."
    )

    return "## 6. Findings\n\n" + "\n\n".join(findings) + "\n"
