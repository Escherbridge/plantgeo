"""The one cache-first scaffold every Open-Meteo multi-location replay lane runs on.

A lane owns its plan contract, its per-variable specifications and its own normalization. It owns
NONE of the governed plumbing below -- the checksum canonicalizer, the retry/backoff loop, the raw
cache write/verify pair, the grid-point attribution guard, the checkpoint state rule. Those live
here once so a correction lands in every lane at the same instant. See execution/AGENTS.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Protocol

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.ingest.http import UpstreamError, upstream_client
from agri_data_service.ingest.open_meteo import OpenMeteoRateLimitError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
    from datetime import date
    from pathlib import Path

    import httpx

    from agri_data_service.execution.backfill_types import AnalysisGridCell
    from agri_data_service.ingest.open_meteo_endpoint import OpenMeteoEndpoint, OpenMeteoProductRequest

# Only a minutely refusal is waited out; every other scope, including an unrecognised body, fails
# closed rather than sitting on an hour-long or day-long quota wall.
RATE_LIMIT_BACKOFF_SECONDS: Final = {"minute": 70.0}
TRANSPORT_BACKOFF_SECONDS: Final = 15.0
MAX_FETCH_ATTEMPTS: Final = 4
DEFAULT_CHUNK_CONCURRENCY: Final = 2
MAX_CHUNK_CONCURRENCY: Final = 4

ISO_DATE_LENGTH: Final = 10

# A float-comparison epsilon added to half the native grid spacing; a returned point further than
# that from the requested centroid is a different grid box and must fail rather than be attributed.
GRID_OFFSET_EPSILON_DEGREES: Final = 1e-6

# Every lane pins its request to GMT, so a returned instant needs no session-timezone reasoning.
OPEN_METEO_TIME_ZONE: Final = "GMT"

# The only nondeterministic field Open-Meteo puts in a response body. Stripping it is what makes a
# payload checksum reproducible, so it is named ONCE: a second such field is a one-line change here
# rather than four independent edits that can each be missed.
NONDETERMINISTIC_RESPONSE_FIELDS: Final = frozenset({"generationtime_ms"})


@dataclass(frozen=True, slots=True)
class OpenMeteoLane:
    """One replay lane's identity: how it names itself in errors, where it caches, and what it reads."""

    # Prefixes every message this scaffold raises, so a failure names its lane without a traceback.
    label: str
    # Directory beneath the local execution root that holds this lane's checkpoints and raw cache.
    cache_directory_name: str
    endpoint: OpenMeteoEndpoint[Any]

    @property
    def max_response_bytes(self) -> int:
        """Return the byte ceiling one response of this lane may occupy."""
        return self.endpoint.bounds.max_bytes


@dataclass(frozen=True, slots=True)
class OpenMeteoLaneCapture:
    """Everything one retrieval is known by before its content is normalized."""

    retrieved_at: datetime
    # The canonicalized document -- what is checksummed, cached and parsed. Never the raw bytes.
    canonical_payload: bytes
    wire_payload_bytes: int
    wire_payload_checksum: str
    # The host this retrieval really answered from; the paid tier is a different host plus one key.
    request_base_url: str


class OpenMeteoLaneFetchError(RuntimeError):
    """Raised when a chunk stopped for good; carries the chunk so a resume is unambiguous."""

    def __init__(self, lane_label: str, chunk_key: str, cause: UpstreamError | None, attempts: int) -> None:
        """Name the lane, the chunk, how many attempts it really made, and what stopped it."""
        detail = "no upstream response" if cause is None else str(cause)
        plural = "attempt" if attempts == 1 else "attempts"
        super().__init__(f"{lane_label} chunk {chunk_key} failed after {attempts} {plural}: {detail}")
        self.chunk_key = chunk_key
        self.cause = cause
        self.attempts = attempts


class ChunkReceipt(Protocol):
    """The one field the shared checkpoint merge needs from a lane's own receipt model."""

    chunk_key: str


def require_aware_utc(value: datetime, field_name: str) -> datetime:
    """Refuse a naive timestamp and normalize an aware one to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def date_range(start: date, end: date) -> Iterator[date]:
    """Yield an inclusive date range with no timezone-dependent arithmetic."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def atomic_write(path: Path, payload: bytes) -> None:
    """Write one local source object atomically so retries never accept a partial cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def canonical_location_document(lane: OpenMeteoLane, wire_payload: bytes) -> bytes:
    """Strip only the provider's nondeterministic fields so identical content yields one checksum."""
    try:
        parsed = json.loads(wire_payload)
    except ValueError as exc:
        raise ValueError(f"{lane.label} response contained invalid JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{lane.label} multi-location response must be a JSON array")
    stripped: list[object] = []
    for location in parsed:
        if not isinstance(location, dict):
            raise ValueError(f"{lane.label} response must contain only location objects")
        stripped.append({key: value for key, value in location.items() if key not in NONDETERMINISTIC_RESPONSE_FIELDS})
    return canonical_json_bytes(stripped)


def ordered_locations(lane: OpenMeteoLane, payload: bytes, expected_count: int) -> list[dict[str, object]]:
    """Read one entry per requested cell, in the order the request named them."""
    parsed = json.loads(payload)
    if not isinstance(parsed, list) or len(parsed) != expected_count:
        raise ValueError(f"{lane.label} response does not carry one entry per requested cell")
    locations: list[dict[str, object]] = []
    for index, location in enumerate(parsed):
        if not isinstance(location, dict):
            raise ValueError(f"{lane.label} response must contain only location objects")
        location_id = location.get("location_id")
        # The provider omits `location_id` on the first entry and numbers the rest from 1.
        if index and (isinstance(location_id, bool) or not isinstance(location_id, int) or location_id != index):
            raise ValueError(f"{lane.label} response locations are not in the requested order")
        locations.append(location)
    return locations


def required_float(lane: OpenMeteoLane, location: Mapping[str, object], field_name: str) -> float:
    """Read one finite numeric field, refusing a bool, a string or a NaN standing in for a number."""
    value = location.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{lane.label} location is missing a numeric {field_name}")
    return float(value)


def nearest_native_grid_point(cell: AnalysisGridCell, step_degrees: float) -> tuple[int, int]:
    """Return the integer index of the native grid node a cell centroid resolves to."""
    return round(cell.latitude / step_degrees), round(cell.longitude / step_degrees)


def max_grid_offset_degrees(native_grid_degrees: float) -> float:
    """Return how far a returned point may sit from a requested centroid and still be the same box."""
    return native_grid_degrees / 2 + GRID_OFFSET_EPSILON_DEGREES


def validated_grid_point(
    lane: OpenMeteoLane,
    cell: AnalysisGridCell,
    location: Mapping[str, object],
    max_offset_degrees: float,
) -> tuple[float, float]:
    """Bind one returned native grid point to its reviewed analysis cell or refuse the attribution."""
    latitude = required_float(lane, location, "latitude")
    longitude = required_float(lane, location, "longitude")
    if abs(latitude - cell.latitude) > max_offset_degrees or abs(longitude - cell.longitude) > max_offset_degrees:
        raise ValueError(f"{lane.label} returned a grid point outside the reviewed cell {cell.cell_key}")
    if location.get("timezone") != OPEN_METEO_TIME_ZONE or location.get("utc_offset_seconds") != 0:
        raise ValueError(f"{lane.label} response is not on the reviewed GMT time base")
    return latitude, longitude


def bounded_numeric_series(  # noqa: PLR0913
    lane: OpenMeteoLane,
    block: Mapping[str, object],
    field_name: str,
    *,
    minimum: float,
    maximum: float,
    expected_count: int,
    subject: str,
) -> list[float | None]:
    """Read one field's numeric series; a value outside its reviewed range fails the whole chunk.

    The range check is the only thing standing between a provider sentinel and a wall of accepted
    values, so it is applied on the way in, before any reduction or summary sees a number.
    """
    raw = block.get(field_name)
    if not isinstance(raw, list) or len(raw) != expected_count:
        raise ValueError(f"{lane.label} {subject} {field_name} does not align with its time axis")
    values: list[float | None] = []
    for value in raw:
        if value is None:
            values.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError(f"{lane.label} {subject} {field_name} carries a non-numeric value")
        numeric = float(value)
        if not minimum <= numeric <= maximum:
            raise ValueError(
                f"{lane.label} {subject} {field_name} carries a value outside its reviewed physical "
                f"range [{minimum}, {maximum}]"
            )
        values.append(numeric)
    return values


async def fetch_lane_capture(  # noqa: PLR0913
    lane: OpenMeteoLane,
    chunk_key: str,
    request: OpenMeteoProductRequest,
    *,
    client: httpx.AsyncClient,
    fetch_text: Callable[[httpx.AsyncClient, str], Awaitable[str]],
    error_factory: Callable[[str, UpstreamError | None, int], Exception],
    retrieved_at: datetime | None = None,
    sleep: object = None,
) -> OpenMeteoLaneCapture:
    """Fetch one chunk with bounded retries; an exhausted quota is raised, never swallowed.

    Only a minutely refusal is slept through. An hourly wall, a daily wall and an unrecognised 429
    body all break out immediately: an unrecognised body is far more likely a reworded wall than a
    blip, and waiting one out turns a quota refusal into an unexplained hang.
    """
    waiter = sleep if callable(sleep) else asyncio.sleep
    last_error: UpstreamError | None = None
    attempt = 0
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            body = await fetch_text(client, request.request_url)
        except OpenMeteoRateLimitError as error:
            last_error = error
            backoff = RATE_LIMIT_BACKOFF_SECONDS.get(error.scope)
            if backoff is None or attempt == MAX_FETCH_ATTEMPTS:
                break
            await waiter(backoff)
            continue
        except UpstreamError as error:
            last_error = error
            if attempt == MAX_FETCH_ATTEMPTS:
                break
            await waiter(TRANSPORT_BACKOFF_SECONDS * attempt)
            continue
        wire_payload = body.encode("utf-8")
        return OpenMeteoLaneCapture(
            retrieved_at=require_aware_utc(retrieved_at or datetime.now(UTC), "retrieved_at"),
            canonical_payload=canonical_location_document(lane, wire_payload),
            wire_payload_bytes=len(wire_payload),
            wire_payload_checksum=hashlib.sha256(wire_payload).hexdigest(),
            request_base_url=lane.endpoint.require_base_url(request.base_url),
        )
    raise error_factory(chunk_key, last_error, attempt)


async def run_lane_chunks[ChunkT, ResultT](
    lane: OpenMeteoLane,
    chunks: Sequence[ChunkT],
    fetch_one: Callable[[httpx.AsyncClient, ChunkT], Awaitable[ResultT]],
    *,
    concurrency: int = DEFAULT_CHUNK_CONCURRENCY,
    client: httpx.AsyncClient | None = None,
) -> list[ResultT | BaseException]:
    """Fetch several chunks under a bounded semaphore, preserving each chunk's own failure."""
    if not 1 <= concurrency <= MAX_CHUNK_CONCURRENCY:
        raise ValueError(f"{lane.label} chunk concurrency must be between 1 and {MAX_CHUNK_CONCURRENCY}")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(active: httpx.AsyncClient, chunk: ChunkT) -> ResultT:
        async with semaphore:
            return await fetch_one(active, chunk)

    if client is None:
        async with upstream_client(lane.endpoint.bounds) as owned:
            return await asyncio.gather(*(one(owned, chunk) for chunk in chunks), return_exceptions=True)
    return await asyncio.gather(*(one(client, chunk) for chunk in chunks), return_exceptions=True)


def lane_checkpoint_path(root: Path, lane: OpenMeteoLane, plan_checksum: str) -> Path:
    """Return a plan-bound durable checkpoint file path."""
    return root / lane.cache_directory_name / f"{plan_checksum}.json"


def lane_raw_cache_paths(
    root: Path,
    lane: OpenMeteoLane,
    plan_checksum: str,
    chunk_key: str,
) -> tuple[Path, Path]:
    """Return canonical-document and receipt locations for one chunk beneath the local run root."""
    directory = root / lane.cache_directory_name / plan_checksum / "raw"
    return directory / f"{chunk_key}.json", directory / f"{chunk_key}.receipt.json"


def write_raw_cache_pair(payload_path: Path, receipt_path: Path, payload: bytes, receipt_bytes: bytes) -> None:
    """Write the document before its receipt, so a receipt never points at bytes that are not there."""
    atomic_write(payload_path, payload)
    atomic_write(receipt_path, receipt_bytes)


def verified_cached_payload(
    lane: OpenMeteoLane,
    payload_path: Path,
    *,
    expected_bytes: int,
    expected_checksum: str,
) -> bytes:
    """Re-read one cached document and prove it is still the bytes its receipt was written against."""
    payload = payload_path.read_bytes()
    if len(payload) != expected_bytes or hashlib.sha256(payload).hexdigest() != expected_checksum:
        raise ValueError(f"{lane.label} raw cache payload does not match its receipt")
    return payload


def require_complete_raw_cache_pair(lane: OpenMeteoLane, payload_path: Path, receipt_path: Path) -> bool:
    """Report whether a chunk is cached, refusing the half-written state where only one file exists."""
    if not payload_path.exists() and not receipt_path.exists():
        return False
    if not payload_path.exists() or not receipt_path.exists():
        raise ValueError(f"{lane.label} raw cache has incomplete content or receipt")
    return True


def derived_checkpoint_state(receipted_chunk_keys: set[str], plan_chunk_keys: Sequence[str]) -> str:
    """Recompute `state` from receipt completeness so a recorded `blocked` cannot outlive its cause."""
    if not receipted_chunk_keys:
        return "initialized"
    if all(key in receipted_chunk_keys for key in plan_chunk_keys):
        return "validated"
    return "running"


def merged_chunk_receipts[ReceiptT: ChunkReceipt](
    lane: OpenMeteoLane,
    existing: Sequence[ReceiptT],
    receipt: ReceiptT,
) -> list[ReceiptT]:
    """Fold one receipt into the sorted set, refusing to rebind a chunk to different source content."""
    prior: dict[str, ReceiptT] = {item.chunk_key: item for item in existing}
    conflicting = prior.get(receipt.chunk_key)
    if conflicting is not None and conflicting != receipt:
        raise ValueError(f"{lane.label} checkpoint already binds this chunk to different source content")
    prior[receipt.chunk_key] = receipt
    return [prior[key] for key in sorted(prior)]


def lane_release_manifest(  # noqa: PLR0913
    lane: OpenMeteoLane,
    *,
    plan_checksum: str,
    transform_version: str,
    checkpoint_plan_checksum: str,
    checkpoint_state: str,
    expected_chunk_keys: Sequence[str],
    receipts: Sequence[Any],
) -> str:
    """Hash the complete ordered chunk receipt set a release must pin."""
    if checkpoint_plan_checksum != plan_checksum or checkpoint_state != "validated":
        raise ValueError(f"a complete validated checkpoint is required for a {lane.label} release manifest")
    if [receipt.chunk_key for receipt in receipts] != list(expected_chunk_keys):
        raise ValueError(f"{lane.label} receipt coverage does not match the reviewed chunk plan")
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "plan_checksum": plan_checksum,
                "transform_version": transform_version,
                "receipts": [receipt.model_dump(mode="json") for receipt in receipts],
            }
        )
    ).hexdigest()
