"""The exported expert label plane: its pinned schema, its key, and its refusals."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from plantgeo_ml_service.pipeline.expert_labels import (
    EXPERT_LABEL_SCHEMA,
    ExpertLabelReadError,
    expert_label_part_path,
    read_expert_labels,
    validate_release_identifier,
)
from plantgeo_ml_service.pipeline.object_store import InMemoryObjectStoreBackend, ObjectStore

RELEASE = "literature-labels-2026-08-14"
MOMENT = datetime(2026, 8, 14, 9, 0, 0, tzinfo=UTC)
ENVELOPE = {"mean_annual_precipitation_mm": [200, 400], "aridity": "semi-arid"}


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "label_key": "species_fit:artemisia-tridentata:0123456789abcdef",
        "release_key": RELEASE,
        "source_key": "doi:10.1000/example",
        "label_kind": "species_fit",
        "subject": "Artemisia tridentata",
        "subject_normalized": "artemisia tridentata",
        "outcome": "fit",
        "condition_envelope": json.dumps(ENVELOPE, sort_keys=True),
        "envelope_checksum": "a" * 64,
        "rationale": "Established on semi-arid sites in the cited trial.",
        "supporting_quote": None,
        "confidence": "high",
        "confidence_weight": 0.9,
        "harvest_slice": "sagebrush-steppe",
        "citation_check_refuted": False,
        "citation_check_doi_resolves": True,
        "citation_check_reason": "DOI resolved and the finding is stated in the abstract.",
        "review_state": "agent_reviewed",
        "review_note": None,
        "reviewed_by": "literature-label-harvest/adversarial-citation-verifier",
        "reviewed_at": MOMENT,
        "owner_signature_reference": None,
        "label_checksum": "b" * 64,
        "created_at": MOMENT,
    }
    row.update(overrides)
    return row


def _store_with(table: pa.Table, *, relative_path: str) -> ObjectStore:
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    store.backend.put(store.absolute_key(relative_path), sink.getvalue(), content_type="application/x-parquet")
    return store


def test_the_release_key_is_one_part_file_under_the_ml_prefix() -> None:
    assert expert_label_part_path(RELEASE) == f"ml/labels/expert/{RELEASE}/part-0000.parquet"


@pytest.mark.parametrize("release", ["", " ", "..", "a/b", "a\\b"])
def test_a_release_identifier_that_could_traverse_is_refused(release: str) -> None:
    with pytest.raises(ExpertLabelReadError):
        validate_release_identifier(release)


def test_an_exported_release_reads_back_with_its_envelope_decoded() -> None:
    table = pa.Table.from_pylist([_row()], schema=EXPERT_LABEL_SCHEMA)
    store = _store_with(table, relative_path=expert_label_part_path(RELEASE))

    release = read_expert_labels(store, RELEASE)

    assert len(release.labels) == 1
    label = release.labels[0]
    assert label.condition_envelope == ENVELOPE
    assert label.confidence_weight == pytest.approx(0.9)
    assert len(release.sha256) == 64  # noqa: PLR2004


def test_an_unexported_release_refuses_and_names_the_verb_that_would_produce_it() -> None:
    store = ObjectStore(backend=InMemoryObjectStoreBackend())

    with pytest.raises(ExpertLabelReadError, match="export-expert-labels"):
        read_expert_labels(store, RELEASE)


def test_a_file_written_under_a_different_schema_is_refused_rather_than_coerced() -> None:
    off_schema = pa.table({"label_key": ["x"], "outcome": ["fit"]})
    store = _store_with(off_schema, relative_path=expert_label_part_path(RELEASE))

    with pytest.raises(ExpertLabelReadError, match="pinned schema"):
        read_expert_labels(store, RELEASE)


def test_an_outcome_that_does_not_belong_to_its_kind_is_refused() -> None:
    """`effective` is a strategy outcome; a species-fit label carrying it is a corrupted export."""
    table = pa.Table.from_pylist([_row(outcome="effective")], schema=EXPERT_LABEL_SCHEMA)
    store = _store_with(table, relative_path=expert_label_part_path(RELEASE))

    with pytest.raises(ExpertLabelReadError, match="allowed"):
        read_expert_labels(store, RELEASE)


def test_an_unknown_confidence_band_is_refused_because_it_has_no_sample_weight() -> None:
    table = pa.Table.from_pylist([_row(confidence="certain")], schema=EXPERT_LABEL_SCHEMA)
    store = _store_with(table, relative_path=expert_label_part_path(RELEASE))

    with pytest.raises(ExpertLabelReadError, match="confidence"):
        read_expert_labels(store, RELEASE)


def test_an_undecodable_envelope_is_refused() -> None:
    table = pa.Table.from_pylist([_row(condition_envelope="{not json")], schema=EXPERT_LABEL_SCHEMA)
    store = _store_with(table, relative_path=expert_label_part_path(RELEASE))

    with pytest.raises(ExpertLabelReadError, match="condition_envelope"):
        read_expert_labels(store, RELEASE)


def test_the_envelope_is_carried_as_text_so_the_term_vocabulary_stays_open() -> None:
    """An Arrow struct would freeze today's seven envelope terms into the file's own schema."""
    assert EXPERT_LABEL_SCHEMA.field("condition_envelope").type == pa.string()
