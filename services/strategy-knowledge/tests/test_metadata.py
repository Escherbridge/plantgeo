"""DESIGN.md section 8 flattening: one-hot keys, scalar values, untagged markers, fire-phase gating."""

import pytest
from conftest import FIXTURES, SOURCE_ID

from strategy_knowledge.corpus import read_json
from strategy_knowledge.metadata import (
    LAND_USE_UNTAGGED,
    RECORD_HASH_KEY,
    REGION_UNTAGGED,
    finding_metadata,
    passage_document,
    passage_metadata,
    sealed,
    strategy_facet_metadata,
    tagged_one_hot,
)
from strategy_knowledge.models import Chunk, Finding, PassageWindow, RegistryStrategy


@pytest.fixture
def registry() -> dict[str, RegistryStrategy]:
    """The fixture registry by strategy_id."""
    rows = read_json(FIXTURES / "strategy_registry.json")["strategies"]
    return {row["strategy_id"]: RegistryStrategy.model_validate(row) for row in rows}


def test_strategy_metadata_is_scalar_one_hot(registry: dict[str, RegistryStrategy]) -> None:
    metadata = strategy_facet_metadata(registry["post-fire-straw-mulching"], "how_to")
    assert all(isinstance(value, str | int | float | bool) for value in metadata.values())
    assert metadata["strategy_id"] == "post-fire-straw-mulching"
    assert metadata["facet"] == "how_to"
    assert metadata["family_id"] == "post-fire-mulching"
    assert metadata["goal_erosion_control"] == "stated"
    assert metadata["lu_rangeland"] is True
    assert metadata["region_southwest"] is True
    assert metadata["soil_hydrophobic"] is True
    assert metadata["scale_field"] is True
    assert metadata["evidence_rank"] == 6
    assert metadata["source_ids"] == SOURCE_ID
    assert "goal_soil_health" not in metadata


def test_fire_phase_keys_need_the_wildfire_goal(registry: dict[str, RegistryStrategy]) -> None:
    with_goal = strategy_facet_metadata(registry["post-fire-straw-mulching"], "overview")
    assert with_goal["phase_post_fire_emergency"] is True
    biochar = registry["biochar-amendment-coarse-soils"]
    without_goal = biochar.model_copy(update={"fire_phase": ["long_term_resilience"]})
    metadata = strategy_facet_metadata(without_goal, "overview")
    assert not any(key.startswith("phase_") for key in metadata)


def test_untagged_marker_only_when_empty() -> None:
    assert tagged_one_hot("lu_", [], LAND_USE_UNTAGGED) == {LAND_USE_UNTAGGED: True}
    assert tagged_one_hot("lu_", ["forest", "forest"], LAND_USE_UNTAGGED) == {"lu_forest": True}


def test_finding_metadata_links_strategies_and_marks_untagged_region() -> None:
    finding = Finding(
        finding_id=f"{SOURCE_ID}#F17",
        claim="claim",
        direction="decrease",
        study_type="field_experiment",
        evidence_strength="field_trial_or_case_study",
        land_use=["rangeland"],
        linked_strategy_ids=["post-fire-straw-mulching"],
        goals={"erosion_control": "stated"},
    )
    metadata = finding_metadata(finding, SOURCE_ID)
    assert metadata["linked_post-fire-straw-mulching"] is True
    assert metadata[REGION_UNTAGGED] is True
    assert metadata["lu_rangeland"] is True
    assert metadata["evidence_rank"] == 4
    assert metadata["direction"] == "decrease"


def test_passage_metadata_and_document_prefix() -> None:
    chunk = Chunk(
        chunk_id=f"{SOURCE_ID}#L14-20",
        line_start=14,
        line_end=20,
        section_path=["Fixture guide", "Straw mulching"],
        title="Straw mulch",
        content_type="research_finding",
        relevance="core",
        keywords=["tons/acre"],
        linked_strategy_ids=["post-fire-straw-mulching"],
    )
    window = PassageWindow(
        passage_id=f"{SOURCE_ID}#L14-20#W0",
        chunk_id=chunk.chunk_id,
        window_index=0,
        source_id=SOURCE_ID,
        line_start=14,
        line_end=19,
        word_count=3,
        text="Spread straw mulch.",
    )
    metadata = passage_metadata(chunk, window)
    assert metadata["chunk_id"] == chunk.chunk_id
    assert metadata["content_type"] == "research_finding"
    assert metadata["section_path"] == "Fixture guide > Straw mulching"
    assert metadata["linked_post-fire-straw-mulching"] is True
    assert metadata[LAND_USE_UNTAGGED] is True
    assert passage_document(chunk.section_path, window.text) == "Fixture guide > Straw mulching\n\nSpread straw mulch."
    assert passage_document([], window.text) == window.text


def test_record_hash_ignores_corpus_version() -> None:
    first = sealed("text", {"strategy_id": "a"}, "version-one")
    second = sealed("text", {"strategy_id": "a"}, "version-two")
    changed = sealed("other text", {"strategy_id": "a"}, "version-one")
    assert first[RECORD_HASH_KEY] == second[RECORD_HASH_KEY]
    assert first[RECORD_HASH_KEY] != changed[RECORD_HASH_KEY]
    assert first["corpus_version"] == "version-one"


def test_the_strategy_name_counts_once_for_bm25(registry: dict[str, RegistryStrategy]) -> None:
    strategy = registry["post-fire-straw-mulching"]
    metadata = strategy_facet_metadata(strategy, "how_to")
    assert strategy.name not in str(metadata["keywords"]).split("|")
    assert metadata["name"] == strategy.name
