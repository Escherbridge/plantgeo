"""Indexed current snapshots require bound source and terminal evidence, including empty replacement."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path, completion_marker_path, partition_path
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.mtbs_snapshot_catalog import _ReadBudget, _terminal, load_latest_mtbs_snapshot
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.parquet_ops.serving import resolve_day, resolve_release, resolve_window
from agri_data_service.parquet_ops.wire import GovernedAbsenceDay, PublishedDay
from agri_data_service.pipeline.parquet.availability_extension import LANE_EXPORT_SOURCE_SCHEMA_VERSION
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityIdentity,
    BootstrapInventoryEvidence,
    BootstrapRequest,
    EvidenceReceipt,
    SourceEvidence,
    TerminalEvidence,
    availability_pointer_key,
    availability_row_from_terminal_evidence,
    build_bootstrap_inventory_evidence,
    build_source_evidence,
    build_terminal_evidence,
    compute_verified_source_inventory_root,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _bootstrap_availability_owned as bootstrap,
)
from agri_data_service.warehouse.mtbs_snapshots import (
    MTBS_SNAPSHOT_LANE_ROOT,
    MtbsSnapshotDescriptor,
    descriptor_from_manifest,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER
from tests.parquet.test_availability_index import MemoryAvailabilityStorage
from tests.parquet_ops.fakes import FakeListing, FakeRowReader

if TYPE_CHECKING:
    from collections.abc import Callable

    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.mtbs_snapshot_catalog import VerifiedMtbsSnapshot

CAPTURE_FIRE_YEAR = 2026
DAY = date(2026, 9, 11)
NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
SCOPE = ReadScope("burn-severity", "observed", 13, None)
RUNGS: tuple[ZoomTier, ...] = (0, 5, 9, 13)


def manifest(count: int = 1) -> bytes:
    descriptor = MtbsSnapshotDescriptor("a" * 64, NOW - timedelta(days=1), NOW - timedelta(days=1), DAY, count)
    value = descriptor.to_wire()
    value.pop("manifest_sha256")
    value.update(
        {
            "counts_by_year": {str(year): count if year == CAPTURE_FIRE_YEAR else 0 for year in range(2018, 2027)},
            "source_content_sha256": "b" * 64,
            "responses": [{"role": "geometry", "parameters": {}, "sha256": "c" * 64, "bytes": 10}],
            "consistency": "bounded capture, no upstream snapshot token",
        }
    )
    return canonical_json(value).encode()


@dataclass
class SnapshotListing(FakeListing):
    mtbs_snapshot_loader: Callable[[date], VerifiedMtbsSnapshot | None] | None = None


def warehouse(
    count: int = 1,
) -> tuple[MemoryAvailabilityStorage, SnapshotListing, FakeRowReader, MtbsSnapshotDescriptor]:
    store = MemoryAvailabilityStorage()
    payload = manifest(count)
    descriptor = descriptor_from_manifest(payload, expected_sha256=sha256_digest(payload))
    manifest_receipt = store.seed(descriptor.manifest_key, payload)
    identity = AvailabilityIdentity(
        MTBS_SNAPSHOT_LANE_ROOT,
        "burn-severity",
        "burn-severity",
        "release_series",
        RUNGS,
        compute_verified_source_inventory_root((manifest_receipt,)),
    )
    inventory = build_bootstrap_inventory_evidence(BootstrapInventoryEvidence(identity, DAY, (manifest_receipt,)))
    store.seed(inventory.receipt.key, inventory.payload)
    detail = canonical_json(
        {
            "schema_version": "mtbs-current-snapshot-binding/v1",
            "manifest": manifest_receipt.to_wire(),
            "descriptor": descriptor.to_wire(),
        }
    )
    export = canonical_json(
        {
            "schema_version": LANE_EXPORT_SOURCE_SCHEMA_VERSION,
            "origin": "mtbs-current-snapshot",
            "lane_root": MTBS_SNAPSHOT_LANE_ROOT,
            "day": DAY.isoformat(),
            "row_count": count,
            "part_count": 1 if count else 0,
            "run_id": "capture-test",
            "detail": detail,
            "exported_at": NOW.isoformat(),
        }
    ).encode()
    raw = store.seed(
        f"{MTBS_SNAPSHOT_LANE_ROOT}/availability/source/day={DAY}/export={sha256_digest(export)}.json", export
    )
    source = build_source_evidence(SourceEvidence(identity, DAY, DAY, (raw,)))
    store.seed(source.receipt.key, source.payload)
    rows = []
    listing = SnapshotListing()
    reader = FakeRowReader()
    for rung in RUNGS:
        data: tuple[EvidenceReceipt, ...] = ()
        completion = None
        absence = None
        if count:
            sink = io.BytesIO()
            pq.write_table(pa.table({"value": list(range(count))}), sink)
            part = store.seed(partition_path("burn-severity", "observed", rung, DAY), sink.getvalue())
            data = (part,)
            marker = PartitionCompletion(
                1,
                count,
                NOW,
                "capture-test",
                parts=()
                if rung == BASE_ZOOM_TIER
                else (CompletedPart(part.key, count, len(sink.getvalue()), part.sha256),),
            )
            completion = store.seed(
                completion_marker_path("burn-severity", "observed", rung, DAY), marker.to_json_bytes()
            )
            listing.keys.update((part.key, completion.key))
            reader.rows_by_key[part.key] = (
                {
                    "release_identifier": descriptor.release_identifier,
                    "observed_day": DAY,
                    "data_available_at": datetime(2026, 9, 11, tzinfo=UTC),
                    "fire_year": 2026,
                    "fire_id": "test-fire",
                },
            )
        else:
            absent_marker = GovernedAbsence(
                "verified_source_empty", "complete captured query returned zero fires", NOW, "capture-test"
            )
            absence = store.seed(
                absence_marker_path("burn-severity", "observed", rung, DAY), absent_marker.to_json_bytes()
            )
            listing.keys.add(absence.key)
            listing.objects[absence.key] = absent_marker.to_json_bytes()
        evidence = TerminalEvidence(
            identity,
            DAY,
            rung,
            "published" if count else "governed_absence",
            count,
            DAY,
            NOW,
            source.receipt,
            data,
            completion,
            absence,
            None if count else "verified_source_empty",
        )
        terminal = build_terminal_evidence(evidence)
        store.seed(terminal.receipt.key, terminal.payload)
        rows.append(availability_row_from_terminal_evidence(evidence, terminal_receipt=terminal.receipt))
    bootstrap(store, BootstrapRequest(identity, DAY, NOW, (inventory.receipt,), tuple(rows), "d" * 64))
    listing.mtbs_snapshot_loader = lambda as_of: load_latest_mtbs_snapshot(store, as_of=as_of, now=NOW)
    return store, listing, reader, descriptor


def test_real_generation_and_evidence_bind_positive_snapshot_and_empty_viewport() -> None:
    store, listing, reader, descriptor = warehouse()
    before = dict(store.objects)
    result = resolve_release(listing, reader, scope=SCOPE, as_of=DAY)
    assert isinstance(result, PublishedDay)
    assert result.mtbs_snapshot == descriptor
    assert result.to_wire()["mtbs_snapshot"] == descriptor.to_wire()
    reader.rows_by_key.clear()
    empty = resolve_day(listing, reader, scope=SCOPE, day=DAY)
    assert isinstance(empty, PublishedDay)
    assert empty.rows == ()
    assert empty.mtbs_snapshot == descriptor
    assert store.objects == before


@pytest.mark.parametrize(
    ("rung", "change", "message"),
    [
        (9, "omit_parts", "part digests"),
        (9, "wrong_digest", "part digests"),
        (13, "wrong_digest", "part digests"),
        (13, "wrong_count", "counts/run"),
        (13, "wrong_run", "counts/run"),
        (13, "noncanonical", "part digests"),
    ],
)
def test_rebound_completion_preserves_rung_digest_and_base_identity_checks(
    rung: ZoomTier, change: str, message: str
) -> None:
    store, _, _, _ = warehouse()
    index = read_latest_availability(store, lane_root=MTBS_SNAPSHOT_LANE_ROOT)
    row = next(row for row in index.rows if row.day == DAY and row.rung == rung)
    assert row.completion_receipt is not None
    marker = PartitionCompletion.from_json_bytes(store.objects[row.completion_receipt.key].payload)
    if change == "omit_parts":
        marker = replace(marker, parts=())
    elif change == "wrong_digest":
        part = row.data_receipts[0]
        marker = replace(marker, parts=(CompletedPart(part.key, 1, len(store.objects[part.key].payload), "e" * 64),))
    elif change == "wrong_count":
        marker = replace(marker, part_count=2)
    elif change == "wrong_run":
        marker = replace(marker, run_id="different-capture")
    payload = marker.to_json_bytes() + (b"\n" if change == "noncanonical" else b"")
    completion = store.seed(row.completion_receipt.key, payload)
    evidence = TerminalEvidence(
        index.pointer.identity,
        row.day,
        row.rung,
        row.terminal_state,
        row.row_count,
        row.source_ceiling,
        row.published_at,
        row.source_receipt,
        row.data_receipts,
        completion,
        None,
        None,
    )
    terminal = build_terminal_evidence(evidence)
    store.seed(terminal.receipt.key, terminal.payload)
    rebound = availability_row_from_terminal_evidence(evidence, terminal_receipt=terminal.receipt)
    with pytest.raises(ValueError, match=message):
        _terminal(_ReadBudget(store), index, rebound, {"run_id": "capture-test"})


def test_zero_source_replaces_history_and_binds_served_absence() -> None:
    _, listing, reader, descriptor = warehouse(0)
    listing.write_day("burn-severity", "observed", 13, date(2024, 8, 22))
    result = resolve_release(listing, reader, scope=SCOPE, as_of=DAY)
    assert isinstance(result, GovernedAbsenceDay)
    assert result.mtbs_snapshot == descriptor
    assert reader.reads == []
    assert resolve_window(listing, reader, scope=SCOPE, first_day=DAY, last_day=DAY) == (result,)
    key = absence_marker_path("burn-severity", "observed", 13, DAY)
    value = json.loads(listing.objects[key])
    value["upstream_response"] = "different source"
    listing.objects[key] = canonical_json(value).encode()
    with pytest.raises(ServingRefusalError, match="absence differs"):
        resolve_day(listing, reader, scope=SCOPE, day=DAY)


@pytest.mark.parametrize("target", ["manifest", "completion", "source", "terminal", "generation"])
def test_changed_indexed_evidence_fails_closed_without_older_fallback(target: str) -> None:
    store, listing, reader, descriptor = warehouse()
    listing.write_day("burn-severity", "observed", 13, date(2024, 8, 22))
    selectors = {
        "manifest": descriptor.manifest_key,
        "completion": completion_marker_path("burn-severity", "observed", 13, DAY),
    }
    key = selectors.get(target) or next(key for key in store.objects if f"/{target}=" in key or f"/{target}/" in key)
    store.seed(key, b"changed")
    with pytest.raises(ServingRefusalError, match="could not be verified"):
        resolve_release(listing, reader, scope=SCOPE, as_of=DAY)
    assert reader.reads == []


def test_missing_pointer_after_bootstrap_refuses() -> None:
    store, listing, reader, _ = warehouse()
    store.objects.pop(availability_pointer_key(MTBS_SNAPSHOT_LANE_ROOT))
    with pytest.raises(ServingRefusalError, match="could not be verified"):
        resolve_release(listing, reader, scope=SCOPE, as_of=DAY)


def test_capture_day_is_not_served_early_and_unregistered_exact_day_refuses() -> None:
    store, listing, reader, _ = warehouse()
    assert load_latest_mtbs_snapshot(store, as_of=DAY, now=NOW - timedelta(days=1)) is None
    listing.mtbs_snapshot_loader = None
    with pytest.raises(ServingRefusalError, match="lacks verified"):
        resolve_day(listing, reader, scope=SCOPE, day=DAY)


def test_wrong_row_identity_and_changed_physical_inventory_refuse() -> None:
    _, listing, reader, _ = warehouse()
    part = partition_path("burn-severity", "observed", 13, DAY)
    reader.rows_by_key[part] = ({**reader.rows_by_key[part][0], "release_identifier": "wrong"},)
    with pytest.raises(ServingRefusalError, match="rows differ"):
        resolve_day(listing, reader, scope=SCOPE, day=DAY)
    listing.keys.remove(part)
    with pytest.raises(ServingRefusalError, match="physical partition differs"):
        resolve_release(listing, reader, scope=SCOPE, as_of=DAY)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("available_day", "2026-09-10"),
        ("capture_complete", 1),
        ("source_row_count", True),
        ("partial_fire_years", [2023]),
        ("bbox", [-180, -90, 180, 90]),
    ],
)
def test_descriptor_rejects_scope_clock_and_boolean_count_drift(field: str, value: object) -> None:
    payload = manifest()
    descriptor = descriptor_from_manifest(payload, expected_sha256=sha256_digest(payload))
    wire = descriptor.to_wire()
    wire[field] = value
    with pytest.raises(ValueError, match=r"snapshot|capture"):
        MtbsSnapshotDescriptor.from_wire(wire)


def test_snapshot_result_cannot_exceed_the_captured_source_count() -> None:
    _, listing, reader, _ = warehouse()
    part = partition_path("burn-severity", "observed", 13, DAY)
    reader.rows_by_key[part] = (reader.rows_by_key[part][0], reader.rows_by_key[part][0])
    with pytest.raises(ServingRefusalError, match="exceeds captured source count"):
        resolve_day(listing, reader, scope=SCOPE, day=DAY)


def test_snapshot_result_rejects_duplicate_fire_identity_within_source_count() -> None:
    _, listing, reader, _ = warehouse(2)
    part = partition_path("burn-severity", "observed", 13, DAY)
    reader.rows_by_key[part] = (reader.rows_by_key[part][0], reader.rows_by_key[part][0])
    with pytest.raises(ServingRefusalError, match="fire identities are missing or repeated"):
        resolve_day(listing, reader, scope=SCOPE, day=DAY)
