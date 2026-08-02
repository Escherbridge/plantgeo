"""Disposable PostgreSQL proof for the governed Boise pilot writer.

Local-dev-only: the plan file's ``capture_base`` (``.agri-local-runs/``) is
gitignored, so this test only runs where that local capture already exists;
it is not wired into CI (see ``.github/workflows/ci.yml``).
"""

# ruff: noqa: PLR2004

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.execution.geospatial_pilot import ingest_boise_intervention_pilot

PROTECTED_COUNT_SQL = {
    "forecast_run": text("SELECT count(*) FROM agri.forecast_run"),
    "forecast_hindcast_run": text("SELECT count(*) FROM agri.forecast_hindcast_run"),
    "forecast_hindcast_value": text("SELECT count(*) FROM agri.forecast_hindcast_value"),
    "forecast_quality_policy": text("SELECT count(*) FROM agri.forecast_quality_policy"),
    "forecast_receipt": text("SELECT count(*) FROM agri.forecast_receipt"),
    "forecast_value": text("SELECT count(*) FROM agri.forecast_value"),
    "forecast_publication": text("SELECT count(*) FROM agri.forecast_publication"),
    "forecast_publication_item": text("SELECT count(*) FROM agri.forecast_publication_item"),
}


async def _protected_state(connection: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for table_name, statement in PROTECTED_COUNT_SQL.items():
        value = await connection.scalar(statement)
        assert value is not None
        result[table_name] = int(value)
    surface_count = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_catalog.pg_tables "
            "WHERE schemaname = 'agri' "
            "AND tablename IN ('strategy_selection', 'recommendation', 'recommendations')"
        )
    )
    assert surface_count is not None
    result["strategy_or_recommendation_tables"] = int(surface_count)
    return result


@pytest.mark.agri_db_manual_grant
@pytest.mark.asyncio
async def test_pilot_writer_is_loader_scoped_idempotent_and_nonpublishing(agri_db_async_dsn: str) -> None:
    service_root = Path(__file__).resolve().parents[1]
    repository_root = service_root.parents[1]
    plan_path = service_root / "plans" / "boise-intervention-capture-v1.json"
    capture_base = repository_root / ".agri-local-runs" / "north-america-intervention"
    engine = create_async_engine(agri_db_async_dsn)
    try:
        async with engine.begin() as connection:
            granted = await connection.scalar(
                text("SELECT has_table_privilege('plantgeo_loader', 'agri.normalized_source_feature', 'INSERT')")
            )
            if not granted:
                pytest.skip(
                    "agri.normalized_source_feature grants for plantgeo_loader are missing; run "
                    "infra/local-warehouse/grant-resolution-aware-loader.sql against this database first"
                )
            before = await _protected_state(connection)
            await connection.execute(text("SET LOCAL ROLE plantgeo_loader"))
            assert await connection.scalar(text("SELECT current_user")) == "plantgeo_loader"
            assert not await connection.scalar(
                text("SELECT has_table_privilege(current_user, 'agri.source_release', 'UPDATE')")
            )
            assert not await connection.scalar(
                text("SELECT has_table_privilege(current_user, 'agri.artifact', 'UPDATE')")
            )

        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(text("SET LOCAL ROLE plantgeo_loader"))
                first = await ingest_boise_intervention_pilot(
                    session,
                    plan_path=plan_path,
                    capture_base=capture_base,
                )
            async with session.begin():
                await session.execute(text("SET LOCAL ROLE plantgeo_loader"))
                second = await ingest_boise_intervention_pilot(
                    session,
                    plan_path=plan_path,
                    capture_base=capture_base,
                )
            assert first == second
            assert first.normalized_feature_count == 7
            assert first.evidence_counts == {
                "observed_fact": 2,
                "model_derived_feature": 6,
                "known_gap": 9,
            }
            assert not first.publication_advanced
            assert not first.life_safety_prediction

        async with engine.connect() as connection:
            assert await _protected_state(connection) == before
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM agri.intervention_evidence_input "
                        "WHERE is_life_safety_validated OR evidence_kind NOT IN "
                        "('observed_fact', 'model_derived_feature', 'known_gap')"
                    )
                )
                == 0
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM agri.intervention_analysis_run WHERE is_life_safety_prediction")
                )
                == 0
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM agri.artifact "
                        "WHERE source_release_id = :source_release_id "
                        "AND kind = 'source_metadata_reference' "
                        "AND checksum_sha256 = "
                        "'b8f3e0e9a7312bdbcf796156a4257eebe1daec8f6880e7c5ead3de0552ca13e0' "
                        "AND encode(public.digest(content_bytes, 'sha256'), 'hex') "
                        "= checksum_sha256"
                    ),
                    {"source_release_id": first.source_release_ids["osm-hillside-to-hollow-20260723"]},
                )
                == 1
            )

        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                text(
                    "UPDATE agri.source_release SET validation_state = 'retracted', "
                    "retraction_reason = 'disposable lifecycle proof' "
                    "WHERE id = :source_release_id"
                ),
                {"source_release_id": next(iter(first.source_release_ids.values()))},
            )
            await transaction.rollback()

        async with engine.connect() as connection:
            transaction = await connection.begin()
            critical_updates = (
                (
                    "UPDATE agri.source_release SET payload_checksum = repeat('0', 64) WHERE id = :record_id",
                    next(iter(first.source_release_ids.values())),
                    "frozen by intervention evidence lineage",
                ),
                (
                    "UPDATE agri.artifact SET metadata_json = '{}'::jsonb "
                    "WHERE source_release_id = :record_id "
                    "AND kind = 'source_metadata_reference'",
                    first.source_release_ids["osm-hillside-to-hollow-20260723"],
                    "frozen by intervention evidence lineage",
                ),
                (
                    "UPDATE agri.release_set SET description = 'drift' WHERE id = :record_id",
                    first.release_set_id,
                    "frozen by intervention evidence lineage",
                ),
                (
                    "UPDATE agri.release_set SET state = 'published', published_at = now() WHERE id = :record_id",
                    first.release_set_id,
                    "validated release set identity is immutable",
                ),
                (
                    "UPDATE agri.release_set_item SET source_role = 'drift' WHERE release_set_id = :record_id",
                    first.release_set_id,
                    "release set membership is immutable after validation",
                ),
            )
            for statement, record_id, expected_error in critical_updates:
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError, match=expected_error):
                    await connection.execute(text(statement), {"record_id": record_id})
                await savepoint.rollback()
            await transaction.rollback()
    finally:
        await engine.dispose()
