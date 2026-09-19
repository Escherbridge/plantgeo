"""The phase-2A Parquet contracts are copies of agri-data-service's; this proves none has drifted.

Two assertions per subject, deliberately: the fixture one runs everywhere including inside the
Docker image where the sibling's source is absent, and the live one runs only in a repository
checkout and is what catches a change made on the sibling's side. A fixture that only ever compared
against itself would pass forever while the two services diverged. See `tests/AGENTS.md`.

One module rather than five near-identical ones: the subjects share their whole structure, and the
parametrisation names each failing subject as clearly as a separate file would.
"""

from __future__ import annotations

import json

import pytest
from parity_cases import FIXTURE_DIRECTORY
from parity_parquet_adapters import (
    ml_availability_adapter,
    ml_lanes_adapter,
    ml_markers_adapter,
    ml_paths_adapter,
    ml_streams_adapter,
    sibling_availability_adapter,
    sibling_lanes_adapter,
    sibling_markers_adapter,
    sibling_paths_adapter,
    sibling_streams_adapter,
)
from parity_parquet_cases import (
    evaluate_availability,
    evaluate_availability_metadata,
    evaluate_lanes,
    evaluate_parquet_markers,
    evaluate_parquet_paths,
    evaluate_streams,
    sibling_source_is_present,
)

#: subject -> (fixture name, this service's adapter factory, the sibling's, the evaluator).
PARITY_SUBJECTS = {
    "parquet_paths": ("parquet_paths", ml_paths_adapter, sibling_paths_adapter, evaluate_parquet_paths),
    "parquet_markers": ("parquet_markers", ml_markers_adapter, sibling_markers_adapter, evaluate_parquet_markers),
    "streams": ("streams", ml_streams_adapter, sibling_streams_adapter, evaluate_streams),
    "lanes": ("lanes", ml_lanes_adapter, sibling_lanes_adapter, evaluate_lanes),
    "availability": ("availability", ml_availability_adapter, sibling_availability_adapter, evaluate_availability),
}

REGENERATE_HINT = (
    "Decide which side is right, port the change, then regenerate with "
    "`uv run --no-sync python scripts/regenerate_parity_fixtures.py`. Regenerating first turns a "
    "caught drift into an accepted one."
)


def _fixture(name: str) -> dict[str, list[str]]:
    """Read one golden fixture."""
    payload = (FIXTURE_DIRECTORY / f"{name}.json").read_text(encoding="utf-8")
    return json.loads(payload)  # type: ignore[no-any-return]  # a fixture is a JSON object of lists


@pytest.mark.parametrize("subject", sorted(PARITY_SUBJECTS))
def test_this_service_reproduces_the_golden_parquet_outputs(subject: str) -> None:
    fixture_name, build_ml_adapter, _sibling, evaluate = PARITY_SUBJECTS[subject]

    assert evaluate(build_ml_adapter()) == _fixture(fixture_name)


@pytest.mark.skipif(not sibling_source_is_present(), reason="agri-data-service source is not on disk")
@pytest.mark.parametrize("subject", sorted(PARITY_SUBJECTS))
def test_the_sibling_still_produces_the_golden_parquet_outputs(subject: str) -> None:
    fixture_name, _ml, build_sibling_adapter, evaluate = PARITY_SUBJECTS[subject]

    assert evaluate(build_sibling_adapter()) == _fixture(fixture_name), (
        f"agri-data-service's {subject} contract no longer matches the parity fixture, so the two "
        f"services would now disagree about the warehouse. {REGENERATE_HINT}"
    )


def test_the_generation_metadata_values_match_their_own_fixture() -> None:
    """The one contract with no sibling side: its VALUE rendering is pinned by this service alone."""
    assert evaluate_availability_metadata(ml_availability_adapter()) == _fixture("availability_metadata")


def test_the_metadata_value_fixture_covers_exactly_the_parity_checked_key_set() -> None:
    """The keys are parity-checked; this is what keeps the value fixture from drifting away from them."""
    rendered = evaluate_availability_metadata(ml_availability_adapter())["generation_metadata"]
    rendered_keys = sorted(entry.split("=", 1)[0] for entry in rendered)

    assert rendered_keys == _fixture("availability")["generation_metadata_keys"]


def test_the_parity_harness_would_notice_a_drift() -> None:
    """Five green comparisons prove nothing unless the comparison itself can fail."""
    adapter = ml_paths_adapter()
    drifted = evaluate_parquet_paths(adapter)
    drifted["partition_path"] = ["ok:layer=signal/kind=observed/part-0.parquet"]

    assert drifted != _fixture("parquet_paths")
