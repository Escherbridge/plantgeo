"""End-to-end: write the lineage plane from scored origins, then re-derive its properties two ways.

The SQL audit walks the stored rows with a recursive CTE; the Python graph re-derives the same
verdict from rows read back out. Both must agree, and both must be computed rather than asserted.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agri_data_service.execution.seasonal_lineage_persist import (
    RESIDUAL_FEEDBACK_SIGNAL_KEY,
    SeasonalLineagePersistError,
    persist_scored_origins,
)
from agri_data_service.method.ml.seasonal_evaluation import ScoredOrigin
from agri_data_service.method.ml.seasonal_lineage_graph import (
    LineageEdge,
    LineageGraph,
    LineageNode,
    snapshot_eligible_values,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

pytestmark = pytest.mark.agri_db

AGRI_TEST_DATABASE_URL_ENV: Final = "AGRI_TEST_DATABASE_URL"
PROTECTED_DATABASE_NAME: Final = "plantgeo"
RELEASE_DIGEST: Final = "e" * 64


def _async_dsn() -> str:
    dsn = os.environ.get(AGRI_TEST_DATABASE_URL_ENV)
    if not dsn:
        pytest.skip(f"set {AGRI_TEST_DATABASE_URL_ENV} to a disposable database migrated past 20260814_0021")
    if dsn.rsplit("/", 1)[-1].split("?")[0] == PROTECTED_DATABASE_NAME:
        pytest.fail(f"refusing to write to the persistent {PROTECTED_DATABASE_NAME!r} warehouse")
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    return dsn


# A unique series key per session: this database is disposable but not necessarily empty, and a
# real persistence run writes the same lattice series. Isolation belongs to the test, not to the
# operator remembering to truncate.
SERIES_KEY: Final = f"test:{uuid4().hex[:12]}|wind_speed|surface"


def _scored_origins() -> tuple[ScoredOrigin, ...]:
    origins: list[ScoredOrigin] = []
    for index in range(3):
        origin = date(2025, 9, 1) + timedelta(days=30 * index)
        steps = tuple(range(1, 5))
        origins.append(
            ScoredOrigin(
                series_key=SERIES_KEY,
                candidate_name="regularized_lag_seasonal_ridge_v1",
                origin=origin,
                fold_kind="final_holdout",
                target_days=tuple(origin + timedelta(days=step) for step in steps),
                horizon_steps=steps,
                median=tuple(3.0 + 0.1 * step for step in steps),
                low=tuple(2.0 + 0.1 * step for step in steps),
                high=tuple(4.0 + 0.1 * step for step in steps),
                actual=tuple(3.2 + 0.05 * step for step in steps),
            )
        )
    return tuple(origins)


async def _persist_and_read(scored: Sequence[ScoredOrigin]) -> tuple[LineageGraph, list[tuple[object, ...]]]:
    engine = create_async_engine(_async_dsn(), pool_size=1, max_overflow=0)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            summary = await persist_scored_origins(session, scored, input_release_checksum=RELEASE_DIGEST)
            assert summary.point_forecast_value_count == summary.residual_value_count
            assert summary.lineage_edge_count == summary.point_forecast_value_count

            node_rows = (
                await session.execute(
                    text(
                        """
                        SELECT value.id, definition.signal_key, value.lineage_depth,
                               value.origin_cutoff_time, value.valid_time, value.availability_time,
                               value.max_dependency_depth
                        FROM agri.forecast_derived_signal_value AS value
                        JOIN agri.forecast_signal_definition AS definition
                            ON definition.id = value.signal_definition_id
                        WHERE value.series_key = :series_key
                        ORDER BY value.id
                        """
                    ),
                    {"series_key": SERIES_KEY},
                )
            ).all()
            edge_rows = (
                await session.execute(
                    text(
                        "SELECT edge.child_value_id, edge.parent_value_id, edge.parent_role "
                        "FROM agri.forecast_signal_lineage_edge AS edge "
                        "JOIN agri.forecast_derived_signal_value AS child "
                        "ON child.id = edge.child_value_id "
                        "WHERE child.series_key = :series_key ORDER BY edge.id"
                    ),
                    {"series_key": SERIES_KEY},
                )
            ).all()
            audit_rows = (
                await session.execute(
                    text(
                        """
                        SELECT audit.traversed_depth, audit.cycle_detected, audit.depth_bound_exceeded,
                               audit.availability_violation_count, audit.ancestor_count
                        FROM agri.forecast_derived_signal_value AS value
                        JOIN agri.forecast_signal_definition AS definition
                            ON definition.id = value.signal_definition_id
                        CROSS JOIN LATERAL agri.forecast_signal_lineage_audit(value.id) AS audit
                        WHERE definition.signal_key = :signal_key
                          AND value.series_key = :series_key
                        ORDER BY value.id
                        """
                    ),
                    {"signal_key": RESIDUAL_FEEDBACK_SIGNAL_KEY, "series_key": SERIES_KEY},
                )
            ).all()
            await session.rollback()

        nodes = [
            LineageNode(
                value_id=int(row[0]),
                signal_key=str(row[1]),
                lineage_depth=int(row[2]),
                origin_cutoff_time=row[3],
                valid_time=row[4],
                availability_time=row[5],
                max_dependency_depth=int(row[6]),
            )
            for row in node_rows
        ]
        edges = [
            LineageEdge(child_value_id=int(row[0]), parent_value_id=int(row[1]), parent_role=str(row[2]))
            for row in edge_rows
        ]
        return LineageGraph(nodes, edges), [tuple(row) for row in audit_rows]
    finally:
        await engine.dispose()


def test_the_persisted_plane_is_acyclic_bounded_and_availability_ordered() -> None:
    graph, audit_rows = asyncio.run(_persist_and_read(_scored_origins()))
    validation = graph.validate()
    assert validation.node_count == 24  # noqa: PLR2004
    assert validation.edge_count == 12  # noqa: PLR2004
    assert validation.is_acyclic
    assert validation.violations == ()
    assert validation.is_valid
    assert validation.max_observed_depth == 1

    assert len(audit_rows) == 12  # noqa: PLR2004
    for traversed_depth, cycle_detected, bound_exceeded, availability_violations, ancestor_count in audit_rows:
        assert traversed_depth == 1
        assert cycle_detected is False
        assert bound_exceeded is False
        assert availability_violations == 0
        assert ancestor_count == 1


def test_a_residual_is_never_available_before_the_forecast_it_scores() -> None:
    graph, _ = asyncio.run(_persist_and_read(_scored_origins()))
    for edge in graph.edges:
        child = graph.nodes[edge.child_value_id]
        parent = graph.nodes[edge.parent_value_id]
        assert parent.availability_time <= child.origin_cutoff_time
        assert parent.valid_time < child.origin_cutoff_time
        assert child.availability_time >= parent.availability_time


def test_snapshot_eligibility_over_the_persisted_plane_moves_with_the_as_of() -> None:
    graph, _ = asyncio.run(_persist_and_read(_scored_origins()))
    early = snapshot_eligible_values(graph, datetime(2025, 9, 2, tzinfo=UTC))
    late = snapshot_eligible_values(graph, datetime(2026, 1, 1, tzinfo=UTC))
    assert len(early) < len(late)
    assert len(late) == len(graph.nodes)
    residuals = {value_id for value_id, node in graph.nodes.items() if node.signal_key == RESIDUAL_FEEDBACK_SIGNAL_KEY}
    assert not residuals & set(early)


def test_persisting_nothing_is_refused() -> None:
    async def _run() -> None:
        engine = create_async_engine(_async_dsn(), pool_size=1, max_overflow=0)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                with pytest.raises(SeasonalLineagePersistError, match="empty plane"):
                    await persist_scored_origins(session, (), input_release_checksum=RELEASE_DIGEST)
        finally:
            await engine.dispose()

    asyncio.run(_run())
