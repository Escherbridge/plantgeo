"""Pure mechanism helpers for canonical JSON, SHA-256 digests, and validation primitives.

Layer L0: May import third-party stdlib only. May NOT import any first-party module,
SQLAlchemy, httpx, asyncpg, or click.

Copied from agri-data-service rather than imported; `tests/test_canonical_parity.py` pins the copy.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def canonical_json(data: Any) -> str:
    """Return canonical, deterministic JSON string with sorted keys and tight separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_digest(content: str | bytes) -> str:
    """Compute SHA-256 hexadecimal digest for string or byte input."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def validate_finite(value: float, name: str = "value") -> float:
    """Guard against non-finite floats (NaN/Inf)."""
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite float, got {value}")
    return value
