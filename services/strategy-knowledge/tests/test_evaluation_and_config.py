"""hit@k / MRR arithmetic, the golden-file format, and settings that never reveal secrets."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES

from strategy_knowledge.config import (
    DEFAULT_CANDIDATE_POOL,
    ObjectStoreNotConfiguredError,
    load_settings,
    parse_env_file,
)
from strategy_knowledge.evaluation import GOLDEN_FILE, evaluate, forbidden_hit, reciprocal_rank
from strategy_knowledge.queries import FindingSearch, PassageSearch, StrategySearch


@dataclass
class _FakeStrategy:
    """The one field `_strategy_families` reads off a registry strategy."""

    family_id: str


class StubSearcher:
    """Returns a fixed ranking per query and records the requests it saw.

    `findings`/`passages` map a query to a ranked list of finding_id/source_id, exercising the "findings"/
    "passages" `kind` routing. `strategies` maps strategy_id -> family_id, exercising family coverage.
    """

    def __init__(
        self,
        rankings: dict[str, list[str]],
        findings: dict[str, list[str]] | None = None,
        passages: dict[str, list[str]] | None = None,
        strategies: dict[str, str] | None = None,
    ) -> None:
        self.rankings = rankings
        self.findings = findings or {}
        self.passages = passages or {}
        self.strategies = {
            strategy_id: _FakeStrategy(family_id) for strategy_id, family_id in (strategies or {}).items()
        }
        self.requests: list[StrategySearch] = []

    def search_strategies(self, request: StrategySearch) -> dict[str, Any]:
        """Ranked ids for the query, truncated to the request's limit."""
        self.requests.append(request)
        ranked = self.rankings[request.query][: request.limit]
        return {"results": [{"strategy_id": identifier} for identifier in ranked]}

    def search_findings(self, request: FindingSearch) -> dict[str, Any]:
        """Ranked finding ids for the query, truncated to the request's limit."""
        ranked = self.findings[request.query][: request.limit]
        return {"results": [{"finding_id": identifier} for identifier in ranked]}

    def search_passages(self, request: PassageSearch) -> dict[str, Any]:
        """Ranked passages for the query (one per source id), truncated to the request's limit."""
        ranked = self.passages[request.query][: request.limit]
        return {"results": [{"source": {"source_id": identifier}} for identifier in ranked]}


def test_reciprocal_rank_within_k() -> None:
    assert reciprocal_rank(["a", "b", "c"], ["c"], 3) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a", "b", "c"], ["c"], 2) == 0.0
    assert reciprocal_rank(["a", "b", "c"], ["z", "a"], 3) == 1.0


def test_evaluate_aggregates_hit_rate_and_mrr() -> None:
    golden = GOLDEN_FILE.validate_python(
        [
            {"query": "first", "filters": {"goals": ["soil_health"]}, "expected_any_of": ["b"], "k": 3},
            {"query": "second", "expected_any_of": ["z"], "k": 2},
        ],
    )
    searcher = StubSearcher({"first": ["a", "b", "c"], "second": ["x", "y", "z"]})
    report = evaluate(searcher, golden)
    assert report["queries"] == 2
    assert report["hit_at_k"] == 0.5
    assert report["mrr"] == 0.25
    assert report["per_query"][0]["first_hit_rank"] == 2
    assert report["per_query"][1]["first_hit_rank"] is None
    assert searcher.requests[0].goals == ["soil_health"]
    assert searcher.requests[0].limit == 3


def test_golden_filters_are_validated() -> None:
    item = {"query": "q", "filters": {"goals": ["not_a_goal"]}, "expected_any_of": ["a"]}
    golden = GOLDEN_FILE.validate_python([item])
    with pytest.raises(ValueError, match="goals"):
        evaluate(StubSearcher({"q": ["a"]}), golden)


def test_example_golden_file_parses() -> None:
    golden = GOLDEN_FILE.validate_json((FIXTURES / "golden_example.json").read_bytes())
    assert len(golden) == 2
    assert all(item.expected_any_of for item in golden)


def test_env_file_parsing_and_precedence(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nOBJECT_STORE_BUCKET='file-bucket'\nexport STRATEGY_KB_PREFIX=custom\nnot a pair\n",
        encoding="utf-8",
    )
    assert parse_env_file(env_file) == {"OBJECT_STORE_BUCKET": "file-bucket", "STRATEGY_KB_PREFIX": "custom"}
    settings = load_settings({"OBJECT_STORE_BUCKET": "env-bucket"}, env_file)
    assert settings.object_store_values["OBJECT_STORE_BUCKET"] == "env-bucket"
    assert settings.prefix == "custom/"
    assert settings.embedding_model == "all-MiniLM-L6-v2"


def test_settings_never_reveal_secrets(tmp_path: Path) -> None:
    environment = {
        "OBJECT_STORE_ENDPOINT_URL": "https://bucket.example",
        "OBJECT_STORE_BUCKET": "bucket",
        "OBJECT_STORE_REGION": "auto",
        "OBJECT_STORE_ACCESS_KEY_ID": "key-id-value",
        "OBJECT_STORE_SECRET_ACCESS_KEY": "secret-value",
    }
    settings = load_settings(environment, tmp_path / "absent.env")
    assert "secret-value" not in repr(settings)
    assert "key-id-value" not in repr(settings)
    object_store = settings.object_store()
    assert "secret-value" not in repr(object_store)
    assert object_store.secret_access_key.get_secret_value() == "secret-value"


def test_the_candidate_pool_is_configurable(tmp_path: Path) -> None:
    absent = tmp_path / "absent.env"
    assert load_settings({}, absent).candidate_pool == DEFAULT_CANDIDATE_POOL
    assert load_settings({"STRATEGY_KB_CANDIDATE_POOL": "120"}, absent).candidate_pool == 120
    assert load_settings({"STRATEGY_KB_CANDIDATE_POOL": "zero"}, absent).candidate_pool == DEFAULT_CANDIDATE_POOL
    assert load_settings({"STRATEGY_KB_CANDIDATE_POOL": "0"}, absent).candidate_pool == DEFAULT_CANDIDATE_POOL


def test_missing_object_store_variables_are_named(tmp_path: Path) -> None:
    settings = load_settings({"OBJECT_STORE_BUCKET": "bucket"}, tmp_path / "absent.env")
    with pytest.raises(ObjectStoreNotConfiguredError, match="OBJECT_STORE_SECRET_ACCESS_KEY"):
        settings.object_store()


def test_expected_ids_resolve_through_the_alias_map() -> None:
    golden = GOLDEN_FILE.validate_python([{"query": "q", "expected_any_of": ["merged-away-id"], "k": 3}])
    searcher = StubSearcher({"q": ["other", "canonical-id", "third"]})
    assert evaluate(searcher, golden)["hit_at_k"] == 0.0
    report = evaluate(searcher, golden, {"merged-away-id": "canonical-id"})
    assert report["hit_at_k"] == 1.0
    assert report["per_query"][0]["first_hit_rank"] == 2
    assert report["per_query"][0]["expected_canonical"] == ["canonical-id"]


def test_golden_query_kind_defaults_to_golden_and_accepts_the_new_kinds() -> None:
    assert GOLDEN_FILE.validate_python([{"query": "q", "expected_any_of": ["a"]}])[0].kind == "golden"
    for kind in ("paraphrase", "lay", "contrast", "family", "agent", "findings", "passages"):
        item = GOLDEN_FILE.validate_python([{"query": "q", "expected_any_of": ["a"], "kind": kind}])[0]
        assert item.kind == kind
    with pytest.raises(ValueError, match="kind"):
        GOLDEN_FILE.validate_python([{"query": "q", "expected_any_of": ["a"], "kind": "not_a_kind"}])


def test_forbidden_hit_within_top_3() -> None:
    assert forbidden_hit(["a", "b", "c", "d"], ["d"], k=3) is None
    assert forbidden_hit(["a", "b", "c", "d"], ["c"], k=3) == "c"
    assert forbidden_hit(["a", "b", "c"], [], k=3) is None


def test_evaluate_reports_forbidden_at_3_violations() -> None:
    golden = GOLDEN_FILE.validate_python(
        [
            {"query": "raise", "expected_any_of": ["lime"], "forbidden_at_3": ["sulfur"], "kind": "contrast"},
            {"query": "lower", "expected_any_of": ["sulfur"], "forbidden_at_3": ["lime"], "kind": "contrast"},
        ],
    )
    # "raise" wrongly surfaces the forbidden "sulfur" in the top 3; "lower" does not.
    searcher = StubSearcher({"raise": ["sulfur", "lime"], "lower": ["sulfur", "gypsum"]})
    report = evaluate(searcher, golden)
    assert report["per_query"][0]["forbidden_violation"] == "sulfur"
    assert report["per_query"][1]["forbidden_violation"] is None
    assert report["forbidden_at_3_violation_rate"] == 0.5


def test_evaluate_forbidden_at_3_rate_is_zero_when_nothing_configures_it() -> None:
    golden = GOLDEN_FILE.validate_python([{"query": "q", "expected_any_of": ["a"]}])
    assert evaluate(StubSearcher({"q": ["a"]}), golden)["forbidden_at_3_violation_rate"] == 0.0


def test_forbidden_at_3_is_checked_past_a_small_k() -> None:
    # A contrast item with k=1 must still see rank 3: the request now asks for max(k, FORBIDDEN_TOP_K) results
    # so a forbidden id just past a small k is not silently missed (AGENTS.md "Evaluation").
    golden = GOLDEN_FILE.validate_python(
        [{"query": "raise", "expected_any_of": ["lime"], "forbidden_at_3": ["sulfur"], "k": 1}],
    )
    searcher = StubSearcher({"raise": ["other", "another", "sulfur", "lime"]})
    report = evaluate(searcher, golden)
    assert searcher.requests[0].limit == 3
    assert report["per_query"][0]["forbidden_violation"] == "sulfur"
    assert report["per_query"][0]["top_ids"] == ["other"]


def test_golden_query_rejects_an_id_that_is_both_expected_and_forbidden() -> None:
    # A 2026-09-27 review found `agent_queries_heldout.json` doing exactly this, which let a wrong-direction
    # top hit score as a hit and a violation at once (AGENTS.md "Evaluation").
    with pytest.raises(ValueError, match="overlap"):
        GOLDEN_FILE.validate_python(
            [{"query": "q", "expected_any_of": ["sulfur"], "forbidden_at_3": ["sulfur"]}],
        )


def test_evaluate_breaks_down_by_kind() -> None:
    golden = GOLDEN_FILE.validate_python(
        [
            {"query": "g1", "expected_any_of": ["a"], "kind": "golden"},
            {"query": "p1", "expected_any_of": ["z"], "kind": "paraphrase"},
        ],
    )
    searcher = StubSearcher({"g1": ["a"], "p1": ["y", "z"]})
    by_kind = evaluate(searcher, golden)["by_kind"]
    assert by_kind["golden"] == {"queries": 1, "hit_at_k": 1.0, "mrr": 1.0, "forbidden_at_3_violation_rate": 0.0}
    assert by_kind["paraphrase"]["mrr"] == pytest.approx(0.5)


def test_evaluate_routes_findings_and_passages_kinds_to_their_own_search() -> None:
    golden = GOLDEN_FILE.validate_python(
        [
            {"query": "fq", "expected_any_of": ["F1"], "kind": "findings"},
            {"query": "pq", "expected_any_of": ["src-1"], "kind": "passages"},
        ],
    )
    searcher = StubSearcher({}, findings={"fq": ["F0", "F1"]}, passages={"pq": ["src-1"]})
    report = evaluate(searcher, golden)
    assert report["per_query"][0]["top_ids"] == ["F0", "F1"]
    assert report["per_query"][0]["hit"] is True
    assert report["per_query"][1]["top_ids"] == ["src-1"]
    assert report["per_query"][1]["hit"] is True
    assert searcher.requests == []  # neither routed through search_strategies


def test_evaluate_family_coverage_reports_tested_and_untested_families() -> None:
    golden = GOLDEN_FILE.validate_python([{"query": "q", "expected_any_of": ["lime-strategy"]}])
    searcher = StubSearcher(
        {"q": ["lime-strategy"]},
        strategies={"lime-strategy": "soil-chemistry-correction", "other-strategy": "cover-cropping"},
    )
    coverage = evaluate(searcher, golden)["family_coverage"]
    assert coverage["tested_families"] == ["soil-chemistry-correction"]
    assert coverage["untested_families"] == ["cover-cropping"]
    assert coverage["per_family_hit_rate"] == {"soil-chemistry-correction": 1.0}


def test_evaluate_family_coverage_is_none_without_a_registry() -> None:
    golden = GOLDEN_FILE.validate_python([{"query": "q", "expected_any_of": ["a"]}])
    assert evaluate(StubSearcher({"q": ["a"]}), golden)["family_coverage"] is None


def test_context_query_is_forwarded_now_that_the_request_model_carries_the_field() -> None:
    golden = GOLDEN_FILE.validate_python(
        [{"query": "q", "expected_any_of": ["a"], "context_query": "the user's verbatim question"}],
    )
    searcher = StubSearcher({"q": ["a"]})
    report = evaluate(searcher, golden)
    # StrategySearch carries context_query (contract seam S3): forwarded onto the request the searcher sees.
    assert "context_query" in StrategySearch.model_fields
    assert report["per_query"][0]["context_query_sent"] is True
    assert searcher.requests[0].context_query == "the user's verbatim question"


def test_context_query_is_recorded_but_dropped_for_a_request_model_without_the_field() -> None:
    # `PassageSearch` never carries `context_query` (contract S3 covers only strategies/findings): this
    # exercises evaluation.py's graceful-degradation path with a real request model, not a hypothetical one.
    assert "context_query" not in PassageSearch.model_fields
    golden = GOLDEN_FILE.validate_python(
        [
            {
                "query": "pq",
                "expected_any_of": ["src-1"],
                "kind": "passages",
                "context_query": "the user's verbatim question",
            },
        ],
    )
    searcher = StubSearcher({}, passages={"pq": ["src-1"]})
    report = evaluate(searcher, golden)
    assert report["per_query"][0]["context_query_sent"] is False
