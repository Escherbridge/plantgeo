"""The brief readers match the shared reader-constant fixture the web reader is checked against too.

`tests/fixtures/site_brief_reader_constants.json` pins CONTRACT C5.1's reader constants and C2's flag
rule; `src/__tests__/services/site-brief-readers.test.ts` asserts the same file (review M9).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agri_data_service.agent import graph as agent_graph
from agri_data_service.agent import site_brief, soil_properties
from agri_data_service.agent import tools as agent_tools

_REQUESTED_RADIUS_METERS = 900

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "site_brief_reader_constants.json").read_text(encoding="utf-8")
)


def test_the_fixture_is_the_version_both_languages_read() -> None:
    assert FIXTURE["fixture_version"] == "site-brief-reader-constants/1"


@pytest.mark.parametrize(("value", "expected"), FIXTURE["flags"]["cases"])
def test_both_flags_parse_by_the_shared_rule(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    flags = FIXTURE["flags"]
    assert flags["soil_reads_variable"] == soil_properties.READS_ENABLED_VARIABLE
    assert flags["site_brief_variable"] == site_brief.SITE_BRIEF_ENABLED_VARIABLE
    for variable in (flags["soil_reads_variable"], flags["site_brief_variable"]):
        if value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
    assert soil_properties.soil_reads_enabled() is expected
    assert site_brief.site_brief_enabled() is expected
    assert site_brief.soil_context_enabled() is expected


def test_the_brief_read_bounds_match() -> None:
    reads = FIXTURE["brief_reads"]
    assert reads["deadline_seconds"] == agent_graph.SITE_BRIEF_SECTION_TIMEOUT_SECONDS
    assert reads["concurrency"] == agent_graph.SITE_BRIEF_READ_CONCURRENCY


def test_the_fire_and_weather_constants_match() -> None:
    assert FIXTURE["fire"]["detection_radius_m"] == agent_graph.FIRE_DETECTION_RADIUS_METERS
    assert FIXTURE["fire"]["detection_window_days"] == agent_graph.FIRE_DETECTION_WINDOW_DAYS
    assert FIXTURE["weather"]["radius_m"] == agent_graph.WEATHER_RADIUS_METERS
    assert FIXTURE["weather"]["days_back"] == agent_graph.WEATHER_DAYS_BACK


@pytest.mark.parametrize(("value", "expected"), FIXTURE["fire"]["burn_severity_cases"])
def test_burn_severity_classes_map_alike(value: object, expected: str | None) -> None:
    assert site_brief.burn_severity_of(value) == expected


def test_the_soil_reader_constants_match() -> None:
    soil = FIXTURE["soil"]
    assert soil["default_radius_m"] == soil_properties.DEFAULT_RADIUS_METERS
    assert soil["min_radius_m"] == soil_properties.MIN_RADIUS_METERS
    assert soil["max_radius_m"] == soil_properties.MAX_RADIUS_METERS
    assert soil["cell_degrees"] == soil_properties.LATTICE_DEGREES
    assert soil["earth_radius_m"] == soil_properties.EARTH_RADIUS_METERS
    assert soil["coverage_cosine_floor"] == soil_properties.COVERAGE_COSINE_FLOOR
    envelope = soil["envelope"]
    assert (envelope["west"], envelope["south"], envelope["east"], envelope["north"]) == (
        soil_properties.LATTICE_WEST,
        soil_properties.LATTICE_SOUTH,
        soil_properties.LATTICE_EAST,
        soil_properties.LATTICE_NORTH,
    )


@pytest.mark.parametrize(("requested", "searched"), FIXTURE["soil"]["search_radius_cases"])
def test_the_soil_search_radius_rule_matches(requested: float, searched: int) -> None:
    assert soil_properties.search_radius(requested) == searched


@pytest.mark.parametrize(("distance_m", "phrase"), FIXTURE["soil"]["distance_phrase_cases"])
def test_the_soil_distance_phrase_matches_at_the_boundary(distance_m: int, phrase: str) -> None:
    assert site_brief.soil_distance_phrase(distance_m) == phrase


@pytest.mark.parametrize("case", FIXTURE["soil"]["coverage_cases"], ids=lambda case: case["name"])
def test_the_soil_coverage_rule_matches(case: dict[str, Any]) -> None:
    outside = soil_properties.outside_release_coverage(case["longitude"], case["latitude"], case["radius_m"])
    assert outside is case["outside"]


async def test_an_invalid_coordinate_answers_in_the_soil_result_shape() -> None:
    """Review m5: C6's state/reason shape, with the web reader's reason for a point no cell can serve."""
    async with agent_tools.run_context():
        payload = json.loads(
            await agent_tools.query_soil_properties_at_point(-200.0, 43.6, radius_meters=_REQUESTED_RADIUS_METERS)
        )
    assert payload["state"] == "unavailable"
    assert payload["reason"] == "outside_release_coverage"
    assert payload["radius_m"] == _REQUESTED_RADIUS_METERS
    assert payload["soilgrids"] is None
    assert "model estimates" in payload["note"]
    assert "longitude must be within" in payload["error"]
