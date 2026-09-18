"""Read availability evidence from the store and prove every claim it makes before it is published.

Rationale and the contracts these enforce: see `AGENTS.md` in this directory, "Availability index".
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Literal

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.absence import (
    ABSENCE_SCHEMA_VERSION,
    GovernedAbsence,
    GovernedAbsenceError,
)
from agri_data_service.foundation.parquet.completion import (
    COMPLETION_SCHEMA_VERSIONS,
    DERIVED_EMPTY_FIELD,
    PartitionCompletion,
    PartitionCompletionError,
)
from agri_data_service.foundation.parquet.completion import PARTS_FIELD as COMPLETION_PARTS_FIELD
from agri_data_service.foundation.parquet.paths import (
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.availability_documents import (
    AvailabilityIdentity,
    AvailabilityRow,
    EvidenceReceipt,
    _is_published_empty_rung,
    _parse_identity,
    _parse_optional_receipt_value,
    _parse_provenance_summary,
    _parse_receipt,
    _parse_receipts,
    _require_sorted_nonempty_receipts,
    _require_typed_receipt_key,
    availability_bootstrap_marker_key,
)
from agri_data_service.pipeline.parquet.availability_evidence import (
    BootstrapInventoryEvidence,
    SourceEvidence,
    TerminalEvidence,
)
from agri_data_service.pipeline.parquet.availability_primitives import (
    _BOOTSTRAP_INVENTORY_FIELDS,
    _BOOTSTRAP_MARKER_FIELDS,
    _SOURCE_EVIDENCE_FIELDS,
    _SYSTEM_BOOTSTRAP_FIELDS,
    _TERMINAL_EVIDENCE_FIELDS,
    BOOTSTRAP_INVENTORY_SCHEMA_VERSION,
    BOOTSTRAP_MARKER_MAX_BYTES,
    BOOTSTRAP_MARKER_SCHEMA_VERSION,
    BOOTSTRAP_RECEIPT_MAX_BYTES,
    EVIDENCE_OBJECT_MAX_BYTES,
    MANIFEST_TRUSTED_PROVENANCE,
    MAX_AVAILABILITY_ROWS,
    PROVENANCE_FIELD,
    SOURCE_EVIDENCE_SCHEMA_VERSION,
    SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
    TERMINAL_EVIDENCE_SCHEMA_VERSION,
    TYPED_RECEIPT_MAX_BYTES,
    AvailabilityChecksumError,
    AvailabilityConflictError,
    AvailabilityMalformedError,
    _decode_canonical_json_object,
    _decode_json_object,
    _optional_string,
    _parse_date,
    _parse_datetime,
    _parse_int,
    _parse_nonnegative_int,
    _parse_positive_int,
    _parse_provenance,
    _parse_terminal_state,
    _physical_lane_identity,
    _require_exact_keys,
    _require_sha256,
    _require_string,
)
from agri_data_service.pipeline.parquet.availability_storage import (
    EvidenceSnapshot,
    _dedupe_receipts,
    _dedupe_snapshots,
    _read_receipt_snapshot,
    _verify_bounded,
    _verify_raw_receipts,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.pipeline.parquet.availability_storage import AvailabilityStorage


def read_bootstrap_marker(store: AvailabilityStorage, *, lane_root: str) -> EvidenceReceipt | None:
    """Return the bootstrap receipt this lane's marker names, or `None` when it was never bootstrapped.

    ONE GET, and never a listing. A lane that answers `None` here has no availability history at all,
    which is the only state a transitional census may fall back on; a lane that answers a receipt and
    has no pointer has LOST its head, which is a fault to withhold rather than to re-prove by scan.
    """
    stored = store.read(availability_bootstrap_marker_key(lane_root), max_bytes=BOOTSTRAP_MARKER_MAX_BYTES)
    if stored is None:
        return None
    value = _decode_canonical_json_object(stored.payload, "availability bootstrap marker")
    # EVERY shape fault becomes `AvailabilityMalformedError`, because the caller is the coverage
    # reader: a bare `ValueError` out of here is not one of the four refusals it classifies and
    # would fail the whole census instead of withholding one lane.
    try:
        _require_exact_keys(value, _BOOTSTRAP_MARKER_FIELDS, "availability bootstrap marker")
        if value["schema_version"] != BOOTSTRAP_MARKER_SCHEMA_VERSION:
            raise ValueError(f"bootstrap marker schema must be {BOOTSTRAP_MARKER_SCHEMA_VERSION}")
        if value["lane_root"] != lane_root:
            raise ValueError("bootstrap marker does not describe the lane it is filed under")
        return EvidenceReceipt(
            key=_require_string(value["bootstrap_receipt_key"], "bootstrap_receipt_key"),
            sha256=_require_string(value["bootstrap_receipt_sha256"], "bootstrap_receipt_sha256"),
        )
    except ValueError as exc:
        raise AvailabilityMalformedError(f"malformed availability bootstrap marker for {lane_root!r}") from exc


def read_terminal_evidence(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    identity: AvailabilityIdentity,
) -> TerminalEvidence:
    """Read and fully verify one rung's terminal evidence wrapper and the physical objects it binds."""
    evidence, _snapshots = _verify_terminal_evidence_receipt(store, receipt, expected_identity=identity)
    return evidence


def _verify_bootstrap_inventory_receipts(
    store: AvailabilityStorage,
    receipts: Sequence[EvidenceReceipt],
    *,
    expected_identity: AvailabilityIdentity,
    expected_source_ceiling: date,
) -> tuple[tuple[BootstrapInventoryEvidence, ...], tuple[EvidenceSnapshot, ...]]:
    inventories: list[BootstrapInventoryEvidence] = []
    snapshots: list[EvidenceSnapshot] = []
    for receipt in _dedupe_receipts(receipts):
        value, wrapper_snapshot = _read_canonical_typed_document(
            store,
            receipt,
            lane_root=expected_identity.lane_root,
            purpose="bootstrap-input",
            max_bytes=BOOTSTRAP_RECEIPT_MAX_BYTES,
        )
        _require_exact_keys(value, _BOOTSTRAP_INVENTORY_FIELDS, "bootstrap inventory evidence")
        if value["schema_version"] != BOOTSTRAP_INVENTORY_SCHEMA_VERSION:
            raise AvailabilityMalformedError("unknown bootstrap inventory evidence schema")
        inventory = BootstrapInventoryEvidence(
            identity=_parse_identity(value),
            source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
            object_receipts=_parse_receipts(value["object_receipts"], "object_receipts"),
        )
        if inventory.identity != expected_identity or inventory.source_ceiling != expected_source_ceiling:
            raise AvailabilityConflictError("bootstrap inventory evidence does not match its request contract")
        inventories.append(inventory)
        snapshots.append(wrapper_snapshot)
        snapshots.extend(_verify_raw_receipts(store, inventory.object_receipts, parallel=True))
    return tuple(inventories), _dedupe_snapshots(snapshots)


def _verify_rows_evidence(
    store: AvailabilityStorage,
    rows: Sequence[AvailabilityRow],
    *,
    identity: AvailabilityIdentity,
) -> tuple[EvidenceSnapshot, ...]:
    groups: dict[str, list[AvailabilityRow]] = {}
    for row in rows:
        group = groups.setdefault(row.source_receipt.key, [])
        if group:
            first = group[0]
            if first.source_receipt.sha256 != row.source_receipt.sha256:
                raise AvailabilityChecksumError("one source evidence key was bound to different digests")
            if (first.day, first.source_ceiling) != (row.day, row.source_ceiling):
                raise AvailabilityConflictError("one source evidence receipt was reused across incompatible rows")
        group.append(row)

    def verify_group(group: list[AvailabilityRow]) -> tuple[EvidenceSnapshot, ...]:
        first = group[0]
        _, source_snapshots = _verify_source_evidence_receipt(
            store,
            first.source_receipt,
            expected_identity=identity,
            expected_day=first.day,
            expected_source_ceiling=first.source_ceiling,
        )
        snapshots = list(source_snapshots)
        for row in group:
            terminal, terminal_snapshots = _verify_terminal_evidence_receipt(
                store,
                row.terminal_receipt,
                expected_identity=identity,
            )
            _cross_bind_terminal_row(row, terminal)
            snapshots.extend(terminal_snapshots)
        return tuple(snapshots)

    verified = _verify_bounded(verify_group, tuple(groups.values()))
    return _dedupe_snapshots(tuple(snapshot for group in verified for snapshot in group))


def _verify_source_evidence_receipt(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    expected_identity: AvailabilityIdentity,
    expected_day: date,
    expected_source_ceiling: date,
) -> tuple[SourceEvidence, tuple[EvidenceSnapshot, ...]]:
    value, wrapper_snapshot = _read_canonical_typed_document(
        store,
        receipt,
        lane_root=expected_identity.lane_root,
        purpose="source",
        max_bytes=TYPED_RECEIPT_MAX_BYTES,
    )
    _require_exact_keys(value, _SOURCE_EVIDENCE_FIELDS, "source evidence")
    if value["schema_version"] != SOURCE_EVIDENCE_SCHEMA_VERSION:
        raise AvailabilityMalformedError("unknown source evidence schema")
    evidence = SourceEvidence(
        identity=_parse_identity(value),
        day=_parse_date(value["day"], "day"),
        source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
        object_receipts=_parse_receipts(value["object_receipts"], "object_receipts"),
    )
    if (
        evidence.identity != expected_identity
        or evidence.day != expected_day
        or evidence.source_ceiling != expected_source_ceiling
    ):
        raise AvailabilityConflictError("source evidence does not match its availability row")
    nested = _verify_raw_receipts(store, evidence.object_receipts)
    return evidence, _dedupe_snapshots((wrapper_snapshot, *nested))


def _verify_terminal_evidence_receipt(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    expected_identity: AvailabilityIdentity,
) -> tuple[TerminalEvidence, tuple[EvidenceSnapshot, ...]]:
    value, wrapper_snapshot = _read_canonical_typed_document(
        store,
        receipt,
        lane_root=expected_identity.lane_root,
        purpose="terminal",
        max_bytes=TYPED_RECEIPT_MAX_BYTES,
    )
    expected_fields = _TERMINAL_EVIDENCE_FIELDS
    if PROVENANCE_FIELD in value:
        # Admitted as a key, never as a value: `_require_declared_provenance` refuses any spelling
        # but `manifest_trusted`, and the canonical re-serialization already performed on this
        # payload refuses a document that says it and does not mean it.
        expected_fields = expected_fields | {PROVENANCE_FIELD}
    _require_exact_keys(value, expected_fields, "terminal evidence")
    if value["schema_version"] != TERMINAL_EVIDENCE_SCHEMA_VERSION:
        raise AvailabilityMalformedError("unknown terminal evidence schema")
    evidence = TerminalEvidence(
        identity=_parse_identity(value),
        day=_parse_date(value["day"], "day"),
        rung=_parse_int(value["rung"], "rung"),
        terminal_state=_parse_terminal_state(value["terminal_state"]),
        row_count=_parse_nonnegative_int(value["row_count"], "row_count"),
        source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
        published_at=_parse_datetime(value["published_at"], "published_at"),
        source_receipt=_parse_receipt(value["source_receipt"], "source_receipt"),
        data_receipts=_parse_receipts(value["data_receipts"], "data_receipts"),
        completion_receipt=_parse_optional_receipt_value(value["completion_receipt"], "completion_receipt"),
        absence_receipt=_parse_optional_receipt_value(value["absence_receipt"], "absence_receipt"),
        absence_reason=_optional_string(value["absence_reason"], "absence_reason"),
        provenance=_parse_provenance(value.get(PROVENANCE_FIELD)),
    )
    if evidence.identity != expected_identity:
        raise AvailabilityConflictError("terminal evidence identity does not match its lane")
    physical = _verify_terminal_physical_objects(store, evidence)
    return evidence, _dedupe_snapshots((wrapper_snapshot, *physical))


def _cross_bind_terminal_row(row: AvailabilityRow, evidence: TerminalEvidence) -> None:
    expected = (
        row.day,
        row.rung,
        row.terminal_state,
        row.row_count,
        row.source_ceiling,
        row.published_at,
        row.source_receipt,
        row.data_receipts,
        row.completion_receipt,
        row.absence_reason,
        row.provenance,
    )
    actual = (
        evidence.day,
        evidence.rung,
        evidence.terminal_state,
        evidence.row_count,
        evidence.source_ceiling,
        evidence.published_at,
        evidence.source_receipt,
        evidence.data_receipts,
        evidence.completion_receipt,
        evidence.absence_reason,
        evidence.provenance,
    )
    if actual != expected:
        raise AvailabilityConflictError("terminal evidence does not exactly bind its availability row")


def _verify_terminal_physical_objects(
    store: AvailabilityStorage,
    evidence: TerminalEvidence,
) -> tuple[EvidenceSnapshot, ...]:
    if evidence.terminal_state == "governed_absence":
        return _verify_absence_object(store, evidence)
    layer, kind = _physical_lane_identity(evidence.identity.lane_root)
    # NO DATA RECEIPTS IS A REACHABLE, VERIFIED STATE HERE -- an emptied derived rung. The loop runs
    # zero times, the contiguity check passes over an empty range, and the row count matches at zero;
    # `_verify_completion_object` then proves the marker says the same thing. Nothing is waved past.
    snapshots: list[EvidenceSnapshot] = []
    part_indexes: list[int] = []
    physical_rows = 0
    for receipt in evidence.data_receipts:
        parsed = try_parse_partition_path(receipt.key)
        if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
            layer,
            kind,
            evidence.rung,
            evidence.day,
        ):
            raise AvailabilityConflictError("data receipt path does not match terminal evidence")
        stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=EVIDENCE_OBJECT_MAX_BYTES)
        try:
            metadata = pq.ParquetFile(io.BytesIO(stored.payload)).metadata
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise AvailabilityMalformedError(f"data receipt {receipt.key!r} is not valid Parquet") from exc
        physical_rows += metadata.num_rows
        part_indexes.append(parsed.part_index)
        snapshots.append(snapshot)
    if sorted(part_indexes) != list(range(len(part_indexes))):
        raise AvailabilityConflictError("data receipt part indexes must be contiguous and ordered")
    if evidence.provenance != MANIFEST_TRUSTED_PROVENANCE and physical_rows != evidence.row_count:
        # A MANIFEST-TRUSTED ROW HAS NO PARTS TO COUNT, so this comparison would read 0 against its
        # row_count and refuse the one class it was built to admit. What that row owes instead is
        # proven in `_verify_completion_object`: the marker is fetched, digested against the receipt
        # the row binds, and made to say the same number.
        raise AvailabilityConflictError("data Parquet row counts do not match terminal row_count")
    snapshots.extend(_verify_completion_object(store, evidence, layer=layer, kind=kind))
    return tuple(snapshots)


def _verify_completion_object(
    store: AvailabilityStorage,
    evidence: TerminalEvidence,
    *,
    layer: str,
    kind: str,
) -> tuple[EvidenceSnapshot, ...]:
    receipt = evidence.completion_receipt
    if receipt is None:
        raise AvailabilityMalformedError("published terminal evidence has no completion receipt")
    parsed = try_parse_completion_marker_path(receipt.key)
    if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
        layer,
        kind,
        evidence.rung,
        evidence.day,
    ):
        raise AvailabilityConflictError("completion receipt path does not match terminal evidence")
    if parsed.derived_empty != _is_published_empty_rung(
        rung=evidence.rung, row_count=evidence.row_count, data_receipts=evidence.data_receipts
    ):
        # THE KEY NAME IS ALSO A CLAIM, and it is checked before the body is opened. A row citing
        # `_complete.json` for an emptied rung binds the receipt of a rung whose parts were deleted
        # out from under it, and a row citing `_complete.empty.json` for a rung holding parts binds
        # a receipt that says the opposite of what it points at.
        raise AvailabilityConflictError("completion receipt key and terminal evidence disagree about an emptied rung")
    stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=TYPED_RECEIPT_MAX_BYTES)
    value = _decode_json_object(stored.payload, "completion marker")
    expected_keys = {"schema_version", "part_count", "row_count", "completed_at", "run_id"}
    if DERIVED_EMPTY_FIELD in value:
        # Admitted as a key, never as a value: `PartitionCompletion.from_json_bytes` refuses any
        # spelling but `true`, and the byte-for-byte re-serialization below refuses anything else the
        # payload could be hiding. Widening the key set costs nothing that those two do not re-check.
        expected_keys.add(DERIVED_EMPTY_FIELD)
    if COMPLETION_PARTS_FIELD in value:
        # Same admission, same reason: the marker's own decoder binds this field to schema version 2
        # and refuses a partial list, and `_require_recorded_parts_agree` below makes it answer to the
        # row that cites it.
        expected_keys.add(COMPLETION_PARTS_FIELD)
    _require_exact_keys(value, expected_keys, "completion marker")
    if value["schema_version"] not in COMPLETION_SCHEMA_VERSIONS:
        raise AvailabilityMalformedError("unknown completion marker schema")
    try:
        completion = PartitionCompletion.from_json_bytes(stored.payload)
    except PartitionCompletionError as exc:
        raise AvailabilityMalformedError("invalid completion marker") from exc
    if completion.to_json_bytes() != stored.payload:
        raise AvailabilityMalformedError("completion marker does not use its authoritative serialization")
    if completion.row_count != evidence.row_count:
        raise AvailabilityConflictError("completion counts do not match terminal evidence")
    _require_part_count_agrees(completion, evidence)
    _require_recorded_parts_agree(completion, evidence, layer=layer, kind=kind)
    if completion.derived_empty != _is_published_empty_rung(
        rung=evidence.rung, row_count=evidence.row_count, data_receipts=evidence.data_receipts
    ):
        # The two statements must be the same statement. A row claiming an empty rung over an
        # ordinary receipt would bind evidence that never said the rung generalised to nothing, and
        # an ordinary row over a derived-empty receipt would serve a rung the receipt calls empty.
        raise AvailabilityConflictError("completion marker and terminal evidence disagree about an emptied rung")
    if completion.completed_at > evidence.published_at:
        raise AvailabilityConflictError("completion marker postdates terminal publication")
    return (snapshot,)


def _require_part_count_agrees(completion: PartitionCompletion, evidence: TerminalEvidence) -> None:
    """Make the marker's part count answer to the row, in the two different ways the classes allow."""
    if evidence.provenance != MANIFEST_TRUSTED_PROVENANCE:
        if completion.part_count != len(evidence.data_receipts):
            raise AvailabilityConflictError("completion counts do not match terminal evidence")
        return
    # THE WHOLE OF WHAT A TRUSTED ROW CLAIMS ABOUT PARTS: that the export said it finished holding
    # some. Nothing here re-counts the objects, because that is the download this class exists to
    # avoid; a marker claiming zero parts over a row claiming rows is still a contradiction and dies.
    if completion.part_count <= 0:
        raise AvailabilityConflictError("a manifest-trusted row binds a completion marker claiming at least one part")


def _require_recorded_parts_agree(
    completion: PartitionCompletion,
    evidence: TerminalEvidence,
    *,
    layer: str,
    kind: str,
) -> None:
    """Bind a marker that RECORDED its parts to the row citing it, so the newer receipt is not decorative.

    A marker written with per-part digests is the artifact that lets a later compile bind a day
    without downloading it, so the moment it is cited it must describe this exact rung-day: real
    partition paths, contiguous indexes, and -- for a digested row -- the same (key, sha256) set the
    row published. A marker without recorded parts is the legacy shape and states nothing to check.
    """
    if not completion.parts:
        return
    recorded: dict[str, str] = {}
    part_indexes: list[int] = []
    for part in completion.parts:
        parsed = try_parse_partition_path(part.relative_path)
        if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
            layer,
            kind,
            evidence.rung,
            evidence.day,
        ):
            raise AvailabilityConflictError("completion marker records a part outside the rung-day it closes")
        recorded[part.relative_path] = part.sha256
        part_indexes.append(parsed.part_index)
    if sorted(part_indexes) != list(range(len(part_indexes))):
        raise AvailabilityConflictError("completion marker part indexes must be contiguous and ordered")
    if evidence.provenance == MANIFEST_TRUSTED_PROVENANCE:
        return
    published = {receipt.key: receipt.sha256 for receipt in evidence.data_receipts}
    if recorded != published:
        raise AvailabilityConflictError("completion marker parts and terminal data receipts disagree")


def _verify_absence_object(
    store: AvailabilityStorage,
    evidence: TerminalEvidence,
) -> tuple[EvidenceSnapshot, ...]:
    receipt = evidence.absence_receipt
    if receipt is None:
        raise AvailabilityMalformedError("governed absence terminal evidence has no absence receipt")
    layer, kind = _physical_lane_identity(evidence.identity.lane_root)
    parsed = try_parse_absence_marker_path(receipt.key)
    if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
        layer,
        kind,
        evidence.rung,
        evidence.day,
    ):
        raise AvailabilityConflictError("absence receipt path does not match terminal evidence")
    stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=TYPED_RECEIPT_MAX_BYTES)
    value = _decode_json_object(stored.payload, "governed absence marker")
    _require_exact_keys(
        value,
        {"schema_version", "reason", "upstream_response", "recorded_at", "run_id"},
        "governed absence marker",
    )
    if value["schema_version"] != ABSENCE_SCHEMA_VERSION:
        raise AvailabilityMalformedError("unknown governed absence marker schema")
    try:
        absence = GovernedAbsence.from_json_bytes(stored.payload)
    except GovernedAbsenceError as exc:
        raise AvailabilityMalformedError("invalid governed absence marker") from exc
    if absence.to_json_bytes() != stored.payload:
        raise AvailabilityMalformedError("governed absence marker does not use its authoritative serialization")
    if absence.reason != evidence.absence_reason:
        raise AvailabilityConflictError("absence marker reason does not match terminal evidence")
    if absence.recorded_at > evidence.published_at:
        raise AvailabilityConflictError("absence marker postdates terminal publication")
    return (snapshot,)


def _read_canonical_typed_document(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    lane_root: str,
    purpose: Literal["bootstrap-input", "source", "terminal"],
    max_bytes: int,
) -> tuple[dict[str, object], EvidenceSnapshot]:
    _require_typed_receipt_key(receipt, lane_root, purpose)
    stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=max_bytes)
    return _decode_canonical_json_object(stored.payload, f"{purpose} evidence"), snapshot


def _verify_system_bootstrap_receipt(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    expected_identity: AvailabilityIdentity,
    maximum_row_count: int,
    maximum_source_ceiling: date,
) -> EvidenceSnapshot:
    expected_key = f"{expected_identity.lane_root}/availability/bootstrap/receipt={receipt.sha256}.json"
    if receipt.key != expected_key:
        raise AvailabilityConflictError("system bootstrap receipt key is outside its content-addressed lane path")
    stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=BOOTSTRAP_RECEIPT_MAX_BYTES)
    value = _decode_canonical_json_object(stored.payload, "system bootstrap receipt")
    _require_exact_keys(value, _SYSTEM_BOOTSTRAP_FIELDS, "system bootstrap receipt")
    if value["schema_version"] != SYSTEM_BOOTSTRAP_SCHEMA_VERSION:
        raise AvailabilityMalformedError("unknown system bootstrap receipt schema")
    identity = _parse_identity(value)
    if identity != expected_identity:
        raise AvailabilityConflictError("system bootstrap receipt identity does not match its pointer")
    input_receipts = _parse_receipts(value["input_receipts"], "input_receipts")
    _require_sorted_nonempty_receipts(input_receipts, "bootstrap input receipts")
    for input_receipt in input_receipts:
        expected_input_key = f"{identity.lane_root}/availability/evidence/bootstrap-input={input_receipt.sha256}.json"
        if input_receipt.key != expected_input_key:
            raise AvailabilityConflictError("system bootstrap receipt references an invalid bootstrap input key")
    _require_sha256(
        _require_string(value["bootstrap_input_sha256"], "bootstrap_input_sha256"),
        "bootstrap_input_sha256",
    )
    _require_sha256(_require_string(value["outcome_sha256"], "outcome_sha256"), "outcome_sha256")
    bootstrap_source_ceiling = _parse_date(value["source_ceiling"], "source_ceiling")
    if bootstrap_source_ceiling > maximum_source_ceiling:
        raise AvailabilityConflictError("system bootstrap source ceiling exceeds its generation contract")
    _parse_datetime(value["created_at"], "created_at")
    row_count = _parse_positive_int(value["row_count"], "row_count")
    if row_count > min(MAX_AVAILABILITY_ROWS, maximum_row_count):
        raise AvailabilityMalformedError("system bootstrap receipt row_count exceeds the generation bound")
    provenance = _parse_provenance_summary(value["provenance"], "system bootstrap provenance")
    if sum(provenance.values()) != row_count:
        raise AvailabilityMalformedError("system bootstrap provenance classes do not account for every row")
    return snapshot
