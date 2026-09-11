"""Preserved archives cannot turn tampered coordinates or unknown objects into admission evidence."""

from __future__ import annotations

import io
import json
import tarfile
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
from agri_data_service.pipeline.parquet.signal_candidate_admission import (
    SignalArchivePin,
    read_signal_archive,
    verify_signal_day,
)
from agri_data_service.pipeline.parquet.signal_coordinate_preview import (
    build_coordinate_candidate,
    logical_sha256,
    parquet_bytes,
)
from tests.parquet.test_signal_coordinate_preview import DAY, _dimension
from tests.parquet.test_signal_rewrite import _legacy_table

if TYPE_CHECKING:
    from pathlib import Path


def _archive(tmp_path: Path, members: list[tuple[str, bytes, bytes]]) -> tuple[Path, SignalArchivePin]:
    target = tmp_path / "candidate.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        for name, payload, kind in members:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return target, SignalArchivePin(
        sha256_digest(target.read_bytes()),
        target.stat().st_size,
        sha256_digest(b"{}"),
        len(members),
        sum(len(payload) for _, payload, _ in members),
    )


def test_archive_verification_never_extracts_even_valid_members(tmp_path: Path) -> None:
    payload = b"preserved source"
    key = f"objects/{sha256_digest(payload)}"
    target, pin = _archive(tmp_path, [("batch-summary.json", b"{}", tarfile.REGTYPE), (key, payload, tarfile.REGTYPE)])
    assert read_signal_archive(target, pin=pin) == {"batch-summary.json": b"{}", key: payload}
    assert list(tmp_path.iterdir()) == [target]
    with pytest.raises(ValueError, match="checksum"):
        read_signal_archive(target, pin=replace(pin, sha256="0" * 64))
    with pytest.raises(ValueError, match="byte count"):
        read_signal_archive(target, pin=replace(pin, byte_count=pin.byte_count + 1))


@pytest.mark.parametrize(
    ("name", "kind", "message"),
    [
        ("../escaped", tarfile.REGTYPE, "unexpected"),
        ("objects/" + "a" * 64, tarfile.SYMTYPE, "nonregular"),
        ("batch-summary.json", tarfile.REGTYPE, "duplicate"),
        ("objects/" + "a" * 64, tarfile.REGTYPE, "checksum"),
    ],
)
def test_pinned_outer_archive_still_requires_safe_valid_unique_members(
    tmp_path: Path,
    name: str,
    kind: bytes,
    message: str,
) -> None:
    target, pin = _archive(tmp_path, [("batch-summary.json", b"{}", tarfile.REGTYPE), (name, b"{}", kind)])
    with pytest.raises(ValueError, match=message):
        read_signal_archive(target, pin=pin)


def _blob(objects: dict[str, bytes], payload: bytes) -> dict[str, object]:
    digest = sha256_digest(payload)
    key = f"objects/{digest}"
    objects[key] = payload
    return {"sha256": digest, "byte_count": len(payload), "local_path": key}


def _refresh_receipt(document: dict[str, object], objects: dict[str, bytes]) -> None:
    payload = json.dumps({key: value for key, value in document.items() if key != "receipt"}).encode()
    document["receipt"] = _blob(objects, payload)


def _day() -> tuple[dict[str, object], dict[str, bytes]]:
    objects: dict[str, bytes] = {}
    base = _legacy_table(DAY)
    candidate = build_coordinate_candidate(base, _dimension(), day=DAY)
    marker = PartitionCompletion(
        part_count=1, row_count=base.num_rows, completed_at=datetime(2026, 9, 10, tzinfo=UTC), run_id="preserved"
    )
    originals = [
        {"key": partition_path("signal", "observed", 13, DAY), **_blob(objects, parquet_bytes(base))},
        {"key": completion_marker_path("signal", "observed", 13, DAY), **_blob(objects, marker.to_json_bytes())},
    ]
    outputs = [
        {
            "rung": rung,
            "candidate_serving_key": partition_path("signal", "observed", rung, DAY),
            "logical_sha256": logical_sha256(table),
            **_blob(objects, parquet_bytes(table)),
        }
        for rung, table in candidate.tables
    ]
    document: dict[str, object] = {
        "day": DAY.isoformat(),
        "apply_authorized": False,
        "originals": originals,
        "candidates": outputs,
        "original_logical_sha256": candidate.original_logical_sha256,
        "preserved_logical_sha256": candidate.preserved_logical_sha256,
        "mapping_sha256": candidate.mapping_sha256,
        "distinct_cell_count": candidate.distinct_cell_count,
    }
    _refresh_receipt(document, objects)
    return document, objects


def test_day_packet_retains_originals_and_verifies_all_four_rungs() -> None:
    document, objects = _day()
    packet = verify_signal_day(document, objects, _dimension(), day=DAY)
    assert packet["original_row_count"] == 1
    assert packet["originals"] == document["originals"]
    candidates = cast("list[dict[str, object]]", packet["candidates"])
    assert [item["rung"] for item in candidates] == [13, 9, 5, 0]
    assert all(item["row_count"] == 1 for item in candidates)


@pytest.mark.parametrize(("column", "value"), [("normalized_value", 999.0), ("cell_longitude", -100.0)])
def test_rehashed_candidate_cannot_change_an_original_value_or_coordinate(column: str, value: float) -> None:
    document, objects = _day()
    candidates = cast("list[dict[str, object]]", document["candidates"])
    original = dict(build_coordinate_candidate(_legacy_table(DAY), _dimension(), day=DAY).tables)[13]
    changed = original.set_column(
        original.schema.get_field_index(column), original.schema.field(column), pa.array([value])
    )
    candidates[0].update(_blob(objects, parquet_bytes(changed)))
    candidates[0]["logical_sha256"] = logical_sha256(changed)
    _refresh_receipt(document, objects)
    with pytest.raises(ValueError, match="candidate values"):
        verify_signal_day(document, objects, _dimension(), day=DAY)


@pytest.mark.parametrize(
    ("change", "message"),
    [("target", "target escapes"), ("rung", "rung set"), ("original", "exact base"), ("receipt", "receipt differs")],
)
def test_rehashed_metadata_cannot_change_the_exact_repair_scope(change: str, message: str) -> None:
    document, objects = _day()
    candidates = cast("list[dict[str, object]]", document["candidates"])
    if change == "target":
        candidates[0]["candidate_serving_key"] = partition_path("signal", "observed", 13, DAY.replace(day=2))
    elif change == "rung":
        candidates.pop()
    elif change == "original":
        cast("list[dict[str, object]]", document["originals"]).pop()
    else:
        document["distinct_cell_count"] = 2
    if change != "receipt":
        _refresh_receipt(document, objects)
    with pytest.raises(ValueError, match=message):
        verify_signal_day(document, objects, _dimension(), day=DAY)
