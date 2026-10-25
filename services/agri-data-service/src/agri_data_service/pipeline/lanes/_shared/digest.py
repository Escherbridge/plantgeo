"""Canonical-JSON rendering and SHA-256 digests, kwargs explicit per call site (`_shared/AGENTS.md` §Digests)."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

#: `(",", ":")`: no whitespace at all.
COMPACT_SEPARATORS: Final = (",", ":")
#: `json.dumps`'s own default `(", ", ": ")`; named so a call site that relied on the default still says so.
DEFAULT_SEPARATORS: Final = (", ", ": ")


def canonical_json_text(
    value: object,
    *,
    sort_keys: bool,
    separators: tuple[str, str],
    allow_nan: bool,
    default: Callable[[object], object] | None = None,
) -> str:
    """`json.dumps` with the digest-relevant kwargs REQUIRED, so no call site inherits a default by accident."""
    return json.dumps(value, sort_keys=sort_keys, separators=separators, allow_nan=allow_nan, default=default)


def canonical_json_bytes(
    value: object,
    *,
    sort_keys: bool,
    separators: tuple[str, str],
    allow_nan: bool,
    default: Callable[[object], object] | None = None,
) -> bytes:
    """`canonical_json_text` encoded UTF-8 (ASCII in practice: `ensure_ascii` stays at its default)."""
    text = canonical_json_text(value, sort_keys=sort_keys, separators=separators, allow_nan=allow_nan, default=default)
    return text.encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    """Lower-case hex SHA-256 of exact bytes."""
    return hashlib.sha256(payload).hexdigest()


def sha256_hex_of_lines(lines: Iterable[str]) -> str:
    """SHA-256 of the lines joined by one `"\\n"` (no trailing newline), UTF-8: the per-row digest variant."""
    return sha256_hex("\n".join(lines).encode("utf-8"))


__all__ = [
    "COMPACT_SEPARATORS",
    "DEFAULT_SEPARATORS",
    "canonical_json_bytes",
    "canonical_json_text",
    "sha256_hex",
    "sha256_hex_of_lines",
]
