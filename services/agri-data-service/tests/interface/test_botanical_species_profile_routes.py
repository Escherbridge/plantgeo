"""The real app factory mounts and dispatches the bounded static profile route."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sanic import Sanic

from agri_data_service import app as app_module
from agri_data_service.interface.http import botanical_species_profiles as routes
from agri_data_service.pipeline.direct.botanical_species_profiles.publication import artifact_key
from agri_data_service.planes import botanical_species_profiles as profiles
from tests.planes.test_botanical_species_profiles import (
    FIXTURE_RELEASE_MISSING,
    ReadOnlyProfileStorage,
    publish_profile_fixture,
)

pytestmark = pytest.mark.skip(
    reason="botanical_species_profile_lookup_20260911: the HTTP blueprint exists but is deliberately not "
    "mounted in app.py pending the P4/P5 integration and independent data-governance verdict the track's "
    "acceptance section requires (a route mounted before that review is serving under a verdict nobody "
    "gave). See conductor/tracks/botanical_species_profile_lookup_20260911/evidence/"
    "shared-registration-20260912.patch for the pending app.py hunk; delete this skip once it lands."
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sanic.response import HTTPResponse

    from agri_data_service.app import AgriApp
    from tests.planes.test_botanical_species_profiles import ProfileFixture

LOOKUP_PATH = "/api/v1/botanical-species-profiles/lookup"


class _Arguments(dict[str, list[str]]):
    def getlist(self, key: str) -> list[str]:
        return self[key]


@pytest.fixture(params=["combined_local", "published_reader"])
def mounted_profile_app(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[tuple[AgriApp, ProfileFixture]]:
    fixture = publish_profile_fixture(tmp_path)
    monkeypatch.setattr(app_module.settings, "service_profile", request.param)
    previous_test_mode = Sanic.test_mode
    Sanic.test_mode = True
    try:
        app = app_module.create_app()
        app.ctx.botanical_profile_storage = ReadOnlyProfileStorage(fixture.storage)
        yield app, fixture
    finally:
        Sanic._app_registry.pop("agri-data-service", None)
        Sanic.test_mode = previous_test_mode


async def _dispatch_lookup(app: AgriApp, parameters: dict[str, Any]) -> HTTPResponse:
    routes_at_path = [route for route in app.router.routes if f"/{route.path.strip('/')}" == LOOKUP_PATH]
    assert len(routes_at_path) == 1
    arguments = _Arguments({key: value if isinstance(value, list) else [value] for key, value in parameters.items()})
    response: HTTPResponse = await routes_at_path[0].handler(SimpleNamespace(args=arguments, app=app))
    return response


def _payload(response: HTTPResponse) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(response.body)
    return payload


async def test_real_read_profiles_mount_and_dispatch_lookup_with_manifest_evidence(
    mounted_profile_app: tuple[AgriApp, ProfileFixture],
) -> None:
    app, fixture = mounted_profile_app
    response = await _dispatch_lookup(app, fixture.parameters)
    payload = _payload(response)
    assert response.status == routes.HTTP_OK
    assert payload["state"] == "published"
    assert payload["release"]["release_id"] == fixture.release.release_id
    assert payload["release"]["source_releases"][0]["licence_id"] == "CC0-1.0"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("over_budget", [False, True])
async def test_registered_route_enforces_the_exact_utf8_response_byte_budget(
    mounted_profile_app: tuple[AgriApp, ProfileFixture],
    monkeypatch: pytest.MonkeyPatch,
    over_budget: bool,
) -> None:
    app, fixture = mounted_profile_app
    rendered: dict[str, Any] = {"state": "published", "reason": None, "evidence": ""}
    unicode_character = "🌱"
    empty_body = json.dumps(rendered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    character_count, padding = divmod(
        profiles.MAX_RESPONSE_BYTES - len(empty_body), len(unicode_character.encode("utf-8"))
    )
    rendered["evidence"] = unicode_character * character_count + "a" * padding
    if over_budget:
        rendered["evidence"] += unicode_character
    expected_body = json.dumps(rendered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(json.dumps(rendered, ensure_ascii=True).encode("utf-8")) > profiles.MAX_RESPONSE_BYTES
    monkeypatch.setattr(profiles, "_render_profile", lambda *_args: rendered)

    response = await _dispatch_lookup(app, fixture.parameters)

    assert response.body is not None
    assert len(response.body) <= profiles.MAX_RESPONSE_BYTES
    assert response.content_type == "application/json"
    assert response.headers["Cache-Control"] == "no-store"
    if over_budget:
        assert response.status == routes.HTTP_CONFLICT
        assert _payload(response)["reason"]["code"] == "profile_response_over_budget"
    else:
        assert response.status == routes.HTTP_OK
        assert response.body == expected_body
        assert len(response.body) == profiles.MAX_RESPONSE_BYTES


def test_write_ingress_profile_does_not_mount_botanical_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module.settings, "service_profile", "receiver_writer")
    previous_test_mode = Sanic.test_mode
    Sanic.test_mode = True
    try:
        paths = {f"/{route.path.strip('/')}" for route in app_module.create_app().router.routes}
    finally:
        Sanic._app_registry.pop("agri-data-service", None)
        Sanic.test_mode = previous_test_mode
    assert LOOKUP_PATH not in paths


@pytest.mark.parametrize(
    "bad_parameters",
    [
        {"authority": ["a", "b"]},
        {"release_id": "latest"},
        {"assertion_limit": "101"},
        {"name": "Syntheticus testii"},
        {"zoom": "13"},
        {"taxon_id": ""},
    ],
)
async def test_malformed_scope_returns_400_before_any_object_read(
    mounted_profile_app: tuple[AgriApp, ProfileFixture],
    bad_parameters: dict[str, Any],
) -> None:
    app, fixture = mounted_profile_app
    response = await _dispatch_lookup(app, {**fixture.parameters, **bad_parameters})
    assert response.status == routes.HTTP_BAD_REQUEST
    assert _payload(response)["reason"]["code"] == "invalid_profile_request"
    assert app.ctx.botanical_profile_storage.reads == []


async def test_unknown_taxon_has_200_unknown_but_unpublished_release_is_a_refusal(
    mounted_profile_app: tuple[AgriApp, ProfileFixture],
) -> None:
    app, fixture = mounted_profile_app
    unknown = await _dispatch_lookup(app, {**fixture.parameters, "taxon_id": "absent-canonical-taxon"})
    unpublished = await _dispatch_lookup(app, {**fixture.parameters, "release_id": FIXTURE_RELEASE_MISSING})
    assert unknown.status == routes.HTTP_OK
    assert _payload(unknown)["state"] == "unknown"
    assert unpublished.status == routes.HTTP_CONFLICT
    assert _payload(unpublished)["reason"]["code"] == "profile_release_unpublished"


async def test_integrity_fault_is_503_and_never_exposes_profile_values(
    mounted_profile_app: tuple[AgriApp, ProfileFixture],
) -> None:
    app, fixture = mounted_profile_app
    (fixture.storage.root / artifact_key(fixture.release.release_id, "profiles")).write_bytes(b"tampered")
    response = await _dispatch_lookup(app, fixture.parameters)
    assert response.status == routes.HTTP_SERVICE_UNAVAILABLE
    assert _payload(response)["reason"]["code"] == "profile_release_integrity"
    assert _payload(response)["assertions"] == []


async def test_http_continuation_preserves_exact_release_and_taxon(
    mounted_profile_app: tuple[AgriApp, ProfileFixture],
) -> None:
    app, fixture = mounted_profile_app
    first = _payload(await _dispatch_lookup(app, {**fixture.parameters, "assertion_limit": "1"}))
    second = _payload(
        await _dispatch_lookup(
            app,
            {
                **fixture.parameters,
                "assertion_limit": "1",
                "cursor": first["continuation"]["next_cursor"],
            },
        )
    )
    assert first["assertions"][0]["assertion_id"] != second["assertions"][0]["assertion_id"]
    assert second["profile_release_id"] == first["profile_release_id"]
    assert second["taxon_identity"] == first["taxon_identity"]
