"""Readiness must describe a configured least-privilege publication plane."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

import psycopg2
import pytest

from agri_data_service.routes import health as health_route


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def mappings(self) -> "_Result":
        return self

    def one(self) -> dict[str, object]:
        assert isinstance(self.value, dict)
        return self.value

    def scalar_one(self) -> object:
        return self.value


class _Session:
    def __init__(self, *, migration_ready: bool = True) -> None:
        self.migration_ready = migration_ready
        self.calls = 0

    async def execute(self, _statement: object, _parameters: object = None) -> _Result:
        self.calls += 1
        if self.calls == 1:
            return _Result(
                {
                    "extensions_ready": True,
                    "migration_catalog_ready": True,
                    "tables_ready": True,
                    "privileges_ready": True,
                    "forecast_roles_ready": True,
                }
            )
        if _parameters is not None:
            return _Result(self.migration_ready)
        return _Result(True)


def _session_factory(session: _Session) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[_Session]:
        yield session

    return factory


def test_readiness_contract_covers_exact_publication_surface() -> None:
    expected = {
        ("agri", "release_set", "SELECT"),
        ("agri", "job_definition", "SELECT"),
        ("agri", "job_definition", "INSERT"),
        ("agri", "job_run", "SELECT"),
        ("agri", "job_run", "INSERT"),
        ("agri", "job_run", "UPDATE"),
        ("agri", "job_output", "SELECT"),
        ("agri", "job_output", "INSERT"),
        ("agri", "job_output", "UPDATE"),
        ("agri", "artifact", "SELECT"),
        ("agri", "artifact", "INSERT"),
        ("agri", "publication_pointer", "SELECT"),
        ("agri", "publication_pointer", "INSERT"),
        ("agri", "publication_pointer", "UPDATE"),
        ("agri", "job_outbox", "SELECT"),
        ("agri", "job_outbox", "INSERT"),
    }
    expected |= {
        ("agri", "release_set", "INSERT"),
        ("agri", "release_set", "UPDATE"),
        ("agri", "data_source", "SELECT"),
        ("agri", "data_source", "INSERT"),
        ("agri", "source_release", "SELECT"),
        ("agri", "source_release", "INSERT"),
        ("agri", "release_set_item", "SELECT"),
        ("agri", "release_set_item", "INSERT"),
        ("agri", "spatial_cell", "SELECT"),
        ("agri", "spatial_cell", "INSERT"),
        ("agri", "cell_source_crosswalk", "SELECT"),
        ("agri", "cell_source_crosswalk", "INSERT"),
        ("agri", "signal_observation", "SELECT"),
        ("agri", "signal_observation", "INSERT"),
        ("agri", "signal_coverage_audit", "SELECT"),
        ("agri", "signal_coverage_audit", "INSERT"),
        ("agri", "source_coverage_audit", "SELECT"),
        ("agri", "source_coverage_audit", "INSERT"),
        ("agri", "drought_polygon_snapshot", "SELECT"),
        ("agri", "drought_polygon_snapshot", "INSERT"),
        ("agri", "historical_promotion_bundle", "SELECT"),
        ("agri", "historical_promotion_bundle", "INSERT"),
        ("agri", "historical_promotion_bundle", "UPDATE"),
        ("agri", "historical_promotion_chunk_receipt", "SELECT"),
        ("agri", "historical_promotion_chunk_receipt", "INSERT"),
        ("agri", "historical_promotion_data_source_receipt", "SELECT"),
        ("agri", "historical_promotion_data_source_receipt", "INSERT"),
        ("agri", "historical_promotion_source_release_receipt", "SELECT"),
        ("agri", "historical_promotion_source_release_receipt", "INSERT"),
        ("agri", "historical_promotion_artifact_receipt", "SELECT"),
        ("agri", "historical_promotion_artifact_receipt", "INSERT"),
        ("agri", "historical_promotion_artifact_receipt", "UPDATE"),
    }

    assert set(health_route.PUBLICATION_TABLE_PRIVILEGES) == expected


def test_receiver_readiness_requires_exact_historical_identity_sequences() -> None:
    assert set(health_route.RECEIVER_SEQUENCE_PRIVILEGES) == {
        ("agri", "signal_observation_id_seq", "USAGE"),
        ("agri", "signal_observation_id_seq", "SELECT"),
        ("agri", "drought_polygon_snapshot_id_seq", "USAGE"),
        ("agri", "drought_polygon_snapshot_id_seq", "SELECT"),
    }
    assert "agri_sequences" in health_route._RECEIVER_ROLE_BOUNDARY_SQL
    assert "has_any_column_privilege" in health_route._RECEIVER_ROLE_BOUNDARY_SQL
    assert "agri_functions" in health_route._RECEIVER_ROLE_BOUNDARY_SQL
    assert set(health_route.REQUIRED_EXTENSIONS) == {
        "postgis",
        "timescaledb",
        "vector",
        "pgcrypto",
    }


def test_expected_alembic_revision_matches_migrated_head_database(agri_db_dsn: str) -> None:
    """Non-vacuous: compares the readiness constant to the live migrated database, not to itself."""
    connection = psycopg2.connect(agri_db_dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM public.alembic_version")
            (revision,) = cursor.fetchone()
    finally:
        connection.close()
    assert revision == health_route.EXPECTED_ALEMBIC_REVISION


def test_readiness_contract_enforces_separate_forecast_capabilities() -> None:
    assert health_route.FORECAST_ROLES == (
        "plantgeo_forecast_writer",
        "plantgeo_forecast_publisher",
        "plantgeo_forecast_reader",
        "plantgeo_forecast_mv_refresher",
    )
    reader_relations = {
        (schema_name, relation_name, privilege_name)
        for role_name, schema_name, relation_name, privilege_name in (health_route.FORECAST_ROLE_RELATION_PRIVILEGES)
        if role_name == "plantgeo_forecast_reader"
    }
    assert reader_relations == {
        ("agri", "v_forecast_series_serving", "SELECT"),
        ("agri", "mv_forecast_ml_daily_serving", "SELECT"),
        ("agri", "spatial_cell", "SELECT"),
    }
    refresher_relations = {
        (schema_name, relation_name, privilege_name)
        for role_name, schema_name, relation_name, privilege_name in (health_route.FORECAST_ROLE_RELATION_PRIVILEGES)
        if role_name == "plantgeo_forecast_mv_refresher"
    }
    assert refresher_relations == {
        ("agri", "mv_forecast_ml_daily_serving", "SELECT"),
    }
    assert set(health_route.FORECAST_ROLE_SEQUENCE_PRIVILEGES) == {
        ("plantgeo_forecast_writer", "agri", "forecast_observation_id_seq", "USAGE"),
        ("plantgeo_forecast_writer", "agri", "forecast_observation_id_seq", "SELECT"),
        ("plantgeo_forecast_writer", "agri", "forecast_value_id_seq", "USAGE"),
        ("plantgeo_forecast_writer", "agri", "forecast_value_id_seq", "SELECT"),
    }
    function_roles = {role_name for role_name, _signature, _privilege in health_route.FORECAST_ROLE_FUNCTION_PRIVILEGES}
    # The reader is absent: its only EXECUTE was `forecast_hindcast_signal_timeseries`,
    # which left with the hindcast plane in 20260803_0018. It is now a relation-only role,
    # and `_READINESS_SQL` asserts it holds EXECUTE on no agri function at all.
    assert function_roles == {
        "plantgeo_forecast_writer",
        "plantgeo_forecast_publisher",
        "plantgeo_forecast_mv_refresher",
    }
    assert "plantgeo_forecast_reader" in health_route.FORECAST_ROLES
    assert "forecast_hindcast" not in health_route._READINESS_SQL
    assert (
        "FROM agri_functions AS function_object\n"
        "            WHERE has_function_privilege(current_user, function_object.oid, 'EXECUTE')"
    ) in health_route._READINESS_SQL
    assert "aclexplode" in health_route._READINESS_SQL
    assert "has_column_privilege" in health_route._READINESS_SQL
    assert "pg_auth_members" in health_route._READINESS_SQL
    assert "WHERE membership.admin_option" in health_route._READINESS_SQL
    assert "membership.inherit_option" in health_route._READINESS_SQL
    assert "EXECUTE WITH GRANT OPTION" in health_route._READINESS_SQL
    assert "candidate.privilege_name || ' WITH GRANT OPTION'" in health_route._READINESS_SQL
    assert "WHERE role.role_name = 'plantgeo_forecast_reader'" in health_route._READINESS_SQL
    assert "LEFT JOIN pg_roles AS role" in health_route._READINESS_SQL
    assert "database.datdba" in health_route._READINESS_SQL
    assert "namespace.nspowner" in health_route._READINESS_SQL


@pytest.mark.asyncio
async def test_readiness_requires_enabled_receiver_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    monkeypatch.setattr(health_route, "receiver_writer_session", _session_factory(session))
    monkeypatch.setattr(health_route.settings, "service_profile", "receiver_writer")
    monkeypatch.setattr(health_route.settings, "local_publication_receiver_enabled", False)
    monkeypatch.setattr(health_route.settings, "historical_promotion_receiver_enabled", False)
    monkeypatch.setattr(health_route.settings, "local_publish_actor", None)

    response = await health_route.readiness_check(None)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["profile"] == "receiver_writer"
    assert payload["checks"]["receiver_identity"] is False
    assert payload["checks"]["migration"] is True
    assert "token" not in response.body.decode().lower()


@pytest.mark.asyncio
async def test_readiness_passes_only_when_every_check_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    monkeypatch.setattr(health_route, "receiver_writer_session", _session_factory(session))
    monkeypatch.setattr(health_route.settings, "service_profile", "receiver_writer")
    monkeypatch.setattr(health_route.settings, "local_publication_receiver_enabled", True)
    monkeypatch.setattr(health_route.settings, "local_publish_actor", "publisher")
    assert health_route.settings.local_publish_token is None
    monkeypatch.setattr(health_route.settings, "local_publish_token", object())

    response = await health_route.readiness_check(None)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status == HTTPStatus.OK
    assert payload == {
        "status": "ready",
        "profile": "receiver_writer",
        "checks": {
            "database_profile": True,
            "receiver_identity": True,
            "extensions": True,
            "migration": True,
            "profile_tables": True,
            "runtime_privileges": True,
            "forecast_role_contracts": True,
        },
    }


@pytest.mark.asyncio
async def test_published_reader_readiness_does_not_require_receiver_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    monkeypatch.setattr(health_route, "published_reader_session", _session_factory(session))
    monkeypatch.setattr(health_route.settings, "service_profile", "published_reader")
    monkeypatch.setattr(health_route.settings, "local_publication_receiver_enabled", False)
    monkeypatch.setattr(health_route.settings, "historical_promotion_receiver_enabled", False)
    monkeypatch.setattr(health_route.settings, "local_publish_actor", None)
    monkeypatch.setattr(health_route.settings, "historical_promotion_actor", None)

    response = await health_route.readiness_check(None)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status == HTTPStatus.OK
    assert payload["profile"] == "published_reader"
    assert payload["checks"]["receiver_identity"] is True
    assert payload["checks"]["runtime_privileges"] is True


@pytest.mark.asyncio
async def test_combined_local_profile_never_reports_production_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_route.settings, "service_profile", "combined_local")

    response = await health_route.readiness_check(None)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["profile"] == "combined_local"
    assert payload["checks"]["database_profile"] is False


@pytest.mark.asyncio
async def test_readiness_fails_when_forecast_role_contract_drifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()

    async def execute_with_role_drift(
        _statement: object,
        _parameters: object = None,
    ) -> _Result:
        session.calls += 1
        if session.calls == 1:
            return _Result(
                {
                    "extensions_ready": True,
                    "migration_catalog_ready": True,
                    "tables_ready": True,
                    "privileges_ready": True,
                    "forecast_roles_ready": False,
                }
            )
        if _parameters is not None:
            return _Result(True)
        return _Result(True)

    session.execute = execute_with_role_drift  # type: ignore[method-assign]
    monkeypatch.setattr(health_route, "published_reader_session", _session_factory(session))
    monkeypatch.setattr(health_route.settings, "service_profile", "published_reader")

    response = await health_route.readiness_check(None)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["checks"]["forecast_role_contracts"] is False
