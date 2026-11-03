"""Deterministic credential-free artifact encodings."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from collections.abc import Mapping


def json_bytes(value: object) -> bytes:
    """Stable JSON with a final newline, safe for environment-template maps."""
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def toml_bytes(document: Mapping[str, object]) -> bytes:
    """Render the JSON-mode lane/provider model subset, including arrays of tables."""
    lines: list[str] = []

    def emit(table: Mapping[str, object], path: tuple[str, ...]) -> None:
        for key, value in sorted(table.items()):
            if not isinstance(value, dict) and not _table_array(value):
                lines.append(f"{json.dumps(key)} = {json.dumps(value, ensure_ascii=False, allow_nan=False)}")
        for key, value in sorted(table.items()):
            nested = (*path, key)
            name = ".".join(json.dumps(part) for part in nested)
            if isinstance(value, dict):
                lines.extend(("", f"[{name}]"))
                emit(value, nested)
            elif _table_array(value):
                for item in value:
                    lines.extend(("", f"[[{name}]]"))
                    emit(item, nested)

    emit(document, ())
    return ("\n".join(lines) + "\n").encode("utf-8")


def _table_array(value: object) -> TypeGuard[list[dict[str, object]]]:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)
