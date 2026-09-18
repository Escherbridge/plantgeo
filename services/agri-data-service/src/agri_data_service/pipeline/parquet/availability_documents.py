"""Lane identity, terminal rows, the generation pointer and the parsers that admit them.

Rationale and the contracts these enforce: see `AGENTS.md` in this directory, "Availability index".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.paths import (
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.availability_primitives import (
    BOOTSTRAP_MARKER_SCHEMA_VERSION,
    DIGESTED_PROVENANCE,
    GENERATION_MAX_BYTES,
    MANIFEST_TRUSTED_PROVENANCE,
    MAX_AVAILABILITY_ROWS,
    POINTER_MAX_BYTES,
    PROVENANCE_FIELD,
    AvailabilityConflictError,
    AvailabilityMalformedError,
    AvailabilityNature,
    AvailabilityProvenance,
    TerminalState,
    _decode_json_object,
    _format_datetime,
    _generation_sha_from_key,
    _optional_string,
    _parse_date,
    _parse_datetime,
    _parse_int,
    _parse_nature,
    _parse_nonnegative_int,
    _parse_positive_int,
    _parse_rungs,
    _parse_terminal_state,
    _physical_lane_identity,
    _require_exact_keys,
    _require_lane_root,
    _require_mapping,
    _require_name,
    _require_nature,
    _require_object_key,
    _require_prior_binding,
    _require_rung,
    _require_rungs,
    _require_sha256,
    _require_string,
    _require_utc,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_SCHEMA_VERSION

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    """One object key and its required byte digest."""

    key: str
    sha256: str

    def __post_init__(self) -> None:
        _require_object_key(self.key, "receipt key")
        _require_sha256(self.sha256, "receipt sha256")

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {"key": self.key, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class AvailabilityIdentity:
    """Stable lane identity and authoritative rung contract."""

    lane_root: str
    lane: str
    product: str
    nature: AvailabilityNature
    required_rungs: tuple[int, ...]
    verified_source_inventory_root: str

    def __post_init__(self) -> None:
        _require_lane_root(self.lane_root)
        _require_name(self.lane, "lane")
        _require_name(self.product, "product")
        _require_nature(self.nature)
        _require_rungs(self.required_rungs)
        _require_sha256(self.verified_source_inventory_root, "verified_source_inventory_root")


@dataclass(frozen=True, slots=True)
class AvailabilityConfig:
    """Generation identity plus its immutable bootstrap binding and current ceiling."""

    identity: AvailabilityIdentity
    source_ceiling: date
    bootstrap_receipt: EvidenceReceipt


@dataclass(frozen=True, slots=True)
class AvailabilityRow:
    """One terminal `(day, rung)` outcome."""

    lane: str
    product: str
    nature: AvailabilityNature
    day: date
    rung: int
    terminal_state: TerminalState
    row_count: int
    source_receipt: EvidenceReceipt
    terminal_receipt: EvidenceReceipt
    data_receipts: tuple[EvidenceReceipt, ...]
    completion_receipt: EvidenceReceipt | None
    absence_reason: str | None
    source_ceiling: date
    published_at: datetime

    def __post_init__(self) -> None:
        _require_name(self.lane, "lane")
        _require_name(self.product, "product")
        _require_nature(self.nature)
        _require_rung(self.rung)
        if self.terminal_state not in ("published", "governed_absence"):
            raise ValueError("terminal_state must be published or governed_absence")
        if self.day > self.source_ceiling:
            raise ValueError("availability day cannot exceed its source ceiling")
        _require_utc(self.published_at, "published_at")
        _validate_data_receipt_collection(self.data_receipts)
        _validate_terminal_payload(self)

    @property
    def grain(self) -> tuple[date, int]:
        """Return the unique generation grain."""
        return self.day, self.rung

    @property
    def provenance(self) -> AvailabilityProvenance:
        """Return how well this row's parts are proven, DERIVED from the row's own shape."""
        return availability_row_provenance(
            terminal_state=self.terminal_state,
            row_count=self.row_count,
            data_receipts=self.data_receipts,
        )

    def evidence_receipts(self) -> tuple[EvidenceReceipt, ...]:
        """Return every object the row binds."""
        completion = () if self.completion_receipt is None else (self.completion_receipt,)
        return (self.source_receipt, self.terminal_receipt, *self.data_receipts, *completion)

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON and Arrow-compatible projection."""
        return {
            "absence_reason": self.absence_reason,
            "completion_receipt_key": None if self.completion_receipt is None else self.completion_receipt.key,
            "completion_receipt_sha256": (None if self.completion_receipt is None else self.completion_receipt.sha256),
            "data_receipts": [receipt.to_wire() for receipt in self.data_receipts],
            "day": self.day.isoformat(),
            "lane": self.lane,
            "nature": self.nature,
            "product": self.product,
            "published_at": _format_datetime(self.published_at),
            "row_count": self.row_count,
            "rung": self.rung,
            "source_ceiling": self.source_ceiling.isoformat(),
            "source_receipt_key": self.source_receipt.key,
            "source_receipt_sha256": self.source_receipt.sha256,
            "terminal_receipt_key": self.terminal_receipt.key,
            "terminal_receipt_sha256": self.terminal_receipt.sha256,
            "terminal_state": self.terminal_state,
        }

    def to_arrow(self) -> dict[str, object]:
        """Return the typed Arrow row projection."""
        payload = self.to_wire()
        payload["day"] = self.day
        payload["source_ceiling"] = self.source_ceiling
        payload["published_at"] = self.published_at
        return payload


def _validate_data_receipt_collection(receipts: tuple[EvidenceReceipt, ...]) -> None:
    if not isinstance(receipts, tuple):
        raise ValueError("data_receipts must be an immutable tuple")
    receipt_keys = tuple(receipt.key for receipt in receipts)
    if receipt_keys != tuple(sorted(set(receipt_keys))):
        raise ValueError("data_receipts must be sorted by unique object key")


def _validate_terminal_payload(row: AvailabilityRow) -> None:
    if row.terminal_state == "published":
        if row.absence_reason is not None:
            raise ValueError("a published availability row cannot carry an absence reason")
        if row.completion_receipt is None:
            raise ValueError("a published availability row requires a completion receipt")
        if _is_published_empty_rung(rung=row.rung, row_count=row.row_count, data_receipts=row.data_receipts):
            return
        if row.row_count <= 0:
            raise ValueError("a published availability row must carry a positive row_count")
        # NO PART RECEIPTS AND ROWS TO SHOW IS THE MANIFEST-TRUSTED SHAPE, and it is admitted HERE
        # rather than refused because the row asserts nothing about parts it did not hash. The
        # completion receipt proven above is the whole of its claim.
        #
        # WHAT KEEPS IT OFF THE FORWARD PATH IS NOT THIS FUNCTION. Two guards do, and neither is
        # here: `_refuse_trusted_publication_rows`, called from the single publish chokepoint in
        # `availability_index.py` that the CLI and `availability_extension` both pass through --
        # refuses the class on any publication; and `_require_declared_provenance` refuses a `TerminalEvidence` whose
        # declared class disagrees with its shape, so a forward writer that dropped its
        # `data_receipts` dies at construction rather than becoming trusted by accident.
        return
    if row.row_count != 0:
        raise ValueError("a governed absence must carry row_count=0")
    if row.data_receipts or row.completion_receipt is not None:
        raise ValueError("a governed absence cannot carry data or completion receipts")
    if row.absence_reason is None or not row.absence_reason.strip():
        raise ValueError("a governed absence requires a non-blank reason")
    if row.absence_reason != row.absence_reason.strip():
        raise ValueError("absence_reason must use canonical trimmed spelling")


def _is_published_empty_rung(*, rung: int, row_count: int, data_receipts: tuple[EvidenceReceipt, ...]) -> bool:
    """True for the one published shape that carries no rows: a DERIVED rung that generalised to none.

    THE DAY IS PUBLISHED AT EVERY RUNG, and this rung of it is empty -- not absent. A governed
    absence claims the SOURCE had nothing, which is false here: the base rung demonstrably holds the
    rows this rung dropped, and a day whose ladder mixed the two terminal states would be refused by
    `_validate_generation_day` and unselectable at every rung rather than merely unindexed.

    THE BASE RUNG IS EXCLUDED, and that exclusion is what keeps the two vocabularies apart. A base
    rung holding no rows is a governed absence and has its own marker; only a rung DERIVED from a
    non-empty base can honestly say "the rows existed and none of them survived my resolution".
    `foundation/parquet/completion.py::PartitionCompletion` refuses the receipt this row binds unless
    it says the same thing, and `objectstore.write_completion_marker` refuses one at the base rung.
    """
    return rung != BASE_ZOOM_TIER and row_count == 0 and not data_receipts


def availability_row_provenance(
    *,
    terminal_state: TerminalState,
    row_count: int,
    data_receipts: tuple[EvidenceReceipt, ...],
) -> AvailabilityProvenance:
    """Classify how well one terminal outcome's parts are proven, FROM ITS SHAPE ALONE.

    THE SHAPE IS THE DECLARATION, and it has to be, because `AVAILABILITY_INDEX_SCHEMA` is frozen at
    version 1: a provenance COLUMN would not survive the generation round trip that `_write_generation`
    re-reads and compares. Published, holding rows, and naming no part is a claim only a
    manifest-trusted row can make -- an ordinary published row always names the parts it counted, and
    a rung that generalised to nothing carries `row_count == 0` and is classified `digested` because
    its emptiness is proven outright by its own marker.

    The bootstrap-input document and the terminal evidence may DECLARE the class in words; both are
    checked against this function, so the two can never disagree.
    """
    if terminal_state == "published" and row_count > 0 and not data_receipts:
        return MANIFEST_TRUSTED_PROVENANCE
    return DIGESTED_PROVENANCE


def availability_provenance_summary(rows: Sequence[AvailabilityRow]) -> dict[str, object]:
    """Return the per-class row count and day range a receipt must carry, in canonical spelling."""
    summary: dict[str, object] = {}
    for provenance in (DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE):
        days = sorted({row.day for row in rows if row.provenance == provenance})
        summary[provenance] = {
            "earliest_day": days[0].isoformat() if days else None,
            "latest_day": days[-1].isoformat() if days else None,
            "row_count": sum(1 for row in rows if row.provenance == provenance),
        }
    return summary


def _parse_provenance_summary(value: object, label: str) -> dict[str, int]:
    """Validate a receipt's provenance summary and return its per-class row counts."""
    mapping = _require_mapping(value, label)
    _require_exact_keys(mapping, {DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE}, label)
    counts: dict[str, int] = {}
    for provenance in (DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE):
        entry = _require_mapping(mapping[provenance], f"{label} {provenance}")
        _require_exact_keys(entry, {"earliest_day", "latest_day", "row_count"}, f"{label} {provenance}")
        row_count = _parse_nonnegative_int(entry["row_count"], f"{label} {provenance} row_count")
        earliest = entry["earliest_day"]
        latest = entry["latest_day"]
        if (earliest is None) != (latest is None) or (row_count == 0) != (earliest is None):
            raise ValueError(f"{label} {provenance} must carry a day range exactly when it counts rows")
        if earliest is not None and _parse_date(earliest, "earliest_day") > _parse_date(latest, "latest_day"):
            raise ValueError(f"{label} {provenance} day range is inverted")
        counts[provenance] = row_count
    return counts


@dataclass(frozen=True, slots=True)
class AvailabilityPointer:
    """Checksum-bound mutable pointer to one immutable generation."""

    schema_version: str
    identity: AvailabilityIdentity
    required_rungs: tuple[int, ...]
    generation_key: str
    generation_sha256: str
    generation_receipt_sha256: str
    generation_bytes: int
    rows: int
    earliest_terminal_day: date
    latest_terminal_day: date
    source_ceiling: date
    prior_generation_key: str | None
    prior_generation_sha256: str | None
    created_at: datetime
    bootstrap_receipt: EvidenceReceipt

    def __post_init__(self) -> None:
        if self.schema_version != AVAILABILITY_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {AVAILABILITY_SCHEMA_VERSION}")
        if self.required_rungs != self.identity.required_rungs:
            raise ValueError("pointer required_rungs do not match its identity")
        _require_sha256(self.generation_sha256, "generation_sha256")
        _require_sha256(self.generation_receipt_sha256, "generation_receipt_sha256")
        if _generation_sha_from_key(self.identity.lane_root, self.generation_key) != self.generation_sha256:
            raise ValueError("generation_key digest does not match generation_sha256")
        if not 0 < self.generation_bytes <= GENERATION_MAX_BYTES:
            raise ValueError("pointer generation_bytes exceeds the bounded generation contract")
        if not 0 < self.rows <= MAX_AVAILABILITY_ROWS:
            raise ValueError("pointer rows exceed the bounded generation contract")
        if self.earliest_terminal_day > self.latest_terminal_day:
            raise ValueError("pointer terminal-day range is inverted")
        if self.latest_terminal_day > self.source_ceiling:
            raise ValueError("pointer latest terminal day exceeds source ceiling")
        _require_prior_binding(
            self.prior_generation_key,
            self.prior_generation_sha256,
            self.identity.lane_root,
        )
        _require_utc(self.created_at, "created_at")

    def to_wire(self) -> dict[str, object]:
        """Return the exact pointer document."""
        return {
            "bootstrap_receipt_key": self.bootstrap_receipt.key,
            "bootstrap_receipt_sha256": self.bootstrap_receipt.sha256,
            "created_at": _format_datetime(self.created_at),
            "earliest_terminal_day": self.earliest_terminal_day.isoformat(),
            "generation_bytes": self.generation_bytes,
            "generation_key": self.generation_key,
            "generation_receipt_sha256": self.generation_receipt_sha256,
            "generation_sha256": self.generation_sha256,
            "lane": self.identity.lane,
            "lane_root": self.identity.lane_root,
            "latest_terminal_day": self.latest_terminal_day.isoformat(),
            "nature": self.identity.nature,
            "prior_generation_key": self.prior_generation_key,
            "prior_generation_sha256": self.prior_generation_sha256,
            "product": self.identity.product,
            "required_rungs": list(self.required_rungs),
            "rows": self.rows,
            "schema_version": self.schema_version,
            "source_ceiling": self.source_ceiling.isoformat(),
            "verified_source_inventory_root": self.identity.verified_source_inventory_root,
        }


@dataclass(frozen=True, slots=True)
class AvailabilityIndex:
    """One verified pointer and its immutable generation rows."""

    pointer: AvailabilityPointer
    rows: tuple[AvailabilityRow, ...]

    def selectable_days(self) -> tuple[date, ...]:
        """Return days whose exact authoritative rung set agrees on one terminal state."""
        by_day: dict[date, list[AvailabilityRow]] = {}
        for row in self.rows:
            by_day.setdefault(row.day, []).append(row)
        selectable: list[date] = []
        expected = self.pointer.required_rungs
        for day in sorted(by_day):
            rows = sorted(by_day[day], key=lambda item: expected.index(item.rung))
            if tuple(row.rung for row in rows) != expected:
                continue
            states = {row.terminal_state for row in rows}
            source_receipts = {row.source_receipt.sha256 for row in rows}
            absence_reasons = {row.absence_reason for row in rows}
            if len(states) == 1 and len(source_receipts) == 1 and len(absence_reasons) == 1:
                selectable.append(day)
        return tuple(selectable)


def availability_pointer_key(lane_root: str) -> str:
    """Return the mutable pointer key beneath one physical lane root."""
    _require_lane_root(lane_root)
    return f"{lane_root}/availability/_LATEST.json"


def availability_generation_key(lane_root: str, generation_sha256: str) -> str:
    """Return the immutable content-addressed Parquet key."""
    _require_lane_root(lane_root)
    _require_sha256(generation_sha256, "generation sha256")
    return f"{lane_root}/availability/generation={generation_sha256}/availability.parquet"


def availability_lane_identity(lane_root: str) -> tuple[str, str]:
    """Return one lane root's `(layer, kind)`, refusing anything outside the frozen layout."""
    return _physical_lane_identity(lane_root)


def availability_bootstrap_marker_key(lane_root: str) -> str:
    """Return the DETERMINISTIC key naming this lane's immutable bootstrap receipt."""
    _require_lane_root(lane_root)
    return f"{lane_root}/availability/bootstrap/_BOOTSTRAPPED.json"


def _bootstrap_marker_payload(lane_root: str, receipt: EvidenceReceipt) -> bytes:
    """Render the marker. Receipt-derived ONLY, so a re-attempt writes byte-identical immutable content."""
    return canonical_json(
        {
            "bootstrap_receipt_key": receipt.key,
            "bootstrap_receipt_sha256": receipt.sha256,
            "lane_root": lane_root,
            "schema_version": BOOTSTRAP_MARKER_SCHEMA_VERSION,
        }
    ).encode("utf-8")


def _parse_pointer(payload: bytes, *, expected_lane_root: str) -> AvailabilityPointer:
    value = _decode_json_object(payload, "availability pointer")
    _require_exact_keys(
        value,
        {
            "bootstrap_receipt_key",
            "bootstrap_receipt_sha256",
            "created_at",
            "earliest_terminal_day",
            "generation_bytes",
            "generation_key",
            "generation_receipt_sha256",
            "generation_sha256",
            "lane",
            "lane_root",
            "latest_terminal_day",
            "nature",
            "prior_generation_key",
            "prior_generation_sha256",
            "product",
            "required_rungs",
            "rows",
            "schema_version",
            "source_ceiling",
            "verified_source_inventory_root",
        },
        "availability pointer",
    )
    required_rungs = _parse_rungs(value["required_rungs"])
    identity = AvailabilityIdentity(
        lane_root=_require_string(value["lane_root"], "lane_root"),
        lane=_require_string(value["lane"], "lane"),
        product=_require_string(value["product"], "product"),
        nature=_parse_nature(value["nature"]),
        required_rungs=required_rungs,
        verified_source_inventory_root=_require_string(
            value["verified_source_inventory_root"], "verified_source_inventory_root"
        ),
    )
    if identity.lane_root != expected_lane_root:
        raise ValueError("availability pointer lane_root does not match its object path")
    pointer = AvailabilityPointer(
        schema_version=_require_string(value["schema_version"], "schema_version"),
        identity=identity,
        required_rungs=required_rungs,
        generation_key=_require_string(value["generation_key"], "generation_key"),
        generation_sha256=_require_string(value["generation_sha256"], "generation_sha256"),
        generation_receipt_sha256=_require_string(value["generation_receipt_sha256"], "generation_receipt_sha256"),
        generation_bytes=_parse_positive_int(value["generation_bytes"], "generation_bytes"),
        rows=_parse_positive_int(value["rows"], "rows"),
        earliest_terminal_day=_parse_date(value["earliest_terminal_day"], "earliest_terminal_day"),
        latest_terminal_day=_parse_date(value["latest_terminal_day"], "latest_terminal_day"),
        source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
        prior_generation_key=_optional_string(value["prior_generation_key"], "prior_generation_key"),
        prior_generation_sha256=_optional_string(value["prior_generation_sha256"], "prior_generation_sha256"),
        created_at=_parse_datetime(value["created_at"], "created_at"),
        bootstrap_receipt=EvidenceReceipt(
            key=_require_string(value["bootstrap_receipt_key"], "bootstrap_receipt_key"),
            sha256=_require_string(value["bootstrap_receipt_sha256"], "bootstrap_receipt_sha256"),
        ),
    )
    if pointer.schema_version != AVAILABILITY_SCHEMA_VERSION:
        raise ValueError(f"availability pointer schema_version must be {AVAILABILITY_SCHEMA_VERSION}")
    _require_sha256(pointer.generation_sha256, "generation_sha256")
    _require_sha256(pointer.generation_receipt_sha256, "generation_receipt_sha256")
    if _generation_sha_from_key(identity.lane_root, pointer.generation_key) != pointer.generation_sha256:
        raise ValueError("generation_key digest does not match generation_sha256")
    _require_prior_binding(pointer.prior_generation_key, pointer.prior_generation_sha256, identity.lane_root)
    if pointer.earliest_terminal_day > pointer.latest_terminal_day:
        raise ValueError("availability pointer terminal-day range is inverted")
    if pointer.latest_terminal_day > pointer.source_ceiling:
        raise ValueError("availability pointer latest terminal day exceeds source ceiling")
    return pointer


def _pointer_payload(pointer: AvailabilityPointer) -> bytes:
    payload = canonical_json(pointer.to_wire()).encode("utf-8")
    if len(payload) > POINTER_MAX_BYTES:
        raise AvailabilityMalformedError("availability pointer exceeds its byte ceiling")
    return payload


def _generation_receipt_sha256(
    *,
    config: AvailabilityConfig,
    rows: tuple[AvailabilityRow, ...],
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> str:
    payload = {
        "bootstrap_receipt": config.bootstrap_receipt.to_wire(),
        "created_at": _format_datetime(created_at),
        "lane": config.identity.lane,
        "lane_root": config.identity.lane_root,
        "nature": config.identity.nature,
        "prior_generation_key": prior_generation_key,
        "prior_generation_sha256": prior_generation_sha256,
        "product": config.identity.product,
        "required_rungs": list(config.identity.required_rungs),
        "rows": [row.to_wire() for row in rows],
        "schema_version": AVAILABILITY_SCHEMA_VERSION,
        "source_ceiling": config.source_ceiling.isoformat(),
        "verified_source_inventory_root": config.identity.verified_source_inventory_root,
    }
    return sha256_digest(canonical_json(payload))


def _require_typed_receipt_key(
    receipt: EvidenceReceipt,
    lane_root: str,
    purpose: Literal["bootstrap-input", "source", "terminal"],
) -> None:
    expected_key = f"{lane_root}/availability/evidence/{purpose}={receipt.sha256}.json"
    if receipt.key != expected_key:
        raise AvailabilityConflictError(f"{purpose} receipt key is not content-addressed for its lane")


def _parse_receipt(value: object, label: str) -> EvidenceReceipt:
    mapping = _require_mapping(value, label)
    _require_exact_keys(mapping, {"key", "sha256"}, label)
    return EvidenceReceipt(
        key=_require_string(mapping["key"], f"{label} key"),
        sha256=_require_string(mapping["sha256"], f"{label} sha256"),
    )


def _parse_optional_receipt_value(value: object, label: str) -> EvidenceReceipt | None:
    if value is None:
        return None
    return _parse_receipt(value, label)


def _require_sorted_nonempty_receipts(receipts: Sequence[EvidenceReceipt], label: str) -> None:
    if not receipts:
        raise ValueError(f"{label} must be non-empty")
    keys = tuple(receipt.key for receipt in receipts)
    if keys != tuple(sorted(set(keys))):
        raise ValueError(f"{label} must use sorted unique object keys")


def _validate_generation_rows(
    rows: tuple[AvailabilityRow, ...],
    *,
    identity: AvailabilityIdentity,
    source_ceiling: date,
) -> None:
    if not rows or len(rows) > MAX_AVAILABILITY_ROWS:
        raise ValueError(f"availability generation must contain 1..{MAX_AVAILABILITY_ROWS} rows")
    grains: set[tuple[date, int]] = set()
    rows_by_day: dict[date, list[AvailabilityRow]] = {}
    for row in rows:
        _validate_generation_row(row, identity=identity, source_ceiling=source_ceiling)
        if row.grain in grains:
            raise ValueError(f"duplicate availability grain {row.day}/z{row.rung}")
        grains.add(row.grain)
        rows_by_day.setdefault(row.day, []).append(row)
    expected_order = tuple(sorted(rows, key=lambda row: (row.day, row.rung)))
    if rows != expected_order:
        raise ValueError("availability rows must be sorted by day then rung")
    for day, day_rows in rows_by_day.items():
        _validate_generation_day(day, day_rows, required_rungs=identity.required_rungs)
    if max(row.source_ceiling for row in rows) != source_ceiling:
        raise ValueError("generation source ceiling must equal the maximum receipt-bound row source ceiling")


def _validate_generation_row(
    row: AvailabilityRow,
    *,
    identity: AvailabilityIdentity,
    source_ceiling: date,
) -> None:
    if (row.lane, row.product, row.nature) != (identity.lane, identity.product, identity.nature):
        raise ValueError("availability row identity does not match its generation")
    if row.rung not in identity.required_rungs:
        raise ValueError(f"availability row rung {row.rung} is outside required_rungs")
    if row.source_ceiling > source_ceiling:
        raise ValueError("availability row source ceiling exceeds its generation source ceiling")
    _require_typed_receipt_key(row.source_receipt, identity.lane_root, "source")
    _require_typed_receipt_key(row.terminal_receipt, identity.lane_root, "terminal")
    if row.terminal_state == "published":
        layer, kind = _physical_lane_identity(identity.lane_root)
        for receipt in row.data_receipts:
            parsed = try_parse_partition_path(receipt.key)
            if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
                layer,
                kind,
                row.rung,
                row.day,
            ):
                raise ValueError("availability data receipt path does not match its row")
        if row.completion_receipt is None:
            raise ValueError("published availability row requires a completion receipt")
        completion = try_parse_completion_marker_path(row.completion_receipt.key)
        if completion is None or (completion.layer, completion.kind, completion.zoom, completion.day) != (
            layer,
            kind,
            row.rung,
            row.day,
        ):
            raise ValueError("availability completion receipt path does not match its row")


def _validate_generation_day(
    day: date,
    rows: list[AvailabilityRow],
    *,
    required_rungs: tuple[int, ...],
) -> None:
    if tuple(row.rung for row in rows) != required_rungs:
        raise ValueError(f"availability day {day} does not contain the exact required_rungs ladder")
    if len({row.terminal_state for row in rows}) != 1:
        raise ValueError(f"availability day {day} mixes terminal states across its ladder")
    if len({row.source_receipt for row in rows}) != 1:
        raise ValueError(f"availability day {day} mixes source receipts across its ladder")
    if len({row.source_ceiling for row in rows}) != 1:
        raise ValueError(f"availability day {day} mixes source ceilings across its ladder")
    if len({row.absence_reason for row in rows}) != 1:
        raise ValueError(f"availability day {day} mixes absence reasons across its ladder")


def _require_rows_published_by(rows: Sequence[AvailabilityRow], created_at: datetime) -> None:
    for row in rows:
        if row.published_at > created_at:
            raise ValueError("availability row published_at cannot exceed generation created_at")


def _parse_identity(value: Mapping[str, object]) -> AvailabilityIdentity:
    return AvailabilityIdentity(
        lane_root=_require_string(value["lane_root"], "lane_root"),
        lane=_require_string(value["lane"], "lane"),
        product=_require_string(value["product"], "product"),
        nature=_parse_nature(value["nature"]),
        required_rungs=_parse_rungs(value["required_rungs"]),
        verified_source_inventory_root=_require_string(
            value["verified_source_inventory_root"], "verified_source_inventory_root"
        ),
    )


def _parse_rows(value: object, *, identity: AvailabilityIdentity) -> tuple[AvailabilityRow, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_AVAILABILITY_ROWS:
        raise ValueError(f"rows must contain 1..{MAX_AVAILABILITY_ROWS} entries")
    rows = tuple(_row_from_mapping(_require_mapping(item, "row")) for item in value)
    for row in rows:
        if (row.lane, row.product, row.nature) != (identity.lane, identity.product, identity.nature):
            raise ValueError("row identity does not match input identity")
    return rows


def _row_from_mapping(value: Mapping[str, object]) -> AvailabilityRow:
    # ONE PARSER, TWO CALLERS: the offline input document, where a row MAY declare its provenance
    # class in words, and the Arrow generation, where it never can -- `AVAILABILITY_INDEX_SCHEMA` is
    # frozen at version 1 and holds no such column. The declaration is therefore optional, and it is
    # checked against the shape rather than believed; the shape is what survives the round trip.
    declared = value.get(PROVENANCE_FIELD)
    expected_keys = set(_AVAILABILITY_ROW_FIELDS)
    if PROVENANCE_FIELD in value:
        expected_keys.add(PROVENANCE_FIELD)
    _require_exact_keys(value, expected_keys, "availability row")
    row = _row_fields(value)
    if declared is not None and _parse_declared_row_provenance(declared) != row.provenance:
        raise ValueError(
            f"availability row declares {declared!r} and has the shape of {row.provenance}; a manifest-trusted "
            "row publishes rows and names no part digest, and nothing else may claim that class"
        )
    return row


def _parse_declared_row_provenance(value: object) -> AvailabilityProvenance:
    """Read a row's DECLARED class. Both spellings are admitted: the input document is compiled, not derived."""
    if value == DIGESTED_PROVENANCE:
        return DIGESTED_PROVENANCE
    if value == MANIFEST_TRUSTED_PROVENANCE:
        return MANIFEST_TRUSTED_PROVENANCE
    raise ValueError(f"{PROVENANCE_FIELD} must be {DIGESTED_PROVENANCE} or {MANIFEST_TRUSTED_PROVENANCE}")


_AVAILABILITY_ROW_FIELDS: Final = {
    "absence_reason",
    "completion_receipt_key",
    "completion_receipt_sha256",
    "data_receipts",
    "day",
    "lane",
    "nature",
    "product",
    "published_at",
    "row_count",
    "rung",
    "source_ceiling",
    "source_receipt_key",
    "source_receipt_sha256",
    "terminal_receipt_key",
    "terminal_receipt_sha256",
    "terminal_state",
}


def _row_fields(value: Mapping[str, object]) -> AvailabilityRow:
    terminal_state = _parse_terminal_state(value["terminal_state"])
    return AvailabilityRow(
        lane=_require_string(value["lane"], "lane"),
        product=_require_string(value["product"], "product"),
        nature=_parse_nature(value["nature"]),
        day=_parse_date(value["day"], "day"),
        rung=_parse_int(value["rung"], "rung"),
        terminal_state=terminal_state,
        row_count=_parse_nonnegative_int(value["row_count"], "row_count"),
        source_receipt=EvidenceReceipt(
            key=_require_string(value["source_receipt_key"], "source_receipt_key"),
            sha256=_require_string(value["source_receipt_sha256"], "source_receipt_sha256"),
        ),
        terminal_receipt=EvidenceReceipt(
            key=_require_string(value["terminal_receipt_key"], "terminal_receipt_key"),
            sha256=_require_string(value["terminal_receipt_sha256"], "terminal_receipt_sha256"),
        ),
        data_receipts=_parse_receipts(value["data_receipts"], "data_receipts"),
        completion_receipt=_optional_receipt(value, "completion_receipt_key", "completion_receipt_sha256"),
        absence_reason=_optional_string(value["absence_reason"], "absence_reason"),
        source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
        published_at=_parse_datetime(value["published_at"], "published_at"),
    )


def _optional_receipt(value: Mapping[str, object], key_name: str, sha_name: str) -> EvidenceReceipt | None:
    key = value[key_name]
    sha256 = value[sha_name]
    if key is None and sha256 is None:
        return None
    if key is None or sha256 is None:
        raise ValueError(f"{key_name} and {sha_name} must both be null or both be strings")
    return EvidenceReceipt(key=_require_string(key, key_name), sha256=_require_string(sha256, sha_name))


def _parse_receipts(value: object, label: str) -> tuple[EvidenceReceipt, ...]:
    if not isinstance(value, list) or len(value) > MAX_AVAILABILITY_ROWS:
        raise ValueError(f"{label} must be a bounded list")
    receipts: list[EvidenceReceipt] = []
    for item in value:
        mapping = _require_mapping(item, label)
        _require_exact_keys(mapping, {"key", "sha256"}, label)
        receipts.append(
            EvidenceReceipt(
                key=_require_string(mapping["key"], "receipt key"),
                sha256=_require_string(mapping["sha256"], "receipt sha256"),
            )
        )
    return tuple(receipts)


def _require_expected_rows(rows: tuple[AvailabilityRow, ...], expected_row_count: int) -> None:
    if not 1 <= expected_row_count <= MAX_AVAILABILITY_ROWS:
        raise ValueError(f"expected-row-count must be between 1 and {MAX_AVAILABILITY_ROWS}")
    if len(rows) != expected_row_count:
        raise ValueError(f"availability input holds {len(rows)} rows, not expected-row-count={expected_row_count}")
