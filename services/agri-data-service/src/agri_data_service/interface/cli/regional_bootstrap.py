"""Offline regional bundle commands; production admission remains separate."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from agri_data_service.execution.regional_bootstrap.bundle import (
    checked_compile,
    load_request,
    preflight_bundle,
    write_bundle,
)
from agri_data_service.execution.regional_bootstrap.compiler import BootstrapError

_NOT_READY_EXIT = 78


@click.command("region-bootstrap")
@click.option("--config", "config_path", required=True, type=click.Path(path_type=Path, dir_okay=False))
@click.option("--output", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.pass_context
def region_bootstrap(ctx: click.Context, config_path: Path, output: Path) -> None:
    """Compile an inactive reproducible bundle without starting acquisition."""
    try:
        bundle = checked_compile(load_request(config_path))
        written = write_bundle(bundle, output)
    except BootstrapError as error:
        click.echo(json.dumps({"error": str(error), "production_ready": False}, sort_keys=True))
    else:
        click.echo(json.dumps({"bundle_write": written, "readiness": bundle.readiness}, sort_keys=True))
    ctx.exit(_NOT_READY_EXIT)


@click.command("region-preflight")
@click.option("--bundle", "directory", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.pass_context
def region_preflight(ctx: click.Context, directory: Path) -> None:
    """Check local bundle and environment facts; do not authenticate or activate anything."""
    try:
        result = preflight_bundle(directory, os.environ)
    except BootstrapError as error:
        result = {"error": str(error), "production_ready": False}
    click.echo(json.dumps(result, sort_keys=True))
    ctx.exit(_NOT_READY_EXIT)
