"""Governed Parquet data command family."""

import click

from agri_data_service.interface.cli.parquet import parquet


@click.group()
def data() -> None:
    """Read governed Parquet products."""


data.add_command(parquet)

__all__ = ["data"]
