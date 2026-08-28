"""Focused configuration contracts for local publication, migrations, and the cron ingest DSN."""

import asyncio

import pytest
from pydantic import ValidationError

import agri_data_service.db.engine as engine_module
from agri_data_service.config import Settings

_TOKEN = "0123456789abcdefghijklmnopqrstuvwxyzABCDEF"


class _EngineFactoryReachedError(Exception):
    """Raised by the stubbed engine factory so a test can assert the DSN without connecting."""


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

    # Since 20260808_0019 the two profile DSNs may share one login, or be the same DSN outright.
    shared_dsn_settings = _settings(
        receiver_writer_database_url=receiver,
        published_reader_database_url=receiver,
    )
    assert shared_dsn_settings.receiver_writer_database_url == receiver
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


@pytest.mark.parametrize(
    ("profile", "field_name", "resolver_name"),
    [
        ("receiver_writer", "receiver_writer_database_url", "require_receiver_writer_database_url"),
        ("published_reader", "published_reader_database_url", "require_published_reader_database_url"),
    ],
)
def test_production_database_profiles_reject_the_legacy_railway_database(
    profile: str,
    field_name: str,
    resolver_name: str,
) -> None:
    configured = _settings(
        service_profile=profile,
        **{field_name: "postgresql://app:password@postgres.railway.internal:5432/railway"},
    )

    with pytest.raises(ValueError, match="must target the plantgeo database"):
        getattr(configured, resolver_name)()


def test_combined_local_database_url_is_explicit_and_profile_bound() -> None:
    with pytest.raises(ValueError, match="requires DATABASE_URL"):
        _settings().require_combined_local_database_url()

    database_url = "postgresql+asyncpg://local:password@127.0.0.1:5432/plantgeo"
    assert _settings(database_url=database_url).require_combined_local_database_url() == database_url


# The three command DSNs share one resolver, `Settings._require_command_database_url`: an
# optional override, DATABASE_URL as the fallback, blank/whitespace treated as unset, and the
# shared completeness guard as the one remaining rejection. See db/AGENTS.md for the ruling.
_COMMAND_DSN_RESOLVERS = [
    ("local_source_loader_database_url", "require_local_source_loader_database_url"),
    ("forecast_mv_refresh_database_url", "require_forecast_mv_refresh_database_url"),
    ("forecast_iteration_database_url", "require_forecast_iteration_database_url"),
]


@pytest.mark.parametrize(("field_name", "method_name"), _COMMAND_DSN_RESOLVERS)
def test_command_dsn_falls_back_to_database_url(field_name: str, method_name: str) -> None:
    del field_name
    application = "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeo"

    resolved = getattr(_settings(database_url=application), method_name)()
    assert resolved == application


@pytest.mark.parametrize(("field_name", "method_name"), _COMMAND_DSN_RESOLVERS)
def test_command_dsn_override_wins_over_database_url(field_name: str, method_name: str) -> None:
    application = "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeo"
    override = "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeo_scratch"

    configured = _settings(database_url=application, **{field_name: override})
    assert getattr(configured, method_name)() == override


@pytest.mark.parametrize(("field_name", "method_name"), _COMMAND_DSN_RESOLVERS)
def test_command_dsn_accepts_the_same_string_as_database_url(field_name: str, method_name: str) -> None:
    """`run-backfill.sh` sets the override to `$DATABASE_URL` verbatim; that used to be rejected."""
    shared = "postgresql://postgres:password@switchback.proxy.rlwy.net:37967/plantgeo"
    normalized = "postgresql+asyncpg://postgres:password@switchback.proxy.rlwy.net:37967/plantgeo"

    configured = _settings(database_url=shared, **{field_name: shared})
    assert getattr(configured, method_name)() == normalized


@pytest.mark.parametrize(("field_name", "method_name"), _COMMAND_DSN_RESOLVERS)
def test_command_dsn_requires_at_least_one_of_the_two_variables(field_name: str, method_name: str) -> None:
    del field_name
    with pytest.raises(ValueError, match="or DATABASE_URL"):
        getattr(_settings(), method_name)()


@pytest.mark.parametrize(("field_name", "method_name"), _COMMAND_DSN_RESOLVERS)
@pytest.mark.parametrize("blank", ["", "  ", "\t\n"])
def test_command_dsn_treats_a_blank_override_as_unset(field_name: str, method_name: str, blank: str) -> None:
    """`export FOO=` is an empty variable, not a DSN.

    `""` was already falsy and fell through, but `"  "` was truthy: it was returned verbatim and
    died inside SQLAlchemy's URL parser instead of here. Both now mean the same thing.
    """
    application = "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeo"

    configured = _settings(database_url=application, **{field_name: blank})
    assert getattr(configured, method_name)() == application

    with pytest.raises(ValueError, match="or DATABASE_URL"):
        getattr(_settings(**{field_name: blank}), method_name)()


@pytest.mark.parametrize(("field_name", "method_name"), _COMMAND_DSN_RESOLVERS)
@pytest.mark.parametrize(
    ("incomplete", "expected"),
    [
        ("postgresql+asyncpg://127.0.0.1:5442/plantgeo", "complete postgresql"),
        ("postgresql+asyncpg://plantgeo_owner:password@127.0.0.1/plantgeo", "complete postgresql"),
        ("postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442", "complete postgresql"),
        ("mysql://plantgeo_owner:password@127.0.0.1:5442/plantgeo", "complete postgresql"),
        ("postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:not-a-port/plantgeo", "invalid port"),
    ],
)
def test_command_dsn_must_still_be_a_complete_database_url(
    field_name: str,
    method_name: str,
    incomplete: str,
    expected: str,
) -> None:
    """The one surviving rejection, shared with the profile DSNs through a single parser."""
    with pytest.raises(ValueError, match=expected):
        getattr(_settings(**{field_name: incomplete}), method_name)()


@pytest.mark.parametrize(("field_name", "method_name"), _COMMAND_DSN_RESOLVERS)
def test_command_dsn_reports_the_variable_that_actually_failed(field_name: str, method_name: str) -> None:
    """A blank override means the fallback is what got parsed, so the message must name it."""
    with pytest.raises(ValueError, match="DATABASE_URL must be a complete"):
        getattr(
            _settings(database_url="postgresql+asyncpg://127.0.0.1:5442/plantgeo", **{field_name: "  "}),
            method_name,
        )()


@pytest.mark.parametrize(("field_name", "method_name"), _COMMAND_DSN_RESOLVERS)
@pytest.mark.parametrize(
    "override",
    [
        # Logins that were rejected somewhere before the teardown.
        "postgresql+asyncpg://plantgeo_owner:password@switchback.proxy.rlwy.net:37967/plantgeo",
        "postgresql+asyncpg://plantgeo_loader:password@switchback.proxy.rlwy.net:37967/plantgeo",
        "postgresql+asyncpg://postgres:password@127.0.0.1:5442/plantgeo",
        # Hosts and ports outside the retired allowlists.
        "postgresql+asyncpg://postgres:password@postgres.railway.internal:5432/plantgeo",
        "postgresql+asyncpg://postgres:password@some-other-proxy.rlwy.net:37967/plantgeo",
        "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5432/plantgeo",
        # Database names outside the retired plantgeo/plantgeo_* rule.
        "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/agri_data",
        "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeography",
        # Query string and fragment, previously refused outright.
        "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeo?sslmode=disable",
        "postgresql+asyncpg://plantgeo_owner:password@127.0.0.1:5442/plantgeo#note",
    ],
)
def test_command_dsn_asserts_nothing_about_login_host_port_or_database(
    field_name: str,
    method_name: str,
    override: str,
) -> None:
    configured = _settings(**{field_name: override})
    assert getattr(configured, method_name)() == override


def test_ingest_session_uses_database_url_when_no_loader_override_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cron seam itself, not a monkeypatched stand-in.

    Every `ingest-*` verb opens `db.engine.ingest_session()`, and `tests/test_ingest_commands.py`
    replaces that wholesale -- so nothing else in the CLI suite exercises the DSN it resolves. A
    container carrying only `DATABASE_URL` used to die here with an unhandled `ValueError`
    outside `run_isolated_job`, on every tick, before a single source was fetched. Since the
    2026-08-08 teardown that is the supported single-credential deployment, so this pins the
    exact DSN handed to the engine factory.
    """
    application = "postgresql+asyncpg://app:password@app-db:5432/plantgeo"
    built_with: list[str] = []

    def _capture_and_stop(url: str, **_kwargs: object) -> object:
        built_with.append(url)
        raise _EngineFactoryReachedError

    monkeypatch.setattr(engine_module, "create_async_engine", _capture_and_stop)
    monkeypatch.setattr(engine_module, "settings", _settings(database_url=application))

    async def _enter() -> None:
        async with engine_module.ingest_session():
            pass  # pragma: no cover - the stub raises before it yields.

    with pytest.raises(_EngineFactoryReachedError):
        asyncio.run(_enter())

    assert built_with == [application]


def test_ingest_session_refuses_before_building_an_engine_when_no_dsn_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins that the one remaining failure raises BEFORE any engine is created, so it can never
    be mistaken for an unreachable database."""

    def _fail_if_an_engine_is_built(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ingest_session must refuse an unconfigured DSN before building an engine")

    monkeypatch.setattr(engine_module, "create_async_engine", _fail_if_an_engine_is_built)
    monkeypatch.setattr(engine_module, "settings", _settings())

    async def _enter() -> None:
        async with engine_module.ingest_session():
            pass  # pragma: no cover - the context manager raises before it yields.

    with pytest.raises(ValueError, match="LOCAL_SOURCE_LOADER_DATABASE_URL"):
        asyncio.run(_enter())
