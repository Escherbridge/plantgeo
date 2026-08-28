"""Forecast command-family wiring."""

import click

from agri_data_service.execution.analog_ensemble_cli import (
    forecast_recalibrate_ndvi,
    forecast_train_anen,
)
from agri_data_service.execution.seasonal_command import register_seasonal_commands
from agri_data_service.interface.cli import commands
from agri_data_service.interface.cli._registry import register_commands


@click.group()
def forecast() -> None:
    """Run evaluation-only forecast workflows."""


register_commands(
    forecast,
    (
        ("train-anen", forecast_train_anen),
        ("recalibrate-ndvi", forecast_recalibrate_ndvi),
        ("refresh-ml-daily", commands.forecast_refresh_ml_daily),
        ("run-iteration", commands.forecast_run_iteration),
        ("reconcile-actuals", commands.forecast_reconcile_actuals),
        ("vegetation-register", commands.forecast_vegetation_register),
        ("vegetation-simulate", commands.forecast_vegetation_simulate),
        ("vegetation-evaluate", commands.forecast_vegetation_evaluate),
        ("train-wind", commands.forecast_train_wind),
        ("train-wind-plan", commands.forecast_train_wind_plan),
        ("train-wind-run", commands.forecast_train_wind_run),
        ("ensemble-status", commands.forecast_ensemble_status),
        ("ensemble-fetch", commands.forecast_ensemble_fetch),
    ),
)
register_seasonal_commands(forecast)
