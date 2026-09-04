"""Completion-marker payload: the receipt a finished multi-part stream-day writes LAST.

Layer L0: stdlib only. The marker's object KEY lives in `paths.py`; this module owns what is
inside the marker. One convention for every stream -- no lane defines its own.

WHY A THIRD OBJECT KIND EXISTS AT ALL (owner, RUNBOOK 0.34.1). A day's parts are uploaded one at a
time, so a run killed part-way leaves a PREFIX of them behind. Every one of those parts is new, so
`oldest_export_instant` still reads at or after the source watermark and the day resolves as
`current` -- a half-written release published as a finished one. The cheaper fix, a rule that a day
whose last export `raised` must be re-exported, only fires when a failure was RECORDED: a container
replaced mid-write records nothing, and deploys replacing a mid-tick container is the normal case
here. So completion is asserted by an object written after the last part, and a day without one is
not finished no matter how many parts it holds.

THE MARKER IS A RECEIPT, NOT A LOCK. It says what the export that wrote it uploaded -- how many
parts, how many rows, when it finished, under which run. Nothing consults it to decide whether a
write may proceed; the census consults it to decide whether a day may be BELIEVED.

ONE RECEIPT CLAIMS NOTHING WAS WRITTEN, AND IT IS NOT A GOVERNED ABSENCE. See `AGENTS.md` in this
directory, "The zero-part receipt: a rung that generalised to nothing".

THE RECEIPT MAY ALSO CARRY EACH PART'S DIGEST (owner decision D3, track
`environmental_postgres_retirement_20260904`). A marker written with `parts` lets a later reader bind
that day's parts by key and sha256 without downloading them, because the export hashed the bytes it
uploaded. Days written before the field exists have no such record, so
`scripts/compile_availability_bootstrap.py` binds them as a MANIFEST-TRUSTED availability row
instead.

ONLY THE DERIVED RUNGS ARE WRITTEN THAT WAY TODAY, so only they stop the trusted region growing.
`derivation.py::_write_tier` populates `parts` from the receipts it already holds; the BASE rung's
marker (`pipeline/parquet/gap_fill.py::_finalize_written_day`) still writes version 1 forever,
because what reaches it is `LaneRunResult` -- three summed integers with every `relative_path` and
`sha256` folded away. That is named, owed work, not an oversight, and its unlock condition is stated:
thread the `WrittenObjectLedger` `fill_one_lane_day` already opens into the marker write AND carry
the soft `len(parts) != part_count` guard `availability_extension._rung_objects_from_ledger` applies,
because `_fill_static_day` retries one day through that single ledger and a stale entry would make
`_validate_parts` raise INSIDE the marker write -- turning a correctly finished export into `raised`.
See `pipeline/parquet/AGENTS.md`, "Per-part digests: `_write_tier` wired, `_finalize_written_day`
stays v1 (D3)". Forward availability publication is unaffected either way: it binds parts from that
ledger, not from the marker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

#: What a marker carrying no per-part digests declares. EVERY marker in the bucket today is this
#: version, and stays readable forever: the version is a statement about the marker's CONTENT, not a
#: migration anyone owes.
COMPLETION_SCHEMA_VERSION: Final = 1

#: What a marker carrying `parts` declares. A day written under this version needs no download to be
#: proven later -- the writer hashed each part as it uploaded it -- which is what stops the
#: manifest-trusted region of `scripts/compile_availability_bootstrap.py` from growing.
COMPLETION_PARTS_SCHEMA_VERSION: Final = 2

#: Both versions are read; exactly one is written, chosen by whether `parts` is populated.
COMPLETION_SCHEMA_VERSIONS: Final = (COMPLETION_SCHEMA_VERSION, COMPLETION_PARTS_SCHEMA_VERSION)

_REQUIRED_FIELDS: Final = ("part_count", "row_count", "completed_at", "run_id")

#: The one optional field, and it is SERIALIZED ONLY WHEN TRUE. Every marker written before this
#: field existed must still round-trip byte-for-byte -- `availability_index._verify_completion_object`
#: re-serializes a stored marker and refuses any difference -- so a `derived_empty: false` key on
#: every ordinary marker would have failed verification for every day already in the bucket.
DERIVED_EMPTY_FIELD: Final = "derived_empty"

#: The second optional field, SERIALIZED ONLY WHEN NON-EMPTY, for the same round-trip reason. It
#: carries the digest of each part the export uploaded, so a later reader can bind the day's parts by
#: key and sha256 without downloading a byte of them.
PARTS_FIELD: Final = "parts"

_PART_FIELDS: Final = ("relative_path", "row_count", "byte_count", "sha256")
_SHA256_LENGTH: Final = 64
_SHA256_ALPHABET: Final = frozenset("0123456789abcdef")


class PartitionCompletionError(ValueError):
    """Raised when a completion payload is incomplete or a marker object cannot be decoded."""


@dataclass(frozen=True, slots=True)
class CompletedPart:
    """One part file the export uploaded, as the export itself measured it."""

    relative_path: str
    row_count: int
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.relative_path.strip() or self.relative_path != self.relative_path.strip():
            raise PartitionCompletionError("a completion part requires a trimmed non-empty relative path")
        if self.row_count <= 0:
            raise PartitionCompletionError(f"a completion part holds at least one row, got {self.row_count}")
        if self.byte_count <= 0:
            raise PartitionCompletionError(f"a completion part holds at least one byte, got {self.byte_count}")
        if len(self.sha256) != _SHA256_LENGTH or not set(self.sha256) <= _SHA256_ALPHABET:
            raise PartitionCompletionError("a completion part digest must be a lowercase SHA-256 hex digest")

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {
            "byte_count": self.byte_count,
            "relative_path": self.relative_path,
            "row_count": self.row_count,
            "sha256": self.sha256,
        }

    @classmethod
    def from_wire(cls, value: object) -> CompletedPart:
        """Decode one recorded part, refusing any shape `to_wire` would not have written."""
        if not isinstance(value, dict) or set(value) != set(_PART_FIELDS):
            raise PartitionCompletionError(f"a completion part must be a JSON object with exactly {_PART_FIELDS}")
        relative_path = value["relative_path"]
        sha256 = value["sha256"]
        row_count = value["row_count"]
        byte_count = value["byte_count"]
        if not isinstance(relative_path, str) or not isinstance(sha256, str):
            raise PartitionCompletionError("a completion part path and digest must be strings")
        if isinstance(row_count, bool) or isinstance(byte_count, bool):
            raise PartitionCompletionError("a completion part count must be an integer, not a boolean")
        if not isinstance(row_count, int) or not isinstance(byte_count, int):
            raise PartitionCompletionError("a completion part row and byte counts must be integers")
        return cls(relative_path=relative_path, row_count=row_count, byte_count=byte_count, sha256=sha256)


@dataclass(frozen=True, slots=True)
class PartitionCompletion:
    """What one finished export of one stream-day-tier actually uploaded, written after its last part."""

    part_count: int
    row_count: int
    completed_at: datetime
    run_id: str
    #: True ONLY for a derived rung whose generalisation dropped every base row. The day is published
    #: and holds rows at its base rung; THIS rung of it is honestly empty. `derivation._retract_tier`
    #: is the only writer, and `objectstore.write_completion_marker` refuses one at the base rung.
    derived_empty: bool = False
    #: Each part this export uploaded, with the digest it computed from the bytes it sent. Empty on
    #: every marker written before this field existed and on every derived-empty one; when present it
    #: must describe EVERY part the marker counts, in sorted unique path order, or the marker is
    #: refused. A partial list would read as a whole one to the bootstrap compiler.
    parts: tuple[CompletedPart, ...] = ()

    def __post_init__(self) -> None:
        # A zero-part completion asserts that a rung finished while holding no rows, which is a
        # governed absence's claim in every other vocabulary -- so it is admitted ONLY when the
        # receipt says out loud which claim it is making. `derived_empty` says "the source had rows
        # and this rung kept none of them"; `absent.json` says "the source had nothing".
        _validate_parts(self.parts, part_count=self.part_count, row_count=self.row_count)
        if self.derived_empty:
            if self.part_count != 0 or self.row_count != 0:
                raise PartitionCompletionError(
                    f"a derived-empty completion marker holds nothing by definition, got "
                    f"{self.part_count} part(s) and {self.row_count} row(s)"
                )
        elif self.part_count <= 0:
            raise PartitionCompletionError(f"a completed day must hold at least one part file, got {self.part_count}")
        elif self.row_count <= 0:
            raise PartitionCompletionError(f"a completed day must hold at least one row, got {self.row_count}")
        if not self.run_id.strip():
            raise PartitionCompletionError("a completion marker requires a non-blank run_id")
        if self.completed_at.tzinfo is None:
            raise PartitionCompletionError("completed_at must be timezone-aware")

    @property
    def schema_version(self) -> int:
        """The version this marker declares, DERIVED from whether it carries part digests."""
        return COMPLETION_PARTS_SCHEMA_VERSION if self.parts else COMPLETION_SCHEMA_VERSION

    def to_json_bytes(self) -> bytes:
        """Serialize the receipt as canonical UTF-8 JSON with sorted keys and a UTC timestamp."""
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "part_count": self.part_count,
            "row_count": self.row_count,
            "completed_at": self.completed_at.astimezone(UTC).isoformat(),
            "run_id": self.run_id,
        }
        if self.derived_empty:
            payload[DERIVED_EMPTY_FIELD] = True
        if self.parts:
            payload[PARTS_FIELD] = [part.to_wire() for part in self.parts]
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> PartitionCompletion:
        """Decode a marker object, refusing anything that is not this convention's shape."""
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PartitionCompletionError(f"completion marker is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise PartitionCompletionError("completion marker must decode to a JSON object")
        version = decoded.get("schema_version")
        if version not in COMPLETION_SCHEMA_VERSIONS:
            raise PartitionCompletionError(
                f"completion marker schema_version {version!r} is not one of {COMPLETION_SCHEMA_VERSIONS}"
            )
        parts = _decode_parts(decoded, version=version)
        missing = [name for name in _REQUIRED_FIELDS if name not in decoded]
        if missing:
            raise PartitionCompletionError(f"completion marker is missing {', '.join(missing)}")
        try:
            completed_at = datetime.fromisoformat(str(decoded["completed_at"]))
        except ValueError as exc:
            raise PartitionCompletionError(f"completion marker completed_at is not ISO-8601: {exc}") from exc
        try:
            part_count = int(decoded["part_count"])
            row_count = int(decoded["row_count"])
        except (TypeError, ValueError) as exc:
            raise PartitionCompletionError(f"completion marker counts are not integers: {exc}") from exc
        return cls(
            part_count=part_count,
            row_count=row_count,
            completed_at=completed_at,
            run_id=str(decoded["run_id"]),
            derived_empty=_decode_derived_empty(decoded),
            parts=parts,
        )


def _validate_parts(parts: tuple[CompletedPart, ...], *, part_count: int, row_count: int) -> None:
    """Refuse a recorded part list that does not describe EXACTLY what the marker's counts claim.

    A marker whose `parts` covered only some of its parts would let the bootstrap compiler bind a
    day by digest while a part of it went unproven -- the one shape this field exists to make
    impossible. Absent is a fine answer; partial is not.
    """
    if not isinstance(parts, tuple):
        raise PartitionCompletionError("completion marker parts must be an immutable tuple")
    if not parts:
        return
    if len(parts) != part_count:
        raise PartitionCompletionError(
            f"a completion marker recording parts must record all {part_count} of them, got {len(parts)}"
        )
    paths = tuple(part.relative_path for part in parts)
    if paths != tuple(sorted(set(paths))):
        raise PartitionCompletionError("completion marker parts must use sorted unique relative paths")
    recorded_rows = sum(part.row_count for part in parts)
    if recorded_rows != row_count:
        raise PartitionCompletionError(
            f"completion marker parts hold {recorded_rows} row(s) while the marker claims {row_count}"
        )


def _decode_parts(decoded: dict[str, object], *, version: object) -> tuple[CompletedPart, ...]:
    """Read the optional recorded parts, binding the field's presence to the version that declares it.

    The version is DERIVED from this field on the way out, so it is checked against it on the way in:
    a v1 marker carrying parts, or a v2 marker carrying none, could not be re-serialized to the bytes
    it was read from, and the availability contract verifies exactly that round trip.
    """
    raw = decoded.get(PARTS_FIELD)
    if raw is None and PARTS_FIELD not in decoded:
        if version == COMPLETION_PARTS_SCHEMA_VERSION:
            raise PartitionCompletionError(
                f"a schema_version {COMPLETION_PARTS_SCHEMA_VERSION} completion marker records its parts"
            )
        return ()
    if version != COMPLETION_PARTS_SCHEMA_VERSION:
        raise PartitionCompletionError(
            f"a completion marker recording {PARTS_FIELD} declares schema_version "
            f"{COMPLETION_PARTS_SCHEMA_VERSION}, not {version!r}"
        )
    if not isinstance(raw, list) or not raw:
        raise PartitionCompletionError(f"completion marker {PARTS_FIELD} must be a non-empty JSON array")
    return tuple(CompletedPart.from_wire(item) for item in raw)


def _decode_derived_empty(decoded: dict[str, object]) -> bool:
    """Read the optional derived-empty flag, refusing any spelling `to_json_bytes` would not write.

    An explicit `false` is REFUSED rather than read as absent: this field is omitted when false, so a
    marker carrying it could not be re-serialized to the bytes it was read from -- and the
    availability contract verifies exactly that round trip before it will bind a marker to a row.
    """
    if DERIVED_EMPTY_FIELD not in decoded:
        return False
    flag = decoded[DERIVED_EMPTY_FIELD]
    if flag is not True:
        raise PartitionCompletionError(
            f"completion marker {DERIVED_EMPTY_FIELD} is written only when true, got {flag!r}"
        )
    return True
