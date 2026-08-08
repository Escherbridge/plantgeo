"""Readiness must describe a migrated, correctly provisioned database -- and nothing about roles.

Revision ``20260808_0019`` retired the ``plantgeo_forecast_*`` capability family and the
calling-login privilege matrix that /ready used to assert. The tests that pinned that matrix are
gone with it; what remains are the two probes that still protect something real (extensions and
the pinned Alembic revision) plus a guard against the role assertions coming back by accident.
"""

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
    def __init__(
        self,
        *,
        migration_ready: bool = True,
        extensions_ready: bool = True,
        serving_surface_ready: bool = True,
    ) -> None:
        self.migration_ready = migration_ready
        self.extensions_ready = extensions_ready
        self.serving_surface_ready = serving_surface_ready
        self.calls = 0

    async def execute(self, _statement: object, _parameters: object = None) -> _Result:
        self.calls += 1
        if self.calls == 1:
            return _Result(
                {
                    "extensions_ready": self.extensions_ready,
                    "migration_catalog_ready": True,
                    "serving_surface_ready": self.serving_surface_ready,
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


def test_readiness_contract_pins_the_extensions_this_build_requires() -> None:
    assert set(health_route.REQUIRED_EXTENSIONS) == {
        "postgis",
        "timescaledb",
        "vector",
        "pgcrypto",
    }
    assert "pg_extension" in health_route._READINESS_SQL
    assert "public.alembic_version" in health_route._READINESS_SQL


def test_readiness_sql_no_longer_asserts_any_retired_role_contract() -> None:
    """Non-vacuous: the substrings below are the exact ones the deleted sections contained."""
    for retired_fragment in (
        "plantgeo_forecast",
        "pg_auth_members",
        "has_column_privilege",
        "has_sequence_privilege",
        "has_function_privilege",
        "aclexplode",
        "pg_has_role",
    ):
        assert retired_fragment not in health_route._READINESS_SQL, retired_fragment
    assert not hasattr(health_route, "FORECAST_ROLES")
    assert not hasattr(health_route, "PUBLICATION_TABLE_PRIVILEGES")


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
            "serving_surface": True,
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
    assert payload["checks"]["extensions"] is True


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
async def test_readiness_fails_when_a_required_extension_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(extensions_ready=False)
    monkeypatch.setattr(health_route, "published_reader_session", _session_factory(session))
    monkeypatch.setattr(health_route.settings, "service_profile", "published_reader")

    response = await health_route.readiness_check(None)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["checks"]["extensions"] is False
    assert payload["checks"]["migration"] is True


@pytest.mark.asyncio
async def test_readiness_fails_when_the_login_cannot_reach_the_serving_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one capability conjunct kept after 20260808_0019: schema USAGE + the serving view."""
    session = _Session(serving_surface_ready=False)
    monkeypatch.setattr(health_route, "published_reader_session", _session_factory(session))
    monkeypatch.setattr(health_route.settings, "service_profile", "published_reader")

    response = await health_route.readiness_check(None)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["checks"]["serving_surface"] is False
    assert payload["checks"]["extensions"] is True


@pytest.mark.asyncio
async def test_readiness_fails_when_the_pinned_revision_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(migration_ready=False)
    monkeypatch.setattr(health_route, "published_reader_session", _session_factory(session))
    monkeypatch.setattr(health_route.settings, "service_profile", "published_reader")

    response = await health_route.readiness_check(None)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["checks"]["migration"] is False
