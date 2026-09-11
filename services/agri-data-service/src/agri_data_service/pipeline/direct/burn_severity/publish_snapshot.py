"""Publish an eligible staged MTBS snapshot through the ordinary lane writer and index."""

from __future__ import annotations

import io
import json
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path, day_prefix
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, validate_zoom_tier
from agri_data_service.pipeline.direct.burn_severity.capture import MAX_RAW_BYTES, MAX_RESPONSE_BYTES, prepare_capture
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import canonical_bytes, digest, put_local_blob
from agri_data_service.pipeline.direct.burn_severity.products import burn_severity_lane_registration
from agri_data_service.pipeline.direct.burn_severity.stage import (
    ROOT,
    blob_key,
    checked_sha,
    load_stage,
    manifest_key,
    remove_published_stage,
)
from agri_data_service.pipeline.parquet.availability_extension import (
    FinalizedLaneDay,
    LaneDaySource,
    _source_object,
    extend_availability_for_lane_day,
)
from agri_data_service.pipeline.parquet.availability_index import (
    EvidenceReceipt,
    SourceEvidence,
    TerminalEvidence,
    availability_row_from_terminal_evidence,
    build_source_evidence,
    build_terminal_evidence,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.derivation import govern_day_absent
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA

if TYPE_CHECKING:
    import pyarrow as pa
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

LANE = "burn-severity"
LANE_ROOT = "layer=burn-severity/kind=observed"


def _reproduce_stage(storage: AvailabilityStorage, manifest: dict[str, Any], prepared: dict[str, Any]) -> None:
    """Reproduce prepared geometry from every pinned decoded source entity before any physical write."""
    with tempfile.TemporaryDirectory(prefix="mtbs-publish-proof-") as directory:
        root = Path(directory)
        captured = root / "capture"
        blobs = captured / "blobs"
        blobs.mkdir(parents=True)
        body = canonical_bytes(manifest)
        identity = prepared["descriptor"]["manifest_sha256"]
        if digest(body) != identity:
            raise ValueError("MTBS source manifest is not canonical")
        (captured / "manifest.json").write_bytes(body)
        total = 0
        for receipt in manifest["responses"]:
            if type(receipt["bytes"]) is not int or not 0 <= receipt["bytes"] <= MAX_RESPONSE_BYTES:
                raise ValueError("MTBS source entity exceeds its bounded read contract")
            total += receipt["bytes"]
            if total > MAX_RAW_BYTES:
                raise ValueError("MTBS source graph exceeds aggregate byte contract")
            source = storage.read(blob_key(receipt["sha256"]), max_bytes=receipt["bytes"])
            if source is None or len(source.payload) != receipt["bytes"] or digest(source.payload) != receipt["sha256"]:
                raise ValueError("MTBS staged source entity failed its identity")
            put_local_blob(blobs, source.payload)
        reproduced = prepare_capture(captured, identity, root / "prepared")
        if canonical_bytes(reproduced) != canonical_bytes(prepared):
            raise ValueError("MTBS staged candidate does not derive from its archived source graph")


def _day_keys(store: ObjectStore, tier: Any, day: date) -> tuple[str, ...]:
    prefix = day_prefix(LANE, "observed", tier, day)
    return tuple(
        key
        for key in store.list_partition_keys(LANE, "observed", tier, year=day.year, month=day.month)
        if key.startswith(prefix)
    )


def _rung_tables(storage: AvailabilityStorage, prepared: dict[str, Any]) -> dict[int, pa.Table]:
    tables = {}
    expected_rows = prepared["descriptor"]["source_row_count"]
    identity = checked_sha(prepared["descriptor"]["manifest_sha256"])
    for receipt in prepared["rungs"]:
        source = storage.read(blob_key(receipt["sha256"]), max_bytes=receipt["bytes"])
        if source is None or len(source.payload) != receipt["bytes"] or digest(source.payload) != receipt["sha256"]:
            raise ValueError("MTBS staged rung differs from its immutable receipt")
        table = pq.read_table(io.BytesIO(source.payload))
        if (
            table.schema != BURN_SEVERITY_SCHEMA.arrow_schema
            or table.num_rows != expected_rows
            or table["geom"].null_count
        ):
            raise ValueError("MTBS staged rung violates row/schema/geometry contract")
        if set(table["release_identifier"].to_pylist()) != (
            {f"mtbs-current-snapshot:{identity}"} if expected_rows else set()
        ):
            raise ValueError("MTBS staged rows lost their source binding")
        if not table.equals(conform_to_stream_schema(table, BURN_SEVERITY_SCHEMA)):
            raise ValueError("MTBS staged rung is not canonically conformed")
        tables[receipt["zoom"]] = table
    return tables


def _verify_owned_physical(store: ObjectStore, prepared: dict[str, Any], day: date, run_id: str) -> None:
    """Refuse foreign original data; retries can touch only this prepared snapshot's bytes."""
    for tier in ZOOM_TIERS:
        expected = next(row for row in prepared["rungs"] if row["zoom"] == tier)
        keys = tuple(key for key in _day_keys(store, tier, day) if key.endswith(".parquet"))
        if keys and not prepared["descriptor"]["source_row_count"]:
            raise ValueError("MTBS empty snapshot contains unexpected physical data")
        if keys:
            observed = store.read_partition_with_receipts(LANE, "observed", tier, day)
            if len(observed.parts) != 1 or observed.parts[0].sha256 != expected["sha256"]:
                raise ValueError("MTBS target day contains foreign or changed data")
        completion = store.read_completion_marker(LANE, "observed", tier, day)
        if completion is not None and completion.run_id != run_id:
            raise ValueError("MTBS target day contains a foreign completion")
        absence = store.read_absence(LANE, "observed", tier, day)
        if absence is not None and (prepared["descriptor"]["source_row_count"] or absence.run_id != run_id):
            raise ValueError("MTBS target day contains a foreign absence")


def _binding(prepared: dict[str, Any], identity: str) -> dict[str, Any]:
    return {
        "schema_version": "mtbs-current-snapshot-binding/v1",
        "manifest": {"key": manifest_key(identity), "sha256": identity},
        "descriptor": prepared["descriptor"],
    }


def _verify_index(storage: AvailabilityStorage, store: ObjectStore, prepared: dict[str, Any], day: date) -> None:  # noqa: PLR0912 - ordered source and terminal proof checks
    index = read_latest_availability(
        storage,
        lane_root=LANE_ROOT,
        expected_lane=LANE,
        expected_product=LANE,
        expected_nature="release_series",
        expected_required_rungs=(0, 5, 9, 13),
    )
    rows = [row for row in index.rows if row.day == day]
    if len(rows) != len(ZOOM_TIERS) or {row.rung for row in rows} != set(ZOOM_TIERS):
        raise ValueError("MTBS snapshot is not indexed at every rung")
    identity = prepared["descriptor"]["manifest_sha256"]
    attempt = storage.read(f"{ROOT}/mtbs-staging/attempts/{identity}.json", max_bytes=4096)
    if attempt is None:
        raise ValueError("MTBS publication lost its durable attempt identity")
    record = json.loads(attempt.payload)
    if record.get("manifest_sha256") != identity:
        raise ValueError("MTBS publication attempt changed source identity")
    source_key, source_body = _source_object(
        LANE_ROOT,
        day=day,
        source=LaneDaySource(
            origin="mtbs-current-snapshot",
            run_id=f"mtbs-current-snapshot:{identity}",
            row_count=prepared["descriptor"]["source_row_count"],
            part_count=int(prepared["descriptor"]["source_row_count"] > 0),
            exported_at=datetime.fromisoformat(record["started_at"]),
            detail=canonical_bytes(_binding(prepared, identity)).decode(),
        ),
    )
    expected_source = build_source_evidence(
        SourceEvidence(
            identity=index.pointer.identity,
            day=day,
            source_ceiling=date.fromisoformat(record["source_ceiling"]),
            object_receipts=(EvidenceReceipt(key=source_key, sha256=digest(source_body)),),
        )
    )
    for key, payload in ((source_key, source_body), (expected_source.receipt.key, expected_source.payload)):
        held = storage.read(key, max_bytes=len(payload))
        if held is None or held.payload != payload:
            raise ValueError("MTBS indexed source evidence differs from the staged descriptor")
    for row in rows:
        tier = validate_zoom_tier(row.rung)
        expected = next(rung for rung in prepared["rungs"] if rung["zoom"] == row.rung)
        if row.row_count != expected["rows"]:
            raise ValueError("MTBS indexed snapshot row count differs from its stage")
        if row.source_receipt != expected_source.receipt or row.terminal_state != (
            "published" if expected["rows"] else "governed_absence"
        ):
            raise ValueError("MTBS indexed snapshot source or terminal state differs from its stage")
        if expected["rows"] and [receipt.sha256 for receipt in row.data_receipts] != [expected["sha256"]]:
            raise ValueError("MTBS indexed snapshot data differs from its stage")
        completion_ref = None
        absence_ref = None
        absence_reason = None
        data_refs: tuple[EvidenceReceipt, ...] = ()
        if expected["rows"]:
            physical = store.read_partition_with_receipts(LANE, "observed", tier, day)
            completion = store.read_completion_receipt(LANE, "observed", tier, day)
            if len(physical.parts) != 1 or physical.parts[0].sha256 != expected["sha256"] or completion is None:
                raise ValueError("MTBS indexed snapshot lost a physical part or completion")
            if completion.completion.row_count != expected["rows"] or completion.completion.part_count != 1:
                raise ValueError("MTBS snapshot completion count differs from its physical stage")
            data_refs = tuple(EvidenceReceipt(key=part.relative_path, sha256=part.sha256) for part in physical.parts)
            completion_ref = EvidenceReceipt(key=completion.relative_path, sha256=completion.sha256)
        else:
            absence = store.read_absence(LANE, "observed", tier, day)
            if absence is None or absence.run_id != f"mtbs-current-snapshot:{identity}":
                raise ValueError("MTBS empty snapshot lost its physical absence")
            absence_reason = absence.reason
            absence_ref = EvidenceReceipt(
                key=absence_marker_path(LANE, "observed", tier, day), sha256=digest(absence.to_json_bytes())
            )
        evidence = TerminalEvidence(
            identity=index.pointer.identity,
            day=day,
            rung=row.rung,
            terminal_state=row.terminal_state,
            row_count=expected["rows"],
            source_ceiling=date.fromisoformat(record["source_ceiling"]),
            published_at=datetime.fromisoformat(record["started_at"]),
            source_receipt=expected_source.receipt,
            data_receipts=data_refs,
            completion_receipt=completion_ref,
            absence_receipt=absence_ref,
            absence_reason=absence_reason,
        )
        terminal = build_terminal_evidence(evidence)
        if row != availability_row_from_terminal_evidence(evidence, terminal_receipt=terminal.receipt):
            raise ValueError("MTBS indexed terminal evidence differs from exact current physical receipts")
        held_terminal = storage.read(terminal.receipt.key, max_bytes=len(terminal.payload))
        if held_terminal is None or held_terminal.payload != terminal.payload:
            raise ValueError("MTBS indexed terminal wrapper is missing or changed")


async def publish_stage(  # noqa: PLR0912, PLR0915 - one lock-owned resumable publication transaction
    session: AsyncSession, store: ObjectStore, storage: AvailabilityStorage, *, identity: str, today: date
) -> dict[str, object]:
    """Publish one eligible immutable stage; keep it queued until all rungs are indexed."""
    manifest, prepared = load_stage(storage, identity)
    day = date.fromisoformat(manifest["available_day"])
    if today > datetime.now(UTC).date():
        raise ValueError("MTBS publisher cannot advance its clock into the future")
    if day > today:
        return {"status": "staged_not_yet_available", "available_day": day.isoformat()}
    _reproduce_stage(storage, manifest, prepared)
    tables = _rung_tables(storage, prepared)
    lane = burn_severity_lane_registration()
    run_id = f"mtbs-current-snapshot:{identity}"
    connection = await session.connection()
    held_sync = connection.sync_connection
    if held_sync is None:
        raise ValueError("MTBS publisher requires a pinned live connection")
    held_driver = held_sync.connection.driver_connection

    async def guard_connection() -> None:
        current = await session.connection()
        current_sync = current.sync_connection
        if (
            current_sync is None
            or current_sync is not held_sync
            or current_sync.invalidated
            or current_sync.closed
            or current_sync.connection.driver_connection is not held_driver
        ):
            raise ValueError("MTBS publication lost its advisory-lock connection")

    async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
        if not granted:
            return {"status": "contended", "available_day": day.isoformat()}
        await guard_connection()
        done_key = f"{ROOT}/mtbs-staging/published/{identity}.json"
        if storage.read(done_key, max_bytes=4096) is not None:
            _verify_owned_physical(store, prepared, day, run_id)
            _verify_index(storage, store, prepared, day)
            remove_published_stage(storage, identity)
            return {"status": "already_published", "available_day": day.isoformat()}
        attempt_key = f"{ROOT}/mtbs-staging/attempts/{identity}.json"
        attempt = storage.read(attempt_key, max_bytes=4096)
        if attempt is None:
            # No prior ownership: refuse any existing physical partition before creating the attempt.
            for tier in ZOOM_TIERS:
                if _day_keys(store, tier, day):
                    raise ValueError("new MTBS snapshot cannot overwrite an existing publication day")
            started = datetime.now(UTC)
            source_ceiling = max(allowed_source_ceiling(lane, today=today), day)
            storage.put_immutable(
                attempt_key,
                canonical_bytes(
                    {
                        "manifest_sha256": identity,
                        "started_at": started.isoformat(),
                        "source_ceiling": source_ceiling.isoformat(),
                    }
                ),
                content_type="application/json",
            )
        else:
            record = json.loads(attempt.payload)
            if record.get("manifest_sha256") != identity:
                raise ValueError("MTBS publication attempt belongs to another stage")
            started = datetime.fromisoformat(record["started_at"])
            source_ceiling = date.fromisoformat(record["source_ceiling"])
            _verify_owned_physical(store, prepared, day, run_id)
        if (
            started.utcoffset() != timedelta(0)
            or not day <= started.date() <= today
            or not day <= source_ceiling <= today
        ):
            raise ValueError("MTBS durable publication attempt has invalid temporal ownership")

        async def adapter(session: AsyncSession, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:
            del session
            await guard_connection()
            if tables[13].num_rows == 0:
                reason = f"complete MTBS current snapshot {identity} contains no mapped fires in its declared footprint"
                return normalise_export_outcome(
                    govern_day_absent(
                        store,
                        GovernedAbsence(
                            reason=reason,
                            upstream_response="complete bounded source query returned zero fires",
                            recorded_at=started,
                            run_id=run_id,
                        ),
                        layer=LANE,
                        kind="observed",
                        day=day,
                    )
                )
            return normalise_export_outcome(
                (store.write_partition(tables[13], layer=LANE, kind="observed", zoom=13, day=day, part_index=0),)
            )

        with store.recording_written_objects() as written:
            result = await fill_one_lane_day(
                session,
                store,
                replace(lane, adapter=adapter),
                day=day,
                run_id=run_id,
                now=lambda: started,
                today=today,
                lane_day_lock=unlocked_lane_day,
                extend_availability=False,
            )
        if result[0] not in {"written", "absent"}:
            raise ValueError(f"MTBS ordinary snapshot writer refused: {result[0]}")
        await guard_connection()
        _verify_owned_physical(store, prepared, day, run_id)
        binding = _binding(prepared, identity)
        absence = None if result[0] == "written" else store.read_absence(LANE, "observed", 13, day)
        if result[0] == "absent" and absence is None:
            raise ValueError("MTBS empty snapshot lost its absence receipt")
        outcome = FinalizedLaneDay(
            terminal_state="published" if result[0] == "written" else "governed_absence",
            day=day,
            written=written,
            source=LaneDaySource(
                origin="mtbs-current-snapshot",
                run_id=run_id,
                row_count=manifest["source_row_count"],
                part_count=len(written.parts_for(kind="observed", zoom=13, day=day)),
                exported_at=started,
                detail=canonical_bytes(binding).decode(),
            ),
            published_at=started,
            source_ceiling=source_ceiling,
            absence_reason=None if absence is None else absence.reason,
        )
        extended = await extend_availability_for_lane_day(
            session,
            store,
            lane=LANE,
            kind="observed",
            day=day,
            outcome=outcome,
            availability=storage,
            now=lambda: datetime.now(UTC),
        )
        if extended.state not in {"extended", "skipped_unchanged"}:
            raise ValueError(f"MTBS snapshot availability remains owed: {extended.state}")
        await guard_connection()
        _verify_index(storage, store, prepared, day)
        storage.put_immutable(
            done_key,
            canonical_bytes({"manifest_sha256": identity, "available_day": day.isoformat()}),
            content_type="application/json",
        )
        remove_published_stage(storage, identity)
        return {"status": "published", "available_day": day.isoformat(), "rows": manifest["source_row_count"]}
