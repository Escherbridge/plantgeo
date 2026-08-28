"""Data ingest, history, coverage, and Parquet command-family wiring."""

import click

from agri_data_service.ingest.commands import INGEST_COMMANDS
from agri_data_service.interface.cli import commands
from agri_data_service.interface.cli._registry import register_commands
from agri_data_service.interface.cli.parquet import parquet


@click.group()
def data() -> None:
    """Ingest, inspect, and maintain governed data products."""


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
        ("historical-promotion-spool", commands.historical_promotion_spool),
        ("historical-promotion-upload", commands.historical_promotion_upload),
        ("parquet-gap-fill", commands.parquet_gap_fill),
        ("parquet-drain", commands.parquet_drain),
        ("parquet-forward-vegetation", commands.parquet_forward_vegetation),
        ("parquet-catch-up-vegetation", commands.parquet_catch_up_vegetation),
        ("parquet-rewrite-vegetation", commands.parquet_rewrite_vegetation),
        ("parquet-vegetation-absence-ladders", commands.parquet_vegetation_absence_ladders),
        ("parquet-retract-vegetation-absences", commands.parquet_retract_vegetation_absences),
        ("parquet-reconcile-vegetation-exact", commands.parquet_reconcile_vegetation_exact),
        ("parquet-repair-audit-vegetation", commands.parquet_repair_audit_vegetation),
        ("parquet-retire-legacy-layout", commands.parquet_retire_legacy_layout),
    ),
)
data.add_command(parquet)
