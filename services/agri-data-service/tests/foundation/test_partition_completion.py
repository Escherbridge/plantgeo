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
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Final

import pytest

from agri_data_service.foundation.parquet.completion import (
    COMPLETION_SCHEMA_VERSION,
    DERIVED_EMPTY_FIELD,
    PartitionCompletion,
    PartitionCompletionError,
)

NOW: Final = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
RUN_ID: Final = "run-completion"


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
