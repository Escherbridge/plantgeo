"""The `plantgeo-ml` click adapter: argument parsing and rendering, no orchestration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import click

from plantgeo_ml_service.pipeline.strategy_label_mapping import preflight_strategy_label_source_mapping
from plantgeo_ml_service.pipeline.strategy_selection import load_strategy_label_bundle, train_strategy_models

#: Exit code for a verb that is wired but not yet implemented. 3, not 2: click spends 2 on its own
#: usage errors and the preflight already mirrors the sibling's 2, so a caller could not tell the
#: three apart. A wrapper branches on this to distinguish "not built yet" from "you called it wrong".
NOT_IMPLEMENTED_EXIT_CODE = 3

#: The one payload every unimplemented verb prints, so a caller can branch on it.
NOT_IMPLEMENTED_PAYLOAD = {"error": "not_implemented_until_phase_2"}

#: Exit code for a preflight that ran to completion and found the mapping not ready to use.
PREFLIGHT_NOT_READY_EXIT_CODE = 2


@click.group()
@click.version_option(package_name="plantgeo-ml-service")
def cli() -> None:
    """PlantGeo machine-learning and Monte Carlo service."""


@cli.command("strategy-label-map-preflight")
@click.option("--mapping-manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.pass_context
def strategy_label_map_preflight(context: click.Context, mapping_manifest: Path) -> None:
    """Validate an intervention-label source mapping."""
    try:
        result = preflight_strategy_label_source_mapping(mapping_manifest)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(result.to_json())
    if not result.ready:
        context.exit(PREFLIGHT_NOT_READY_EXIT_CODE)


@cli.command("strategy-train")
@click.option("--label-bundle", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--output-artifact", type=click.Path(path_type=Path, dir_okay=False), required=True)
def strategy_train(label_bundle: Path, output_artifact: Path) -> None:
    """Train the local evaluation-only strategy benchmark."""
    try:
        artifact = train_strategy_models(load_strategy_label_bundle(label_bundle))
        write_atomic(output_artifact, artifact.to_json())
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    payload = {"artifact_checksum": artifact.checksum, "output_artifact": str(output_artifact)}
    click.echo(json.dumps(payload, sort_keys=True))


@cli.command("predict-daily")
@click.pass_context
def predict_daily(context: click.Context) -> None:
    """Run the daily fire-risk, Monte Carlo and analog-ensemble lanes (phase 2)."""
    click.echo(json.dumps(NOT_IMPLEMENTED_PAYLOAD, sort_keys=True))
    context.exit(NOT_IMPLEMENTED_EXIT_CODE)


@cli.command("serve")
@click.option("--host", default=None, help="Override SANIC_HOST.")
@click.option("--port", type=int, default=None, help="Override SANIC_PORT.")
def serve(host: str | None, port: int | None) -> None:
    """Run the Sanic application in this process."""
    from plantgeo_ml_service.app import create_app  # noqa: PLC0415 - CLI must not import Sanic to answer --help
    from plantgeo_ml_service.config import get_settings  # noqa: PLC0415 - see create_app import above

    settings = get_settings()
    create_app().run(
        host=host or settings.sanic_host,
        port=port or settings.sanic_port,
        debug=settings.sanic_debug,
        single_process=True,
    )


def write_atomic(path: Path, payload: str) -> None:
    """Write one artifact through a same-directory temporary file, so a reader never sees a partial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=path.parent,
            encoding="utf-8",
            newline="\n",
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
