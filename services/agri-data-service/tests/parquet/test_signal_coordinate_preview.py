"""Coordinate completion preserves signal facts and rejects ambiguous source geometry."""

from __future__ import annotations

import struct
from datetime import date

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.parquet.signal_coordinate_preview import (
    build_coordinate_candidate,
    coordinate_witness,
    decode_centroid,
    parquet_bytes,
)
from tests.parquet.test_signal_rewrite import _legacy_table

DAY = date(2026, 8, 1)
CELL = "00000000-0000-0000-0000-000000000001"


def _dimension(*, identity: str = CELL, centroid: bytes | None = None) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "id": identity,
                "cell_key": identity,
                "grid_name": "test-grid",
                "centroid": centroid
                if centroid is not None
                else struct.pack("<BIIdd", 1, 0x20000001, 4326, -116.0, 43.0),
            }
        ]
    )


def test_candidate_preserves_every_original_column_and_derives_all_rungs() -> None:
    base = _legacy_table(DAY)
    candidate = build_coordinate_candidate(base, _dimension(), day=DAY)
    assert candidate.original_logical_sha256 == candidate.preserved_logical_sha256
    assert candidate.distinct_cell_count == 1
    assert {rung for rung, _table in candidate.tables} == {0, 5, 9, 13}
    corrected = dict(candidate.tables)[13]
    assert corrected.select(base.column_names).cast(base.schema).equals(base)
    assert corrected["cell_longitude"].to_pylist() == [-116.0]
    assert corrected["cell_latitude"].to_pylist() == [43.0]
    for _rung, table in candidate.tables:
        assert table["observation_count"].to_pylist() == [24]
        assert table["normalized_value"].to_pylist() == [3.5]
    repeated = build_coordinate_candidate(base, _dimension(), day=DAY)
    assert [parquet_bytes(table) for _, table in candidate.tables] == [
        parquet_bytes(table) for _, table in repeated.tables
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        struct.pack("<BIdd", 1, 1, -116.0, 43.0),
        struct.pack("<BIIdd", 1, 0x20000001, 3857, -116.0, 43.0),
        struct.pack("<BIIdd", 1, 0x20000001, 4326, float("nan"), 43.0),
        struct.pack("<BIIdd", 1, 0x20000001, 4326, -116.0, 91.0),
    ],
)
def test_invalid_or_unreferenced_geometry_never_enters_the_witness(payload: bytes) -> None:
    with pytest.raises(ValueError, match="centroid"):
        decode_centroid(payload)
    dimension = pa.concat_tables([_dimension(), _dimension(identity="unused", centroid=payload)])
    with pytest.raises(ValueError, match="centroid"):
        build_coordinate_candidate(_legacy_table(DAY), dimension, day=DAY)


def test_missing_or_duplicated_cell_identity_is_refused() -> None:
    with pytest.raises(ValueError, match="unwitnessed"):
        build_coordinate_candidate(_legacy_table(DAY), _dimension(identity="other"), day=DAY)
    with pytest.raises(ValueError, match="duplicate"):
        coordinate_witness(pa.concat_tables([_dimension(), _dimension()]))


def test_wrong_day_and_current_schema_are_not_coordinate_repair_candidates() -> None:
    with pytest.raises(ValueError, match="requested day"):
        build_coordinate_candidate(_legacy_table(DAY), _dimension(), day=date(2026, 8, 2))
    corrected = dict(build_coordinate_candidate(_legacy_table(DAY), _dimension(), day=DAY).tables)[13]
    with pytest.raises(ValueError, match="exact approved"):
        build_coordinate_candidate(corrected, _dimension(), day=DAY)
