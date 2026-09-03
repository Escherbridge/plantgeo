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
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

COMPLETION_SCHEMA_VERSION: Final = 1

_REQUIRED_FIELDS: Final = ("part_count", "row_count", "completed_at", "run_id")

#: The one optional field, and it is SERIALIZED ONLY WHEN TRUE. Every marker written before this
#: field existed must still round-trip byte-for-byte -- `availability_index._verify_completion_object`
#: re-serializes a stored marker and refuses any difference -- so a `derived_empty: false` key on
#: every ordinary marker would have failed verification for every day already in the bucket.
DERIVED_EMPTY_FIELD: Final = "derived_empty"


class PartitionCompletionError(ValueError):
    """Raised when a completion payload is incomplete or a marker object cannot be decoded."""


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

    def __post_init__(self) -> None:
        # A zero-part completion asserts that a rung finished while holding no rows, which is a
        # governed absence's claim in every other vocabulary -- so it is admitted ONLY when the
        # receipt says out loud which claim it is making. `derived_empty` says "the source had rows
        # and this rung kept none of them"; `absent.json` says "the source had nothing".
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

    def to_json_bytes(self) -> bytes:
        """Serialize the receipt as canonical UTF-8 JSON with sorted keys and a UTC timestamp."""
        payload: dict[str, object] = {
            "schema_version": COMPLETION_SCHEMA_VERSION,
            "part_count": self.part_count,
            "row_count": self.row_count,
            "completed_at": self.completed_at.astimezone(UTC).isoformat(),
            "run_id": self.run_id,
        }
        if self.derived_empty:
            payload[DERIVED_EMPTY_FIELD] = True
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
        if version != COMPLETION_SCHEMA_VERSION:
            raise PartitionCompletionError(
                f"completion marker schema_version {version!r} is not {COMPLETION_SCHEMA_VERSION}"
            )
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
        )


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
