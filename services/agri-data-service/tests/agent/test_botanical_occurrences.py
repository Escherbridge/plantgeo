"""The three occurrence tools: exact beside substitute, never instead of it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from agri_data_service.agent.botanical_occurrences import (
    _exact_block,
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
from agri_data_service.pipeline.direct.botanical_occurrences.publish import LocalPublicationTarget
from tests.direct.botanical_occurrences.conftest import default_members, write_archive

if TYPE_CHECKING:
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


async def test_an_unknown_release_set_is_unavailable_rather_than_empty() -> None:
    use_generation_root(None)
    payload = json.loads(await botanical_occurrences_in_region("0" * 64, -122.6, 47.4, -122.0, 47.9))
    assert payload["state"] in {"unavailable", "refused"}
    assert payload.get("exact") is None, "a failed read must not present itself as an empty answer"
