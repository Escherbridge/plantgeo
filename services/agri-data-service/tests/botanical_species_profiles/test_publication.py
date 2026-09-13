"""Immutable Parquet publication, corruption, retry and conditional rollback contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from agri_data_service.pipeline.direct.botanical_species_profiles.publication import (
    CURRENT_POINTER_KEY,
    build_release,
    publish_release,
    read_current_pointer,
    read_release,
    rollback_release,
)
from agri_data_service.pipeline.direct.botanical_species_profiles.storage import LocalAvailabilityStorage
from agri_data_service.warehouse.botanical_species_profiles.contract import (
    MAX_OBJECT_BYTES,
    ProfileConflictError,
    ProfileError,
    ProfileIntegrityError,
    ProfileUnavailableError,
    TraitValue,
)
from agri_data_service.warehouse.parquet.schema import get_stream_schema
from tests.botanical_species_profiles.fixtures import IDENTITY, make_assertion, make_release_request

if TYPE_CHECKING:
    from pathlib import Path

DISTINCT_REVIEW_AND_SOURCE_RELEASES = 3
INTERRUPT_AFTER_ARTIFACTS = 2


def test_parquet_roundtrip_preserves_identity_source_review_and_unknowns(tmp_path: Path) -> None:
    storage = LocalAvailabilityStorage(tmp_path)
    bundle = build_release(make_release_request((make_assertion(),)))
    receipt = publish_release(storage, bundle, expected_pointer_etag=None)
    published = read_release(storage, bundle.release_id)
    assert published == bundle.release
    assert receipt.state == "published"
    assert published.manifest.authoring_census_state == "unavailable"
    assert published.assertions[0].authoring_row_id == "reviewed-row-7"
    assert published.taxa[0].synonyms[0].source_name_id == "S-1"
    assert published.lookup(IDENTITY) is not None
    assert published.lookup(IDENTITY.model_copy(update={"authority_version": "v2"})) is None


def test_reordering_reviewed_input_is_exactly_deterministic() -> None:
    first = make_assertion()
    second = make_assertion("assertion-2", trait="edible", value=TraitValue(boolean=False))
    left = build_release(make_release_request((first, second)))
    right = build_release(make_release_request((second, first)))
    assert left == right
    assert all(
        artifact.payload == other.payload for artifact, other in zip(left.artifacts, right.artifacts, strict=True)
    )


def test_reviewer_and_source_content_changes_produce_new_release_ids() -> None:
    request = make_release_request()
    first = build_release(request)
    reviewer_changed = build_release(request.model_copy(update={"reviewer": "different-reviewer"}))
    source_changed = build_release(
        request.model_copy(
            update={
                "sources": (request.sources[0].model_copy(update={"content_sha256": "c" * 64}),),
            }
        )
    )
    assert (
        len({first.release_id, reviewer_changed.release_id, source_changed.release_id})
        == DISTINCT_REVIEW_AND_SOURCE_RELEASES
    )


def test_idempotent_replay_adopts_exact_artifacts_and_pointer(tmp_path: Path) -> None:
    storage = LocalAvailabilityStorage(tmp_path)
    bundle = build_release(make_release_request())
    original = publish_release(storage, bundle, expected_pointer_etag=None)
    replay = publish_release(storage, bundle, expected_pointer_etag=None)
    assert replay.state == "replayed"
    assert replay.pointer_etag == original.pointer_etag


def test_competing_release_requires_current_pointer_etag_and_rollback_verifies_prior(tmp_path: Path) -> None:
    storage = LocalAvailabilityStorage(tmp_path)
    first = build_release(make_release_request())
    second = build_release(make_release_request((make_assertion(),)))
    original = publish_release(storage, first, expected_pointer_etag=None)
    with pytest.raises(ProfileConflictError, match="current botanical release changed"):
        publish_release(storage, second, expected_pointer_etag=None)
    advanced = publish_release(storage, second, expected_pointer_etag=original.pointer_etag)
    assert read_release(storage, first.release_id) == first.release
    with pytest.raises(ProfileConflictError):
        rollback_release(storage, first.release_id, expected_pointer_etag=original.pointer_etag)
    rolled_back = rollback_release(storage, first.release_id, expected_pointer_etag=advanced.pointer_etag)
    assert rolled_back.state == "rolled_back"
    pointer = read_current_pointer(storage)
    assert pointer is not None
    assert pointer[0] == first.release_id


class InterruptedStorage(LocalAvailabilityStorage):
    """Fail once after two immutable writes to model a stopped publication process."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.writes = 0
        self.interrupted = False

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        if self.writes == INTERRUPT_AFTER_ARTIFACTS and not self.interrupted:
            self.interrupted = True
            raise OSError("synthetic process interruption")
        super().put_immutable(key, payload, content_type=content_type)
        self.writes += 1


def test_interruption_before_manifest_never_advances_pointer_and_retry_recovers(tmp_path: Path) -> None:
    storage = InterruptedStorage(tmp_path)
    bundle = build_release(make_release_request())
    with pytest.raises(OSError, match="synthetic process interruption"):
        publish_release(storage, bundle, expected_pointer_etag=None)
    assert read_current_pointer(storage) is None
    with pytest.raises(ProfileUnavailableError):
        read_release(storage, bundle.release_id)
    publish_release(storage, bundle, expected_pointer_etag=None)
    assert read_release(storage, bundle.release_id) == bundle.release


def test_corrupt_immutable_bytes_are_refused_by_checksum_and_replay(tmp_path: Path) -> None:
    storage = LocalAvailabilityStorage(tmp_path)
    bundle = build_release(make_release_request())
    publish_release(storage, bundle, expected_pointer_etag=None)
    victim = bundle.artifacts[0]
    (tmp_path / victim.key).write_bytes(victim.payload + b"corruption")
    with pytest.raises(ProfileIntegrityError, match="checksum or byte count"):
        read_release(storage, bundle.release_id)
    with pytest.raises(ProfileConflictError, match="different bytes"):
        publish_release(storage, bundle, expected_pointer_etag=None)


def test_incomplete_bundle_never_publishes_a_pointer(tmp_path: Path) -> None:
    storage = LocalAvailabilityStorage(tmp_path)
    bundle = build_release(make_release_request())
    with pytest.raises(ProfileError, match="all five exact"):
        publish_release(storage, replace(bundle, artifacts=bundle.artifacts[:-1]), expected_pointer_etag=None)
    assert read_current_pointer(storage) is None


@pytest.mark.parametrize("key", ["../outside", "/absolute", "nested/../../outside", "C:/outside", "nested\\outside"])
def test_local_storage_refuses_path_escape(tmp_path: Path, key: str) -> None:
    with pytest.raises(ProfileError, match=r"canonical relative|escapes"):
        LocalAvailabilityStorage(tmp_path).put_immutable(key, b"bytes", content_type="application/octet-stream")


def test_local_storage_enforces_read_ceiling_and_immutable_equality(tmp_path: Path) -> None:
    storage = LocalAvailabilityStorage(tmp_path)
    storage.put_immutable("test/object", b"original", content_type="application/octet-stream")
    with pytest.raises(ProfileError, match="byte ceiling"):
        storage.read("test/object", max_bytes=1)
    with pytest.raises(ProfileConflictError):
        storage.put_immutable("test/object", b"changed", content_type="application/octet-stream")
    with pytest.raises(ProfileError, match="bounded byte ceiling"):
        storage.read("test/object", max_bytes=MAX_OBJECT_BYTES + 1)


def test_two_local_compare_and_swaps_allow_only_one_winner(tmp_path: Path) -> None:
    storage = LocalAvailabilityStorage(tmp_path)

    def attempt(payload: bytes) -> bool:
        return storage.compare_and_swap("pointer.json", payload, expected_etag=None, content_type="application/json")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(attempt, (b"one", b"two")))
    assert results.count(True) == 1
    assert results.count(False) == 1


def test_missing_pinned_release_ignores_other_current_release(tmp_path: Path) -> None:
    storage = LocalAvailabilityStorage(tmp_path)
    existing = build_release(make_release_request())
    publish_release(storage, existing, expected_pointer_etag=None)
    with pytest.raises(ProfileUnavailableError):
        read_release(storage, "bspf-" + "f" * 64)
    assert storage.read(CURRENT_POINTER_KEY, max_bytes=4096) is not None


def test_product_schema_is_autoloadable_by_its_exact_singular_slug() -> None:
    schema = get_stream_schema("botanical-species-profile")
    assert schema.name == "botanical-species-profile"
    assert schema.column_names == ("identity", "accepted_name", "sections")
