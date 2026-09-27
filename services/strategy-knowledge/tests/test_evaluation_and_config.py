"""hit@k / MRR arithmetic, the golden-file format, and settings that never reveal secrets."""

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
from strategy_knowledge.evaluation import GOLDEN_FILE, evaluate, reciprocal_rank
from strategy_knowledge.queries import StrategySearch


class StubSearcher:
    """Returns a fixed ranking per query and records the requests it saw."""

    def __init__(self, rankings: dict[str, list[str]]) -> None:
        self.rankings = rankings
        self.requests: list[StrategySearch] = []

    def search_strategies(self, request: StrategySearch) -> dict[str, Any]:
        """Ranked ids for the query, truncated to the request's limit."""
        self.requests.append(request)
        ranked = self.rankings[request.query][: request.limit]
        return {"results": [{"strategy_id": identifier} for identifier in ranked]}


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
