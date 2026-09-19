"""Settings refuse to construct while any database variable is in the environment (decision D5)."""

from __future__ import annotations

import os

import pytest

from plantgeo_ml_service.config import (
    DATABASE_REFUSAL_MESSAGE,
    DATABASE_VARIABLE_MARKERS,
    Settings,
    names_a_database_variable,
)


@pytest.fixture(autouse=True)
def without_inherited_database_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every case from an environment the refusal has not already fired on.

    A developer shell commonly carries `DATABASE_URL` for the sibling service; without this the
    positive cases below would fail for a reason that has nothing to do with what they assert.
    """
    for name in list(os.environ):
        if names_a_database_variable(name):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "variable_name",
    [
        "DATABASE_URL",
        "AGRI_TEST_DATABASE_URL",
        "LOCAL_SOURCE_LOADER_DATABASE_URL",
        "database_url",
        "PGHOST",
        "PGDATABASE",
        "POSTGRES_DSN",
        "DATABASE_DSN",
    ],
)
def test_any_database_variable_refuses_to_boot(monkeypatch: pytest.MonkeyPatch, variable_name: str) -> None:
    monkeypatch.setenv(variable_name, "postgresql+asyncpg://user@host:5432/plantgeo")

    with pytest.raises(ValueError, match="zero-Postgres by owner decision D5"):
        Settings(_env_file=None)


def test_the_refusal_names_the_offending_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORECAST_MV_REFRESH_DATABASE_URL", "postgresql://host/db")

    with pytest.raises(ValueError, match="FORECAST_MV_REFRESH_DATABASE_URL"):
        Settings(_env_file=None)


def test_settings_construct_with_no_database_variable_present() -> None:
    settings = Settings(_env_file=None)

    assert settings.kernels == "python"
    assert settings.ml_prefix == "ml/"
    assert settings.object_store_region == "auto"


def test_an_unconfigured_object_store_names_every_missing_variable() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(ValueError, match="OBJECT_STORE_ENDPOINT_URL"):
        settings.require_object_store()


@pytest.mark.parametrize("marker", DATABASE_VARIABLE_MARKERS)
def test_every_declared_marker_is_actually_refused(monkeypatch: pytest.MonkeyPatch, marker: str) -> None:
    """The marker tuple is the contract; a name added there but not enforced is a silent hole."""
    monkeypatch.setenv(f"SOME_{marker}_VARIABLE", "anything")

    with pytest.raises(ValueError, match="zero-Postgres by owner decision D5"):
        Settings(_env_file=None)


def test_the_refusal_message_is_the_one_the_decision_records() -> None:
    assert DATABASE_REFUSAL_MESSAGE == "plantgeo-ml-service is zero-Postgres by owner decision D5 (2026-09-18)"
