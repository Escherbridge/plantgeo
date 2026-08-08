"""Disposable PostgreSQL proof for the governed Boise pilot writer.

Local-dev-only: the plan file's ``capture_base`` (``.agri-local-runs/``) is
gitignored, so this test only runs where that local capture already exists.
There is no CI for this suite; it is operator-run via pytest against a live
PostgreSQL 16 instance.

Gated on ``AGRI_TEST_DATABASE_URL`` plus the local capture, which a fresh clone
does not have. It used to carry the ``agri_db_manual_grant`` marker because it
ran the writer under a ``SET LOCAL ROLE plantgeo_loader`` custody scope that
needed an operator-run grant runbook first; the 2026-08-08 owner ruling retired
role management and the writer now runs under the owner credential like every
other command. The role-scoping and ``has_table_privilege`` assertions went with
it; every functional assertion below is unchanged.
"""

# ruff: noqa: PLR2004

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.execution.geospatial_pilot import ingest_boise_intervention_pilot

PROTECTED_COUNT_SQL = {
    "forecast_run": text("SELECT count(*) FROM agri.forecast_run"),
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


@pytest.mark.asyncio
async def test_pilot_writer_is_idempotent_and_nonpublishing(agri_db_async_dsn: str) -> None:
    service_root = Path(__file__).resolve().parents[1]
    repository_root = service_root.parents[1]
    plan_path = service_root / "plans" / "boise-intervention-capture-v1.json"
    capture_base = repository_root / ".agri-local-runs" / "north-america-intervention"
    if not capture_base.is_dir():
        pytest.skip(f"local pilot capture is absent: {capture_base} (gitignored; not in a fresh clone)")
    engine = create_async_engine(agri_db_async_dsn)
    try:
        async with engine.connect() as connection:
            before = await _protected_state(connection)

        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            async with session.begin():
                first = await ingest_boise_intervention_pilot(
                    session,
                    plan_path=plan_path,
                    capture_base=capture_base,
                )
            async with session.begin():
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

    finally:
        await engine.dispose()
