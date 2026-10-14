"""Governed Parquet data command family."""

import click

from agri_data_service.interface.cli._registry import register_commands
from agri_data_service.interface.cli.availability import (
    availability_bootstrap,
    availability_digest_trusted,
    availability_publish,
    availability_reconcile_physical,
    availability_release_verified_days,
    availability_verify_days,
)
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
        ("availability-reconcile-physical", availability_reconcile_physical),
        ("availability-digest-trusted", availability_digest_trusted),
        ("availability-verify-days", availability_verify_days),
        ("availability-release-verified-days", availability_release_verified_days),
    ),
)

__all__ = ["data"]
