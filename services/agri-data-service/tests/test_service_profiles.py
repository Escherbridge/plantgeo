"""Production profiles must mount and connect only their authorized surface."""

from typing import Any

import pytest
from sanic import Sanic

from agri_data_service import app as app_module
from agri_data_service.db import engine as engine_module


def _route_paths(app: app_module.AgriApp) -> set[str]:
    return {f"/{route.path.strip('/')}" for route in app.router.routes}


def test_create_app_registers_only_the_selected_profile_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    previous_test_mode = Sanic.test_mode
    Sanic.test_mode = True
    try:
        monkeypatch.setattr(app_module.settings, "service_profile", "receiver_writer")
        monkeypatch.setattr(
            app_module.settings,
            "receiver_writer_database_url",
            "postgresql+asyncpg://receiver:password@database.internal:5432/plantgeo",
        )
        receiver_paths = _route_paths(app_module.create_app())
        assert any(path.startswith("/api/v1/local-execution/") for path in receiver_paths)
        assert any(path.startswith("/api/v1/historical-promotions/") for path in receiver_paths)
        assert not any(path.startswith("/api/v1/forecasts") for path in receiver_paths)
        assert not any(path.startswith("/api/v1/strategies") for path in receiver_paths)

        monkeypatch.setattr(app_module.settings, "service_profile", "published_reader")
        monkeypatch.setattr(
            app_module.settings,
            "published_reader_database_url",
            "postgresql+asyncpg://reader:password@database.internal:5432/plantgeo",
        )
        reader_paths = _route_paths(app_module.create_app())
        assert any(path.startswith("/api/v1/forecasts") for path in reader_paths)
        assert not any(path.startswith("/api/v1/local-execution") for path in reader_paths)
        assert not any(path.startswith("/api/v1/historical-promotions") for path in reader_paths)
        assert not any(path.startswith("/api/v1/strategies") for path in reader_paths)
    finally:
        Sanic._app_registry.pop("agri-data-service", None)
        Sanic.test_mode = previous_test_mode


@pytest.mark.asyncio
async def test_wrong_profile_session_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module.settings, "service_profile", "published_reader")

    with pytest.raises(ValueError, match="receiver_writer database session is unavailable"):
        async with engine_module.receiver_writer_session():
            pass
    with pytest.raises(ValueError, match="DATABASE_URL is available only for the combined_local"):
        engine_module.combined_local_engine()
    assert engine_module._combined_state.engine is None


def test_service_engine_uses_only_the_selected_profile_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    selected_url = "postgresql+asyncpg://receiver:password@database.internal:5432/plantgeo"
    created_urls: list[str] = []
    marker = object()

    def fake_create_async_engine(database_url: str, **_kwargs: object) -> Any:
        created_urls.append(database_url)
        return marker

    monkeypatch.setattr(engine_module.settings, "service_profile", "receiver_writer")
    monkeypatch.setattr(engine_module.settings, "receiver_writer_database_url", selected_url)
    monkeypatch.setattr(engine_module, "create_async_engine", fake_create_async_engine)
    engine_module._service_engines.clear()
    try:
        assert engine_module._service_engine("receiver_writer") is marker
        assert created_urls == [selected_url]
    finally:
        engine_module._service_engines.clear()
