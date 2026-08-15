"""CLI verbs for the evaluation-only seasonal forecast/feedback track.

Registered by one line in `cli.py`: ``register_seasonal_commands(cli)``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from agri_data_service.execution.seasonal_benchmark import (
    render_results_markdown,
    run_benchmark,
    write_benchmark_results,
)
from agri_data_service.execution.seasonal_evaluation_export import (
    ExportScope,
    SeasonalExportError,
    freeze_export,
    verify_export,
)
from agri_data_service.execution.seasonal_evidence_report import (
    BOISE_AREA_CELL_KEYS,
    collect_evidence_from_url,
    render_evidence_markdown,
)
from agri_data_service.execution.seasonal_lineage_persist import persist_benchmark

if TYPE_CHECKING:
    from click import Group


def _database_url_option(function: click.decorators.FC) -> click.decorators.FC:
    return click.option(
        "--database-url",
        required=True,
        envvar="SEASONAL_READ_DATABASE_URL",
        help="Async DSN read read-only; the transaction itself is SET TRANSACTION READ ONLY.",
    )(function)


def register_seasonal_commands(cli: Group) -> None:
    """Attach the seasonal-track verbs to the service CLI group."""

    @cli.command("seasonal-evidence-report")
    @_database_url_option
    @click.option("--output", type=click.Path(path_type=Path, dir_okay=False), required=True)
    @click.option("--cell-key", "cell_keys", multiple=True)
    def seasonal_evidence_report(database_url: str, output: Path, cell_keys: tuple[str, ...]) -> None:
        """Write the Phase 0 read-only forecast-iteration and data-quality report."""
        selected = cell_keys or BOISE_AREA_CELL_KEYS
        evidence = asyncio.run(collect_evidence_from_url(database_url, cell_keys=selected))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_evidence_markdown(evidence), encoding="utf-8", newline="")
        click.echo(
            json.dumps(
                {
                    "output": str(output),
                    "iteration_groups": len(evidence.iteration_inventory),
                    "series_profiled": len(evidence.series_profiles),
                    "source_releases": len(evidence.release_lineage),
                }
            )
        )

    @cli.command("seasonal-freeze-export")
    @_database_url_option
    @click.option("--destination", type=click.Path(path_type=Path, file_okay=False), required=True)
    @click.option("--export-key", required=True)
    @click.option("--cell-key", "cell_keys", multiple=True)
    def seasonal_freeze_export(
        database_url: str, destination: Path, export_key: str, cell_keys: tuple[str, ...]
    ) -> None:
        """Freeze the governed series into a checksummed, database-free evaluation export."""
        selected = cell_keys or BOISE_AREA_CELL_KEYS
        try:
            scope = ExportScope(
                export_key=export_key,
                frozen_at=datetime.now(UTC),
                cell_keys=tuple(selected),
            )
            manifest = asyncio.run(freeze_export(database_url, destination, scope))
        except SeasonalExportError as error:
            raise click.ClickException(str(error)) from error
        click.echo(
            json.dumps(
                {
                    "destination": str(destination),
                    "manifest_checksum": manifest.manifest_checksum,
                    "observation_rows": manifest.observation_row_count,
                    "series": len(manifest.series),
                    "source_releases": len(manifest.source_releases),
                    "known_missing_inputs": len(manifest.known_missing_inputs),
                }
            )
        )

    @cli.command("seasonal-verify-export")
    @click.option("--destination", type=click.Path(path_type=Path, file_okay=False, exists=True), required=True)
    def seasonal_verify_export(destination: Path) -> None:
        """Recompute every digest in a frozen export."""
        try:
            manifest = verify_export(destination)
        except SeasonalExportError as error:
            raise click.ClickException(str(error)) from error
        click.echo(json.dumps({"export_key": manifest.export_key, "manifest_checksum": manifest.manifest_checksum}))

    @cli.command("seasonal-benchmark")
    @click.option("--export-dir", type=click.Path(path_type=Path, file_okay=False, exists=True), required=True)
    @click.option("--output-dir", type=click.Path(path_type=Path, file_okay=False), required=True)
    def seasonal_benchmark(export_dir: Path, output_dir: Path) -> None:
        """Score the pre-registered candidate ladder over a frozen export; writes no database row."""
        results = run_benchmark(export_dir)
        write_benchmark_results(output_dir, results)
        (output_dir / "results.md").write_text(render_results_markdown(results), encoding="utf-8", newline="")
        click.echo(
            json.dumps(
                {
                    "output_dir": str(output_dir),
                    "manifest_checksum": results.manifest_checksum,
                    "targets": len(results.targets),
                    "candidates": len(results.candidate_names),
                    "abstentions": len(results.abstentions),
                }
            )
        )

    @cli.command("seasonal-persist-lineage")
    @click.option("--export-dir", type=click.Path(path_type=Path, file_okay=False, exists=True), required=True)
    @click.option(
        "--database-url",
        required=True,
        envvar="SEASONAL_WRITE_DATABASE_URL",
        help="Async DSN of a DISPOSABLE database; never the retained warehouse.",
    )
    @click.option("--candidate", "candidate_name", required=True)
    @click.option("--series-key", "series_keys", multiple=True)
    def seasonal_persist_lineage(
        export_dir: Path, database_url: str, candidate_name: str, series_keys: tuple[str, ...]
    ) -> None:
        """Persist candidate receipts and the residual-feedback lineage plane. Evaluation only."""
        summary = asyncio.run(
            persist_benchmark(
                export_dir,
                database_url,
                candidate_name=candidate_name,
                series_keys=series_keys or None,
            )
        )
        click.echo(json.dumps(summary))
