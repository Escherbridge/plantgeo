"""Durable NASA POWER Retry-After constraints; see the sibling AGENTS.md."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

NASA_POWER_COOLDOWN_KEY: Final = "source-provider-cooldowns/v1/nasa-power.json"
NASA_POWER_COOLDOWN_MAX_BYTES: Final = 4096
NASA_POWER_COOLDOWN_CAS_ATTEMPTS: Final = 3
NASA_POWER_COOLDOWN_VERSION: Final = "provider-cooldown-v1"


class ClimateCooldownError(RuntimeError):
    """The provider's durable request constraint could not be verified or retained."""


@dataclass(frozen=True, slots=True)
class NasaPowerCooldown:
    """Read one provider constraint or advance it monotonically through bounded CAS attempts."""

    storage: AvailabilityStorage

    def read(self) -> datetime | None:
        """Read one small constraint; absence permits access, malformed or unreadable state refuses it."""
        try:
            stored = self.storage.read(NASA_POWER_COOLDOWN_KEY, max_bytes=NASA_POWER_COOLDOWN_MAX_BYTES)
            return None if stored is None else _decode(stored.payload)
        except Exception as error:
            raise ClimateCooldownError("NASA POWER cooldown state could not be read and verified") from error

    def advance(self, retry_not_before: datetime) -> datetime:
        """Retain the later of the new provider hint and any concurrent stored constraint."""
        try:
            _require_aware(retry_not_before)
            for _attempt in range(NASA_POWER_COOLDOWN_CAS_ATTEMPTS):
                prior = self.storage.read(NASA_POWER_COOLDOWN_KEY, max_bytes=NASA_POWER_COOLDOWN_MAX_BYTES)
                current = None if prior is None else _decode(prior.payload)
                if current is not None and current >= retry_not_before:
                    return current
                if self.storage.compare_and_swap(
                    NASA_POWER_COOLDOWN_KEY,
                    _encode(retry_not_before),
                    expected_etag=None if prior is None else prior.etag,
                    content_type="application/json",
                ):
                    return retry_not_before
        except Exception as error:
            raise ClimateCooldownError("NASA POWER Retry-After could not be durably retained") from error
        raise ClimateCooldownError("NASA POWER cooldown CAS contention exhausted its bounded attempts")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider cooldown requires a timezone-aware instant")


def _encode(retry_not_before: datetime) -> bytes:
    value = {
        "schema_version": NASA_POWER_COOLDOWN_VERSION,
        "provider": "nasa-power",
        "retry_not_before": retry_not_before.astimezone(UTC).isoformat(),
    }
    return canonical_json({"constraint": value, "sha256": sha256_digest(canonical_json(value))}).encode()


def _decode(payload: bytes) -> datetime:
    if not payload or len(payload) > NASA_POWER_COOLDOWN_MAX_BYTES:
        raise ValueError("provider cooldown exceeds its byte bound or is empty")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict) or set(decoded) != {"constraint", "sha256"}:
        raise ValueError("invalid provider cooldown envelope")
    value = decoded["constraint"]
    if not isinstance(value, dict) or sha256_digest(canonical_json(value)) != decoded["sha256"]:
        raise ValueError("provider cooldown checksum mismatch")
    if (
        set(value) != {"schema_version", "provider", "retry_not_before"}
        or value["schema_version"] != NASA_POWER_COOLDOWN_VERSION
        or value["provider"] != "nasa-power"
        or not isinstance(value["retry_not_before"], str)
    ):
        raise ValueError("invalid provider cooldown identity or instant")
    instant = datetime.fromisoformat(value["retry_not_before"])
    _require_aware(instant)
    return instant.astimezone(UTC)
