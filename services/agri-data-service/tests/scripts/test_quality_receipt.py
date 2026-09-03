"""Regression tests for the quality-receipt tree digest: what it covers and how it hashes.

See `scripts/AGENTS.md`, "Locked quality receipt", for why the digest normalizes line endings
before hashing (a Windows working tree may carry CRLF that git's `* text=auto eol=lf` only
normalizes away on commit) and why the migration and tool-configuration inputs joined it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path

QUALITY_RECEIPT = load_scripts_module("quality_receipt.py", "quality_receipt")


def _write(path: Path, content: bytes) -> None:
    """Create one file, including its parent directories, with exact bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_crlf_and_lf_trees_digest_identically(tmp_path: Path) -> None:
    """Two trees whose only difference is CRLF vs. LF in a src/ file must digest the same."""
    crlf_tree = tmp_path / "crlf"
    lf_tree = tmp_path / "lf"
    _write(crlf_tree / "src" / "module.py", b"def greet() -> str:\r\n    return 'hi'\r\n")
    _write(lf_tree / "src" / "module.py", b"def greet() -> str:\n    return 'hi'\n")

    crlf_digest, crlf_count = QUALITY_RECEIPT.compute_tree_digest(crlf_tree)
    lf_digest, lf_count = QUALITY_RECEIPT.compute_tree_digest(lf_tree)

    assert crlf_digest == lf_digest
    assert crlf_count == lf_count == 1


def test_a_lone_carriage_return_is_not_normalized(tmp_path: Path) -> None:
    """Only CRLF folds to LF. Git leaves a lone CR alone, so the digest must leave it alone too."""
    carriage_return_tree = tmp_path / "carriage-return"
    line_feed_tree = tmp_path / "line-feed"
    _write(carriage_return_tree / "src" / "module.py", b"x\ry")
    _write(line_feed_tree / "src" / "module.py", b"x\ny")

    carriage_return_digest, _ = QUALITY_RECEIPT.compute_tree_digest(carriage_return_tree)
    line_feed_digest, _ = QUALITY_RECEIPT.compute_tree_digest(line_feed_tree)

    assert carriage_return_digest != line_feed_digest


def test_changing_content_changes_the_digest(tmp_path: Path) -> None:
    """Editing one byte of real content must change the digest."""
    tree = tmp_path / "tree"
    target = tree / "src" / "module.py"
    _write(target, b"x = 1\n")
    before, _ = QUALITY_RECEIPT.compute_tree_digest(tree)

    _write(target, b"x = 2\n")
    after, _ = QUALITY_RECEIPT.compute_tree_digest(tree)

    assert before != after


def test_length_prefixes_defeat_a_path_and_content_collision(tmp_path: Path) -> None:
    """The collision the length prefixes exist for: one boundary moved, the same concatenation.

    Without them both trees hash `src/a.pyb.py-DATA`, so a rename would digest as an edit.
    """
    boundary_left = tmp_path / "boundary-left"
    boundary_right = tmp_path / "boundary-right"
    _write(boundary_left / "src" / "a.py", b"b.py-DATA")
    _write(boundary_right / "src" / "a.pyb.py", b"-DATA")

    left_digest, _ = QUALITY_RECEIPT.compute_tree_digest(boundary_left)
    right_digest, _ = QUALITY_RECEIPT.compute_tree_digest(boundary_right)

    assert left_digest != right_digest


def test_the_digest_domain_is_pinned_and_separates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The domain prefix is part of the receipt contract, and changing it must change the digest."""
    assert QUALITY_RECEIPT.DIGEST_DOMAIN == b"plantgeo.agri-data-service.quality-receipt.v2"

    tree = tmp_path / "tree"
    _write(tree / "src" / "module.py", b"content = 1\n")
    before, _ = QUALITY_RECEIPT.compute_tree_digest(tree)

    monkeypatch.setattr(QUALITY_RECEIPT, "DIGEST_DOMAIN", b"plantgeo.agri-data-service.quality-receipt.v3")
    after, _ = QUALITY_RECEIPT.compute_tree_digest(tree)

    assert before != after


def test_pycache_and_pyc_files_are_excluded(tmp_path: Path) -> None:
    """A `__pycache__` directory or a `.pyc` sibling must not change the digest or file count."""
    tree = tmp_path / "tree"
    _write(tree / "src" / "module.py", b"content = 1\n")
    before_digest, before_count = QUALITY_RECEIPT.compute_tree_digest(tree)

    _write(tree / "src" / "__pycache__" / "module.cpython-312.pyc", b"not real bytecode")
    _write(tree / "src" / "module.pyc", b"also not real bytecode")
    after_digest, after_count = QUALITY_RECEIPT.compute_tree_digest(tree)

    assert after_digest == before_digest
    assert after_count == before_count == 1


def test_the_digest_covers_migrations_and_tool_configuration() -> None:
    """What the runtime image ships and what a passing sweep even means are both digest inputs."""
    assert QUALITY_RECEIPT.DIGEST_DIRECTORIES == ("src", "tests", "scripts", "alembic", "db")
    assert QUALITY_RECEIPT.DIGEST_FILES == ("pyproject.toml", "uv.lock", "mypy.ini", "ruff.toml", "alembic.ini")


def test_every_declared_digest_input_is_collected(tmp_path: Path) -> None:
    """Each declared directory and each declared root file must actually reach the digest."""
    for directory in QUALITY_RECEIPT.DIGEST_DIRECTORIES:
        _write(tmp_path / directory / "covered.txt", b"covered\n")
    for file_name in QUALITY_RECEIPT.DIGEST_FILES:
        _write(tmp_path / file_name, b"covered\n")

    collected = {path.relative_to(tmp_path).as_posix() for path in QUALITY_RECEIPT.digest_input_paths(tmp_path)}

    expected = {f"{directory}/covered.txt" for directory in QUALITY_RECEIPT.DIGEST_DIRECTORIES}
    assert collected == expected | set(QUALITY_RECEIPT.DIGEST_FILES)


@pytest.mark.parametrize(
    ("relative", "covered"),
    [
        ("src/agri_data_service/app.py", True),
        ("tests/conftest.py", True),
        ("alembic/versions/20260827_0027_vegetation_publication_queue.py", True),
        ("db/agri/tables/agri.signal_observation.sql", True),
        ("mypy.ini", True),
        ("ruff.toml", True),
        ("README.md", False),
        ("QUALITY_RECEIPT.json", False),
        ("src/__pycache__/app.cpython-312.pyc", False),
        ("db/tools/__pycache__/regenerate.cpython-312.pyc", False),
        ("", False),
    ],
)
def test_is_digest_input_judges_by_name(relative: str, covered: bool) -> None:
    """The index guard filters git's listing by name alone, with exactly the walk's rule."""
    assert QUALITY_RECEIPT.is_digest_input(relative) is covered
