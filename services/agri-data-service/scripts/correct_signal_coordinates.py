"""Prepare or admit the pinned signal candidates while unbootstrapped; see scripts/AGENTS.md."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from sqlalchemy import func, select

from agri_data_service.config import settings
from agri_data_service.db.engine import ingest_session
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import postgres_lane_day_lock
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore
from agri_data_service.pipeline.parquet.publication_barrier import postgres_lane_publication_barrier
from agri_data_service.pipeline.parquet.signal_candidate_admission import ARCHIVE_PIN, _mapping
from agri_data_service.pipeline.parquet.signal_coordinate_correction import (
    JSON_CONTENT,
    LANE_ROOT,
    MAX_BYTES,
    GuardedSignalBackend,
    SignalRepairDay,
    build_signal_repair,
    encode,
    read_signal_candidate,
    read_signal_scope,
    require,
    require_no_retry,
    require_unbootstrapped,
    string,
    verify_signal_originals,
    verify_signal_physical,
    verify_signal_quiescence,
    verify_signal_transition,
    write_signal_ladder,
)

DEFAULT_BATCH_DAYS: Final = 8
MAX_BATCH_DAYS: Final = 32
OWNERSHIP_PING_SECONDS: Final = 10
MUTATION_GUARD_SECONDS: Final = 60


def local_read(path: Path, *, digest: str | None = None) -> bytes:
    require(not path.is_symlink() and path.is_file(), "input must be a regular local file")
    with path.open("rb") as source:
        payload = source.read(MAX_BYTES + 1)
    require(len(payload) <= MAX_BYTES, "local input exceeds byte budget")
    require(digest is None or sha256_digest(payload) == digest, "local input checksum differs")
    return payload


def connect() -> tuple[BotoObjectStoreBackend, BotoAvailabilityStorage]:
    require(settings.object_store_prefix.strip("/") == "", "signal correction requires the reviewed empty prefix")
    backend = BotoObjectStoreBackend.from_credentials(settings.require_object_store())
    return backend, BotoAvailabilityStorage(bucket=backend.bucket, client=backend.client, prefix="")


def prepare(archive: Path, out: Path) -> dict[str, object]:
    """Read current objects and write only one new local request file."""
    require(not out.exists(), "preparation output must be a new file")
    backend, storage = connect()
    repair = build_signal_repair(archive, bucket=backend.bucket, completed_at=datetime.now(UTC))
    require_unbootstrapped(storage)
    for _pass in range(2):
        for day in repair.days:
            require_no_retry(storage, day.day)
            verify_signal_originals(day, read_signal_scope(backend, storage, day), storage)
        require_unbootstrapped(storage)
    with out.open("xb") as target:
        target.write(repair.request)
    return {
        "request_sha256": sha256_digest(repair.request),
        "days": len(repair.days),
        "archive_sha256": ARCHIVE_PIN.sha256,
        "apply_performed": False,
        "physical_admission_performed": False,
        "availability_publication": False,
        "requires_current_quiescence": True,
        "requires_locked_revalidation": True,
    }


async def apply(  # noqa: PLR0913, PLR0915 - ordered, independently checked recovery gates
    request: Path, archive: Path, request_sha: str, quiescence: Path, quiescence_sha: str, *, max_days: int
) -> dict[str, object]:
    """Admit bounded physical days under real locks, leaving supported availability bootstrap separate."""
    require(1 <= max_days <= MAX_BATCH_DAYS, "apply day budget is outside 1..32")
    payload = local_read(request, digest=request_sha)
    document = _mapping(json.loads(payload))
    completed_at = datetime.fromisoformat(string(document.get("completed_at")))
    require(completed_at.tzinfo is not None and completed_at <= datetime.now(UTC), "invalid repair timestamp")
    repair = build_signal_repair(archive, bucket=string(document.get("bucket")), completed_at=completed_at)
    require(repair.request == payload, "request does not reproduce from the reviewed archive")
    archive_bytes = local_read(archive, digest=ARCHIVE_PIN.sha256)
    proof_bytes = local_read(quiescence, digest=quiescence_sha)
    proof = _mapping(json.loads(proof_bytes))
    verify_signal_quiescence(proof, request_sha=request_sha, now=datetime.now(UTC))
    backend, storage = connect()
    require(document.get("bucket") == backend.bucket, "signal repair bucket changed")
    root = f"{LANE_ROOT}/availability/repairs/{request_sha}"
    journal_key = f"{root}/journal.json"
    states: dict[str, object] = {day.day.isoformat(): "archived" for day in repair.days}
    async with ingest_session() as session, postgres_lane_publication_barrier(session, LANE_ROOT) as owns_lane:
        require(owns_lane, "signal publication barrier contended")
        connection = await session.connection()
        initial_owner = await asyncio.wait_for(
            session.execute(select(func.pg_backend_pid())), timeout=OWNERSHIP_PING_SECONDS
        )
        pid = initial_owner.scalar_one()
        sync_connection = connection.sync_connection
        require(sync_connection is not None, "lock connection has no synchronous owner")
        if sync_connection is None:
            raise ValueError("lock connection has no synchronous owner")
        driver = sync_connection.connection.driver_connection

        def guard() -> None:
            verify_signal_quiescence(proof, request_sha=request_sha, now=datetime.now(UTC))
            require(not connection.invalidated and not connection.closed, "signal lock connection lost")
            held = connection.sync_connection
            if held is None:
                raise ValueError("signal lock connection lost its synchronous owner")
            require(
                held is sync_connection and held.connection.driver_connection is driver,
                "signal lock driver connection changed",
            )

        async def ownership() -> None:
            guard()
            require(await session.connection() is connection, "signal lock session connection changed")
            observed = await asyncio.wait_for(
                session.execute(select(func.pg_backend_pid())), timeout=OWNERSHIP_PING_SECONDS
            )
            require(
                observed.scalar_one() == pid,
                "signal lock backend changed",
            )
            guard()
            require(await session.connection() is connection, "signal lock session connection changed")

        async def archive_exact(key: str, data: bytes, content_type: str) -> None:
            await ownership()
            storage.put_immutable(key, data, content_type=content_type)
            held = storage.read(key, max_bytes=MAX_BYTES)
            require(held is not None and held.payload == data, "durable repair archive readback differs")

        require_unbootstrapped(storage)
        previous = storage.read(journal_key, max_bytes=MAX_BYTES)
        if previous is not None:
            saved = _mapping(json.loads(previous.payload))
            require(
                set(saved) == {"schema_version", "request_sha256", "days"}
                and saved.get("schema_version") == "signal-correction-journal/v1"
                and saved.get("request_sha256") == request_sha,
                "unknown signal correction journal",
            )
            restored = _mapping(saved.get("days"))
            require(
                set(restored) == set(states)
                and all(state in {"archived", "mutating", "verified"} for state in restored.values()),
                "signal journal scope or state differs",
            )
            states = restored
        await archive_exact(f"{root}/request.json", payload, JSON_CONTENT)
        await archive_exact(f"{root}/source-archive.tar.gz", archive_bytes, "application/gzip")
        await archive_exact(f"{root}/quiescence/{quiescence_sha}.json", proof_bytes, JSON_CONTENT)

        async def save() -> None:
            nonlocal previous
            await ownership()
            body = encode(
                {"schema_version": "signal-correction-journal/v1", "request_sha256": request_sha, "days": states}
            )
            require(
                storage.compare_and_swap(
                    journal_key,
                    body,
                    expected_etag=None if previous is None else previous.etag,
                    content_type=JSON_CONTENT,
                ),
                "signal journal CAS conflict",
            )
            previous = storage.read(journal_key, max_bytes=MAX_BYTES)
            require(previous is not None and previous.payload == body, "signal journal readback differs")

        async def write_day(day: SignalRepairDay) -> None:
            loop = asyncio.get_running_loop()
            active = threading.Event()
            active.set()

            async def mutation_ownership() -> None:
                require(active.is_set(), "signal physical mutation owner is inactive")
                await ownership()
                await asyncio.to_thread(require_unbootstrapped, storage)
                await asyncio.to_thread(require_no_retry, storage, day.day)
                await ownership()
                require(active.is_set(), "signal physical mutation owner is inactive")

            def mutation_guard() -> None:
                require(active.is_set(), "signal physical mutation owner is inactive")
                checked = asyncio.run_coroutine_threadsafe(mutation_ownership(), loop)
                try:
                    checked.result(timeout=MUTATION_GUARD_SECONDS)
                except TimeoutError as exc:
                    checked.cancel()
                    raise ValueError("signal mutation ownership check timed out") from exc
                require(active.is_set(), "signal physical mutation owner is inactive")

            guarded = GuardedSignalBackend(backend, storage, day, mutation_guard)
            try:
                await asyncio.to_thread(
                    write_signal_ladder,
                    ObjectStore(guarded),
                    read_signal_candidate(day),
                    day=day.day,
                    completed_at=repair.completed_at,
                    run_id=repair.run_id,
                )
            finally:
                active.clear()

        if previous is None:
            await save()
        admitted = 0
        for day in repair.days:
            name = day.day.isoformat()
            if states[name] != "verified" and admitted >= max_days:
                break
            async with postgres_lane_day_lock(session, f"parquet-gap-fill:signal:observed:z13:{name}") as owns_day:
                require(owns_day, "signal lane-day lock contended")
                await ownership()
                require_unbootstrapped(storage)
                require_no_retry(storage, day.day)
                current = read_signal_scope(backend, storage, day)
                if states[name] == "verified":
                    verify_signal_physical(day, current)
                    continue
                if states[name] == "archived":
                    verify_signal_originals(day, current, storage)
                else:
                    verify_signal_transition(day, current)
                for key, data in day.objects.items():
                    await archive_exact(
                        f"{root}/objects/{sha256_digest(data)}",
                        data,
                        "application/vnd.apache.parquet" if key.endswith(".parquet") else JSON_CONTENT,
                    )
                states[name] = "mutating"
                await save()
                await ownership()
                await write_day(day)
                await ownership()
                require_unbootstrapped(storage)
                require_no_retry(storage, day.day)
                verify_signal_physical(day, read_signal_scope(backend, storage, day))
                states[name] = "verified"
                await save()
                admitted += 1
        complete = all(state == "verified" for state in states.values())
        result: dict[str, object] = {
            "schema_version": "signal-physical-admission/v1",
            "request_sha256": request_sha,
            "archive_sha256": ARCHIVE_PIN.sha256,
            "archive_key": f"{root}/source-archive.tar.gz",
            "journal_key": journal_key,
            "days_admitted_this_run": admitted,
            "days_verified": sum(state == "verified" for state in states.values()),
            "physical_complete": complete,
            "availability_publication": False,
            "selected_day_serving_verified": False,
            "relation_retirement_performed": False,
            "verified_at": datetime.now(UTC).isoformat(),
        }
        if complete:
            receipt = encode(result)
            await archive_exact(f"{root}/physical-verification/{sha256_digest(receipt)}.json", receipt, JSON_CONTENT)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--out", type=Path)
    action.add_argument("--apply", type=Path)
    parser.add_argument("--request-sha256")
    parser.add_argument("--quiescence", type=Path)
    parser.add_argument("--quiescence-sha256")
    parser.add_argument("--max-days", type=int, default=DEFAULT_BATCH_DAYS)
    arguments = parser.parse_args()
    if arguments.out is not None:
        if any((arguments.request_sha256, arguments.quiescence, arguments.quiescence_sha256)):
            parser.error("apply evidence flags cannot be used for preparation")
        result = prepare(arguments.archive, arguments.out)
    else:
        if not all((arguments.request_sha256, arguments.quiescence, arguments.quiescence_sha256)):
            parser.error("apply requires the exact request and quiescence files and their SHA-256 pins")
        result = asyncio.run(
            apply(
                arguments.apply,
                arguments.archive,
                arguments.request_sha256,
                arguments.quiescence,
                arguments.quiescence_sha256,
                max_days=arguments.max_days,
            )
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
