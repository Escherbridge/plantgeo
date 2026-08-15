"""Pure mechanism helpers for canonical JSON, SHA-256 digests, ISO date prefixing, and validation primitives.

Layer L0: May import third-party stdlib only. May NOT import any first-party module,
SQLAlchemy, httpx, asyncpg, or click.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime
from typing import Any


def canonical_json(data: Any) -> str:
    """Return canonical, deterministic JSON string with sorted keys and tight separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_digest(content: str | bytes) -> str:
    """Compute SHA-256 hexadecimal digest for string or byte input."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def iso_date_prefix(val: date | datetime | str) -> str:
    """Extract standard ISO-8601 calendar date prefix YYYY-MM-DD."""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    iso_calendar_date_length = 10
    if len(s) >= iso_calendar_date_length and s[4] == "-" and s[7] == "-":
        return s[:iso_calendar_date_length]
    raise ValueError(f"Invalid ISO date prefix input: {val!r}")


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(tz=UTC)


def validate_finite(value: float, name: str = "value") -> float:
    """Guard against non-finite floats (NaN/Inf)."""
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite float, got {value}")
    return value
