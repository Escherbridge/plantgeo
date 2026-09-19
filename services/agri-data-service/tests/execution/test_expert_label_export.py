"""The one-time expert-label export: row assembly, the pinned shape, and the refusal to overwrite.

No database and no bucket. The two halves worth testing are pure: turning rows into the pinned Arrow
table, and deciding what to do when the object already exists. The SQL that produces those rows is
covered by `test_sql_tree_conventions.py` for its shape and by a live run for its content; what a
unit test can prove here is that a release is assembled the same way twice and that an already-
exported release is never silently replaced.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.execution.expert_label_export import (
    ExpertLabelExportRefusal,
    build_expert_label_table,
    encode_expert_label_table,
    expert_label_part_path,
    expert_label_receipt_path,
    export_expert_labels,
    sha256_of,
    validate_release_identifier,
    write_expert_label_export,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.expert_labels import (
    EXPERT_LABEL_COLUMNS,
    EXPERT_LABEL_EXPORT_SCHEMA,
    MAX_EXPERT_LABELS,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

RELEASE = "pnw-fire-2026-09"
CREATED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
EXPORTED_AT = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)

#: The twenty-four columns the ML service's reader pins
#: (`plantgeo_ml_service/pipeline/expert_labels.py::EXPERT_LABEL_SCHEMA`). Hand-spelled here rather
#: than derived from the schema object, because deriving it would assert the schema equals itself.
#: This is the agri end of the pin, and it is READABLE rather than merely equal: it says which
#: twenty-four columns, in which order. The EQUALITY against the other service's own module is
#: proved separately by `tests/parquet/test_ml_schema_parity.py`, which reads that module off disk
#: in a monorepo checkout and skips in the Docker image where the sibling tree is absent.
EXPECTED_COLUMNS = (
    "label_key",
    "release_key",
    "source_key",
    "label_kind",
    "subject",
    "subject_normalized",
    "outcome",
    "condition_envelope",
    "envelope_checksum",
    "rationale",
    "supporting_quote",
    "confidence",
    "confidence_weight",
    "harvest_slice",
    "citation_check_refuted",
    "citation_check_doi_resolves",
    "citation_check_reason",
    "review_state",
    "review_note",
    "reviewed_by",
    "reviewed_at",
    "owner_signature_reference",
    "label_checksum",
    "created_at",
)


class InMemoryBackend:
    """An `ObjectStoreBackend` that keeps every object in a dict; no network, no credentials."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        self.objects[key] = payload
        self.content_types[key] = content_type

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.content_types.pop(key, None)

    def list_objects(self, prefix: str) -> Iterator[Any]:
        raise NotImplementedError(f"this test never lists ({prefix})")

    def size_of(self, key: str) -> int | None:
        payload = self.objects.get(key)
        return None if payload is None else len(payload)


def _label_row(label_key: str, **overrides: Any) -> dict[str, Any]:
    """One plausible `agri.expert_label` row joined to its release and source keys."""
    row: dict[str, Any] = {
        "label_key": label_key,
        "release_key": RELEASE,
        "source_key": "doi:10.1000/example",
        "label_kind": "species_fit",
        "subject": "Pseudotsuga menziesii",
        "subject_normalized": "pseudotsuga menziesii",
        "outcome": "fit",
        # Deliberately NOT in sorted key order, so the canonicalisation has something to do.
        "condition_envelope": {"soil_ph": 6.1, "elevation_m": 900},
        "envelope_checksum": "a" * 64,
        "rationale": "Established across the sampled elevation band.",
        "supporting_quote": None,
        "confidence": "high",
        "confidence_weight": 1.0,
        "harvest_slice": "pnw-conifers",
        "citation_check_refuted": False,
        "citation_check_doi_resolves": True,
        "citation_check_reason": "doi_resolved",
        "review_state": "agent_reviewed",
        "review_note": None,
        "reviewed_by": "agent",
        "reviewed_at": REVIEWED_AT,
        "owner_signature_reference": None,
        "label_checksum": "b" * 64,
        "created_at": CREATED_AT,
    }
    row.update(overrides)
    return row


def test_the_pinned_schema_is_the_twenty_four_columns_the_ml_reader_refuses_to_do_without() -> None:
    """A dropped or renamed column makes every exported release unreadable, not merely lossy."""
    assert EXPERT_LABEL_COLUMNS == EXPECTED_COLUMNS
    assert tuple(EXPERT_LABEL_EXPORT_SCHEMA.names) == EXPECTED_COLUMNS


def test_rows_become_the_pinned_table_with_the_envelope_as_canonical_json() -> None:
    """The envelope is TEXT in the file, and canonical text, so identical content gives identical bytes."""
    table = build_expert_label_table([_label_row("label-1")], release=RELEASE)

    assert table.schema.equals(EXPERT_LABEL_EXPORT_SCHEMA)
    assert table.num_rows == 1
    assert table.column("condition_envelope").to_pylist() == ['{"elevation_m":900,"soil_ph":6.1}']


def test_an_envelope_already_rendered_as_text_reaches_the_same_canonical_form() -> None:
    """A driver that hands back `jsonb` as a string must not produce a different file."""
    as_mapping = build_expert_label_table([_label_row("label-1")], release=RELEASE)
    as_text = build_expert_label_table(
        [_label_row("label-1", condition_envelope=json.dumps({"soil_ph": 6.1, "elevation_m": 900}))],
        release=RELEASE,
    )

    assert encode_expert_label_table(as_mapping) == encode_expert_label_table(as_text)


def test_rows_are_sorted_by_label_key_so_two_exports_agree_byte_for_byte() -> None:
    """`label_key` is unique across the plane, so the sort leaves no ties to reorder between runs."""
    forward = build_expert_label_table([_label_row("label-a"), _label_row("label-b")], release=RELEASE)
    reversed_input = build_expert_label_table([_label_row("label-b"), _label_row("label-a")], release=RELEASE)

    assert forward.column("label_key").to_pylist() == ["label-a", "label-b"]
    assert encode_expert_label_table(forward) == encode_expert_label_table(reversed_input)


def test_an_empty_release_is_refused_rather_than_exported_as_an_empty_file() -> None:
    """A reader cannot tell an empty export from a release that was never exported at all."""
    with pytest.raises(ExpertLabelExportRefusal, match="holds no labels"):
        build_expert_label_table([], release=RELEASE)


def test_a_release_past_the_ceiling_is_refused_because_the_reader_refuses_it_too() -> None:
    """Writing a file the consumer rejects publishes an unreadable object, which is worse than failing."""
    rows = [_label_row(f"label-{index:05d}") for index in range(MAX_EXPERT_LABELS + 1)]

    with pytest.raises(ExpertLabelExportRefusal, match="more than"):
        build_expert_label_table(rows, release=RELEASE)


def test_duplicate_label_keys_are_refused_because_the_sort_would_not_be_total() -> None:
    """Two rows sharing the key make row order arbitrary, and arbitrary order is not reproducible."""
    with pytest.raises(ExpertLabelExportRefusal, match="duplicate label keys"):
        build_expert_label_table([_label_row("label-1"), _label_row("label-1")], release=RELEASE)


def test_a_missing_column_is_named_rather_than_written_as_a_null() -> None:
    """A query that lost a column must fail here, where the operator can read which one."""
    row = _label_row("label-1")
    del row["harvest_slice"]

    with pytest.raises(ExpertLabelExportRefusal, match="harvest_slice"):
        build_expert_label_table([row], release=RELEASE)


def test_a_release_identifier_that_is_not_one_object_key_segment_is_refused() -> None:
    """The identifier arrives from a command line and becomes a path segment."""
    assert validate_release_identifier(RELEASE) == RELEASE
    for bad in ("", " ", ".", "..", "a/b", "a\\b"):
        with pytest.raises(ExpertLabelExportRefusal):
            validate_release_identifier(bad)


def test_the_export_writes_the_part_file_and_a_receipt_beside_it() -> None:
    """The receipt is what an operator reads to learn what was written without opening Parquet."""
    store = ObjectStore(InMemoryBackend())
    table = build_expert_label_table([_label_row("label-1")], release=RELEASE)
    payload = encode_expert_label_table(table)

    export = write_expert_label_export(
        store, payload, release=RELEASE, label_count=table.num_rows, exported_at=EXPORTED_AT
    )

    assert export.written is True
    assert export.part_path == f"ml/labels/expert/{RELEASE}/part-0000.parquet"
    assert export.receipt_path == f"ml/labels/expert/{RELEASE}/receipt.json"
    assert export.sha256 == sha256_of(payload)
    assert store.read_ml_object(export.part_path) == payload
    receipt_bytes = store.read_ml_object(export.receipt_path)
    assert receipt_bytes is not None
    receipt = json.loads(receipt_bytes)
    assert receipt["release"] == RELEASE
    assert receipt["label_count"] == 1
    assert receipt["sha256"] == export.sha256
    assert receipt["exported_at"] == EXPORTED_AT.isoformat()
    # And the file that landed still carries the pinned schema, which is what the reader compares.
    assert pq.read_table(io.BytesIO(payload)).schema.remove_metadata().equals(EXPERT_LABEL_EXPORT_SCHEMA)


def test_re_exporting_identical_bytes_is_a_no_op_rather_than_a_rewrite() -> None:
    """The same labels read twice are the same release; the store already agrees."""
    store = ObjectStore(InMemoryBackend())
    payload = encode_expert_label_table(build_expert_label_table([_label_row("label-1")], release=RELEASE))

    first = write_expert_label_export(store, payload, release=RELEASE, label_count=1, exported_at=EXPORTED_AT)
    second = write_expert_label_export(store, payload, release=RELEASE, label_count=1, exported_at=EXPORTED_AT)

    assert first.written is True
    assert second.written is False
    assert second.sha256 == first.sha256


def test_re_exporting_different_bytes_under_the_same_release_is_refused() -> None:
    """A model artifact pins its training set by this digest; replacing it would move that ground."""
    store = ObjectStore(InMemoryBackend())
    original = encode_expert_label_table(build_expert_label_table([_label_row("label-1")], release=RELEASE))
    changed = encode_expert_label_table(
        build_expert_label_table([_label_row("label-1", outcome="marginal")], release=RELEASE)
    )
    write_expert_label_export(store, original, release=RELEASE, label_count=1, exported_at=EXPORTED_AT)

    with pytest.raises(ExpertLabelExportRefusal, match="already holds different bytes"):
        write_expert_label_export(store, changed, release=RELEASE, label_count=1, exported_at=EXPORTED_AT)

    assert store.read_ml_object(expert_label_part_path(RELEASE)) == original


def test_an_object_outside_the_ml_prefix_is_refused_by_the_store_itself() -> None:
    """The two ml/ writers are scoped so no caller can put a key beside a lane's partitions."""
    store = ObjectStore(InMemoryBackend())

    for bad in ("layer=signal/x.parquet", "ml/../layer=signal/x.parquet", "/ml/x.json"):
        with pytest.raises(ValueError, match=r"ml/|relative POSIX|traversing"):
            store.write_ml_object(b"{}", relative_path=bad, content_type="application/json")


def test_the_receipt_path_sits_beside_the_part_file_under_the_same_release_segment() -> None:
    """One release, one directory: an operator listing the prefix sees the file and its receipt."""
    assert expert_label_receipt_path(RELEASE).rsplit("/", 1)[0] == expert_label_part_path(RELEASE).rsplit("/", 1)[0]


@pytest.mark.parametrize(
    "bad_prefix",
    ["labels/expert", "ml/../layer=signal", "/ml/labels/expert", "ml\\labels\\expert", "ml/./labels"],
)
def test_a_prefix_outside_ml_is_refused_by_name_before_anything_is_read(bad_prefix: str) -> None:
    """The operator mistyped a flag, so the refusal names the flag -- not an object key two layers down.

    The store enforces the same rule, but only at WRITE time: a dry run would never reach it, and an
    apply run would only hear about it after reading and encoding the whole release, as a bare
    `ValueError` about a key the operator never typed.
    """
    with pytest.raises(ExpertLabelExportRefusal, match="--prefix"):
        expert_label_part_path(RELEASE, prefix=bad_prefix)
    with pytest.raises(ExpertLabelExportRefusal, match="--prefix"):
        expert_label_receipt_path(RELEASE, prefix=bad_prefix)


def test_a_scratch_prefix_under_ml_is_allowed_because_that_is_how_a_rehearsal_avoids_the_real_one() -> None:
    """The rule is the `ml/` boundary, not one literal path; refusing every alternative would kill the rehearsal."""
    assert expert_label_part_path(RELEASE, prefix="ml/labels/scratch").startswith("ml/labels/scratch/")


def test_the_export_command_is_a_dry_run_unless_apply_is_passed() -> None:
    """The write is immutable, so the default has to be the reversible one.

    Asserted against the command's own parameters rather than by invoking it, because invoking it
    would need a database and a bucket. What must hold is that the writing path is opt-in: a flag
    named `--apply` exists, defaults to False, and no `--dry-run` flag remains to be forgotten.
    """
    flags = {parameter.name: parameter for parameter in export_expert_labels.params}

    assert "apply_" in flags, "writing is opt-in through --apply"
    assert flags["apply_"].is_flag
    assert flags["apply_"].default is False
    assert "dry_run" not in flags, "two ways to say the same thing is how the safe default gets lost"
    assert "--apply" in flags["apply_"].opts
