"""HEAD probes of the thirty pinned ISRIC VRTs, drift against the pins, retries, canonical JSON and digests.

See `pipeline/direct/soil_properties/AGENTS.md`, "Watermark and drift".
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Final

import httpx

from agri_data_service.pipeline.direct.soil_properties.products import SOURCE_FILE_PINS, SourceFilePin
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.warehouse.schemas.soil_properties import SOIL_PROPERTIES_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

HEAD_TIMEOUT_SECONDS: Final = 30.0
#: The PROJ trap (`scripts/raster/AGENTS.md`): a machine PROJ_LIB silently changes the Homolosine transform.
PROJ_ENVIRONMENT_VARIABLES: Final = ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA")
_DIGEST_CHUNK_BYTES: Final = 1 << 20
_FLOAT_INTEGER_LIMIT: Final = 2**53
_HTTP_OK: Final = 200


class SoilPropertiesPipelineError(PipelineOperationError):
    """A soil-properties verb refused; the message is the operator-facing contract."""


class SoilPropertiesDriftError(SoilPropertiesPipelineError):
    """A pinned VRT's Last-Modified or ETag moved, or it vanished: a new ISRIC release is suspected."""


def fail(message: str, *, stage: str, code: str = "operation_failed", retryable: bool = False) -> Exception:
    """Build the verb's typed refusal; callers `raise fail(...)`."""
    return SoilPropertiesPipelineError(
        message, code=code, lane=SOIL_PROPERTIES_STREAM, stage=stage, retryable=retryable
    )


def require_clean_proj_environment(stage: str) -> None:
    """Refuse to transform coordinates under a machine PROJ/GDAL data path."""
    present = [name for name in PROJ_ENVIRONMENT_VARIABLES if os.environ.get(name)]
    if present:
        raise fail(
            f"unset {', '.join(present)} before `{stage}`: a machine PROJ/GDAL data path silently changes the "
            "Homolosine transform (scripts/raster/AGENTS.md)",
            stage=stage,
            code="invalid_environment",
        )


# --- Digests and canonical JSON ---------------------------------------------------------


def sha256_hex(payload: bytes) -> str:
    """SHA-256 of bytes, lowercase hex."""
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    """SHA-256 of a file, streamed."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_DIGEST_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_number(value: float) -> str:
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError("canonical JSON has no representation for a non-finite number")
    if value.is_integer() and abs(value) < _FLOAT_INTEGER_LIMIT:
        return str(int(value))
    return repr(value)


def canonical_json(value: Any) -> str:
    """CONTRACT C5.5 canonical JSON: sorted keys, no spaces, integral floats as integers."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return "{" + ",".join(f"{json.dumps(str(key))}:{canonical_json(item)}" for key, item in items) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    raise TypeError(f"canonical JSON cannot render {type(value).__name__}")


def manifest_digest(manifest: dict[str, Any]) -> str:
    """C8: sha256 over the canonical JSON of the manifest without its own `manifest_sha256` field."""
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return sha256_hex(canonical_json(body).encode("utf-8"))


# --- Retries --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff: attempt n waits min(max, base * 2^(n-1)) seconds before the next try."""

    attempts: int
    base_seconds: float
    max_seconds: float

    def delay(self, attempt: int) -> float:
        """The wait after the given 1-based failed attempt."""
        # `2.0 ** ...` (a float base), not `2 ** ...`: typeshed types `int.__pow__` as returning
        # `Any` (the exponent's sign is not statically known), which would otherwise widen this
        # whole expression to `Any` and trip mypy's no-any-return on this float-returning method.
        return min(self.max_seconds, self.base_seconds * (2.0 ** (attempt - 1)))


def retrying[T](
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    retryable: tuple[type[BaseException], ...],
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `operation`, retrying the named failures with backoff; the last failure propagates."""
    for attempt in range(1, policy.attempts + 1):
        try:
            return operation()
        except retryable:
            if attempt == policy.attempts:
                raise
            sleep(policy.delay(attempt))
    raise AssertionError("unreachable: RetryPolicy.attempts must be at least 1")


# --- HEAD probes and drift ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbedFile:
    """What a live HEAD answered for one pinned VRT."""

    pin: SourceFilePin
    status: int
    last_modified: datetime | None
    etag: str | None
    content_length: int | None

    def drift(self) -> tuple[str, ...]:
        """Every way this answer differs from the pin; empty when it matches."""
        if self.status != _HTTP_OK:
            return (f"{self.pin.file_name}: HTTP {self.status} (the file set changed)",)
        reasons: list[str] = []
        if self.last_modified != self.pin.last_modified:
            observed = self.last_modified.isoformat() if self.last_modified else "absent"
            reasons.append(
                f"{self.pin.file_name}: Last-Modified {observed} != pinned {self.pin.last_modified.isoformat()}"
            )
        if self.etag != self.pin.etag:
            reasons.append(f"{self.pin.file_name}: ETag {self.etag!r} != pinned {self.pin.etag!r}")
        return tuple(reasons)

    def to_report(self) -> dict[str, Any]:
        """A JSON-safe record of the probe."""
        return {
            "file": self.pin.file_name,
            "status": self.status,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            "etag": self.etag,
            "content_length": self.content_length,
            "drift": list(self.drift()),
        }


def _parse_last_modified(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def probe_pin(client: httpx.Client, pin: SourceFilePin, policy: RetryPolicy) -> ProbedFile:
    """HEAD one VRT (retrying transport faults and 5xx) and record what it answered."""

    def head() -> httpx.Response:
        response = client.head(pin.vrt_url, follow_redirects=True, timeout=HEAD_TIMEOUT_SECONDS)
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            response.raise_for_status()
        return response

    response = retrying(head, policy, retryable=(httpx.TransportError, httpx.HTTPStatusError))
    length = response.headers.get("Content-Length")
    return ProbedFile(
        pin=pin,
        status=response.status_code,
        last_modified=_parse_last_modified(response.headers.get("Last-Modified")),
        etag=response.headers.get("ETag"),
        content_length=int(length) if length and length.isdigit() else None,
    )


def probe_pins(
    client: httpx.Client, policy: RetryPolicy, pins: Sequence[SourceFilePin] = SOURCE_FILE_PINS
) -> tuple[ProbedFile, ...]:
    """HEAD every pinned VRT, in pin order."""
    return tuple(probe_pin(client, pin, policy) for pin in pins)


def drift_report(probes: Sequence[ProbedFile]) -> list[str]:
    """Every drift reason across the probes; empty means the release is exactly the pinned one."""
    return [reason for probe in probes for reason in probe.drift()]


def refuse_on_drift(probes: Sequence[ProbedFile], *, stage: str) -> None:
    """Capture refuses a moved release: it is a new ISRIC release to republish, never an owed day."""
    reasons = drift_report(probes)
    if reasons:
        raise SoilPropertiesDriftError(
            "new ISRIC release suspected; republish under the CQ-8 carve-out rather than capturing it as "
            f"{SOIL_PROPERTIES_STREAM}: " + "; ".join(reasons),
            code="source_drift",
            lane=SOIL_PROPERTIES_STREAM,
            stage=stage,
        )


__all__ = [
    "PROJ_ENVIRONMENT_VARIABLES",
    "ProbedFile",
    "RetryPolicy",
    "SoilPropertiesDriftError",
    "SoilPropertiesPipelineError",
    "canonical_json",
    "drift_report",
    "fail",
    "file_sha256",
    "manifest_digest",
    "probe_pin",
    "probe_pins",
    "refuse_on_drift",
    "require_clean_proj_environment",
    "retrying",
    "sha256_hex",
]
