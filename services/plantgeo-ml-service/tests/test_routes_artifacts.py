"""`GET /api/v1/ml/artifacts/<kind>`: what has been trained, with digests and training windows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from serving_harness import body_of, build_serving, mount

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.pipeline.object_store import JSON_CONTENT_TYPE
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.routes import read_artifacts
from plantgeo_ml_service.planes.wire import ARTIFACT_ABSENT_NO_ARTIFACT, CLAIM_TIER

if TYPE_CHECKING:
    from serving_harness import ServingHarness

FIXTURES: Final = Path(__file__).resolve().parent / "fixtures" / "fire_risk"
FIRE_RISK_KIND: Final = "fire-risk"
ARTIFACT_PREFIX: Final = "ml/artifacts/fire-risk/"

HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400

#: The stub request every artifact handler ignores; the kind travels as a path segment.
NO_REQUEST: Final = None


def _stored_artifact(harness: ServingHarness, name: str) -> str:
    """Store one fixture artifact under the digest of its own document, and return that digest."""
    document = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    document.pop("sha256", None)
    digest = sha256_digest(canonical_json(document))
    document["sha256"] = digest
    harness.store.put_immutable(
        f"{ARTIFACT_PREFIX}{digest}.json", canonical_json(document).encode("utf-8"), content_type=JSON_CONTENT_TYPE
    )
    return digest


@pytest.fixture
def artifact_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServingHarness:
    """A bucket holding one cleared fire-risk artifact, with the blueprint pointed at it."""
    built = build_serving(tmp_path)
    mount(built, monkeypatch)
    return built


async def test_a_kind_with_no_artifacts_answers_an_empty_listing_and_says_why(
    artifact_store: ServingHarness,
) -> None:
    """An empty list with no reason reads as "nothing trained"; the reason says which it is."""
    assert not artifact_store.objects

    response = await read_artifacts(NO_REQUEST, FIRE_RISK_KIND)
    body = body_of(response)

    assert response.status == HTTP_OK
    assert body["count"] == 0
    assert body["artifacts"] == []
    assert body["artifact_absent_reason"] == ARTIFACT_ABSENT_NO_ARTIFACT
    assert body["claim_tier"] == CLAIM_TIER


async def test_a_stored_artifact_is_listed_with_its_digest_and_training_window(
    artifact_store: ServingHarness,
) -> None:
    digest = _stored_artifact(artifact_store, "artifact-cleared.json")

    body = body_of(await read_artifacts(NO_REQUEST, FIRE_RISK_KIND))

    assert body["count"] == 1
    listed = body["artifacts"][0]
    assert listed["sha256"] == digest
    assert listed["digest_matches_key"] is True
    assert set(listed["trained_on"]) == {"first_day", "last_day"}
    assert body["artifact_sha256"] == digest


async def test_a_listed_artifact_that_cannot_be_decoded_refuses_rather_than_reading_as_absent(
    artifact_store: ServingHarness,
) -> None:
    artifact_store.store.put_immutable(
        f"{ARTIFACT_PREFIX}{'a' * 64}.json", b"not json at all", content_type=JSON_CONTENT_TYPE
    )

    response = await read_artifacts(NO_REQUEST, FIRE_RISK_KIND)

    assert response.status != HTTP_OK
    assert body_of(response)["error"]["code"] == refusals.ARTIFACT_UNREADABLE


async def test_a_model_kind_this_service_does_not_write_is_rejected_by_name(
    artifact_store: ServingHarness,
) -> None:
    assert not artifact_store.objects

    response = await read_artifacts(NO_REQUEST, "species_fit")
    body = body_of(response)

    assert response.status == HTTP_BAD_REQUEST
    assert body["error"]["code"] == refusals.ARTIFACT_KIND_UNKNOWN


async def test_a_path_segment_that_is_not_a_kind_shape_is_rejected_before_any_listing(
    artifact_store: ServingHarness,
) -> None:
    """A segment that reached a listing prefix would assemble an object key from caller input."""
    assert not artifact_store.objects

    response = await read_artifacts(NO_REQUEST, "../secrets")

    assert response.status == HTTP_BAD_REQUEST
    assert body_of(response)["error"]["code"] == refusals.INVALID_REQUEST
