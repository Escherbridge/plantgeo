"""`data availability-digest-trusted`: manifest-trusted days gain real part receipts, or are refused one by one.

Driven through the CLI over ONE in-memory bucket shared by the lane's object store and the
availability storage, exactly as production shares R2: the publisher re-opens and re-hashes the same
part keys the digest cited. Only the process edges are faked -- the Postgres publication barrier and
the writer session.
"""

from __future__ import annotations

import functools
import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from click.testing import CliRunner

import agri_data_service.interface.cli.availability as availability_cli
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path, completion_marker_path, partition_path
from agri_data_service.interface.cli import cli
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityIdentity,
    AvailabilityRow,
    BootstrapInventoryEvidence,
    BootstrapRequest,
    EvidenceReceipt,
    SourceEvidence,
    TerminalEvidence,
    availability_pointer_key,
    build_bootstrap_inventory_evidence,
    build_source_evidence,
    build_terminal_evidence,
    compute_verified_source_inventory_root,
    publish_availability,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.availability_primitives import (
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
)
from agri_data_service.pipeline.parquet.availability_reconciliation import AvailabilityReconciliationError
from agri_data_service.pipeline.parquet.availability_trusted_digest import compile_trusted_digest
from agri_data_service.pipeline.parquet.gap_fill import GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS
from tests.parquet.test_availability_extension import (
    LANE,
    LANE_ROOT,
    RUN_ID,
    LaneAvailabilityStorage,
    LoggingBackend,
    _bootstrap_availability_owned,
    granted_barrier,
    new_lane,
    write_published_day,
)
from tests.parquet.test_objectstore_writer import signal_rows

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, WrittenObjectLedger

TRUSTED_DAYS = (date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 8))
HASHED_DAY = date(2025, 10, 9)
MIXED_DAY = date(2025, 10, 10)
CEILING = date(2025, 10, 11)
FIRST, LAST = TRUSTED_DAYS[0], MIXED_DAY
BOOTSTRAPPED_AT = datetime(2025, 10, 12, 12, tzinfo=UTC)
ROWS_PUBLISHED_AT = BOOTSTRAPPED_AT - timedelta(hours=1)
MARKERS_COMPLETED_AT = BOOTSTRAPPED_AT - timedelta(hours=2)
SABOTAGED_DAY = TRUSTED_DAYS[1]
BASE = cast("ZoomTier", GAP_FILL_ZOOM_TIER)


# --- fixture: a bootstrapped head holding trusted, hashed and mixed days ---------------------------


def trusted_lane() -> tuple[LoggingBackend, ObjectStore, LaneAvailabilityStorage]:
    """Bootstrap generation zero the way D3 did: history days bound by marker alone, one day hashed, one mixed."""
    backend, store, storage, _log = new_lane()
    manifest_key = f"{LANE_ROOT}/availability/manifest/bootstrap.json"
    storage.put_immutable(manifest_key, b"verified bootstrap manifest", content_type="application/json")
    manifest = EvidenceReceipt(key=manifest_key, sha256=sha256_digest(b"verified bootstrap manifest"))
    identity = AvailabilityIdentity(
        lane_root=LANE_ROOT,
        lane=LANE,
        product=LANE,
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root((manifest,)),
    )
    rows: list[AvailabilityRow] = []
    for day in TRUSTED_DAYS:
        ledger = write_published_day(store, day=day, completed_at=MARKERS_COMPLETED_AT)
        rows.extend(seed_rows(storage, identity, ledger, day=day, hashed_rungs=()))
    ledger = write_published_day(store, day=HASHED_DAY, completed_at=MARKERS_COMPLETED_AT, record_parts=True)
    rows.extend(seed_rows(storage, identity, ledger, day=HASHED_DAY, hashed_rungs=AVAILABILITY_REQUIRED_RUNGS))
    ledger = write_published_day(store, day=MIXED_DAY, completed_at=MARKERS_COMPLETED_AT)
    rows.extend(seed_rows(storage, identity, ledger, day=MIXED_DAY, hashed_rungs=(AVAILABILITY_REQUIRED_RUNGS[0],)))
    inventory = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=CEILING, object_receipts=(manifest,))
    )
    storage.put_immutable(inventory.receipt.key, inventory.payload, content_type="application/json")
    _bootstrap_availability_owned(
        storage,
        BootstrapRequest(
            identity=identity,
            source_ceiling=CEILING,
            created_at=BOOTSTRAPPED_AT,
            input_receipts=(inventory.receipt,),
            rows=tuple(sorted(rows, key=lambda row: (row.day, row.rung))),
            input_sha256=sha256_digest(b"bootstrap input"),
        ),
    )
    return backend, store, storage


def seed_rows(
    storage: LaneAvailabilityStorage,
    identity: AvailabilityIdentity,
    ledger: WrittenObjectLedger,
    *,
    day: date,
    hashed_rungs: tuple[int, ...],
) -> tuple[AvailabilityRow, ...]:
    """Bind one written day; rungs outside `hashed_rungs` cite no part, which is the manifest-trusted shape."""
    source_payload = f"bootstrap source for {day.isoformat()}".encode()
    source_key = f"{LANE_ROOT}/availability/source/day={day.isoformat()}/bootstrap.json"
    storage.put_immutable(source_key, source_payload, content_type="application/json")
    source = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=day,
            source_ceiling=CEILING,
            object_receipts=(EvidenceReceipt(key=source_key, sha256=sha256_digest(source_payload)),),
        )
    )
    storage.put_immutable(source.receipt.key, source.payload, content_type="application/json")
    rows: list[AvailabilityRow] = []
    for rung in identity.required_rungs:
        tier = cast("ZoomTier", rung)
        completion = ledger.completion_for(kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=day)
        assert completion is not None
        hashed = rung in hashed_rungs
        data_receipts = (
            tuple(
                EvidenceReceipt(key=part.relative_path, sha256=part.sha256)
                for part in ledger.parts_for(kind=GAP_FILL_PARTITION_KIND, zoom=tier, day=day)
            )
            if hashed
            else ()
        )
        evidence = TerminalEvidence(
            identity=identity,
            day=day,
            rung=rung,
            terminal_state="published",
            row_count=completion.row_count,
            source_ceiling=CEILING,
            published_at=ROWS_PUBLISHED_AT,
            source_receipt=source.receipt,
            data_receipts=data_receipts,
            completion_receipt=EvidenceReceipt(key=completion.relative_path, sha256=completion.sha256),
            absence_receipt=None,
            absence_reason=None,
            provenance=DIGESTED_PROVENANCE if hashed else MANIFEST_TRUSTED_PROVENANCE,
        )
        terminal = build_terminal_evidence(evidence)
        storage.put_immutable(terminal.receipt.key, terminal.payload, content_type="application/json")
        rows.append(
            AvailabilityRow(
                lane=identity.lane,
                product=identity.product,
                nature=identity.nature,
                day=day,
                rung=rung,
                terminal_state="published",
                row_count=completion.row_count,
                source_receipt=source.receipt,
                terminal_receipt=terminal.receipt,
                data_receipts=data_receipts,
                completion_receipt=evidence.completion_receipt,
                absence_reason=None,
                source_ceiling=CEILING,
                published_at=ROWS_PUBLISHED_AT,
            )
        )
    return tuple(rows)


# --- CLI seam --------------------------------------------------------------------------------------


@asynccontextmanager
async def _writer_session() -> AsyncIterator[str]:
    yield "session"


def wire_cli(monkeypatch: pytest.MonkeyPatch, store: ObjectStore, storage: LaneAvailabilityStorage) -> None:
    """Point the CLI at the in-memory bucket; only the Postgres barrier and session are stand-ins."""
    monkeypatch.setattr(availability_cli, "_storage", lambda: storage)
    monkeypatch.setattr(availability_cli, "_object_store", lambda: store)
    monkeypatch.setattr(availability_cli, "receiver_writer_session", _writer_session)
    monkeypatch.setattr(
        availability_cli,
        "publish_availability",
        functools.partial(publish_availability, publication_barrier=granted_barrier),
    )


def forbid_live_dependencies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record, and fail, any store or session construction."""
    touched: list[str] = []

    def refuse(name: str) -> Callable[[], object]:
        def constructed() -> object:
            touched.append(name)
            raise AssertionError(f"{name} must not be constructed")

        return constructed

    monkeypatch.setattr(availability_cli, "_storage", refuse("availability storage"))
    monkeypatch.setattr(availability_cli, "_object_store", refuse("object store"))
    monkeypatch.setattr(availability_cli, "receiver_writer_session", refuse("writer session"))
    return touched


def digest(tmp_path: Path, *extra: str, start: date = FIRST, end: date = LAST) -> tuple[int, str, dict[str, object]]:
    """Run the verb and return its exit code, its output, and the report it wrote (empty if none)."""
    output = tmp_path / "report.json"
    output.unlink(missing_ok=True)
    result = CliRunner().invoke(
        cli,
        [
            "data",
            "availability-digest-trusted",
            "--lane",
            LANE,
            "--kind",
            GAP_FILL_PARTITION_KIND,
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--output",
            str(output),
            *extra,
        ],
    )
    report = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    return result.exit_code, result.output, report


def day_report(report: dict[str, object], day: date) -> dict[str, object]:
    days = cast("list[dict[str, object]]", report["days"])
    return next(entry for entry in days if entry["day"] == day.isoformat())


def bucket_snapshot(backend: LoggingBackend) -> dict[str, bytes]:
    return dict(backend.objects)


def data_plane(backend: LoggingBackend) -> dict[str, bytes]:
    """The lane's Parquet parts and markers: everything under the lane root that is not availability state."""
    return {
        key: value for key, value in backend.objects.items() if key.startswith("layer=") and "/availability/" not in key
    }


# --- flows -----------------------------------------------------------------------------------------


def test_a_dry_run_reports_every_day_and_writes_nothing_to_the_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, store, storage = trusted_lane()
    before = bucket_snapshot(backend)
    wire_cli(monkeypatch, store, storage)

    code, output, report = digest(tmp_path)

    assert code == 0, output
    assert bucket_snapshot(backend) == before
    assert json.loads(output)["applied"] is False
    for day in TRUSTED_DAYS:
        entry = day_report(report, day)
        assert entry["outcome"] == "ready"
        rungs = cast("list[dict[str, object]]", entry["rungs"])
        assert [rung["outcome"] for rung in rungs] == ["verified"] * len(AVAILABILITY_REQUIRED_RUNGS)
        assert all(rung["physical_part_count"] == rung["marker_part_count"] == 1 for rung in rungs)
        assert all(rung["parquet_row_count"] == rung["indexed_row_count"] for rung in rungs)
    hashed = day_report(report, HASHED_DAY)
    assert (hashed["outcome"], hashed["reason"], hashed["rungs"]) == ("skipped", "already_hashed", [])
    mixed = day_report(report, MIXED_DAY)
    assert (mixed["outcome"], mixed["reason"]) == ("skipped", "mixed")
    assert "z0=digested" in cast("str", mixed["held_shape"])
    totals = cast("dict[str, object]", report["totals"])
    assert totals["days_ready"] == len(TRUSTED_DAYS)
    assert totals["parts_hashed"] == len(TRUSTED_DAYS) * len(AVAILABILITY_REQUIRED_RUNGS)
    assert cast("int", totals["bytes_downloaded"]) > 0
    # The three ready days leave only the mixed day's three trusted rungs for a later run.
    index_size = cast("dict[str, object]", report["index_size"])
    assert index_size["trusted_rows_after_run"] == len(AVAILABILITY_REQUIRED_RUNGS) - 1
    assert index_size["fits_cache_after_run"] is True
    document = tmp_path / "report.publication.json"
    assert sha256_digest(document.read_bytes()) == report["document_sha256"]


def test_apply_with_the_reviewed_pins_publishes_rows_that_cite_every_part_by_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, store, storage = trusted_lane()
    wire_cli(monkeypatch, store, storage)
    _code, _output, reviewed = digest(tmp_path)
    data_plane_before = data_plane(backend)
    held_before = read_latest_availability(storage, lane_root=LANE_ROOT)

    code, output, _report = digest(
        tmp_path,
        "--apply",
        "--expected-sha256",
        cast("str", reviewed["document_sha256"]),
        "--expected-head-generation",
        cast("str", reviewed["head_generation_key"]),
    )

    assert code == 0, output
    assert json.loads(output)["advanced"] is True
    index = read_latest_availability(storage, lane_root=LANE_ROOT)
    assert index.pointer.prior_generation_key == held_before.pointer.generation_key
    held_by_grain = {row.grain: row for row in held_before.rows}
    for row in (row for row in index.rows if row.day in TRUSTED_DAYS):
        assert row.provenance == DIGESTED_PROVENANCE
        expected_key = partition_path(LANE, GAP_FILL_PARTITION_KIND, cast("ZoomTier", row.rung), row.day)
        assert row.data_receipts == (
            EvidenceReceipt(key=expected_key, sha256=sha256_digest(backend.objects[expected_key])),
        )
        assert row.completion_receipt == held_by_grain[row.grain].completion_receipt
        assert row.row_count == held_by_grain[row.grain].row_count
        assert row.published_at > held_by_grain[row.grain].published_at
    untouched = {row.grain: row for row in index.rows if row.day not in TRUSTED_DAYS}
    assert untouched == {grain: row for grain, row in held_by_grain.items() if grain[0] not in TRUSTED_DAYS}
    # Markers and Parquet are never rewritten: only availability evidence and the pointer moved.
    assert data_plane(backend) == data_plane_before

    _code, _output, rerun = digest(tmp_path)
    assert {day_report(rerun, day)["reason"] for day in TRUSTED_DAYS} == {"already_hashed"}
    assert rerun["document_sha256"] is None


def _rewrite_marker(backend: LoggingBackend, store: ObjectStore) -> None:
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=3, completed_at=MARKERS_COMPLETED_AT, run_id=f"{RUN_ID}:re-export"),
        layer=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        zoom=BASE,
        day=SABOTAGED_DAY,
    )
    del backend


def _drop_marker(backend: LoggingBackend, store: ObjectStore) -> None:
    del store
    backend.objects.pop(completion_marker_path(LANE, GAP_FILL_PARTITION_KIND, BASE, SABOTAGED_DAY))


def _add_absence(backend: LoggingBackend, store: ObjectStore) -> None:
    del store
    backend.objects[absence_marker_path(LANE, GAP_FILL_PARTITION_KIND, BASE, SABOTAGED_DAY)] = b"{}"


def _add_surplus_part(backend: LoggingBackend, store: ObjectStore) -> None:
    del backend
    store.write_partition(
        signal_rows(), layer=LANE, kind=GAP_FILL_PARTITION_KIND, zoom=BASE, day=SABOTAGED_DAY, part_index=1
    )


def _corrupt_part(backend: LoggingBackend, store: ObjectStore) -> None:
    del store
    backend.objects[partition_path(LANE, GAP_FILL_PARTITION_KIND, BASE, SABOTAGED_DAY)] = b"not parquet"


def _shrink_part(backend: LoggingBackend, store: ObjectStore) -> None:
    """Swap in valid Parquet holding one row, leaving the three-row marker standing (an in-place overwrite)."""
    elsewhere = HASHED_DAY + timedelta(days=400)
    written = store.write_partition(
        signal_rows(cell_ids=("c1",)), layer=LANE, kind=GAP_FILL_PARTITION_KIND, zoom=BASE, day=elsewhere
    )
    backend.objects[partition_path(LANE, GAP_FILL_PARTITION_KIND, BASE, SABOTAGED_DAY)] = backend.objects.pop(
        written.relative_path
    )


@pytest.mark.parametrize(
    ("sabotage", "reason"),
    [
        (_rewrite_marker, "completion_receipt_mismatch"),
        (_drop_marker, "completion_marker_missing"),
        (_add_absence, "absence_marker_present"),
        (_add_surplus_part, "part_count_mismatch"),
        (_corrupt_part, "parts_unreadable"),
        (_shrink_part, "row_count_mismatch"),
    ],
)
def test_each_failing_check_refuses_that_day_and_publishes_the_rest(
    sabotage: Callable[[LoggingBackend, ObjectStore], None],
    reason: str,
) -> None:
    backend, store, storage = trusted_lane()
    sabotage(backend, store)

    compiled = compile_trusted_digest(
        store, storage, lane=LANE, kind=GAP_FILL_PARTITION_KIND, start_day=FIRST, end_day=LAST
    )

    by_day = {report.day: report for report in compiled.days}
    refused = by_day[SABOTAGED_DAY]
    assert (refused.outcome, refused.reason) == ("refused", reason)
    base_rung = next(rung for rung in refused.rungs if rung.rung == GAP_FILL_ZOOM_TIER)
    assert base_rung.outcome == "refused"
    assert compiled.publication is not None
    assert compiled.publication.candidate_days == tuple(day for day in TRUSTED_DAYS if day != SABOTAGED_DAY)
    document = json.loads(compiled.publication.document)
    assert SABOTAGED_DAY.isoformat() not in {row["day"] for row in document["rows"]}


class _ReExportDuringHashing:
    """The lane's store, with a re-export committing a same-count marker right after the day's parts are read."""

    def __init__(self, store: ObjectStore) -> None:
        self._store = store

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)

    def read_partition_with_receipts(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> object:
        read = self._store.read_partition_with_receipts(layer, kind, zoom, day)
        if (zoom, day) == (BASE, SABOTAGED_DAY):
            # Same part and row counts as the indexed marker: only its digest tells the two apart.
            self._store.write_completion_marker(
                PartitionCompletion(
                    part_count=1, row_count=3, completed_at=MARKERS_COMPLETED_AT, run_id=f"{RUN_ID}:mid-digest"
                ),
                layer=LANE,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=BASE,
                day=SABOTAGED_DAY,
            )
        return read


def test_a_marker_rewritten_while_its_parts_are_hashed_refuses_that_day() -> None:
    _backend, store, storage = trusted_lane()

    compiled = compile_trusted_digest(
        cast("ObjectStore", _ReExportDuringHashing(store)),
        storage,
        lane=LANE,
        kind=GAP_FILL_PARTITION_KIND,
        start_day=FIRST,
        end_day=LAST,
    )

    refused = next(report for report in compiled.days if report.day == SABOTAGED_DAY)
    assert (refused.outcome, refused.reason) == ("refused", "marker_changed_during_digest")
    assert compiled.publication is not None
    assert compiled.publication.candidate_days == tuple(day for day in TRUSTED_DAYS if day != SABOTAGED_DAY)


@pytest.mark.parametrize(("days", "accepted"), [(366, True), (367, False)])
def test_the_range_is_capped_at_366_days(days: int, *, accepted: bool) -> None:
    _backend, store, storage = trusted_lane()
    end = FIRST + timedelta(days=days - 1)

    if accepted:
        compiled = compile_trusted_digest(
            store, storage, lane=LANE, kind=GAP_FILL_PARTITION_KIND, start_day=FIRST, end_day=end
        )
        assert len(compiled.days) == days
        return
    with pytest.raises(AvailabilityReconciliationError, match=r"1\.\.366 days"):
        compile_trusted_digest(store, storage, lane=LANE, kind=GAP_FILL_PARTITION_KIND, start_day=FIRST, end_day=end)


def test_the_cli_refuses_an_oversized_range_before_touching_any_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched = forbid_live_dependencies(monkeypatch)

    code, output, report = digest(tmp_path, end=FIRST + timedelta(days=366))

    assert code != 0
    assert "1..366 days, got 367" in output
    assert (touched, report) == ([], {})


def test_apply_without_both_pins_is_refused_before_any_store_is_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched = forbid_live_dependencies(monkeypatch)

    code, output, _report = digest(tmp_path, "--apply", "--expected-sha256", "a" * 64)

    assert code != 0
    assert "--apply requires --expected-sha256 and --expected-head-generation" in output
    assert touched == []


def test_apply_against_a_head_other_than_the_reviewed_one_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, store, storage = trusted_lane()
    wire_cli(monkeypatch, store, storage)
    _code, _output, reviewed = digest(tmp_path)
    before = bucket_snapshot(backend)

    code, output, _report = digest(
        tmp_path,
        "--apply",
        "--expected-sha256",
        cast("str", reviewed["document_sha256"]),
        "--expected-head-generation",
        f"{LANE_ROOT}/availability/generations/generation={'0' * 64}.parquet",
    )

    assert code != 0
    assert "expected" in output
    assert bucket_snapshot(backend) == before
    assert backend.objects[availability_pointer_key(LANE_ROOT)] == before[availability_pointer_key(LANE_ROOT)]
