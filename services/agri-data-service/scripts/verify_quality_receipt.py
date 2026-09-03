"""Refuse a tree whose locked quality receipt was not refreshed by a green sweep.

Run by the image build, so it is stdlib-only and needs no virtualenv: `python
scripts/verify_quality_receipt.py` from the service root. Exit 0 means the receipt names this exact
tree and records every check as passing; any other exit means the image must not be built.
"""

from __future__ import annotations

import sys
from pathlib import Path

from quality_receipt import RECEIPT_PATH, ReceiptError, compute_tree_digest, read_receipt


def _recorded_failures(receipt: dict[str, object]) -> list[str]:
    """Return the name of every check the receipt itself records as not passing."""
    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ReceiptError("receipt records no checks")
    failures: list[str] = []
    for entry in checks:
        if not isinstance(entry, dict):
            raise ReceiptError(f"receipt check entry is not an object: {entry!r}")
        if entry.get("status") != "pass":
            failures.append(str(entry.get("name", "(unnamed)")))
    return failures


def verify(receipt_path: Path = RECEIPT_PATH) -> str:
    """Return a success message, or raise `ReceiptError` naming exactly what does not match."""
    receipt = read_receipt(receipt_path)
    failures = _recorded_failures(receipt)
    if failures:
        raise ReceiptError(f"receipt records failing checks: {', '.join(failures)}")

    recorded = receipt.get("tree_digest")
    if not isinstance(recorded, str) or not recorded.startswith("sha256:"):
        raise ReceiptError(f"receipt tree_digest is {recorded!r}, expected a 'sha256:' string")

    actual_digest, file_count = compute_tree_digest(receipt_path.parent)
    actual = f"sha256:{actual_digest}"
    if actual != recorded:
        raise ReceiptError(
            "the tree does not match its receipt -- source changed without a green sweep.\n"
            f"  receipt: {recorded} over {receipt.get('digest_file_count')} files\n"
            f"  tree:    {actual} over {file_count} files\n"
            "  fix: uv run --no-sync python scripts/check.py --write-receipt"
        )
    return f"quality receipt verified: {actual} over {file_count} files, generated {receipt.get('generated_at')}"


def main(argv: list[str] | None = None) -> int:
    """Verify the receipt beside this script's service root and report the verdict."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print(f"{Path(__file__).name} takes no arguments", file=sys.stderr)
        return 2
    try:
        print(verify())
    except ReceiptError as error:
        print(f"QUALITY RECEIPT REFUSED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
