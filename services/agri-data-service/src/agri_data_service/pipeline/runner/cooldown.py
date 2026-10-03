"""Per-host provider cooldowns: a stated Retry-After the unit ladder will not wait out, carried to later turns.

Stored beside the turn receipts and source checkpoints in the availability storage. See
`pipeline/runner/AGENTS.md` "One retry ladder".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.canonical import canonical_json

if TYPE_CHECKING:
    from agri_data_service.pipeline.runner.receipts import ConditionalObjectStorage

COOLDOWN_PREFIX: Final = "lane-provider-cooldowns/v1"
COOLDOWN_VERSION: Final = "lane-provider-cooldown-v1"
COOLDOWN_MAX_BYTES: Final = 4 * 1024
COOLDOWN_CAS_ATTEMPTS: Final = 4
#: A stated wait is honoured up to a day (the forward cadence); a broken header never parks a lane longer.
MAX_COOLDOWN_SECONDS: Final = 24 * 60 * 60
_HOST: Final = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


class CooldownError(RuntimeError):
    """A cooldown scope is invalid, or every compare-and-swap lost."""


def cooldown_key(host: str) -> str:
    """Where one provider host's cooldown lives."""
    if _HOST.fullmatch(host) is None:
        raise CooldownError("invalid provider cooldown host")
    return f"{COOLDOWN_PREFIX}/{host}.json"


def _decode(payload: object, host: str) -> datetime | None:
    """The stored instant, or `None` for anything unreadable: a cooldown is advisory and fails open."""
    if not isinstance(payload, bytes) or len(payload) > COOLDOWN_MAX_BYTES:
        return None
    try:
        document = json.loads(payload)
        if document.get("schema_version") != COOLDOWN_VERSION or document.get("host") != host:
            return None
        until = datetime.fromisoformat(document["throttled_until"])
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return until if until.tzinfo is not None else None


@dataclass(frozen=True, slots=True)
class ProviderCooldowns:
    """Read and extend one host's "throttled until" instant through conditional object storage."""

    storage: ConditionalObjectStorage

    def throttled_until(self, host: str, *, now: datetime) -> datetime | None:
        """The stored instant while it is still in the future, else `None`."""
        stored = self.storage.read(cooldown_key(host), max_bytes=COOLDOWN_MAX_BYTES)
        until = None if stored is None else _decode(getattr(stored, "payload", None), host)
        return until if until is not None and until > now else None

    def record(self, host: str, until: datetime, *, now: datetime, run_id: str) -> datetime:
        """Keep the later of the stored and the new instant (CAS-merged); returns the instant now stored."""
        key = cooldown_key(host)
        for _attempt in range(COOLDOWN_CAS_ATTEMPTS):
            stored = self.storage.read(key, max_bytes=COOLDOWN_MAX_BYTES)
            etag = None if stored is None else getattr(stored, "etag", None)
            held = None if stored is None else _decode(getattr(stored, "payload", None), host)
            if held is not None and held >= until:
                return held
            payload = canonical_json(
                {
                    "schema_version": COOLDOWN_VERSION,
                    "host": host,
                    "throttled_until": until.astimezone(UTC).isoformat(),
                    "recorded_at": now.astimezone(UTC).isoformat(),
                    "run_id": run_id,
                }
            ).encode()
            if self.storage.compare_and_swap(key, payload, expected_etag=etag, content_type="application/json"):
                return until
        raise CooldownError(f"the cooldown for {host} lost every swap")


__all__ = [
    "COOLDOWN_PREFIX",
    "MAX_COOLDOWN_SECONDS",
    "CooldownError",
    "ProviderCooldowns",
    "cooldown_key",
]
