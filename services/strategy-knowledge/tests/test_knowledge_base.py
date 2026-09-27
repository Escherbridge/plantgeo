"""Knowledge-base behaviour over a real index: paging, region hierarchy, aliases, echoes, malformed rows."""

from pathlib import Path
from typing import Any

import pytest
from conftest import SOURCE_ID, HashingEmbedder, seed_store

from strategy_knowledge.corpus import REGISTRY_FILE, CorpusStore, read_json, write_json
from strategy_knowledge.index import PASSAGES, STRATEGY_FACETS, Indexer
from strategy_knowledge.knowledge_base import CollectionView, KnowledgeBase, RequestError, snippet
from strategy_knowledge.metadata import FACET_TEXT_KEY, SEARCH_TERMS_KEY
from strategy_knowledge.queries import FindingSearch, PassageSearch, StrategySearch
from strategy_knowledge.site_profile import SiteProfile
from strategy_knowledge.vocabulary import CLAIM_TIER

STRAW = "post-fire-straw-mulching"
STRAW_ALIAS = "post-fire-straw-or-hay-mulching"
HYDROMULCH = "post-fire-hydromulch-application"
BIOCHAR = "biochar-amendment-coarse-soils"
COVER = "grass-cover-cropping"
COVER_CANDIDATE = "winter-cereal-rye-cover"


def _ids(response: dict[str, Any]) -> list[str]:
    return [row["strategy_id"] for row in response["results"]]


def test_pages_are_disjoint_and_add_up_to_one_larger_page(knowledge_base: KnowledgeBase) -> None:
    def page(offset: int, limit: int) -> dict[str, Any]:
        return knowledge_base.search_strategies(
            StrategySearch(query="soil erosion", family_diversity=0, offset=offset, limit=limit),
        )

    first, second, both = page(0, 2), page(2, 2), page(0, 4)
    assert set(_ids(first)).isdisjoint(_ids(second))
    assert _ids(first) + _ids(second) == _ids(both)
    scores = [row["score"] for row in [*first["results"], *second["results"]]]
    assert scores == [row["score"] for row in both["results"]]
    assert first["next_offset"] == 2
    assert first["truncated"] is False

    passages = [
        knowledge_base.search_passages(PassageSearch(query="soil", offset=offset, limit=limit))
        for offset, limit in ((0, 2), (2, 2), (0, 4))
    ]
    ids = [[row["passage_id"] for row in response["results"]] for response in passages]
    assert ids[0] + ids[1] == ids[2]


def test_a_small_candidate_pool_reports_truncation(fixture_store: CorpusStore, embedder: HashingEmbedder) -> None:
    Indexer(fixture_store, embedder).rebuild_all()
    small = KnowledgeBase.open(fixture_store, embedder, candidate_pool=2)
    response = small.search_strategies(StrategySearch(query="soil", family_diversity=0, limit=10))
    assert response["truncated"] is True
    assert response["candidate_pool"] == 2
    assert len(response["results"]) < 5
    whole = KnowledgeBase.open(fixture_store, embedder)
    assert whole.search_strategies(StrategySearch(query="soil", family_diversity=0, limit=10))["truncated"] is False


@pytest.fixture
def regional_knowledge_base(tmp_path: Path, embedder: HashingEmbedder) -> KnowledgeBase:
    """The fixture corpus with hydromulch re-tagged `north_america_general`; biochar is already `global`."""
    store = seed_store(tmp_path / "regional")
    registry = read_json(store.path(REGISTRY_FILE))
    for row in registry["strategies"]:
        if row["strategy_id"] == HYDROMULCH:
            row["region"] = ["north_america_general"]
    write_json(store.path(REGISTRY_FILE), registry)
    Indexer(store, embedder).rebuild_all()
    return KnowledgeBase.open(store, embedder)


def test_a_north_american_region_admits_global_and_north_america_general(
    regional_knowledge_base: KnowledgeBase,
) -> None:
    inland = regional_knowledge_base.search_strategies(
        StrategySearch(query="soil", region=["pnw_inland"], family_diversity=0, limit=10),
    )
    assert set(_ids(inland)) == {BIOCHAR, COVER, HYDROMULCH}
    assert inland["applied_filters"]["region_also_admits"] == [
        "general",
        "global",
        "north_america_general",
        "(untagged)",
    ]
    europe = regional_knowledge_base.search_strategies(
        StrategySearch(query="soil", region=["europe"], family_diversity=0, limit=10),
    )
    assert set(_ids(europe)) == {BIOCHAR, COVER}
    from_profile = regional_knowledge_base.search_strategies(
        StrategySearch(query="soil", site_profile=SiteProfile(region="pnw_inland"), family_diversity=0, limit=10),
    )
    assert set(_ids(from_profile)) == {BIOCHAR, COVER, HYDROMULCH}


def test_scope_ids_cover_merged_and_matched_aliases(knowledge_base: KnowledgeBase) -> None:
    assert knowledge_base._scope_ids(STRAW_ALIAS) == (STRAW, STRAW_ALIAS)
    assert knowledge_base._scope_ids(COVER) == (COVER, COVER_CANDIDATE)
    assert knowledge_base._scope_ids("unknown-id") == ("unknown-id",)
    resolved = knowledge_base.get_strategy([COVER_CANDIDATE])["strategies"]
    assert [(record["strategy_id"], record["resolved_from"]) for record in resolved] == [(COVER, COVER_CANDIDATE)]


def test_alias_and_canonical_are_one_strategy(knowledge_base: KnowledgeBase) -> None:
    family = knowledge_base.families["post-fire-mulching"]
    knowledge_base.families["post-fire-mulching"] = family.model_copy(
        update={"member_strategy_ids": [*family.member_strategy_ids, STRAW_ALIAS]},
    )
    members = [member["strategy_id"] for member in knowledge_base.get_family("post-fire-mulching")["members"]]
    assert sorted(members) == sorted([STRAW, HYDROMULCH])
    records = knowledge_base.get_strategy([STRAW, STRAW_ALIAS])["strategies"]
    assert [record["strategy_id"] for record in records] == [STRAW]
    comparison = knowledge_base.compare_strategies([STRAW, STRAW_ALIAS, COVER])
    assert comparison["strategy_ids"] == [STRAW, COVER]
    assert comparison["claim_tier"] == CLAIM_TIER
    with pytest.raises(RequestError, match="distinct"):
        knowledge_base.compare_strategies([STRAW_ALIAS, STRAW])


def test_list_sources_reports_review_state(knowledge_base: KnowledgeBase) -> None:
    sources = knowledge_base.list_sources()["sources"]
    assert [source["review_state"] for source in sources] == ["machine_extracted"]


def test_search_findings_does_not_echo_boosts_it_does_not_apply(knowledge_base: KnowledgeBase) -> None:
    profile = SiteProfile(burn_severity="high", slope_pct=45)
    response = knowledge_base.search_findings(FindingSearch(query="sediment", site_profile=profile))
    assert response["site_profile"]["boosts"] == {"soil_conditions": [], "fire_phase": []}
    assert any("not applied to findings" in note for note in response["site_profile"]["notes"])
    assert response["boosts"]["soil_conditions"] == []
    assert response["boosts"]["fire_phase"] == []


def test_a_malformed_passage_row_is_skipped_not_raised(knowledge_base: KnowledgeBase) -> None:
    view = knowledge_base.views[PASSAGES]
    broken = f"{SOURCE_ID}#L14-20#W0"
    view.metadatas.pop(broken)
    response = knowledge_base.search_passages(PassageSearch(query="straw mulch slopes", limit=10))
    assert broken not in [row["passage_id"] for row in response["results"]]
    view.documents[f"{SOURCE_ID}#L21-26#W0"] = ""
    again = knowledge_base.search_passages(PassageSearch(query="biochar sandy", limit=10))
    assert f"{SOURCE_ID}#L21-26#W0" not in [row["passage_id"] for row in again["results"]]


def test_facet_documents_name_their_strategy_and_keep_the_raw_text(knowledge_base: KnowledgeBase) -> None:
    view = knowledge_base.views[STRATEGY_FACETS]
    strategy = knowledge_base.strategies[STRAW]
    document_id = f"{STRAW}::how_to"
    assert view.documents[document_id] == f"{strategy.name} (how to): {strategy.facets.how_to}"
    assert view.metadatas[document_id][FACET_TEXT_KEY] == strategy.facets.how_to
    hit = next(
        row
        for row in knowledge_base.search_strategies(StrategySearch(query="straw", family_diversity=0))["results"]
        if row["strategy_id"] == STRAW
    )
    assert hit["matched_facet_snippet"] == snippet(getattr(strategy.facets, hit["matched_facet"]))


def test_an_unknown_scope_id_is_reported_not_found(knowledge_base: KnowledgeBase) -> None:
    findings = knowledge_base.search_findings(FindingSearch(query="straw", strategy_id="no-such-strategy"))
    assert findings["results"] == []
    assert findings["not_found"] == ["no-such-strategy"]
    passages = knowledge_base.search_passages(PassageSearch(query="straw", source_id="no-such-source"))
    assert passages["results"] == []
    assert passages["not_found"] == ["no-such-source"]
    known = knowledge_base.search_findings(FindingSearch(query="straw", strategy_id=STRAW_ALIAS))
    assert known["not_found"] == []
    assert known["results"]
    scoped = knowledge_base.search_passages(PassageSearch(query="straw", source_id=SOURCE_ID))
    assert scoped["not_found"] == []
    assert scoped["results"]


def test_filter_semantics_name_every_region_a_filter_admits(knowledge_base: KnowledgeBase) -> None:
    semantics = " ".join(knowledge_base.list_facets()["filter_semantics"])
    assert "'global'" in semantics
    assert "'north_america_general'" in semantics


def test_search_strategies_echoes_query_intent_and_context_use(knowledge_base: KnowledgeBase) -> None:
    plain = knowledge_base.search_strategies(StrategySearch(query="straw mulch", family_diversity=0))
    assert plain["context_query_used"] is False
    assert plain["query_intent"] == {
        "lay_terms": [],
        "ph_direction": None,
        "soil_condition_boosts": [],
        "expansion_tokens": [],
    }
    blank = knowledge_base.search_strategies(StrategySearch(query="straw mulch", context_query="   "))
    assert blank["context_query_used"] is False
    burnt = knowledge_base.search_strategies(
        StrategySearch(query="straw mulch", context_query="the hillside got burnt", family_diversity=0),
    )
    assert burnt["context_query_used"] is True
    assert burnt["query_intent"]["lay_terms"] == ["burnt", "hillside"]
    assert burnt["query_intent"]["soil_condition_boosts"] == ["burned_high_severity"]
    assert burnt["boosts"]["query_intent_soil_conditions"] == ["burned_high_severity"]
    straw = next(row for row in burnt["results"] if row["strategy_id"] == STRAW)
    assert "soil_burned_high_severity" in straw["boosted_by"]


def test_a_context_query_adds_a_second_ranking_signal(knowledge_base: KnowledgeBase) -> None:
    alone = knowledge_base.search_strategies(StrategySearch(query="soil", family_diversity=0, limit=10))
    guided = knowledge_base.search_strategies(
        StrategySearch(query="soil", context_query="biochar for sandy coarse ground", family_diversity=0, limit=10),
    )
    assert guided["context_query_used"] is True
    assert _ids(guided).index(BIOCHAR) <= _ids(alone).index(BIOCHAR)
    alone_score = next(row["score"] for row in alone["results"] if row["strategy_id"] == BIOCHAR)
    assert next(row["score"] for row in guided["results"] if row["strategy_id"] == BIOCHAR) > alone_score


def test_search_findings_reads_the_context_but_applies_no_soil_boost(knowledge_base: KnowledgeBase) -> None:
    response = knowledge_base.search_findings(FindingSearch(query="sediment", context_query="burnt hillside"))
    assert response["context_query_used"] is True
    assert response["query_intent"]["lay_terms"] == ["burnt", "hillside"]
    assert response["query_intent"]["soil_condition_boosts"] == []
    assert "burned" in response["query_intent"]["expansion_tokens"]
    assert response["results"]


def test_bm25_reads_family_text_and_registry_search_terms(tmp_path: Path, embedder: HashingEmbedder) -> None:
    store = seed_store(tmp_path / "terms")
    registry = read_json(store.path(REGISTRY_FILE))
    for row in registry["strategies"]:
        if row["strategy_id"] == COVER:
            row["search_terms"] = ["zzyzx hardpan buster"]
    write_json(store.path(REGISTRY_FILE), registry)
    Indexer(store, embedder).rebuild_all()
    terms = KnowledgeBase.open(store, embedder)
    view = terms.views[STRATEGY_FACETS]
    cover_documents = [identifier for identifier, row in view.metadatas.items() if row["strategy_id"] == COVER]
    assert cover_documents
    for identifier in cover_documents:
        assert view.metadatas[identifier][SEARCH_TERMS_KEY] == (
            "Cover cropping|Living cover between cash crops.|zzyzx hardpan buster"
        )
    assert {identifier.split("::")[0] for identifier in view.lexical.search("zzyzx", None, 10)} == {COVER}
    assert _ids(terms.search_strategies(StrategySearch(query="zzyzx", limit=1))) == [COVER]


class _IndexBeforeSearchTerms:
    """A collection whose metadata predates `search_terms`, as in the index published before this key existed."""

    def get(self, include: list[str]) -> dict[str, Any]:
        """Four facet rows with keywords but no search_terms key."""
        assert "metadatas" in include
        ids = ["a::overview", "b::overview", "c::overview", "d::overview"]
        return {
            "ids": ids,
            "documents": [
                "Straw mulch (overview): cover",
                "Biochar (overview): char",
                "Rye (overview): cover",
                "Gypsum (overview): calcium",
            ],
            "metadatas": [{"strategy_id": identifier[0], "keywords": "bales"} for identifier in ids[:1]]
            + [{"strategy_id": identifier[0]} for identifier in ids[1:]],
        }


def test_an_index_built_before_search_terms_still_loads_and_ranks() -> None:
    view = CollectionView.load(_IndexBeforeSearchTerms())  # type: ignore[arg-type]
    assert view.size == 4
    assert view.lexical.search("bales", None, 5) == ["a::overview"]
    assert view.lexical.search("straw", None, 5) == ["a::overview"]
