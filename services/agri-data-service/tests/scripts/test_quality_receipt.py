"""Regression tests for the CRLF-normalized quality-receipt tree digest.

See `scripts/AGENTS.md`, "Locked quality receipt", for why the digest normalizes line endings
before hashing: a Windows working tree may carry CRLF that git's `* text=auto eol=lf` only
normalizes away on commit, while the Linux Docker build context is always LF.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _quality_receipt_module() -> Any:
    """Load scripts/quality_receipt.py by file path (mirrors tests/test_purge_parquet_layout.py)."""
    path = Path(__file__).parents[2] / "scripts" / "quality_receipt.py"
    name = "plantgeo_quality_receipt_module"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


QUALITY_RECEIPT = _quality_receipt_module()


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


def test_changing_content_changes_the_digest(tmp_path: Path) -> None:
    """Editing one byte of real content must change the digest."""
    tree = tmp_path / "tree"
    target = tree / "src" / "module.py"
    _write(target, b"x = 1\n")
    before, _ = QUALITY_RECEIPT.compute_tree_digest(tree)

    _write(target, b"x = 2\n")
    after, _ = QUALITY_RECEIPT.compute_tree_digest(tree)

    assert before != after


def test_renaming_a_file_changes_the_digest(tmp_path: Path) -> None:
    """A rename must not digest the same as the original path, even with identical content."""
    original_tree = tmp_path / "original"
    renamed_tree = tmp_path / "renamed"
    _write(original_tree / "src" / "a.py", b"same = True\n")
    _write(renamed_tree / "src" / "b.py", b"same = True\n")

    original_digest, _ = QUALITY_RECEIPT.compute_tree_digest(original_tree)
    renamed_digest, _ = QUALITY_RECEIPT.compute_tree_digest(renamed_tree)

    assert original_digest != renamed_digest


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
