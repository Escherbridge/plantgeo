"""Thin CLI adapters over the externally pinned availability bootstrap and publication contract.

Both verbs validate one local SHA-pinned JSON document offline by default; only `--apply` constructs
a bucket client and a writer session and attempts conditional publication. See `AGENTS.md` in this
directory, "Availability operator inputs".
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from agri_data_service.config import settings
from agri_data_service.db.engine import receiver_writer_session
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityError,
    BotoAvailabilityStorage,
    bootstrap_availability,
    load_bootstrap_request,
    load_publication_request,
    publish_availability,
)
from agri_data_service.pipeline.parquet.availability_reconciliation import (
    AvailabilityReconciliationError,
    ReconciliationCompilation,
    compile_physical_ladder_reconciliation,
    install_reconciliation_evidence,
)
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIdentity,
        BootstrapRequest,
        PublicationRequest,
        PublicationResult,
    )

_APPLY_TIMEOUT_SECONDS = 120.0


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
    _emit(
        {
            "operation": operation,
            "applied": True,
            "advanced": result.advanced,
            "attempts": result.attempts,
            "pointer": result.pointer.to_wire(),
            "request": preview,
        }
    )


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


def _emit(payload: dict[str, object]) -> None:
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))


__all__ = ["availability_bootstrap", "availability_publish", "availability_reconcile_physical"]
