"""Governed Parquet data command family."""

import click

from agri_data_service.interface.cli._registry import register_commands
from agri_data_service.interface.cli.availability import availability_bootstrap, availability_publish
from agri_data_service.interface.cli.parquet import parquet


@click.group()
def data() -> None:
    """Read governed Parquet products and operate their availability index."""


data.add_command(parquet)
register_commands(
    data,
    (
        ("availability-bootstrap", availability_bootstrap),
        ("availability-publish", availability_publish),
    ),
)

__all__ = ["data"]
