"""Bounded, checksum-verified source response checkpoints; see AGENTS.md."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

CHECKPOINT_VERSION: Final = "source-response-checkpoint-v1"
CHECKPOINT_MAX_AGE: Final = timedelta(days=7)
CHECKPOINT_MAX_BODY_BYTES: Final = 2 * 1024 * 1024
CHECKPOINT_MAX_BYTES: Final = 3 * 1024 * 1024
logger = structlog.get_logger(__name__)


def report_checkpoint_rejection(identity: SourceCheckpointIdentity, error: Exception) -> None:
    """Name a provider parser's refusal without changing the meaning of the source body."""
    logger.warning("source_checkpoint_parser_rejected", key=identity.key, reason=f"{type(error).__name__}: {error}")


@dataclass(frozen=True, slots=True)
class SourceCheckpointIdentity:
    """Bind a response to its provider, parser version, complete support, day and exact request."""

    provider: str
    support_sha256: str
    day: str
    request_url: str

    def as_dict(self) -> dict[str, str]:
        """Render the identity used by both the object key and its verified payload."""
        return {
            "schema_version": CHECKPOINT_VERSION,
            "provider": self.provider,
            "support_sha256": self.support_sha256,
            "day": self.day,
            "request_url": self.request_url,
        }

    @property
    def key(self) -> str:
        """Use an operational namespace that cannot be discovered as a serving partition."""
        return f"source-response-checkpoints/v1/{sha256_digest(canonical_json(self.as_dict()))}.json"


@dataclass(frozen=True, slots=True)
class SourceCheckpoint:
    """Original parser input and the original retrieval instant, with digests reverified."""

    body: bytes
    retrieved_at: datetime


@dataclass(slots=True)
class SourceResponseCheckpoints:
    """Read and conditionally refresh bounded source checkpoints without touching availability."""

    storage: AvailabilityStorage

    def read(self, identity: SourceCheckpointIdentity, *, now: datetime) -> SourceCheckpoint | None:
        """Reject stale, foreign or corrupted bytes before a provider parser sees them."""
        try:
            stored = self.storage.read(identity.key, max_bytes=CHECKPOINT_MAX_BYTES)
            if stored is None:
                return None
            return _decode_checkpoint(stored.payload, identity=identity, now=now)
        except Exception as error:
            logger.warning("source_checkpoint_rejected", key=identity.key, reason=f"{type(error).__name__}: {error}")
            return None

    def write(self, identity: SourceCheckpointIdentity, checkpoint: SourceCheckpoint, *, response_sha256: str) -> None:
        """Checkpoint only original response bytes, refreshing a stable key with an ETag guard."""
        try:
            if not checkpoint.body or len(checkpoint.body) > CHECKPOINT_MAX_BODY_BYTES:
                raise ValueError("source response exceeds the checkpoint body bound or is empty")
            if sha256_digest(checkpoint.body) != response_sha256:
                raise ValueError("original response bytes disagree with their source receipt")
            if checkpoint.retrieved_at.tzinfo is None or checkpoint.retrieved_at.utcoffset() is None:
                raise ValueError("source retrieval instant must be timezone-aware")
            value = {
                **identity.as_dict(),
                "retrieved_at": checkpoint.retrieved_at.astimezone(UTC).isoformat(),
                "response_sha256": response_sha256,
                "response_bytes": len(checkpoint.body),
                "body_base64": base64.b64encode(checkpoint.body).decode("ascii"),
            }
            payload = canonical_json({"checkpoint": value, "sha256": sha256_digest(canonical_json(value))}).encode()
            if len(payload) > CHECKPOINT_MAX_BYTES:
                raise ValueError("source checkpoint exceeds the envelope byte bound")
            prior = self.storage.read(identity.key, max_bytes=CHECKPOINT_MAX_BYTES)
            if prior is not None:
                try:
                    current = _decode_checkpoint(
                        prior.payload, identity=identity, now=checkpoint.retrieved_at, require_fresh=False
                    )
                    if current.retrieved_at >= checkpoint.retrieved_at:
                        return
                except ValueError:
                    pass
            changed = self.storage.compare_and_swap(
                identity.key,
                payload,
                expected_etag=None if prior is None else prior.etag,
                content_type="application/json",
            )
            if not changed:
                logger.warning("source_checkpoint_contended", key=identity.key)
        except Exception as error:
            logger.warning(
                "source_checkpoint_write_failed", key=identity.key, reason=f"{type(error).__name__}: {error}"
            )


def _decode_checkpoint(
    payload: bytes, *, identity: SourceCheckpointIdentity, now: datetime, require_fresh: bool = True
) -> SourceCheckpoint:
    """Verify the envelope and source digest separately; neither substitutes for provider validation."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("checkpoint clock must be timezone-aware")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict) or set(decoded) != {"checkpoint", "sha256"}:
        raise ValueError("invalid source checkpoint envelope")
    value = decoded["checkpoint"]
    if not isinstance(value, dict) or sha256_digest(canonical_json(value)) != decoded["sha256"]:
        raise ValueError("source checkpoint envelope checksum mismatch")
    expected_keys = {*identity.as_dict(), "retrieved_at", "response_sha256", "response_bytes", "body_base64"}
    if set(value) != expected_keys or any(value.get(key) != expected for key, expected in identity.as_dict().items()):
        raise ValueError("source checkpoint identity mismatch")
    stamp, encoded = value["retrieved_at"], value["body_base64"]
    if not isinstance(stamp, str) or not isinstance(encoded, str):
        raise ValueError("source checkpoint has invalid clock or body")
    retrieved_at = datetime.fromisoformat(stamp)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("source checkpoint retrieval instant is naive")
    age = now - retrieved_at
    if require_fresh and (age < timedelta(0) or age > CHECKPOINT_MAX_AGE):
        raise ValueError("source checkpoint is future-dated or expired")
    body = base64.b64decode(encoded, validate=True)
    if not body or len(body) > CHECKPOINT_MAX_BODY_BYTES or value["response_bytes"] != len(body):
        raise ValueError("source checkpoint body length mismatch")
    if sha256_digest(body) != value["response_sha256"]:
        raise ValueError("source checkpoint body checksum mismatch")
    return SourceCheckpoint(body=body, retrieved_at=retrieved_at)
