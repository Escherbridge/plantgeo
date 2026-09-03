"""Tree digest and file shape for the locked quality receipt.

Shared by `check.py --write-receipt` (the writer) and `verify_quality_receipt.py` (the reader that
the image build runs). Stdlib only, on purpose: the verifier executes on a bare interpreter in a
Docker stage that has no virtualenv, and it must never need git. See `scripts/AGENTS.md` section
"Locked quality receipt".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

SERVICE_ROOT: Final = Path(__file__).resolve().parent.parent
RECEIPT_FILE_NAME: Final = "QUALITY_RECEIPT.json"
RECEIPT_PATH: Final = SERVICE_ROOT / RECEIPT_FILE_NAME

#: Bumped to 2 when `digest_domain` became a required key.
RECEIPT_SCHEMA_VERSION: Final = 2

#: The one command that produces a valid receipt; quoted in every refusal.
RECEIPT_REWRITE_COMMAND: Final = "uv run --no-sync python scripts/check.py --write-receipt"

#: Domain-separation prefix so a digest of this tree can never be replayed as a digest of anything
#: else that happens to length-prefix paths and bytes the same way. Bumped to v2 because the digest
#: now hashes CRLF-normalized content instead of raw disk bytes; see scripts/AGENTS.md.
DIGEST_DOMAIN: Final = b"plantgeo.agri-data-service.quality-receipt.v2"

#: Everything a green sweep reads, plus everything that decides what the judgement and the image
#: mean. `src` and `tests` are what pytest and mypy judge, `scripts` is the operator surface the
#: extended mypy scope covers, `alembic` and `db` are the migration machinery the runtime image
#: ships, the lock files fix the tool and library versions, and `mypy.ini`/`ruff.toml` define what
#: "mypy pass" and "lint pass" mean at all.
DIGEST_DIRECTORIES: Final[tuple[str, ...]] = ("src", "tests", "scripts", "alembic", "db")
DIGEST_FILES: Final[tuple[str, ...]] = ("pyproject.toml", "uv.lock", "mypy.ini", "ruff.toml", "alembic.ini")

#: Build artifacts that differ between a developer tree and a Docker build context. Including them
#: would make the receipt unverifiable rather than more honest.
EXCLUDED_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints"}
)
EXCLUDED_SUFFIXES: Final[frozenset[str]] = frozenset({".pyc", ".pyo", ".pyd"})


class ReceiptError(RuntimeError):
    """A receipt is absent, malformed, or does not describe this tree."""


def normalize_content(content: bytes) -> bytes:
    """Return one file's bytes as git stores a text file: CRLF folded to LF, a lone CR left alone."""
    return content.replace(b"\r\n", b"\n")


def _is_excluded(relative: str) -> bool:
    """Return whether one POSIX-relative path names a build artifact rather than reviewed source."""
    pure = PurePosixPath(relative)
    if pure.suffix in EXCLUDED_SUFFIXES:
        return True
    return any(part in EXCLUDED_DIRECTORY_NAMES for part in pure.parts)


def is_digest_input(relative: str) -> bool:
    """Return whether one POSIX-relative path is covered by the digest, judged by name alone.

    Name-based so a listing that never touched this filesystem -- git's index, for one -- can be
    filtered by exactly the rule the on-disk walk applies.
    """
    parts = PurePosixPath(relative).parts
    if not parts or _is_excluded(relative):
        return False
    return parts[0] in DIGEST_DIRECTORIES or relative in DIGEST_FILES


def digest_input_paths(root: Path = SERVICE_ROOT) -> list[Path]:
    """Return every file the digest covers, sorted by POSIX-relative path."""
    collected: list[Path] = []
    for directory in DIGEST_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        collected.extend(
            path for path in base.rglob("*") if path.is_file() and not _is_excluded(path.relative_to(root).as_posix())
        )
    collected.extend(root / name for name in DIGEST_FILES if (root / name).is_file())
    return sorted(collected, key=lambda path: path.relative_to(root).as_posix())


def compute_digest(relative_paths: Iterable[str], read_content: Callable[[str], bytes]) -> tuple[str, int]:
    """Return the sha256 over each POSIX-relative path and its content, plus how many were covered.

    Paths are sorted here rather than by the caller, so a filesystem walk and a git index listing
    naming the same files always hash in the same order. Both the path and the content are
    length-prefixed, so renaming a file can never produce the same digest as editing one --
    concatenation alone is ambiguous about where a path stops. Content is CRLF-normalized before it
    is length-prefixed, so the digest describes the bytes as committed rather than the bytes a given
    checkout's line endings happen to carry.
    """
    digest = hashlib.sha256()
    digest.update(DIGEST_DOMAIN)
    ordered = sorted(relative_paths)
    for relative in ordered:
        encoded = relative.encode("utf-8")
        content = normalize_content(read_content(relative))
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest(), len(ordered)


def compute_tree_digest(root: Path = SERVICE_ROOT) -> tuple[str, int]:
    """Return the digest of one working tree's digest inputs, and how many files were covered."""
    relative_paths = [path.relative_to(root).as_posix() for path in digest_input_paths(root)]
    return compute_digest(relative_paths, lambda relative: (root / relative).read_bytes())


def write_receipt(payload: dict[str, object], receipt_path: Path = RECEIPT_PATH) -> None:
    """Write one receipt as sorted, newline-terminated, LF-only JSON."""
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    receipt_path.write_text(rendered, encoding="utf-8", newline="\n")


def read_receipt(receipt_path: Path = RECEIPT_PATH) -> dict[str, object]:
    """Read one receipt, refusing anything that is not a JSON object this verifier can compare."""
    if not receipt_path.is_file():
        raise ReceiptError(f"{receipt_path.name} is absent; run `{RECEIPT_REWRITE_COMMAND}` on a green tree")
    try:
        parsed = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReceiptError(f"{receipt_path.name} is not valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ReceiptError(f"{receipt_path.name} is not a JSON object")
    version = parsed.get("schema_version")
    if version != RECEIPT_SCHEMA_VERSION:
        raise ReceiptError(
            f"{receipt_path.name} declares schema_version {version!r}, expected {RECEIPT_SCHEMA_VERSION}; "
            f"rewrite it with a green sweep: {RECEIPT_REWRITE_COMMAND}"
        )
    domain = parsed.get("digest_domain")
    expected_domain = DIGEST_DOMAIN.decode()
    if domain != expected_domain:
        raise ReceiptError(
            f"{receipt_path.name} was written with digest domain {domain!r}, this verifier computes "
            f"{expected_domain!r} -- the digest algorithm changed, so the two are not comparable; "
            f"rewrite it with a green sweep: {RECEIPT_REWRITE_COMMAND}"
        )
    return parsed
