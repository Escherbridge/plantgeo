"""Index the fixture corpus with the hashing embedder, then exercise every knowledge-base operation."""

import hashlib
import re
from pathlib import Path

import pytest
from conftest import FIXTURES, SOURCE_ID, SOURCE_URL, HashingEmbedder, OtherModelEmbedder

from strategy_knowledge.corpus import FAMILIES_FILE, CorpusStore
from strategy_knowledge.evaluation import GOLDEN_FILE, evaluate
from strategy_knowledge.index import FINDINGS, PASSAGES, STRATEGY_FACETS, Indexer, IndexMismatchError, IndexMissingError
from strategy_knowledge.knowledge_base import KnowledgeBase
from strategy_knowledge.queries import FindingSearch, PassageSearch, StrategySearch
from strategy_knowledge.site_profile import SiteProfile
from strategy_knowledge.vocabulary import CLAIM_TIER

MULCHING = {"post-fire-straw-mulching", "post-fire-hydromulch-application"}


def _independent_corpus_version(store: CorpusStore) -> str:
    """AGENTS.md's definition recomputed from scratch: SHA-256 over sorted `relative path NUL sha256(file)`."""
    covered = [store.root / "corpus" / "sources.json"]
    for directory in ("chunk_plans", "findings", "strategies"):
        covered.extend((store.root / "corpus" / directory).glob("*.json"))
    lines = sorted(
        f"{path.relative_to(store.root).as_posix()}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
        for path in covered
        if path.is_file()
    )
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def test_build_counts_and_incremental_no_op(fixture_store: CorpusStore, embedder: HashingEmbedder) -> None:
    indexer = Indexer(fixture_store, embedder)
    built = indexer.rebuild_all()
    assert built["collections"][STRATEGY_FACETS]["records"] == 20
    assert built["collections"][FINDINGS]["records"] == 3
    assert built["collections"][PASSAGES]["records"] == 5
    assert built["index_stamped_full"] is True
    again = indexer.update(SOURCE_ID)
    for name in (STRATEGY_FACETS, FINDINGS, PASSAGES):
        counts = again["collections"][name]
        assert counts["added"] == counts["replaced"] == counts["removed"] == counts["records_written"] == 0


def test_corpus_version_is_a_content_hash(fixture_store: CorpusStore, embedder: HashingEmbedder) -> None:
    built = Indexer(fixture_store, embedder).rebuild_all()
    version = built["corpus_version"]
    assert re.fullmatch(r"[0-9a-f]{64}", version)
    assert version == _independent_corpus_version(fixture_store)
    families = fixture_store.path(FAMILIES_FILE)
    original = families.read_bytes()
    families.write_bytes(original.replace(b"Post-fire mulching", b"Post-fire mulches"))
    assert fixture_store.corpus_version() != version
    assert fixture_store.corpus_version() == _independent_corpus_version(fixture_store)
    families.write_bytes(original)
    assert fixture_store.corpus_version() == version


def test_search_strategies_collapses_facets_and_caps_families(knowledge_base: KnowledgeBase) -> None:
    response = knowledge_base.search_strategies(
        StrategySearch(query="mulch burned slopes before the first fall rains", family_diversity=1),
    )
    assert response["claim_tier"] == CLAIM_TIER
    ids = [result["strategy_id"] for result in response["results"]]
    assert len(ids) == len(set(ids))
    assert ids[0] in MULCHING
    assert len(MULCHING & set(ids)) == 1
    assert response["family_diversity"]["held_back_by_family"]["post-fire-mulching"]
    first = response["results"][0]
    assert first["matched_facet"] in {"overview", "how_to", "fit", "outcomes"}
    assert first["review_state"] == "machine_extracted"
    assert first["citation_count"] == 1


def test_min_evidence_and_category_filters(knowledge_base: KnowledgeBase) -> None:
    strong = knowledge_base.search_strategies(StrategySearch(query="soil", min_evidence="review_or_meta_analysis"))
    assert {result["evidence_strength"] for result in strong["results"]} == {"review_or_meta_analysis"}
    assert strong["applied_filters"]["min_evidence_rank"] == 6
    mulching = knowledge_base.search_strategies(StrategySearch(query="soil", category=["mulching"], family_diversity=0))
    assert {result["strategy_id"] for result in mulching["results"]} == MULCHING


def test_site_profile_is_echoed_with_filters_and_boosts(knowledge_base: KnowledgeBase) -> None:
    profile = SiteProfile(burn_severity="high", slope_pct=40, land_cover="Evergreen Forest", days_since_fire=20)
    response = knowledge_base.search_strategies(StrategySearch(query="stabilise burned ground", site_profile=profile))
    echo = response["site_profile"]
    assert echo["filters"]["land_use"] == ["forest"]
    assert set(echo["boosts"]["soil_conditions"]) == {"steep_slope", "burned_high_severity", "hydrophobic"}
    assert echo["boosts"]["fire_phase"] == ["post_fire_emergency"]
    assert response["applied_filters"]["land_use"] == ["forest"]
    assert response["applied_filters"]["land_use_also_admits"] == ["general", "(untagged)"]
    straw = next(result for result in response["results"] if result["strategy_id"] == "post-fire-straw-mulching")
    assert {"soil_burned_high_severity", "soil_hydrophobic", "phase_post_fire_emergency"} <= set(straw["boosted_by"])


def test_explicit_land_use_overrides_the_derived_one(knowledge_base: KnowledgeBase) -> None:
    profile = SiteProfile(land_cover="Cultivated Crops")
    response = knowledge_base.search_strategies(
        StrategySearch(query="cover", land_use=["forest"], site_profile=profile),
    )
    assert response["applied_filters"]["land_use"] == ["forest"]
    assert response["site_profile"]["overridden_by_explicit_filters"] == ["land_use"]


def test_get_strategy_resolves_merge_aliases_and_enriches_citations(knowledge_base: KnowledgeBase) -> None:
    response = knowledge_base.get_strategy(["post-fire-straw-or-hay-mulching", "no-such-strategy"])
    record = response["strategies"][0]
    assert record["strategy_id"] == "post-fire-straw-mulching"
    assert record["resolved_from"] == "post-fire-straw-or-hay-mulching"
    assert set(record["facets"]) == {"overview", "how_to", "fit", "outcomes"}
    citation = record["citations"][0]
    assert citation["url"] == SOURCE_URL
    assert citation["excerpt"].startswith("Spread certified weed-free straw mulch")
    assert response["not_found"] == ["no-such-strategy"]


def test_get_family_and_compare(knowledge_base: KnowledgeBase) -> None:
    family = knowledge_base.get_family("post-fire-mulching")
    assert {member["strategy_id"] for member in family["members"]} == MULCHING
    assert family["name"] == "Post-fire mulching"
    comparison = knowledge_base.compare_strategies(["post-fire-straw-mulching", "grass-cover-cropping", "nope"])
    rows = {row["attribute"]: row["values"] for row in comparison["rows"]}
    assert rows["category"] == {"post-fire-straw-mulching": "mulching", "grass-cover-cropping": "cover_cropping"}
    assert rows["cost_level"]["grass-cover-cropping"] == "low"
    assert comparison["not_found"] == ["nope"]


def test_search_findings_returns_verbatim_magnitude_and_source(knowledge_base: KnowledgeBase) -> None:
    response = knowledge_base.search_findings(FindingSearch(query="straw mulch sediment yield"))
    first = response["results"][0]
    assert first["finding_id"] == f"{SOURCE_ID}#F17"
    assert first["magnitude"] == "reduced sediment yield by 50-70%"
    assert first["source"]["url"] == SOURCE_URL
    increasing = knowledge_base.search_findings(FindingSearch(query="water", direction="increase"))
    assert [result["finding_id"] for result in increasing["results"]] == [f"{SOURCE_ID}#F23"]


def test_search_passages_scopes_and_default_exclusions(knowledge_base: KnowledgeBase) -> None:
    request = PassageSearch(query="cereal rye winter", strategy_id="grass-cover-cropping")
    response = knowledge_base.search_passages(request)
    assert [result["chunk_id"] for result in response["results"]] == [f"{SOURCE_ID}#L27-31"]
    passage = response["results"][0]
    assert passage["text"].startswith("Cover crops through winter Cereal rye planted")
    assert passage["section_path"] == ["Fixture guide", "Cover crops through winter"]
    assert passage["review_state"] == "verbatim_source_text"
    assert response["applied_filters"]["excluded_content_type"] == ["noise"]
    references = knowledge_base.search_passages(PassageSearch(query="reference", content_type=["references"]))
    assert [result["content_type"] for result in references["results"]] == ["references"]


def test_scope_filters_resolve_merge_aliases(knowledge_base: KnowledgeBase) -> None:
    request = PassageSearch(query="straw", strategy_id="post-fire-straw-or-hay-mulching")
    passages = knowledge_base.search_passages(request)
    assert [result["chunk_id"] for result in passages["results"]] == [f"{SOURCE_ID}#L14-20"]
    findings_request = FindingSearch(query="straw", strategy_id="post-fire-straw-or-hay-mulching")
    findings = knowledge_base.search_findings(findings_request)
    assert [result["finding_id"] for result in findings["results"]] == [f"{SOURCE_ID}#F17"]


def test_list_facets_and_sources(knowledge_base: KnowledgeBase) -> None:
    facets = knowledge_base.list_facets()
    categories = {row["value"]: row for row in facets["vocabularies"]["category"]}
    assert categories["mulching"]["strategies"] == 2
    regions = [row["value"] for row in facets["vocabularies"]["region"]]
    assert "us_midwest" in regions
    assert "oceania" in regions
    assert facets["totals"] == {"strategies": 5, "families": 4, "findings": 3, "passages": 5, "sources": 1}
    sources = knowledge_base.list_sources()["sources"]
    assert len(sources) == 1
    assert sources[0]["source_id"] == SOURCE_ID
    assert (sources[0]["chunks"], sources[0]["findings"], sources[0]["strategies_citing"]) == (5, 3, 5)


def test_open_refuses_an_index_built_for_another_model(fixture_store: CorpusStore, embedder: HashingEmbedder) -> None:
    Indexer(fixture_store, embedder).rebuild_all()
    with pytest.raises(IndexMismatchError, match="embedding_model"):
        KnowledgeBase.open(fixture_store, OtherModelEmbedder())


def test_open_refuses_a_missing_index(tmp_path: Path, embedder: HashingEmbedder) -> None:
    with pytest.raises(IndexMissingError):
        KnowledgeBase.open(CorpusStore(tmp_path / "empty"), embedder)


def test_example_golden_queries_score(knowledge_base: KnowledgeBase) -> None:
    golden = GOLDEN_FILE.validate_json((FIXTURES / "golden_example.json").read_bytes())
    report = evaluate(knowledge_base, golden)
    assert report["queries"] == 2
    assert report["per_query"][0]["hit"]
    assert 0.0 < report["mrr"] <= 1.0
