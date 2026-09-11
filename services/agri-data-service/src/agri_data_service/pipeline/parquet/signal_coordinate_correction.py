"""Guard exact preserved-signal physical admission; see AGENTS.md for ownership and bootstrap gates."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    completion_marker_path,
    day_prefix,
    derived_empty_completion_marker_path,
    partition_path,
)
from agri_data_service.pipeline.parquet.availability_index import (
    availability_bootstrap_marker_key,
    availability_pointer_key,
)
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.objectstore import (
    ListedObject,
    ObjectStore,
    availability_retry_path,
    availability_retry_quarantine_path,
)
from agri_data_service.pipeline.parquet.signal_candidate_admission import (
    ARCHIVE_PIN,
    BASE_RUNG,
    RUNGS,
    _documents,
    _referenced,
    read_signal_archive,
    verify_signal_candidates,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend

LANE_ROOT: Final = "layer=signal/kind=observed"
MAX_PHYSICAL_OBJECTS: Final = 40
MAX_BYTES: Final = 64 * 1024 * 1024
MAX_DAY_BYTES: Final = 16 * 1024 * 1024
MAX_QUIESCENCE_SECONDS: Final = 3600
JSON_CONTENT: Final = "application/json"


def encode(value: object) -> bytes:
    """Serialize exact request and journal bytes without inferred values."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def require(condition: object, detail: str) -> None:
    if not condition:
        raise ValueError(detail)


def string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a nonempty evidence string")
    return value


@dataclass(frozen=True, slots=True)
class SignalRepairDay:
    """One exact original population and its ordinary completed replacement."""

    day: date
    originals: dict[str, bytes]
    identities: tuple[dict[str, object], ...]
    candidate: bytes
    objects: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class SignalRepair:
    """The full date-pinned request and deterministic physical outputs."""

    request: bytes
    days: tuple[SignalRepairDay, ...]
    completed_at: datetime
    run_id: str


@dataclass
class SignalMemory:
    """A bounded local backend for the ordinary writer and deriver."""

    objects: dict[str, bytes]

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        del content_type
        require(len(payload) <= MAX_BYTES and len(self.objects) < MAX_PHYSICAL_OBJECTS, "candidate bound exceeded")
        self.objects[key] = payload

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def size_of(self, key: str) -> int | None:
        value = self.objects.get(key)
        return None if value is None else len(value)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        return iter(ListedObject(key, None) for key in sorted(self.objects) if key.startswith(prefix))


def write_signal_ladder(store: ObjectStore, table: pa.Table, *, day: date, completed_at: datetime, run_id: str) -> None:
    """Write through the ordinary partition/derivation paths and finish the base with real part digests."""
    receipt = store.write_partition(table, layer="signal", kind="observed", zoom=BASE_RUNG, day=day)
    derive_and_write_day_tiers(store, layer="signal", kind="observed", day=day, run_id=run_id, now=lambda: completed_at)
    store.write_completion_marker(
        PartitionCompletion(
            part_count=1,
            row_count=table.num_rows,
            completed_at=completed_at,
            run_id=run_id,
            parts=(CompletedPart(receipt.relative_path, receipt.row_count, receipt.byte_count, receipt.sha256),),
        ),
        layer="signal",
        kind="observed",
        zoom=BASE_RUNG,
        day=day,
    )


def build_signal_repair(archive: Path, *, bucket: str, completed_at: datetime) -> SignalRepair:
    """Reproduce a complete apply request from the fixed archive and a stable publication timestamp."""
    require(
        completed_at.tzinfo is not None and completed_at.utcoffset() == timedelta(0), "repair timestamp must use UTC"
    )
    require(bool(bucket.strip()), "repair bucket is missing")
    verified = verify_signal_candidates(archive)
    objects = read_signal_archive(archive)
    run_id = "signal-coordinate-" + completed_at.strftime("%Y%m%dT%H%M%S%fZ")
    days: list[SignalRepairDay] = []
    wire: list[dict[str, object]] = []
    for item in _documents(verified["days"]):
        day = date.fromisoformat(string(item["day"]))
        identities = _documents(item["originals"])
        originals = {string(original["key"]): _referenced(objects, original) for original in identities}
        base_receipt = next(candidate for candidate in _documents(item["candidates"]) if candidate["rung"] == BASE_RUNG)
        candidate_bytes = objects["objects/" + string(base_receipt["sha256"])]
        candidate = pq.ParquetFile(io.BytesIO(candidate_bytes)).read()
        memory = SignalMemory({})
        write_signal_ladder(ObjectStore(memory), candidate, day=day, completed_at=completed_at, run_id=run_id)
        prepared = SignalRepairDay(day, originals, identities, candidate_bytes, memory.objects)
        verify_signal_physical(prepared, memory.objects)
        days.append(prepared)
        wire.append(
            {
                "day": day.isoformat(),
                "originals": list(identities),
                "objects": {
                    key: {"sha256": sha256_digest(payload), "byte_count": len(payload)}
                    for key, payload in sorted(memory.objects.items())
                },
            }
        )
    request = encode(
        {
            "schema_version": "signal-coordinate-correction/v1",
            "lane_root": LANE_ROOT,
            "bucket": bucket,
            "prefix": "",
            "archive_sha256": ARCHIVE_PIN.sha256,
            "batch_manifest_sha256": ARCHIVE_PIN.manifest_sha256,
            "completed_at": completed_at.isoformat(),
            "run_id": run_id,
            "days": wire,
            "publication_scope": "physical_only_unbootstrapped",
            "availability_publication": False,
        }
    )
    return SignalRepair(request, tuple(days), completed_at, run_id)


def require_unbootstrapped(storage: AvailabilityStorage) -> None:
    """Refuse current-generation correction and lost-pointer recovery in this bounded operator."""
    for key in (availability_pointer_key(LANE_ROOT), availability_bootstrap_marker_key(LANE_ROOT)):
        require(
            storage.read(key, max_bytes=MAX_BYTES) is None, "signal availability exists; separate recovery required"
        )


def require_no_retry(storage: AvailabilityStorage, day: date) -> None:
    for key in (
        availability_retry_path("signal", "observed", day),
        availability_retry_quarantine_path("signal", "observed", day),
    ):
        require(storage.read(key, max_bytes=MAX_BYTES) is None, "target signal retry/quarantine exists")


def read_signal_scope(
    backend: ObjectStoreBackend, storage: AvailabilityStorage, prepared: SignalRepairDay
) -> dict[str, bytes]:
    """Read only the four exact day prefixes, refusing extra or changing objects."""
    result: dict[str, bytes] = {}
    limits = {
        key: max(len(prepared.originals.get(key, b"")), len(prepared.objects.get(key, b"")))
        for key in prepared.originals.keys() | prepared.objects.keys()
    }
    require(sum(limits.values()) <= MAX_DAY_BYTES, "prepared signal day exceeds byte budget")
    total = 0
    for rung in RUNGS:
        prefix = day_prefix("signal", "observed", rung, prepared.day)
        for item in backend.list_objects(prefix):
            require(item.key.startswith(prefix) and item.key not in result, "invalid physical listing")
            require(len(result) < MAX_PHYSICAL_OBJECTS, "physical object budget exceeded")
            require(item.key in limits, "unexpected signal object in physical scope")
            held = storage.read(item.key, max_bytes=limits[item.key])
            require(held is not None, "listed signal object disappeared")
            if held is not None:
                total += len(held.payload)
                require(
                    len(held.payload) <= limits[item.key] and total <= MAX_DAY_BYTES,
                    "signal physical byte budget exceeded",
                )
                result[item.key] = held.payload
    return result


def verify_signal_originals(
    prepared: SignalRepairDay, current: Mapping[str, bytes], storage: AvailabilityStorage
) -> None:
    require(dict(current) == prepared.originals, "original signal physical population changed")
    for identity in prepared.identities:
        key = string(identity["key"])
        held = storage.read(key, max_bytes=len(prepared.originals[key]))
        require(
            held is not None
            and held.payload == prepared.originals[key]
            and held.etag == identity["etag"]
            and held.version_id == identity["version_id"],
            "original signal object identity changed",
        )


def verify_signal_transition(prepared: SignalRepairDay, current: Mapping[str, bytes]) -> None:
    """Resume admits only pinned old bytes, pinned new bytes, or missing transition objects."""
    for key, payload in current.items():
        require(
            payload == prepared.originals.get(key) or payload == prepared.objects.get(key),
            f"unknown signal transition object: {key}",
        )


def read_signal_candidate(prepared: SignalRepairDay) -> pa.Table:
    """Decode only the current day's already verified candidate."""
    return pq.ParquetFile(io.BytesIO(prepared.candidate)).read()


def verify_signal_physical(prepared: SignalRepairDay, actual: Mapping[str, bytes]) -> None:
    require(dict(actual) == prepared.objects, "signal physical ladder differs from prepared bytes")
    for rung in RUNGS:
        marker = PartitionCompletion.from_json_bytes(
            actual[completion_marker_path("signal", "observed", rung, prepared.day)]
        )
        require(
            0 < marker.part_count < MAX_PHYSICAL_OBJECTS and len(marker.parts) == marker.part_count,
            "signal completion lacks exact part counts and digests",
        )
        parts = {
            partition_path("signal", "observed", rung, prepared.day, index): pq.ParquetFile(
                io.BytesIO(actual[partition_path("signal", "observed", rung, prepared.day, index)])
            ).read()
            for index in range(marker.part_count)
        }
        table = pa.concat_tables(list(parts.values()))
        require(
            marker.row_count == table.num_rows and {part.relative_path for part in marker.parts} == set(parts),
            "signal completion lacks exact part counts and digests",
        )
        for part in marker.parts:
            require(
                part.row_count == parts[part.relative_path].num_rows
                and part.byte_count == len(actual[part.relative_path])
                and part.sha256 == sha256_digest(actual[part.relative_path]),
                "signal completion part differs",
            )
        if rung == BASE_RUNG:
            require(table.equals(read_signal_candidate(prepared)), "signal base values changed in ordinary publication")


def verify_signal_quiescence(proof: Mapping[str, object], *, request_sha: str, now: datetime) -> None:
    require(proof.get("schema_version") == "signal-correction-quiescence/v1", "unknown quiescence proof")
    require(proof.get("request_sha256") == request_sha and proof.get("lane_root") == LANE_ROOT, "proof scope differs")
    require(
        proof.get("writers_stopped") is True and proof.get("no_inflight_retry_workers") is True,
        "proof must cover writers and retry workers",
    )
    string(proof.get("operator"))
    string(proof.get("evidence"))
    start = datetime.fromisoformat(string(proof.get("observed_at")))
    end = datetime.fromisoformat(string(proof.get("valid_until")))
    require(
        start.tzinfo is not None and end.tzinfo is not None and start <= now <= end,
        "quiescence proof is expired or future-dated",
    )
    require((end - start).total_seconds() <= MAX_QUIESCENCE_SECONDS, "quiescence exceeds one hour")


@dataclass
class GuardedSignalBackend:
    """Restrict ordinary mutable writes to one prepared day while its real owner remains held."""

    backend: ObjectStoreBackend
    storage: AvailabilityStorage
    prepared: SignalRepairDay
    guard: Callable[[], None]

    def _known(self, key: str) -> None:
        limit = max(1, len(self.prepared.originals.get(key, b"")), len(self.prepared.objects.get(key, b"")))
        held = self.storage.read(key, max_bytes=limit)
        require(
            held is None
            or held.payload == self.prepared.originals.get(key)
            or held.payload == self.prepared.objects.get(key),
            "object changed before physical mutation",
        )

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        self.guard()
        require(self.prepared.objects.get(key) == payload, "ordinary writer departed from prepared signal bytes")
        self._known(key)
        self.guard()
        self.backend.put(key, payload, content_type=content_type)

    def delete(self, key: str) -> None:
        self.guard()
        allowed = {completion_marker_path("signal", "observed", rung, self.prepared.day) for rung in RUNGS}
        allowed.update(
            derived_empty_completion_marker_path("signal", "observed", rung, self.prepared.day) for rung in RUNGS
        )
        require(key in allowed, "signal deletion escaped completion markers")
        self._known(key)
        self.guard()
        self.backend.delete(key)

    def get(self, key: str) -> bytes | None:
        require(key in self.prepared.originals or key in self.prepared.objects, "read escaped prepared signal objects")
        limit = max(len(self.prepared.originals.get(key, b"")), len(self.prepared.objects.get(key, b"")))
        held = self.storage.read(key, max_bytes=limit)
        require(
            held is None
            or held.payload == self.prepared.originals.get(key)
            or held.payload == self.prepared.objects.get(key),
            "signal object changed before ordinary readback",
        )
        return None if held is None else held.payload

    def size_of(self, key: str) -> int | None:
        return self.backend.size_of(key)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        return self.backend.list_objects(prefix)
