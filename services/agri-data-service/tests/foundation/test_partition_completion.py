"""The completion receipt, and the one shape of it that claims nothing was written.

The zero-part receipt exists to close a hole that had no other door: a derived rung whose
generalisation dropped every base row was retracted, left with no parts and no marker, and could
therefore never present the exact required-rungs ladder `availability_index` demands -- so the day
was permanently unindexable while looking perfectly healthy to the base-tier census.

Two properties here are load-bearing and neither is obvious:

  * `derived_empty` is serialized ONLY when true. Every marker already in the bucket must
    re-serialize to the exact bytes it was read from, because
    `availability_index._verify_completion_object` compares them before it will bind a marker to an
    availability row. An always-emitted `derived_empty: false` would have failed that check for every
    day the warehouse holds.
  * An explicit `false` is REFUSED rather than tolerated, for the same reason: a marker carrying it
    could not be re-serialized to the bytes it came from.

BELT AND BRACES SINCE 2026-09-02: the flag stays in the BODY, and the claim is ALSO in the KEY --
`objectstore.write_completion_marker` routes a derived-empty receipt to `_complete.empty.json`. The
body is what a reader that already opened the marker checks; the key is what every reader that may
only LIST checks, which is the rule `layer-lanes.md` §4 imposes on gap detection. The two must agree,
and `availability_index._verify_completion_object` refuses a row whose key and body disagree.
`tests/parquet/test_partition_paths.py` pins the key space; this file pins the payload.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Final

import pytest

from agri_data_service.foundation.parquet.completion import (
    COMPLETION_PARTS_SCHEMA_VERSION,
    COMPLETION_SCHEMA_VERSION,
    DERIVED_EMPTY_FIELD,
    PARTS_FIELD,
    CompletedPart,
    PartitionCompletion,
    PartitionCompletionError,
)

NOW: Final = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
RUN_ID: Final = "run-completion"
DAY_PREFIX: Final = "layer=test-lane/kind=observed/zoom=13/year=2026/month=09/day=02"
FIRST_DIGEST: Final = "a" * 64
SECOND_DIGEST: Final = "b" * 64


def _ordinary() -> PartitionCompletion:
    return PartitionCompletion(part_count=2, row_count=40, completed_at=NOW, run_id=RUN_ID)


def _derived_empty() -> PartitionCompletion:
    return PartitionCompletion(part_count=0, row_count=0, completed_at=NOW, run_id=RUN_ID, derived_empty=True)


def test_an_ordinary_receipt_still_refuses_zero_parts_and_zero_rows() -> None:
    """The original refusal stands for every receipt that does not say WHICH claim it is making."""
    with pytest.raises(PartitionCompletionError, match="at least one part file"):
        PartitionCompletion(part_count=0, row_count=5, completed_at=NOW, run_id=RUN_ID)
    with pytest.raises(PartitionCompletionError, match="at least one row"):
        PartitionCompletion(part_count=1, row_count=0, completed_at=NOW, run_id=RUN_ID)


def test_a_derived_empty_receipt_must_actually_be_empty() -> None:
    """`derived_empty` is a statement about holding nothing; counts that disagree are a different claim."""
    with pytest.raises(PartitionCompletionError, match="holds nothing by definition"):
        PartitionCompletion(part_count=1, row_count=0, completed_at=NOW, run_id=RUN_ID, derived_empty=True)
    with pytest.raises(PartitionCompletionError, match="holds nothing by definition"):
        PartitionCompletion(part_count=0, row_count=3, completed_at=NOW, run_id=RUN_ID, derived_empty=True)


def test_an_ordinary_receipt_serializes_exactly_as_it_always_did() -> None:
    """DO NOT DELETE. Every marker in the bucket predates this field and must round-trip byte for byte.

    `availability_index._verify_completion_object` re-serializes a stored marker and refuses any
    difference, so an always-emitted `derived_empty: false` would have invalidated the whole
    warehouse's availability evidence at once.
    """
    payload = _ordinary().to_json_bytes()

    assert set(json.loads(payload)) == {"schema_version", "part_count", "row_count", "completed_at", "run_id"}
    assert PartitionCompletion.from_json_bytes(payload) == _ordinary()
    assert PartitionCompletion.from_json_bytes(payload).to_json_bytes() == payload


def test_a_derived_empty_receipt_round_trips_carrying_its_flag() -> None:
    payload = _derived_empty().to_json_bytes()
    decoded = json.loads(payload)

    assert decoded[DERIVED_EMPTY_FIELD] is True
    assert decoded["schema_version"] == COMPLETION_SCHEMA_VERSION
    assert PartitionCompletion.from_json_bytes(payload) == _derived_empty()
    assert PartitionCompletion.from_json_bytes(payload).to_json_bytes() == payload


def test_an_explicit_false_flag_is_refused_rather_than_read_as_absent() -> None:
    """It cannot be re-serialized to the bytes it was read from, so accepting it would fail later, elsewhere."""
    payload = json.dumps(
        {
            "schema_version": COMPLETION_SCHEMA_VERSION,
            "part_count": 1,
            "row_count": 1,
            "completed_at": NOW.isoformat(),
            "run_id": RUN_ID,
            DERIVED_EMPTY_FIELD: False,
        },
        sort_keys=True,
    ).encode("utf-8")

    with pytest.raises(PartitionCompletionError, match="written only when true"):
        PartitionCompletion.from_json_bytes(payload)


def test_a_stored_zero_part_marker_decodes_only_with_the_flag() -> None:
    """Zero counts alone are not the claim: without the flag they are an ordinary receipt of nothing."""
    payload = json.dumps(
        {
            "schema_version": COMPLETION_SCHEMA_VERSION,
            "part_count": 0,
            "row_count": 0,
            "completed_at": NOW.isoformat(),
            "run_id": RUN_ID,
        },
        sort_keys=True,
    ).encode("utf-8")

    with pytest.raises(PartitionCompletionError, match="at least one part file"):
        PartitionCompletion.from_json_bytes(payload)


def _recorded_parts() -> tuple[CompletedPart, ...]:
    return (
        CompletedPart(relative_path=f"{DAY_PREFIX}/part-0.parquet", row_count=25, byte_count=900, sha256=FIRST_DIGEST),
        CompletedPart(relative_path=f"{DAY_PREFIX}/part-1.parquet", row_count=15, byte_count=700, sha256=SECOND_DIGEST),
    )


def _with_parts() -> PartitionCompletion:
    return PartitionCompletion(
        part_count=2,
        row_count=40,
        completed_at=NOW,
        run_id=RUN_ID,
        parts=_recorded_parts(),
    )


def test_a_marker_recording_its_parts_declares_the_second_version_and_round_trips() -> None:
    """The version is DERIVED from the field, so a recorded marker re-serializes to its own bytes."""
    payload = _with_parts().to_json_bytes()
    decoded = json.loads(payload)

    assert decoded["schema_version"] == COMPLETION_PARTS_SCHEMA_VERSION
    assert [part["sha256"] for part in decoded[PARTS_FIELD]] == [FIRST_DIGEST, SECOND_DIGEST]
    assert PartitionCompletion.from_json_bytes(payload) == _with_parts()
    assert PartitionCompletion.from_json_bytes(payload).to_json_bytes() == payload


def test_the_two_versions_are_bound_to_the_presence_of_the_field() -> None:
    """A version and a field that disagree could not re-serialize, so neither spelling is admitted."""
    v1_with_parts = json.dumps(
        {
            "schema_version": COMPLETION_SCHEMA_VERSION,
            "part_count": 2,
            "row_count": 40,
            "completed_at": NOW.isoformat(),
            "run_id": RUN_ID,
            PARTS_FIELD: [part.to_wire() for part in _recorded_parts()],
        },
        sort_keys=True,
    ).encode("utf-8")
    v2_without_parts = json.dumps(
        {
            "schema_version": COMPLETION_PARTS_SCHEMA_VERSION,
            "part_count": 2,
            "row_count": 40,
            "completed_at": NOW.isoformat(),
            "run_id": RUN_ID,
        },
        sort_keys=True,
    ).encode("utf-8")

    with pytest.raises(PartitionCompletionError, match=f"declares schema_version {COMPLETION_PARTS_SCHEMA_VERSION}"):
        PartitionCompletion.from_json_bytes(v1_with_parts)
    with pytest.raises(PartitionCompletionError, match="records its parts"):
        PartitionCompletion.from_json_bytes(v2_without_parts)


def test_a_recorded_part_list_must_describe_every_part_the_marker_counts() -> None:
    """A PARTIAL list would let a bootstrap bind a day by digest while one of its parts went unproven."""
    with pytest.raises(PartitionCompletionError, match="must record all 2 of them"):
        PartitionCompletion(part_count=2, row_count=25, completed_at=NOW, run_id=RUN_ID, parts=_recorded_parts()[:1])


def test_recorded_parts_must_be_sorted_unique_and_add_up() -> None:
    first, second = _recorded_parts()
    with pytest.raises(PartitionCompletionError, match="sorted unique relative paths"):
        PartitionCompletion(part_count=2, row_count=40, completed_at=NOW, run_id=RUN_ID, parts=(second, first))
    with pytest.raises(PartitionCompletionError, match="while the marker claims"):
        PartitionCompletion(part_count=2, row_count=41, completed_at=NOW, run_id=RUN_ID, parts=_recorded_parts())


def test_a_recorded_part_requires_a_real_digest_and_real_counts() -> None:
    """Nothing enters this field that was not measured from an object: no short digest, no empty part."""
    with pytest.raises(PartitionCompletionError, match="lowercase SHA-256"):
        CompletedPart(relative_path=f"{DAY_PREFIX}/part-0.parquet", row_count=1, byte_count=1, sha256="a" * 63)
    with pytest.raises(PartitionCompletionError, match="at least one row"):
        CompletedPart(relative_path=f"{DAY_PREFIX}/part-0.parquet", row_count=0, byte_count=1, sha256=FIRST_DIGEST)
    with pytest.raises(PartitionCompletionError, match="at least one byte"):
        CompletedPart(relative_path=f"{DAY_PREFIX}/part-0.parquet", row_count=1, byte_count=0, sha256=FIRST_DIGEST)


def test_a_derived_empty_receipt_can_record_no_parts() -> None:
    """It holds nothing by definition, so there is nothing for it to have hashed."""
    with pytest.raises(PartitionCompletionError, match="must record all 0 of them"):
        PartitionCompletion(
            part_count=0,
            row_count=0,
            completed_at=NOW,
            run_id=RUN_ID,
            derived_empty=True,
            parts=_recorded_parts(),
        )
