"""The two provenance classes an availability row may be bound by, and what each one has to prove.

`digested` is the ordinary class: the row names every part it publishes and each name carries a
SHA-256 computed from that object's bytes. `manifest_trusted` is the bootstrap-only class owner
decision D3 (`environmental_postgres_retirement_20260904`) introduced: the row names NO part and
rests on its completion marker, which is still fetched and digested. It exists because hashing every
part of every lane-day -- for `fire-detections`, every day since 2000-11-01 at four rungs -- would
have put the time slider's startup fix behind the whole cutover.

THREE PROPERTIES HERE ARE LOAD-BEARING:

  * The class is DERIVED FROM THE ROW'S SHAPE, never from a column. `AVAILABILITY_INDEX_SCHEMA` is
    frozen at version 1, so a provenance column would not survive the generation round trip
    `_write_generation` re-reads and compares. Published, holding rows, and naming no part is a
    claim only a trusted row can make.
  * A DECLARATION is checked against that shape, never believed. The bootstrap input and the terminal
    evidence may both say the class out loud; a document that says one and carries the other dies.
  * The class stops at the bootstrap, and it is stopped at the CHOKEPOINT both forward callers pass
    through -- `_publish_availability_owned` -- not merely at the document loader one of them uses.
    A forward publication holds every part digest it just wrote, so a trusted row there would grow
    the region D3 deliberately bounded to history.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.pipeline.parquet.availability_index import (
    BOOTSTRAP_INPUT_SCHEMA_VERSION,
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    PROVENANCE_FIELD,
    PUBLICATION_INPUT_SCHEMA_VERSION,
    AvailabilityConfig,
    AvailabilityConflictError,
    AvailabilityIdentity,
    BootstrapInventoryEvidence,
    BootstrapRequest,
    EvidenceReceipt,
    PublicationRequest,
    SourceEvidence,
    StoredAvailabilityObject,
    TerminalEvidence,
    availability_provenance_summary,
    availability_row_from_terminal_evidence,
    availability_row_provenance,
    build_bootstrap_inventory_evidence,
    build_source_evidence,
    build_terminal_evidence,
    compute_verified_source_inventory_root,
    load_bootstrap_request,
    load_publication_request,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _bootstrap_availability_owned as bootstrap_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _publish_availability_owned as publish_availability_owned,
)
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityProvenance, AvailabilityRow

LANE_ROOT = "layer=test-lane/kind=observed"
CEILING = date(2026, 9, 3)
DIGESTED_DAY = date(2026, 9, 2)
TRUSTED_DAY = date(2020, 3, 1)
FORWARD_TRUSTED_DAY = date(2020, 3, 2)
CREATED_AT = datetime(2026, 9, 3, 12, tzinfo=UTC)
PUBLISHED_AT = CREATED_AT - timedelta(hours=1)
ROW_COUNT = 4


@dataclass
class MemoryAvailabilityStorage:
    """The conditional object operations availability needs, in memory and with a read log."""

    objects: dict[str, StoredAvailabilityObject] = field(default_factory=dict)
    read_log: list[str] = field(default_factory=list)
    version: int = 0

    def seed(self, key: str, payload: bytes) -> EvidenceReceipt:
        """Write one object as a new version and return the receipt a row would cite it by."""
        self.version += 1
        self.objects[key] = StoredAvailabilityObject(
            payload=payload,
            etag=f'"{self.version}"',
            version_id=f"version-{self.version}",
        )
        return EvidenceReceipt(key=key, sha256=sha256_digest(payload))

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Return one object, recording the key so a test can prove what was NOT fetched."""
        del max_bytes
        self.read_log.append(key)
        return self.objects.get(key)

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Create one object, accepting only an exact idempotent replay."""
        del content_type
        existing = self.objects.get(key)
        if existing is not None:
            if existing.payload != payload:
                raise AvailabilityConflictError("immutable conflict")
            return
        self.seed(key, payload)

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        """Advance the pointer only while its comparison token still matches."""
        del content_type
        existing = self.objects.get(key)
        if expected_etag is None:
            if existing is not None:
                return False
        elif existing is None or existing.etag != expected_etag:
            return False
        self.seed(key, payload)
        return True


def test_the_class_is_derived_from_the_row_shape_alone() -> None:
    """Published, holding rows, naming no part: the one shape a trusted row has, and the only one."""
    part = EvidenceReceipt(key=_part_key(DIGESTED_DAY, 13, 0), sha256="c" * 64)

    assert (
        availability_row_provenance(terminal_state="published", row_count=ROW_COUNT, data_receipts=())
        == MANIFEST_TRUSTED_PROVENANCE
    )
    assert (
        availability_row_provenance(terminal_state="published", row_count=ROW_COUNT, data_receipts=(part,))
        == DIGESTED_PROVENANCE
    )
    # An emptied derived rung proves its emptiness outright and a governed absence proves it with an
    # absence marker; neither asserts anything about parts, so neither is TRUSTING anything.
    assert availability_row_provenance(terminal_state="published", row_count=0, data_receipts=()) == (
        DIGESTED_PROVENANCE
    )
    assert availability_row_provenance(terminal_state="governed_absence", row_count=0, data_receipts=()) == (
        DIGESTED_PROVENANCE
    )


def test_a_bootstrap_mixing_both_classes_publishes_and_records_the_split() -> None:
    """The whole point: a lane binds recent days by digest and old days by their markers, and says so."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store)

    result = bootstrap_availability(store, request)

    assert result.advanced is True
    recorded = json.loads(store.objects[result.pointer.bootstrap_receipt.key].payload)[PROVENANCE_FIELD]
    assert recorded == availability_provenance_summary(request.rows)
    assert recorded[MANIFEST_TRUSTED_PROVENANCE] == {
        "earliest_day": TRUSTED_DAY.isoformat(),
        "latest_day": TRUSTED_DAY.isoformat(),
        "row_count": len(AVAILABILITY_REQUIRED_RUNGS),
    }
    assert recorded[DIGESTED_PROVENANCE]["row_count"] == len(AVAILABILITY_REQUIRED_RUNGS)
    # NOT ONE PART OF THE TRUSTED DAY WAS FETCHED, which is the cost this class exists to avoid.
    assert not [key for key in store.read_log if "part-" in key and f"year={TRUSTED_DAY.year:04d}" in key]
    assert _part_key(DIGESTED_DAY, 13, 0) in store.read_log


def test_a_trusted_row_must_agree_with_the_marker_it_rests_on() -> None:
    """Nothing else proves a trusted row's row_count, so a marker that disagrees ends the bootstrap."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store, trusted_marker_row_count=ROW_COUNT + 1)

    with pytest.raises(AvailabilityConflictError, match="completion counts do not match"):
        bootstrap_availability(store, request)


def test_a_digested_row_must_agree_with_the_parts_its_marker_recorded() -> None:
    """A marker that recorded its own digests is evidence, so a row citing different ones is refused."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store, recorded_digest="f" * 64)

    with pytest.raises(AvailabilityConflictError, match="completion marker parts and terminal data receipts"):
        bootstrap_availability(store, request)


def test_a_row_declaring_a_class_it_does_not_have_is_refused(tmp_path: Path) -> None:
    """The declaration is checked against the shape; a document that lies about it never loads."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store)
    rows = [{**row.to_wire(), PROVENANCE_FIELD: MANIFEST_TRUSTED_PROVENANCE} for row in request.rows]
    document = _bootstrap_document(request, rows=rows)
    path = _write_document(tmp_path, document)

    with pytest.raises(ValueError, match="has the shape of digested"):
        load_bootstrap_request(
            path,
            expected_sha256=sha256_digest(path.read_bytes()),
            expected_row_count=len(request.rows),
        )


def test_a_forward_publication_may_not_carry_a_trusted_row(tmp_path: Path) -> None:
    """The trusted region is bounded to history: a writer that just wrote a day holds its digests."""
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store)
    document = _publication_document(request)
    path = _write_document(tmp_path, document)

    with pytest.raises(ValueError, match="manifest-trusted row"):
        load_publication_request(
            path,
            expected_sha256=sha256_digest(path.read_bytes()),
            expected_row_count=len(AVAILABILITY_REQUIRED_RUNGS),
        )


def test_the_publisher_itself_refuses_a_trusted_row_with_no_document_in_sight() -> None:
    """DO NOT DELETE. The document loader is not the forward path, and guarding only it guards nobody.

    `load_publication_request` serves exactly ONE caller, the `interface/cli/data.py` command. The
    primary forward writer is `availability_extension._publish_rows`, which assembles its
    `PublicationRequest` in memory and calls `publish_availability` straight through -- no document,
    no loader, no guard. Owner decision D4's eight direct-to-Parquet writers all publish this way,
    and the template they will be copied from does pass `provenance=`, so a trusted row reaching the
    publisher is one careless copy away rather than hypothetical. This test publishes exactly as they
    will: nothing is written to disk and no bytes are hashed but the request's own.
    """
    store = MemoryAvailabilityStorage()
    request = _bootstrap_request(store)
    bootstrapped = bootstrap_availability(store, request)
    rows = _trusted_day_rows(store, request.identity, FORWARD_TRUSTED_DAY, marker_row_count=ROW_COUNT)
    reads_before = len(store.read_log)

    with pytest.raises(ValueError, match="manifest-trusted row"):
        publish_availability_owned(
            store,
            PublicationRequest(
                config=AvailabilityConfig(
                    identity=request.identity,
                    source_ceiling=CEILING,
                    bootstrap_receipt=bootstrapped.pointer.bootstrap_receipt,
                ),
                created_at=CREATED_AT,
                rows=tuple(sorted(rows, key=lambda row: (row.day, row.rung))),
                input_sha256=sha256_digest(b"forward publication input"),
            ),
        )

    # REFUSED BEFORE THE HEAD IS EVEN READ: the guard sits beside `_validate_generation_rows`, above
    # every fetch, so a bad publication costs one validation pass rather than a round of evidence.
    assert len(store.read_log) == reads_before


def _write_document(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "input.json"
    path.write_bytes(canonical_json(document).encode("utf-8"))
    return path


def _bootstrap_document(request: BootstrapRequest, *, rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "created_at": _format(request.created_at),
        "input_receipts": [receipt.to_wire() for receipt in request.input_receipts],
        "lane": request.identity.lane,
        "lane_root": request.identity.lane_root,
        "nature": request.identity.nature,
        "product": request.identity.product,
        "required_rungs": list(request.identity.required_rungs),
        "rows": rows,
        "schema_version": BOOTSTRAP_INPUT_SCHEMA_VERSION,
        "source_ceiling": request.source_ceiling.isoformat(),
        "verified_source_inventory_root": request.identity.verified_source_inventory_root,
    }


def _publication_document(request: BootstrapRequest) -> dict[str, object]:
    return {
        "bootstrap_receipt_key": f"{LANE_ROOT}/availability/bootstrap/receipt={'a' * 64}.json",
        "bootstrap_receipt_sha256": "a" * 64,
        "created_at": _format(request.created_at),
        "lane": request.identity.lane,
        "lane_root": request.identity.lane_root,
        "nature": request.identity.nature,
        "product": request.identity.product,
        "required_rungs": list(request.identity.required_rungs),
        "rows": [row.to_wire() for row in request.rows if row.day == TRUSTED_DAY],
        "schema_version": PUBLICATION_INPUT_SCHEMA_VERSION,
        "source_ceiling": request.source_ceiling.isoformat(),
        "verified_source_inventory_root": request.identity.verified_source_inventory_root,
    }


def _bootstrap_request(
    store: MemoryAvailabilityStorage,
    *,
    trusted_marker_row_count: int | None = None,
    recorded_digest: str | None = None,
) -> BootstrapRequest:
    manifest = store.seed("evidence/bootstrap-manifest.json", b"verified manifest")
    identity = AvailabilityIdentity(
        lane_root=LANE_ROOT,
        lane="test-lane",
        product="test-lane",
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root((manifest,)),
    )
    rows = (
        *_digested_day_rows(store, identity, DIGESTED_DAY, recorded_digest=recorded_digest),
        *_trusted_day_rows(store, identity, TRUSTED_DAY, marker_row_count=trusted_marker_row_count or ROW_COUNT),
    )
    inventory = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=CEILING, object_receipts=(manifest,))
    )
    store.seed(inventory.receipt.key, inventory.payload)
    return BootstrapRequest(
        identity=identity,
        source_ceiling=CEILING,
        created_at=CREATED_AT,
        input_receipts=(inventory.receipt,),
        rows=tuple(sorted(rows, key=lambda row: (row.day, row.rung))),
        input_sha256=sha256_digest(b"bootstrap input"),
    )


def _digested_day_rows(
    store: MemoryAvailabilityStorage,
    identity: AvailabilityIdentity,
    day: date,
    *,
    recorded_digest: str | None,
) -> tuple[AvailabilityRow, ...]:
    """Seed one day whose parts exist, are hashed, and are RECORDED by their own completion marker."""
    source = _seed_source(store, identity, day)
    rows: list[AvailabilityRow] = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        payload = _parquet_payload(ROW_COUNT)
        data = store.seed(_part_key(day, rung, 0), payload)
        completion = PartitionCompletion(
            part_count=1,
            row_count=ROW_COUNT,
            completed_at=PUBLISHED_AT,
            run_id=f"test-{day}-z{rung}",
            parts=(
                CompletedPart(
                    relative_path=data.key,
                    row_count=ROW_COUNT,
                    byte_count=len(payload),
                    sha256=recorded_digest or data.sha256,
                ),
            ),
        )
        rows.append(
            _row(
                store,
                identity=identity,
                day=day,
                rung=rung,
                source=source,
                completion=completion,
                data_receipts=(data,),
                provenance=DIGESTED_PROVENANCE,
            )
        )
    return tuple(rows)


def _trusted_day_rows(
    store: MemoryAvailabilityStorage,
    identity: AvailabilityIdentity,
    day: date,
    *,
    marker_row_count: int,
) -> tuple[AvailabilityRow, ...]:
    """Seed one day bound WITHOUT its parts: no part object is written and none is ever cited."""
    source = _seed_source(store, identity, day)
    rows: list[AvailabilityRow] = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        completion = PartitionCompletion(
            part_count=1,
            row_count=marker_row_count,
            completed_at=PUBLISHED_AT,
            run_id=f"test-{day}-z{rung}",
        )
        rows.append(
            _row(
                store,
                identity=identity,
                day=day,
                rung=rung,
                source=source,
                completion=completion,
                data_receipts=(),
                provenance=MANIFEST_TRUSTED_PROVENANCE,
            )
        )
    return tuple(rows)


def _row(  # noqa: PLR0913 - one coordinate of the row being seeded per argument
    store: MemoryAvailabilityStorage,
    *,
    identity: AvailabilityIdentity,
    day: date,
    rung: int,
    source: EvidenceReceipt,
    completion: PartitionCompletion,
    data_receipts: tuple[EvidenceReceipt, ...],
    provenance: AvailabilityProvenance,
) -> AvailabilityRow:
    marker = store.seed(_completion_key(day, rung), completion.to_json_bytes())
    evidence = TerminalEvidence(
        identity=identity,
        day=day,
        rung=rung,
        terminal_state="published",
        row_count=ROW_COUNT,
        source_ceiling=CEILING,
        published_at=PUBLISHED_AT,
        source_receipt=source,
        data_receipts=data_receipts,
        completion_receipt=marker,
        absence_receipt=None,
        absence_reason=None,
        provenance=provenance,
    )
    artifact = build_terminal_evidence(evidence)
    return availability_row_from_terminal_evidence(
        evidence,
        terminal_receipt=store.seed(artifact.receipt.key, artifact.payload),
    )


def _seed_source(store: MemoryAvailabilityStorage, identity: AvailabilityIdentity, day: date) -> EvidenceReceipt:
    source_object = store.seed(f"source/{day}/response.bin", f"source:{day}".encode())
    evidence = build_source_evidence(
        SourceEvidence(identity=identity, day=day, source_ceiling=CEILING, object_receipts=(source_object,))
    )
    return store.seed(evidence.receipt.key, evidence.payload)


def _day_prefix(day: date, rung: int) -> str:
    return f"{LANE_ROOT}/zoom={rung:02d}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"


def _part_key(day: date, rung: int, part_index: int) -> str:
    return f"{_day_prefix(day, rung)}/part-{part_index}.parquet"


def _completion_key(day: date, rung: int) -> str:
    return f"{_day_prefix(day, rung)}/_complete.json"


def _parquet_payload(row_count: int) -> bytes:
    sink = io.BytesIO()
    pq.write_table(pa.table({"value": pa.nulls(row_count, type=pa.int8())}), sink)
    return sink.getvalue()


def _format(moment: datetime) -> str:
    rendered = moment.astimezone(UTC).isoformat(timespec="microseconds")
    return f"{rendered[:-6]}Z"
