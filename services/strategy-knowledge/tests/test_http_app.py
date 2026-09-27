"""The HTTP transport: health, readiness, the tool route against MCP, refusals, /mcp, pull-first and serve flags."""

from pathlib import Path
from typing import Any, Final

import anyio
import pytest
from conftest import SOURCE_ID, HashingEmbedder
from mcp import Client
from starlette.applications import Starlette
from starlette.testclient import TestClient

from strategy_knowledge import cli, server
from strategy_knowledge.config import Settings, load_settings
from strategy_knowledge.corpus import CorpusStore
from strategy_knowledge.http_app import (
    MAXIMUM_REQUEST_BYTES,
    MCP_PATH,
    RAILWAY_SERVICE_HOST,
    build_http_app,
    transport_security,
)
from strategy_knowledge.index import Indexer
from strategy_knowledge.knowledge_base import KnowledgeBase
from strategy_knowledge.server import ServingRefusedError, build_server, build_service, load_serving_knowledge_base
from strategy_knowledge.storage import SyncReport
from strategy_knowledge.vocabulary import CLAIM_TIER

#: One call of each tool, in registration order (the order `unknown_tool` lists them in).
TOOL_CALLS: Final[dict[str, dict[str, Any]]] = {
    "list_facets": {},
    "search_strategies": {"query": "straw mulch on burned slopes", "site_profile": {"slope_pct": 40}},
    "get_strategy": {"ids": ["post-fire-straw-mulching"]},
    "get_family": {"family_id": "post-fire-mulching"},
    "compare_strategies": {"ids": ["post-fire-straw-mulching", "grass-cover-cropping"]},
    "search_findings": {"query": "sediment yield", "direction": "decrease"},
    "search_passages": {"query": "cereal rye winter cover"},
    "list_sources": {},
}
STRAW: Final = "post-fire-straw-mulching"
STRAW_ALIAS: Final = "post-fire-straw-or-hay-mulching"
JSON_BODY: Final = {"content-type": "application/json"}
MCP_ACCEPT: Final = {"accept": "application/json, text/event-stream"}
INITIALIZE: Final = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "http-test", "version": "0"},
    },
}
PRIVATE_HOST: Final = f"{RAILWAY_SERVICE_HOST}:8000"


def _app(knowledge_base: KnowledgeBase) -> Starlette:
    return build_http_app(build_service(lambda: knowledge_base))


def _mcp_texts(knowledge_base: KnowledgeBase) -> dict[str, str]:
    """Each tool's text content over the SDK's in-memory MCP session (as tests/test_server.py drives it)."""

    async def session() -> dict[str, str]:
        async with Client(build_server(lambda: knowledge_base)) as client:
            results = {name: await client.call_tool(name, arguments) for name, arguments in TOOL_CALLS.items()}
        for name, result in results.items():
            assert not result.is_error, (name, result.content)
        return {name: result.content[0].text for name, result in results.items()}

    return anyio.run(session)


def test_every_tool_answers_over_http_byte_for_byte_as_over_mcp(knowledge_base: KnowledgeBase) -> None:
    expected = _mcp_texts(knowledge_base)
    with TestClient(_app(knowledge_base)) as client:
        responses = {name: client.post(f"/v1/tools/{name}", json=arguments) for name, arguments in TOOL_CALLS.items()}
    for name, response in responses.items():
        assert response.status_code == 200, (name, response.text)
        assert response.headers["content-type"].startswith("application/json")
        assert response.content == expected[name].encode("utf-8"), name
        assert response.json()["claim_tier"] == CLAIM_TIER
    assert responses["search_strategies"].json()["results"]


def test_health_is_always_up_and_ready_waits_for_the_knowledge_base(knowledge_base: KnowledgeBase) -> None:
    client = TestClient(_app(knowledge_base))
    health = client.get("/health")
    ready_before = client.get("/ready")
    tool_before = client.post("/v1/tools/list_facets", json={})
    with client:
        ready_after = client.get("/ready")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready_before.status_code == 503
    assert ready_before.json()["status"] == "not_ready"
    assert ready_before.json()["reason"]
    assert tool_before.status_code == 503
    assert tool_before.json() == {"error": "not_ready"}
    assert ready_after.status_code == 200
    assert ready_after.json() == {
        "status": "ready",
        "corpus_version": knowledge_base.index_corpus_version,
        "index_is_stale": False,
    }


def test_a_stale_index_is_not_ready_yet_still_answers(fixture_store: CorpusStore, embedder: HashingEmbedder) -> None:
    indexer = Indexer(fixture_store, embedder)
    indexer.rebuild_all()
    indexer.update(SOURCE_ID)
    with TestClient(_app(KnowledgeBase.open(fixture_store, embedder))) as client:
        ready = client.get("/ready")
        facets = client.post("/v1/tools/list_facets", json={})
    assert ready.status_code == 503
    assert "partial" in ready.json()["reason"]
    assert facets.status_code == 200
    assert facets.json()["index_is_stale"] is True


@pytest.mark.parametrize(
    ("tool", "body", "fragment"),
    [
        ("search_strategies", {"query": ""}, "query"),
        ("search_strategies", {"query": "mulch", "limit": 500}, "limit"),
        ("search_strategies", {"query": "mulch", "goal": ["soil_health"]}, "unknown argument"),
        ("compare_strategies", {"ids": [STRAW]}, "ids"),
        ("compare_strategies", {"ids": [STRAW, STRAW_ALIAS]}, "distinct"),
        ("search_findings", [1, 2], "JSON object"),
    ],
)
def test_refused_arguments_are_400(knowledge_base: KnowledgeBase, tool: str, body: Any, fragment: str) -> None:
    with TestClient(_app(knowledge_base)) as client:
        response = client.post(f"/v1/tools/{tool}", json=body)
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "invalid_arguments"
    assert fragment in response.json()["detail"]


def test_unknown_tools_malformed_json_and_oversized_bodies(knowledge_base: KnowledgeBase) -> None:
    oversized = b" " * (MAXIMUM_REQUEST_BYTES + 1)
    streamed = iter((b" " * MAXIMUM_REQUEST_BYTES, b"  "))  # no Content-Length: the size is counted while reading
    with TestClient(_app(knowledge_base)) as client:
        unknown = client.post("/v1/tools/drop_everything", json={})
        malformed = client.post("/v1/tools/search_strategies", content=b"{not json", headers=JSON_BODY)
        declared = client.post("/v1/tools/search_strategies", content=oversized, headers=JSON_BODY)
        chunked = client.post("/v1/tools/search_strategies", content=streamed, headers=JSON_BODY)
    assert unknown.status_code == 404
    assert unknown.json() == {"error": "unknown_tool", "tools": list(TOOL_CALLS)}
    assert malformed.status_code == 400
    assert "not JSON" in malformed.json()["detail"]
    for response in (declared, chunked):
        assert response.status_code == 413
        assert response.json() == {"error": "request_too_large", "limit_bytes": MAXIMUM_REQUEST_BYTES}


def test_an_unexpected_failure_is_a_json_500_without_the_traceback(
    knowledge_base: KnowledgeBase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(lambda: knowledge_base)

    def explode() -> dict[str, Any]:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(service.tools.tools["list_facets"], "fn", explode)
    with TestClient(build_http_app(service)) as client:
        response = client.post("/v1/tools/list_facets", json={})
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error"}


def test_the_mcp_endpoint_is_mounted_behind_host_validation(knowledge_base: KnowledgeBase) -> None:
    with TestClient(_app(knowledge_base)) as client:
        loopback = client.post(MCP_PATH, json=INITIALIZE, headers={**MCP_ACCEPT, "host": "127.0.0.1:8000"})
        private = client.post(MCP_PATH, json=INITIALIZE, headers={**MCP_ACCEPT, "host": PRIVATE_HOST})
        rebound = client.post(MCP_PATH, json=INITIALIZE, headers={**MCP_ACCEPT, "host": "rebound.example"})
    assert loopback.status_code == 200, loopback.text
    assert loopback.json()["result"]["serverInfo"]["name"] == "strategy-knowledge"
    assert private.status_code == 200, private.text
    assert rebound.status_code == 421


def test_mcp_hosts_are_loopback_the_railway_host_and_configured_ones(tmp_path: Path) -> None:
    absent = tmp_path / "absent.env"
    environment = {
        "RAILWAY_PRIVATE_DOMAIN": "renamed.railway.internal",
        "STRATEGY_KB_ALLOWED_HOSTS": " extra.internal , ",
    }
    settings = load_settings(environment, absent)
    assert settings.allowed_hosts == ("renamed.railway.internal", "extra.internal")
    assert load_settings({}, absent).allowed_hosts == ()
    allowed = transport_security(settings.allowed_hosts).allowed_hosts
    for host in ("127.0.0.1", "localhost", "[::1]", RAILWAY_SERVICE_HOST, *settings.allowed_hosts):
        assert host in allowed
        assert f"{host}:*" in allowed


class FakeBucketSync:
    """Stands in for `BucketSync` (constructor and pull): records each pull, returns a canned report."""

    def __init__(self, report: SyncReport) -> None:
        self.report = report
        self.pulls: list[bool] = []

    def __call__(self, _settings: Settings, _store: CorpusStore) -> "FakeBucketSync":
        return self

    def pull(self, *, with_index: bool = False) -> SyncReport:
        self.pulls.append(with_index)
        return self.report


def _serving_settings(root: Path) -> Settings:
    return Settings(cache_dir=root, prefix="strategy-knowledge/", embedding_model="hashing", object_store_values={})


def test_pull_from_bucket_serves_only_a_full_fresh_index(
    fixture_store: CorpusStore,
    embedder: HashingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "embedder_for", lambda _model: embedder)
    bucket = FakeBucketSync(SyncReport())
    monkeypatch.setattr(server, "BucketSync", bucket)
    settings = _serving_settings(fixture_store.root)
    with pytest.raises(ServingRefusedError, match="no complete index"):
        load_serving_knowledge_base(settings, pull_from_bucket=True)
    indexer = Indexer(fixture_store, embedder)
    indexer.rebuild_all()
    assert load_serving_knowledge_base(settings, pull_from_bucket=True).index_staleness() == []
    assert load_serving_knowledge_base(settings).index_staleness() == []
    indexer.update(SOURCE_ID)
    with pytest.raises(ServingRefusedError, match="not full and fresh"):
        load_serving_knowledge_base(settings, pull_from_bucket=True)
    assert bucket.pulls == [True, True, True]


def test_a_failed_pull_refuses_to_serve(
    fixture_store: CorpusStore,
    embedder: HashingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Indexer(fixture_store, embedder).rebuild_all()
    monkeypatch.setattr(server, "embedder_for", lambda _model: embedder)
    monkeypatch.setattr(server, "BucketSync", FakeBucketSync(SyncReport(rejected_keys=["index/LATEST"])))
    with pytest.raises(ServingRefusedError, match="bucket pull failed"):
        load_serving_knowledge_base(_serving_settings(fixture_store.root), pull_from_bucket=True)


def test_serve_defaults_to_stdio_and_parses_the_http_flags() -> None:
    parser = cli.build_parser()
    stdio = parser.parse_args(["serve"])
    http_flags = ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "9100", "--pull-from-bucket"]
    http = parser.parse_args(http_flags)
    assert (stdio.transport, stdio.pull_from_bucket, stdio.handler) == ("stdio", False, cli.command_serve)
    assert (http.transport, http.host, http.port, http.pull_from_bucket) == ("http", "0.0.0.0", 9100, True)
    with pytest.raises(SystemExit):
        parser.parse_args(["serve", "--transport", "sse"])


def test_serve_dispatches_on_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(cli, "serve", lambda _settings, **options: calls.append(("stdio", options)))
    monkeypatch.setattr(cli, "serve_http", lambda _settings, **options: calls.append(("http", options)))
    assert cli.main(["serve"]) == cli.EXIT_OK
    assert cli.main(["serve", "--transport", "http", "--port", "9100", "--pull-from-bucket"]) == cli.EXIT_OK
    assert calls == [
        ("stdio", {"pull_from_bucket": False}),
        ("http", {"host": "127.0.0.1", "port": 9100, "pull_from_bucket": True}),
    ]


def test_a_lifespan_startup_failure_never_serves() -> None:
    """A failing loader (e.g. `--pull-from-bucket` refusing) fails ASGI startup instead of answering any route."""

    def failing_loader() -> KnowledgeBase:
        raise ServingRefusedError("bucket pull failed: rejected keys ['index/LATEST']")

    with (
        pytest.raises(ServingRefusedError, match="bucket pull failed"),
        TestClient(build_http_app(build_service(failing_loader))),
    ):
        pass


def test_serve_refusal_over_stdio_exits_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """`serve` (stdio, the default transport) turns a `ServingRefusedError` into `refused: ...` and exit 2, wrapped
    exactly how it really arrives: `stdio_server()` runs the lifespan inside its own anyio task group, so anyio
    (4.15) delivers the refusal inside an `ExceptionGroup`, not bare (a plain `except ServingRefusedError` in
    `main()` never matches that).
    """

    def raise_wrapped_refusal(_settings: Settings, **_options: Any) -> None:
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [ServingRefusedError("no complete index after the pull")],
        )

    monkeypatch.setattr(cli, "serve", raise_wrapped_refusal)
    assert cli.main(["serve"]) == cli.EXIT_REFUSED


def test_serve_refusal_wrapped_with_an_unrelated_error_still_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A task-group failure that mixes a refusal with an unrelated error must not be swallowed as a clean refusal."""

    def raise_mixed_group(_settings: Settings, **_options: Any) -> None:
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [ServingRefusedError("no complete index after the pull"), RuntimeError("boom")],
        )

    monkeypatch.setattr(cli, "serve", raise_mixed_group)
    with pytest.raises(BaseExceptionGroup) as excinfo:
        cli.main(["serve"])
    assert excinfo.group_contains(RuntimeError, match="boom")
