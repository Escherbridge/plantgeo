"""The checksum-bound current pointer: it resolves, and it fails closed on every way it can break.

No network and no bucket: every case publishes into a `LocalPublicationTarget` through the real
writer, then corrupts exactly one thing and asserts the reason the reader names.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from agri_data_service.pipeline.direct.botanical_occurrences.forward import (
    ArchiveRequest,
    BotanicalForwardConfig,
    run_botanical_occurrences_forward,
)
from agri_data_service.pipeline.direct.botanical_occurrences.pointer import (
    LATEST_POINTER_KIND,
    LEGACY_POINTER_KIND,
    POINTER_SCHEMA_VERSION,
    SHA256_HEX_LENGTH,
    BotanicalPointerMalformedError,
    manifest_digest,
    parse_latest_pointer,
)
from agri_data_service.pipeline.direct.botanical_occurrences.publish import (
    COMPLETION_MARKER,
    LocalPublicationTarget,
    advance_latest_pointer,
    generation_prefix,
    latest_pointer_path,
    pointer_path,
)
from agri_data_service.planes.botanical_occurrences import read_current_botanical_release
from tests.direct.botanical_occurrences.conftest import default_members, write_archive

if TYPE_CHECKING:
    from pathlib import Path

ENVELOPE = (-123.0, 47.0, -122.0, 48.0)


@pytest.fixture
def published(tmp_path: Path) -> tuple[LocalPublicationTarget, str]:
    """One published generation, written by the real publisher so the pointer is the real one."""
    archive = write_archive(tmp_path / "valid.zip", default_members())
    target = LocalPublicationTarget(tmp_path / "publication")
    report = run_botanical_occurrences_forward(
        BotanicalForwardConfig(
            archives=(ArchiveRequest(path=archive, collection_key="test:COLL:vascular", source_version="1.0"),),
            root=str(tmp_path / "publication"),
            supports=("grid-0.25", "grid-0.05"),
            envelope=ENVELOPE,
            target=target,
        )
    )
    assert report["outcome"] == "published"
    return target, report["release_set_id"]


def _pointer_file(target: LocalPublicationTarget) -> Path:
    return target.root / latest_pointer_path()


def test_the_pointer_resolves_to_its_generation_with_checksum_provenance(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, release_set_id = published
    answer = read_current_botanical_release(target=target)
    assert answer["state"] == "current"
    assert answer["release_set_id"] == release_set_id
    assert answer["generation_id"] == release_set_id
    assert answer["pointer_schema_version"] == POINTER_SCHEMA_VERSION
    assert len(str(answer["manifest_sha256"])) == SHA256_HEX_LENGTH
    assert answer["manifest_key"] == f"{generation_prefix(release_set_id)}/manifest.json"


def test_a_lane_that_never_published_is_missing_not_empty(tmp_path: Path) -> None:
    """`pointer_missing` is a governed absence of a POINTER, and says nothing about any generation."""
    answer = read_current_botanical_release(target=LocalPublicationTarget(tmp_path / "empty"))
    assert answer["state"] == "unavailable"
    assert answer["reason"] == "pointer_missing"


def test_a_bucket_carrying_only_the_legacy_pointer_is_bridged_and_says_so(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    """Bridge-then-cut: a pre-4a bucket keeps serving, and the answer NAMES the weaker pointer.

    The digest on a bridged answer is computed from the manifest bytes this read fetched, not read
    off a pointer that never bound one -- so the provenance describes what was actually served.
    """
    target, release_set_id = published
    _pointer_file(target).unlink()
    assert (target.root / pointer_path()).is_file()

    answer = read_current_botanical_release(target=target)

    assert answer["state"] == "current"
    assert answer["generation_id"] == release_set_id
    assert answer["pointer_kind"] == LEGACY_POINTER_KIND
    assert answer["pointer_written_at"] is None, "a legacy pointer records no write time; never invent one"
    manifest_bytes = (target.root / generation_prefix(release_set_id) / "manifest.json").read_bytes()
    assert answer["manifest_sha256"] == manifest_digest(manifest_bytes)


def test_the_checksum_bound_pointer_is_named_as_itself(published: tuple[LocalPublicationTarget, str]) -> None:
    """The two kinds must be distinguishable, or the bridge silently becomes the permanent path."""
    target, _ = published
    assert read_current_botanical_release(target=target)["pointer_kind"] == LATEST_POINTER_KIND


def test_a_legacy_pointer_naming_an_incomplete_generation_fails_closed(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    """The bridge re-reads the completion marker BECAUSE nothing else on this path proves it."""
    target, release_set_id = published
    _pointer_file(target).unlink()
    (target.root / generation_prefix(release_set_id) / COMPLETION_MARKER).unlink()

    answer = read_current_botanical_release(target=target)

    assert answer["state"] == "unavailable"
    assert answer["reason"] == "pointer_stale"


def test_a_legacy_pointer_naming_a_vanished_manifest_fails_closed(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, release_set_id = published
    _pointer_file(target).unlink()
    (target.root / generation_prefix(release_set_id) / "manifest.json").unlink()

    assert read_current_botanical_release(target=target)["reason"] == "pointer_stale"


def test_an_unreadable_legacy_pointer_is_malformed_not_bridged(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    target, _ = published
    _pointer_file(target).unlink()
    (target.root / pointer_path()).write_bytes(b"{")

    assert read_current_botanical_release(target=target)["reason"] == "pointer_malformed"


def test_a_broken_checksum_pointer_is_never_bridged_around(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    """A present-but-corrupt 4a pointer must not fall through to the legacy one.

    Falling through would hide exactly the corruption the checksum exists to surface, and would do
    it on a bucket where the operator believes the cut has already happened.
    """
    target, _ = published
    _pointer_file(target).write_bytes(b'{"release_set_id": "whatever"}')
    assert (target.root / pointer_path()).is_file()

    assert read_current_botanical_release(target=target)["reason"] == "pointer_malformed"


def test_a_pointer_naming_a_vanished_manifest_is_stale(published: tuple[LocalPublicationTarget, str]) -> None:
    target, release_set_id = published
    (target.root / generation_prefix(release_set_id) / "manifest.json").unlink()
    answer = read_current_botanical_release(target=target)
    assert answer["reason"] == "pointer_stale"


def test_a_manifest_replaced_under_the_pointer_is_checksum_invalid(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    """The exact hazard the digest exists for: pointer from one writer, manifest from the next."""
    target, release_set_id = published
    manifest_file = target.root / generation_prefix(release_set_id) / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["published_at"] = "2001-01-01T00:00:00+00:00"
    manifest_file.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    answer = read_current_botanical_release(target=target)
    assert answer["reason"] == "pointer_checksum_invalid"


def test_a_pointer_for_another_product_is_refused(published: tuple[LocalPublicationTarget, str]) -> None:
    target, _ = published
    document = json.loads(_pointer_file(target).read_text(encoding="utf-8"))
    document["product"] = "vegetation"
    _pointer_file(target).write_text(json.dumps(document), encoding="utf-8")
    answer = read_current_botanical_release(target=target)
    assert answer["reason"] == "pointer_malformed"


def test_an_unknown_schema_version_is_refused_rather_than_read_optimistically() -> None:
    with pytest.raises(BotanicalPointerMalformedError):
        parse_latest_pointer(
            json.dumps(
                {
                    "pointer_schema_version": POINTER_SCHEMA_VERSION + 1,
                    "product": "botanical-occurrences",
                    "generation_id": "abc",
                    "manifest_key": "botanical-occurrences/abc/manifest.json",
                    "manifest_sha256": "0" * 64,
                    "published_at": None,
                    "pointer_written_at": "2026-09-18T00:00:00+00:00",
                }
            ).encode("utf-8")
        )


def test_advancing_the_pointer_over_an_existing_generation_makes_it_resolvable(
    published: tuple[LocalPublicationTarget, str],
) -> None:
    """The production upgrade path: a bucket that predates `_LATEST.json` gets one without republishing."""
    target, release_set_id = published
    _pointer_file(target).unlink()
    assert advance_latest_pointer(target, release_set_id) is not None
    assert read_current_botanical_release(target=target)["generation_id"] == release_set_id


def test_an_incomplete_generation_is_never_pointed_at(published: tuple[LocalPublicationTarget, str]) -> None:
    target, release_set_id = published
    (target.root / generation_prefix(release_set_id) / COMPLETION_MARKER).unlink()
    assert advance_latest_pointer(target, release_set_id) is None
