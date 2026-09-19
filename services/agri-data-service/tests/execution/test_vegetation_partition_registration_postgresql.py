"""Disposable-PostgreSQL proof that partition registration lands with NO source table at all.

Replaces `tests/test_vegetation_ndvi_plane_postgresql.py`, which seeded a `geo.features` stand-in
because the registration verb read one. It no longer does: the values arrive from the caller's
Parquet day partition, so the only fixture this needs is the lattice dimension
(`agri.spatial_cell`), which the verb resolves and never creates. The absence of a geo fixture here
IS the regression test for the 2026-09-19 rollbacks
(`conductor/tracks/gapless_parquet_publication_20260901/evidence/ndvi-promotion-activation-20260919.md`).

Gated on ``AGRI_TEST_DATABASE_URL`` through the shared ``tests/conftest.py`` fixture, which verifies
the Alembic head and refuses the persistent ``plantgeo`` warehouse. Everything runs inside one
transaction that is rolled back, so the database is left exactly as it was found.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.execution.vegetation_ndvi_plane import (
    DATA_SOURCE_KEY,
    GRID_NAME,
    UnregisteredPartitionCellsError,
    all_requested_cells_materialised,
    forward_release_set_logical_key,
    register_governed_partition_plane,
)

OBSERVED_DAY = date(2026, 9, 12)
SEEDED_CELL_KEYS = ("43.1250:-116.1250", "43.3750:-116.3750")
# Deliberately absent from agri.spatial_cell: the re-keyed-upstream case the guard exists for.
UNREGISTERED_CELL_KEYS = ("99.8750:-179.8750",)
CELL_RESOLUTION_M = 27750

_INSERT_FIXTURE_CELL = """
INSERT INTO agri.spatial_cell (cell_key, grid_name, resolution_m, geometry, centroid, coverage_fraction)
SELECT
    CAST(:grid_name AS text) || ':' || CAST(:entity_key AS text),
    CAST(:grid_name AS text),
    CAST(:resolution_m AS integer),
    envelope.geometry,
    ST_Centroid(envelope.geometry),
    1.0
FROM (
    -- Every corner is cast: an untyped parameter reused in a bare `+ 0.25` deduces as numeric on
    -- one side and double precision on the other, which asyncpg refuses outright.
    SELECT ST_SetSRID(
        ST_MakeEnvelope(
            CAST(:min_lon AS double precision),
            CAST(:min_lat AS double precision),
            CAST(:min_lon AS double precision) + 0.25,
            CAST(:min_lat AS double precision) + 0.25
        ),
        4326
    ) AS geometry
) AS envelope
"""

_COUNT_RELEASES = """
SELECT count(*)
FROM agri.source_release AS release
INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE source.key = :data_source_key
"""

_COUNT_RELEASE_SET_ITEMS = """
SELECT count(*)
FROM agri.release_set_item AS item
INNER JOIN agri.release_set AS release_set ON release_set.id = item.release_set_id
WHERE release_set.logical_key = :logical_key
"""


async def _seed_lattice_cells(session: AsyncSession) -> None:
    """Register only the lattice dimension: no geo schema, no source rows, no observations."""
    statement = text(_INSERT_FIXTURE_CELL)
    for cell_key in SEEDED_CELL_KEYS:
        latitude, longitude = (float(part) for part in cell_key.split(":"))
        await session.execute(
            statement,
            {
                "grid_name": GRID_NAME,
                "entity_key": cell_key,
                "resolution_m": CELL_RESOLUTION_M,
                "min_lat": latitude,
                "min_lon": longitude,
            },
        )


async def _release_count(session: AsyncSession) -> int:
    result = await session.execute(text(_COUNT_RELEASES), {"data_source_key": DATA_SOURCE_KEY})
    return int(result.scalar_one())


async def _release_set_item_count(session: AsyncSession, logical_key: str) -> int:
    result = await session.execute(text(_COUNT_RELEASE_SET_ITEMS), {"logical_key": logical_key})
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_partition_registration_lands_and_repeats_without_a_source_table(
    agri_db_async_dsn: str,
) -> None:
    """One partition lands every cell, and re-registering identical content re-uses its release."""
    engine = create_async_engine(agri_db_async_dsn)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            transaction = await session.begin()
            try:
                await _seed_lattice_cells(session)
                cell_values = tuple((cell_key, 0.40 + 0.01 * index) for index, cell_key in enumerate(SEEDED_CELL_KEYS))

                summary = await register_governed_partition_plane(
                    session,
                    observed_day=OBSERVED_DAY,
                    cell_values=cell_values,
                )

                assert summary.requested_cell_count == len(SEEDED_CELL_KEYS)
                assert summary.observation_count == len(SEEDED_CELL_KEYS)
                assert summary.selection.observation_count == len(SEEDED_CELL_KEYS)
                assert summary.selection.series_count == len(SEEDED_CELL_KEYS)
                assert all_requested_cells_materialised(
                    selection=summary.selection,
                    requested_cell_count=summary.requested_cell_count,
                )
                assert summary.materialisation.first_observed_day == OBSERVED_DAY
                assert summary.materialisation.last_observed_day == OBSERVED_DAY
                assert await _release_count(session) == 1

                repeated = await register_governed_partition_plane(
                    session,
                    observed_day=OBSERVED_DAY,
                    cell_values=cell_values,
                )
                # Identical content is the identical release: the content SHA IS the release identity.
                assert repeated.plane.payload_checksum == summary.plane.payload_checksum
                assert repeated.plane.source_release_id == summary.plane.source_release_id
                assert repeated.plane.release_set_id == summary.plane.release_set_id
                assert repeated.observation_count == 0
                assert await _release_count(session) == 1
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_changed_partition_content_versions_its_own_release_set(agri_db_async_dsn: str) -> None:
    """A changed value is a different partition, so it registers beside the old one, not over it."""
    engine = create_async_engine(agri_db_async_dsn)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            transaction = await session.begin()
            try:
                await _seed_lattice_cells(session)
                original = tuple((cell_key, 0.40 + 0.01 * index) for index, cell_key in enumerate(SEEDED_CELL_KEYS))
                first = await register_governed_partition_plane(
                    session, observed_day=OBSERVED_DAY, cell_values=original
                )
                first_key = forward_release_set_logical_key(OBSERVED_DAY, first.plane.payload_checksum)
                assert await _release_set_item_count(session, first_key) == 1

                amended = ((SEEDED_CELL_KEYS[0], 0.77), *original[1:])
                second = await register_governed_partition_plane(
                    session, observed_day=OBSERVED_DAY, cell_values=amended
                )
                second_key = forward_release_set_logical_key(OBSERVED_DAY, second.plane.payload_checksum)

                assert second.plane.payload_checksum != first.plane.payload_checksum
                assert second_key != first_key
                assert await _release_count(session) == 2  # noqa: PLR2004 - two releases IS the assertion
                # The earlier release set is immutable: the amendment never rejoins it.
                assert await _release_set_item_count(session, first_key) == 1
                assert await _release_set_item_count(session, second_key) == 1
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unregistered_cells_are_refused_before_any_release_is_written(
    agri_db_async_dsn: str,
) -> None:
    """The refusal precedes the release insert, so a refused turn leaves nothing behind."""
    engine = create_async_engine(agri_db_async_dsn)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            transaction = await session.begin()
            try:
                await _seed_lattice_cells(session)
                assert await _release_count(session) == 0

                with pytest.raises(UnregisteredPartitionCellsError) as caught:
                    await register_governed_partition_plane(
                        session,
                        observed_day=OBSERVED_DAY,
                        cell_values=tuple((cell_key, 0.5) for cell_key in UNREGISTERED_CELL_KEYS),
                    )

                assert caught.value.cell_keys == UNREGISTERED_CELL_KEYS
                assert await _release_count(session) == 0
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
