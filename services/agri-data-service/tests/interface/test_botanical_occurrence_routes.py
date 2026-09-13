"""Transport behaviour for the occurrence route, which `app.py` does not mount yet.

Called directly rather than through `create_app()`: the blueprint's registration hunk is held in the
track's `evidence/shared-registration.patch` until an independent review lands, so a test that
asserted the path was mounted would be asserting a decision nobody has made.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from agri_data_service.interface.http import botanical_occurrences as routes
from agri_data_service.pipeline.direct.botanical_occurrences.forward import (
    ArchiveRequest,
    BotanicalForwardConfig,
    run_botanical_occurrences_forward,
)
from agri_data_service.pipeline.direct.botanical_occurrences.publish import LocalPublicationTarget
from tests.direct.botanical_occurrences.conftest import default_members, write_archive

if TYPE_CHECKING:
    from pathlib import Path

ENVELOPE = (-123.0, 47.0, -122.0, 48.0)


class _Arguments(dict[str, list[str]]):
    def getlist(self, key: str) -> list[str]:
        return self[key]


def _request(parameters: dict[str, str], target: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        args=_Arguments({key: [value] for key, value in parameters.items()}),
        app=SimpleNamespace(ctx=SimpleNamespace(botanical_occurrences_target=target)),
    )


@pytest.fixture
def published(tmp_path: Path) -> tuple[LocalPublicationTarget, str]:
    archive = write_archive(tmp_path / "valid.zip", default_members())
    root = tmp_path / "publication"
    target = LocalPublicationTarget(root)
    report = run_botanical_occurrences_forward(
        BotanicalForwardConfig(
            archives=(ArchiveRequest(path=archive, collection_key="test:COLL:vascular", source_version="1.0"),),
            root=str(root),
            supports=("grid-0.25",),
            envelope=ENVELOPE,
            target=target,
        )
    )
    return target, str(report["release_set_id"])


def test_the_blueprint_is_mounted_under_its_own_prefix() -> None:
    assert routes.botanical_occurrences_bp.url_prefix == "/botanical-occurrences"
    assert routes.botanical_occurrences_bp.name == "botanical_occurrences"


async def test_a_published_generation_answers_two_hundred(published: tuple[LocalPublicationTarget, str]) -> None:
    target, release_set_id = published
    response = await routes.query_botanical_occurrences(
        _request({"release_set_id": release_set_id, "bbox": "-122.6,47.4,-122.0,47.9", "zoom": "13"}, target)
    )
    assert response.status == routes.HTTP_OK
    payload = json.loads(response.body)
    assert payload["state"] == "detail"
    assert payload["release_set_id"] == release_set_id


async def test_an_unpinned_release_is_refused_before_any_read_happens() -> None:
    response = await routes.query_botanical_occurrences(
        _request({"release_set_id": "current", "bbox": "-122.6,47.4,-122.0,47.9", "zoom": "13"})
    )
    assert response.status == routes.HTTP_BAD_REQUEST
    payload = json.loads(response.body)
    assert payload["state"] == "refused"
    assert payload["reason"] == "release_not_pinned"


async def test_an_unpublished_generation_answers_service_unavailable(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, _ = published
    response = await routes.query_botanical_occurrences(
        _request({"release_set_id": "0" * 64, "bbox": "-122.6,47.4,-122.0,47.9", "zoom": "13"}, target)
    )
    assert response.status == routes.HTTP_SERVICE_UNAVAILABLE
    assert json.loads(response.body)["state"] == "unavailable"


async def test_a_repeated_parameter_is_refused_rather_than_silently_taking_the_first() -> None:
    request = SimpleNamespace(
        args=_Arguments({"release_set_id": ["a" * 64, "b" * 64], "bbox": ["-1,-1,1,1"], "zoom": ["13"]}),
        app=SimpleNamespace(ctx=SimpleNamespace()),
    )
    response = await routes.query_botanical_occurrences(request)
    assert response.status == routes.HTTP_BAD_REQUEST
    assert json.loads(response.body)["reason"] == "invalid_request"


async def test_every_answer_forbids_caching() -> None:
    response = await routes.query_botanical_occurrences(
        _request({"release_set_id": "current", "bbox": "-1,-1,1,1", "zoom": "13"})
    )
    assert response.headers["Cache-Control"] == "no-store"
