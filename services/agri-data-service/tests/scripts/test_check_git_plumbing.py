"""Real-repository tests for check.py's git plumbing: `_git_output`, `_listed_paths`,
`_staged_blob_ids`, `_read_blobs` and `_git_index_snapshot`.

Every test elsewhere in `tests/scripts/` replaces `index_reader=` with a hermetic double, so this
plumbing -- the thing that actually shells out to git and parses its `-z`-separated, batch-header
output -- was exercised by nothing. This is the ONE module allowed to shell out to a real `git`: each
test builds a throwaway repository fresh under `tmp_path`, isolated from this checkout's own history
and config, and asserts against what the real index reports.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: `check.py` does `from quality_receipt import ...`, which only resolves because loading
#: `quality_receipt` first registers it in `sys.modules` under its real name -- `scripts/` is never
#: added to `sys.path`. Loading `CHECK` before this line reproducibly raises `ModuleNotFoundError`.
QUALITY_RECEIPT = load_scripts_module("quality_receipt.py", "quality_receipt")
CHECK = load_scripts_module("check.py", "plantgeo_check_module")

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="these tests need a real git executable")

MODULE_RELATIVE_PATH = "src/module.py"
MODULE_CONTENT = b"value = 1\n"
SPACE_RELATIVE_PATH = "src/with space.py"
SPACE_CONTENT = b"space = True\n"
CRLF_RELATIVE_PATH = "src/crlf_file.py"
CRLF_DISK_CONTENT = b"crlf = True\r\n"
CRLF_STAGED_CONTENT = b"crlf = True\n"
BINARY_RELATIVE_PATH = "src/binary_with_nul.py"
BINARY_CONTENT = b"alpha\n\x00\x00binary\x00middle\ndelta\n"
TEST_RELATIVE_PATH = "tests/test_x.py"
TEST_CONTENT = b"def test_x() -> None:\n    assert True\n"
PYPROJECT_RELATIVE_PATH = "pyproject.toml"
PYPROJECT_CONTENT = b'[project]\nname = "throwaway-fixture"\nversion = "0.0.0"\n'
UV_LOCK_RELATIVE_PATH = "uv.lock"
UV_LOCK_CONTENT = b"version = 1\n"
IGNORED_RELATIVE_PATH = "src/ignored_by_git.py"
IGNORED_CONTENT = b"ignored = True\n"

#: Every file `_write_baseline_tree` writes, keyed to what a clean `_git_index_snapshot` must return.
BASELINE_DIGEST_INPUTS: tuple[tuple[str, bytes], ...] = (
    (MODULE_RELATIVE_PATH, MODULE_CONTENT),
    (SPACE_RELATIVE_PATH, SPACE_CONTENT),
    (CRLF_RELATIVE_PATH, CRLF_DISK_CONTENT),
    (BINARY_RELATIVE_PATH, BINARY_CONTENT),
    (TEST_RELATIVE_PATH, TEST_CONTENT),
    (PYPROJECT_RELATIVE_PATH, PYPROJECT_CONTENT),
    (UV_LOCK_RELATIVE_PATH, UV_LOCK_CONTENT),
)


def _run_git(arguments: Sequence[str], repository_root: Path) -> None:
    """Run one git command inside the throwaway repository, failing loudly if it errors."""
    subprocess.run(["git", *arguments], cwd=repository_root, check=True, capture_output=True)


def _init_repository(root: Path) -> None:
    """Create a fresh repository with a deterministic local identity, isolated from the real one."""
    _run_git(["init", "-q"], root)
    _run_git(["config", "user.email", "quality-receipt-tests@example.invalid"], root)
    _run_git(["config", "user.name", "Quality Receipt Tests"], root)
    _run_git(["config", "core.autocrlf", "false"], root)


def _write_baseline_tree(root: Path) -> None:
    """Write the minimal service tree the digest covers, plus the CRLF/binary/space edge cases.

    `.gitattributes`' `* text=auto eol=lf` mirrors the real repo root (see scripts/AGENTS.md, "Locked
    quality receipt"): it is what normalizes `CRLF_RELATIVE_PATH`'s on-disk CRLF to LF on `git add`,
    so the staged blob and the disk bytes deliberately disagree until `normalize_content` reconciles
    them.
    """
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
    for relative, content in BASELINE_DIGEST_INPUTS:
        (root / relative).write_bytes(content)


def _stage_baseline_tree(root: Path) -> None:
    """Build the baseline tree in a fresh repository and stage every file in one commit-free add."""
    _init_repository(root)
    _write_baseline_tree(root)
    _run_git(["add", "-A"], root)


def test_index_snapshot_reads_every_digest_input_at_its_staged_bytes(tmp_path: Path) -> None:
    """The full read chain -- `ls-files -s -z` then one `cat-file --batch` -- for a clean stage."""
    _stage_baseline_tree(tmp_path)

    snapshot = CHECK._git_index_snapshot(tmp_path)

    assert set(snapshot.blobs) == {relative for relative, _content in BASELINE_DIGEST_INPUTS}
    assert snapshot.blobs[MODULE_RELATIVE_PATH] == MODULE_CONTENT
    assert snapshot.blobs[SPACE_RELATIVE_PATH] == SPACE_CONTENT
    assert snapshot.blobs[BINARY_RELATIVE_PATH] == BINARY_CONTENT
    assert snapshot.untracked == ()
    assert snapshot.ignored == ()


def test_index_snapshot_normalizes_crlf_to_lf_on_add(tmp_path: Path) -> None:
    """`* text=auto eol=lf` normalizes the staged blob; the disk file is left carrying CRLF."""
    _stage_baseline_tree(tmp_path)

    snapshot = CHECK._git_index_snapshot(tmp_path)

    assert snapshot.blobs[CRLF_RELATIVE_PATH] == CRLF_STAGED_CONTENT
    assert b"\r" not in snapshot.blobs[CRLF_RELATIVE_PATH]
    assert (tmp_path / CRLF_RELATIVE_PATH).read_bytes() == CRLF_DISK_CONTENT


def test_compare_tree_to_index_reports_clean_for_a_fully_staged_tree(tmp_path: Path) -> None:
    """A tree that was just `git add -A`'d must reconcile its disk and index digests exactly."""
    _stage_baseline_tree(tmp_path)
    snapshot = CHECK._git_index_snapshot(tmp_path)

    verdict = CHECK.compare_tree_to_index(tmp_path, snapshot)

    assert verdict.is_clean
    assert verdict.disk_digest == verdict.index_digest
    assert verdict.disk_digest == QUALITY_RECEIPT.compute_tree_digest(tmp_path)


def test_read_blobs_round_trips_binary_content_with_embedded_nul_bytes(tmp_path: Path) -> None:
    """`_read_blobs` must slice each record by the batch header's byte length, not a newline scan."""
    _stage_baseline_tree(tmp_path)

    blob_ids = CHECK._staged_blob_ids(tmp_path)
    contents = CHECK._read_blobs(blob_ids, tmp_path)

    assert contents[BINARY_RELATIVE_PATH] == BINARY_CONTENT


def test_staged_and_listed_paths_handle_a_space_in_the_file_name(tmp_path: Path) -> None:
    """`-z`-separated records must not split or truncate a path that itself contains a space."""
    _stage_baseline_tree(tmp_path)

    blob_ids = CHECK._staged_blob_ids(tmp_path)
    assert blob_ids[SPACE_RELATIVE_PATH]

    (tmp_path / "src" / "another name.py").write_bytes(b"untracked = True\n")
    untracked = CHECK._listed_paths(("--others", "--exclude-standard"), tmp_path)

    assert "src/another name.py" in untracked


def test_compare_tree_to_index_flags_an_untracked_digest_input(tmp_path: Path) -> None:
    """A digest input git never staged must surface as untracked, not silently pass as clean."""
    _stage_baseline_tree(tmp_path)
    (tmp_path / "src" / "new_untracked.py").write_bytes(b"new = True\n")

    verdict = CHECK.compare_tree_to_index(tmp_path, CHECK._git_index_snapshot(tmp_path))

    assert not verdict.is_clean
    assert "src/new_untracked.py" in verdict.untracked


def test_compare_tree_to_index_flags_an_unstaged_edit(tmp_path: Path) -> None:
    """A disk edit made after `git add` must surface as modified against the staged blob."""
    _stage_baseline_tree(tmp_path)
    (tmp_path / MODULE_RELATIVE_PATH).write_bytes(b"value = 2\n")

    verdict = CHECK.compare_tree_to_index(tmp_path, CHECK._git_index_snapshot(tmp_path))

    assert not verdict.is_clean
    assert MODULE_RELATIVE_PATH in verdict.modified


def test_compare_tree_to_index_flags_an_ignored_digest_input(tmp_path: Path) -> None:
    """A digest input excluded by `.gitignore` can never be committed, so it must surface distinctly."""
    _init_repository(tmp_path)
    _write_baseline_tree(tmp_path)
    (tmp_path / ".gitignore").write_bytes(f"{IGNORED_RELATIVE_PATH}\n".encode())
    (tmp_path / IGNORED_RELATIVE_PATH).write_bytes(IGNORED_CONTENT)
    _run_git(["add", "-A"], tmp_path)

    verdict = CHECK.compare_tree_to_index(tmp_path, CHECK._git_index_snapshot(tmp_path))

    assert not verdict.is_clean
    assert IGNORED_RELATIVE_PATH in verdict.ignored
    assert IGNORED_RELATIVE_PATH not in verdict.untracked


def test_compare_tree_to_index_flags_a_staged_file_deleted_from_disk(tmp_path: Path) -> None:
    """A file git still has staged but that vanished from disk must surface as missing, not silent."""
    _stage_baseline_tree(tmp_path)
    (tmp_path / MODULE_RELATIVE_PATH).unlink()

    verdict = CHECK.compare_tree_to_index(tmp_path, CHECK._git_index_snapshot(tmp_path))

    assert not verdict.is_clean
    assert MODULE_RELATIVE_PATH in verdict.missing_from_disk
