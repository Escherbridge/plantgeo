"""Conditional publication: order, idempotent replay, and an interruption that moves no pointer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.pipeline.direct.botanical_occurrences.forward import (
    ArchiveRequest,
    BotanicalForwardConfig,
    run_botanical_occurrences_forward,
)
from agri_data_service.pipeline.direct.botanical_occurrences.publish import (
    COMPLETION_MARKER,
    LocalPublicationTarget,
    generation_prefix,
    pointer_path,
    read_pointer,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class RecordingTarget:
    """A target that remembers write ORDER, because the order is the recovery contract."""

    inner: LocalPublicationTarget
    writes: list[str] = field(default_factory=list)
    fail_before: str | None = None

    def read_bytes(self, relative_path: str) -> bytes | None:
        return self.inner.read_bytes(relative_path)

    def write_bytes(self, relative_path: str, payload: bytes) -> None:
        if self.fail_before is not None and relative_path.endswith(self.fail_before):
            raise RuntimeError(f"simulated interruption before {relative_path}")
        self.writes.append(relative_path)
        self.inner.write_bytes(relative_path, payload)

    def exists(self, relative_path: str) -> bool:
        return self.inner.exists(relative_path)


def _turn(archive: Path, target: Any, tmp_path: Path) -> dict[str, Any]:  # noqa: ANN401 - any PublicationTarget
    return run_botanical_occurrences_forward(
        BotanicalForwardConfig(
            archives=(ArchiveRequest(path=archive, collection_key="test:COLL:vascular", source_version="1.0"),),
            root=str(tmp_path / "publication"),
            run_id="publish-test",
            supports=("grid-0.25",),
            envelope=(-123.0, 47.0, -122.0, 48.0),
            target=target,
        )
    )


def test_a_turn_publishes_every_artifact_and_advances_the_pointer(valid_archive: Path, tmp_path: Path) -> None:
    target = LocalPublicationTarget(tmp_path / "publication")
    report = _turn(valid_archive, target, tmp_path)
    assert report["outcome"] == "published"
    prefix = generation_prefix(report["release_set_id"])
    for artifact in (
        "releases/part-0000.parquet",
        "raw/part-0000.parquet",
        "identifications/part-0000.parquet",
        "occurrences/part-0000.parquet",
        "nonspatial/part-0000.parquet",
        "associations/part-0000.parquet",
        "support/grid-0.25/cells.parquet",
        "summary/grid-0.25/cell_taxon.parquet",
        "manifest.json",
        COMPLETION_MARKER,
    ):
        assert target.exists(f"{prefix}/{artifact}"), artifact
    assert read_pointer(target) == report["release_set_id"]


def test_the_completion_marker_is_written_after_every_other_artifact(valid_archive: Path, tmp_path: Path) -> None:
    target = RecordingTarget(LocalPublicationTarget(tmp_path / "publication"))
    report = _turn(valid_archive, target, tmp_path)
    prefix = generation_prefix(report["release_set_id"])
    marker_index = target.writes.index(f"{prefix}/{COMPLETION_MARKER}")
    generation_writes = [index for index, path in enumerate(target.writes) if path.startswith(prefix)]
    assert marker_index == max(generation_writes)
    assert target.writes[-1] == pointer_path(), "the pointer moves only after the marker is durable"


def test_replaying_the_same_release_set_is_a_no_op(valid_archive: Path, tmp_path: Path) -> None:
    target = LocalPublicationTarget(tmp_path / "publication")
    first = _turn(valid_archive, target, tmp_path)
    second = _turn(valid_archive, target, tmp_path)
    assert first["release_set_id"] == second["release_set_id"], "identity is a function of inputs, not of the clock"
    assert second["outcome"] == "idempotent_noop"
    assert second["objects_written"] == []


def test_an_interrupted_publication_leaves_the_pointer_where_it_was(valid_archive: Path, tmp_path: Path) -> None:
    root = tmp_path / "publication"
    completed = LocalPublicationTarget(root)
    first = _turn(valid_archive, completed, tmp_path)
    interrupting = RecordingTarget(LocalPublicationTarget(root), fail_before=COMPLETION_MARKER)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        run_botanical_occurrences_forward(
            BotanicalForwardConfig(
                archives=(
                    ArchiveRequest(path=valid_archive, collection_key="other:COLL:vascular", source_version="2.0"),
                ),
                root=str(root),
                supports=("grid-0.25",),
                envelope=(-123.0, 47.0, -122.0, 48.0),
                target=interrupting,
            )
        )
    assert read_pointer(completed) == first["release_set_id"], "an unfinished generation never becomes current"


def test_a_turn_with_no_archive_reports_no_window_rather_than_publishing_nothing(tmp_path: Path) -> None:
    report = run_botanical_occurrences_forward(BotanicalForwardConfig(root=str(tmp_path / "publication")))
    assert report["outcome"] == "no_window"
    assert report["release_set_id"] is None


def test_a_turn_whose_only_archive_is_quarantined_publishes_nothing(traversal_archive: Path, tmp_path: Path) -> None:
    target = LocalPublicationTarget(tmp_path / "publication")
    report = _turn(traversal_archive, target, tmp_path)
    assert report["outcome"] == "blocked"
    assert report["release_set_id"] is None
    assert read_pointer(target) is None
