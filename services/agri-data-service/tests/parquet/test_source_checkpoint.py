"""Source checkpoints preserve original evidence while rejecting stale or foreign reuse."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.parquet.source_checkpoint import (
    CHECKPOINT_MAX_AGE,
    CHECKPOINT_MAX_BODY_BYTES,
    SourceCheckpoint,
    SourceCheckpointIdentity,
    SourceResponseCheckpoints,
)
from tests.parquet.test_availability_index import MemoryAvailabilityStorage

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
BODY = b'{ "source": "original spacing is evidence" }\n'
IDENTITY = SourceCheckpointIdentity("test-provider-v1", sha256_digest(b"support"), "2026-06-26", "https://source/day")


def test_roundtrip_preserves_original_bytes_and_source_retrieval_instant() -> None:
    storage = MemoryAvailabilityStorage()
    cache = SourceResponseCheckpoints(storage)
    checkpoint = SourceCheckpoint(BODY, NOW)
    cache.write(IDENTITY, checkpoint, response_sha256=sha256_digest(BODY))

    assert cache.read(IDENTITY, now=NOW + timedelta(days=1)) == checkpoint
    value = json.loads(storage.objects[IDENTITY.key].payload)["checkpoint"]
    assert value["response_sha256"] == sha256_digest(BODY)
    assert base64.b64decode(value["body_base64"]) == BODY


@pytest.mark.parametrize("offset", [timedelta(microseconds=-1), CHECKPOINT_MAX_AGE + timedelta(microseconds=1)])
def test_future_and_expired_responses_are_not_reused(offset: timedelta) -> None:
    cache = SourceResponseCheckpoints(MemoryAvailabilityStorage())
    cache.write(IDENTITY, SourceCheckpoint(BODY, NOW), response_sha256=sha256_digest(BODY))

    assert cache.read(IDENTITY, now=NOW + offset) is None
    assert cache.read(IDENTITY, now=NOW + CHECKPOINT_MAX_AGE) is not None


@pytest.mark.parametrize("field", ["support_sha256", "request_url", "day", "provider"])
def test_a_checkpoint_cannot_be_reused_for_a_different_identity(field: str) -> None:
    cache = SourceResponseCheckpoints(MemoryAvailabilityStorage())
    cache.write(IDENTITY, SourceCheckpoint(BODY, NOW), response_sha256=sha256_digest(BODY))

    assert cache.read(replace(IDENTITY, **{field: "different"}), now=NOW) is None


def test_corrupt_body_is_rejected_even_if_the_envelope_checksum_was_recomputed() -> None:
    storage = MemoryAvailabilityStorage()
    cache = SourceResponseCheckpoints(storage)
    cache.write(IDENTITY, SourceCheckpoint(BODY, NOW), response_sha256=sha256_digest(BODY))
    value = json.loads(storage.objects[IDENTITY.key].payload)["checkpoint"]
    changed_body = BODY.replace(b"original", b"modified")
    value["body_base64"] = base64.b64encode(changed_body).decode()
    value["response_bytes"] = len(changed_body)
    storage.seed(
        IDENTITY.key, canonical_json({"checkpoint": value, "sha256": sha256_digest(canonical_json(value))}).encode()
    )

    assert cache.read(IDENTITY, now=NOW) is None


def test_a_slow_writer_cannot_replace_a_newer_response_with_older_evidence() -> None:
    storage = MemoryAvailabilityStorage()
    cache = SourceResponseCheckpoints(storage)
    later = SourceCheckpoint(b"later", NOW + timedelta(minutes=1))
    cache.write(IDENTITY, later, response_sha256=sha256_digest(later.body))
    original = storage.objects[IDENTITY.key]

    cache.write(IDENTITY, SourceCheckpoint(BODY, NOW), response_sha256=sha256_digest(BODY))

    assert storage.objects[IDENTITY.key] == original
    assert cache.read(IDENTITY, now=later.retrieved_at) == later


def test_a_refreshed_response_replaces_expired_bytes_at_the_same_key() -> None:
    storage = MemoryAvailabilityStorage()
    cache = SourceResponseCheckpoints(storage)
    cache.write(IDENTITY, SourceCheckpoint(BODY, NOW), response_sha256=sha256_digest(BODY))
    later = SourceCheckpoint(b"new", NOW + CHECKPOINT_MAX_AGE + timedelta(seconds=1))

    cache.write(IDENTITY, later, response_sha256=sha256_digest(later.body))

    assert list(storage.objects) == [IDENTITY.key]
    assert cache.read(IDENTITY, now=later.retrieved_at) == later


def test_mismatched_original_digest_and_oversize_body_never_create_a_checkpoint() -> None:
    storage = MemoryAvailabilityStorage()
    cache = SourceResponseCheckpoints(storage)
    cache.write(IDENTITY, SourceCheckpoint(BODY, NOW), response_sha256=sha256_digest(b"different"))
    oversized = b"x" * (CHECKPOINT_MAX_BODY_BYTES + 1)
    cache.write(IDENTITY, SourceCheckpoint(oversized, NOW), response_sha256=sha256_digest(oversized))

    assert storage.objects == {}


def test_a_cas_race_retains_the_concurrent_winners_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = MemoryAvailabilityStorage()
    cache = SourceResponseCheckpoints(storage)
    cache.write(IDENTITY, SourceCheckpoint(BODY, NOW), response_sha256=sha256_digest(BODY))
    original = storage.objects[IDENTITY.key].payload
    winner = SourceCheckpoint(b"winner", NOW + timedelta(hours=1))
    cache.write(IDENTITY, winner, response_sha256=sha256_digest(winner.body))
    winning_payload = storage.objects[IDENTITY.key].payload
    storage.seed(IDENTITY.key, original)
    compare_and_swap = storage.compare_and_swap

    def racing_swap(key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        storage.seed(key, winning_payload)
        return compare_and_swap(key, payload, expected_etag=expected_etag, content_type=content_type)

    monkeypatch.setattr(storage, "compare_and_swap", racing_swap)
    loser = SourceCheckpoint(b"loser", NOW + timedelta(minutes=1))
    cache.write(IDENTITY, loser, response_sha256=sha256_digest(loser.body))

    assert cache.read(IDENTITY, now=winner.retrieved_at) == winner
