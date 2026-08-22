"""The batch inserts, lattice lookups and WKT helpers all four historical lanes copied verbatim."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from agri_data_service.execution.provenance import ensure_data_source
from agri_data_service.models.historical import (
    CellSourceCrosswalk,
    SignalCoverageAudit,
    SignalObservation,
    SpatialCell,
)
from agri_data_service.models.provenance import DataSource, SourceReviewState

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.backfill_types import AnalysisGridCell
    from agri_data_service.execution.source_ingestion import SourceDefinition

WGS84_SRID = 4326
HISTORICAL_NASA_OBSERVATION_INSERT_BATCH_SIZE = 1_000
HISTORICAL_SIGNAL_INSERT_BATCH_SIZE = 1_000

# Every historical lane refuses the same way when its reviewed source is withdrawn mid-backfill.
HISTORICAL_SOURCE_INACTIVE_MESSAGE = "historical source is not approved and active"


async def _ensure_data_source(
    session: AsyncSession,
    source_definition: SourceDefinition,
    *,
    configuration: dict[str, str],
) -> DataSource:
    """Register the lane's reviewed source once, refusing a key already governed by other metadata."""
    expected: dict[str, object] = {
        "name": source_definition.name,
        "owner": source_definition.owner,
        "purpose": source_definition.purpose,
        "base_url": source_definition.base_url,
        "license_name": source_definition.license_name,
        "license_url": source_definition.license_url,
        "citation": source_definition.citation,
        "retention_days": source_definition.retention_days,
        "reviewed_at": source_definition.reviewed_at,
        "reviewed_by": source_definition.reviewed_by,
        "configuration": configuration,
    }
    source, _ = await ensure_data_source(
        session,
        DataSource(
            key=source_definition.key,
            name=source_definition.name,
            owner=source_definition.owner,
            purpose=source_definition.purpose,
            base_url=source_definition.base_url,
            license_name=source_definition.license_name,
            license_url=source_definition.license_url,
            citation=source_definition.citation,
            retention_days=source_definition.retention_days,
            allowed_client_exposure=False,
            review_state=SourceReviewState.APPROVED,
            reviewed_at=source_definition.reviewed_at,
            reviewed_by=source_definition.reviewed_by,
            configuration=configuration,
        ),
        expected=expected,
        inactive_message=HISTORICAL_SOURCE_INACTIVE_MESSAGE,
        conflict_message="historical source key is already governed by different metadata",
    )
    return source


async def _insert_cell_crosswalk_batch(session: AsyncSession, rows: list[dict[str, object]]) -> None:
    """Insert one bounded idempotent cell crosswalk batch."""
    await session.execute(
        insert(CellSourceCrosswalk)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_cell_source_crosswalk_release_feature_cell")
    )


async def _insert_signal_observation_batch(session: AsyncSession, rows: list[dict[str, object]]) -> None:
    """Insert one bounded idempotent signal observation batch."""
    await session.execute(
        insert(SignalObservation)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_signal_observation_release_cell_signal_time")
    )


async def _insert_signal_coverage_batch(session: AsyncSession, rows: list[dict[str, object]]) -> None:
    """Insert one bounded idempotent signal coverage batch."""
    await session.execute(
        insert(SignalCoverageAudit)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_signal_coverage_release_cell_signal_parameter_window")
    )


async def _require_spatial_cells(
    session: AsyncSession,
    cells: Sequence[AnalysisGridCell],
    *,
    missing_message: str,
) -> dict[str, SpatialCell]:
    """Require every reviewed analysis cell to already exist; these lanes never mint spatial cells."""
    cell_keys = [cell.cell_key for cell in cells]
    existing = (await session.execute(select(SpatialCell).where(SpatialCell.cell_key.in_(cell_keys)))).scalars()
    resolved = {cell.cell_key: cell for cell in existing}
    missing = [cell.cell_key for cell in cells if cell.cell_key not in resolved]
    if missing:
        raise ValueError(missing_message)
    return resolved


def _cell_polygon_wkt(cell: AnalysisGridCell, half_span: float) -> str:
    """Return the axis-aligned sampling box centred on one analysis cell."""
    west = cell.longitude - half_span
    east = cell.longitude + half_span
    south = cell.latitude - half_span
    north = cell.latitude + half_span
    return (
        "POLYGON(("
        f"{west:.8f} {south:.8f}, {east:.8f} {south:.8f}, {east:.8f} {north:.8f}, "
        f"{west:.8f} {north:.8f}, {west:.8f} {south:.8f}"
        "))"
    )


def _point_wkt(cell: AnalysisGridCell) -> str:
    """Return one analysis cell's requested sampling point."""
    return f"POINT({cell.longitude:.8f} {cell.latitude:.8f})"


def _utc_now_or_value(value: datetime | None) -> datetime:
    """Return the caller's aware timestamp in UTC, defaulting to now."""
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("validated_at must include a timezone")
    return timestamp.astimezone(UTC)
