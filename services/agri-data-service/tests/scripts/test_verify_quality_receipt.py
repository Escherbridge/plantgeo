"""Refusal tests for the image build's quality-receipt gate.

`verify_quality_receipt.py` is the only thing standing between an unjudged tree and a published
image, and it is stdlib-only so the Docker stage can run it on a bare interpreter. These tests
build whole trees and receipts under `tmp_path`, so they exercise the real file shape without a
repository, a virtualenv or a subprocess. See `scripts/AGENTS.md`, "Locked quality receipt".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path

QUALITY_RECEIPT = load_scripts_module("quality_receipt.py", "quality_receipt")
VERIFY = load_scripts_module("verify_quality_receipt.py", "plantgeo_verify_quality_receipt_module")

PASSING_CHECK = {"name": "pytest", "command": "pytest -q", "status": "pass", "duration_seconds": 1.0}


def _write_source(tree: Path) -> None:
    """Create the smallest tree the digest covers: one file under src/."""
    (tree / "src").mkdir(parents=True, exist_ok=True)
    (tree / "src" / "module.py").write_bytes(b"value = 1\n")


def _receipt_for(tree: Path, **overrides: object) -> Path:
    """Write a schema-current receipt describing `tree`, with any single field overridden."""
    tree_digest, file_count = QUALITY_RECEIPT.compute_tree_digest(tree)
    payload: dict[str, object] = {
        "schema_version": QUALITY_RECEIPT.RECEIPT_SCHEMA_VERSION,
        "generated_at": "2026-09-03T00:00:00Z",
        "digest_domain": QUALITY_RECEIPT.DIGEST_DOMAIN.decode(),
        "tree_digest": f"sha256:{tree_digest}",
        "digest_file_count": file_count,
        "tools": {"python": "3.12.10"},
        "checks": [PASSING_CHECK],
    }
    payload.update(overrides)
    receipt_path = tree / QUALITY_RECEIPT.RECEIPT_FILE_NAME
    QUALITY_RECEIPT.write_receipt(payload, receipt_path)
    return receipt_path


def test_verify_accepts_a_receipt_that_names_this_tree(tmp_path: Path) -> None:
    """The success path: same bytes, same digest, every recorded check passing."""
    _write_source(tmp_path)
    receipt_path = _receipt_for(tmp_path)

    message = VERIFY.verify(receipt_path)

    assert "quality receipt verified" in message
    assert "over 1 files" in message


def test_verify_refuses_an_absent_receipt(tmp_path: Path) -> None:
    """No receipt at all is the state a tree is in before its first green sweep."""
    _write_source(tmp_path)

    with pytest.raises(QUALITY_RECEIPT.ReceiptError, match="is absent") as error:
        VERIFY.verify(tmp_path / QUALITY_RECEIPT.RECEIPT_FILE_NAME)

    assert "check.py --write-receipt" in str(error.value)


def test_verify_refuses_a_stale_schema_version(tmp_path: Path) -> None:
    """A receipt from before `digest_domain` existed cannot be compared, only rewritten."""
    _write_source(tmp_path)
    receipt_path = _receipt_for(tmp_path, schema_version=1)

    with pytest.raises(QUALITY_RECEIPT.ReceiptError, match="schema_version 1") as error:
        VERIFY.verify(receipt_path)

    assert "rewrite it with a green sweep" in str(error.value)


def test_verify_refuses_a_stale_digest_domain(tmp_path: Path) -> None:
    """A v1 digest must fail as `written by another algorithm`, never as `source changed`."""
    _write_source(tmp_path)
    receipt_path = _receipt_for(tmp_path, digest_domain="plantgeo.agri-data-service.quality-receipt.v1")

    with pytest.raises(QUALITY_RECEIPT.ReceiptError, match="digest domain") as error:
        VERIFY.verify(receipt_path)

    message = str(error.value)
    assert "quality-receipt.v1" in message
    assert "rewrite it with a green sweep" in message


def test_verify_refuses_a_recorded_failing_check(tmp_path: Path) -> None:
    """A receipt that admits a red gate is still a receipt; it must not build an image."""
    _write_source(tmp_path)
    failing = {**PASSING_CHECK, "status": "fail"}
    receipt_path = _receipt_for(tmp_path, checks=[failing])

    with pytest.raises(QUALITY_RECEIPT.ReceiptError, match="records failing checks: pytest"):
        VERIFY.verify(receipt_path)


def test_verify_refuses_a_receipt_with_no_checks(tmp_path: Path) -> None:
    """An empty check list would otherwise pass the `no failures` test vacuously."""
    _write_source(tmp_path)
    receipt_path = _receipt_for(tmp_path, checks=[])

    with pytest.raises(QUALITY_RECEIPT.ReceiptError, match="records no checks"):
        VERIFY.verify(receipt_path)


def test_verify_refuses_a_tree_digest_that_is_not_a_sha256_string(tmp_path: Path) -> None:
    """A malformed digest field must be named as malformed, not compared."""
    _write_source(tmp_path)
    receipt_path = _receipt_for(tmp_path, tree_digest=42)

    with pytest.raises(QUALITY_RECEIPT.ReceiptError, match="expected a 'sha256:' string"):
        VERIFY.verify(receipt_path)


def test_verify_refuses_a_digest_mismatch_and_names_every_cause(tmp_path: Path) -> None:
    """The refusal must list all three production causes, because two are not fixed by re-running."""
    _write_source(tmp_path)
    receipt_path = _receipt_for(tmp_path)
    (tmp_path / "src" / "module.py").write_bytes(b"value = 2\n")

    with pytest.raises(QUALITY_RECEIPT.ReceiptError) as error:
        VERIFY.verify(receipt_path)

    message = str(error.value)
    assert "does not match its receipt" in message
    assert "edited after the sweep" in message
    assert "untracked file" in message
    assert ".gitignore" in message
    assert ".dockerignore" in message
    assert "re-run the sweep" in message
    assert "alembic.ini" in message


def test_verify_reports_both_digests_and_both_file_counts(tmp_path: Path) -> None:
    """A differing file count is the operator's signal for cause 2 or 3 rather than cause 1."""
    _write_source(tmp_path)
    receipt_path = _receipt_for(tmp_path)
    (tmp_path / "src" / "extra.py").write_bytes(b"added = True\n")

    with pytest.raises(QUALITY_RECEIPT.ReceiptError) as error:
        VERIFY.verify(receipt_path)

    message = str(error.value)
    assert "over 1 files" in message
    assert "over 2 files" in message
