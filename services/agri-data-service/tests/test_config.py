"""Focused configuration contracts for local publication and migrations."""

import pytest
from pydantic import ValidationError

from agri_data_service.config import Settings

_TOKEN = "0123456789abcdefghijklmnopqrstuvwxyzABCDEF"


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_operator_token_does_not_enable_receiver() -> None:
    configured = _settings(local_publish_token=_TOKEN)

    assert configured.local_publication_receiver_enabled is False
    assert configured.local_publish_actor is None


def test_enabled_receiver_requires_credential_identity() -> None:
    with pytest.raises(ValidationError, match="LOCAL_PUBLISH_ACTOR"):
        _settings(
            local_publication_receiver_enabled=True,
            local_publish_token=_TOKEN,
        )


def test_receiver_actor_is_normalized_and_bounded() -> None:
    configured = _settings(
        local_publication_receiver_enabled=True,
        local_publish_token=_TOKEN,
        local_publish_actor="  plantgeo-local-forecast-publisher  ",
    )

    assert configured.local_publish_actor == "plantgeo-local-forecast-publisher"

    with pytest.raises(ValidationError, match="control characters"):
        _settings(local_publish_token=_TOKEN, local_publish_actor="publisher\nadmin")


def test_migration_url_requires_a_synchronous_driver() -> None:
    configured = _settings(database_url_sync="postgres://migration@example.test/plantgeo")
    assert configured.database_url_sync.startswith("postgresql://")

    with pytest.raises(ValidationError, match="synchronous PostgreSQL driver"):
        _settings(database_url_sync="postgresql+asyncpg://migration@example.test/plantgeo")


def test_source_loader_requires_an_explicit_isolated_local_compose_target() -> None:
    target = "postgresql+asyncpg://plantgeo_loader:password@127.0.0.1:5442/plantgeo"

    with pytest.raises(ValueError, match="LOCAL_SOURCE_LOADER_DATABASE_URL"):
        _settings().require_local_source_loader_database_url()

    configured = _settings(local_source_loader_database_url=target)
    assert configured.require_local_source_loader_database_url() == target

    with pytest.raises(ValueError, match="must not reuse DATABASE_URL"):
        _settings(
            database_url=target,
            local_source_loader_database_url=target,
        ).require_local_source_loader_database_url()

    with pytest.raises(ValueError, match=r"127\.0\.0\.1:5442/plantgeo"):
        _settings(
            local_source_loader_database_url="postgresql+asyncpg://plantgeo_loader:password@127.0.0.1:5432/plantgeo"
        ).require_local_source_loader_database_url()

    with pytest.raises(ValueError, match="must not use the plantgeo_owner"):
        _settings(
            local_source_loader_database_url="postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeo"
        ).require_local_source_loader_database_url()
