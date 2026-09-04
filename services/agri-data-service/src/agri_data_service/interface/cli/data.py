"""Data ingest, history, coverage, and Parquet command-family wiring."""

import asyncio
import json
from pathlib import Path

import click

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.ingest.commands import INGEST_COMMANDS
from agri_data_service.interface.cli import commands
from agri_data_service.interface.cli._registry import register_commands
from agri_data_service.interface.cli.parquet import parquet
from agri_data_service.pipeline.parquet.availability_index import (
    BootstrapRequest,
    BotoAvailabilityStorage,
    PublicationRequest,
    PublicationResult,
    bootstrap_availability,
    load_bootstrap_request,
    load_publication_request,
    publish_availability,
)


@click.group()
def data() -> None:
    """Ingest, inspect, and maintain governed data products."""


@click.command("availability-bootstrap")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Exact local bootstrap JSON compiled from verified manifests/checkpoints.",
)
@click.option("--input-sha256", required=True, help="Externally computed SHA-256 of --input bytes.")
@click.option("--expected-row-count", type=click.IntRange(min=1), required=True)
@click.option(
    "--apply",
    is_flag=True,
    help="Verify source objects and publish. Omit for the default offline, no-network validation.",
)
def availability_bootstrap(
    input_path: Path,
    input_sha256: str,
    expected_row_count: int,
    apply: bool,
) -> None:
    """Validate or apply one immutable availability bootstrap."""
    try:
        request = load_bootstrap_request(
            input_path,
            expected_sha256=input_sha256,
            expected_row_count=expected_row_count,
        )
        report: dict[str, object] = {
            "apply": apply,
            "dry_run": not apply,
            "input_sha256": request.input_sha256,
            "lane": request.identity.lane,
            "lane_root": request.identity.lane_root,
            "product": request.identity.product,
            "required_rungs": list(request.identity.required_rungs),
            "row_count": len(request.rows),
            "source_ceiling": request.source_ceiling.isoformat(),
            "verified_source_inventory_root": request.identity.verified_source_inventory_root,
        }
        if apply:
            result = asyncio.run(_apply_availability_bootstrap(request))
            report.update(_publication_report(result.pointer.to_wire(), result.advanced, result.attempts))
    except (OSError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, sort_keys=True, separators=(",", ":")))


@click.command("availability-publish")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Exact local append/correction JSON whose receipts already name durable objects.",
)
@click.option("--input-sha256", required=True, help="Externally computed SHA-256 of --input bytes.")
@click.option("--expected-row-count", type=click.IntRange(min=1), required=True)
@click.option(
    "--apply",
    is_flag=True,
    help="Verify receipts and conditionally advance _LATEST. Omit for offline, no-network validation.",
)
def availability_publish(
    input_path: Path,
    input_sha256: str,
    expected_row_count: int,
    apply: bool,
) -> None:
    """Validate or publish terminal availability outcomes."""
    try:
        request = load_publication_request(
            input_path,
            expected_sha256=input_sha256,
            expected_row_count=expected_row_count,
        )
        report: dict[str, object] = {
            "apply": apply,
            "dry_run": not apply,
            "input_sha256": request.input_sha256,
            "lane": request.config.identity.lane,
            "lane_root": request.config.identity.lane_root,
            "product": request.config.identity.product,
            "required_rungs": list(request.config.identity.required_rungs),
            "row_count": len(request.rows),
            "source_ceiling": request.config.source_ceiling.isoformat(),
        }
        if apply:
            result = asyncio.run(_apply_availability_publication(request))
            report.update(_publication_report(result.pointer.to_wire(), result.advanced, result.attempts))
    except (OSError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, sort_keys=True, separators=(",", ":")))


def _publication_report(pointer: dict[str, object], advanced: bool, attempts: int) -> dict[str, object]:
    """Render the small common command result."""
    return {"advanced": advanced, "attempts": attempts, "pointer": pointer}


async def _apply_availability_bootstrap(request: BootstrapRequest) -> PublicationResult:
    """Apply bootstrap through the dedicated loader session and guarded public API."""
    database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(database_url) as session:
        return await bootstrap_availability(session, BotoAvailabilityStorage.from_settings(), request)


async def _apply_availability_publication(request: PublicationRequest) -> PublicationResult:
    """Apply publication through the dedicated loader session and guarded public API."""
    database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(database_url) as session:
        return await publish_availability(session, BotoAvailabilityStorage.from_settings(), request)


register_commands(
    data,
    (
        *((command.name or "", command) for command in INGEST_COMMANDS if (command.name or "").startswith("ingest-")),
        ("source-ingest", commands.source_ingest),
        ("source-ingest-status", commands.source_ingest_status),
        ("historical-nasa-backfill", commands.historical_nasa_backfill),
        ("historical-nasa-status", commands.historical_nasa_status),
        ("historical-nasa-materialize-parquet", commands.historical_nasa_materialize_parquet),
        ("historical-nasa-finalize", commands.historical_nasa_finalize),
        ("historical-era5-backfill", commands.historical_era5_backfill),
        ("historical-era5-persist", commands.historical_era5_persist),
        ("historical-era5-materialize-parquet", commands.historical_era5_materialize_parquet),
        ("historical-era5-finalize", commands.historical_era5_finalize),
        ("historical-open-meteo-status", commands.historical_open_meteo_status),
        ("historical-open-meteo-backfill", commands.historical_open_meteo_backfill),
        ("historical-open-meteo-persist", commands.historical_open_meteo_persist),
        ("historical-glofas-status", commands.historical_glofas_status),
        ("historical-glofas-backfill", commands.historical_glofas_backfill),
        ("historical-glofas-persist", commands.historical_glofas_persist),
        ("historical-cams-status", commands.historical_cams_status),
        ("historical-cams-backfill", commands.historical_cams_backfill),
        ("historical-cams-persist", commands.historical_cams_persist),
        ("historical-usdm-backfill", commands.historical_usdm_backfill),
        ("historical-usdm-finalize", commands.historical_usdm_finalize),
        ("historical-usdm-status", commands.historical_usdm_status),
        ("historical-plan-continue", commands.historical_plan_continue),
        ("historical-plan-staleness", commands.historical_plan_staleness),
        ("coverage-status", commands.coverage_status),
        ("coverage-fill", commands.coverage_fill),
        ("availability-bootstrap", availability_bootstrap),
        ("availability-publish", availability_publish),
        ("historical-promotion-spool", commands.historical_promotion_spool),
        ("historical-promotion-upload", commands.historical_promotion_upload),
        ("parquet-gap-fill", commands.parquet_gap_fill),
        ("parquet-drain", commands.parquet_drain),
        ("parquet-forward-vegetation", commands.parquet_forward_vegetation),
        ("parquet-catch-up-vegetation", commands.parquet_catch_up_vegetation),
        ("parquet-rewrite-vegetation", commands.parquet_rewrite_vegetation),
        ("parquet-rewrite-signal", commands.parquet_rewrite_signal),
        ("parquet-vegetation-absence-ladders", commands.parquet_vegetation_absence_ladders),
        ("parquet-retract-vegetation-absences", commands.parquet_retract_vegetation_absences),
        ("parquet-reconcile-vegetation-exact", commands.parquet_reconcile_vegetation_exact),
        ("parquet-repair-audit-vegetation", commands.parquet_repair_audit_vegetation),
        ("parquet-retire-legacy-layout", commands.parquet_retire_legacy_layout),
    ),
)
data.add_command(parquet)
