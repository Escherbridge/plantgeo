"""Tree digest and file shape for the locked quality receipt.

Shared by `check.py --write-receipt` (the writer) and `verify_quality_receipt.py` (the reader that
the image build runs). Stdlib only, on purpose: the verifier executes on a bare interpreter in a
Docker stage that has no virtualenv. See `scripts/AGENTS.md` section "Locked quality receipt".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final

SERVICE_ROOT: Final = Path(__file__).resolve().parent.parent
RECEIPT_FILE_NAME: Final = "QUALITY_RECEIPT.json"
RECEIPT_PATH: Final = SERVICE_ROOT / RECEIPT_FILE_NAME
RECEIPT_SCHEMA_VERSION: Final = 1

#: Domain-separation prefix so a digest of this tree can never be replayed as a digest of anything
#: else that happens to length-prefix paths and bytes the same way. Bumped to v2 because the digest
#: now hashes CRLF-normalized content instead of raw disk bytes; see scripts/AGENTS.md.
DIGEST_DOMAIN: Final = b"plantgeo.agri-data-service.quality-receipt.v2"

#: Everything a green sweep actually reads. `src` and `tests` are what pytest and mypy judge,
#: `scripts` is the operator surface the extended mypy scope now covers, and the two lock files
#: decide which tool and library versions produced the judgement.
DIGEST_DIRECTORIES: Final[tuple[str, ...]] = ("src", "tests", "scripts")
DIGEST_FILES: Final[tuple[str, ...]] = ("pyproject.toml", "uv.lock")

#: Build artifacts that differ between a developer tree and a Docker build context. Including them
#: would make the receipt unverifiable rather than more honest.
EXCLUDED_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints"}
)
EXCLUDED_SUFFIXES: Final[frozenset[str]] = frozenset({".pyc", ".pyo", ".pyd"})


class ReceiptError(RuntimeError):
    """A receipt is absent, malformed, or does not describe this tree."""


def _is_excluded(path: Path, root: Path) -> bool:
    """Return whether one path is a build artifact rather than reviewed source."""
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    return any(part in EXCLUDED_DIRECTORY_NAMES for part in path.relative_to(root).parts)


def digest_input_paths(root: Path = SERVICE_ROOT) -> list[Path]:
    """Return every file the digest covers, sorted by POSIX-relative path."""
    collected: list[Path] = []
    for directory in DIGEST_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        collected.extend(path for path in base.rglob("*") if path.is_file() and not _is_excluded(path, root))
    collected.extend(root / name for name in DIGEST_FILES if (root / name).is_file())
    return sorted(collected, key=lambda path: path.relative_to(root).as_posix())


def compute_tree_digest(root: Path = SERVICE_ROOT) -> tuple[str, int]:
    """Return the sha256 over every covered path and its bytes, plus how many files were covered.

    Both the path and the content are length-prefixed so that renaming a file can never produce the
    same digest as editing one -- concatenation alone is ambiguous about where a path stops. Content
    is CRLF-normalized to LF before hashing and length-prefixing, so the digest describes the bytes
    as committed rather than the bytes a given checkout's line endings happen to carry.
    """
    digest = hashlib.sha256()
    digest.update(DIGEST_DOMAIN)
    paths = digest_input_paths(root)
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest(), len(paths)


def write_receipt(payload: dict[str, object], receipt_path: Path = RECEIPT_PATH) -> None:
    """Write one receipt as sorted, newline-terminated, LF-only JSON."""
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    receipt_path.write_text(rendered, encoding="utf-8", newline="\n")


def read_receipt(receipt_path: Path = RECEIPT_PATH) -> dict[str, object]:
    """Read one receipt, refusing anything that is not a JSON object of the expected version."""
    if not receipt_path.is_file():
        raise ReceiptError(
            f"{receipt_path.name} is absent; run `uv run --no-sync python scripts/check.py --write-receipt` "
            "on a green tree"
        )
    try:
        parsed = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReceiptError(f"{receipt_path.name} is not valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ReceiptError(f"{receipt_path.name} is not a JSON object")
    version = parsed.get("schema_version")
    if version != RECEIPT_SCHEMA_VERSION:
        raise ReceiptError(
            f"{receipt_path.name} declares schema_version {version!r}, expected {RECEIPT_SCHEMA_VERSION}"
        )
    return parsed
