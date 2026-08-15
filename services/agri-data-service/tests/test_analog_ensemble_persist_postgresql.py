"""PostgreSQL contract proof that the AnEn training receipt chain writes end-to-end.

Seeds a minimal governed fixture directly against the disposable database (a spatial cell,
90 days of meteorology `signal_observation` history for the seven `agri_covariates_v1`
meteorology signals, weekly-cadence USDM drought coverage so the whole-row completeness mask
can resolve, a validated `release_set`, an active `forecast_quality_policy`, and a
`forecast_series`), then drives `analog_ensemble_persist.run_analog_ensemble_training(...,
persist=True)` through the real async engine path -- the same path
`analog_ensemble_cli.forecast_train_anen` uses -- and asserts `agri.validate_forecast_training_run`
actually returns `'validated'` on a real server, not a fake session.

Rolled back on teardown; nothing here is meant to persist. A separate, deliberately COMMITTING
run of this same shape (see the track's evidence report) is what produced the durable receipt
ids recorded there.
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.execution.analog_ensemble_persist import AnEnTrainingRequest, run_analog_ensemble_training
from agri_data_service.method.ml.analog_ensemble import AnEnHyperparameters

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import TextClause

DAY_COUNT = 90
HISTORY_START = datetime(2024, 1, 1, tzinfo=UTC)
METEOROLOGY_AVAILABLE_AT = HISTORY_START + timedelta(days=DAY_COUNT + 1)
DROUGHT_AVAILABLE_AT = HISTORY_START + timedelta(days=DAY_COUNT + 1)
AS_OF_TIME = HISTORY_START + timedelta(days=DAY_COUNT + 5)
ORIGIN_INDEX = 85
HORIZON_DAYS = 5
K_NEIGHBORS = 3

METEOROLOGY_SIGNALS = (
    ("air_temperature_max", "T2M_MAX", "C", 25.0),
    ("air_temperature_mean", "T2M", "C", 18.0),
    ("air_temperature_min", "T2M_MIN", "C", 10.0),
    ("dew_point_temperature", "T2MDEW", "C", 8.0),
    ("precipitation", "PRECTOTCORR", "mm", 2.0),
    ("relative_humidity", "RH2M", "%", 55.0),
    ("wind_speed", "WS2M", "m/s", 3.5),
)
COVERING_MULTIPOLYGON_WKT = "MULTIPOLYGON(((-117.0 43.0, -115.0 43.0, -115.0 44.0, -117.0 44.0, -117.0 43.0)))"


def _checksum(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _scalar_uuid(session: AsyncSession, statement: TextClause, parameters: dict[str, object]) -> uuid.UUID:
    """Run a statement expected to yield exactly one id, returning it as a real UUID."""
    value = (await session.execute(statement, parameters)).scalar_one()
    return uuid.UUID(str(value))


async def _seed_spatial_cell(session: AsyncSession, suffix: str) -> uuid.UUID:
    return await _scalar_uuid(
        session,
        text(
            """
            INSERT INTO agri.spatial_cell(cell_key, grid_name, resolution_m, geometry, centroid)
            VALUES (
                :cell_key, 'anen-persist-contract-grid', 50000,
                ST_GeomFromText(
                    'POLYGON((-116.5 43.3, -115.7 43.3, -115.7 44.0, -116.5 44.0, -116.5 43.3))', 4326
                ),
                ST_GeomFromText('POINT(-116.1 43.65)', 4326)
            )
            RETURNING id
            """
        ),
        {"cell_key": f"anen-persist-contract:{suffix}"},
    )


async def _seed_meteorology(session: AsyncSession, suffix: str, cell_id: uuid.UUID) -> uuid.UUID:
    """One validated meteorology release covering all seven `agri_covariates_v1` signals."""
    data_source_id = await _scalar_uuid(
        session,
        text(
            """
            INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
            VALUES (:key, 'AnEn persist contract', 'PlantGeo test', 'rolled-back AnEn receipt validation',
                    'test-only', 'test-only')
            RETURNING id
            """
        ),
        {"key": f"anen-persist-contract-{suffix}"},
    )
    source_release_id = (
        await session.execute(
            text(
                """
                INSERT INTO agri.source_release(
                    data_source_id, source_version, retrieved_at, data_available_at,
                    observed_from, observed_to, payload_checksum, schema_version,
                    transform_version, license_snapshot, validation_state, validated_at
                )
                VALUES (
                    :data_source_id, :source_version, :available_at, :available_at,
                    :observed_from, :observed_to, :checksum, 'fixture-v1',
                    'anen-persist-contract-normalization-v1', 'test-only', 'valid', :available_at
                )
                RETURNING id
                """
            ),
            {
                "data_source_id": data_source_id,
                "source_version": f"anen-persist-contract-v1:{suffix}",
                "available_at": METEOROLOGY_AVAILABLE_AT,
                "observed_from": HISTORY_START,
                "observed_to": HISTORY_START + timedelta(days=DAY_COUNT - 1),
                "checksum": _checksum(f"anen-meteorology:{suffix}"),
            },
        )
    ).scalar_one()

    rows = []
    for day in range(DAY_COUNT):
        observed_at = HISTORY_START + timedelta(days=day)
        for signal_name, source_parameter, unit, base in METEOROLOGY_SIGNALS:
            value = base + (day * 0.02) + 2.0 * math.sin(day * 0.31)
            rows.append(
                {
                    "source_release_id": source_release_id,
                    "cell_id": cell_id,
                    "signal_name": signal_name,
                    "source_parameter": source_parameter,
                    "observed_at": observed_at,
                    "data_available_at": METEOROLOGY_AVAILABLE_AT,
                    "value": value,
                    "unit": unit,
                }
            )
    await session.execute(
        text(
            """
            INSERT INTO agri.signal_observation(
                source_release_id, cell_id, signal_name, source_parameter, observed_at,
                data_available_at, original_value, normalized_value, is_observed,
                original_unit, normalized_unit
            )
            VALUES (
                :source_release_id, :cell_id, :signal_name, :source_parameter, :observed_at,
                :data_available_at, :value, :value, true, :unit, :unit
            )
            """
        ),
        rows,
    )
    return uuid.UUID(str(data_source_id))


async def _seed_drought_coverage(session: AsyncSession, suffix: str) -> None:
    """Weekly-cadence covering polygons so the whole-row completeness mask can resolve.

    Without this, `drought_severity_class_lag_1`/`_lag_7` stay NULL for every day (an
    unresolved drought class is never coerced to class 0 -- see `execution/AGENTS.md`), which
    would mark every single row incomplete and leave nothing for AnEn to train on.
    """
    data_source_id = (
        await session.execute(
            text(
                """
                INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
                VALUES (:key, 'AnEn persist contract (USDM)', 'PlantGeo test',
                        'rolled-back AnEn receipt validation', 'test-only', 'test-only')
                RETURNING id
                """
            ),
            {"key": f"anen-persist-contract-usdm-{suffix}"},
        )
    ).scalar_one()
    source_release_id = (
        await session.execute(
            text(
                """
                INSERT INTO agri.source_release(
                    data_source_id, source_version, retrieved_at, data_available_at,
                    observed_from, observed_to, payload_checksum, schema_version,
                    transform_version, license_snapshot, validation_state, validated_at
                )
                VALUES (
                    :data_source_id, :source_version, :available_at, :available_at,
                    :observed_from, :observed_to, :checksum, 'fixture-v1',
                    'anen-persist-contract-usdm-normalization-v1', 'test-only', 'valid', :available_at
                )
                RETURNING id
                """
            ),
            {
                "data_source_id": data_source_id,
                "source_version": f"anen-persist-contract-usdm-v1:{suffix}",
                "available_at": DROUGHT_AVAILABLE_AT,
                "observed_from": HISTORY_START,
                "observed_to": HISTORY_START + timedelta(days=DAY_COUNT - 1),
                "checksum": _checksum(f"anen-drought:{suffix}"),
            },
        )
    ).scalar_one()

    issue_date = HISTORY_START.date() - timedelta(days=7)
    last_issue = HISTORY_START.date() + timedelta(days=DAY_COUNT)
    week_index = 0
    while issue_date <= last_issue:
        await session.execute(
            text(
                """
                INSERT INTO agri.drought_polygon_snapshot(
                    source_release_id, issue_date, severity_class, impact_type,
                    geometry, geometry_checksum, data_available_at
                )
                VALUES (:release_id, :issue_date, 1, 'none', ST_GeomFromText(:wkt, 4326), :checksum, :available_at)
                """
            ),
            {
                "release_id": source_release_id,
                "issue_date": issue_date,
                "wkt": COVERING_MULTIPOLYGON_WKT,
                "checksum": _checksum(f"anen-drought:{suffix}:{week_index}"),
                "available_at": DROUGHT_AVAILABLE_AT,
            },
        )
        issue_date += timedelta(days=7)
        week_index += 1


async def _seed_release_set_and_policy(session: AsyncSession, suffix: str) -> str:
    await session.execute(
        text(
            """
            INSERT INTO agri.release_set(logical_key, as_of_time, manifest_checksum, state, validated_at)
            VALUES (:logical_key, :as_of_time, :checksum, 'validated', :as_of_time)
            """
        ),
        {
            "logical_key": f"anen-persist-contract-release-set:{suffix}",
            "as_of_time": METEOROLOGY_AVAILABLE_AT,
            "checksum": _checksum(f"anen-release-set:{suffix}"),
        },
    )
    policy_key = f"anen-persist-contract-policy-{suffix}"
    await session.execute(
        text(
            """
            INSERT INTO agri.forecast_quality_policy(
                policy_key, min_training_points, min_backtest_points, min_coverage_fraction,
                required_quantiles, is_active
            )
            VALUES (:policy_key, 3, 1, 0.5, ARRAY[0.1, 0.5, 0.9], true)
            """
        ),
        {"policy_key": policy_key},
    )
    return policy_key


async def _seed_forecast_series(
    session: AsyncSession, suffix: str, *, data_source_id: uuid.UUID, cell_id: uuid.UUID
) -> uuid.UUID:
    return await _scalar_uuid(
        session,
        text(
            """
            INSERT INTO agri.forecast_series(
                series_key, source_variant_key, input_adapter, data_source_id,
                signal_name, source_parameter, support_key, source_transform_version,
                entity_type, entity_key, metric_name, metric_unit,
                spatial_cell_id, representation_kind, spatial_support_kind,
                source_temporal_support, output_temporal_support
            )
            VALUES (
                :series_key, :source_version, 'signal_observation', :data_source_id,
                'wind_speed', 'WS2M', 'surface', 'anen-persist-contract-normalization-v1',
                'anen_persist_contract_cell', :entity_key, 'wind_speed', 'm/s',
                :cell_id, 'raw_native', 'point_sample', interval '1 day', interval '1 day'
            )
            RETURNING id
            """
        ),
        {
            "series_key": f"anen-persist-contract-series:{suffix}",
            "source_version": f"anen-persist-contract-v1:{suffix}",
            "data_source_id": data_source_id,
            "entity_key": f"anen-persist-contract:{suffix}",
            "cell_id": cell_id,
        },
    )


async def test_persist_training_receipt_validates_end_to_end_on_a_real_server(agri_db_async_dsn: str) -> None:
    suffix = os.urandom(8).hex()
    engine = create_async_engine(agri_db_async_dsn)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session, session.begin():
            cell_id = await _seed_spatial_cell(session, suffix)
            data_source_id = await _seed_meteorology(session, suffix, cell_id)
            await _seed_drought_coverage(session, suffix)
            policy_key = await _seed_release_set_and_policy(session, suffix)
            series_id = await _seed_forecast_series(session, suffix, data_source_id=data_source_id, cell_id=cell_id)

            request = AnEnTrainingRequest(
                cell_id=str(cell_id),
                series_id=str(series_id),
                history_start=HISTORY_START.date(),
                history_end=(HISTORY_START + timedelta(days=DAY_COUNT - 1)).date(),
                origin_date=(HISTORY_START + timedelta(days=ORIGIN_INDEX)).date(),
                as_of_time=AS_OF_TIME,
                hyperparams=AnEnHyperparameters(
                    k_neighbors=K_NEIGHBORS, temporal_exclusion_days=5, horizon_days=HORIZON_DAYS
                ),
                origin_count=1,
                quality_policy_key=policy_key,
            )

            report = await run_analog_ensemble_training(session, request, persist=True)

            assert report.persisted is True
            assert report.receipt is not None
            assert report.receipt.training_run_status == "validated"
            assert report.receipt.feature_snapshot_status == "validated"
            assert report.receipt.backtest_metric_count >= 1

            # Independently re-read the row the receipt claims -- proving the server itself
            # recorded 'validated', not merely that the Python-side dataclass says so.
            server_status = (
                await session.execute(
                    text("SELECT status FROM agri.forecast_training_run WHERE id = :id"),
                    {"id": report.receipt.training_run_id},
                )
            ).scalar_one()
            assert server_status == "validated"

            server_run_status = (
                await session.execute(
                    text("SELECT status FROM agri.forecast_run WHERE id = :id"),
                    {"id": report.receipt.forecast_run_id},
                )
            ).scalar_one()
            assert server_run_status == "staged", "the lane never calls agri.validate_forecast_run"

            await session.rollback()
    finally:
        await engine.dispose()
