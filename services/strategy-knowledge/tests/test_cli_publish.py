"""CLI wiring for the atomic `sync push` publish path: --with-index/--corpus-only mutual exclusion, dispatch."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from strategy_knowledge import cli
from strategy_knowledge.config import Settings
from strategy_knowledge.storage import SyncReport


@dataclass
class FakeSync:
    """Stands in for `BucketSync`: records each pull/push call's keyword arguments, returns a canned report."""

    report: SyncReport
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def pull(self, **kwargs: Any) -> SyncReport:
        self.calls.append(("pull", kwargs))
        return self.report

    def push(self, **kwargs: Any) -> SyncReport:
        self.calls.append(("push", kwargs))
        return self.report


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, report: SyncReport) -> FakeSync:
    """Route `cli.command_sync` at a fake `BucketSync` over a throwaway cache dir; no real bucket is touched."""
    fake = FakeSync(report)
    settings = Settings(
        cache_dir=tmp_path,
        prefix="strategy-knowledge/",
        embedding_model="test",
        object_store_values={},
    )
    monkeypatch.setattr(cli, "load_settings", lambda *_args, **_kwargs: settings)
    monkeypatch.setattr(cli, "BucketSync", lambda *_args, **_kwargs: fake)
    return fake


def test_with_index_and_corpus_only_are_mutually_exclusive() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "push", "--with-index", "--corpus-only"])


def test_push_forwards_with_index_and_corpus_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _install(monkeypatch, tmp_path, SyncReport())
    assert cli.main(["sync", "push", "--corpus-only"]) == cli.EXIT_OK
    assert fake.calls == [("push", {"with_index": False, "corpus_only": True, "on_conflict": "fail"})]


def test_push_with_index_forwards_the_atomic_publish_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _install(monkeypatch, tmp_path, SyncReport())
    assert cli.main(["sync", "push", "--with-index", "--force-local"]) == cli.EXIT_OK
    assert fake.calls == [("push", {"with_index": True, "corpus_only": False, "on_conflict": "local"})]


def test_pull_takes_with_index_but_never_corpus_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _install(monkeypatch, tmp_path, SyncReport())
    assert cli.main(["sync", "pull", "--with-index"]) == cli.EXIT_OK
    assert fake.calls == [("pull", {"with_index": True, "on_conflict": "fail"})]


def test_a_refused_publish_exits_problems_and_prints_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    refused = SyncReport(index_refusals=["the bucket already serves corpus deadbeef, which differs..."])
    _install(monkeypatch, tmp_path, refused)
    assert cli.main(["sync", "push"]) == cli.EXIT_PROBLEMS
    assert "index_refusals" in capsys.readouterr().out


def test_a_clean_publish_exits_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, tmp_path, SyncReport(transferred=["index/abc/chroma.tar.gz", "index/LATEST"]))
    assert cli.main(["sync", "push", "--with-index"]) == cli.EXIT_OK
