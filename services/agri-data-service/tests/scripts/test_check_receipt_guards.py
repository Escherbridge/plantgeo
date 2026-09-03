"""Refusal tests for `check.py --write-receipt`, the writer half of the quality-receipt gate.

The writer promises something the verifier cannot check on its own: that the digest it records
describes the bytes git stores, taken from a tree that did not move while the sweep ran. Every
guard below is hermetic -- the check runner and the git index reader are injected, so no test
starts a subprocess, needs a repository, or waits three minutes for a real sweep.
"""

from __future__ import annotations

import functools
import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path

QUALITY_RECEIPT = load_scripts_module("quality_receipt.py", "quality_receipt")
CHECK = load_scripts_module("check.py", "plantgeo_check_module")
VERIFY = load_scripts_module("verify_quality_receipt.py", "plantgeo_verify_quality_receipt_module")

SOURCE_RELATIVE_PATH = "src/module.py"


def _tree_with_one_source(root: Path, content: bytes = b"value = 1\n") -> None:
    """Create the smallest tree the digest covers: one file under src/."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / SOURCE_RELATIVE_PATH).write_bytes(content)


def _stub_tooling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the writer believe uv is on PATH without any test starting a process."""
    monkeypatch.setattr(CHECK.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(CHECK, "_tool_version", lambda tool, _uv_path: f"{tool} 0.0.0-test")
    monkeypatch.setattr(CHECK, "_uv_version", lambda _uv_path: "uv 0.0.0-test")


def _green_runner(check: Any, _uv_path: str) -> Any:
    """Report every check as passing without running anything."""
    return CHECK.CheckResult(name=check.name, returncode=0, duration_seconds=0.01, output="")


def _red_runner(check: Any, _uv_path: str) -> Any:
    """Report the pytest gate as failing, every other gate as passing."""
    failed = check.name == "pytest"
    return CHECK.CheckResult(
        name=check.name,
        returncode=1 if failed else 0,
        duration_seconds=0.01,
        output="1 failed, 0 passed\n" if failed else "",
    )


def _forbidden_runner(check: Any, uv_path: str) -> Any:
    """Fail loudly: this sweep must be refused before any check is allowed to start."""
    message = f"the sweep must not start: {check.name} was run with {uv_path}"
    raise AssertionError(message)


def _runner_writing_a_new_input(root: Path, check: Any, uv_path: str) -> Any:
    """Create a digest input while the sweep runs -- exactly the drift the guard must catch."""
    (root / "src" / f"{check.name}_artifact.py").write_bytes(b"written = True\n")
    return _green_runner(check, uv_path)


def _snapshot_matching(root: Path, *, untracked: tuple[str, ...] = (), ignored: tuple[str, ...] = ()) -> Any:
    """Build the snapshot git returns for a fully staged tree, minus whatever it is said to withhold."""
    withheld = set(untracked) | set(ignored)
    blobs = {
        relative: (root / relative).read_bytes()
        for relative in (path.relative_to(root).as_posix() for path in QUALITY_RECEIPT.digest_input_paths(root))
        if relative not in withheld
    }
    return CHECK.IndexSnapshot(blobs=blobs, untracked=untracked, ignored=ignored)


def _reader_returning(snapshot: Any) -> Any:
    """Return an index reader that answers with one prepared snapshot."""
    return lambda _service_root: snapshot


def _reader_raising(message: str) -> Any:
    """Return an index reader that fails the way an absent git or a bare directory fails."""

    def read(_service_root: Path) -> Any:
        raise CHECK.GitQueryError(message)

    return read


def _receipt_path(root: Path) -> Path:
    """Return where the writer must put the receipt for one service root."""
    return root / QUALITY_RECEIPT.RECEIPT_FILE_NAME


def test_write_receipt_refuses_a_partial_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--only` judges part of the tree, so it may not certify all of it -- and must refuse early."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)

    exit_code = CHECK.main(
        ["--write-receipt", "--only", "lint"],
        service_root=tmp_path,
        check_runner=_forbidden_runner,
        index_reader=_reader_returning(None),
    )

    assert exit_code == 1
    assert "requires every check" in capsys.readouterr().out
    assert not _receipt_path(tmp_path).exists()


def test_write_receipt_refuses_a_red_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A receipt records that every gate passed, so one failing gate must leave no receipt."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_red_runner,
        index_reader=_reader_returning(_snapshot_matching(tmp_path)),
    )

    assert exit_code == 1
    assert "the sweep was not green" in capsys.readouterr().out
    assert not _receipt_path(tmp_path).exists()


def test_write_receipt_refuses_a_tree_that_changed_during_the_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file written during the three-minute sweep would otherwise be certified untested."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=functools.partial(_runner_writing_a_new_input, tmp_path),
        index_reader=_reader_returning(_snapshot_matching(tmp_path)),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "changed while the sweep ran" in output
    assert "before: sha256:" in output
    assert not _receipt_path(tmp_path).exists()


def test_write_receipt_refuses_an_untracked_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An untracked input is in the author's digest and in no checkout: the 2026-09-03 failure."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)
    snapshot = _snapshot_matching(tmp_path, untracked=(SOURCE_RELATIVE_PATH,))

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_green_runner,
        index_reader=_reader_returning(snapshot),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "untracked, so a fresh checkout would not have it" in output
    assert f"    {SOURCE_RELATIVE_PATH}" in output
    assert "git add <path>" in output
    assert not _receipt_path(tmp_path).exists()


def test_write_receipt_refuses_an_ignored_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An ignored input can never be committed, so re-running the sweep would never fix it."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)
    snapshot = _snapshot_matching(tmp_path, untracked=(SOURCE_RELATIVE_PATH,), ignored=(SOURCE_RELATIVE_PATH,))

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_green_runner,
        index_reader=_reader_returning(snapshot),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "excluded by an ignore rule" in output
    assert "untracked, so a fresh checkout" not in output
    assert not _receipt_path(tmp_path).exists()


def test_write_receipt_refuses_an_unstaged_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The digest must describe the staged bytes, not the ones only this working tree has."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)
    snapshot = CHECK.IndexSnapshot(blobs={SOURCE_RELATIVE_PATH: b"value = 2\n"}, untracked=(), ignored=())

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_green_runner,
        index_reader=_reader_returning(snapshot),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "edited since staging" in output
    assert not _receipt_path(tmp_path).exists()


def test_write_receipt_refuses_an_input_deleted_without_staging_the_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A checkout would still carry the deleted file, so its digest would still include it."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)
    blobs = {SOURCE_RELATIVE_PATH: b"value = 1\n", "src/removed.py": b"gone = True\n"}
    snapshot = CHECK.IndexSnapshot(blobs=blobs, untracked=(), ignored=())

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_green_runner,
        index_reader=_reader_returning(snapshot),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "staged but missing from the working tree" in output
    assert "src/removed.py" in output
    assert not _receipt_path(tmp_path).exists()


def test_write_receipt_refuses_when_git_cannot_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No git, no repository, no receipt: the claim being made is about committed bytes."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_green_runner,
        index_reader=_reader_raising("unable to run git: not found"),
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "unable to run git" in output
    assert "only means something for a committed tree" in output
    assert not _receipt_path(tmp_path).exists()


def test_write_receipt_ignores_paths_outside_the_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git lists hundreds of ignored `__pycache__` entries; none of them is a digest input."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)
    cache_directory = tmp_path / "src" / "__pycache__"
    cache_directory.mkdir(parents=True)
    (cache_directory / "module.cpython-312.pyc").write_bytes(b"not real bytecode")
    snapshot = _snapshot_matching(tmp_path)
    noisy = CHECK.IndexSnapshot(
        blobs=snapshot.blobs,
        untracked=("src/__pycache__/module.cpython-312.pyc",),
        ignored=("src/__pycache__/module.cpython-312.pyc",),
    )

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_green_runner,
        index_reader=_reader_returning(noisy),
    )

    assert exit_code == 0
    assert _receipt_path(tmp_path).is_file()


def test_a_crlf_working_tree_still_matches_an_lf_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Normalization is what lets a Windows checkout certify the bytes git stores with LF."""
    _tree_with_one_source(tmp_path, content=b"value = 1\r\n")
    _stub_tooling(monkeypatch)
    snapshot = CHECK.IndexSnapshot(blobs={SOURCE_RELATIVE_PATH: b"value = 1\n"}, untracked=(), ignored=())

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_green_runner,
        index_reader=_reader_returning(snapshot),
    )

    assert exit_code == 0
    assert _receipt_path(tmp_path).is_file()


def test_a_green_committed_tree_gets_a_receipt_the_verifier_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole gate, end to end: the writer's receipt is exactly what the image build verifies."""
    _tree_with_one_source(tmp_path)
    _stub_tooling(monkeypatch)

    exit_code = CHECK.main(
        ["--write-receipt"],
        service_root=tmp_path,
        check_runner=_green_runner,
        index_reader=_reader_returning(_snapshot_matching(tmp_path)),
    )

    assert exit_code == 0
    assert "Wrote QUALITY_RECEIPT.json" in capsys.readouterr().out

    receipt = json.loads(_receipt_path(tmp_path).read_text(encoding="utf-8"))
    assert receipt["schema_version"] == QUALITY_RECEIPT.RECEIPT_SCHEMA_VERSION
    assert receipt["digest_domain"] == QUALITY_RECEIPT.DIGEST_DOMAIN.decode()
    assert receipt["digest_file_count"] == 1
    assert [entry["status"] for entry in receipt["checks"]] == ["pass"] * len(CHECK.CHECKS)
    assert "quality receipt verified" in VERIFY.verify(_receipt_path(tmp_path))


def test_the_index_digest_matches_the_disk_digest_for_a_clean_tree(tmp_path: Path) -> None:
    """The two digests are computed from different sources; a clean tree must reconcile them."""
    _tree_with_one_source(tmp_path, content=b"value = 1\r\n")

    verdict = CHECK.compare_tree_to_index(tmp_path, _snapshot_matching(tmp_path))

    assert verdict.is_clean
    assert verdict.disk_digest == verdict.index_digest
    assert verdict.disk_digest == QUALITY_RECEIPT.compute_tree_digest(tmp_path)


def _fake_ls_files_dash_s_dash_z(records: tuple[tuple[str, str, int, str], ...]) -> bytes:
    """Build the raw bytes `git ls-files -s -z` would print for `(mode, blob id, stage, path)` rows."""
    rendered = (f"{mode} {blob_id} {stage}\t{path}".encode() for mode, blob_id, stage, path in records)
    return b"".join(record + b"\x00" for record in rendered)


def test_staged_blob_ids_refuses_an_unresolved_merge_conflict(tmp_path: Path) -> None:
    """A conflicted path carries stages 1/2/3 and no stage-0 record; the last one seen must not win.

    Feeds `_staged_blob_ids` a fabricated `git ls-files -s -z` listing through its injectable
    `git_output` seam -- the one hermetic way to exercise conflict parsing without a real repository.
    """
    fake_output = _fake_ls_files_dash_s_dash_z(
        (
            ("100644", "1111111111111111111111111111111111111a", 1, "src/conflict.py"),
            ("100644", "2222222222222222222222222222222222222b", 2, "src/conflict.py"),
            ("100644", "3333333333333333333333333333333333333c", 3, "src/conflict.py"),
            ("100644", "4444444444444444444444444444444444444d", 0, SOURCE_RELATIVE_PATH),
        )
    )

    with pytest.raises(CHECK.GitQueryError, match=r"src/conflict\.py"):
        CHECK._staged_blob_ids(tmp_path, git_output=lambda _arguments, _root: fake_output)


def test_staged_blob_ids_resolves_an_ordinary_stage_zero_index(tmp_path: Path) -> None:
    """The happy path through the same injected seam: an unconflicted index still resolves."""
    fake_output = _fake_ls_files_dash_s_dash_z(
        (("100644", "4444444444444444444444444444444444444d", 0, SOURCE_RELATIVE_PATH),)
    )

    blob_ids = CHECK._staged_blob_ids(tmp_path, git_output=lambda _arguments, _root: fake_output)

    assert blob_ids == {SOURCE_RELATIVE_PATH: "4444444444444444444444444444444444444d"}
