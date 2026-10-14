"""Thin CLI adapters over the externally pinned availability bootstrap and publication contract.

Both verbs validate one local SHA-pinned JSON document offline by default; only `--apply` constructs
a bucket client and a writer session and attempts conditional publication. See `AGENTS.md` in this
directory, "Availability operator inputs".
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from agri_data_service.config import settings
from agri_data_service.db.engine import receiver_writer_session
from agri_data_service.parquet_ops.authorized_serving import MAX_CACHED_INDEX_GENERATION_BYTES, MAX_CACHED_INDEX_ROWS
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityError,
    BotoAvailabilityStorage,
    bootstrap_availability,
    load_bootstrap_request,
    load_publication_request,
    publish_availability,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.availability_reconciliation import (
    AvailabilityReconciliationError,
    ReconciliationCompilation,
    compile_physical_ladder_reconciliation,
    install_reconciliation_evidence,
)
from agri_data_service.pipeline.parquet.availability_trusted_digest import (
    MAX_TRUSTED_DIGEST_DAYS,
    IndexCacheLimits,
    TrustedDigestCompilation,
    compile_trusted_digest,
)
from agri_data_service.pipeline.parquet.objectstore import (
    BotoObjectStoreBackend,
    ObjectStore,
    availability_lane_root,
)
from agri_data_service.pipeline.parquet.verified_days import (
    VerifiedDayCorrection,
    VerifiedDaysError,
    base_completion_sha256,
    forget_verified_days,
    ladder_sha256,
    read_verified_days,
    record_verified_days,
    release_verified_days,
    verified_entries,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIdentity,
        AvailabilityRow,
        BootstrapRequest,
        PublicationRequest,
        PublicationResult,
    )

_APPLY_TIMEOUT_SECONDS = 120.0
_DIGEST_VERB = "availability-digest-trusted"
_VERIFY_VERB = "availability-verify-days"
_RELEASE_VERB = "availability-release-verified-days"
#: A ready day whose head ladder no longer matches what this digest compiled (a write raced the publish).
_HEAD_CHANGED_AFTER_DIGEST = "head_changed_after_digest"


def _pinned_document_options(function: click.decorators.FC) -> click.decorators.FC:
    function = click.option(
        "--apply",
        "apply_",
        is_flag=True,
        default=False,
        help="Construct the bucket client and writer session and publish for real.",
    )(function)
    function = click.option(
        "--expected-row-count",
        required=True,
        type=int,
        help="Externally pinned exact row count the document must carry.",
    )(function)
    function = click.option(
        "--expected-sha256",
        required=True,
        help="Externally pinned digest of the document's exact bytes.",
    )(function)
    return click.option(
        "--input",
        "input_path",
        required=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Local availability input document, already externally verified.",
    )(function)


@click.command("availability-bootstrap")
@_pinned_document_options
def availability_bootstrap(
    input_path: Path,
    expected_sha256: str,
    expected_row_count: int,
    *,
    apply_: bool,
) -> None:
    """Validate one pinned bootstrap document offline; publish generation zero only with --apply.

    The document must already name the exact verified manifest/checkpoint receipts: this verb
    discovers no history, activates no scheduler and grants no production authorization.
    """
    request = _load(
        lambda: load_bootstrap_request(
            input_path,
            expected_sha256=expected_sha256,
            expected_row_count=expected_row_count,
        )
    )
    preview = {
        **_request_preview(
            identity=request.identity,
            rows=len(request.rows),
            input_sha256=request.input_sha256,
            source_ceiling=request.source_ceiling.isoformat(),
            created_at=request.created_at.isoformat(),
        ),
        "input_receipts": [receipt.to_wire() for receipt in request.input_receipts],
        "provenance": request.provenance_summary,
    }
    _run("availability-bootstrap", preview, apply_=apply_, work=lambda: _apply_bootstrap(request))


@click.command("availability-publish")
@_pinned_document_options
def availability_publish(
    input_path: Path,
    expected_sha256: str,
    expected_row_count: int,
    *,
    apply_: bool,
) -> None:
    """Validate one pinned append/correction document offline; publish only with --apply.

    The document must name its bootstrap receipt; this verb discovers no history, activates no
    scheduler and grants no production authorization.
    """
    request = _load(
        lambda: load_publication_request(
            input_path,
            expected_sha256=expected_sha256,
            expected_row_count=expected_row_count,
        )
    )
    preview = {
        **_request_preview(
            identity=request.config.identity,
            rows=len(request.rows),
            input_sha256=request.input_sha256,
            source_ceiling=request.config.source_ceiling.isoformat(),
            created_at=request.created_at.isoformat(),
        ),
        "bootstrap_receipt": request.config.bootstrap_receipt.to_wire(),
    }
    _run("availability-publish", preview, apply_=apply_, work=lambda: _apply_publication(request))


@click.command("availability-reconcile-physical")
@click.option("--lane", required=True, help="Registered physical layer slug.")
@click.option("--kind", required=True, type=click.Choice(["observed", "forecast"]))
@click.option("--start", "start_text", required=True, help="Inclusive first day (YYYY-MM-DD).")
@click.option("--end", "end_text", required=True, help="Inclusive last day (YYYY-MM-DD).")
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Local path for the deterministic availability-publication input.",
)
@click.option("--expected-sha256", help="Required with --apply; pin from the reviewed dry run.")
@click.option("--expected-head-generation", help="Required with --apply; head generation key from the dry run.")
@click.option("--apply", "apply_", is_flag=True, default=False, help="Install evidence and invoke the CAS publisher.")
def availability_reconcile_physical(  # noqa: PLR0913 - each option is a safety pin or range coordinate
    lane: str,
    kind: str,
    start_text: str,
    end_text: str,
    output_path: Path,
    expected_sha256: str | None,
    expected_head_generation: str | None,
    *,
    apply_: bool,
) -> None:
    """Compile complete physical ladders into a pinned availability-only publication."""
    try:
        start_day = date.fromisoformat(start_text)
        end_day = date.fromisoformat(end_text)
    except ValueError as exc:
        raise click.ClickException(f"invalid_reconciliation_range: {exc}") from exc
    if apply_ and (expected_sha256 is None or expected_head_generation is None):
        raise click.ClickException("--apply requires --expected-sha256 and --expected-head-generation")
    try:
        storage = _storage()
        store = ObjectStore(
            BotoObjectStoreBackend.from_credentials(settings.require_object_store()),
            prefix=settings.object_store_prefix,
        )
        compilation = compile_physical_ladder_reconciliation(
            store,
            storage,
            lane=lane,
            kind=cast("PartitionKind", kind),
            start_day=start_day,
            end_day=end_day,
        )
    except (AvailabilityError, AvailabilityReconciliationError, OSError, ValueError) as exc:
        raise click.ClickException(f"availability_reconciliation_refused: {exc}") from exc
    if expected_sha256 is not None and compilation.document_sha256 != expected_sha256:
        raise click.ClickException(
            f"reconciliation digest is {compilation.document_sha256}, expected {expected_sha256}"
        )
    if expected_head_generation is not None and compilation.head_generation_key != expected_head_generation:
        raise click.ClickException(
            f"reconciliation head is {compilation.head_generation_key}, expected {expected_head_generation}"
        )
    output_path.write_bytes(compilation.document)
    request = _load(
        lambda: load_publication_request(
            output_path,
            expected_sha256=compilation.document_sha256,
            expected_row_count=compilation.row_count,
        )
    )
    preview = {
        **_request_preview(
            identity=request.config.identity,
            rows=len(request.rows),
            input_sha256=request.input_sha256,
            source_ceiling=request.config.source_ceiling.isoformat(),
            created_at=request.created_at.isoformat(),
        ),
        "candidate_days": [day.isoformat() for day in compilation.candidate_days],
        "already_blessed_days": [day.isoformat() for day in compilation.already_blessed_days],
        "head_generation_key": compilation.head_generation_key,
        "head_generation_sha256": compilation.head_generation_sha256,
        "head_pointer_sha256": compilation.head_pointer_sha256,
        "output": str(output_path),
    }
    _run(
        "availability-reconcile-physical",
        preview,
        apply_=apply_,
        work=lambda: _apply_reconciliation(storage, compilation, request),
    )


@click.command("availability-digest-trusted")
@click.option("--lane", required=True, help="Registered physical layer slug.")
@click.option("--kind", required=True, type=click.Choice(["observed", "forecast"]))
@click.option("--start", "start_text", required=True, help="Inclusive first day (YYYY-MM-DD).")
@click.option("--end", "end_text", required=True, help=f"Inclusive last day; at most {MAX_TRUSTED_DIGEST_DAYS} days.")
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Local JSON report; the publication document is written beside it as <stem>.publication.json.",
)
@click.option("--expected-sha256", help="Required with --apply; document_sha256 from the reviewed dry run.")
@click.option("--expected-head-generation", help="Required with --apply; head_generation_key from the dry run.")
@click.option("--apply", "apply_", is_flag=True, default=False, help="Install evidence and invoke the CAS publisher.")
def availability_digest_trusted(  # noqa: PLR0913 - each option is a safety pin or range coordinate
    lane: str,
    kind: str,
    start_text: str,
    end_text: str,
    output_path: Path,
    expected_sha256: str | None,
    expected_head_generation: str | None,
    *,
    apply_: bool,
) -> None:
    """Hash the parts of manifest-trusted days and publish them as digested rows (dry run by default)."""
    try:
        start_day = date.fromisoformat(start_text)
        end_day = date.fromisoformat(end_text)
    except ValueError as exc:
        raise click.ClickException(f"invalid_digest_range: {exc}") from exc
    span = (end_day - start_day).days + 1
    if span < 1 or span > MAX_TRUSTED_DIGEST_DAYS:
        raise click.ClickException(f"invalid_digest_range: must contain 1..{MAX_TRUSTED_DIGEST_DAYS} days, got {span}")
    if apply_ and (expected_sha256 is None or expected_head_generation is None):
        raise click.ClickException("--apply requires --expected-sha256 and --expected-head-generation")
    try:
        storage = _storage()
        compilation = compile_trusted_digest(
            _object_store(),
            storage,
            lane=lane,
            kind=cast("PartitionKind", kind),
            start_day=start_day,
            end_day=end_day,
            cache_limits=IndexCacheLimits(
                generation_bytes=MAX_CACHED_INDEX_GENERATION_BYTES, rows=MAX_CACHED_INDEX_ROWS
            ),
        )
    except (AvailabilityError, AvailabilityReconciliationError, OSError, ValueError) as exc:
        raise click.ClickException(f"availability_digest_refused: {exc}") from exc
    report = compilation.report()
    document_path = output_path.with_name(f"{output_path.stem}.publication.json")
    publication = compilation.publication
    report["document_path"] = None if publication is None else str(document_path)
    # The report is written before the pins are compared, so a refused apply still leaves the evidence.
    output_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if expected_head_generation is not None and compilation.head_generation_key != expected_head_generation:
        raise click.ClickException(
            f"trusted-digest head is {compilation.head_generation_key}, expected {expected_head_generation}"
        )
    if expected_sha256 is not None and compilation.document_sha256 != expected_sha256:
        raise click.ClickException(
            f"trusted-digest document digest is {compilation.document_sha256}, expected {expected_sha256}"
        )
    preview: dict[str, object] = {
        "document_sha256": compilation.document_sha256,
        "head_generation_key": compilation.head_generation_key,
        "head_generation_sha256": compilation.head_generation_sha256,
        "index_size": report["index_size"],
        "lane": lane,
        "output": str(output_path),
        "totals": report["totals"],
    }
    if publication is None:
        if apply_:
            raise click.ClickException("availability_digest_refused: no day in the range is ready to publish")
        _emit({"operation": "availability-digest-trusted", "applied": False, "request": preview})
        return
    document_path.write_bytes(publication.document)
    request = _load(
        lambda: load_publication_request(
            document_path,
            expected_sha256=publication.document_sha256,
            expected_row_count=publication.row_count,
        )
    )
    _run(
        _DIGEST_VERB,
        preview,
        apply_=apply_,
        work=lambda: _apply_reconciliation(storage, publication, request),
        after=lambda _result: _register_digested_days(storage, compilation, kind=cast("PartitionKind", kind)),
    )


@click.command(_VERIFY_VERB)
@click.option("--lane", required=True, help="Registered physical layer slug.")
@click.option("--kind", required=True, type=click.Choice(["observed", "forecast"]))
@click.option("--start", "start_text", required=True, help="Inclusive first day (YYYY-MM-DD).")
@click.option("--end", "end_text", required=True, help=f"Inclusive last day; at most {MAX_TRUSTED_DIGEST_DAYS} days.")
@click.option("--expected-head-generation", help="Required with --apply; head_generation_key from the dry run.")
@click.option("--apply", "apply_", is_flag=True, default=False, help="Write the verified-days register (CAS).")
def availability_verify_days(  # noqa: PLR0913 - each option is a safety pin or range coordinate
    lane: str,
    kind: str,
    start_text: str,
    end_text: str,
    expected_head_generation: str | None,
    *,
    apply_: bool,
) -> None:
    """Register already-digested published days as VERIFIED -- manual-correction only (dry run by default).

    Verified days are refused by every automated writer; see `pipeline/parquet/AGENTS.md`, "Verified
    days are manual-correction only". This is how a manually corrected day is re-verified.
    """
    try:
        start_day = date.fromisoformat(start_text)
        end_day = date.fromisoformat(end_text)
    except ValueError as exc:
        raise click.ClickException(f"invalid_verify_range: {exc}") from exc
    span = (end_day - start_day).days + 1
    if span < 1 or span > MAX_TRUSTED_DIGEST_DAYS:
        raise click.ClickException(f"invalid_verify_range: must contain 1..{MAX_TRUSTED_DIGEST_DAYS} days, got {span}")
    if apply_ and expected_head_generation is None:
        raise click.ClickException("--apply requires --expected-head-generation")
    partition_kind = cast("PartitionKind", kind)
    try:
        storage = _storage()
        index = read_latest_availability(
            storage, lane_root=availability_lane_root(lane, partition_kind), expected_lane=lane
        )
    except (AvailabilityError, OSError, ValueError) as exc:
        raise click.ClickException(f"availability_verify_refused: {exc}") from exc
    held = read_verified_days(storage, lane=lane, kind=partition_kind)
    if held.unreadable is not None:
        raise click.ClickException(f"availability_verify_refused: the register could not be read: {held.unreadable}")
    entries, skipped = verified_entries(
        index.rows,
        days=(start_day + timedelta(days=offset) for offset in range(span)),
        required_rungs=index.pointer.required_rungs,
        generation_key=index.pointer.generation_key,
        verified_at=datetime.now(UTC),
        verified_by=_VERIFY_VERB,
    )
    already = {
        entry.day
        for entry in entries
        if (current := held.days.get(entry.day)) is not None and current.ladder_sha256 == entry.ladder_sha256
    }
    owed = tuple(entry for entry in entries if entry.day not in already)
    report: dict[str, object] = {
        "operation": _VERIFY_VERB,
        "applied": False,
        "lane": lane,
        "kind": kind,
        "head_generation_key": index.pointer.generation_key,
        "would_register": [entry.day.isoformat() for entry in owed],
        "already_verified": sorted(day.isoformat() for day in already),
        "skipped": _days_by_reason(skipped),
    }
    if not apply_:
        _emit(report)
        return
    if index.pointer.generation_key != expected_head_generation:
        raise click.ClickException(
            f"verify head is {index.pointer.generation_key}, expected {expected_head_generation}"
        )
    if not owed:
        raise click.ClickException("availability_verify_refused: no day in the range is ready to register")
    try:
        record_verified_days(storage, lane=lane, kind=partition_kind, entries=owed)
    except (VerifiedDaysError, AvailabilityError, OSError, ValueError) as exc:
        raise click.ClickException(f"availability_verify_refused: {exc}") from exc
    _emit({**report, "applied": True, "registered": report["would_register"]})


@click.command(_RELEASE_VERB)
@click.option("--lane", required=True, help="Registered physical layer slug.")
@click.option("--kind", required=True, type=click.Choice(["observed", "forecast"]))
@click.option("--day", "day_texts", required=True, multiple=True, help="A verified day (YYYY-MM-DD); repeatable.")
@click.option("--reason", required=True, help="Why the owner is releasing the day(s); kept in the register's trace.")
@click.option("--run-id", help="Correlation id kept in the trace; generated when omitted.")
@click.option("--apply", "apply_", is_flag=True, default=False, help="Release the days from the register (CAS).")
def availability_release_verified_days(  # noqa: PLR0913 - each option is a safety pin or day coordinate
    lane: str,
    kind: str,
    day_texts: tuple[str, ...],
    reason: str,
    run_id: str | None,
    *,
    apply_: bool,
) -> None:
    """Release VERIFIED days so a correction can run: the manual path for every lane (dry run by default).

    Each released day leaves a durable `corrections` record (run id, reason, the verified ladder digest,
    the base marker it had); after it, any writer may rewrite the day. Re-verify with
    `availability-verify-days`. See `pipeline/parquet/AGENTS.md`, "Verified days are manual-correction only".
    """
    try:
        days = sorted({date.fromisoformat(text) for text in day_texts})
    except ValueError as exc:
        raise click.ClickException(f"invalid_release_day: {exc}") from exc
    if not reason.strip():
        raise click.ClickException("availability_release_refused: --reason must say why the day is released")
    partition_kind = cast("PartitionKind", kind)
    try:
        storage = _storage()
    except (AvailabilityError, OSError, ValueError) as exc:
        raise click.ClickException(f"availability_release_refused: {exc}") from exc
    held = read_verified_days(storage, lane=lane, kind=partition_kind)
    if held.unreadable is not None:
        raise click.ClickException(f"availability_release_refused: the register could not be read: {held.unreadable}")
    releasable = [day for day in days if day in held.days]
    report: dict[str, object] = {
        "operation": _RELEASE_VERB,
        "applied": False,
        "lane": lane,
        "kind": kind,
        "reason": reason.strip(),
        "would_release": [day.isoformat() for day in releasable],
        "not_verified": [day.isoformat() for day in days if day not in held.days],
    }
    if not apply_:
        _emit(report)
        return
    if not releasable:
        raise click.ClickException("availability_release_refused: no named day is verified")
    released_at = datetime.now(UTC)
    trace_run_id = run_id or f"{_RELEASE_VERB}:{released_at:%Y%m%dT%H%M%SZ}"
    try:
        store = _object_store()
        corrections = tuple(
            VerifiedDayCorrection(
                day=day,
                corrected_at=released_at,
                run_id=trace_run_id,
                verified_ladder_sha256=held.days[day].ladder_sha256,
                old_base_completion_sha256=base_completion_sha256(store, lane=lane, kind=partition_kind, day=day),
                new_base_completion_sha256=None,
                reason=reason.strip(),
            )
            for day in releasable
        )
        release_verified_days(storage, lane=lane, kind=partition_kind, corrections=corrections)
        forget_verified_days(store)
    except (VerifiedDaysError, AvailabilityError, OSError, ValueError) as exc:
        raise click.ClickException(f"availability_release_refused: {exc}") from exc
    _emit({**report, "applied": True, "released": report["would_release"], "run_id": trace_run_id})


def _register_digested_days(
    storage: BotoAvailabilityStorage, compilation: TrustedDigestCompilation, *, kind: PartitionKind
) -> dict[str, object]:
    """Register each day this digest published AS THE HEAD NOW HOLDS IT; a drifted day is named, not registered."""
    ready = [report.day for report in compilation.days if report.outcome == "ready"]
    index = read_latest_availability(
        storage, lane_root=availability_lane_root(compilation.lane, kind), expected_lane=compilation.lane
    )
    entries, skipped = verified_entries(
        index.rows,
        days=ready,
        required_rungs=index.pointer.required_rungs,
        generation_key=index.pointer.generation_key,
        verified_at=datetime.now(UTC),
        verified_by=_DIGEST_VERB,
    )
    compiled = _ladders_by_day(compilation.rows)
    drifted = {entry.day for entry in entries if compiled.get(entry.day) != entry.ladder_sha256}
    kept = tuple(entry for entry in entries if entry.day not in drifted)
    if kept:
        record_verified_days(storage, lane=compilation.lane, kind=kind, entries=kept)
    return {
        "registered": [entry.day.isoformat() for entry in kept],
        "not_registered": _days_by_reason({**skipped, **dict.fromkeys(drifted, _HEAD_CHANGED_AFTER_DIGEST)}),
    }


def _ladders_by_day(rows: Sequence[AvailabilityRow]) -> dict[date, str]:
    by_day: dict[date, list[AvailabilityRow]] = {}
    for row in rows:
        by_day.setdefault(row.day, []).append(row)
    return {day: ladder_sha256(day_rows) for day, day_rows in by_day.items()}


def _days_by_reason(skipped: dict[date, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for day in sorted(skipped):
        grouped.setdefault(skipped[day], []).append(day.isoformat())
    return grouped


def _request_preview(
    *,
    identity: AvailabilityIdentity,
    rows: int,
    input_sha256: str,
    source_ceiling: str,
    created_at: str,
) -> dict[str, object]:
    return {
        "created_at": created_at,
        "input_sha256": input_sha256,
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        "rows": rows,
        "source_ceiling": source_ceiling,
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }


def _load[T](work: Callable[[], T]) -> T:
    # AvailabilityError is caught alongside ValueError because the loader raises BOTH: a pin mismatch
    # arrives as AvailabilityChecksumError (a RuntimeError), a schema violation as a plain ValueError.
    try:
        return work()
    except (OSError, ValueError, AvailabilityError) as exc:
        raise click.ClickException(f"invalid_availability_input: {exc}") from exc


def _run(
    operation: str,
    preview: dict[str, object],
    *,
    apply_: bool,
    work: Callable[[], Coroutine[object, object, PublicationResult]],
    after: Callable[[PublicationResult], dict[str, object]] | None = None,
) -> None:
    # THE GATE: every store/session constructor sits behind `work`, which is only ever called here.
    # Offline validation has already finished by this point, so a missing --apply cannot touch the
    # network even when the document is perfectly valid.
    if not apply_:
        _emit({"operation": operation, "applied": False, "request": preview})
        return
    try:
        result = asyncio.run(work())
    except AvailabilityError as exc:
        raise click.ClickException(f"availability_refused: {exc}") from exc
    except ValueError as exc:
        raise click.ClickException(f"invalid_availability_input: {exc}") from exc
    payload: dict[str, object] = {
        "operation": operation,
        "applied": True,
        "advanced": result.advanced,
        "attempts": result.attempts,
        "pointer": result.pointer.to_wire(),
        "request": preview,
    }
    if after is not None:
        # THE PUBLICATION IS ALREADY DURABLE; a failed follow-up is reported beside it, never instead of it.
        try:
            payload["verified_registration"] = after(result)
        except (AvailabilityError, VerifiedDaysError, OSError, ValueError) as exc:
            payload["verified_registration"] = {"error": f"{type(exc).__name__}: {exc}"}
            _emit(payload)
            raise click.ClickException(
                f"availability_published_but_not_verified: {exc}; re-run {_VERIFY_VERB} over the same range"
            ) from exc
    _emit(payload)


async def _apply_bootstrap(request: BootstrapRequest) -> PublicationResult:
    async with asyncio.timeout(_APPLY_TIMEOUT_SECONDS), receiver_writer_session() as session:
        return await bootstrap_availability(session, _storage(), request)


async def _apply_publication(request: PublicationRequest) -> PublicationResult:
    async with asyncio.timeout(_APPLY_TIMEOUT_SECONDS), receiver_writer_session() as session:
        return await publish_availability(session, _storage(), request)


async def _apply_reconciliation(
    storage: BotoAvailabilityStorage,
    compilation: ReconciliationCompilation,
    request: PublicationRequest,
) -> PublicationResult:
    """Install immutable evidence, then delegate the only mutable step to the existing CAS publisher."""
    install_reconciliation_evidence(storage, compilation)
    async with asyncio.timeout(_APPLY_TIMEOUT_SECONDS), receiver_writer_session() as session:
        return await publish_availability(
            session,
            storage,
            request,
            expected_head_generation_key=compilation.head_generation_key,
            expected_head_generation_sha256=compilation.head_generation_sha256,
        )


def _storage() -> BotoAvailabilityStorage:
    return BotoAvailabilityStorage.from_settings(settings)


def _object_store() -> ObjectStore:
    return ObjectStore(
        BotoObjectStoreBackend.from_credentials(settings.require_object_store()),
        prefix=settings.object_store_prefix,
    )


def _emit(payload: dict[str, object]) -> None:
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))


__all__ = [
    "availability_bootstrap",
    "availability_digest_trusted",
    "availability_publish",
    "availability_reconcile_physical",
    "availability_release_verified_days",
    "availability_verify_days",
]
