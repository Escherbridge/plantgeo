"""The three occurrence tools: exact beside substitute, never instead of it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError

from agri_data_service.agent.botanical_occurrences import (
    _exact_block,
    botanical_occurrence_current_release,
    botanical_occurrence_spatial_neighbours,
    botanical_occurrence_temporal_neighbours,
    botanical_occurrences_in_region,
    use_generation_root,
)
from agri_data_service.pipeline.direct.botanical_occurrences.forward import (
    ArchiveRequest,
    BotanicalForwardConfig,
    run_botanical_occurrences_forward,
)
from agri_data_service.pipeline.direct.botanical_occurrences.publish import (
    LocalPublicationTarget,
    ObjectStorePublicationTarget,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend
from agri_data_service.planes import botanical_occurrences as botanical_plane
from tests.direct.botanical_occurrences.conftest import default_members, write_archive

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ENVELOPE = (-123.0, 47.0, -122.0, 48.0)


@pytest.fixture
def release_set_id(tmp_path: Path) -> str:
    """Publish one generation locally and point the agent tools at it."""
    archive = write_archive(tmp_path / "valid.zip", default_members())
    root = tmp_path / "publication"
    report = run_botanical_occurrences_forward(
        BotanicalForwardConfig(
            archives=(ArchiveRequest(path=archive, collection_key="test:COLL:vascular", source_version="1.0"),),
            root=str(root),
            supports=("grid-0.25",),
            envelope=ENVELOPE,
            target=LocalPublicationTarget(root),
        )
    )
    use_generation_root(str(root))
    return str(report["release_set_id"])


async def test_the_region_tool_reports_its_own_exact_state(release_set_id: str) -> None:
    payload = json.loads(await botanical_occurrences_in_region(release_set_id, -122.6, 47.4, -122.0, 47.9))
    assert payload["state"] == "detail"
    assert payload["exact"]["state"] in {"found", "empty"}
    assert payload["exact"]["count"] == len(payload["features"])


async def test_every_tool_restates_the_claims_it_refuses(release_set_id: str) -> None:
    payload = json.loads(await botanical_occurrences_in_region(release_set_id, -122.6, 47.4, -122.0, 47.9))
    assert "abundance" in payload["refused_claims"]
    assert "surveyed_absence" in payload["refused_claims"]
    assert "habitat_suitability" in payload["refused_claims"]


async def test_spatial_neighbours_are_flagged_and_carry_a_real_distance(release_set_id: str) -> None:
    payload = json.loads(
        await botanical_occurrence_spatial_neighbours(release_set_id, -122.32, 47.60, radius_meters=20_000)
    )
    assert payload["exact"]["state"] in {"found", "empty"}
    assert payload["substitutes"], payload
    for neighbour in payload["substitutes"]:
        assert neighbour["substitute"] is True
        assert neighbour["distance_m"] >= 0
        assert neighbour["distance_semantics"] in {"to_record_point", "to_reported_point"}


def test_an_empty_exact_result_is_never_replaced_by_a_neighbour() -> None:
    """The shape is the guarantee: `exact` and `substitutes` are different keys in one payload."""
    assert _exact_block([]) == {"state": "empty", "count": 0}


async def test_temporal_neighbours_report_signed_days_and_overlap(release_set_id: str) -> None:
    payload = json.loads(
        await botanical_occurrence_temporal_neighbours(
            release_set_id, -122.6, 47.4, -122.0, 47.9, "1987-06-10", "1987-06-20"
        )
    )
    assert payload["requested_interval"] == {"start": "1987-06-10", "end": "1987-06-20"}
    assert payload["exact"]["count"] == len(payload["overlapping"])
    for overlapping in payload["overlapping"]:
        assert overlapping["overlap"] is True
    for neighbour in payload["substitutes"]:
        assert neighbour["overlap"] is False
        assert neighbour["abs_days"] == abs(neighbour["signed_days"])
        assert neighbour["substitute"] is True


async def test_an_unparseable_window_is_refused_rather_than_guessed(release_set_id: str) -> None:
    payload = json.loads(
        await botanical_occurrence_temporal_neighbours(
            release_set_id, -122.6, 47.4, -122.0, 47.9, "sometime in June", "1987-06-20"
        )
    )
    assert payload["state"] == "refused"


async def test_an_unknown_release_set_is_unavailable_rather_than_empty(tmp_path: Path) -> None:
    use_generation_root(str(tmp_path / "nothing-published-here"))
    payload = json.loads(await botanical_occurrences_in_region("0" * 64, -122.6, 47.4, -122.0, 47.9))
    assert payload["state"] in {"unavailable", "refused"}
    assert payload.get("exact") is None, "a failed read must not present itself as an empty answer"


async def test_current_release_resolves_the_id_the_other_three_tools_require(release_set_id: str) -> None:
    """The pin the other tools demand has to come from somewhere; this is that somewhere."""
    payload = json.loads(await botanical_occurrence_current_release())
    assert payload["state"] == "current"
    assert payload["release_set_id"] == release_set_id
    # The resolved id must actually be accepted by a pinned tool call, not just look plausible.
    detail = json.loads(await botanical_occurrences_in_region(payload["release_set_id"], -122.6, 47.4, -122.0, 47.9))
    assert detail["state"] == "detail"


async def test_current_release_is_unavailable_rather_than_fabricated_with_no_publication(tmp_path: Path) -> None:
    # An empty local root keeps this refusal test independent of configured object storage.
    use_generation_root(str(tmp_path / "nothing-published-here"))
    payload = json.loads(await botanical_occurrence_current_release())
    assert payload["state"] == "unavailable"
    assert "release_set_id" not in payload


def _failing_target(probe: str, error: Exception) -> ObjectStorePublicationTarget:
    backend = Mock(spec=ObjectStoreBackend)

    def read(key: str) -> bytes:
        if probe == "pointer" or key.endswith("manifest.json"):
            raise error
        return json.dumps({"release_set_id": "0" * 64}).encode()

    backend.get.side_effect = read
    backend.size_of.return_value = None if probe == "incomplete_manifest" else 1
    if probe == "marker":
        backend.size_of.side_effect = error
    return ObjectStorePublicationTarget(backend)


async def _call_occurrence_tool(tool_name: str) -> str:
    if tool_name == "current":
        return await botanical_occurrence_current_release()
    if tool_name == "spatial":
        return await botanical_occurrence_spatial_neighbours("0" * 64, -122.32, 47.60)
    if tool_name == "temporal":
        return await botanical_occurrence_temporal_neighbours(
            "0" * 64, -122.6, 47.4, -122.0, 47.9, "1987-06-10", "1987-06-20"
        )
    assert tool_name == "region"
    return await botanical_occurrences_in_region("0" * 64, -122.6, 47.4, -122.0, 47.9)


@pytest.mark.parametrize(
    ("tool_name", "probe", "error_factory"),
    [
        ("region", "marker", EndpointConnectionError),
        ("spatial", "marker", EndpointConnectionError),
        ("temporal", "marker", EndpointConnectionError),
        ("region", "incomplete_manifest", ReadTimeoutError),
        ("region", "manifest", ReadTimeoutError),
        ("current", "pointer", EndpointConnectionError),
        ("current", "marker", EndpointConnectionError),
        ("current", "manifest", ReadTimeoutError),
    ],
)
async def test_storage_transport_failure_is_unavailable_without_private_error_details(
    tool_name: str, probe: str, error_factory: Callable[..., Exception], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = error_factory(endpoint_url="https://example.invalid/private?token=synthetic-secret")
    target = _failing_target(probe, error)
    monkeypatch.setattr(botanical_plane, "publication_target", Mock(return_value=target))
    use_generation_root(str(tmp_path))

    encoded = await _call_occurrence_tool(tool_name)
    payload = json.loads(encoded)

    assert payload["state"] == "unavailable"
    assert payload["reason"] == "the botanical occurrence object store could not complete the bounded read"
    assert "exact" not in payload
    assert "features" not in payload
    assert "release_set_id" not in payload
    assert "example.invalid" not in encoded
    assert "synthetic-secret" not in encoded


@pytest.mark.parametrize("tool_name", ["region", "current"])
async def test_unexpected_backend_errors_are_not_disguised_as_unavailability(
    tool_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _failing_target("marker", RuntimeError("synthetic programming fault"))
    monkeypatch.setattr(botanical_plane, "publication_target", Mock(return_value=target))
    use_generation_root(str(tmp_path))

    with pytest.raises(RuntimeError, match="synthetic programming fault"):
        await _call_occurrence_tool(tool_name)


@pytest.mark.parametrize("zoom", [11, 5], ids=["detail", "aggregate"])
def test_late_manifest_transport_failure_discards_the_scanned_answer(
    zoom: int, release_set_id: str, tmp_path: Path
) -> None:
    local_target = LocalPublicationTarget(tmp_path / "publication")
    backend = Mock(spec=ObjectStoreBackend)
    backend.size_of.side_effect = lambda key: 1 if local_target.exists(key) else None
    manifest_read_once = False

    def read(key: str) -> bytes | None:
        nonlocal manifest_read_once
        if key.endswith("manifest.json"):
            if manifest_read_once:
                raise ReadTimeoutError(endpoint_url="https://example.invalid/private?token=synthetic-secret")
            manifest_read_once = True
        return local_target.read_bytes(key)

    backend.get.side_effect = read
    result = botanical_plane.read_botanical_occurrences(
        botanical_plane.BotanicalOccurrenceRequest(
            release_set_id=release_set_id, bbox=(-122.6, 47.4, -122.0, 47.9), zoom=zoom
        ),
        root=local_target.root,
        target=ObjectStorePublicationTarget(backend),
    )

    assert result["state"] == "unavailable"
    assert result["reason"] == "the botanical occurrence object store could not complete the bounded read"
    assert "features" not in result
    assert "cells" not in result
    assert "synthetic-secret" not in json.dumps(result)
