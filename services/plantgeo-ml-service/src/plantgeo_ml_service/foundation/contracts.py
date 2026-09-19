"""Credential-custody guards and the byte-level canonical serializer used for checksums.

Layer L0: stdlib only. Copied from agri-data-service's `execution/contracts.py`;
`tests/test_contracts_parity.py` pins the copy against the source.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final
from urllib.parse import parse_qsl, urlsplit

#: Field names that never reach durable storage, however they are spelled.
SENSITIVE_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "auth_token",
        "bearer_token",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "id_token",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
        "set_cookie",
        "token",
        "x_api_key",
    }
)

#: Unambiguous credential suffixes, so `openai_api_key` is caught without an exhaustive list.
SENSITIVE_FIELD_SUFFIXES: Final[tuple[str, ...]] = (
    "_api_key",
    "_apikey",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)

_CAMEL_CASE_BOUNDARY: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALPHANUMERIC_RUN: Final = re.compile(r"[^a-z0-9]+")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a contract deterministically for checksums and run IDs."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_sensitive_field_name(value: object) -> str:
    """Normalize camelCase, punctuation, and whitespace before custody checks."""
    camel_split = _CAMEL_CASE_BOUNDARY.sub("_", str(value))
    return _NON_ALPHANUMERIC_RUN.sub("_", camel_split.lower()).strip("_")


def is_sensitive_field_name(value: object) -> bool:
    """Recognize explicit credential names plus unambiguous credential suffixes."""
    normalized = canonical_sensitive_field_name(value)
    return normalized in SENSITIVE_FIELD_NAMES or normalized.endswith(SENSITIVE_FIELD_SUFFIXES)


def reject_credential_url(value: str) -> None:
    """Reject URLs that would persist userinfo or credential query parameters."""
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValueError("URLs with credentials cannot be persisted")
    components = [parsed.query, parsed.fragment]
    if not parsed.scheme and not parsed.netloc and "=" in value:
        components.append(value)
    for component in components:
        for key, _ in parse_qsl(component, keep_blank_values=True):
            if is_sensitive_field_name(key):
                raise ValueError("URLs with credential query parameters cannot be persisted")
