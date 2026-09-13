"""The four serving states, the pinning rules, and cursor continuation over a published generation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.pipeline.direct.botanical_occurrences.forward import (
    ArchiveRequest,
    BotanicalForwardConfig,
    run_botanical_occurrences_forward,
)
from agri_data_service.pipeline.direct.botanical_occurrences.publish import (
    COMPLETION_MARKER,
    LocalPublicationTarget,
    generation_prefix,
)
from agri_data_service.planes.botanical_occurrences import (
    BotanicalOccurrenceRequest,
    BotanicalOccurrenceRequestError,
    parse_botanical_occurrence_request,
    read_botanical_occurrences,
)
from tests.direct.botanical_occurrences.conftest import default_members, write_archive

if TYPE_CHECKING:
    from pathlib import Path

ENVELOPE = (-123.0, 47.0, -122.0, 48.0)


@pytest.fixture
def published(tmp_path: Path) -> tuple[LocalPublicationTarget, str]:
    """One published generation, built from a synthetic archive through the real writer."""
    archive = write_archive(tmp_path / "valid.zip", default_members())
    target = LocalPublicationTarget(tmp_path / "publication")
    report = run_botanical_occurrences_forward(
        BotanicalForwardConfig(
            archives=(ArchiveRequest(path=archive, collection_key="test:COLL:vascular", source_version="1.0"),),
            root=str(tmp_path / "publication"),
            supports=("grid-0.25", "grid-0.05"),
            envelope=ENVELOPE,
            target=target,
        )
    )
    assert report["outcome"] == "published"
    return target, report["release_set_id"]


def _request(release_set_id: str, **overrides: Any) -> BotanicalOccurrenceRequest:
    parameters = {
        "release_set_id": release_set_id,
        "bbox": "-122.6,47.4,-122.0,47.9",
        "zoom": "13",
        **{key: str(value) for key, value in overrides.items()},
    }
    return parse_botanical_occurrence_request(parameters)


def test_a_detail_request_returns_points_with_their_event_intervals(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, release_set_id = published
    result = read_botanical_occurrences(_request(release_set_id), target=target)
    assert result["state"] == "detail"
    assert result["support_id"] is None
    assert result["features"], result
    feature = result["features"][0]
    assert feature["event_interval"]["precision"] in {"day", "month", "year", "interval"}
    assert feature["membership"] in {"confirmed", "possible"}


def test_withheld_and_nonspatial_records_are_counted_but_never_returned_as_points(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, release_set_id = published
    result = read_botanical_occurrences(_request(release_set_id, spatial_quality="all"), target=target)
    assert result["counts"]["excluded_by_qc"] >= 2, "the withheld and the coordinate-less record"
    assert all(feature["spatial_class"] in {"exact", "generalized"} for feature in result["features"])


def test_an_aggregate_zoom_answers_with_support_cells(published: tuple[LocalPublicationTarget, str]) -> None:
    target, release_set_id = published
    result = read_botanical_occurrences(_request(release_set_id, zoom=5, bbox="-124.0,46.0,-121.0,49.0"), target=target)
    assert result["state"] == "aggregate"
    assert result["support_id"] == "grid-0.25"
    assert result["cells"], result
    assert {cell["evaluation"] for cell in result["cells"]} <= {
        "documented",
        "evaluated_zero",
        "withheld_or_generalized_only",
    }
    assert all(cell["geometry"]["type"] == "Polygon" for cell in result["cells"])


def test_the_fine_rung_answers_the_middle_zoom_band(published: tuple[LocalPublicationTarget, str]) -> None:
    target, release_set_id = published
    result = read_botanical_occurrences(_request(release_set_id, zoom=9, bbox="-122.6,47.4,-122.0,47.9"), target=target)
    assert result["support_id"] == "grid-0.05"


def test_current_is_refused_rather_than_resolved() -> None:
    with pytest.raises(BotanicalOccurrenceRequestError) as raised:
        parse_botanical_occurrence_request({"release_set_id": "current", "bbox": "-1,-1,1,1", "zoom": "13"})
    assert raised.value.reason == "release_not_pinned"


def test_a_missing_release_set_id_is_refused() -> None:
    with pytest.raises(BotanicalOccurrenceRequestError) as raised:
        parse_botanical_occurrence_request({"bbox": "-1,-1,1,1", "zoom": "13"})
    assert raised.value.reason == "release_not_pinned"


def test_a_name_only_taxon_filter_is_refused() -> None:
    with pytest.raises(BotanicalOccurrenceRequestError) as raised:
        parse_botanical_occurrence_request(
            {"release_set_id": "abcdefgh", "bbox": "-1,-1,1,1", "zoom": "13", "scientific_name": "Lupinus argenteus"}
        )
    assert raised.value.reason == "name_only_taxon_filter"


def test_a_bbox_too_wide_for_its_zoom_is_refused_before_anything_is_read(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, release_set_id = published
    result = read_botanical_occurrences(
        _request(release_set_id, zoom=13, bbox="-130.0,40.0,-110.0,52.0"), target=target
    )
    assert result["state"] == "refused"
    assert result["reason"] == "bbox_too_large_for_zoom"


def test_an_unpublished_release_set_is_unavailable_not_empty(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, _ = published
    result = read_botanical_occurrences(_request("0" * 64), target=target)
    assert result["state"] == "unavailable"


def test_a_generation_without_its_completion_marker_is_refused(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    """Parts on disk are not a publication; the marker is what makes a generation readable."""
    target, release_set_id = published
    (target.root / generation_prefix(release_set_id) / COMPLETION_MARKER).unlink()
    result = read_botanical_occurrences(_request(release_set_id), target=target)
    assert result["state"] == "refused"
    assert result["reason"] == "release_incomplete"


def test_the_cursor_continues_exactly_where_the_previous_page_stopped(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, release_set_id = published
    first = read_botanical_occurrences(_request(release_set_id, limit=1, spatial_quality="all"), target=target)
    assert first["truncated"] is True
    assert first["next_cursor"]
    second = read_botanical_occurrences(
        _request(release_set_id, limit=1, spatial_quality="all", cursor=first["next_cursor"]), target=target
    )
    first_ids = {feature["occurrence_id"] for feature in first["features"]}
    second_ids = {feature["occurrence_id"] for feature in second["features"]}
    assert first_ids.isdisjoint(second_ids)


def test_a_limit_over_the_ceiling_is_refused() -> None:
    with pytest.raises(BotanicalOccurrenceRequestError) as raised:
        parse_botanical_occurrence_request(
            {"release_set_id": "abcdefgh", "bbox": "-1,-1,1,1", "zoom": "13", "limit": "5000"}
        )
    assert raised.value.reason == "limit_exceeded"


def test_an_event_window_filters_by_overlap(published: tuple[LocalPublicationTarget, str]) -> None:
    target, release_set_id = published
    inside = read_botanical_occurrences(
        _request(release_set_id, spatial_quality="all", event_start="1987-06-10", event_end="1987-06-20"),
        target=target,
    )
    outside = read_botanical_occurrences(
        _request(release_set_id, spatial_quality="all", event_start="2020-01-01", event_end="2020-12-31"),
        target=target,
    )
    assert inside["counts"]["matched"] >= 1
    assert outside["counts"]["matched"] == 0
