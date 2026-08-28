"""Root Click group for the hard-cutover `agri-service` binary."""

import click

from agri_data_service.interface.cli.data import data
from agri_data_service.interface.cli.forecast import forecast
from agri_data_service.interface.cli.ml import ml
from agri_data_service.interface.cli.ops import ops


@click.group()
def cli() -> None:
    """PlantGeo agriculture data, forecasting, ML, and operations."""


cli.add_command(forecast)
cli.add_command(ml)
cli.add_command(data)
cli.add_command(ops)

__all__ = ["cli"]
