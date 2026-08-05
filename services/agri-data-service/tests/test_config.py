"""Focused configuration contracts for local publication, migrations, and the cron ingest DSN."""

import asyncio

import pytest
from pydantic import ValidationError

import agri_data_service.db.engine as engine_module
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


def test_enabled_historical_receiver_requires_a_distinct_credential_identity() -> None:
    with pytest.raises(ValidationError, match="HISTORICAL_PROMOTION_ACTOR"):
        _settings(historical_promotion_receiver_enabled=True, historical_promotion_token=_TOKEN)

    configured = _settings(
        historical_promotion_receiver_enabled=True,
        historical_promotion_token=_TOKEN,
        historical_promotion_actor="  historical-promoter  ",
    )
    assert configured.historical_promotion_actor == "historical-promoter"


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


def test_production_database_profiles_require_explicit_nonshared_dsns() -> None:
    receiver = "postgresql+asyncpg://receiver:password@database.internal:5432/plantgeo"
    reader = "postgresql+asyncpg://reader:password@database.internal:5432/plantgeo"

    with pytest.raises(ValueError, match="RECEIVER_WRITER_DATABASE_URL"):
        _settings(service_profile="receiver_writer").require_receiver_writer_database_url()
    with pytest.raises(ValueError, match="PUBLISHED_READER_DATABASE_URL"):
        _settings(service_profile="published_reader").require_published_reader_database_url()

    receiver_settings = _settings(
        service_profile="receiver_writer",
        receiver_writer_database_url=receiver,
    )
    assert receiver_settings.require_receiver_writer_database_url() == receiver
    reader_settings = _settings(
        service_profile="published_reader",
        published_reader_database_url=reader,
    )
    assert reader_settings.require_published_reader_database_url() == reader

    with pytest.raises(ValidationError, match="must be distinct"):
        _settings(
            receiver_writer_database_url=receiver,
            published_reader_database_url=receiver,
        )
    with pytest.raises(ValidationError, match="distinct login roles"):
        _settings(
            receiver_writer_database_url=receiver,
            published_reader_database_url=receiver.replace("database.internal", "reader.internal"),
        )
    with pytest.raises(ValidationError, match="must not receive DATABASE_URL"):
        _settings(
            service_profile="published_reader",
            database_url=reader,
            published_reader_database_url=reader,
        )
    with pytest.raises(ValidationError, match="must not receive PUBLISHED_READER_DATABASE_URL"):
        _settings(
            service_profile="receiver_writer",
            receiver_writer_database_url=receiver,
            published_reader_database_url=reader,
        )


def test_combined_local_database_url_is_explicit_and_profile_bound() -> None:
    with pytest.raises(ValueError, match="requires DATABASE_URL"):
        _settings().require_combined_local_database_url()

    database_url = "postgresql+asyncpg://local:password@127.0.0.1:5432/plantgeo"
    assert _settings(database_url=database_url).require_combined_local_database_url() == database_url


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


def test_source_loader_accepts_plantgeo_prefixed_disposable_databases() -> None:
    target = "postgresql+asyncpg://plantgeo_loader:password@127.0.0.1:5442/plantgeo_boise_completion_20260725"
    assert _settings(local_source_loader_database_url=target).require_local_source_loader_database_url() == target

    with pytest.raises(ValueError, match=r"127\.0\.0\.1:5442/plantgeo"):
        _settings(
            local_source_loader_database_url=(
                "postgresql+asyncpg://plantgeo_loader:password@127.0.0.1:5442/plantgeography"
            )
        ).require_local_source_loader_database_url()


@pytest.mark.parametrize(
    "suffix",
    [
        "?host=database.internal&port=5432",
        "#host=database.internal&port=5432",
    ],
)
def test_source_loader_rejects_query_strings_and_fragments(suffix: str) -> None:
    with pytest.raises(ValueError, match=r"127\.0\.0\.1:5442/plantgeo"):
        _settings(
            local_source_loader_database_url=(
                "postgresql+asyncpg://plantgeo_loader:password@127.0.0.1:5442/plantgeo" + suffix
            )
        ).require_local_source_loader_database_url()


def test_source_loader_accepts_the_widened_production_ingest_target() -> None:
    """The 2026-08-03 widening adds the Railway cron target alongside the local compose loader."""
    target = "postgresql+asyncpg://postgres:password@switchback.proxy.rlwy.net:37967/plantgeo"
    assert _settings(local_source_loader_database_url=target).require_local_source_loader_database_url() == target


def test_source_loader_still_accepts_the_local_compose_loader_after_widening() -> None:
    target = "postgresql+asyncpg://plantgeo_loader:password@127.0.0.1:5442/plantgeo"
    assert _settings(local_source_loader_database_url=target).require_local_source_loader_database_url() == target


def test_source_loader_rejects_a_target_outside_the_widened_allowlist() -> None:
    """Assume the allowlist does not work until it has been watched reject an out-of-list host."""
    with pytest.raises(ValueError, match=r"127\.0\.0\.1:5442/plantgeo"):
        _settings(
            local_source_loader_database_url=(
                "postgresql+asyncpg://postgres:password@some-other-proxy.rlwy.net:37967/plantgeo"
            )
        ).require_local_source_loader_database_url()


def test_source_loader_rejects_the_local_role_at_the_production_host_and_port() -> None:
    """An allowed host/port with the wrong login must be rejected, not silently accepted."""
    with pytest.raises(ValueError, match="must authenticate as postgres"):
        _settings(
            local_source_loader_database_url=(
                "postgresql+asyncpg://plantgeo_loader:password@switchback.proxy.rlwy.net:37967/plantgeo"
            )
        ).require_local_source_loader_database_url()


def test_source_loader_rejects_the_production_role_at_the_local_host_and_port() -> None:
    with pytest.raises(ValueError, match="must authenticate as plantgeo_loader"):
        _settings(
            local_source_loader_database_url=("postgresql+asyncpg://postgres:password@127.0.0.1:5442/plantgeo")
        ).require_local_source_loader_database_url()


def test_forecast_mv_refresh_requires_its_separate_capability_role() -> None:
    target = "postgresql+asyncpg://forecast_refresh_operator:password@forecast-db.internal:5432/plantgeo"

    with pytest.raises(ValueError, match="FORECAST_MV_REFRESH_DATABASE_URL"):
        _settings().require_forecast_mv_refresh_database_url()

    configured = _settings(forecast_mv_refresh_database_url=target)
    assert configured.require_forecast_mv_refresh_database_url() == target

    with pytest.raises(ValueError, match="must not reuse DATABASE_URL"):
        _settings(
            database_url=target,
            forecast_mv_refresh_database_url=target,
        ).require_forecast_mv_refresh_database_url()

    with pytest.raises(ValueError, match="dedicated operator login"):
        _settings(
            database_url="postgresql+asyncpg://forecast_refresh_operator:password@app:5432/plantgeo",
            forecast_mv_refresh_database_url=target,
        ).require_forecast_mv_refresh_database_url()

    with pytest.raises(ValueError, match="complete postgresql"):
        _settings(
            forecast_mv_refresh_database_url="postgresql+asyncpg://forecast-db.internal:5432/plantgeo"
        ).require_forecast_mv_refresh_database_url()


def test_forecast_iteration_database_url_is_explicit_local_and_profile_separate() -> None:
    with pytest.raises(ValueError, match="FORECAST_ITERATION_DATABASE_URL"):
        _settings().require_forecast_iteration_database_url()

    target = "postgresql+asyncpg://plantgeo_local_developer:password@127.0.0.1:5442/plantgeo_forecast_test"
    assert _settings(forecast_iteration_database_url=target).require_forecast_iteration_database_url() == target

    with pytest.raises(ValueError, match="must not reuse DATABASE_URL"):
        _settings(
            database_url=target,
            forecast_iteration_database_url=target,
        ).require_forecast_iteration_database_url()

    approved_production = "postgresql+asyncpg://postgres:password@switchback.proxy.rlwy.net:37967/plantgeo"
    assert (
        _settings(forecast_iteration_database_url=approved_production).require_forecast_iteration_database_url()
        == approved_production
    )

    with pytest.raises(ValueError, match="approved forecast-iteration targets"):
        _settings(
            forecast_iteration_database_url=(
                "postgresql+asyncpg://plantgeo_local_developer:password@db.internal:5432/plantgeo"
            )
        ).require_forecast_iteration_database_url()

    with pytest.raises(ValueError, match="plantgeo_local_developer"):
        _settings(
            forecast_iteration_database_url=("postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeo")
        ).require_forecast_iteration_database_url()

    with pytest.raises(ValueError, match="must authenticate as postgres"):
        _settings(
            forecast_iteration_database_url=(
                "postgresql+asyncpg://plantgeo_local_developer:password@switchback.proxy.rlwy.net:37967/plantgeo"
            )
        ).require_forecast_iteration_database_url()

    with pytest.raises(ValueError, match="approved forecast-iteration targets"):
        _settings(
            forecast_iteration_database_url=(
                "postgresql+asyncpg://plantgeo_local_developer:password@127.0.0.1:5442/"
                "plantgeo?host=database.internal&port=5432"
            )
        ).require_forecast_iteration_database_url()


def test_ingest_session_resolves_its_dsn_through_the_real_loader_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cron seam itself, not a monkeypatched stand-in.

    Every `ingest-*` verb opens `db.engine.ingest_session()`, and `tests/test_ingest_commands.py`
    replaces that wholesale -- so nothing in the CLI suite ever exercised the DSN it resolves. A
    container configured the way `docs/deployment.md` and `docs/env-vars.md` used to prescribe
    (`DATABASE_URL` only) died here with an unhandled `ValueError` outside `run_isolated_job`, on
    every hourly tick, before a single source was fetched. This test pins the message and, more
    importantly, pins that the raise happens BEFORE any engine is created -- so the failure can
    never be mistaken for an unreachable database.
    """

    def _fail_if_an_engine_is_built(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ingest_session must refuse an unconfigured loader DSN before building an engine")

    monkeypatch.setattr(engine_module, "create_async_engine", _fail_if_an_engine_is_built)
    # The container the old docs described: DATABASE_URL present, the loader DSN absent.
    monkeypatch.setattr(
        engine_module,
        "settings",
        _settings(database_url="postgresql+asyncpg://app:password@app-db:5432/plantgeo"),
    )

    async def _enter() -> None:
        async with engine_module.ingest_session():
            pass  # pragma: no cover - the context manager raises before it yields.

    with pytest.raises(ValueError, match="DATABASE_URL is never a loader fallback"):
        asyncio.run(_enter())


def test_the_cron_containers_public_proxy_dsn_is_the_configuration_that_actually_works() -> None:
    """`DATABASE_URL` set to the same string is NOT a working alternative, and neither is the
    private-network host: `_INGEST_SOURCE_LOADER_ALLOWED_TARGETS` names the public proxy only."""
    proxy = "postgresql://postgres:password@switchback.proxy.rlwy.net:37967/plantgeo"
    normalized = "postgresql+asyncpg://postgres:password@switchback.proxy.rlwy.net:37967/plantgeo"

    assert _settings(local_source_loader_database_url=proxy).require_local_source_loader_database_url() == normalized

    # Both fields normalise to postgresql+asyncpg://, so identical raw strings compare EQUAL here.
    with pytest.raises(ValueError, match="must not reuse DATABASE_URL"):
        _settings(
            database_url=proxy,
            local_source_loader_database_url=proxy,
        ).require_local_source_loader_database_url()

    with pytest.raises(ValueError, match="approved"):
        _settings(
            local_source_loader_database_url=("postgresql://postgres:password@postgres.railway.internal:5432/plantgeo")
        ).require_local_source_loader_database_url()
