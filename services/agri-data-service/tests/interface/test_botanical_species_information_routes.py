"""Registration and transport tests for botanical species information."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sanic import Sanic

from agri_data_service import app as app_module
from agri_data_service.interface.http import botanical_species_information as routes

LOOKUP_PATH = "/api/v1/botanical-species-information/lookup"


class _Arguments(dict[str, list[str]]):
    def getlist(self, key: str) -> list[str]:
        return self[key]


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("combined_local", True), ("published_reader", True), ("receiver_writer", False)],
)
def test_route_mounts_only_on_read_capable_profiles(
    monkeypatch: pytest.MonkeyPatch, profile: str, expected: bool
) -> None:
    monkeypatch.setattr(app_module.settings, "service_profile", profile)
    previous = Sanic.test_mode
    Sanic.test_mode = True
    try:
        paths = {f"/{route.path.strip('/')}" for route in app_module.create_app().router.routes}
    finally:
        Sanic._app_registry.pop("agri-data-service", None)
        Sanic.test_mode = previous
    assert (LOOKUP_PATH in paths) is expected


async def test_transport_rejects_name_lookup_before_opening_a_session() -> None:
    request = SimpleNamespace(
        args=_Arguments({"species_id": ["Lupinus argenteus"]}),
        app=SimpleNamespace(ctx=SimpleNamespace()),
    )
    response = await routes.lookup_species_information(request)
    assert response.status == routes.HTTP_BAD_REQUEST
    payload = json.loads(response.body)
    assert payload["reason"]["code"] == "invalid_species_information_request"
    assert payload["publication_state"] == "not_published"
