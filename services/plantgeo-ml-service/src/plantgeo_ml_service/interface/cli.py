"""The `plantgeo-ml` click adapter: argument parsing and rendering, no orchestration."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from plantgeo_ml_service.pipeline.strategy_label_mapping import preflight_strategy_label_source_mapping
from plantgeo_ml_service.pipeline.strategy_selection import load_strategy_label_bundle, train_strategy_models

if TYPE_CHECKING:
    from plantgeo_ml_service.pipeline.availability_publisher import PointerStore
    from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
    from plantgeo_ml_service.pipeline.object_store import ObjectStore
    from plantgeo_ml_service.pipeline.observed_reader import ObservedReader

#: Exit code for a preflight that ran to completion and found the mapping not ready to use.
PREFLIGHT_NOT_READY_EXIT_CODE = 2

#: Exit code for a turn that could not reach the bucket at all. 4, not 1: click spends 1 on an
#: unhandled exception and 2 on its own usage errors, so a scheduler that pages on "the store is
#: gone" must be able to tell that apart from "you called it wrong". A turn where every lane
#: refused is NOT this: it exits 0, because a bounded turn is a completed turn (owner 2026-09-04).
INFRASTRUCTURE_EXIT_CODE = 4


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
@click.option("--issued-on", type=click.DateTime(formats=["%Y-%m-%d"]), default=None, help="Issue day, default today.")
@click.option("--dry-run-prefix", default=None, help="Write under ml/scratch/<x>/ instead of the published lanes.")
@click.option("--lane", "lane_slugs", multiple=True, help="Run only these lanes; repeatable, default all of them.")
@click.pass_context
def predict_daily(
    context: click.Context,
    issued_on: datetime | None,
    dry_run_prefix: str | None,
    lane_slugs: tuple[str, ...],
) -> None:
    """Run the daily fire-risk, Monte Carlo, analog-ensemble and weather-forecast lanes.

    Exit codes: 0 for a bounded turn, INCLUDING one where every lane refused; 2 for a usage error
    such as an unknown lane slug; 4 when the bucket itself could not be reached, which is the only
    failure a scheduler should page on.
    """
    from plantgeo_ml_service.config import get_settings  # noqa: PLC0415 - keeps `--help` free of DuckDB
    from plantgeo_ml_service.pipeline.predict_daily import (  # noqa: PLC0415 - see the import above
        PredictDailyInfrastructureError,
        run_predict_daily,
    )

    day = issued_on.date() if issued_on is not None else datetime.now(tz=UTC).date()
    forecast_cells_key = get_settings().forecast_cells_key
    try:
        runtime = open_runtime()
    except ValueError as error:
        click.echo(json.dumps({"error": "object_store_unconfigured", "detail": str(error)}, sort_keys=True), err=True)
        context.exit(INFRASTRUCTURE_EXIT_CODE)
    try:
        receipt = run_predict_daily(
            runtime.store,
            issued_on=day,
            dry_run_prefix=dry_run_prefix,
            reader=runtime.reader,
            pointers=runtime.pointers,
            lanes=list(lane_slugs) or None,
            forecast_cells_key=forecast_cells_key,
        )
    except ValueError as error:
        raise click.BadParameter(str(error)) from error
    except PredictDailyInfrastructureError as error:
        click.echo(json.dumps({"error": "object_store_unreachable", "detail": str(error)}, sort_keys=True), err=True)
        context.exit(INFRASTRUCTURE_EXIT_CODE)
    finally:
        runtime.close()
    click.echo(receipt.to_canonical_json())


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


@dataclass(frozen=True, slots=True)
class PredictRuntime:
    """The handles one turn runs through, built once from settings and closed together."""

    store: ObjectStore
    pointers: PointerStore
    reader: ObservedReader
    session: DuckDbSession

    def close(self) -> None:
        """Release the DuckDB session the reader borrowed, whatever the turn did."""
        self.session.close()


def open_runtime() -> PredictRuntime:
    """Build the bucket, the pointer store and the leakage-gated reader one turn runs through."""
    from plantgeo_ml_service.config import get_settings  # noqa: PLC0415 - see `predict_daily`
    from plantgeo_ml_service.pipeline.availability_publisher import BotoPointerStore  # noqa: PLC0415 - see above
    from plantgeo_ml_service.pipeline.duckdb_session import open_session  # noqa: PLC0415 - see above
    from plantgeo_ml_service.pipeline.object_store import BotoObjectStoreBackend, ObjectStore  # noqa: PLC0415 - above
    from plantgeo_ml_service.pipeline.observed_reader import ObservedReader  # noqa: PLC0415 - see above

    settings = get_settings()
    credentials = settings.require_object_store()
    backend = BotoObjectStoreBackend.from_credentials(credentials)
    store = ObjectStore(backend=backend, prefix=settings.object_store_prefix)
    session = open_session(credentials, settings=settings, prefix=settings.object_store_prefix)
    return PredictRuntime(
        store=store,
        pointers=BotoPointerStore(bucket=credentials.bucket, client=backend.client),
        reader=ObservedReader(store=store, session=session),
        session=session,
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
