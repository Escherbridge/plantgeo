"""Scalar, JSON and lane-path primitives shared by every availability module.

Rationale and the contracts these enforce: see `AGENTS.md` in this directory, "Availability index".
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, Literal, cast

from agri_data_service.foundation.canonical import canonical_json
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from collections.abc import Mapping

AvailabilityNature = Literal["daily_series", "release_series"]
TerminalState = Literal["published", "governed_absence"]

#: HOW WELL ONE ROW'S PARTS ARE PROVEN, and the only two answers there are.
#:
#: `digested` is the ordinary class: the row names every part it publishes and each name carries a
#: SHA-256 that was computed from that object's bytes, so `--apply` re-downloads and re-hashes them.
#:
#: `manifest_trusted` is the bootstrap-only class introduced by owner decision D3
#: (`environmental_postgres_retirement_20260904`): the row names NO parts and its proof is the
#: completion marker, which is itself fetched and digested. It exists because the contract as written
#: requires hashing every part of every lane-day -- for `fire-detections`, every day since
#: 2000-11-01 at every rung -- and that cost would push the time slider's startup fix behind the
#: whole cutover. A trusted row states a WEAKER claim; it never states a false one, because a digest
#: that was not computed from the object it describes is never emitted.
AvailabilityProvenance = Literal["digested", "manifest_trusted"]

DIGESTED_PROVENANCE: Final[AvailabilityProvenance] = "digested"
MANIFEST_TRUSTED_PROVENANCE: Final[AvailabilityProvenance] = "manifest_trusted"

#: The optional provenance key, SERIALIZED ONLY WHEN MANIFEST-TRUSTED -- the same rule
#: `foundation/parquet/completion.py::DERIVED_EMPTY_FIELD` follows, and for the same reason: a
#: `provenance: "digested"` key on every ordinary terminal document would change the content address
#: of evidence already written and re-verified byte-for-byte.
PROVENANCE_FIELD: Final = "provenance"

JSON_CONTENT_TYPE: Final = "application/json"
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
BOOTSTRAP_INPUT_SCHEMA_VERSION: Final = "availability-bootstrap-input-v1"
PUBLICATION_INPUT_SCHEMA_VERSION: Final = "availability-publication-input-v1"
MAX_INPUT_BYTES: Final = 64 * 1024 * 1024
MAX_AVAILABILITY_ROWS: Final = 250_000
MAX_PUBLICATION_ATTEMPTS: Final = 4
MAX_IMMUTABLE_CREATE_ATTEMPTS: Final = 3
_SHA256_LENGTH: Final = 64
_MAX_RUNG: Final = 30
_LANE_ROOT_SEGMENT_COUNT: Final = 2
POINTER_MAX_BYTES: Final = 64 * 1024
TYPED_RECEIPT_MAX_BYTES: Final = 1024 * 1024
BOOTSTRAP_RECEIPT_MAX_BYTES: Final = 64 * 1024 * 1024
GENERATION_MAX_BYTES: Final = 256 * 1024 * 1024
EVIDENCE_OBJECT_MAX_BYTES: Final = 256 * 1024 * 1024
BOOTSTRAP_INVENTORY_SCHEMA_VERSION: Final = "availability-bootstrap-inventory-v1"
SOURCE_EVIDENCE_SCHEMA_VERSION: Final = "availability-source-evidence-v1"
TERMINAL_EVIDENCE_SCHEMA_VERSION: Final = "availability-terminal-evidence-v1"
SYSTEM_BOOTSTRAP_SCHEMA_VERSION: Final = "availability-system-bootstrap-v1"
BOOTSTRAP_MARKER_SCHEMA_VERSION: Final = "availability-bootstrap-marker-v1"

#: The bootstrap RECEIPT is content-addressed, so nothing can find it without already knowing its
#: digest. This marker sits at a deterministic key beside it and names it, which is what lets a
#: reader ask "was this lane ever bootstrapped?" with ONE GET instead of a prefix walk. See
#: `parquet_ops/availability_coverage.py`: a bootstrapped lane whose pointer is gone is withheld,
#: never quietly re-censused.
BOOTSTRAP_MARKER_MAX_BYTES: Final = 64 * 1024
_BOOTSTRAP_MARKER_FIELDS: Final = {
    "bootstrap_receipt_key",
    "bootstrap_receipt_sha256",
    "lane_root",
    "schema_version",
}
_IDENTITY_FIELDS: Final = {
    "lane_root",
    "lane",
    "product",
    "nature",
    "required_rungs",
    "verified_source_inventory_root",
}
_BOOTSTRAP_INVENTORY_FIELDS: Final = _IDENTITY_FIELDS | {
    "object_receipts",
    "schema_version",
    "source_ceiling",
}
_SOURCE_EVIDENCE_FIELDS: Final = _IDENTITY_FIELDS | {
    "day",
    "object_receipts",
    "schema_version",
    "source_ceiling",
}
_TERMINAL_EVIDENCE_FIELDS: Final = _IDENTITY_FIELDS | {
    "absence_reason",
    "absence_receipt",
    "completion_receipt",
    "data_receipts",
    "day",
    "row_count",
    "rung",
    "published_at",
    "schema_version",
    "source_ceiling",
    "source_receipt",
    "terminal_state",
}
_SYSTEM_BOOTSTRAP_FIELDS: Final = _IDENTITY_FIELDS | {
    "bootstrap_input_sha256",
    "created_at",
    "input_receipts",
    "outcome_sha256",
    "provenance",
    "row_count",
    "schema_version",
    "source_ceiling",
}
_ABSENT_CODES: Final = frozenset({"404", "NoSuchKey", "NotFound"})
_PRECONDITION_CODES: Final = frozenset({"412", "PreconditionFailed"})
_CREATE_RETRY_CODES: Final = frozenset({"409", "ConditionalRequestConflict"})
_CAS_CONFLICT_CODES: Final = _PRECONDITION_CODES | _CREATE_RETRY_CODES


class AvailabilityError(RuntimeError):
    """Base refusal for availability evidence or publication."""


class AvailabilityUnavailableError(AvailabilityError):
    """A required pointer or generation is missing or stale."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AvailabilityMalformedError(AvailabilityError):
    """Availability evidence is not the frozen schema."""


class AvailabilityChecksumError(AvailabilityError):
    """A checksum-bound object does not match its receipt."""


class AvailabilityConflictError(AvailabilityError):
    """An immutable key or conditional pointer update conflicts."""


class AlreadyBootstrappedError(AvailabilityConflictError):
    """A different immutable bootstrap already owns the lane."""


def _decode_canonical_json_object(payload: bytes, label: str) -> dict[str, object]:
    value = _decode_json_object(payload, label)
    if canonical_json(value).encode("utf-8") != payload:
        raise AvailabilityMalformedError(f"{label} is not canonical JSON")
    return value


def _decode_json_object(payload: bytes, label: str) -> dict[str, object]:
    value: object = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_pairs)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object with string keys")
    return cast("dict[str, object]", value)


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON object contains duplicate key {key!r}")
        value[key] = item
    return value


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return cast("dict[str, object]", value)


def _require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields must be exactly: {', '.join(sorted(expected))}")


def _parse_rungs(value: object) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise ValueError("required_rungs must be a non-empty ordered integer list")
    rungs = tuple(cast("list[int]", value))
    _require_rungs(rungs)
    return rungs


def _require_rungs(rungs: tuple[int, ...]) -> None:
    if rungs != AVAILABILITY_REQUIRED_RUNGS:
        raise ValueError(f"required_rungs must be the canonical ordered set {AVAILABILITY_REQUIRED_RUNGS}")
    for rung in rungs:
        _require_rung(rung)


def _require_rung(rung: int) -> None:
    if isinstance(rung, bool) or not 0 <= rung <= _MAX_RUNG:
        raise ValueError(f"rung must be an integer between 0 and {_MAX_RUNG}")


def _parse_nature(value: object) -> AvailabilityNature:
    if value == "daily_series":
        return "daily_series"
    if value == "release_series":
        return "release_series"
    raise ValueError("nature must be daily_series or release_series")


def _require_nature(value: str) -> None:
    _parse_nature(value)


def _parse_provenance(value: object) -> AvailabilityProvenance:
    """Read a declared provenance class; absent means the ordinary one, and `digested` is never written."""
    if value is None:
        return DIGESTED_PROVENANCE
    if value == MANIFEST_TRUSTED_PROVENANCE:
        return MANIFEST_TRUSTED_PROVENANCE
    raise ValueError(f"{PROVENANCE_FIELD} is written only as {MANIFEST_TRUSTED_PROVENANCE}, got {value!r}")


def _parse_terminal_state(value: object) -> TerminalState:
    if value == "published":
        return "published"
    if value == "governed_absence":
        return "governed_absence"
    raise ValueError("terminal_state must be published or governed_absence")


def _parse_date(value: object, label: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = _require_string(value, label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical YYYY-MM-DD date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{label} must be a canonical YYYY-MM-DD date")
    return parsed


def _parse_datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        _require_utc(value, label)
        return value.astimezone(UTC)
    text = _require_string(value, label)
    if not text.endswith("Z"):
        raise ValueError(f"{label} must be canonical UTC with a Z suffix")
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical UTC timestamp") from exc
    _require_utc(parsed, label)
    if _format_datetime(parsed) != text:
        raise ValueError(f"{label} must use canonical UTC spelling")
    return parsed


def _format_datetime(value: datetime) -> str:
    _require_utc(value, "datetime")
    rendered = value.isoformat(timespec="microseconds")
    return f"{rendered[:-6]}Z"


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None or value.astimezone(UTC).utcoffset() != value.utcoffset():
        raise ValueError(f"{label} must be timezone-aware UTC")


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, label)


def _parse_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _parse_nonnegative_int(value: object, label: str) -> int:
    parsed = _parse_int(value, label)
    if parsed < 0:
        raise ValueError(f"{label} must be non-negative")
    return parsed


def _parse_positive_int(value: object, label: str) -> int:
    if isinstance(value, str):
        if not value.isdecimal():
            raise ValueError(f"{label} must be a positive integer")
        parsed = int(value)
    else:
        parsed = _parse_int(value, label)
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _require_sha256(value: str, label: str) -> None:
    if len(value) != _SHA256_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_name(value: str, label: str) -> None:
    if not value or value != value.strip() or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"{label} must be a safe non-empty lane identifier")


def _require_lane_root(value: str) -> None:
    if (
        not value
        or value != value.strip("/")
        or value.startswith("/")
        or "\\" in value
        or ".." in value
        or not value.startswith("layer=")
    ):
        raise ValueError("lane_root must be a relative layer=... object prefix")
    _physical_lane_identity(value)


def _require_object_key(value: str, label: str) -> None:
    if not value or value != value.strip("/") or value.startswith("/") or "\\" in value or ".." in value:
        raise ValueError(f"{label} must be a relative safe object key")


def _normalize_prefix(prefix: str) -> str:
    normalized = prefix.strip("/")
    if not normalized:
        return ""
    _require_object_key(normalized, "object store prefix")
    return f"{normalized}/"


def _generation_sha_from_key(lane_root: str, key: str) -> str:
    prefix = f"{lane_root}/availability/generation="
    suffix = "/availability.parquet"
    if not key.startswith(prefix) or not key.endswith(suffix):
        raise ValueError("generation key is outside the lane's immutable availability layout")
    sha256 = key[len(prefix) : -len(suffix)]
    _require_sha256(sha256, "generation key digest")
    return sha256


def _require_prior_binding(key: str | None, sha256: str | None, lane_root: str) -> None:
    if key is None and sha256 is None:
        return
    if key is None or sha256 is None:
        raise ValueError("prior generation key and SHA-256 must both be null or both be present")
    _require_sha256(sha256, "prior_generation_sha256")
    if _generation_sha_from_key(lane_root, key) != sha256:
        raise ValueError("prior generation key digest does not match prior_generation_sha256")


def _physical_lane_identity(lane_root: str) -> tuple[str, str]:
    segments = lane_root.split("/")
    if (
        len(segments) != _LANE_ROOT_SEGMENT_COUNT
        or not segments[0].startswith("layer=")
        or not segments[1].startswith("kind=")
    ):
        raise ValueError("lane_root must be exactly layer=<slug>/kind=<observed|forecast>")
    layer = segments[0].removeprefix("layer=")
    kind = segments[1].removeprefix("kind=")
    if not layer or kind not in {"observed", "forecast"}:
        raise ValueError("lane_root must be exactly layer=<slug>/kind=<observed|forecast>")
    return layer, kind
