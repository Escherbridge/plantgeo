"""Refuse a tree whose locked quality receipt was not refreshed by a green sweep.

Run by the image build, so it is stdlib-only, needs no virtualenv and never calls git: `python
scripts/verify_quality_receipt.py` from the service root. Exit 0 means the receipt names this exact
tree and records every check as passing; any other exit means the image must not be built.
"""

from __future__ import annotations

import sys
from pathlib import Path

from quality_receipt import (
    DIGEST_DIRECTORIES,
    DIGEST_FILES,
    RECEIPT_PATH,
    RECEIPT_REWRITE_COMMAND,
    ReceiptError,
    compute_tree_digest,
    read_receipt,
)


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


def _mismatch_message(recorded: str, recorded_count: object, actual: str, actual_count: int) -> str:
    """Return the refusal text: what differs, then every cause that can make it differ.

    Deliberately not "source changed, re-run the sweep". Two of the three causes seen in production
    are not fixed by re-running anything, and saying so sent an operator down the wrong path.
    """
    covered = ", ".join((*DIGEST_DIRECTORIES, *DIGEST_FILES))
    return (
        "the tree does not match its receipt.\n"
        f"  receipt: {recorded} over {recorded_count} files\n"
        f"  tree:    {actual} over {actual_count} files\n"
        f"The digest is a sha256 over the CRLF-normalized bytes of the tracked inputs ({covered}), so\n"
        "it can differ here for exactly three reasons:\n"
        "  1. a digest input was edited after the sweep that wrote the receipt.\n"
        f"     fix: re-run the sweep -- {RECEIPT_REWRITE_COMMAND}\n"
        "  2. the receipt was committed without a new or changed input, so a file in the author's\n"
        "     digest never reached this checkout (an untracked file).\n"
        "     fix: git add the missing file, then re-run the sweep so both land in one commit\n"
        "  3. an input is in the author's tree but not in this build context, because .gitignore\n"
        "     keeps it out of the commit or .dockerignore keeps it out of the COPY.\n"
        "     fix: un-exclude it, or drop it from the digest via EXCLUDED_DIRECTORY_NAMES\n"
        "A differing file count points at 2 or 3; an equal count points at 1. The writer refuses to\n"
        "write while 2 or 3 hold, so a receipt from the current check.py normally fails here only for\n"
        "1 -- or because this build context is not the tree that was swept."
    )


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
        raise ReceiptError(_mismatch_message(recorded, receipt.get("digest_file_count"), actual, file_count))
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
