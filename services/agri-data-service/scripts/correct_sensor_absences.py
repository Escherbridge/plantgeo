"""Prepare or apply the exact reviewed September 5/6 sensor correction; see scripts/AGENTS.md."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import func, select

from agri_data_service.config import settings
from agri_data_service.db.engine import ingest_session
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import completion_marker_path, derived_empty_completion_marker_path
from agri_data_service.pipeline.direct.sensors.adapter import DirectSensorsForwardAdapter
from agri_data_service.pipeline.parquet.availability_extension import extend_availability_for_lane_day
from agri_data_service.pipeline.parquet.availability_index import (
    BotoAvailabilityStorage,
    StoredAvailabilityObject,
    _verify_source_evidence_receipt,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import (
    BotoObjectStoreBackend,
    ObjectStore,
    availability_retry_path,
    availability_retry_quarantine_path,
)
from agri_data_service.pipeline.parquet.publication_barrier import postgres_lane_publication_barrier
from agri_data_service.pipeline.parquet.sensor_absence_correction import (
    ARCHIVE_SHA,
    CANDIDATE_SHA,
    DAYS,
    JSON_CONTENT,
    LANE_ROOT,
    MAX_BYTES,
    MAX_OBJECTS,
    RUNGS,
    SnapshotStorage,
    build_ladder,
    check_known_objects,
    encode,
    finalized,
    ledger_wire,
    original_keys,
    read_scope,
    require,
    verify_export_source,
    verify_generation_binding,
    verify_index_row,
    verify_physical,
    verify_quiescence,
    verify_retry,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityIndex
    from agri_data_service.pipeline.parquet.lane_registry import LaneAdapter
    from agri_data_service.pipeline.parquet.objectstore import ListedObject

SHA256_LENGTH = 64


def local_read(path: Path, digest: str | None = None) -> bytes:
    """Bound a local proof read before trusting its digest."""
    with path.open("rb") as stream:
        payload = stream.read(MAX_BYTES + 1)
    require(len(payload) <= MAX_BYTES, "local proof exceeds byte bound")
    require(digest is None or sha256_digest(payload) == digest, f"local proof digest mismatch: {path.name}")
    return payload


def blob(out: Path, payload: bytes) -> str:
    """Retain one content-addressed local preparation object."""
    digest = sha256_digest(payload)
    target = out / "objects" / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        require(local_read(target, digest) == payload, "local blob collision")
    return digest


def load_objects(out: Path, references: dict[str, str]) -> dict[str, bytes]:
    """Resolve only digest-shaped local object names."""
    result = {}
    for key, digest in references.items():
        require(len(digest) == SHA256_LENGTH and all(c in "0123456789abcdef" for c in digest), "invalid blob name")
        result[key] = local_read(out / "objects" / digest, digest)
    return result


def connect() -> tuple[BotoObjectStoreBackend, BotoAvailabilityStorage, str]:
    """Construct explicit production stores only after CLI proof validation."""
    backend = BotoObjectStoreBackend.from_credentials(settings.require_object_store())
    prefix = settings.object_store_prefix.strip("/")
    storage = BotoAvailabilityStorage(bucket=backend.bucket, client=backend.client, prefix=prefix)
    return backend, storage, prefix + "/" if prefix else ""


def validate_plan(
    plan: dict[str, Any], source: dict[str, bytes], original: dict[str, bytes], *, now: datetime
) -> AvailabilityIndex:
    """Revalidate apply scope independently of a caller's ability to rehash a JSON request."""
    require(
        set(plan)
        == {
            "schema_version",
            "lane_root",
            "published_at",
            "run_id",
            "source_ceiling",
            "bucket",
            "prefix",
            "proof",
            "days",
            "originals",
            "row_hashes",
            "source_complete",
            "requires_external_quiescence",
        },
        "request fields changed",
    )
    require(
        plan["schema_version"] == "sensors-positive-correction/v1" and plan["lane_root"] == LANE_ROOT,
        "unknown request scope",
    )
    require(
        plan["prefix"] == "" and plan["source_complete"] is False and plan["requires_external_quiescence"] is True,
        "request safety scope changed",
    )
    published = datetime.fromisoformat(plan["published_at"])
    require(
        published.tzinfo is not None and published.utcoffset() == UTC.utcoffset(published) and published <= now,
        "publication clock must be an aware nonfuture UTC instant",
    )
    require(
        plan["source_ceiling"] == str(allowed_source_ceiling(LANE_REGISTRY["sensors"], today=published.date())),
        "registered source ceiling changed",
    )
    require(
        plan["run_id"] == "sensors-correction-" + published.strftime("%Y%m%dT%H%M%S%fZ"),
        "run identity differs from fixed publication time",
    )
    manifest = json.loads(source["candidate-manifest.json"])
    require(
        sha256_digest(source["candidate-manifest.json"]) == CANDIDATE_SHA
        and sha256_digest(source["source-archive.tar.gz"]) == ARCHIVE_SHA,
        "source pins changed",
    )
    require(
        set(source) == {"candidate-manifest.json", "source-archive.tar.gz", *manifest["artifacts"]},
        "proof population changed",
    )
    require(set(plan["days"]) == {str(d) for d in DAYS}, "target days changed")
    require(all(set(item) == {"objects", "ledger"} for item in plan["days"].values()), "day fields changed")
    require(
        all(set(item) == {"sha256", "etag", "version_id"} for item in plan["originals"].values()),
        "original identity fields changed",
    )
    require(
        len(original) <= MAX_OBJECTS and set(original) == set(plan["originals"]), "original graph exceeds exact bounds"
    )
    captured = {item["key"]: item for item in manifest["original_publication_objects"] if item.get("present")}
    for key in set().union(*(original_keys(d) for d in DAYS)):
        require(
            sha256_digest(original[key]) == captured[key]["sha256"], "original absence no longer matches captured proof"
        )
        require(
            plan["originals"][key]["etag"] == captured[key]["etag"]
            and plan["originals"][key]["version_id"] == captured[key]["version_id"],
            "original absence identity changed",
        )
    storage = SnapshotStorage(
        {
            key: StoredAvailabilityObject(value, plan["originals"][key]["etag"], plan["originals"][key]["version_id"])
            for key, value in original.items()
        }
    )
    index = read_latest_availability(storage, lane_root=LANE_ROOT, expected_required_rungs=RUNGS)
    require(
        plan["row_hashes"] == {f"{r.day}/{r.rung}": sha256_digest(encode(asdict(r))) for r in index.rows},
        "original row census changed",
    )
    expected = {
        f"{LANE_ROOT}/availability/_LATEST.json",
        f"{LANE_ROOT}/availability/bootstrap/_BOOTSTRAPPED.json",
        index.pointer.generation_key,
    }
    for day in DAYS:
        rows = [row for row in index.rows if row.day == day]
        require(
            len(rows) == len(RUNGS)
            and all(row.terminal_state == "governed_absence" and row.published_at < published for row in rows),
            "original target rows are not older absences",
        )
        expected.update(original_keys(day))
        for row in rows:
            expected.update(receipt.key for receipt in row.evidence_receipts())
            _, snapshots = _verify_source_evidence_receipt(
                storage,
                row.source_receipt,
                expected_identity=index.pointer.identity,
                expected_day=day,
                expected_source_ceiling=row.source_ceiling,
            )
            expected.update(snapshot.key for snapshot in snapshots)
    require(set(original) == expected, "original graph contains missing or unscoped objects")
    return index


def prepare(candidate: Path, archive: Path, out: Path) -> dict[str, Any]:
    """Read the exact candidate/current objects and emit a local immutable apply request."""
    candidate_bytes = local_read(candidate, CANDIDATE_SHA)
    manifest = json.loads(candidate_bytes)
    require(
        manifest["source_complete"] is False and manifest["archive_sha256"] == ARCHIVE_SHA, "candidate scope changed"
    )
    proof = {
        "candidate-manifest.json": blob(out, candidate_bytes),
        "source-archive.tar.gz": blob(out, local_read(archive, ARCHIVE_SHA)),
    }
    for name, receipt in manifest["artifacts"].items():
        require(Path(name).name == name, "candidate artifact escaped its directory")
        payload = local_read(candidate.parent / name, receipt["sha256"])
        require(len(payload) == receipt["bytes"], "candidate artifact length mismatch")
        proof[name] = blob(out, payload)
    backend, storage, prefix = connect()
    require(prefix == "", "this captured correction is scoped to the original empty object-store prefix")
    index = read_latest_availability(storage, lane_root=LANE_ROOT, expected_required_rungs=RUNGS)
    published = datetime.now(UTC)
    require(
        all(row.published_at < published for row in index.rows if row.day in DAYS),
        "publication clock precedes old rows",
    )
    plan: dict[str, Any] = {
        "schema_version": "sensors-positive-correction/v1",
        "lane_root": LANE_ROOT,
        "published_at": published.isoformat(),
        "run_id": "sensors-correction-" + published.strftime("%Y%m%dT%H%M%S%fZ"),
        "source_ceiling": allowed_source_ceiling(LANE_REGISTRY["sensors"], today=published.date()).isoformat(),
        "bucket": backend.bucket,
        "prefix": prefix,
        "proof": proof,
        "days": {},
        "originals": {},
        "row_hashes": {f"{r.day}/{r.rung}": sha256_digest(encode(asdict(r))) for r in index.rows},
        "source_complete": False,
        "requires_external_quiescence": True,
    }

    def capture(key: str) -> None:
        require(len(plan["originals"]) < MAX_OBJECTS or key in plan["originals"], "original graph exceeds object bound")
        held = storage.read(key, max_bytes=MAX_BYTES)
        require(held is not None, f"required original is missing: {key}")
        if held is not None:
            plan["originals"][key] = {
                "sha256": blob(out, held.payload),
                "etag": held.etag,
                "version_id": held.version_id,
            }

    for key in (
        f"{LANE_ROOT}/availability/_LATEST.json",
        f"{LANE_ROOT}/availability/bootstrap/_BOOTSTRAPPED.json",
        index.pointer.generation_key,
    ):
        capture(key)
    old = {r["key"]: r for r in manifest["original_publication_objects"] if r.get("present")}
    for day in DAYS:
        current = read_scope(backend, storage, day, prefix)
        require(
            storage.read(availability_retry_quarantine_path("sensors", "observed", day), max_bytes=MAX_BYTES) is None,
            "target retry is quarantined",
        )
        require(
            set(current) == original_keys(day),
            "prepare requires exactly four old absences and no other physical objects",
        )
        require(
            storage.read(availability_retry_path("sensors", "observed", day), max_bytes=MAX_BYTES) is None,
            "pending target retry claim exists",
        )
        rows = [r for r in index.rows if r.day == day]
        require(
            len(rows) == len(RUNGS) and all(r.terminal_state == "governed_absence" for r in rows),
            "target index is not four governed absences",
        )
        for row in rows:
            for receipt in row.evidence_receipts():
                capture(receipt.key)
                require(
                    plan["originals"][receipt.key]["sha256"] == receipt.sha256,
                    "old availability evidence digest differs",
                )
            _, snapshots = _verify_source_evidence_receipt(
                storage,
                row.source_receipt,
                expected_identity=index.pointer.identity,
                expected_day=day,
                expected_source_ceiling=row.source_ceiling,
            )
            for snapshot in snapshots:
                capture(snapshot.key)
                require(
                    plan["originals"][snapshot.key]["sha256"] == snapshot.expected_sha256,
                    "old nested source evidence changed",
                )
        for key, payload in current.items():
            require(sha256_digest(payload) == old[key]["sha256"], "absence is not the captured false-absence identity")
            capture(key)
            require(plan["originals"][key]["etag"] == old[key]["etag"], "old absence ETag changed")
        name = f"sensors-{day}-positive-candidate.parquet"
        memory, ledger = build_ladder(
            local_read(candidate.parent / name, proof[name]), day=day, run_id=plan["run_id"], published_at=published
        )
        plan["days"][day.isoformat()] = {
            "objects": {key: blob(out, payload) for key, payload in memory.objects.items()},
            "ledger": ledger_wire(ledger),
        }
    for key, receipt in plan["originals"].items():
        held = storage.read(key, max_bytes=MAX_BYTES)
        require(
            held is not None
            and sha256_digest(held.payload) == receipt["sha256"]
            and held.etag == receipt["etag"]
            and held.version_id == receipt["version_id"],
            "original changed during prepare",
        )
    target = out / "request.json"
    validate_plan(
        plan,
        load_objects(out, proof),
        load_objects(out, {key: item["sha256"] for key, item in plan["originals"].items()}),
        now=datetime.now(UTC),
    )
    with target.open("xb") as stream:
        stream.write(encode(plan))
    return {"mode": "prepared", "request": str(target), "sha256": sha256_digest(encode(plan)), "apply_performed": False}


class GuardedBackend:
    """Allow only exact prepared physical writes and exact-day retry-claim operations."""

    def __init__(  # noqa: PLR0913 - exact mutation and identity boundaries
        self,
        backend: BotoObjectStoreBackend,
        storage: BotoAvailabilityStorage,
        prefix: str,
        candidates: dict[str, bytes],
        originals: dict[str, bytes],
        mutation_guard: Callable[[], None],
        original_identities: dict[str, Any],
    ) -> None:
        self.backend, self.storage, self.prefix = backend, storage, prefix
        self.candidates, self.originals = candidates, originals
        self.mutation_guard = mutation_guard
        self.original_identities = original_identities
        self.retry_keys = {availability_retry_path("sensors", "observed", d) for d in DAYS}
        self.deletable = set(originals) | {
            completion_marker_path("sensors", "observed", z, d) for d in DAYS for z in RUNGS
        }
        self.deletable |= {
            derived_empty_completion_marker_path("sensors", "observed", z, d)
            for d in DAYS
            for z in RUNGS
            if z != BASE_ZOOM_TIER
        }

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        self.mutation_guard()
        relative = key.removeprefix(self.prefix)
        require(
            relative in self.retry_keys or payload == self.candidates.get(relative), "unprepared physical write refused"
        )
        self.backend.put(key, payload, content_type=content_type)

    def delete(self, key: str) -> None:
        self.mutation_guard()
        relative = key.removeprefix(self.prefix)
        require(relative in self.deletable or relative in self.retry_keys, "unscoped delete refused")
        held = self.storage.read(relative, max_bytes=MAX_BYTES)
        if held is not None and relative not in self.retry_keys:
            require(
                held.payload == self.originals.get(relative) or held.payload == self.candidates.get(relative),
                "changed object cannot be deleted",
            )
            if relative in self.originals:
                identity = self.original_identities[relative]
                require(
                    held.etag == identity["etag"] and held.version_id == identity["version_id"],
                    "original deletion identity changed",
                )
        self.mutation_guard()
        self.backend.delete(key)

    def get(self, key: str) -> bytes | None:
        held = self.storage.read(key.removeprefix(self.prefix), max_bytes=MAX_BYTES)
        return held.payload if held is not None else None

    def size_of(self, key: str) -> int | None:
        return self.backend.size_of(key)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        return self.backend.list_objects(prefix)


async def apply(  # noqa: PLR0912, PLR0915 - ordered recovery gates
    request: Path, request_sha: str, quiescence: Path, quiescence_sha: str
) -> dict[str, Any]:
    """Apply only a pinned plan under externally proven quiescence and existing advisory locks."""
    payload = local_read(request, request_sha)
    plan = json.loads(payload)
    require(
        plan["schema_version"] == "sensors-positive-correction/v1" and plan["lane_root"] == LANE_ROOT,
        "unknown repair request",
    )
    require(set(plan["days"]) == {str(d) for d in DAYS}, "request is not exactly September 5/6")
    proof_bytes = local_read(quiescence, quiescence_sha)
    proof = json.loads(proof_bytes)
    verify_quiescence(proof, request_sha=request_sha, now=datetime.now(UTC))
    local = request.parent
    source = load_objects(local, plan["proof"])
    require(
        sha256_digest(source["candidate-manifest.json"]) == CANDIDATE_SHA
        and sha256_digest(source["source-archive.tar.gz"]) == ARCHIVE_SHA,
        "source pins differ",
    )
    source_manifest = json.loads(source["candidate-manifest.json"])
    for name, receipt in source_manifest["artifacts"].items():
        require(
            sha256_digest(source[name]) == receipt["sha256"] and len(source[name]) == receipt["bytes"],
            "archived source artifact changed",
        )
    original = load_objects(local, {key: value["sha256"] for key, value in plan["originals"].items()})
    original_index = validate_plan(plan, source, original, now=datetime.now(UTC))
    candidates: dict[str, bytes] = {}
    for day in DAYS:
        record = plan["days"][str(day)]
        memory, ledger = build_ladder(
            source[f"sensors-{day}-positive-candidate.parquet"],
            day=day,
            run_id=plan["run_id"],
            published_at=datetime.fromisoformat(plan["published_at"]),
        )
        require(ledger_wire(ledger) == record["ledger"], "prepared finalization does not reproduce")
        held_candidates = load_objects(local, record["objects"])
        require(memory.objects == held_candidates, "prepared ladder does not reproduce")
        candidates.update(held_candidates)
    backend, storage, prefix = connect()
    require(prefix == "", "this captured correction is scoped to the original empty object-store prefix")
    require(backend.bucket == plan["bucket"] and prefix == plan["prefix"], "store target changed")
    physical_original = {
        key: value for key, value in original.items() if key in set().union(*(original_keys(d) for d in DAYS))
    }
    repair_root = f"{LANE_ROOT}/availability/repairs/{request_sha}"
    journal_key = f"{repair_root}/journal.json"
    journal: dict[str, Any] = {"request_sha256": request_sha, "days": {str(d): "archived" for d in DAYS}}
    async with ingest_session() as session, AsyncExitStack() as locks:
        require(
            await locks.enter_async_context(postgres_lane_publication_barrier(session, LANE_ROOT)),
            "publication barrier contended",
        )
        for day in DAYS:
            require(
                await locks.enter_async_context(
                    postgres_lane_day_lock(session, f"parquet-gap-fill:sensors:observed:z13:{day}")
                ),
                "day lock contended",
            )
        connection = await session.connection()
        pid = (await session.execute(select(func.pg_backend_pid()))).scalar_one()
        sync_connection = connection.sync_connection
        if sync_connection is None:
            raise ValueError("pinned connection has no synchronous owner")
        driver_connection = sync_connection.connection.driver_connection

        def mutation_guard() -> None:
            verify_quiescence(proof, request_sha=request_sha, now=datetime.now(UTC))
            require(not connection.invalidated and not connection.closed, "lock connection was lost before mutation")
            held_sync = connection.sync_connection
            if held_sync is None:
                raise ValueError("pinned connection lost its synchronous owner")
            require(
                held_sync is sync_connection and held_sync.connection.driver_connection is driver_connection,
                "lock driver connection changed",
            )

        store = ObjectStore(
            GuardedBackend(backend, storage, prefix, candidates, physical_original, mutation_guard, plan["originals"]),
            prefix=prefix.strip("/"),
        )

        async def ownership() -> None:
            mutation_guard()
            verify_quiescence(proof, request_sha=request_sha, now=datetime.now(UTC))
            require(not connection.invalidated and not connection.closed, "lock connection was lost")
            require((await session.execute(select(func.pg_backend_pid()))).scalar_one() == pid, "lock backend changed")

        @asynccontextmanager
        async def owned_day(held_session: AsyncSession, key: str) -> AsyncIterator[bool]:
            require(
                held_session is session and key in {f"parquet-gap-fill:sensors:observed:z13:{d}" for d in DAYS},
                "unowned day lock callback",
            )
            await ownership()
            yield True

        @asynccontextmanager
        async def owned_barrier(held_session: AsyncSession, lane_root: str) -> AsyncIterator[bool]:
            require(held_session is session and lane_root == LANE_ROOT, "unowned publication callback")
            await ownership()
            yield True

        previous = storage.read(journal_key, max_bytes=MAX_BYTES)
        if previous is not None:
            journal = json.loads(previous.payload)
            require(journal["request_sha256"] == request_sha, "journal belongs to a different request")
        else:
            for key, expected in plan["originals"].items():
                held = storage.read(key, max_bytes=MAX_BYTES)
                require(
                    held is not None
                    and held.payload == original[key]
                    and held.etag == expected["etag"]
                    and held.version_id == expected["version_id"],
                    "original identity changed before apply",
                )
            for day in DAYS:
                require(
                    storage.read(availability_retry_path("sensors", "observed", day), max_bytes=MAX_BYTES) is None,
                    "pre-existing target retry claim",
                )

        async def save() -> None:
            nonlocal previous
            await ownership()
            require(
                storage.compare_and_swap(
                    journal_key,
                    encode(journal),
                    expected_etag=previous.etag if previous else None,
                    content_type=JSON_CONTENT,
                ),
                "journal CAS conflict",
            )
            previous = storage.read(journal_key, max_bytes=MAX_BYTES)
            require(previous is not None and previous.payload == encode(journal), "journal readback mismatch")

        current_index = read_latest_availability(storage, lane_root=LANE_ROOT, expected_required_rungs=RUNGS)
        verify_generation_binding(current_index, original_index)
        marker_key = f"{LANE_ROOT}/availability/bootstrap/_BOOTSTRAPPED.json"
        held_marker = storage.read(marker_key, max_bytes=MAX_BYTES)
        require(held_marker is not None and held_marker.payload == original[marker_key], "bootstrap marker changed")
        require(
            {f"{r.day}/{r.rung}" for r in current_index.rows} == set(plan["row_hashes"]),
            "availability population changed",
        )
        for row in current_index.rows:
            if sha256_digest(encode(asdict(row))) == plan["row_hashes"][f"{row.day}/{row.rung}"]:
                continue
            require(
                row.day in DAYS and journal["days"][str(row.day)] in ("physical_verified", "indexed"),
                "unexpected availability replacement",
            )
            outcome = finalized(plan, row.day, request_sha)
            verify_index_row(row, outcome, current_index.pointer.identity)
            require(
                row.terminal_state == "published" and row.published_at == outcome.published_at,
                "replacement timestamp/state changed",
            )
            verify_export_source(storage, outcome)
        for day in DAYS:
            retry = storage.read(availability_retry_path("sensors", "observed", day), max_bytes=MAX_BYTES)
            require(
                storage.read(availability_retry_quarantine_path("sensors", "observed", day), max_bytes=MAX_BYTES)
                is None,
                "target retry is quarantined",
            )
            if retry is not None:
                require(
                    journal["days"][str(day)] in ("physical_verified", "indexed"),
                    "retry appeared before durable physical finalization",
                )
                verify_retry(retry.payload, finalized(plan, day, request_sha))
        for value in [*source.values(), *original.values(), *candidates.values(), proof_bytes]:
            await ownership()
            storage.put_immutable(
                f"{repair_root}/objects/{sha256_digest(value)}", value, content_type="application/octet-stream"
            )
        storage.put_immutable(f"{repair_root}/request.json", payload, content_type=JSON_CONTENT)
        storage.put_immutable(f"{repair_root}/quiescence/{quiescence_sha}.json", proof_bytes, content_type=JSON_CONTENT)
        await save()
        for day in DAYS:
            await ownership()
            state = journal["days"][str(day)]
            require(state in ("archived", "mutation_started", "physical_verified", "indexed"), "unknown journal phase")
            day_candidates = {k: candidates[k] for k in plan["days"][str(day)]["objects"]}
            day_original = {k: physical_original[k] for k in original_keys(day)}
            current = read_scope(backend, storage, day, prefix)
            check_known_objects(current, day_original, day_candidates, mutation_started=state != "archived")
            if state in ("archived", "mutation_started"):
                journal["days"][str(day)] = "mutation_started"
                await save()
                for zoom in RUNGS:
                    await ownership()
                    store.clear_absence_marker("sensors", "observed", zoom, day)
                table = pq.read_table(io.BytesIO(source[f"sensors-{day}-positive-candidate.parquet"]))
                lane = replace(
                    LANE_REGISTRY["sensors"], adapter=cast("LaneAdapter", DirectSensorsForwardAdapter(table))
                )
                with store.recording_written_objects() as actual_written:
                    result = await fill_one_lane_day(
                        session,
                        store,
                        lane,
                        day=day,
                        run_id=plan["run_id"],
                        now=lambda: datetime.fromisoformat(plan["published_at"]),
                        today=date.fromisoformat(plan["published_at"][:10]),
                        lane_day_lock=owned_day,
                        extend_availability=False,
                    )
                require(result[0] == "written", f"ordinary finalizer failed: {result}")
                require(
                    ledger_wire(actual_written) == plan["days"][str(day)]["ledger"],
                    "actual write receipts differ from durable finalization",
                )
                await ownership()
                verify_physical(store, day_candidates, read_scope(backend, storage, day, prefix), day)
                journal["days"][str(day)] = "physical_verified"
                await save()
            verify_physical(store, day_candidates, read_scope(backend, storage, day, prefix), day)
            outcome = finalized(plan, day, request_sha)
            extension = await extend_availability_for_lane_day(
                session,
                store,
                lane="sensors",
                kind="observed",
                day=day,
                outcome=outcome,
                availability=storage,
                now=lambda: datetime.now(UTC),
                publication_barrier=owned_barrier,
            )
            require(
                extension.state in ("extended", "skipped_unchanged"), f"exact-day availability deferred: {extension}"
            )
            await ownership()
            verify_physical(store, day_candidates, read_scope(backend, storage, day, prefix), day)
            index = read_latest_availability(storage, lane_root=LANE_ROOT, expected_required_rungs=RUNGS)
            verify_generation_binding(index, original_index)
            rows = [r for r in index.rows if r.day == day]
            require(len(rows) == len(RUNGS), "indexed rung count mismatch")
            for row in rows:
                verify_index_row(row, outcome, index.pointer.identity)
                verify_export_source(storage, outcome)
                require(
                    row.terminal_state == "published"
                    and row.published_at == outcome.published_at
                    and row.provenance == "digested",
                    "indexed row differs from correction",
                )
                for receipt in row.evidence_receipts():
                    evidence = storage.read(receipt.key, max_bytes=MAX_BYTES)
                    require(
                        evidence is not None and sha256_digest(evidence.payload) == receipt.sha256,
                        "final evidence readback mismatch",
                    )
            for row in index.rows:
                if row.day not in DAYS:
                    require(
                        plan["row_hashes"].get(f"{row.day}/{row.rung}") == sha256_digest(encode(asdict(row))),
                        "unrelated availability row changed",
                    )
            journal["days"][str(day)] = "indexed"
            await save()
        await ownership()
        final_index = read_latest_availability(storage, lane_root=LANE_ROOT, expected_required_rungs=RUNGS)
        verify_generation_binding(final_index, original_index)
        require(
            {f"{r.day}/{r.rung}" for r in final_index.rows} == set(plan["row_hashes"]),
            "final availability population changed",
        )
        for day in DAYS:
            expected = {key: candidates[key] for key in plan["days"][str(day)]["objects"]}
            verify_physical(store, expected, read_scope(backend, storage, day, prefix), day)
            for row in final_index.rows:
                if row.day == day:
                    verify_index_row(row, finalized(plan, day, request_sha), final_index.pointer.identity)
        await ownership()
    return {
        "mode": "applied",
        "request_sha256": request_sha,
        "journal_key": journal_key,
        "days": journal["days"],
        "source_complete": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--apply", type=Path, metavar="REQUEST")
    parser.add_argument("--request-sha256")
    parser.add_argument("--quiescence", type=Path)
    parser.add_argument("--quiescence-sha256")
    args = parser.parse_args()
    if args.apply:
        require(
            all((args.request_sha256, args.quiescence, args.quiescence_sha256)),
            "apply requires request and quiescence SHA pins",
        )
        result = asyncio.run(apply(args.apply, args.request_sha256, args.quiescence, args.quiescence_sha256))
    else:
        require(all((args.candidate, args.archive, args.out)), "prepare requires candidate, archive and out")
        result = prepare(args.candidate, args.archive, args.out)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
